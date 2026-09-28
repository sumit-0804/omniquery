import { type ReactNode, useEffect, useRef, useState } from "react";
import { type ApiError, api, type Catalog, type ConnectionCheck, isApiError, type Output, type Source } from "../api/client";
import { formatBytes, nameFromDsn, nameFromFiles } from "../state/catalog";

const ACCEPT = ".csv,.tsv,.parquet,.json,.jsonl,.ndjson";

const toApiError = (e: unknown): ApiError =>
  isApiError(e) ? e : { code: "client.error", detail: e instanceof Error ? e.message : String(e) };

const inputClass =
  "w-full rounded-input border border-line-strong bg-raised-2 px-2 py-1.5 font-mono text-[11px] text-ink outline-none placeholder:text-ink-5 focus:border-accent";
const buttonClass =
  "rounded-input border border-line-strong bg-raised-3 px-2.5 py-1.5 text-[11.5px] leading-none text-ink-2 disabled:opacity-50";

function StatusCard({ tone, children }: { tone: "ok" | "error" | "busy"; children: ReactNode }) {
  const style = {
    ok: { borderColor: "#23332a", background: "#121813", dot: "#9ece6a" },
    error: { borderColor: "#3a2530", background: "#181316", dot: "#f7768e" },
    busy: { borderColor: "#2a3550", background: "#131720", dot: "#7aa2f7" },
  }[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className="mt-2 rounded-input border px-2.5 py-2 text-[11px] leading-[1.5]"
      style={{ borderColor: style.borderColor, background: style.background }}
    >
      <div className="flex items-start gap-[7px]">
        <div
          className={`mt-[5px] size-1.5 flex-none rounded-full ${tone === "busy" ? "animate-oq-pulse" : ""}`}
          style={{ background: style.dot }}
        />
        <div className="min-w-0 flex-1 break-words">{children}</div>
      </div>
    </div>
  );
}

export function ConnectPostgres({ onAdded }: { onAdded: (source: Source) => void }) {
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState("public");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<"test" | "add" | null>(null);
  const [check, setCheck] = useState<ConnectionCheck | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const finalName = name.trim() || nameFromDsn(dsn);

  const reset = () => {
    setCheck(null);
    setError(null);
  };

  const run = async (kind: "test" | "add") => {
    setBusy(kind);
    reset();
    try {
      if (kind === "test") {
        setCheck(await api.testPostgres(dsn.trim(), schema.trim() || "public"));
      } else {
        const { source } = await api.addPostgres(finalName, dsn.trim(), schema.trim() || "public");
        setDsn("");
        setName("");
        onAdded(source);
      }
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="px-3.5">
      <label className="sr-only" htmlFor="oq-dsn">Postgres connection URL</label>
      <input
        id="oq-dsn"
        className={inputClass}
        value={dsn}
        placeholder="postgresql://user:pass@host:5432/db"
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => {
          setDsn(e.target.value);
          reset();
        }}
      />
      <div className="mt-1.5 flex gap-1.5">
        <input
          aria-label="Schema"
          className={inputClass}
          value={schema}
          placeholder="schema"
          spellCheck={false}
          onChange={(e) => {
            setSchema(e.target.value);
            reset();
          }}
        />
        <input
          aria-label="Source name"
          className={inputClass}
          value={name}
          placeholder={nameFromDsn(dsn) || "name"}
          spellCheck={false}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div className="mt-1.5 flex gap-1.5">
        <button type="button" className={buttonClass} disabled={!dsn.trim() || busy !== null} onClick={() => run("test")}>
          {busy === "test" ? "Testing…" : "Test connection"}
        </button>
        <button
          type="button"
          className={buttonClass}
          disabled={!dsn.trim() || !finalName || busy !== null}
          onClick={() => run("add")}
        >
          {busy === "add" ? "Adding…" : "Add"}
        </button>
      </div>
      {check && (
        <StatusCard tone="ok">
          <div className="text-ink">Connected · {check.latency_ms} ms</div>
          <div className="font-mono text-[10.5px] text-ink-4">
            PostgreSQL {check.version.split(" ")[0]} · {check.tables} tables ·{" "}
            {check.readonly ? "role is read-only" : "role can write"}
          </div>
        </StatusCard>
      )}
      {error && (
        <StatusCard tone="error">
          <div className="text-error">{error.code}</div>
          <div className="font-mono text-[10.5px] text-ink-3">{error.detail}</div>
        </StatusCard>
      )}
    </div>
  );
}

type Uploaded = { source: Source; catalog: Catalog | null };

