import type { Catalog } from "./api/client";

export type AgentKey = "sql" | "etl" | "chart";

export const AGENTS: Record<AgentKey, { name: string; color: string; tint: string }> = {
  sql: { name: "QUERY", color: "#7aa2f7", tint: "rgba(122,162,247,.12)" },
  etl: { name: "ETL", color: "#bb9af7", tint: "rgba(187,154,247,.12)" },
  chart: { name: "VISUALIZE", color: "#7dcfff", tint: "rgba(125,207,255,.12)" },
};

export type Example = { agent: AgentKey; question: string; tables: string };

const isDate = (type: string) => /date|time/i.test(type);

/** Example questions drawn from the source's own tables, never a hard-coded schema. */
export function exampleQuestions(catalog: Catalog | null): Example[] {
  if (!catalog || catalog.tables.length === 0) return [];
  const byRows = [...catalog.tables].sort((a, b) => (b.row_estimate ?? 0) - (a.row_estimate ?? 0));
  const examples: Example[] = [];

  const main = byRows[0];
  examples.push({ agent: "sql", question: `How many ${main.name} are there?`, tables: main.name });

  const categorical = byRows
    .flatMap((t) => t.columns.filter((c) => c.values && c.values.length > 1).map((c) => ({ t, c })))[0];
  if (categorical) {
    examples.push({
      agent: "sql",
      question: `How many ${categorical.t.name} are there for each ${categorical.c.name}?`,
      tables: categorical.t.name,
    });
  }

  const dated = byRows.flatMap((t) => t.columns.filter((c) => isDate(c.type)).map((c) => ({ t, c })))[0];
  if (dated) {
    examples.push({
      agent: "chart",
      question: `Plot the number of ${dated.t.name} per month by ${dated.c.name}`,
      tables: dated.t.name,
    });
  }

  const second = byRows[1] ?? main;
  examples.push({ agent: "etl", question: `Save ${second.name} as a CSV file`, tables: second.name });
  return examples;
}
