import { useEffect, useRef, useState } from "react";
import embed, { type Result } from "vega-embed";
import type { Config } from "vega-lite";
import { type MarkType, withMark } from "../state/chart";

// The default Vega theme is light; this matches the app (README §5).
const THEME: Config = {
  background: "transparent",
  view: { stroke: null },
  font: "IBM Plex Mono",
  title: {
    color: "#c8d0dc",
    subtitleColor: "#8a939f",
    font: "IBM Plex Sans",
    fontSize: 12.5,
    fontWeight: 500,
    anchor: "start",
    subtitleFont: "IBM Plex Sans",
  },
  axis: {
    labelColor: "#98a1ad",
    titleColor: "#8a939f",
    gridColor: "#1d2126",
    domainColor: "#2d323a",
    tickColor: "#2d323a",
    labelFont: "IBM Plex Mono",
    labelFontSize: 10,
    titleFont: "IBM Plex Mono",
    titleFontSize: 10,
    titleFontWeight: "normal",
  },
  // Compact SI numbers (10.3k); the design's "$" prefix assumed revenue, and most results are counts.
  axisQuantitative: { format: ",.3~s" },
  legend: {
    orient: "top",
    symbolType: "square",
    labelColor: "#98a1ad",
    titleColor: "#8a939f",
    labelFont: "IBM Plex Mono",
    titleFont: "IBM Plex Mono",
    labelFontSize: 10,
    titleFontSize: 10,
  },
  range: { category: ["#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#7dcfff"] },
  mark: { color: "#7aa2f7" },
  line: { strokeWidth: 2 },
  point: { filled: true, size: 45 },
};

/** One vega-embed view; it re-renders only when the spec object or the mark changes. */
export default function Chart({ spec, mark }: { spec: Record<string, unknown>; mark: MarkType }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    let view: Result | undefined;
    (async () => {
      // Canvas measures text once, so draw after the mono font is ready, not with a fallback.
      await document.fonts.load("10px 'IBM Plex Mono'");
      if (cancelled) return;
      try {
        const result = await embed(el, withMark(spec, mark) as never, {
          actions: false,
          renderer: "canvas",
          config: THEME,
          tooltip: { theme: "dark" },
        });
        if (cancelled) result.finalize();
        else view = result;
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
      view?.finalize();
    };
  }, [spec, mark]);

  if (error) {
    return <div className="px-3 py-3 text-xs text-ink-4">The chart could not be drawn: {error}</div>;
  }
  return (
    <div className="min-h-[340px] px-2.5 pt-3.5 pb-2.5">
      <div ref={ref} className="w-full" />
    </div>
  );
}
