import { useRef } from "react";
import { AGENTS, type Example } from "../agents";
import type { Source } from "../api/client";
import { Chip } from "./ui";

type HeaderProps = {
  source: Source | null;
  tableCount: number | null;
  showToggle: boolean;
  onToggleSidebar: () => void;
  onCatalog: () => void;
};

export function ChatHeader({ source, tableCount, showToggle, onToggleSidebar, onCatalog }: HeaderProps) {
  const notes = source ? Object.keys(source.notes).length : 0;
  return (
    <header className="flex h-[46px] flex-none items-center gap-2.5 border-b border-line px-4">
      {showToggle && (
        <button
          type="button"
          onClick={onToggleSidebar}
          title="Sources"
          aria-label="Show or hide sources"
          className="flex-none rounded-input border border-line bg-raised-2 px-[9px] py-[5px] font-mono text-xs leading-none text-ink-3"
        >
          ☰
        </button>
      )}
      {source && (
        <div className="flex min-w-0 flex-none items-center gap-[7px] rounded-input border border-line bg-raised-2 px-[9px] py-1">
          <div className="size-1.5 rounded-full bg-success" />
          <div className="font-mono text-[11.5px] leading-none whitespace-nowrap text-ink">{source.name}</div>
          {tableCount !== null && (
            <div className="font-mono text-[11px] leading-none whitespace-nowrap text-ink-4">{tableCount} tables</div>
          )}
        </div>
      )}
      {notes > 0 && (
        <div className="flex-none text-[11.5px] whitespace-nowrap text-success">{notes} {notes === 1 ? "note" : "notes"} teaching the model</div>
      )}
      {source && (
        <button
          type="button"
          onClick={onCatalog}
          className="ml-auto flex-none rounded-input border border-line-strong bg-raised-3 px-2.5 py-[5px] text-[12px] leading-none text-ink-2"
        >
          Catalog
        </button>
      )}
    </header>
  );
}

export type ConnectTarget = "postgres" | "files";

type EmptyProps = {
  source: Source | null;
  examples: Example[];
  onExample: (question: string) => void;
  onConnect: (target: ConnectTarget) => void;
};

const connectButton = "rounded-input border px-3.5 py-2 text-[13px] font-medium leading-none";

export function EmptyState({ source, examples, onExample, onConnect }: EmptyProps) {
  if (!source) {
    return (
      <div className="mx-auto max-w-[720px] pt-[11vh]">
        <h1 className="text-[22px] font-semibold tracking-[-.02em]">Connect your data</h1>
        <p className="mt-[7px] max-w-[520px] leading-[1.6] text-ink-3">
          Connect a Postgres database or upload CSV, Parquet or JSON files, then ask about them in plain English.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onConnect("postgres")}
            className={`${connectButton} border-accent bg-accent text-bg`}
          >
            Connect a database
          </button>
          <button
            type="button"
            onClick={() => onConnect("files")}
            className={`${connectButton} border-line-strong bg-raised-3 text-ink-2`}
          >
            Upload files
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-[720px] pt-[11vh]">
      <h1 className="text-[22px] font-semibold tracking-[-.02em]">Ask about {source.name}</h1>
      <p className="mt-[7px] max-w-[520px] leading-[1.6] text-ink-3">
        Plain English in, SQL and an answer out. A router picks the agent; everything it does shows up on the right as it
        happens.
      </p>
      {examples.length > 0 && (
        <>
          <div className="mt-[26px] font-mono text-[10px] leading-none font-medium tracking-[.09em] text-ink-4">
            TRY ONE OF THESE
          </div>
          <div className="mt-2.5 flex flex-col gap-1.5">
            {examples.map((e) => (
              <button
                key={e.question}
                type="button"
                onClick={() => onExample(e.question)}
                className="flex w-full items-center gap-2.5 rounded-row border border-line bg-raised px-3 py-2.5 text-left text-[13px] text-ink"
              >
                <Chip color={AGENTS[e.agent].color} tint={AGENTS[e.agent].tint}>
                  {AGENTS[e.agent].name}
                </Chip>
                <div className="flex-1">{e.question}</div>
                <div className="flex-none font-mono text-[10.5px] leading-none text-ink-5">{e.tables}</div>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

type ComposerProps = {
  draft: string;
  onDraft: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  busy: boolean;
  sourceName: string | null;
};

export function Composer({ draft, onDraft, onSubmit, disabled, busy, sourceName }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const canSend = !disabled && !busy && draft.trim().length > 0;
  return (
    <div className="flex-none border-t border-line bg-surface px-4 pt-3 pb-3.5">
      <div className="mx-auto max-w-[880px]">
        <div className="flex items-end gap-[9px] rounded-card border border-line-strong bg-raised-2 px-2.5 py-[9px]">
          <textarea
            ref={ref}
            value={draft}
            rows={1}
            disabled={disabled}
            aria-label="Question"
            placeholder={disabled ? "Connect a source to start asking" : "Ask a question, or describe a transform…"}
            onChange={(e) => {
              onDraft(e.target.value);
              // Grow with the text up to the max height, then scroll.
              e.target.style.height = "auto";
              e.target.style.height = `${e.target.scrollHeight}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSend) onSubmit();
              }
            }}
            className="max-h-[120px] flex-1 resize-none border-0 bg-transparent py-0.5 text-[13.5px] leading-[1.6] text-ink outline-none placeholder:text-ink-5 disabled:opacity-60"
          />
          <button
            type="button"
            onClick={onSubmit}
            disabled={!canSend}
            className={`flex-none rounded-input border px-[13px] py-1.5 text-[12.5px] font-medium ${
              canSend ? "border-accent bg-accent text-bg" : "border-line-strong bg-raised-3 text-ink-5"
            }`}
          >
            {busy ? "Running…" : "Send"}
          </button>
        </div>
        <div className="mt-[7px] flex gap-3.5 overflow-hidden font-mono text-[10.5px] leading-none whitespace-nowrap text-ink-5">
          <div>↵ send</div>
          <div>⇧↵ newline</div>
          {sourceName && <div>running against {sourceName}</div>}
        </div>
      </div>
    </div>
  );
}
