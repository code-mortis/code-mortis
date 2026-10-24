import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api";

export function usePagedList<T extends { id?: string; repository?: { id: number } | string; finding_id?: unknown }>(path: string, live = true, revision?: string) {
  type Page = { items: T[]; total: number; page: number; page_size: number; result_counts?: Record<string, number>;
    registered_total?: number; outcome_counts?: Record<string, number>; severity_counts?: Record<string, number>; kind_counts?: Record<string, number>;
    state_counts?: Record<string, number>; grade_counts?: Record<string, number>; status_counts?: Record<string, number>; category_counts?: Record<string, number> };
  const [selection, setSelection] = useState({ path, page: 1 });
  if (selection.path !== path) setSelection({ path, page: 1 });
  const page = selection.path === path ? selection.page : 1;
  const saved = useRef<{ path: string; data?: Page }>({ path });
  if (saved.current.path !== path) saved.current = { path };
  const query = useQuery<Page, ApiError>({
    queryKey: ["paged-list", path, page],
    queryFn: ({ signal }) => api<Page>(`${path}${path.includes("?") ? "&" : "?"}page=${page}`, "GET", undefined, undefined, signal),
    staleTime: 0,
    refetchInterval: live ? 5000 : false,
    refetchOnWindowFocus: live,
    retry: false,
  });
  const lastPage = query.data ? Math.max(1, Math.ceil(query.data.total / query.data.page_size)) : 1;
  if (query.data && page <= lastPage) saved.current.data = query.data;
  useEffect(() => {
    if (query.data && page > lastPage) setSelection({ path, page: lastPage });
  }, [query.data, page, lastPage, path]);
  const denied = query.error instanceof ApiError && [401, 403, 404].includes(query.error.status);
  if (denied) saved.current.data = undefined;
  const data = denied ? undefined : query.data ?? saved.current.data;
  const lastRevision = useRef(revision);
  useEffect(() => {
    if (lastRevision.current === revision) return;
    lastRevision.current = revision;
    void query.refetch();
  }, [revision, query.refetch]);
  const seen = new Set<unknown>();
  const items = (data?.items ?? []).filter((item) => {
    const id = item.id ?? (typeof item.repository === "object" ? item.repository.id : undefined) ?? item.finding_id;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  return { query: { ...query, data, isPending: query.isPending && !data }, items, denied, total: data?.total ?? 0,
    resultCounts: data?.result_counts ?? {}, pagination: {
      page: data?.page ?? page, pageSize: data?.page_size ?? 10, total: data?.total ?? 0,
      loading: query.isFetching && (data?.page !== page || !!query.error), error: query.error, onRetry: query.refetch,
      onPageChange: (next: number) => { if (next === page) void query.refetch(); else setSelection({ path, page: next }); },
    } };
}

