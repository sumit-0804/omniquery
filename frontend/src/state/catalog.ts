import type { Catalog, CatalogTable } from "../api/client";

export const TABLE_LIST_CAP = 80;

/** Tables whose name, or any column's name, contains the query. */
export function filterTables(tables: CatalogTable[], query: string): CatalogTable[] {
  const q = query.trim().toLowerCase();
  if (!q) return tables;
  return tables.filter((t) => t.name.toLowerCase().includes(q) || t.columns.some((c) => c.name.toLowerCase().includes(q)));
}

export const hasNote = (t: CatalogTable) => Boolean(t.note) || t.columns.some((c) => c.note);

/** The catalog with its notes replaced by the server's current set. */
export function applyNotes(catalog: Catalog, notes: Record<string, string>): Catalog {
  return {
    ...catalog,
    tables: catalog.tables.map((t) => ({
      ...t,
      note: notes[t.name] ?? "",
      columns: t.columns.map((c) => ({ ...c, note: notes[`${t.name}.${c.name}`] ?? "" })),
    })),
  };
}

/** Known values for low-cardinality columns, else the first sample rows. */
export function sampleOf(table: CatalogTable, column: number): string {
  const col = table.columns[column];
  if (col.values?.length) return col.values.join(", ");
  return table.samples
    .map((row) => row[column])
    .filter((v) => v !== null && v !== undefined)
    .slice(0, 3)
    .map(String)
    .join(", ");
}

export function keyOf(table: CatalogTable, column: number): string {
  const col = table.columns[column];
  if (col.pk) return "PK";
  return col.fk ? `→ ${col.fk}` : "";
}

/** A default source name: the database in a Postgres URL. */
export function nameFromDsn(dsn: string): string {
  const m = /^postgres(?:ql)?:\/\/[^/]*\/([^?#/]+)/i.exec(dsn.trim());
  return m ? decodeURIComponent(m[1]) : "";
}

export const nameFromFiles = (files: { name: string }[]) => files[0]?.name.replace(/\.[^.]+$/, "") ?? "";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
