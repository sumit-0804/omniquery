import { describe, expect, it } from "vitest";
import type { RunEvent } from "../api/events";
import { attemptSummary, idleRun, replay, runReducer } from "../state/run";
import canceled from "./fixtures/canceled.json";
import chart from "./fixtures/chart.json";
import clarify from "./fixtures/clarify.json";
import clarifyExplain from "./fixtures/clarify-explain.json";
import clarifyResume from "./fixtures/clarify-resume.json";
import error from "./fixtures/error.json";
import etl from "./fixtures/etl.json";
import retry from "./fixtures/retry.json";
import sqlAnswer from "./fixtures/sql-answer.json";

const events = (fixture: unknown) => fixture as RunEvent[];
const stepIds = (fixture: unknown) => replay(events(fixture)).steps.map((s) => s.id);

describe("a plain SQL answer", () => {
  const run = replay(events(sqlAnswer), "cancellations by reason");

  it("ends done, with every step closed", () => {
    expect(run.phase).toBe("done");
    expect(run.done?.outcome).toBe("answer");
    expect(run.steps.every((s) => s.status !== "active")).toBe(true);
  });

  it("shows each node once, in run order, starting with submitted", () => {
    const ids = run.steps.map((s) => s.id);
    expect(ids[0]).toBe("submitted");
    expect(ids.indexOf("generate_sql")).toBeLessThan(ids.indexOf("execute_sql"));
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("joins the streamed answer and keeps the SQL, rows and provider", () => {
    expect(run.answer.length).toBeGreaterThan(20);
    expect(run.answerDone).toBe(true);
    expect(run.sql?.query).toMatch(/cancellation_reason/i);
    expect(run.rows?.row_count).toBeGreaterThan(1);
    expect(run.provider?.model).toBeTruthy();
    expect(run.routing?.agent).toBe("sql");
  });

  it("uses the server's times, which never go backwards", () => {
    const times = run.steps.map((s) => s.t);
    expect(times).toEqual([...times].sort((a, b) => a - b));
  });
});

describe("a retry", () => {
  const run = replay(events(retry));

  it("shows the regenerated node twice and keeps the final SQL", () => {
    expect(stepIds(retry).filter((id) => id === "generate_sql")).toHaveLength(2);
    expect(run.sql?.query).toMatch(/avg\(fare\)/);
    expect(run.sql?.attempt).toBe(2);
  });

  it("marks the failed validation and summarises the attempt in amber", () => {
    expect(run.steps.find((s) => s.id === "validate_sql")?.status).toBe("failed");
    expect(attemptSummary(run)).toEqual({ attempt: 2, of: 3, code: "42703", spent: false });
  });

  it("blanks the SQL between the retry and the next attempt", () => {
    const upToRetry = events(retry).slice(0, events(retry).findIndex((e) => e.type === "retry") + 1);
    expect(replay(upToRetry).sql?.query).toBe("");
  });
});

describe("every attempt failing", () => {
  const run = replay(events(error));

  it("ends as an error with the retries spent", () => {
    expect(run.done?.outcome).toBe("error");
    expect(run.error?.code).toBe("db.query_failed");
    expect(attemptSummary(run)?.spent).toBe(true);
    expect(attemptSummary(run)?.attempt).toBe(3);
  });
});

describe("a write", () => {
  const run = replay(events(canceled));

  it("is canceled, not an error", () => {
    expect(run.done?.outcome).toBe("canceled");
    expect(run.judge?.safe).toBe(false);
    expect(run.error).toBeNull();
    expect(run.steps.find((s) => s.id === "check_readonly")?.status).toBe("failed");
  });
});

describe("the chart and etl routes", () => {
  it("keep the chart spec", () => {
    const run = replay(events(chart));
    expect(run.routing?.agent).toBe("chart");
    expect(run.chart?.spec).toBeTruthy();
  });

  it("keep the saved output", () => {
    const run = replay(events(etl));
    expect(run.routing?.agent).toBe("etl");
    expect(run.output?.rows).toBeGreaterThan(0);
  });
});

describe("clarify, then resume on the same run", () => {
  it("pauses with a thread id and the three agents", () => {
    const run = replay(events(clarify));
    expect(run.done?.outcome).toBe("clarify");
    expect(run.clarify?.options.map((o) => o.key)).toEqual(["sql", "etl", "chart"]);
    expect(run.threadId).toBeTruthy();
  });

  it("keeps the rail's clock rising across resumes", () => {
    let run = replay(events(clarify));
    const pausedAt = run.lastT;
    for (const part of [clarifyExplain, clarifyResume]) {
      run = runReducer(run, { type: "resume" });
      run = events(part).reduce((s, event) => runReducer(s, { type: "event", event }), run);
    }
    expect(run.done?.outcome).toBe("answer");
    expect(run.steps.at(-1)!.t).toBeGreaterThan(pausedAt);
    const times = run.steps.map((s) => s.t);
    expect(times).toEqual([...times].sort((a, b) => a - b));
  });
});

it("a dropped connection fails the open step instead of spinning", () => {
  const partial = events(sqlAnswer).slice(0, 4);
  const run = runReducer(replay(partial), { type: "lost", detail: "closed" });
  expect(run.phase).toBe("done");
  expect(run.error?.code).toBe("stream.lost");
  expect(run.steps.some((s) => s.status === "active")).toBe(false);
});

it("starting a new question clears the last run", () => {
  const run = runReducer(replay(events(sqlAnswer)), { type: "start", question: "next" });
  expect(run).toEqual({ ...idleRun, runId: 2, phase: "running", question: "next" });
});