export function UploadFiles({ onAdded }: { onAdded: (source: Source) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<Uploaded | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const upload = async (files: File[]) => {
    if (!files.length || busy) return;
    setBusy(files.map((f) => f.name).join(", "));
    setDone(null);
    setError(null);
    try {
      const { source } = await api.addFiles(nameFromFiles(files), files);
      // The catalog gives the rows and columns the add response leaves out.
      const catalog = await api.catalog(source.id).catch(() => null);
      setDone({ source, catalog });
      onAdded(source);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div className="px-3.5">
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          upload([...e.dataTransfer.files]);
        }}
        className={`block cursor-pointer rounded-input border border-dashed px-3 py-3.5 text-center text-[11.5px] leading-[1.5] has-[input:focus]:border-accent ${
          dragging ? "border-accent bg-[#131720] text-ink-2" : "border-line-strong text-ink-4"
        }`}
      >
        Drop CSV, Parquet or JSON files, or <span className="text-accent underline">browse</span>
        <input
          ref={input}
          id="oq-upload"
          type="file"
          multiple
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => upload([...(e.target.files ?? [])])}
        />
      </label>
      {busy && (
        <StatusCard tone="busy">
          <div className="text-accent">Validating…</div>
          <div className="truncate font-mono text-[10.5px] text-ink-4">{busy}</div>
        </StatusCard>
      )}
      {done && (
        <StatusCard tone="ok">
          <div className="text-ink">Added {done.source.name}</div>
          {(done.catalog?.tables ?? []).map((t) => (
            <div key={t.name} className="mt-1 font-mono text-[10.5px] text-ink-4">
              <span className="text-ink-2">{t.name}</span> · {(t.row_estimate ?? 0).toLocaleString("en-US")} rows ·{" "}
              {t.columns.length} columns · ready
              <div className="truncate text-ink-5" title={t.columns.map((c) => `${c.name} ${c.type}`).join(", ")}>
                {t.columns.map((c) => `${c.name} ${c.type}`).join(", ")}
              </div>
            </div>
          ))}
        </StatusCard>
      )}
      {error && (
        <StatusCard tone="error">
          <div className="text-error">{error.code}</div>
          <div className="font-mono text-[10.5px] text-ink-3">{error.detail}</div>
        </StatusCard>
      )}
    </div>
  );
}

export function WriteWarning({ snippet }: { snippet: string }) {
  const [copied, setCopied] = useState<"yes" | "failed" | null>(null);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(null), 1400);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <div className="mx-2.5 mt-2 rounded-input border border-[#3a3122] bg-[#1b1811] px-2.5 py-2.5">
      <div className="text-[12px] font-medium text-warn">This role can write</div>
      <div className="mt-1 text-[11px] leading-[1.5] text-ink-3">
        Writes are already refused at the connection level. For a second guard, connect with a read-only role:
      </div>
      <pre className="mt-2 overflow-x-auto rounded-chip bg-bg px-2 py-1.5 font-mono text-[10px] leading-[1.6] text-ink-2">
        {snippet}
      </pre>
      <button
        type="button"
        onClick={() =>
          navigator.clipboard.writeText(snippet).then(
            () => setCopied("yes"),
            () => setCopied("failed"),
          )
        }
        className="mt-2 w-full rounded-input border border-[#3a3122] bg-transparent py-1.5 text-[11.5px] text-warn"
      >
        {copied === "yes" ? "Copied" : copied === "failed" ? "Copy failed — select the text above" : "Copy snippet"}
      </button>
    </div>
  );
}

export function Outputs({ version }: { version: number }) {
  const [outputs, setOutputs] = useState<Output[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .outputs()
      .then((o) => current && (setOutputs(o), setError(false)))
      .catch(() => current && setError(true));
    return () => {
      current = false;
    };
  }, [version]);

  if (error) return <div className="px-3.5 text-[11px] text-error">Could not load outputs.</div>;
  if (!outputs) return null;
  if (!outputs.length) return <div className="px-3.5 text-[11px] text-ink-5">Transforms you run are saved here.</div>;
  return (
    <div className="flex flex-col px-2.5">
      {[...outputs].reverse().map((o) => (
        <div key={o.name} className="flex items-center gap-2 rounded-row px-1 py-[5px]">
          <div className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink-2" title={o.name}>
            {o.name}
          </div>
          <div className="flex-none font-mono text-[10px] text-ink-5">{formatBytes(o.bytes)}</div>
          <a
            href={api.outputUrl(o.name)}
            download={o.name}
            className="flex-none text-[11px] text-accent no-underline hover:underline"
          >
            Download
          </a>
        </div>
      ))}
    </div>
  );
}
