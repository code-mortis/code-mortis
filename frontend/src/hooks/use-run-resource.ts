import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { INBOX_MAX_PAGES } from "../lib/inbox-filters";

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
    enabled, retry: false, staleTime: 0,
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

export function useRunPages<T extends { revision?: number | string; page: number; page_size: number; total: number }>(
  path: string, revision: number | string, tick: number, pageKey?: string,
) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const [selection, setSelection] = useState({ path, page: 1 });
  if (selection.path !== path) setSelection({ path, page: 1 });
  const requested = pageKey ? Number(params.get(pageKey) ?? 1) : selection.path === path ? selection.page : 1;
  const page = Number.isInteger(requested) && requested >= 1 && requested <= INBOX_MAX_PAGES ? requested : 1;
  const query = useRunResource<T>(`${path}&page=${page}`, revision, tick, { scope: path });
  function changePage(nextPage: number, replace = false) {
    if (!pageKey) { setSelection({ path, page: nextPage }); return; }
    const next = new URLSearchParams(params);
    if (nextPage > 1) next.set(pageKey, String(nextPage)); else next.delete(pageKey);
    setParams(next, { replace, state: location.state });
  }
  useEffect(() => {
    if (query.data && !query.isFetching && !query.error && query.data.page !== requested) changePage(query.data.page, true);
  }, [query.data, query.isFetching, query.error, requested, path, params, setParams, pageKey, location.state]);
  return { ...query, pagination: {
    page: query.data?.page ?? page, pageSize: query.data?.page_size ?? 10, total: query.data?.total ?? 0,
    loading: query.isFetching && (query.data?.page !== page || !!query.error), error: query.error, onRetry: query.refetch,
    onPageChange: (next: number) => { if (next === page) void query.refetch(); else changePage(next); },
  } };
}
