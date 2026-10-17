import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api";

// The run's successful poll is the clock for its visible lists and assessment drawer.
export function useRunResource<T extends { revision?: number | string }>(
  path: string, revision: number | string, tick: number,
  { enabled = true, scope = path, placeholder }: { enabled?: boolean; scope?: string; placeholder?: T } = {},
) {
  const saved = useRef({ scope, data: placeholder });
  if (saved.current.scope !== scope) saved.current = { scope, data: placeholder };
  const attempts = useRef({ path, tick: 0, errorAt: 0, failures: 0, due: 0 });
  if (attempts.current.path !== path) attempts.current = { path, tick: 0, errorAt: 0, failures: 0, due: 0 };
  const query = useQuery<T, ApiError>({
    queryKey: [path], queryFn: ({ signal }) => api<T>(path, "GET", undefined, undefined, signal),
    enabled, retry: false,
    refetchOnMount: "always", refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  if (query.data) saved.current.data = query.data;
  const { data, dataUpdatedAt, error, errorUpdatedAt, isFetching, refetch } = query;
  useEffect(() => {
    if (!enabled || !tick || isFetching) return;
    const state = attempts.current;
    if (error) {
      if (error.status !== 0 && error.status !== 429 && error.status < 500) return;
      if (state.errorAt !== errorUpdatedAt) {
        state.errorAt = errorUpdatedAt;
        state.failures++;
        state.due = Math.max(errorUpdatedAt + Math.min(5000 * 2 ** (state.failures - 1), 30000), error.retryAt);
      }
      if (Date.now() < state.due) return;
    } else {
      state.failures = 0;
      if (!data || data.revision === revision || dataUpdatedAt >= tick) return;
    }
    if (state.tick >= tick) return;
    state.tick = tick;
    void refetch({ cancelRefetch: false });
  }, [enabled, path, revision, tick, data, dataUpdatedAt, error, errorUpdatedAt, isFetching, refetch]);
  return { ...query, data: query.data ?? saved.current.data };
}
