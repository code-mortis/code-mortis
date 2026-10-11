import { useCallback, useEffect } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { ApiError, type Schema } from "./api";
import { Empty, ErrorNotice, Loading } from "./components/common";
import { PullRequestListHeading, PullRequestRow, PullRequestStatus } from "./components/pull-request-row";
import { InboxFilters } from "./components/inbox-filters";
import { fetchGroupedPages, type GroupedData } from "./lib/grouped-pagination";
import { Button } from "./components/ui/button";
import { groups, INBOX_MAX_PAGES, normalizeFilters, withoutPages, type Property } from "./lib/inbox-filters";

type InboxData = GroupedData<Schema["InboxOut"]>;

async function fetchInbox(params: URLSearchParams, signal: AbortSignal): Promise<InboxData> {
  const request = withoutPages(params);
  request.set("group", "all");
  return fetchGroupedPages<Schema["InboxOut"]>("/pulls", request,
    { all: Number(params.get("all_pages") ?? 1) }, signal);
}

export function Inbox() {
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
    refetchInterval: params.has("all_pages") ? false : 5000,
    retry: false,
    placeholderData: (previous) => previous,
  });
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
  if (legacy) return <Navigate to={`/repositories?${raw}`} replace />;
  const data = query.error && [401, 403].includes(query.error.status) ? undefined : query.data;
  const list = data?.groups[0];
  const busy = query.isFetching && Number(params.get("all_pages") ?? 1) > (list?.page ?? 1);
  const error = data?.errors.all;
  const emptyTitle = data?.registered_total ? "No matching pull requests" : "Your inbox is empty";
  return <>
    <header className="mb-6">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Inbox</h1>
        <span role="status" aria-live="polite" className="text-sm tabular-nums text-muted-foreground">{data ? <>{data.total}<span className="sr-only"> pull {data.total === 1 ? "request" : "requests"} · {data.repository_count} {data.repository_count === 1 ? "repository" : "repositories"}</span></> : query.error ? "Pull requests unavailable" : "Loading pull requests…"}{query.isPlaceholderData && <span className="sr-only"> · Updating pull requests…</span>}</span>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">Pull requests across your repositories.</p>
    </header>
    <div className="mb-6"><InboxFilters params={params} data={data} onChange={change} onSearch={search} /></div>
    {query.error && <div className="mb-4 space-y-2"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry inbox</Button></div>}
    {query.isPending && <Loading />}
    {data && data.total === 0 && !query.isPlaceholderData && <Empty title={emptyTitle}>
      {!data.registered_total && <>Registered pull requests will appear here. <Link to="/repositories" className="underline">Manage repositories</Link> to connect or sync a repository.</>}
      {!!data.registered_total && <>Try another search or <button className="underline" onClick={() => setParams({ state: "all" })}>view all pull requests</button>.</>}
    </Empty>}
    {list && list.total > 0 && <section aria-label="Pull requests" aria-busy={query.isPlaceholderData} className="overflow-hidden rounded-lg border bg-card">
      <PullRequestListHeading />
      {list.items.map((pr) => <PullRequestRow key={pr.id} id={pr.id} title={pr.title} findings={pr.open_findings} timestamp={pr.created_at}
        state={{ inbox: `${location.pathname}${location.search}` }}
        metadata={<><span>#{pr.number} ·</span><span className="break-all">{pr.repository_name}</span><span className="break-all">@{pr.author}</span>{pr.publication === "failed" && <span className="text-destructive">Publication failed</span>}{pr.publication === "partially_published" && <span className="text-amber-800 dark:text-amber-300">Partially published</span>}{pr.draft && <span>Draft</span>}</>}
        status={<><PullRequestStatus reviewStatus={pr.review_status} state={pr.state} />{pr.has_running_review && pr.review_status !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}</>} />)}
      {error && <div className="space-y-2 px-5 py-3"><ErrorNotice error={error} /><Button variant="outline" size="sm" onClick={() => query.refetch()}>Retry pull requests</Button></div>}
      {(list.has_more || busy) && list.page < INBOX_MAX_PAGES && !error && <div className="flex items-center justify-between gap-3 px-5 py-3"><Button variant="ghost" size="sm" disabled={busy || query.isPlaceholderData} onClick={() => {
        const next = new URLSearchParams(params);
        next.set("all_pages", String(list.page + 1));
        setParams(normalizeFilters(next, "open"));
      }}>{busy && <LoaderCircle className="size-3 motion-safe:animate-spin" />}{busy ? "Loading…" : "Load more"}<span className="sr-only"> pull requests</span></Button><span className="text-xs tabular-nums text-muted-foreground">{list.items.length} of {list.total}</span></div>}
      {list.has_more && list.page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining pull requests.</p>}
    </section>}
  </>;
}
