import argparse
import glob
import sys
from pathlib import Path

from utils import sources


def ask(question: str, source_id: str, open_chart: bool = False) -> str:
    import uuid

    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    from agents.data_agent import data_agent

    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    result = data_agent.invoke({"messages": [HumanMessage(content=question)], "source_id": source_id}, config)
    # The router paused to ask the user; answer it and resume the same run.
    while result.get("__interrupt__"):
        result = data_agent.invoke(Command(resume=_choose(result["__interrupt__"][0].value)), config)

    answer = result["messages"][-1].content
    if result.get("chart_spec"):
        path = save_chart(result["chart_spec"])
        answer += f"\n\nChart: {path}"
        if open_chart:
            import webbrowser

            webbrowser.open(path.as_uri())
    elif result.get("chart_error"):
        answer += f"\n\n(No chart: {result['chart_error']})"
    return answer


_CHART_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/vega@6"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-lite@6"></script>
<script src="https://cdn.jsdelivr.net/npm/vega-embed@7"></script>
<style>body {{ font-family: system-ui, sans-serif; margin: 24px; }} #chart {{ width: 100%; }}</style>
</head><body><div id="chart"></div>
<script>vegaEmbed("#chart", {spec});</script>
</body></html>
"""


def save_chart(spec: dict) -> Path:
    """Write the spec as a standalone HTML page, until the web UI renders charts itself."""
    import html
    import json
    from datetime import datetime

    folder = sources.home() / "charts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}.html"
    title = spec["title"]["text"] if isinstance(spec.get("title"), dict) else spec.get("title", "Chart")
    # "</" would end the script tag early if a value contained "</script>".
    payload = json.dumps(spec).replace("</", "<\\/")
    path.write_text(_CHART_PAGE.format(title=html.escape(str(title)), spec=payload), encoding="utf-8")
    return path


def _choose(prompt: dict) -> str:
    """Show the router's question and return the user's raw reply; the agent interprets it."""
    if prompt.get("explanation"):
        print(f"\n{prompt['explanation']}")
    else:
        print(f"\n{prompt['question']}")
        if prompt.get("why"):
            print(f"({prompt['why']})")
    for n, option in enumerate(prompt["options"], 1):
        print(f"  {n}. {option['key']}: {option['label']}")
    try:
        return input("Pick a number, ask a question if you're not sure, or press Enter to cancel: ")
    except (EOFError, KeyboardInterrupt):
        return ""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omniquery", description="Ask questions about your data.")
    p.add_argument("question", nargs="*", help="question to ask; omit for an interactive prompt")
    p.add_argument("--source", metavar="NAME", help="saved source to ask about")
    p.add_argument("--open", action="store_true", help="open charts in the browser")
    p.add_argument("--sources", action="store_true", help="list saved sources")
    p.add_argument("--outputs", action="store_true", help="list files saved by extracts and transforms")
    p.add_argument("--add-source", nargs=2, metavar=("NAME", "URL"), help="save a Postgres connection")
    p.add_argument("--schema", default="public", help="schema for --add-source (default: public)")
    p.add_argument("--add-files", nargs="+", metavar="NAME FILE", help="save files (csv, parquet, json) as a source")
    p.add_argument("--remove-source", metavar="NAME", help="delete a saved source")
    p.add_argument("--note", nargs=3, metavar=("NAME", "PATH", "TEXT"),
                   help='note on "table" or "table.column"; empty TEXT removes it')
    return p


def _fail(result) -> int:
    print(result.error, file=sys.stderr)
    return 2 if result.code == "source.ambiguous" else 1


def _describe(s: dict) -> str:
    if s["kind"] == "files":
        where = f"files   {len(s['tables'])} tables: {', '.join(s['tables'])}"
    else:
        where = f"postgres  {sources.masked(s['url'])}  schema {s['schema']}"
        where += "" if s["readonly"] else "  (account can write)"
    notes = f"  {len(s['notes'])} notes" if s["notes"] else ""
    return f"{s['name']:<16} {where}{notes}"


def _manage(args) -> int | None:
    """Run a source command, or return None when the arguments are a question."""
    if args.sources:
        saved = sources.list_sources()
        print("\n".join(_describe(s) for s in saved) if saved else "No sources saved yet.")
        return 0

    if args.outputs:
        outputs = sources.list_outputs()
        for o in outputs:
            print(f"{o['name']:<32} {o['bytes'] / 1024:>9,.1f} KB  table {o['table'] or '-'}")
        print(f"\n{len(outputs)} files in {sources.outputs_dir()}" if outputs else "No outputs yet.")
        return 0

    if args.add_source:
        name, url = args.add_source
        result = sources.add_postgres(name, url, args.schema)
        if not result:
            return _fail(result)
        m = result.meta
        print(f"Saved {name}: {m['tables']} tables, Postgres {m['version']}, {m['latency_ms']} ms.")
        if not m["readonly"]:
            print("\nWarning: this account can change data. OmniQuery only ever runs read-only"
                  "\ntransactions, but a read-only role is safer:\n\n" + sources.readonly_role_sql(args.schema))
        return 0

    if args.add_files:
        if len(args.add_files) < 2:
            print("--add-files needs a name and at least one file.", file=sys.stderr)
            return 1
        name, *patterns = args.add_files
        # PowerShell and cmd pass "data/*.csv" through unexpanded, so expand it here.
        paths = [p for pattern in patterns for p in (sorted(glob.glob(pattern)) or [pattern])]
        result = sources.add_files(name, paths)
        if not result:
            return _fail(result)
        print(f"Saved {name}: {', '.join(result.value['tables'])}.")
        return 0

    if args.remove_source:
        result = sources.remove_source(args.remove_source)
        if not result:
            return _fail(result)
        print(f"Removed {result.value['name']}.")
        return 0

    if args.note:
        name, path, text = args.note
        result = sources.set_note(name, path, text)
        if not result:
            return _fail(result)
        print(f"Saved note on {path}." if text.strip() else f"Removed note on {path}.")
        return 0

    return None


def serve(argv: list[str]) -> int:
    """Run the web API (and the built React app, if present) on this machine only."""
    import threading
    import webbrowser

    import uvicorn

    p = argparse.ArgumentParser(prog="omniquery serve")
    p.add_argument("--port", type=int, default=7666)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)

    from api.app import FRONTEND_DIST

    url = f"http://127.0.0.1:{args.port}"
    if not FRONTEND_DIST.is_dir():
        print(f"No built frontend at {FRONTEND_DIST}; serving the API only ({url}/api/docs).")
    elif not args.no_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    # Loopback only: nothing else on the network can reach the app.
    uvicorn.run("api.app:app", host="127.0.0.1", port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:
        return serve(argv[1:])
    args = _parser().parse_args(argv)
    managed = _manage(args)
    if managed is not None:
        return managed

    source = sources.resolve(args.source)
    if not source:
        return _fail(source)
    source_id = source.value["id"]

    if args.question:
        print(ask(" ".join(args.question), source_id, open_chart=args.open))
        return 0

    print(f"OmniQuery on {source.value['name']}. Ask a question, or press Ctrl-C to quit.")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question:
            print(ask(question, source_id, open_chart=args.open))


if __name__ == "__main__":
    raise SystemExit(main())
