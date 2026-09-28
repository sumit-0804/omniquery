import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ErrorEvent, RunEvent } from "../api/events";
import { Turn } from "../components/Turn";
import { errorHeadline, errorMeta, outputFormat } from "../state/outcome";
import { replay, runReducer } from "../state/run";
import error from "./fixtures/error.json";
import sqlAnswer from "./fixtures/sql-answer.json";

const err = (extra: Partial<ErrorEvent>): ErrorEvent => ({ type: "error", t: 0, code: "x", detail: "", attempts: 0, ...extra });

it("uses the agent's own headline for a spent retry loop", () => {
  const run = replay(error as RunEvent[]);
  expect(errorHeadline(run.error!)).toBe("The database rejected the generated query.");
  expect(errorMeta(run.error!, 5.84)).toBe("after 3 attempts · 5.8s");
});

it("has plain words for errors raised outside an agent", () => {
  const lost = runReducer(replay((sqlAnswer as RunEvent[]).slice(0, 4)), { type: "lost", detail: "closed" });
  expect(errorHeadline(lost.error!)).toMatch(/connection to the backend closed/);
  expect(errorHeadline(err({ code: "something.new" }))).toBe("The request could not be completed.");
  expect(errorMeta(err({ attempts: 1 }), null)).toBe("");
});

describe("the fallback strip", () => {
  const render = (fallback: boolean) => {
    const run = replay(sqlAnswer as RunEvent[]);
    const withProvider = { ...run, provider: { ...run.provider!, fallback } };
    const noop = () => {};
    return renderToStaticMarkup(
      createElement(Turn, { run: withProvider, notes: {}, onReply: noop, onRetry: noop, onCatalog: noop }),
    );
  };

  it("names the fallback model above the answer", () => {
    expect(render(true)).toMatch(/Still a valid answer/);
  });

  it("stays hidden when the first-choice model answered", () => {
    expect(render(false)).not.toMatch(/Still a valid answer/);
  });
});

it("reads the output format from the file name", () => {
  expect(outputFormat("users.parquet")).toBe("PARQUET");
  expect(outputFormat("monthly_revenue.csv")).toBe("CSV");
});
