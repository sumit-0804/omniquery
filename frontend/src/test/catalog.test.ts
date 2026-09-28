import { describe, expect, it } from "vitest";
import type { Catalog, CatalogColumn, CatalogTable } from "../api/client";
import { applyNotes, filterTables, formatBytes, hasNote, keyOf, nameFromDsn, nameFromFiles, sampleOf } from "../state/catalog";

const column = (name: string, extra: Partial<CatalogColumn> = {}): CatalogColumn => ({
  name,
  type: "text",
  comment: null,
  pk: false,
  fk: null,
  values: null,
  values_complete: false,
  note: "",
  ...extra,
});
const table = (name: string, columns: CatalogColumn[], extra: Partial<CatalogTable> = {}): CatalogTable => ({
  name,
  comment: null,
  row_estimate: 10,
  note: "",
  references: [],
  columns,
  samples: [],
  ...extra,
});

const rides = table("rides", [column("ride_id", { pk: true }), column("status", { values: ["completed", "cancelled"] })], {
  samples: [[1, "completed"], [2, null]],
});
const users = table("users", [column("user_id", { pk: true }), column("city_id", { fk: "cities.city_id" })]);
const catalog: Catalog = { source: "demo", schema: "public", tables: [rides, users] };

describe("filterTables", () => {
  it("matches table names and column names, ignoring case", () => {
    expect(filterTables(catalog.tables, "RIDE").map((t) => t.name)).toEqual(["rides"]);
    expect(filterTables(catalog.tables, "city").map((t) => t.name)).toEqual(["users"]);
    expect(filterTables(catalog.tables, "  ")).toHaveLength(2);
  });
});

describe("applyNotes", () => {
  it("puts table and column notes in place, and clears removed ones", () => {
    const noted = applyNotes(catalog, { rides: "one row per request", "users.city_id": "home city" });
    expect(noted.tables[0].note).toBe("one row per request");
    expect(noted.tables[1].columns[1].note).toBe("home city");
    expect(hasNote(noted.tables[1])).toBe(true);

    const cleared = applyNotes(noted, {});
    expect(cleared.tables.some(hasNote)).toBe(false);
  });
});

it("describes keys and samples for the column table", () => {
  expect(keyOf(rides, 0)).toBe("PK");
  expect(keyOf(users, 1)).toBe("→ cities.city_id");
  expect(sampleOf(rides, 1)).toBe("completed, cancelled");
  expect(sampleOf(rides, 0)).toBe("1, 2");
});

it("suggests source names from a URL or the first file", () => {
  expect(nameFromDsn("postgresql://u:p@localhost:5432/omniquery?sslmode=disable")).toBe("omniquery");
  expect(nameFromDsn("postgres://host/db")).toBe("db");
  expect(nameFromDsn("not a url")).toBe("");
  expect(nameFromFiles([{ name: "sales.2025.csv" }, { name: "b.csv" }])).toBe("sales.2025");
});

it("formats byte sizes", () => {
  expect(formatBytes(124)).toBe("124 B");
  expect(formatBytes(12281)).toBe("12.0 KB");
  expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
});
