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
  readonly_role_sql?: string; // only for Postgres sources whose role can write
};

export type AppConfig = { demo: boolean; repo: string };

export type ConnectionCheck = { ok: true; latency_ms: number; version: string; tables: number; readonly: boolean };
export type Output = { name: string; bytes: number; table: string | null };

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw { code: "api.unreachable", detail: "Could not reach the OmniQuery backend." } satisfies ApiError;
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw (body ?? { code: `http.${response.status}`, detail: response.statusText }) as ApiError;
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const sourcePath = (id: string) => `/api/sources/${encodeURIComponent(id)}`;

export const isApiError = (e: unknown): e is ApiError =>
  typeof e === "object" && e !== null && "code" in e && "detail" in e;

export const api = {
  config: () => request<AppConfig>("/api/config"),
  sources: () => request<Source[]>("/api/sources"),
  catalog: (sourceId: string) => request<Catalog>(`${sourcePath(sourceId)}/catalog`),
  testPostgres: (dsn: string, schema: string) =>
    request<ConnectionCheck>("/api/sources/test", json("POST", { dsn, schema })),
  addPostgres: (name: string, dsn: string, schema: string) =>
    request<{ source: Source }>("/api/sources/postgres", json("POST", { name, dsn, schema })),
  addFiles: (name: string, files: File[]) => {
    const form = new FormData();
    form.append("name", name);
    for (const f of files) form.append("files", f);
    return request<{ source: Source }>("/api/sources/files", { method: "POST", body: form });
  },
  setNote: (sourceId: string, path: string, note: string) =>
    request<{ notes: Record<string, string> }>(`${sourcePath(sourceId)}/notes`, json("PUT", { path, note })),
  outputs: () => request<Output[]>("/api/outputs"),
  outputUrl: (name: string) => `/api/outputs/${encodeURIComponent(name)}`,
};
