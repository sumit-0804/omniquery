import type { ReactNode } from "react";

/** Mono section label with a trailing hairline, e.g. SOURCES ────── */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 px-3.5 pt-3.5 pb-1.5">
      <div className="font-mono text-[10px] leading-none font-medium tracking-[.09em] text-ink-4">{children}</div>
      <div className="h-px flex-1 bg-line" />
    </div>
  );
}

/** Agent or status chip: colour on a 12% tint of itself. */
export function Chip({ color, tint, children }: { color: string; tint: string; children: ReactNode }) {
  return (
    <span
      className="flex-none rounded-chip px-[5px] py-[3px] font-mono text-[9.5px] leading-none font-medium"
      style={{ color, background: tint }}
    >
      {children}
    </span>
  );
}

/** The app's only mark: a bordered square with an inset fill. */
export function Mark() {
  return (
    <div className="grid size-[18px] place-items-center rounded-chip border-[1.5px] border-accent">
      <div className="size-1.5 rounded-[1px] bg-accent" />
    </div>
  );
}
