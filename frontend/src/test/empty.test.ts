import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { Source } from "../api/client";
import { EmptyState } from "../components/Chat";

const noop = () => {};
const render = (source: Source | null) =>
  renderToStaticMarkup(createElement(EmptyState, { source, examples: [], onExample: noop, onConnect: noop }));

it("a first run offers both ways to connect data", () => {
  const html = render(null);
  expect(html).toContain("Connect your data");
  expect(html).toContain("Connect a database");
  expect(html).toContain("Upload files");
});

it("once a source exists, it asks about that source instead", () => {
  const source: Source = { id: "rides", name: "rides", kind: "postgres", notes: {}, readonly: true };
  const html = render(source);
  expect(html).toContain("Ask about rides");
  expect(html).not.toContain("Connect a database");
});
