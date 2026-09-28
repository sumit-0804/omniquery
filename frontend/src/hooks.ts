import { useEffect, useState } from "react";

/** Viewport width, measured the way the design's breakpoints are (clientWidth). */
export function useViewportWidth(): number {
  const [width, setWidth] = useState(() => document.documentElement.clientWidth);
  useEffect(() => {
    const onResize = () => setWidth(document.documentElement.clientWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}

/** Column widths for the three-column shell, per the handoff's breakpoint table. */
export function shellColumns(width: number, sidebarVisible: boolean): string {
  const [side, rail] =
    width >= 1240 ? [266, 344] : width >= 1060 ? [222, 300] : width >= 760 ? [240, 296] : [230, 260];
  return sidebarVisible ? `${side}px minmax(0,1fr) ${rail}px` : `minmax(0,1fr) ${rail}px`;
}

/** Below 1060px the sidebar hides behind a toggle. */
export const sidebarCollapses = (width: number) => width < 1060;

export function useStoredState(key: string, initial: string | null) {
  const [value, setValue] = useState<string | null>(() => {
    try {
      return localStorage.getItem(key) ?? initial;
    } catch {
      return initial;
    }
  });
  const store = (next: string | null) => {
    setValue(next);
    try {
      if (next === null) localStorage.removeItem(key);
      else localStorage.setItem(key, next);
    } catch {
      // Private windows can refuse storage; the choice just won't persist.
    }
  };
  return [value, store] as const;
}
