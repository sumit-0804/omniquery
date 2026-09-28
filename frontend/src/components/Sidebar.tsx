import type { Source } from "../api/client";
import { ConnectPostgres, Outputs, UploadFiles, WriteWarning } from "./Connect";
import { Mark, SectionLabel } from "./ui";

type Props = {
  sources: Source[];
  activeId: string | null;
  onPick: (id: string) => void;
  onAdded: (source: Source) => void;
  outputsVersion: number;
};

function detail(source: Source): string {
  if (source.kind === "postgres") return source.url ?? "";
  const tables = source.tables ?? [];
  return tables.length ? `${tables.length} table${tables.length === 1 ? "" : "s"}: ${tables.join(", ")}` : "no tables yet";
}

export function Sidebar({ sources, activeId, onPick, onAdded, outputsVersion }: Props) {
  const active = sources.find((s) => s.id === activeId);
  return (
    <aside className="flex min-h-0 flex-col overflow-y-auto border-r border-line bg-surface pb-5">
      <div className="flex items-center gap-2 border-b border-line px-3.5 pt-3.5 pb-3">
        <Mark />
        <div className="font-semibold tracking-[-.01em]">OmniQuery</div>
        <div className="ml-auto font-mono text-[10px] leading-none text-ink-4">localhost:7666</div>
      </div>

      <SectionLabel>SOURCES</SectionLabel>
      <div className="flex flex-col gap-1 px-2.5">
        {sources.length === 0 && <div className="px-1 py-2 text-[11.5px] text-ink-5">No sources yet.</div>}
        {sources.map((s) => {
          const isActive = s.id === activeId;
          const dot = isActive ? "#9ece6a" : "#3c434d";
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => onPick(s.id)}
              aria-pressed={isActive}
              className="block w-full rounded-row border border-line bg-raised-2 px-2.5 py-[9px] text-left text-[12.5px] text-ink"
            >
              <div className="flex items-center gap-[7px]">
                <div className="size-1.5 flex-none rounded-full" style={{ background: dot }} />
                <div className="truncate font-medium">{s.name}</div>
                <div
                  className="ml-auto flex-none rounded-chip px-[5px] py-0.5 font-mono text-[9.5px] leading-none"
                  style={{ color: isActive ? dot : "#8a939f", background: isActive ? "rgba(158,206,106,.12)" : "#1b1e23" }}
                >
                  {s.kind === "postgres" ? "PG" : "FILE"}
                </div>
              </div>
              <div className="mt-1 truncate font-mono text-[10.5px] leading-[1.4] text-ink-4">{detail(s)}</div>
            </button>
          );
        })}
      </div>
      {active?.readonly_role_sql && <WriteWarning snippet={active.readonly_role_sql} />}

      <SectionLabel>CONNECT POSTGRES</SectionLabel>
      <ConnectPostgres onAdded={onAdded} />

      <SectionLabel>UPLOAD FILES</SectionLabel>
      <UploadFiles onAdded={onAdded} />

      <SectionLabel>OUTPUTS</SectionLabel>
      <Outputs version={outputsVersion} />
    </aside>
  );
}
