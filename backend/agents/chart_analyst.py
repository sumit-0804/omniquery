"""The chart agent: the SQL pipeline, then one step that designs a Vega-Lite chart.

The model only picks the encoding (mark and columns) and writes the answer; our code
builds and validates the spec, so a bad reply can never produce a broken chart.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

from langchain_core.messages import AIMessage

from agents.sql_analyst import ROW_LIMIT, build_graph, represent_final_answer
from Models.schema import AgentSchema
from utils.database import MAX_FETCH_ROWS, format_rows
from utils.llm_pick import pick_llm, text_of

# Charts need every point they draw, and the model reads a summary rather than the rows.
CHART_ROW_LIMIT = MAX_FETCH_ROWS
MARKS = ("bar", "line", "area", "point")
# Above this many categories a vertical bar's labels overlap, so bars turn horizontal.
MAX_BAR_CATEGORIES = 25
VEGA_LITE_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2})?)?")


def column_kinds(columns: list[str], rows: list[list]) -> dict[str, str]:
    """Vega-Lite field type per column, read from the values themselves."""
    kinds = {}
    for i, name in enumerate(columns):
        values = [row[i] for row in rows if row[i] is not None]
        if values and all(isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) for v in values):
            kinds[name] = "quantitative"
        elif values and all(isinstance(v, (date, datetime)) for v in values):
            kinds[name] = "temporal"
        elif values and all(isinstance(v, str) and _ISO_DATE.match(v) for v in values):
            # to_char / strftime months like "2025-01" are dates written as text.
            kinds[name] = "temporal"
        else:
            kinds[name] = "nominal"
    return kinds


def design_chart(state: AgentSchema) -> dict:

    columns, rows = state.result_columns, state.result_rows
    kinds = column_kinds(columns, rows)
    note = f"Showing the first {len(rows):,} rows; the result had more." if state.result_truncated else ""

    if len(rows) < 2 or len(columns) < 2 or "quantitative" not in kinds.values():
        # A single number or a plain list is answered, not charted.
        return represent_final_answer(state) | {
            "chart_error": "chart.unsupported_shape",
            "chart_error_detail": "A chart needs at least two rows, a label column and a numeric column.",
            "chart_note": note,
        }

    reply = text_of(pick_llm("low").invoke(_design_prompt(state, kinds, note)))
    choice, answer = parse_choice(reply, kinds)
    if choice is None:
        choice = heuristic_choice(columns, kinds)
    if not answer:
        answer = represent_final_answer(state)["final_answer"]

    update = {"final_answer": answer, "messages": [AIMessage(content=answer)], "chart_note": note}
    spec = build_spec(columns, rows, kinds, choice, note)
    problem = validate_spec(spec)
    if problem:
        # The data is still valid even when the chart is not, so the answer and rows stay.
        return update | {"chart_spec": None, "chart_error": "chart.invalid_spec", "chart_error_detail": problem}
    return update | {"chart_spec": spec}


def _design_prompt(state: AgentSchema, kinds: dict[str, str], note: str) -> str:
    columns, rows = state.result_columns, state.result_rows
    summary = [f"rows: {len(rows):,}"]
    for i, name in enumerate(columns):
        values = [row[i] for row in rows if row[i] is not None]
        if kinds[name] == "quantitative" and values:
            summary.append(f"{name}: min {min(values)}, max {max(values)}, total {sum(values)}")
    first = columns[0]
    summary.append(f"{first} runs from {rows[0][0]} to {rows[-1][0]}")
    truncation = f"\n    {note} Say so in the answer." if note else ""

    return f"""
    You design a chart for a query result and write a short answer to the user's question.
    Reply with exactly one JSON object and nothing else:
    {{"mark": "bar | line | area | point", "x": "<column>", "y": "<numeric column>",
      "color": "<column or null>", "title": "<short chart title>", "answer": "<one or two sentences>"}}

    Rules:
    - line or area for a trend over time, bar to compare categories, point for two numbers.
    - y must be a numeric column. Use color only to split lines or bars by a category column.
    - The answer states only what the summary and rows support. Do not invent numbers.{truncation}

    User's question: {state.user_question}

    Columns: {", ".join(f"{c} ({kinds[c]})" for c in columns)}
    Summary of all rows:
    {chr(10).join("    " + line for line in summary)}

    First rows:
    {format_rows(columns, rows, ROW_LIMIT)}
    """


def parse_choice(reply: str, kinds: dict[str, str]) -> tuple[dict | None, str]:
    """The model's encoding if it is usable, and its answer if it gave one."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        return None, ""

    answer = str(data.get("answer") or "").strip()
    color = data.get("color") or None
    usable = (
        data.get("mark") in MARKS
        and data.get("x") in kinds
        and kinds.get(data.get("y")) == "quantitative"
        and data.get("x") != data.get("y")
        and (color is None or color in kinds)
    )
    if not usable:
        return None, answer
    choice = {"mark": data["mark"], "x": data["x"], "y": data["y"], "color": color,
              "title": str(data.get("title") or "")}
    return choice, answer


