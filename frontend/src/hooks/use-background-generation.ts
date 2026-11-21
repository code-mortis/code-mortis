import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Repository, type Schema } from "../api";
import { activityQueryKey } from "./use-activity-read";

type BackgroundJob = Schema["BackgroundJobOut"];

export function backgroundFinished(status: BackgroundJob["status"] | undefined) {
  return status === "done" || status === "failed" || status === "cancelled";
}

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
  const cache = useQueryClient();
  return useMutation({
    onSuccess: () => { void cache.invalidateQueries({ queryKey: activityQueryKey }); },
    mutationFn: (body: Partial<Schema["BackgroundInput"]>) =>
      api<BackgroundJob>(`/repositories/${repositoryId}/background`, "POST", body),
  });
}

export function useBackgroundStatus(repositoryId: number, jobId?: string) {
  const path = `/repositories/${repositoryId}/background/${encodeURIComponent(jobId ?? "")}`;
  return useQuery({
    queryKey: [path],
    queryFn: () => api<BackgroundJob>(path),
    enabled: !!jobId,
    retry: (count, error) => count < 3 && isBackgroundConnectionError(error),
    retryDelay: 1000,
    refetchInterval: (query) => {
      if (backgroundFinished(query.state.data?.status)) return false;
      const error = query.state.error;
      return error ? isBackgroundConnectionError(error) ? 4000 : false : 1000;
    },
  });
}
