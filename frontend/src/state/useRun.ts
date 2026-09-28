import { useCallback, useEffect, useReducer, useRef } from "react";
import type { RunEvent } from "../api/events";
import { idleRun, runReducer } from "./run";

/** Runs a question over SSE and keeps the reducer state for the screen. */
export function useRun() {
  const [state, dispatch] = useReducer(runReducer, idleRun);
  const source = useRef<EventSource | null>(null);

  const close = useCallback(() => {
    source.current?.close();
    source.current = null;
  }, []);

  const listen = useCallback(
    (url: string) => {
      close();
      const es = new EventSource(url);
      source.current = es;
      es.onmessage = (message) => {
        const event = JSON.parse(message.data) as RunEvent;
        dispatch({ type: "event", event });
        if (event.type === "done") close();
      };
      es.onerror = () => {
        // EventSource reconnects on its own, which would re-run the question; stop instead.
        if (source.current === es) {
          close();
          dispatch({ type: "lost", detail: "The connection to the backend closed before the run finished." });
        }
      };
    },
    [close],
  );

  const ask = useCallback(
    (question: string, sourceId: string) => {
      dispatch({ type: "start", question });
      listen(`/api/ask?${new URLSearchParams({ q: question, source: sourceId })}`);
    },
    [listen],
  );

  const resume = useCallback(
    (threadId: string, reply: string) => {
      dispatch({ type: "resume" });
      listen(`/api/ask/${encodeURIComponent(threadId)}/resume?${new URLSearchParams({ reply })}`);
    },
    [listen],
  );

  /** Dev only: play a recorded stream at its real pace, to review a state without an LLM call. */
  const play = useCallback(
    (events: RunEvent[], question: string) => {
      close();
      dispatch({ type: "start", question });
      const timers = events.map((event) =>
        window.setTimeout(() => dispatch({ type: "event", event }), event.t * 1000),
      );
      return () => timers.forEach(window.clearTimeout);
    },
    [close],
  );

  useEffect(() => close, [close]);

  return { state, ask, resume, play };
}
