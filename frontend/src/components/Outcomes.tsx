import { useState } from "react";
import Markdown from "react-markdown";
import { AGENTS } from "../agents";
import { api } from "../api/client";
import type { ClarifyEvent, ErrorEvent, JudgeEvent, OutputEvent, ProviderEvent } from "../api/events";
import { errorHeadline, errorMeta, outputFormat } from "../state/outcome";
import { Chip } from "./ui";

const eyebrow = "font-mono text-[9.5px] leading-none font-medium tracking-[.09em]";
const actionClass = "rounded-input border px-2.5 py-1.5 text-[12px] leading-none disabled:opacity-50";

/** A question, not an error: blue, with one button per agent and a box for anything else. */
export function ClarifyCard({ clarify, onReply }: { clarify: ClarifyEvent; onReply: (reply: string) => void }) {
  const [text, setText] = useState("");
  const send = () => {
    if (text.trim()) onReply(text.trim());
  };
  return (
    <div className="rounded-card border border-[#2a3550] bg-[#131720] px-[18px] py-4">
      <div className={`${eyebrow} text-accent`}>ONE QUESTION FIRST</div>
      <div className="mt-2 text-[15.5px] leading-[1.55] text-ink">{clarify.question}</div>
      {clarify.why && <div className="mt-1.5 text-[12.5px] leading-[1.55] text-ink-3">{clarify.why}</div>}
      {clarify.explanation && (
        <div className="oq-answer mt-3 border-l-2 border-[#2a3550] pl-3 text-[13px] leading-[1.6] text-ink-2">
          <Markdown allowedElements={["p", "strong", "em", "ul", "ol", "li", "code"]} unwrapDisallowed>
            {clarify.explanation}
          </Markdown>
        </div>
      )}
      <div className="mt-3.5 flex flex-col gap-1.5">
        {clarify.options.map((o) => (
          <button
            key={o.key}
            type="button"
            onClick={() => onReply(o.key)}
            className="flex w-full items-center gap-2.5 rounded-row border border-[#2a3550] bg-[#161b26] px-3 py-2.5 text-left text-[13px] text-ink"
          >
            <Chip color={AGENTS[o.key].color} tint={AGENTS[o.key].tint}>
              {AGENTS[o.key].name}
            </Chip>
            <span>{o.label}</span>
          </button>
        ))}
      </div>
      <div className="mt-2.5 flex gap-1.5">
        <input
          aria-label="Answer in your own words"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Or ask what the options mean, or say cancel"
          className="min-w-0 flex-1 rounded-input border border-[#2a3550] bg-[#10141c] px-2.5 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-ink-5 focus:border-accent"
        />
        <button type="button" onClick={send} disabled={!text.trim()} className={`${actionClass} border-accent bg-accent text-bg`}>
          Reply
        </button>
      </div>
    </div>
  );
}

/** The judge refused a write: calm and blue, with its reason. The SQL stays below, read-only. */
export function NotRunCard({ judge }: { judge: JudgeEvent | null }) {
  return (
    <div className="rounded-card border border-[#2a3550] bg-[#131720] px-[18px] py-4">
      <div className={`${eyebrow} text-accent`}>NOT RUN</div>
      <div className="mt-2 text-[15px] leading-[1.55] text-ink">
        This query would change data, so it was not run. OmniQuery only reads.
      </div>
      {judge?.comments && <div className="mt-1.5 font-mono text-[12px] text-ink-3">{judge.comments}</div>}
    </div>
  );
}

type ErrorProps = {
  error: ErrorEvent;
  elapsed: number | null;
  onRetry: (() => void) | null;
  onCatalog: (() => void) | null;
};

export function ErrorCard({ error, elapsed, onRetry, onCatalog }: ErrorProps) {
  const [open, setOpen] = useState(false);
  const meta = errorMeta(error, elapsed);
  return (
    <div role="alert" className="rounded-card border border-[#3a2530] bg-[#181316] px-[18px] py-4">
      <div className="flex items-center gap-2">
        <Chip color="#f7768e" tint="rgba(247,118,142,.12)">
          {error.code}
        </Chip>
        {meta && <div className="font-mono text-[11px] text-ink-4">{meta}</div>}
      </div>
      <div className="mt-2.5 text-[14px] leading-[1.55] text-ink">{errorHeadline(error)}</div>
      {(onRetry || onCatalog) && (
        <div className="mt-3 flex gap-1.5">
          {onRetry && (
            <button type="button" onClick={onRetry} className={`${actionClass} border-line-strong bg-raised-3 text-ink-2`}>
              Try again
            </button>
          )}
          {onCatalog && (
            <button type="button" onClick={onCatalog} className={`${actionClass} border-line-strong bg-raised-3 text-ink-2`}>
              Open catalog
            </button>
          )}
        </div>
      )}
      {error.detail && (
        <>
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
            className="mt-3 border-0 bg-transparent p-0 font-mono text-[11px] text-ink-4"
          >
            {open ? "▾" : "▸"} technical detail
          </button>
          {open && (
            <pre className="mt-1.5 overflow-x-auto rounded-input bg-bg px-3 py-2 font-mono text-[11px] leading-[1.6] whitespace-pre-wrap text-ink-3">
              {error.detail}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

/** Honest and quiet: a fallback model answered. Not an error. */
export function FallbackStrip({ provider }: { provider: ProviderEvent }) {
  return (
    <div className="mb-3 border-l-2 border-warn bg-[#15161a] px-3 py-2 text-[12px] leading-[1.5] text-ink-3">
      Answered by <span className="font-mono text-warn">{provider.model}</span>; the first-choice model was busy or out
      of budget. Still a valid answer; quality may be a little lower.
    </div>
  );
}

export function EtlCard({ output }: { output: OutputEvent }) {
  return (
    <div className="mt-4 overflow-hidden rounded-card border border-[#2d2440] bg-[#16141c]">
      <div className="flex items-center gap-2.5 px-3.5 py-2.5">
        <Chip color={AGENTS.etl.color} tint={AGENTS.etl.tint}>
          {AGENTS.etl.name}
        </Chip>
        <div className="min-w-0 truncate font-mono text-[12px] text-ink">{output.name}</div>
        <div className="ml-auto flex-none font-mono text-[11px] text-ink-4">
          {output.rows.toLocaleString("en-US")} rows · {output.columns.length} columns · {outputFormat(output.name)}
        </div>
      </div>
      <div
        className="truncate border-t border-[#2d2440] px-3.5 py-2 font-mono text-[11px] text-ink-4"
        title={output.columns.join(", ")}
      >
        {output.columns.join(", ")}
      </div>
      <div className="flex items-center gap-3 border-t border-[#2d2440] px-3.5 py-2">
        <a
          href={api.outputUrl(output.name)}
          download={output.name}
          className="rounded-input border border-[#2d2440] px-2.5 py-1.5 text-[12px] leading-none text-etl no-underline"
        >
          Download
        </a>
        {output.table && (
          <div className="text-[11.5px] text-ink-4">
            Also a table in the <span className="font-mono">workspace</span> source: <span className="font-mono">{output.table}</span>
          </div>
        )}
      </div>
    </div>
  );
}
