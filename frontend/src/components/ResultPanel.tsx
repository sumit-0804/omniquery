import { lazy, Suspense, useMemo, useState } from "react";
import type { ChartEvent, RowsEvent } from "../api/events";
import { chartErrorText, type MarkType, markOf, marksFor } from "../state/chart";
import { columnKinds, formatCell, rowSummary } from "../state/table";

// Vega is most of the bundle; load it only when a chart is shown.
const Chart = lazy(() => import("./Chart"));

function Tab({ active, disabled, title, onClick, children }: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick?: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      title={title}
      onClick={onClick}
      className={`rounded-control border px-2.5 py-[5px] text-xs leading-none ${
        active ? "border-line-strong bg-[#242830] text-ink" : "border-transparent bg-transparent text-ink-4"
      } disabled:opacity-50`}
    >
      {children}
    </button>
  );
}

function DataTable({ rows, visible }: { rows: RowsEvent; visible: number }) {
  const kinds = useMemo(() => columnKinds(rows.columns, rows.rows), [rows]);
  return (
    <div className="max-h-[400px] overflow-auto border-t border-line">
      {/* As wide as the panel or the data, whichever is wider; a fixed min-width hid narrow results off-screen. */}
      <table className="w-max min-w-full border-collapse font-mono text-xs">
        <thead>
          <tr>
            {rows.columns.map((name, i) => (
              <th
                key={i}
                scope="col"
                className={`sticky top-0 h-[29px] bg-table-head px-3 font-normal whitespace-nowrap text-ink-3 ${
                  kinds[i].numeric ? "text-right" : "text-left"
                }`}
              >
                {name} {kinds[i].type !== "null" && <span className="text-ink-5">{kinds[i].type}</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.rows.slice(0, visible).map((row, r) => (
            <tr key={r} className="h-7 border-t border-divider">
              {row.map((value, i) => (
                <td
                  key={i}
                  className={`px-3 whitespace-nowrap text-ink-2 ${kinds[i].numeric ? "text-right tabular-nums" : ""}`}
                >
                  {value === null ? <span className="text-ink-5">NULL</span> : formatCell(value, rows.columns[i])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.row_count === 0 && <div className="px-3 py-3 text-xs text-ink-5">No rows matched.</div>}
    </div>
  );
}

/** The query result: the chart when the chart agent made one, and always the table. */
export function ResultPanel({ rows, chart }: { rows: RowsEvent; chart: ChartEvent | null }) {
  const spec = chart?.spec ?? null;
  // The chart arrives after the rows, so the default follows it until the user picks a tab.
  const [pickedTab, setTab] = useState<"table" | "chart" | null>(null);
  const [pickedMark, setPickedMark] = useState<MarkType | null>(null);
  const [expanded, setExpanded] = useState(false);
  const showChart = (pickedTab ?? "chart") === "chart" && spec !== null;
  // The chart plots every fetched row; only the table starts at the model's 25.
  const summary = rowSummary(rows, expanded || showChart);
  const mark = pickedMark ?? (spec ? markOf(spec) : "bar");

  return (
    <div className="mt-5 overflow-hidden rounded-card border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-1 bg-raised px-[9px] py-[7px]">
        <Tab active={!showChart} onClick={() => setTab("table")}>
          Table
        </Tab>
        <Tab
          active={showChart}
          disabled={!spec}
          title={spec ? undefined : "No chart for this answer"}
          onClick={() => setTab("chart")}
        >
          Chart
        </Tab>
        {showChart && (
          <div role="group" aria-label="Chart type" className="ml-2 flex gap-1 border-l border-line pl-2">
            {marksFor(spec).map((m) => (
              <Tab key={m} active={m === mark} onClick={() => setPickedMark(m)}>
                {m}
              </Tab>
            ))}
          </div>
        )}
        <div className="ml-auto font-mono text-[11px] leading-none text-ink-4">{summary.text}</div>
      </div>

      {chart?.error && (
        <div className="border-t border-line px-3 py-2 text-xs text-ink-4">{chartErrorText(chart.error)}</div>
      )}

      {showChart ? (
        <div className="border-t border-line">
          <Suspense fallback={<div className="min-h-[340px]" />}>
            <Chart spec={spec} mark={mark} />
          </Suspense>
        </div>
      ) : (
        <>
          <DataTable rows={rows} visible={summary.visible} />
          {summary.hidden > 0 && (
            <div className="flex items-center gap-3 border-t border-line bg-raised px-3 py-2 text-xs text-ink-4">
              <div>
                …and {summary.hidden.toLocaleString("en-US")} more rows — the model saw the first {rows.shown}
              </div>
              <button
                type="button"
                onClick={() => setExpanded(true)}
                className="ml-auto rounded-control border border-line-strong bg-raised-3 px-2.5 py-[5px] text-xs leading-none text-ink-2"
              >
                Show all {rows.rows.length.toLocaleString("en-US")}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
