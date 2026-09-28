// The events the backend streams for one run (see design_handoff_omniquery_web/BACKEND_CONTRACT.md).
// Every event carries `t`: seconds since the question was submitted, measured by the server.

import type { AgentKey } from "../agents";

type Base = { t: number };

export type StepEvent = Base & {
  type: "step";
  id: string; // the graph node that ran, or "submitted"
  label: string;
  status: "active" | "done" | "failed";
  detail?: string;
};
export type RoutingEvent = Base & { type: "routing"; agent: AgentKey; confidence: number; source: "jev" | "llm" | "human" };
export type SqlEvent = Base & { type: "sql"; query: string; attempt: number; notes: string[] };
export type JudgeEvent = Base & { type: "judge"; safe: boolean; comments: string };
export type RetryEvent = Base & { type: "retry"; attempt: number; of: number; code: string; detail: string };
export type RowsEvent = Base & {
  type: "rows";
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  shown: number;
};
export type ChartEvent = Base & { type: "chart"; spec: Record<string, unknown> | null; note: string; error?: string };
export type OutputEvent = Base & { type: "output"; name: string; table: string | null; rows: number; columns: string[] };
export type ProviderEvent = Base & { type: "provider"; provider: string; model: string; fallback: boolean };
export type AnswerEvent = Base & { type: "answer"; text: string; done: boolean };
export type ClarifyEvent = Base & {
  type: "clarify";
  thread_id: string;
  question: string;
  why: string;
  explanation?: string;
  options: { key: AgentKey; label: string }[];
};
export type ErrorEvent = Base & { type: "error"; code: string; detail: string; attempts: number };
export type DoneEvent = Base & {
  type: "done";
  elapsed: number;
  outcome: "answer" | "canceled" | "clarify" | "error" | "stopped";
};

export type RunEvent =
  | StepEvent
  | RoutingEvent
  | SqlEvent
  | JudgeEvent
  | RetryEvent
  | RowsEvent
  | ChartEvent
  | OutputEvent
  | ProviderEvent
  | AnswerEvent
  | ClarifyEvent
  | ErrorEvent
  | DoneEvent;
