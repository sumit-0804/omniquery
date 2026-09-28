import { describe, expect, it } from "vitest";
import type { RunEvent } from "../api/events";
import { chartErrorText, markOf, marksFor, withMark } from "../state/chart";
import { replay } from "../state/run";
import chart from "./fixtures/chart.json";

const recorded = replay(chart as RunEvent[]).chart!.spec!;

describe("swapping the chart type", () => {
  it("reads the agent's mark and offers all four for a vertical chart", () => {
    expect(markOf(recorded)).toBe("line");
    expect(marksFor(recorded)).toEqual(["bar", "line", "area", "point"]);
  });

  it("changes only the mark, keeping the encoding, data and tooltip flag", () => {
    const bar = withMark(recorded, "bar");
    expect(bar.mark).toEqual({ type: "bar", tooltip: true });
    expect(bar.encoding).toEqual(recorded.encoding);
    expect(bar.data).toBe(recorded.data);
    expect(recorded.mark).toEqual({ type: "line", tooltip: true }); // the original is untouched
  });

  it("groups bars side by side when there is a series over categories", () => {
    const spec = {
      mark: { type: "line" },
      encoding: {
        x: { field: "city", type: "nominal" },
        y: { field: "rides", type: "quantitative" },
        color: { field: "status", type: "nominal" },
      },
    };
    expect((withMark(spec, "bar").encoding as Record<string, unknown>).xOffset).toEqual({
      field: "status",
      type: "nominal",
    });
    expect((withMark(spec, "area").encoding as Record<string, unknown>).xOffset).toBeUndefined();
  });

  it("offers only bar and point for horizontal bars", () => {
    const horizontal = {
      mark: { type: "bar" },
      encoding: { x: { field: "n", type: "quantitative" }, y: { field: "city", type: "nominal" } },
    };
    expect(marksFor(horizontal)).toEqual(["bar", "point"]);
  });
});

it("explains chart error codes in words", () => {
  expect(chartErrorText("chart.unsupported_shape")).toMatch(/at least two rows/);
  expect(chartErrorText("chart.other")).toMatch(/chart\.other/);
});
