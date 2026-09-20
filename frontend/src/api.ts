import type { Preview, RunDetail, RunSummary } from "./types";
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
async function request<T>(path: string, data?: unknown): Promise<T> {
  const res = await fetch(
    `/api${path}`,
    data === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(data),
        },
  );
  if (!res.ok) {
    let message = `Request failed (${res.status}). Please try again.`;
    try {
      const payload = await res.json();
      message = typeof payload.detail === "string" ? payload.detail : message;
    } catch {
      /* non-JSON upstream error */
    }
    throw new ApiError(message, res.status);
  }
  return res.json();
}
const runPath = (id: string) => `/runs/${encodeURIComponent(id)}`;
export const api = {
  runs: () => request<{ runs: RunSummary[] }>("/runs"),
  run: (id: string) => request<RunDetail>(runPath(id)),
  demo: (variant: "opening" | "followup" | "clean") =>
    request<RunDetail>("/demo", { variant }),
  create: (name: string, scorer_csv: string, league_csv: string) =>
    request<RunDetail>("/runs", { name, scorer_csv, league_csv }),
  preview: (
    id: string,
    caseId: string,
    action: "select" | "exclude",
    candidate_id?: string,
  ) =>
    request<Preview>(
      `${runPath(id)}/cases/${encodeURIComponent(caseId)}/preview`,
      { action, candidate_id },
    ),
  resolve: (
    id: string,
    caseId: string,
    data: {
      action: "select" | "exclude";
      candidate_id?: string;
      expected_version: number;
      note: string;
      reviewer: string;
    },
  ) =>
    request<RunDetail>(
      `${runPath(id)}/cases/${encodeURIComponent(caseId)}/resolve`,
      data,
    ),
  replay: (id: string, reviewer: string) =>
    request<{ applied: number; run: RunDetail }>(`${runPath(id)}/replay`, {
      reviewer,
    }),
  exportUrl: (id: string) => `/api${runPath(id)}/export`,
};
