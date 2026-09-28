import { useEffect, useMemo, useState } from "react";
import { exampleQuestions } from "./agents";
import { api, type Catalog, type Source, WORKSPACE_ID } from "./api/client";
import { ChatHeader, Composer, EmptyState } from "./components/Chat";
import { Rail } from "./components/Rail";
import { Sidebar } from "./components/Sidebar";
import { Turn } from "./components/Turn";
import { shellColumns, sidebarCollapses, useStoredState, useViewportWidth } from "./hooks";
import { useRun } from "./state/useRun";

/** User sources first; the workspace (ETL outputs) last, and only once it holds something. */
function askable(sources: Source[]): Source[] {
  const user = sources.filter((s) => s.id !== WORKSPACE_ID);
  const workspace = sources.find((s) => s.id === WORKSPACE_ID && (s.tables?.length ?? 0) > 0);
  return workspace ? [...user, workspace] : user;
}

export default function App() {
  const width = useViewportWidth();
  const collapses = sidebarCollapses(width);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const sidebarVisible = !collapses || sidebarOpen;

  const [sources, setSources] = useState<Source[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [storedId, setStoredId] = useStoredState("oq.activeSource", null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [draft, setDraft] = useState("");
  const { state: run, ask, play } = useRun();

  // Dev only: ?replay=retry plays src/test/fixtures/retry.json through the real reducer.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const name = new URLSearchParams(location.search).get("replay");
    if (!name) return;
    // The import is async, so a cleanup can arrive before playback starts; the flag covers that.
    let cancelled = false;
    let stop: (() => void) | undefined;
    import(`./test/fixtures/${name}.json`).then((m) => {
      if (!cancelled) stop = play(m.default, `(replay) ${name}`);
    });
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [play]);

  useEffect(() => {
    api
      .sources()
      .then((all) => setSources(askable(all)))
      .catch(() => setLoadError("Could not reach the OmniQuery backend on 127.0.0.1:7666. Is `omniquery serve` running?"));
  }, []);

  const active = useMemo(
    () => sources?.find((s) => s.id === storedId) ?? sources?.[0] ?? null,
    [sources, storedId],
  );

  useEffect(() => {
    setCatalog(null);
    if (!active) return;
    let current = true;
    api.catalog(active.id).then((c) => current && setCatalog(c)).catch(() => current && setCatalog(null));
    return () => {
      current = false;
    };
  }, [active]);

  const examples = useMemo(() => exampleQuestions(catalog), [catalog]);

  return (
    <div
      className="grid h-screen min-h-0 bg-bg"
      style={{ gridTemplateColumns: shellColumns(width, sidebarVisible) }}
    >
      {sidebarVisible && (
        <Sidebar
          sources={sources ?? []}
          activeId={active?.id ?? null}
          onPick={(id) => {
            setStoredId(id);
            if (collapses) setSidebarOpen(false);
          }}
        />
      )}

      <main className="flex min-h-0 min-w-0 flex-col bg-bg">
        <ChatHeader
          source={active}
          tableCount={catalog ? catalog.tables.length : null}
          showToggle={collapses}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
        />
        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {loadError ? (
            <div role="alert" className="mx-auto max-w-[720px] pt-[11vh] text-ink-3">
              {loadError}
            </div>
          ) : run.phase !== "idle" ? (
            <Turn key={run.runId} run={run} notes={active?.notes ?? {}} />
          ) : (
            sources && <EmptyState source={active} examples={examples} onExample={setDraft} />
          )}
        </div>
        <Composer
          draft={draft}
          onDraft={setDraft}
          onSubmit={() => {
            if (!active || !draft.trim()) return;
            ask(draft.trim(), active.id);
            setDraft("");
          }}
          disabled={!active}
          busy={run.phase === "running"}
          sourceName={active?.name ?? null}
        />
      </main>

      <Rail run={run} />
    </div>
  );
}
