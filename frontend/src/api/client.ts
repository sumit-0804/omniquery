// Typed calls to the FastAPI backend. Vite proxies /api to 127.0.0.1:7666.

export type Source = {
  id: string;
  name: string;
  kind: "postgres" | "files";
  url?: string; // always masked by the server
  schema?: string;
  files?: string[];
  tables?: string[];
  notes: Record<string, string>;
  readonly: boolean;
};

export type CatalogColumn = {
  name: string;
  type: string;
  comment: string | null;
  pk: boolean;
  fk: string | null;
  values: string[] | null;
  values_complete: boolean;
  note: string;
};

export type CatalogTable = {
  name: string;
  comment: string | null;
  row_estimate: number | null;
  note: string;
  references: string[];
  columns: CatalogColumn[];
  samples: unknown[][];
};

export type Catalog = { source: string; schema: string; tables: CatalogTable[] };

export type ApiError = { code: string; detail: string };

export const WORKSPACE_ID = "workspace";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const body = await response.json();
  if (!response.ok) throw body as ApiError;
  return body as T;
}

export const api = {
  sources: () => getJson<Source[]>("/api/sources"),
  catalog: (sourceId: string) => getJson<Catalog>(`/api/sources/${encodeURIComponent(sourceId)}/catalog`),
};
