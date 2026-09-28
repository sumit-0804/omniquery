import { useMemo, useState } from "react";
import { type Catalog as CatalogData, type CatalogTable, isApiError, type Source } from "../api/client";
import { filterTables, hasNote, keyOf, sampleOf, TABLE_LIST_CAP } from "../state/catalog";

type SaveNote = (path: string, text: string) => Promise<void>;

const buttonClass = "rounded-input border px-2.5 py-1.5 text-[11.5px] leading-none disabled:opacity-50";
const primary = `${buttonClass} border-accent bg-accent text-bg`;
const secondary = `${buttonClass} border-line-strong bg-raised-3 text-ink-2`;

/** A textarea with Save / Cancel that reports its own failure; shared by table and column notes. */
function NoteForm({ path, initial, onSave, onDone, autoFocus }: {
  path: string;
  initial: string;
  onSave: SaveNote;
  onDone?: () => void;
  autoFocus?: boolean;
}) {
  const [draft, setDraft] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = draft.trim() !== initial.trim();

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(path, draft.trim());
      onDone?.();
    } catch (e) {
      setError(isApiError(e) ? e.detail : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <textarea
        aria-label={`Note for ${path}`}
        value={draft}
        autoFocus={autoFocus}
        rows={2}
        placeholder="e.g. amounts are in cents; status 'void' means refunded"
        onChange={(e) => setDraft(e.target.value)}
        className="w-full resize-y rounded-input border border-line-strong bg-raised-2 px-2.5 py-2 text-[12.5px] leading-[1.5] text-ink outline-none placeholder:text-ink-5 focus:border-accent"
      />
      {(changed || onDone) && (
        <div className="mt-1.5 flex items-center gap-1.5">
          <button type="button" className={primary} disabled={!changed || busy} onClick={save}>
            {busy ? "Saving…" : "Save note"}
          </button>
          <button
            type="button"
            className={secondary}
            disabled={busy}
            onClick={() => {
              setDraft(initial);
              onDone?.();
            }}
          >
            Cancel
          </button>
          {error && <div role="alert" className="text-[11px] text-error">{error}</div>}
        </div>
      )}
    </div>
  );
}

