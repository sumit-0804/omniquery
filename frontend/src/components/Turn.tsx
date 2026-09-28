import { useState } from "react";
import type { RunState } from "../state/run";
import { Answer } from "./Answer";
import { ResultPanel } from "./ResultPanel";

/** The SQL: open while the run works, collapsed once answered; blank between a retry and the next attempt. */
export function SqlBlock({ run, notes }: { run: RunState; notes: Record<string, string> }) {
  const [picked, setPicked] = useState<boolean | null>(null);
  if (!run.sql) return null;
  const open = picked ?? run.phase === "running";
  const attempts = run.retries.length ? `attempt ${run.sql.attempt} of ${run.retries[0].of}` : null;
  return (
    <div className="mt-3 overflow-hidden rounded-card border border-line bg-panel">
      <div className="flex items-center gap-[9px] bg-raised pr-[11px]">
        <button
          type="button"
          onClick={() => setPicked(!open)}
          aria-expanded={open}
          className="flex items-center gap-[9px] border-0 bg-transparent py-2 pl-[11px] text-left text-ink"
        >
          <div className="w-2 font-mono text-[10px] leading-none text-ink-4">{open ? "▾" : "▸"}</div>
          <div className="font-mono text-[11px] leading-none font-medium tracking-[.06em] text-ink-3">SQL</div>
        </button>
        {attempts && (
          <div className="rounded-chip bg-[rgba(224,175,104,.11)] px-[5px] py-[3px] font-mono text-[10px] leading-none text-warn">
            {attempts}
          </div>
        )}
        {/* The payoff of teaching notes: which ones the model was given for this query. */}
        {run.sql.notes.map((path) => (
          <div
            key={path}
            title={notes[path] || undefined}
            className="rounded-chip bg-[rgba(158,206,106,.11)] px-[5px] py-[3px] font-mono text-[10px] leading-none text-success"
          >
            note · {path}
          </div>
        ))}
      </div>
      {open && (
        <pre className="m-0 min-h-[46px] overflow-x-auto border-t border-line px-3.5 py-[13px] font-mono text-xs leading-[1.7] whitespace-pre text-ink-2">
          {run.sql.query || " "}
        </pre>
      )}
    </div>
  );
}

/** One question and what came back: answer, then the result, then the SQL. */
export function Turn({ run, notes }: { run: RunState; notes: Record<string, string> }) {
  return (
    <div className="mx-auto max-w-[880px] pt-[22px] pb-10">
      <div className="flex items-start gap-2.5">
        <div className="flex-none pt-[3px] font-mono text-[9.5px] leading-[1.6] font-medium text-ink-5">YOU</div>
        <div className="text-[15px] font-medium tracking-[-.01em] text-ink">{run.question}</div>
      </div>
      <div className="mt-5">
        <Answer text={run.answer} done={run.answerDone} running={run.phase === "running"} />
        {run.rows && <ResultPanel rows={run.rows} />}
        <SqlBlock run={run} notes={notes} />
      </div>
    </div>
  );
}
