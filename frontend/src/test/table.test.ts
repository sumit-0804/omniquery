import { describe, expect, it } from "vitest";
import type { RowsEvent, RunEvent } from "../api/events";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import { createElement } from "react";
import { literalBackslashes, nextReveal } from "../components/Answer";
import { replay } from "../state/run";
import { columnKinds, formatCell, rowSummary } from "../state/table";
import rowsMany from "./fixtures/rows-many.json";

const rowsOf = (rows: unknown[][], extra: Partial<RowsEvent> = {}): RowsEvent => ({
  type: "rows",
  t: 0,
  columns: ["a"],
  rows,
  row_count: rows.length,
  truncated: false,
  shown: Math.min(rows.length, 25),
  ...extra,
});
const n = (count: number) => Array.from({ length: count }, (_, i) => [i]);

describe("rowSummary", () => {
  it("says 'all' when nothing is hidden", () => {
    expect(rowSummary(rowsOf(n(12)), false)).toEqual({ visible: 12, hidden: 0, text: "all 12 rows" });
    expect(rowSummary(rowsOf(n(1)), false).text).toBe("all 1 row");
  });

  it("shows the model's 25 first, and all of them once expanded", () => {
    expect(rowSummary(rowsOf(n(60)), false)).toEqual({ visible: 25, hidden: 35, text: "showing 25 of 60 rows" });
    expect(rowSummary(rowsOf(n(60)), true)).toEqual({ visible: 60, hidden: 0, text: "all 60 rows" });
  });

  it("admits when the fetch itself was capped", () => {
    const capped = rowsOf(n(1000), { truncated: true });
    expect(rowSummary(capped, false).text).toBe("showing 25 of 1,000+ rows");
    expect(rowSummary(capped, true).text).toBe("first 1,000 rows — the result had more");
  });

  it("handles an empty result", () => {
    expect(rowSummary(rowsOf([]), false)).toEqual({ visible: 0, hidden: 0, text: "no rows" });
  });
});

describe("columnKinds", () => {
  it("infers a type per column from non-null values", () => {
    const kinds = columnKinds(
      ["id", "fare", "day", "name", "ok", "empty"],
      [
        [1, 12.5, "2025-01-03", "a", true, null],
        [2, null, "2025-01-04T10:00:00", "b", false, null],
      ],
    );
    expect(kinds.map((k) => k.type)).toEqual(["int", "num", "date", "text", "bool", "null"]);
    expect(kinds.map((k) => k.numeric)).toEqual([true, true, false, false, false, false]);
  });
});

it("formatCell groups thousands, except ids and years", () => {
  expect(formatCell(12345, "total")).toBe("12,345");
  expect(formatCell(12345, "ride_id")).toBe("12345");
  expect(formatCell(2025, "year")).toBe("2025");
  expect(formatCell(0.1234567, "ratio")).toBe("0.123457");
});

it("nextReveal steps two words at a time and ends at the text's end", () => {
  const text = "Credit card leads with **416** rides.";
  const steps: string[] = [];
  for (let shown = 0; shown < text.length; ) {
    shown = nextReveal(text, shown);
    steps.push(text.slice(0, shown));
  }
  expect(steps).toEqual(["Credit card", "Credit card leads with", "Credit card leads with **416** rides."]);
  expect(nextReveal("one two   ", 7)).toBe(10);
});

it("keeps Windows paths intact through markdown", () => {
  const path = String.raw`Saved to C:\Users\sumit\.omniquery\outputs\users.parquet`;
  const html = renderToStaticMarkup(createElement(Markdown, null, literalBackslashes(path)));
  expect(html).toContain(String.raw`C:\Users\sumit\.omniquery\outputs\users.parquet`);
});

it("a 60-row run keeps every row, with the model's 25 marked", () => {
  const run = replay(rowsMany as RunEvent[]);
  expect(run.rows?.rows).toHaveLength(60);
  expect(rowSummary(run.rows!, false).text).toBe("showing 25 of 60 rows");
  expect(run.sql?.notes).toEqual(["rides"]);
});