function TableDetail({ table, schema, wide, onSave }: {
  table: CatalogTable;
  schema: string;
  wide: boolean;
  onSave: SaveNote;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const span = wide ? 5 : 4;
  const rows = table.row_estimate;

  return (
    <div>
      <div className="font-mono text-[17px] text-ink">{table.name}</div>
      <div className="mt-1 font-mono text-[11px] text-ink-4">
        {rows !== null ? `${rows.toLocaleString("en-US")} rows · ` : ""}
        {schema} schema
        {table.references.length > 0 && ` · references ${table.references.join(", ")}`}
      </div>
      {table.comment && <div className="mt-1.5 text-[12px] text-ink-3">{table.comment}</div>}

      <div
        className="mt-3.5 rounded-card border px-3 py-2.5"
        style={
          table.note
            ? { borderColor: "#23332a", background: "#121813" }
            : { borderColor: "#23272e", background: "#121418" }
        }
      >
        <div className="mb-1.5 flex items-center gap-2 font-mono text-[10px] tracking-[.09em]">
          <span className="text-ink-4">TABLE NOTE</span>
          <span className={table.note ? "text-success" : "text-ink-5"}>
            {table.note ? "sent with every query" : "nothing taught yet"}
          </span>
        </div>
        <NoteForm key={`${table.name}:${table.note}`} path={table.name} initial={table.note} onSave={onSave} />
      </div>

      <div className="mt-4 overflow-x-auto rounded-card border border-line">
        <table className="w-full border-collapse text-xs">
          <thead>
            <tr className="bg-table-head font-mono text-[11px] text-ink-3">
              {["column", "note", "type", "key", ...(wide ? ["sample"] : [])].map((h) => (
                <th key={h} scope="col" className="h-[29px] px-3 text-left font-normal whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.columns.map((col, i) => {
              const path = `${table.name}.${col.name}`;
              const open = editing === path;
              return [
                <tr key={path} className="h-[31px] border-t border-divider">
                  <td className="px-3 font-mono whitespace-nowrap text-ink-2">{col.name}</td>
                  {/* 99% width + max-width 0 lets the note take the spare room and ellipsise. */}
                  <td className="w-[99%] max-w-0 min-w-[160px] px-2 py-1">
                    <button
                      type="button"
                      aria-expanded={open}
                      onClick={() => setEditing(open ? null : path)}
                      title={col.note || undefined}
                      className={`block w-full truncate rounded-chip border-0 px-2 py-1 text-left text-[12px] ${
                        col.note ? "bg-[rgba(158,206,106,.08)] text-success" : "bg-raised-2 text-ink-5"
                      }`}
                    >
                      {col.note || "+ teach the model"}
                    </button>
                  </td>
                  <td className="max-w-[150px] truncate px-3 font-mono whitespace-nowrap text-ink-4" title={col.type}>
                    {col.type}
                  </td>
                  <td className="px-3 font-mono whitespace-nowrap text-ink-4">{keyOf(table, i)}</td>
                  {wide && (
                    <td className="max-w-[220px] truncate px-3 font-mono whitespace-nowrap text-ink-5" title={sampleOf(table, i)}>
                      {sampleOf(table, i)}
                    </td>
                  )}
                </tr>,
                open && (
                  <tr key={`${path}:edit`} className="bg-raised">
                    <td colSpan={span} className="px-3 py-3">
                      <div className="mb-1.5 text-[11.5px] text-ink-3">
                        teaching the model about <span className="font-mono text-ink">{path}</span>
                      </div>
                      <NoteForm path={path} initial={col.note} onSave={onSave} onDone={() => setEditing(null)} autoFocus />
                      <div className="mt-1.5 text-[11px] text-ink-5">
                        Sent with every query against this source, and cited on the answer.
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type Props = {
  gridColumn: string;
  source: Source;
  catalog: CatalogData | null;
  error: string | null;
  wide: boolean;
  lastQuestion: string | null;
  onClose: () => void;
  onSaveNote: SaveNote;
  onRerun: () => void;
};

export function Catalog({ gridColumn, source, catalog, error, wide, lastQuestion, onClose, onSaveNote, onRerun }: Props) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ path: string; removed: boolean; n: number } | null>(null);
  const tables = catalog?.tables ?? [];
  const matching = useMemo(() => filterTables(tables, query), [tables, query]);
  const selected = tables.find((t) => t.name === picked) ?? matching[0] ?? null;
  const notes = Object.keys(source.notes).length;

  const save: SaveNote = async (path, text) => {
    await onSaveNote(path, text);
    setSaved((s) => ({ path, removed: !text, n: (s?.n ?? 0) + 1 }));
  };

  return (
    <section aria-label="Catalog" className="flex min-h-0 flex-col bg-bg" style={{ gridColumn }}>
      <header className="flex h-[46px] flex-none items-center gap-3 border-b border-line px-4">
        <button type="button" onClick={onClose} className={secondary}>
          ← Chat
        </button>
        <div className="font-semibold">Catalog</div>
        <div className="font-mono text-[11px] whitespace-nowrap text-ink-4">
          {source.name} · {catalog ? `${tables.length} tables` : "loading…"}
        </div>
        <input
          aria-label="Filter tables and columns"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter tables and columns"
          className="w-[280px] min-w-0 rounded-input border border-line-strong bg-raised-2 px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink-5 focus:border-accent"
        />
        {notes > 0 && (
          <div className="ml-auto rounded-full bg-[rgba(158,206,106,.12)] px-2.5 py-1 text-[11px] whitespace-nowrap text-success">
            {notes} {notes === 1 ? "note" : "notes"} sent with every query
          </div>
        )}
      </header>

      <div className={`grid min-h-0 flex-1 ${wide ? "grid-cols-[288px_minmax(0,1fr)]" : "grid-cols-[200px_minmax(0,1fr)]"}`}>
        <nav aria-label="Tables" className="min-h-0 overflow-y-auto border-r border-line bg-surface py-2">
          <div className="px-3.5 pb-1.5 font-mono text-[10.5px] text-ink-5">
            {matching.length > TABLE_LIST_CAP
              ? `showing ${TABLE_LIST_CAP} of ${matching.length} matching`
              : `${matching.length} ${query ? "matching" : "tables"}`}
          </div>
          {matching.slice(0, TABLE_LIST_CAP).map((t) => (
            <button
              key={t.name}
              type="button"
              aria-current={t.name === selected?.name}
              onClick={() => setPicked(t.name)}
              className={`flex h-[31px] w-full items-center gap-2 border-0 px-3.5 text-left font-mono text-xs ${
                t.name === selected?.name ? "bg-raised-3 text-ink" : "bg-transparent text-ink-3"
              }`}
            >
              <span
                className="size-1 flex-none rounded-full"
                style={{ background: hasNote(t) ? "#9ece6a" : "#2d323a" }}
                aria-label={hasNote(t) ? "has notes" : undefined}
              />
              <span className="truncate">{t.name}</span>
            </button>
          ))}
        </nav>

        <div className="min-h-0 overflow-y-auto px-6 py-5">
          {saved && (
            <div
              key={saved.n}
              role="status"
              className="mb-4 flex animate-oq-flash items-center gap-3 rounded-card border border-[#23332a] px-3 py-2.5 text-[12.5px] text-ink-2"
            >
              <div className="flex-1">
                {saved.removed ? (
                  <>
                    Removed. <span className="font-mono">{saved.path}</span> is no longer sent.
                  </>
                ) : (
                  <>
                    Saved. <span className="font-mono text-success">{saved.path}</span> will be sent with every query
                    against {source.name}.
                  </>
                )}
              </div>
              {lastQuestion && (
                <button type="button" className={primary} onClick={onRerun}>
                  Re-run last question
                </button>
              )}
            </div>
          )}
          {error ? (
            <div role="alert" className="text-error">{error}</div>
          ) : !catalog ? (
            <div className="text-ink-5">Loading the catalog…</div>
          ) : selected ? (
            <TableDetail key={selected.name} table={selected} schema={catalog.schema} wide={wide} onSave={save} />
          ) : (
            <div className="text-ink-5">No table or column matches “{query}”.</div>
          )}
        </div>
      </div>
    </section>
  );
}
