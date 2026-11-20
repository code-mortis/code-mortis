import { useCallback, useEffect, useRef } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type Schema } from "./api";
import { Empty, ErrorNotice, Loading, PageTitle } from "./components/common";
import { PullRequestListHeading, PullRequestRow, PullRequestStatus } from "./components/pull-request-row";
import { InboxFilters } from "./components/inbox-filters";
import { useRefreshQueuedReviews } from "./hooks/use-refresh-queued-reviews";
import { fetchGroupedPages, type GroupedData } from "./lib/grouped-pagination";
import { Button } from "./components/ui/button";
import { Pagination } from "./components/pagination";
import { groups, normalizeFilters, withoutPages, type Property } from "./lib/inbox-filters";

type InboxData = GroupedData<Schema["InboxOut"]>;

async function fetchInbox(params: URLSearchParams, signal: AbortSignal): Promise<InboxData> {
  const request = withoutPages(params);
  request.set("group", "all");
  return fetchGroupedPages<Schema["InboxOut"]>("/pulls", request,
    { all: Number(params.get("all_pages") ?? 1) }, signal);
}

export function Inbox({ admin }: { admin: boolean }) {
  const [raw, setParams] = useSearchParams();
  const navigate = useNavigate();
  const params = normalizeFilters(raw, "open");
  // Preserve the depth of shared links from the former grouped inbox.
  const loadedPages = Math.max(...[...groups, "all"].map((group) => Number(params.get(`${group}_pages`) ?? 1)));
  groups.forEach((group) => params.delete(`${group}_pages`));
  if (loadedPages > 1) params.set("all_pages", String(loadedPages));
  const canonical = params.toString();
  const filters = withoutPages(params).toString();
  const location = useLocation();
  const legacy = raw.has("repo") && raw.has("configure");
  const query = useQuery<InboxData, ApiError>({
    queryKey: ["inbox", filters, canonical],
    queryFn: ({ signal }) => fetchInbox(new URLSearchParams(canonical), signal),
    enabled: !legacy,
    staleTime: 0,
    refetchInterval: 5000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  useRefreshQueuedReviews(!legacy && query.isSuccess && query.isFetchedAfterMount && !query.isPlaceholderData && !query.isFetching
    ? query.data.groups : undefined);
  useEffect(() => {
    if (!legacy && raw.toString() !== canonical) setParams(canonical, { replace: true });
  }, [raw, canonical, legacy, setParams]);
  useEffect(() => {
    if (!query.data || query.isPlaceholderData || query.isFetching) return;
    const bounded = new URLSearchParams(canonical);
    const maximum = Math.max(1, Math.ceil(query.data.total / query.data.page_size));
    if (Number(bounded.get("all_pages") ?? 1) > maximum) {
      if (maximum === 1) bounded.delete("all_pages");
      else bounded.set("all_pages", String(maximum));
    }
    if (bounded.toString() !== canonical) setParams(bounded, { replace: true });
  }, [query.data, query.isPlaceholderData, query.isFetching, canonical, setParams]);
  const search = useCallback((value: string) => {
    const next = new URLSearchParams(filters);
    if (value.trim()) next.set("q", value); else next.delete("q");
    // Unlike setParams, navigate stays stable when only the loaded pages change.
    navigate({ search: normalizeFilters(next, "open").toString() }, { replace: true });
  }, [filters, navigate]);
  function change(key: Property | "ordering", values: string[]) {
    const next = withoutPages(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(normalizeFilters(next, "open"));
  }
  const saved = useRef<InboxData>(undefined);
  if (query.data) saved.current = query.data;
  const denied = query.error && [401, 403].includes(query.error.status);
  if (denied) saved.current = undefined;
  const data = denied ? undefined : query.data ?? saved.current;
  const list = data?.groups[0];
  if (legacy) return <Navigate to={`/repositories?${raw}`} replace />;
  const emptyTitle = data?.registered_total ? "No matching pull requests" : "Your inbox is empty";
  return <>
    <PageTitle title="Inbox" headingProps={{ tabIndex: -1, ...{ "data-pull-list-focus": true } }}
      titleMeta={<span role="status" aria-live="polite" className="text-sm tabular-nums text-muted-foreground">{data ? <>{data.total}<span className="sr-only"> pull {data.total === 1 ? "request" : "requests"} · {data.repository_count} {data.repository_count === 1 ? "repository" : "repositories"}</span></> : query.error ? "Pull requests unavailable" : "Loading pull requests…"}{query.isPlaceholderData && <span className="sr-only"> · Updating pull requests…</span>}</span>}>
      Pull requests across your repositories.
    </PageTitle>
    <div className="mb-6"><InboxFilters params={params} data={data} onChange={change} onSearch={search} /></div>
    {query.error && !data && <div className="mb-4 space-y-2"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry inbox</Button></div>}
    {query.isPending && <Loading />}
    {data && data.total === 0 && !query.isPlaceholderData && <Empty title={emptyTitle}>
      {!data.registered_total && <>Registered pull requests will appear here. <Link to="/repositories" className="underline">Manage repositories</Link> to connect or sync a repository.</>}
      {!!data.registered_total && <>Try another search or <button className="underline" onClick={() => setParams({ state: "all" })}>view all pull requests</button>.</>}
    </Empty>}
    {list && list.total > 0 && <section aria-label="Pull requests" aria-busy={query.isPlaceholderData} className="overflow-hidden rounded-lg border bg-card">
      <PullRequestListHeading />
      {list.items.map((pr) => <PullRequestRow key={pr.id} id={pr.id} number={pr.number} repositoryId={pr.repository_id} admin={admin} ignored={pr.ignored} title={pr.title} findings={pr.open_findings} timestamp={pr.created_at}
        state={{ inbox: `${location.pathname}${location.search}` }}
        metadata={<><span>#{pr.number} ·</span><span className="break-all">{pr.repository_name}</span><span className="break-all">@{pr.author}</span>{pr.publication === "failed" && <span className="text-destructive">Publication failed</span>}{pr.publication === "partially_published" && <span className="text-amber-800 dark:text-amber-300">Partially published</span>}{pr.draft && <span>Draft</span>}</>}
        status={<PullRequestStatus reviewStatus={pr.review_status} state={pr.state} hasRunningReview={pr.has_running_review} />} />)}
      <Pagination page={list.page} pageSize={data.page_size} total={list.total} loading={query.isFetching && (query.isPlaceholderData || !!query.error)}
        error={query.error} onRetry={query.refetch} label="pull requests" onPageChange={(page) => {
          const next = new URLSearchParams(params);
          if (page === 1) next.delete("all_pages"); else next.set("all_pages", String(page));
          setParams(normalizeFilters(next, "open"));
        }} />
    </section>}
  </>;
}
