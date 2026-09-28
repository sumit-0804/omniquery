export const MARKS = ["bar", "line", "area", "point"] as const;
export type MarkType = (typeof MARKS)[number];

type Spec = Record<string, unknown>;
type Channel = { field?: string; type?: string };
const encodingOf = (spec: Spec) => (spec.encoding ?? {}) as Record<string, Channel | undefined>;

export function markOf(spec: Spec): MarkType {
  const mark = spec.mark;
  const type = typeof mark === "string" ? mark : (mark as { type?: string } | undefined)?.type;
  return (MARKS as readonly string[]).includes(type ?? "") ? (type as MarkType) : "bar";
}

/** Horizontal bars (categories on y) only read as bars or points; lines across categories mislead. */
export function marksFor(spec: Spec): MarkType[] {
  return encodingOf(spec).y?.type === "quantitative" ? [...MARKS] : ["bar", "point"];
}

/** The agent's spec with only the mark swapped: no request, same encoding. */
export function withMark(spec: Spec, mark: MarkType): Spec {
  const encoding = { ...encodingOf(spec) };
  const color = encoding.color?.field;
  const xType = encoding.x?.type;
  // Grouped, not stacked, bars when there is a series and a categorical x.
  if (mark === "bar" && color && (xType === "nominal" || xType === "ordinal")) {
    encoding.xOffset = { field: color, type: "nominal" };
  }
  const base = typeof spec.mark === "object" && spec.mark ? spec.mark : {};
  return { ...spec, height: 300, mark: { ...base, type: mark }, encoding };
}

const CHART_ERRORS: Record<string, string> = {
  "chart.unsupported_shape": "No chart for this result: it needs at least two rows, a label column and a numeric column.",
  "chart.invalid_spec": "The chart the model designed was not valid, so only the table is shown.",
};

export const chartErrorText = (code: string) => CHART_ERRORS[code] ?? `No chart for this result (${code}).`;