def heuristic_choice(columns: list[str], kinds: dict[str, str]) -> dict:
    """A sensible encoding from the column types alone, used when the model's is unusable."""
    by_kind = {k: [c for c in columns if kinds[c] == k] for k in ("temporal", "nominal", "quantitative")}
    numbers = by_kind["quantitative"]
    if by_kind["temporal"]:
        x, mark, color = by_kind["temporal"][0], "line", (by_kind["nominal"] or [None])[0]
    elif by_kind["nominal"]:
        x, mark, color = by_kind["nominal"][0], "bar", (by_kind["nominal"][1:] or [None])[0]
    else:
        x, mark, color = numbers[0], "point", None
    y = next(c for c in numbers if c != x)
    return {"mark": mark, "x": x, "y": y, "color": color, "title": ""}


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float):
        # Summed doubles carry noise like 198738.88000000006, which would show in tooltips.
        return round(value, 6)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def build_spec(columns: list[str], rows: list[list], kinds: dict[str, str], choice: dict, note: str = "") -> dict:
    x, y, mark = choice["x"], choice["y"], choice["mark"]
    x_enc = {"field": x, "type": kinds[x], "title": x}
    y_enc = {"field": y, "type": "quantitative", "title": y}

    if mark == "bar" and kinds[x] == "nominal":
        categories = len({row[columns.index(x)] for row in rows})
        if categories > MAX_BAR_CATEGORIES:
            # Horizontal, largest first: long category lists stay readable.
            x_enc, y_enc = y_enc, x_enc | {"sort": "-x"}
        else:
            x_enc["sort"] = "-y"

    encoding = {"x": x_enc, "y": y_enc,
                "tooltip": [{"field": c, "type": kinds[c], "title": c} for c in columns]}
    if choice.get("color"):
        encoding["color"] = {"field": choice["color"], "type": kinds[choice["color"]]}

    title = choice.get("title") or f"{y} by {x}"
    return {
        "$schema": VEGA_LITE_SCHEMA,
        "title": {"text": title, "subtitle": note} if note else title,
        "width": "container",
        "height": 320,
        "data": {"values": [{c: _json_value(v) for c, v in zip(columns, row, strict=True)} for row in rows]},
        "mark": {"type": mark, "tooltip": True},
        "encoding": encoding,
    }


@lru_cache(maxsize=1)
def _schema() -> dict:
    from altair.vegalite.v6.schema import load_schema

    return load_schema()


def validate_spec(spec: dict) -> str:
    """Empty when the spec is valid Vega-Lite, else the first problem found."""
    from altair.utils.schemapi import validate_jsonschema

    error = validate_jsonschema(spec, _schema(), raise_error=False)
    return "" if error is None else f"{'/'.join(map(str, error.path)) or 'spec'}: {error.message}"[:300]


chart_analyst = build_graph("design_chart", design_chart)


if __name__ == "__main__":
    from utils.sources import default_source

    response = chart_analyst.invoke({
        "user_question": "plot the number of completed rides per month in 2025",
        "source_id": default_source().value["id"],
        "row_limit": CHART_ROW_LIMIT,
    })
    print(response["final_answer"], response.get("chart_error"))
