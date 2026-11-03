import { useEffect, useRef, useState } from "react";
import { hashKey, QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type Schema } from "../api";

type Workspace = Schema["CodeWorkspace"];
type Navigation = Partial<Schema["CodeNavigation"]> & Pick<Schema["CodeNavigation"], "view">;
type Metadata = { data: Workspace; graphs: Record<string, string> };
const AGE = 30_000;
const BYTES = 5 * 1024 * 1024;

export function findingCount(state: Schema["CodeFileState"], filters: Schema["CodeNavigation"]["findings"] = []) {
  return (["open", "resolved", "dismissed"] as const).reduce((count, status) =>
    count + (!filters.length || filters.includes("any") || filters.includes(status) ? state[`${status}_count`] : 0), 0);
}

function identity(data: Workspace) {
  return { base_sha: data.base_sha, head_sha: data.head_sha, merge_base_sha: data.merge_base_sha };
}
function authority(data: Workspace) {
  return [data.base_sha, data.head_sha, data.merge_base_sha, data.policy_digest, data.graph_enabled, data.graph_paused];
}
function selection(meta: Metadata | null, nav: Navigation) {
  const group = meta?.data.groups.find((item) => item.id === nav.group);
  const findings = nav.findings?.length ? nav.findings : nav.with_findings ? ["open"] as const : [];
  const reviewed = nav.reviewed?.length ? nav.reviewed : nav.unreviewed ? [false] : [];
  const files = meta?.data.files.filter((file, index) => (!group || group.file_indexes.includes(index)) && file.path.toLowerCase().includes((nav.query ?? "").toLowerCase())
    && (!reviewed.length || reviewed.includes(meta.data.file_states[index].reviewed))
    && (!findings.length || findings.some((value) => value === "none"
      ? meta.data.file_states[index].complete && findingCount(meta.data.file_states[index]) === 0
      : findingCount(meta.data.file_states[index], [value]) > 0 || !meta.data.file_states[index].complete))) ?? [];
  const page = Math.min(nav.page ?? 1, Math.max(1, Math.ceil(files.length / 10)));
  let path = nav.path ?? files[(page - 1) * 10]?.path ?? null;
  let revision = nav.revision ?? "head";
  const file = meta?.data.files.find((item) => item.path === path || item.previous_path === path);
  if (file) {
    if (nav.view === "diff") path = file.path;
    else if (revision === "base" || file.change === "removed") { path = file.previous_path ?? file.path; revision = "base"; }
  }
  return { files, page, group, path, revision };
}
function requestFor(meta: Metadata | null, nav: Navigation): Navigation {
  const { path, revision } = selection(meta, nav);
  return { path, revision, view: nav.view, offset: nav.offset ?? 0, start_byte: nav.start_byte ?? 0, end_byte: nav.end_byte ?? 1048576,
    ...(meta ? { expected: identity(meta.data) } : { page: nav.page, query: nav.query, group: nav.group, reviewed: nav.reviewed, findings: nav.findings, unreviewed: nav.unreviewed, with_findings: nav.with_findings }) };
}
function keyFor(meta: Metadata | null, request: Navigation) {
  return [meta ? "content" : "bootstrap", meta ? authority(meta.data) : null,
    request.view === "diff" ? null : meta?.graphs[request.revision ?? "head"] ?? null, request] as const;
}

