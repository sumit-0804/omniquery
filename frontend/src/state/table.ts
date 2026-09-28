import type { RowsEvent } from "../api/events";

export type ColumnKind = { type: "int" | "num" | "text" | "date" | "bool" | "json" | "null"; numeric: boolean };

const DATE = /^\d{4}-\d{2}-\d{2}([T ][\d:.]+)?/;
// Ids and years read wrong with separators: 2,025 or 1,042.
const UNGROUPED = /(^|_)(id|year)s?$/i;

/** A label and alignment per column, judged from its non-null values; `rows` carries no types. */
export function columnKinds(columns: string[], rows: unknown[][]): ColumnKind[] {
  return columns.map((_, i) => {
    const values = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined);
    if (!values.length) return { type: "null", numeric: false };
    if (values.every((v) => typeof v === "boolean")) return { type: "bool", numeric: false };
    if (values.every((v) => typeof v === "number")) {
      return { type: values.every((v) => Number.isInteger(v)) ? "int" : "num", numeric: true };
    }
    if (values.every((v) => typeof v === "string" && DATE.test(v))) return { type: "date", numeric: false };
    if (values.every((v) => typeof v === "object")) return { type: "json", numeric: false };
    return { type: "text", numeric: false };
  });
}

export function formatCell(value: unknown, column: string): string {
  if (typeof value === "number") {
    if (Number.isInteger(value) && UNGROUPED.test(column)) return String(value);
    return value.toLocaleString("en-US", { maximumFractionDigits: 6 });
  }
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

const count = (n: number) => n.toLocaleString("en-US");
const rowsWord = (n: number) => `${count(n)} ${n === 1 ? "row" : "rows"}`;

/** Always says whether the table is a subset, and how many rows are hidden behind "Show all". */
export function rowSummary(r: RowsEvent, expanded: boolean): { visible: number; text: string; hidden: number } {
  const visible = expanded ? r.rows.length : Math.min(r.shown, r.rows.length);
  const hidden = r.rows.length - visible;
  let text: string;
  if (r.row_count === 0) text = "no rows";
  else if (hidden === 0) text = r.truncated ? `first ${rowsWord(visible)} — the result had more` : `all ${rowsWord(visible)}`;
  else text = `showing ${count(visible)} of ${count(r.row_count)}${r.truncated ? "+" : ""} rows`;
  return { visible, text, hidden };
}
