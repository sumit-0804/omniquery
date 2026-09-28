import { useState } from "react";
import type { RunState } from "../state/run";
import { Answer } from "./Answer";
import { ClarifyCard, ErrorCard, EtlCard, FallbackStrip, NotRunCard } from "./Outcomes";
import { ResultPanel } from "./ResultPanel";

/** The SQL: open while the run works, collapsed once answered; blank between a retry and the next attempt. */
export function SqlBlock({ run, notes }: { run: RunState; notes: Record<string, string> }) {
  const [picked, setPicked] = useState<boolean | null>(null);
  if (!run.sql) return null;
  // When the SQL is the story (refused or failed), keep it open.
  const outcome = run.done?.outcome;
  const open = picked ?? (run.phase === "running" || outcome === "canceled" || outcome === "error");
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

type TurnProps = {
  run: RunState;
  notes: Record<string, string>;
  onReply: (reply: string) => void;
  onRetry: () => void;
  onCatalog: () => void;
};

/** One question and what came back: the outcome or answer, then the result, then the SQL. */
export function Turn({ run, notes, onReply, onRetry, onCatalog }: TurnProps) {
  const outcome = run.done?.outcome;
  const error = run.error;
  // A lost stream or a server fault is worth retrying as is; a schema problem is fixed in the catalog.
  const catalogHelps = error !== null && ["db.query_failed", "db.schema_failed"].includes(error.code);

  let body;
  if (run.clarify) body = <ClarifyCard clarify={run.clarify} onReply={onReply} />;
  else if (outcome === "canceled") body = <NotRunCard judge={run.judge} />;
  else if (error) {
    body = (
      <ErrorCard
        error={error}
        elapsed={run.done?.elapsed ?? null}
        onRetry={run.phase === "done" ? onRetry : null}
        onCatalog={catalogHelps ? onCatalog : null}
      />
    );
  } else body = <Answer text={run.answer} done={run.answerDone} running={run.phase === "running"} />;

  return (
    <div className="mx-auto max-w-[880px] pt-[22px] pb-10">
      <div className="flex items-start gap-2.5">
        <div className="flex-none pt-[3px] font-mono text-[9.5px] leading-[1.6] font-medium text-ink-5">YOU</div>
        <div className="text-[15px] font-medium tracking-[-.01em] text-ink">{run.question}</div>
      </div>
      <div className="mt-5">
        {run.provider?.fallback && run.answer && <FallbackStrip provider={run.provider} />}
        {body}
        {run.output && <EtlCard output={run.output} />}
        {run.rows && <ResultPanel rows={run.rows} chart={run.chart} />}
        <SqlBlock run={run} notes={notes} />
      </div>
    </div>
  );
}