/** A query cache exists only while this Code workspace is mounted; mutations still use the app's client. */
export function usePRCode(prId: string, navigation: Navigation) {
  const applicationCache = useQueryClient();
  const [cache] = useState(() => new QueryClient());
  const [meta, setMeta] = useState<Metadata | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const current = useRef(meta);
  const epoch = useRef(0);
  const active = useRef("");
  const intent = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const prefetched = useRef<ReturnType<typeof keyFor> | null>(null);
  const selected = selection(meta, navigation);
  const request = requestFor(meta, navigation);
  const key = keyFor(meta, request);
  active.current = JSON.stringify(key);

  function discard() {
    epoch.current++;
    void cache.cancelQueries();
    cache.clear();
    current.current = null;
    setMeta(null);
  }
  async function fetchView(nav: Navigation, signal: AbortSignal) {
    const admitted = epoch.current;
    try {
      const result = await api<Workspace>(`/pulls/${prId}/code/workspace`, "POST", nav, undefined, signal);
      if (signal.aborted || admitted !== epoch.current) throw new DOMException("Navigation cancelled", "AbortError");
      const prior = current.current;
      const changed = prior && JSON.stringify(authority(prior.data)) !== JSON.stringify(authority(result));
      if (changed) {
        epoch.current++;
        // Keep this verified response; fence and cancel every older request and cached source.
        void cache.cancelQueries({ predicate: (query) => query.queryHash !== hashKey(keyFor(prior, nav)) });
        cache.removeQueries({ predicate: (query) => query.getObserversCount() === 0 });
      }
      const graphs = changed ? {} : { ...prior?.graphs };
      if (result.graph) graphs[result.revision] = `${result.graph.snapshot_id}:${result.graph.extractor_version}:${result.graph.policy_digest}`;
      const next = { data: { ...result, diff: null, graph: null }, graphs };
      current.current = next;
      const normalized = requestFor(next, { ...nav, path: result.path, revision: result.revision });
      cache.setQueryData(keyFor(next, normalized), result, { updatedAt: Date.now() });
      setMeta(next);
      return result;
    } catch (error) {
      if (signal.aborted || admitted !== epoch.current || (error instanceof DOMException && error.name === "AbortError")) throw error;
      const problem = error instanceof ApiError ? error : new ApiError(503, "Code could not be loaded. Retry this view.");
      discard();
      setFailure(problem);
      throw problem;
    }
  }
  const query = useQuery<Workspace, ApiError>({ queryKey: key, queryFn: ({ signal }) => fetchView(request, signal),
    enabled: !failure && (!meta || !!request.path), staleTime: AGE, gcTime: AGE, retry: false,
    refetchInterval: (query) => Math.max(1000, Math.min(25_000, AGE - (Date.now() - query.state.dataUpdatedAt) - 5000)), refetchOnWindowFocus: true,
  }, cache);

  useEffect(() => {
    cache.mount();
    const offline = () => { discard(); setFailure(new ApiError(503, "You are offline. Connect to the internet, then retry Code.")); };
    window.addEventListener("offline", offline);
    if (!navigator.onLine) offline();
    const unsubscribe = cache.getQueryCache().subscribe((event) => {
      if (event.type !== "updated" || event.action.type !== "success") return;
      const entries = cache.getQueryCache().findAll().filter((item) => item.state.data)
        .sort((a, b) => Number(JSON.stringify(b.queryKey) === active.current) - Number(JSON.stringify(a.queryKey) === active.current) || b.state.dataUpdatedAt - a.state.dataUpdatedAt);
      let bytes = 0, count = 0;
      for (const entry of entries) {
        const size = new TextEncoder().encode(JSON.stringify(entry.state.data)).length;
        const visible = JSON.stringify(entry.queryKey) === active.current;
        const nav = entry.queryKey[3] as Navigation;
        const obsolete = current.current && (JSON.stringify(entry.queryKey[1]) !== JSON.stringify(authority(current.current.data))
          || (nav.view !== "diff" && entry.queryKey[2] !== (current.current.graphs[nav.revision ?? "head"] ?? null)));
        if (!visible && (obsolete || entry.queryKey[0] === "bootstrap" || size > BYTES || count >= 10 || bytes + size > BYTES || Date.now() - entry.state.dataUpdatedAt >= AGE)) {
          cache.removeQueries({ queryKey: entry.queryKey, exact: true });
        } else { bytes += size; count++; }
      }
    });
    return () => {
      epoch.current++;
      clearTimeout(intent.current);
      window.removeEventListener("offline", offline);
      unsubscribe();
      void cache.cancelQueries();
      cache.clear();
      cache.unmount();
    };
  }, [cache]);

  useEffect(() => applicationCache.getQueryCache().subscribe((event) => {
    if (event.type === "updated" && event.action.type === "invalidate" && event.query.queryKey[0] === `/pulls/${prId}`) {
      void cache.cancelQueries().then(() => cache.invalidateQueries());
    }
  }), [applicationCache, cache, prId]);

  async function loadFindings(path: string) {
    const nav = requestFor(current.current, { view: "diff", path, revision: "head" });
    return cache.fetchQuery({ queryKey: keyFor(current.current, nav), queryFn: ({ signal }) => fetchView(nav, signal), staleTime: 0, gcTime: AGE, retry: false });
  }

  // Oversized results can be displayed, but must not survive navigation as reusable cache entries.
  useEffect(() => {
    for (const entry of cache.getQueryCache().findAll()) if (entry.getObserversCount() === 0 && entry.state.data
      && new TextEncoder().encode(JSON.stringify(entry.state.data)).length > BYTES) cache.removeQueries({ queryKey: entry.queryKey, exact: true });
  }, [cache, query.dataUpdatedAt, selected.path, navigation.view]);

  function prefetch(values?: Partial<Navigation>) {
    clearTimeout(intent.current);
    if (prefetched.current) void cache.cancelQueries({ queryKey: prefetched.current, exact: true, predicate: (query) => query.getObserversCount() === 0 });
    prefetched.current = null;
    if (!values || !current.current || failure) return;
    intent.current = setTimeout(() => {
      if (document.visibilityState !== "visible" || !navigator.onLine || cache.isFetching()) return;
      const nav = requestFor(current.current, { ...navigation, ...values });
      if (!nav.path) return;
      prefetched.current = keyFor(current.current, nav);
      void cache.prefetchQuery({ queryKey: prefetched.current, queryFn: ({ signal }) => fetchView(nav, signal), staleTime: AGE, gcTime: AGE, retry: false });
    }, 200);
  }
  function refresh() {
    clearTimeout(intent.current);
    if (failure) { setFailure(null); return; }
    void query.refetch();
  }
  const expired = query.isStale && Date.now() - query.dataUpdatedAt >= AGE;
  return { ...selected, inventory: meta?.data, data: !failure && !expired ? query.data : undefined,
    loadFindings, error: failure ?? query.error, loading: query.isPending || expired, fetching: query.isFetching, refresh, prefetch };
}
