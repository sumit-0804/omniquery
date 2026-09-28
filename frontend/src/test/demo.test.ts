import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { Catalog as CatalogData, Source } from "../api/client";
import { Catalog } from "../components/Catalog";
import { EmptyState } from "../components/Chat";
import { Sidebar } from "../components/Sidebar";

const noop = () => {};
const source: Source = {
  id: "rides",
  name: "rides",
  kind: "postgres",
  url: "hosted demo database",
  notes: { rides: "one row per request" },
  readonly: false,
  readonly_role_sql: "CREATE ROLE ...",
};
const catalog: CatalogData = {
  source: "rides",
  schema: "public",
  tables: [
    {
      name: "rides",
      comment: null,
      row_estimate: 10,
      note: "one row per request",
      references: [],
      columns: [
        { name: "fare", type: "numeric", comment: null, pk: false, fk: null, values: null, values_complete: false, note: "" },
      ],
      samples: [],
    },
  ],
};

const sidebar = (demo: boolean) =>
  renderToStaticMarkup(
    createElement(Sidebar, { sources: [source], activeId: "rides", onPick: noop, onAdded: noop, outputsVersion: 0, demo }),
  );

it("the demo sidebar lists the source but offers no way to add data", () => {
  const html = sidebar(true);
  expect(html).toContain("rides");
  for (const hidden of ["CONNECT POSTGRES", "UPLOAD FILES", "OUTPUTS", "This role can write"]) {
    expect(html).not.toContain(hidden);
  }
  expect(sidebar(false)).toContain("CONNECT POSTGRES");
});

const catalogView = (onSaveNote: ((p: string, t: string) => Promise<void>) | null) =>
  renderToStaticMarkup(
    createElement(Catalog, {
      gridColumn: "2 / 4",
      source,
      catalog,
      error: null,
      wide: true,
      lastQuestion: null,
      onClose: noop,
      onSaveNote,
      onRerun: noop,
    }),
  );

it("the demo catalog shows notes but cannot edit them", () => {
  const html = catalogView(null);
  expect(html).toContain("one row per request");
  expect(html).not.toContain("<textarea");
  expect(html).not.toContain("+ teach the model");
  expect(catalogView(async () => {})).toContain("+ teach the model");
});

it("the demo never offers to connect data when its source is missing", () => {
  const html = renderToStaticMarkup(
    createElement(EmptyState, { source: null, examples: [], demo: true, onExample: noop, onConnect: noop }),
  );
  expect(html).toContain("demo data isn");
  expect(html).not.toContain("Connect a database");
});
