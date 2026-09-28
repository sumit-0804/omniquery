import type { ErrorEvent } from "../api/events";

// Only for errors raised outside an agent; agent failures bring their own headline from the backend.
const RUN_HEADLINES: Record<string, string> = {
  "stream.lost": "The connection to the backend closed before the run finished.",
  "server.error": "Something went wrong on the server. The details are in its log.",
  "run.not_paused": "That question is no longer waiting for an answer. Ask it again.",
  "source.not_found": "That data source is not saved.",
};

export function errorHeadline(error: ErrorEvent): string {
  return error.headline || RUN_HEADLINES[error.code] || "The request could not be completed.";
}

/** "after 3 attempts · 5.8s", leaving out what does not apply. */
export function errorMeta(error: ErrorEvent, elapsed: number | null): string {
  const parts = [];
  if (error.attempts > 1) parts.push(`after ${error.attempts} attempts`);
  if (elapsed !== null && elapsed > 0) parts.push(`${elapsed.toFixed(1)}s`);
  return parts.join(" · ");
}

export const outputFormat = (name: string) => name.split(".").pop()?.toUpperCase() ?? "";
