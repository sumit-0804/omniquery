"""Execution-accuracy eval for the SQL agent.

Runs each question through sql_analyst, re-executes the SQL it settled on, and
compares the rows with the gold SQL's rows.

    uv run python evals/run_eval.py                  # full run
    uv run python evals/run_eval.py --only q07,q15   # a subset
    uv run python evals/run_eval.py --check-gold     # validate gold SQL only, no LLM calls
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from psycopg2 import sql as psql

from utils.database import DatabaseUtil

EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS = EVAL_DIR / "questions.json"
RESULTS_DIR = EVAL_DIR / "results"

# Gold results must fit under the agent's own row cap, or a correct answer looks wrong.
MAX_GOLD_ROWS = 25
MAX_DECIMALS = 2


def fetch(query: str, schema: str | None) -> tuple[list[str], list[tuple]]:
    # Deliberately not DatabaseUtil.execute_sql: its return shape changes between
    # checkpoints, and the eval must measure every checkpoint the same way.
    with DatabaseUtil()._cursor(readonly=True) as cur:
        if schema:
            cur.execute(psql.SQL("SET LOCAL search_path = {}").format(psql.Identifier(schema)))
        cur.execute(query)
        columns = [d[0] for d in cur.description] if cur.description else []
        return columns, cur.fetchmany(1001)


def _as_decimal(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _decimals(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def _column_key(values: list, places: int | None) -> list:
    """Canonical form of one column. Numbers compare at `places` decimals."""
    if places is None:
        return [v.isoformat() if isinstance(v, (datetime, date)) else
                (None if v is None else str(v).strip()) for v in values]
    return [None if v is None else round(_as_decimal(v), places) for v in values]


def _precision(values: list) -> int | None:
    """Decimals to compare a column at, or None if it is not numeric."""
    numbers = [_as_decimal(v) for v in values if v is not None]
    if not numbers or any(n is None for n in numbers):
        return None
    return min(max(_decimals(n) for n in numbers), MAX_DECIMALS)


def results_match(gold: tuple[list, list], agent: tuple[list, list]) -> bool:
    """Gold columns must map one-to-one onto agent columns with equal row multisets.

    Order-insensitive, extra agent columns allowed. Numeric columns compare at the
    coarser precision of the two sides, so 2.8 matches 2.7624 but 2.8 does not match 2.5.
    """
    gold_rows, agent_rows = gold[1], agent[1]
    if len(gold_rows) != len(agent_rows):
        return False
    if not gold_rows:
        return True

    gold_cols = list(zip(*gold_rows))
    agent_cols = list(zip(*agent_rows))

    def key_pair(g: int, a: int) -> tuple[list, list] | None:
        gp, ap = _precision(list(gold_cols[g])), _precision(list(agent_cols[a]))
        if (gp is None) != (ap is None):
            return None
        places = None if gp is None else min(gp, ap)
        return _column_key(list(gold_cols[g]), places), _column_key(list(agent_cols[a]), places)

    def candidates(g: int) -> list[int]:
        out = []
        for a in range(len(agent_cols)):
            pair = key_pair(g, a)
            if pair and Counter(pair[0]) == Counter(pair[1]):
                out.append(a)
        return out

    options = [candidates(g) for g in range(len(gold_cols))]

    def search(g: int, used: tuple[int, ...]) -> bool:
        if g == len(gold_cols):
            gold_t = Counter(zip(*(key_pair(i, a)[0] for i, a in enumerate(used))))
            agent_t = Counter(zip(*(key_pair(i, a)[1] for i, a in enumerate(used))))
            return gold_t == agent_t
        return any(search(g + 1, used + (a,)) for a in options[g] if a not in used)

    return search(0, ())


def llm_calls_so_far() -> int:
    from utils.llm_pick import usage_report

    return sum(row["rpd_used"] for row in usage_report())


def run_question(item: dict, schema: str | None) -> dict:
    from agents.sql_analyst import sql_analyst

    record = {"id": item["id"], "tags": item["tags"], "question": item["question"]}
    gold = fetch(item["gold_sql"], schema)

    calls_before, started = llm_calls_so_far(), time.time()
    agent_input = {"user_question": item["question"]}
    if schema:
        agent_input["schema_name"] = schema
    try:
        state = sql_analyst.invoke(agent_input)
    except Exception as exc:
        record.update(outcome="crash", detail=f"{type(exc).__name__}: {exc}"[:300])
        return record
    finally:
        record["seconds"] = round(time.time() - started, 1)
        record["llm_calls"] = llm_calls_so_far() - calls_before

    record["sql"] = state.get("generated_sql_query", "")
    record["answer"] = state.get("final_answer", "")
    if state.get("sql_attempts") is not None:
        record["attempts"] = state["sql_attempts"]

    if state.get("error_code"):
        record.update(outcome="error", detail=f"{state['error_code']}: {state.get('error_detail', '')}"[:300])
        return record
    if state.get("is_safe") != "Yes":
        record.update(outcome="canceled", detail=state.get("comments", "")[:300])
        return record

    try:
        agent = fetch(record["sql"], schema)
    except Exception as exc:
        record.update(outcome="error", detail=f"re-execute failed: {exc}"[:300])
        return record

    record["outcome"] = "pass" if results_match(gold, agent) else "wrong_result"
    if record["outcome"] == "wrong_result":
        record["detail"] = f"gold {len(gold[1])} rows {gold[1][:3]} | agent {len(agent[1])} rows {agent[1][:3]}"[:300]
    return record


def check_gold(items: list[dict], schema: str | None) -> int:
    problems = 0
    for item in items:
        try:
            _, rows = fetch(item["gold_sql"], schema)
        except Exception as exc:
            print(f"  FAIL  {item['id']}  {exc}")
            problems += 1
            continue
        issue = ("no rows" if not rows else
                 f"{len(rows)} rows > {MAX_GOLD_ROWS}" if len(rows) > MAX_GOLD_ROWS else
                 "contains NULL" if any(v is None for r in rows for v in r) else "")
        problems += bool(issue)
        print(f"  {'WARN' if issue else 'ok  '}  {item['id']}  {len(rows):>2} rows  {rows[0] if rows else ''}  {issue}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--schema", help="run against another Postgres schema, e.g. bigdemo")
    parser.add_argument("--check-gold", action="store_true", help="validate gold SQL; no LLM calls")
    args = parser.parse_args()

    items = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i["id"] in wanted]
    if args.limit:
        items = items[: args.limit]

    if args.check_gold:
        return 1 if check_gold(items, args.schema) else 0

    records = []
    for item in items:
        record = run_question(item, args.schema)
        records.append(record)
        print(f"  {record['outcome']:<12} {record['id']}  {record.get('seconds', 0):>5}s  "
              f"{record.get('llm_calls', 0)} calls  {record.get('detail', '')[:90]}", flush=True)

    passed = sum(r["outcome"] == "pass" for r in records)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        for tag in r["tags"]:
            by_tag[tag].append(r["outcome"] == "pass")
    outcomes = Counter(r["outcome"] for r in records)

    print(f"\naccuracy  {passed}/{len(records)} = {100 * passed / max(len(records), 1):.0f}%")
    print("outcomes  " + ", ".join(f"{k} {v}" for k, v in outcomes.most_common()))
    print(f"llm calls {sum(r.get('llm_calls', 0) for r in records) / max(len(records), 1):.1f} per question")
    for tag, results in sorted(by_tag.items()):
        print(f"  {tag:<12} {sum(results)}/{len(results)}")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}{'-' + args.schema if args.schema else ''}.json"
    out.write_text(json.dumps({"accuracy": passed / max(len(records), 1),
                               "outcomes": dict(outcomes), "records": records},
                              indent=2, default=str), encoding="utf-8")
    print(f"\nsaved {out.relative_to(EVAL_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
