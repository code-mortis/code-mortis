import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Repository, type Schema } from "../api";

type BackgroundJob = Schema["BackgroundJobOut"];

export type BackgroundHandoff = { jobId: string; generationId: string; mode: "context" | "summary_draft"; inputs: string };

export function isBackgroundConnectionError(error: unknown) {
  return !(error instanceof ApiError) || error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500;
}

export function savedBackgroundInputs(repository: Repository) {
  const { background, model, token_budget, timeout_seconds, project_summary } = repository.settings;
  return JSON.stringify([repository.id, repository.connection_id, model, token_budget ?? null, timeout_seconds ?? null, background,
    project_summary ?? null]);
}

export function useStartBackground(repositoryId: number) {
  return useMutation({
    mutationFn: (body: Partial<Schema["BackgroundInput"]>) =>
      api<BackgroundJob>(`/repositories/${repositoryId}/background`, "POST", body),
  });
}

export function useBackgroundStatus(repositoryId: number, jobId?: string) {
  const path = `/repositories/${repositoryId}/background/${jobId ?? ""}`;
  return useQuery({
    queryKey: [path],
    queryFn: () => api<BackgroundJob>(path),
    enabled: !!jobId,
    retry: (count, error) => count < 3 && isBackgroundConnectionError(error),
    retryDelay: 1000,
    refetchInterval: (query) => query.state.error || ["done", "failed", "cancelled"].includes(query.state.data?.status ?? "") ? false : 1000,
  });
}
