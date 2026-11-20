import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError, type Repository, type Schema } from "../api";

type BackgroundJob = Schema["BackgroundJobOut"];

export function savedBackgroundInputs(repository: Repository) {
  const { background, model, token_budget, timeout_seconds } = repository.settings;
  return JSON.stringify([repository.id, repository.connection_id, model, token_budget ?? null, timeout_seconds ?? null, background]);
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
    retry: (count, error) => count < 3 && (!(error instanceof ApiError)
      || error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500),
    retryDelay: 1000,
    refetchInterval: (query) => query.state.error || ["done", "failed"].includes(query.state.data?.status ?? "") ? false : 1000,
  });
}
