# OmniQuery

Ask questions about your own data in plain English, and see exactly how the answer was made.

## What

A local, single-user app that connects to a Postgres database or to CSV, Parquet and JSON files. Ask a question and you get the answer, plus the SQL that produced it, a result table or a chart. You can also ask it to extract or transform data into a new file.

## Why

- **Visible:** every step (routing, SQL, retries, which model answered) streams into the UI as it happens.
- **Safe:** it only reads. Queries that would change data are refused, both by a check and at the connection level.
- **Teachable:** add notes on tables and columns; they are sent with every query, and the answer shows which ones it used.
- **Cheap:** runs on free-tier models (Groq, Cloudflare Workers AI, Gemini), falling back to the next one when a limit is hit.

## For whom

Data engineers who want a quick look at a database or a set of files (what the tables hold, counts, trends) without writing SQL by hand, and who want to check the SQL that ran.

## How it works

```mermaid
flowchart TD
    Q["Your question"] --> R{"Router<br/>Jev classifier, LLM fallback"}
    R -- "unclear" --> H["Ask you<br/>pause, explain, resume"]
    H --> R2{"Your choice"}
    R -- "sql" --> S1
    R -- "chart" --> S1
    R -- "etl" --> E1
    R2 -- "sql / chart" --> S1
    R2 -- "etl" --> E1

    subgraph SQL["SQL pipeline (sql and chart)"]
        S1["Select tables<br/>+ your notes"] --> S2["Generate SQL"]
        S2 --> S3{"Read-only?"}
        S3 -- "no" --> NR["Not run"]
        S3 -- "yes" --> S4["Validate with EXPLAIN"]
        S4 --> S5["Execute"]
        S4 -- "error, up to 3 tries" --> S2
        S5 -- "error, up to 3 tries" --> S2
        S5 --> S6["Compose the answer<br/>or design the chart"]
        S4 -- "tries spent" --> F["Report the failure"]
    end

    subgraph ETL["ETL pipeline"]
        E1["Read the source"] --> E2["Plan the job"]
        E2 --> E3["Run in DuckDB<br/>or a Python sandbox"]
        E3 --> E4["Write the output file"]
    end

    S6 --> UI["Web UI<br/>every step streamed over SSE"]
    NR --> UI
    F --> UI
    E4 --> UI
```

The agents are [LangGraph](https://github.com/langchain-ai/langgraph) graphs (`backend/agents/`). The backend is FastAPI, uploaded files and outputs live in DuckDB, and the frontend is React + Vite + Tailwind with Vega-Lite charts.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and [Bun](https://bun.sh/).

```bash
cp backend/.env.example backend/.env    # add at least one model API key
cd frontend && bun install && bun run build
cd ../backend && uv run omniquery serve # opens http://127.0.0.1:7666
```

Then connect a database or upload files from the sidebar. There is also a CLI: `uv run omniquery --help`.

## Tests

```bash
cd backend && uv run pytest
cd frontend && bun run test
```
