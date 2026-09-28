import type {
  ChartEvent,
  ClarifyEvent,
  DoneEvent,
  ErrorEvent,
  JudgeEvent,
  OutputEvent,
  ProviderEvent,
  RetryEvent,
  RoutingEvent,
  RowsEvent,
  RunEvent,
  SqlEvent,
} from "../api/events";

/** One line in the run rail. A node that runs twice (a retry) gets two lines. */
export type RailStep = {
  key: number;
  id: string;
  label: string;
  status: "active" | "done" | "failed";
  t: number; // when it last changed, from the server
  detail?: string;
};

export type RunState = {
  runId: number; // new per question, kept across resumes; keys the turn's local UI state
  phase: "idle" | "running" | "done";
  question: string;
  threadId: string | null;
  steps: RailStep[];
  routing: RoutingEvent | null;
  sql: SqlEvent | null; // query is "" between a retry and the next attempt
  retries: RetryEvent[];
  judge: JudgeEvent | null;
  rows: RowsEvent | null;
  chart: ChartEvent | null;
  output: OutputEvent | null;
  provider: ProviderEvent | null;
  answer: string;
  answerDone: boolean;
  clarify: ClarifyEvent | null;
  error: ErrorEvent | null;
  done: DoneEvent | null;
  lastT: number;
  tOffset: number; // a resumed run's clock starts at 0 again; this keeps the rail's times rising
};

export const idleRun: RunState = {
  runId: 0,
  phase: "idle",
  question: "",
  threadId: null,
  steps: [],
  routing: null,
  sql: null,
  retries: [],
  judge: null,
  rows: null,
  chart: null,
  output: null,
  provider: null,
  answer: "",
  answerDone: false,
  clarify: null,
  error: null,
  done: null,
  lastT: 0,
  tOffset: 0,
};

export type RunAction =
  | { type: "start"; question: string }
  | { type: "resume" } // same run, continuing after a clarify
  | { type: "event"; event: RunEvent }
  | { type: "lost"; detail: string };

let nextKey = 1;

function applyStep(steps: RailStep[], e: Extract<RunEvent, { type: "step" }>): RailStep[] {
  // Update the open (active) line for this node; otherwise this is a new line.
  const open = steps.findLastIndex((s) => s.id === e.id && s.status === "active");
  if (open >= 0) {
    const updated = { ...steps[open], status: e.status, t: e.t, label: e.label, detail: e.detail ?? steps[open].detail };
    return steps.map((s, i) => (i === open ? updated : s));
  }
  if (e.status === "active") {
    const last = steps.at(-1);
    // A duplicate "active" for a line that is already the latest is a no-op.
    if (last?.id === e.id && last.status === "active") return steps;
  }
  return [...steps, { key: nextKey++, id: e.id, label: e.label, status: e.status, t: e.t, detail: e.detail }];
}

function applyEvent(state: RunState, raw: RunEvent): RunState {
  const e = { ...raw, t: raw.t + state.tOffset } as RunEvent;
  const s = { ...state, lastT: Math.max(state.lastT, e.t) };
  switch (e.type) {
    case "step":
      return { ...s, steps: applyStep(s.steps, e) };
    case "routing":
      return { ...s, routing: e };
    case "sql":
      return { ...s, sql: e };
    case "retry":
      // Blank the SQL: the next attempt streams in visibly, which is the signal it was regenerated.
      return { ...s, retries: [...s.retries, e], sql: s.sql ? { ...s.sql, query: "" } : s.sql };
    case "judge":
      return { ...s, judge: e };
    case "rows":
      return { ...s, rows: e };
    case "chart":
      return { ...s, chart: e };
    case "output":
      return { ...s, output: e };
    case "provider":
      return { ...s, provider: e };
    case "answer":
      return e.done ? { ...s, answerDone: true } : { ...s, answer: s.answer + e.text };
    case "clarify":
      return { ...s, clarify: e, threadId: e.thread_id };
    case "error":
      return { ...s, error: e };
    case "done":
      return { ...s, done: e, phase: "done" };
  }
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "start":
      return { ...idleRun, runId: state.runId + 1, phase: "running", question: action.question };
    case "resume":
      return { ...state, phase: "running", clarify: null, done: null, tOffset: state.lastT };
    case "event":
      return applyEvent(state, action.event);
    case "lost":
      // The connection dropped before `done`; say so instead of spinning forever.
      return {
        ...state,
        phase: "done",
        steps: state.steps.map((s) => (s.status === "active" ? { ...s, status: "failed" } : s)),
        error: { type: "error", t: state.lastT, code: "stream.lost", detail: action.detail, attempts: 0 },
      };
  }
}

/** Replays a recorded stream, for tests and for reasoning about a run. */
export function replay(events: RunEvent[], question = ""): RunState {
  return events.reduce(
    (state, event) => runReducer(state, { type: "event", event }),
    runReducer(idleRun, { type: "start", question }),
  );
}

/** Which attempt failed with what, for the retry line on the SQL step. */
export function attemptSummary(state: RunState): { attempt: number; of: number; code: string; spent: boolean } | null {
  const last = state.retries.at(-1);
  if (!last) return null;
  const spent = state.error !== null;
  return { attempt: spent ? last.of : last.attempt + 1, of: last.of, code: last.code, spent };
}
