import { useCallback, useEffect, useMemo, useState } from "react";
import { exampleQuestions } from "./agents";
import { api, type Catalog as CatalogData, isApiError, type Source, WORKSPACE_ID } from "./api/client";
import { Catalog } from "./components/Catalog";
import { ChatHeader, Composer, type ConnectTarget, EmptyState } from "./components/Chat";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Rail } from "./components/Rail";
import { Sidebar } from "./components/Sidebar";
import { Turn } from "./components/Turn";
import { shellColumns, sidebarCollapses, useStoredState, useViewportWidth } from "./hooks";
import { applyNotes } from "./state/catalog";
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
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [view, setView] = useState<"chat" | "catalog">("chat");
  const [outputsVersion, setOutputsVersion] = useState(0);
  const [focusTarget, setFocusTarget] = useState<ConnectTarget | null>(null);
  const [draft, setDraft] = useState("");
  const { state: run, ask, resume, play } = useRun();

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

  const loadSources = useCallback(
    () =>
      api
        .sources()
        .then((all) => {
          setSources(askable(all));
          setLoadError(null);
        })
        .catch(() => setLoadError("Could not reach the OmniQuery backend on 127.0.0.1:7666. Is `omniquery serve` running?")),
    [],
  );
  useEffect(() => {
    loadSources();
  }, [loadSources]);

  // An ETL run adds a file to OUTPUTS and a table to the workspace source.
  useEffect(() => {
    if (run.done && run.output) {
      setOutputsVersion((v) => v + 1);
      loadSources();
    }
  }, [run.done, run.output, loadSources]);

  const active = useMemo(
    () => sources?.find((s) => s.id === storedId) ?? sources?.[0] ?? null,
    [sources, storedId],
  );
  const activeId = active?.id ?? null;

  // Keyed on the id, not the object: a notes update replaces the source but not its tables.
  useEffect(() => {
    setCatalog(null);
    setCatalogError(null);
    if (!activeId) return;
    let current = true;
    api
      .catalog(activeId)
      .then((c) => current && setCatalog(c))
      .catch((e) => current && setCatalogError(isApiError(e) ? e.detail : "Could not load the catalog."));
    return () => {
      current = false;
    };
  }, [activeId]);

  const examples = useMemo(() => exampleQuestions(catalog), [catalog]);

  // The sidebar mounts only once it is open, so focus after that render rather than in the click.
  useEffect(() => {
    if (!focusTarget || !sidebarVisible) return;
    const el = document.getElementById(focusTarget === "postgres" ? "oq-dsn" : "oq-upload");
    (el?.closest("label") ?? el)?.scrollIntoView({ block: "center" });
    el?.focus();
    setFocusTarget(null);
  }, [focusTarget, sidebarVisible]);

  const saveNote = async (path: string, text: string) => {
    if (!active) return;
    const { notes } = await api.setNote(active.id, path, text);
    setSources((all) => all?.map((s) => (s.id === active.id ? { ...s, notes } : s)) ?? all);
    setCatalog((c) => (c ? applyNotes(c, notes) : c));
  };

  const submit = (question: string) => {
    if (!active || !question.trim()) return;
    ask(question.trim(), active.id);
  };

  return (
    <div
      className="grid h-screen min-h-0 bg-bg"
      style={{ gridTemplateColumns: shellColumns(width, sidebarVisible) }}
    >
      {sidebarVisible && (
        <Sidebar
          sources={sources ?? []}
          activeId={activeId}
          onPick={(id) => {
            setStoredId(id);
            if (collapses) setSidebarOpen(false);
          }}
          onAdded={(source) => {
            setStoredId(source.id);
            loadSources();
          }}
          outputsVersion={outputsVersion}
        />
      )}

      {view === "catalog" && active ? (
        <Catalog
          gridColumn={sidebarVisible ? "2 / 4" : "1 / -1"}
          source={active}
          catalog={catalog}
          error={catalogError}
          wide={width >= 1160}
          lastQuestion={run.question || null}
          onClose={() => setView("chat")}
          onSaveNote={saveNote}
          onRerun={() => {
            setView("chat");
            submit(run.question);
          }}
        />
      ) : (
        <>
          <main className="flex min-h-0 min-w-0 flex-col bg-bg">
            <ChatHeader
              source={active}
              tableCount={catalog ? catalog.tables.length : null}
              showToggle={collapses}
              onToggleSidebar={() => setSidebarOpen((open) => !open)}
              onCatalog={() => setView("catalog")}
            />
            <div className="min-h-0 flex-1 overflow-y-auto px-4">
              {loadError ? (
                <div role="alert" className="mx-auto max-w-[720px] pt-[11vh] text-ink-3">
                  {loadError}
                </div>
              ) : run.phase !== "idle" ? (
                <ErrorBoundary key={run.runId}>
                  <Turn
                    run={run}
                    notes={active?.notes ?? {}}
                    onReply={(reply) => run.threadId && resume(run.threadId, reply)}
                    onRetry={() => submit(run.question)}
                    onCatalog={() => setView("catalog")}
                  />
                </ErrorBoundary>
              ) : (
                sources && (
                  <EmptyState
                    source={active}
                    examples={examples}
                    onExample={setDraft}
                    onConnect={(target) => {
                      setSidebarOpen(true);
                      setFocusTarget(target);
                    }}
                  />
                )
              )}
            </div>
            <Composer
              draft={draft}
              onDraft={setDraft}
              onSubmit={() => {
                submit(draft);
                setDraft("");
              }}
              disabled={!active}
              busy={run.phase === "running"}
              sourceName={active?.name ?? null}
            />
          </main>

          <Rail run={run} />
        </>
      )}
    </div>
  );
}
