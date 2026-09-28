import { useState } from "react";
import { AGENTS } from "../agents";
import { attemptSummary, type RailStep, type RunState } from "../state/run";
import { Chip } from "./ui";

// Steps where a retry lands: the retry line hangs off the most recent one.
const RETRY_TARGETS = new Set(["generate_sql", "plan_etl"]);

const DOT = { done: "#9ece6a", active: "#7aa2f7", failed: "#f7768e" } as const;
const STATUS_WORD = { done: "done", active: "in progress", failed: "failed" } as const;

const seconds = (t: number) => `${t.toFixed(1)}s`;

function RetryLine({ run }: { run: RunState }) {
  const [open, setOpen] = useState(false);
  const summary = attemptSummary(run);
  if (!summary) return null;
  const red = summary.spent;
  // Details arrive as "{code}: {message}"; show the code once, in red.
  const split = (detail: string, fallback: string) => {
    const m = /^([^:\s]+):\s*(.*)$/s.exec(detail);
    return m ? { code: m[1], text: m[2] } : { code: fallback, text: detail };
  };
  const failures = [
    ...run.retries.map((r) => ({ code: r.code, text: split(r.detail, r.code).text })),
    ...(red && run.error ? [split(run.error.detail, run.error.code)] : []),
  ];
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="mt-[5px] flex items-center gap-1.5 rounded-control border px-[7px] py-1 font-mono text-[10.5px] leading-none"
        style={
          red
            ? { borderColor: "#3a2530", background: "#181316", color: "#f7768e" }
            : { borderColor: "#3a3122", background: "#1b1811", color: "#e0af68" }
        }
      >
        <span className="text-ink-5">{open ? "▾" : "▸"}</span>
        attempt {summary.attempt} of {summary.of} · {summary.code}
      </button>
      {open && (
        <div className="mt-1.5 rounded-input border border-[#3a2530] bg-[#181316] px-[9px] py-2">
          {failures.map((f, i) => (
            <div key={i} className="py-[3px] font-mono text-[10.5px] leading-[1.6] break-words text-ink-3">
              <span className="text-error">{f.code}</span> {f.text}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function StepRow({ step, run, showRetry }: { step: RailStep; run: RunState; showRetry: boolean }) {
  const active = step.status === "active";
  return (
    <li className="grid animate-oq-in grid-cols-[16px_minmax(0,1fr)] gap-[9px]">
      <div className="relative flex flex-col items-center">
        <div
          className={`mt-1 size-[9px] flex-none rounded-full ${active ? "animate-oq-pulse" : ""}`}
          style={{ background: DOT[step.status], boxShadow: active ? "0 0 0 3px rgba(122,162,247,.16)" : "none" }}
        />
        <div
          className="relative my-[3px] w-px flex-1 overflow-hidden"
          style={{ background: step.status === "done" ? "#2d323a" : "#23272e" }}
        >
          {/* The app's primary animation: machinery working, not a spinner. */}
          {active && (
            <div className="absolute inset-x-0 top-0 h-[40%] animate-oq-sweep bg-[linear-gradient(180deg,transparent,#7aa2f7,transparent)]" />
          )}
        </div>
      </div>
      <div className="min-w-0 pb-[13px]">
        <div className="flex items-baseline gap-[7px]">
          <div
            className="text-[12.5px] font-medium"
            style={{ color: step.status === "failed" ? "#f7768e" : active ? "#e7eaef" : "#c8d0dc" }}
          >
            {step.label}
            <span className="sr-only"> — {STATUS_WORD[step.status]}</span>
          </div>
          <div className="ml-auto flex-none font-mono text-[10px] leading-none text-ink-5">
            {active ? "" : seconds(step.t)}
          </div>
        </div>
        {step.detail && (
          <div className="mt-[3px] font-mono text-[11px] leading-[1.55] break-words text-ink-4">{step.detail}</div>
        )}
        {showRetry && <RetryLine run={run} />}
      </div>
    </li>
  );
}

function ProviderFooter({ run }: { run: RunState }) {
  const p = run.provider;
  if (!p) return null;
  const color = p.fallback ? "#e0af68" : "#9ece6a";
  return (
    <div className="flex-none border-t border-line px-[13px] pt-2.5 pb-3">
      <div className="flex items-center gap-[7px]">
        <div className="size-[5px] rounded-full" style={{ background: color }} />
        <div className="font-mono text-[11px] leading-none text-ink-3">{p.model}</div>
        <div className="ml-auto font-mono text-[10px] leading-none" style={{ color }}>
          {p.fallback ? "FALLBACK" : "PRIMARY"}
        </div>
      </div>
      <div className="mt-[5px] text-[11px] leading-[1.5] text-ink-5">
        {p.fallback
          ? `Answered by ${p.provider}; an earlier model in the chain was busy or out of budget.`
          : `Answered by ${p.provider}, the first choice in the chain.`}
      </div>
    </div>
  );
}

export function Rail({ run }: { run: RunState }) {
  const agent = run.routing ? AGENTS[run.routing.agent] : null;
  const retryAt = run.retries.length
    ? run.steps.findLastIndex((s) => RETRY_TARGETS.has(s.id))
    : -1;
  const clock = run.done ? run.done.elapsed : run.lastT;

  return (
    <aside aria-label="Run" className="flex min-h-0 flex-col border-l border-line bg-surface">
      <div className="flex h-[46px] flex-none items-center gap-2 border-b border-line px-[13px]">
        <div className="font-mono text-[10px] leading-none font-medium tracking-[.09em] text-ink-4">RUN</div>
        {agent && (
          <Chip color={agent.color} tint={agent.tint}>
            {agent.name}
          </Chip>
        )}
        {run.phase !== "idle" && (
          <div
            className="ml-auto font-mono text-[11px] leading-none"
            style={{ color: run.phase === "running" ? "#7aa2f7" : "#8a939f" }}
          >
            {seconds(clock)}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-[13px] pt-3 pb-5">
        {run.phase === "idle" ? (
          <div className="pt-1.5 text-xs leading-[1.7] text-ink-5">
            Nothing running.
            <br />
            Every event the backend streams — routing, each pipeline step, retries, the SQL, the model that answered —
            lands here in order and stays as the record of the last run.
          </div>
        ) : (
          <ol aria-live="polite" className="m-0 flex list-none flex-col p-0">
            {run.steps.map((step, i) => (
              <StepRow key={step.key} step={step} run={run} showRetry={i === retryAt} />
            ))}
          </ol>
        )}
      </div>

      <ProviderFooter run={run} />
    </aside>
  );
}
