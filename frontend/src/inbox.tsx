import { useCallback, useEffect } from "react";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, LoaderCircle } from "lucide-react";
import { api, ApiError, type Schema } from "./api";
import { Empty, ErrorNotice, Loading, Status, date } from "./components/common";
import { InboxFilters } from "./components/inbox-filters";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { groups, INBOX_MAX_PAGES, normalizeFilters, withoutPages, type Group, type Property } from "./lib/inbox-filters";

const titles: Record<Group, string> = { active: "Open", merged: "Merged", closed: "Closed" };
const createdFormatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
type InboxData = Schema["InboxOut"] & { errors: Partial<Record<Group, string>> };

async function fetchInbox(params: URLSearchParams, signal: AbortSignal): Promise<InboxData> {
  const filters = withoutPages(params).toString();
  const first = await api<Schema["InboxOut"]>(`/pulls?${filters}`, "GET", undefined, undefined, signal);
  const errors: InboxData["errors"] = {};
  const pages = await Promise.all(first.groups.map(async (group) => {
    const wanted = Math.min(Number(params.get(`${group.group}_pages`) ?? 1), Math.max(1, Math.ceil(group.total / first.page_size)), INBOX_MAX_PAGES);
    const combined = { ...group, items: [...group.items] };
    for (let page = 2; page <= wanted; page++) {
      signal.throwIfAborted();
      try {
        const response = await api<Schema["InboxOut"]>(`/pulls?${filters}&group=${group.group}&page=${page}`, "GET", undefined, undefined, signal);
        const next = response.groups[0];
        if (!next) break;
        combined.items.push(...next.items);
        combined.page = next.page;
        combined.total = next.total;
        combined.has_more = next.has_more;
        if (next.page < page) break;
      } catch (error) {
        signal.throwIfAborted();
        if (error instanceof ApiError && [401, 403].includes(error.status)) throw error;
        errors[group.group] = error instanceof Error ? error.message : "Could not load more pull requests.";
        break;
      }
    }
    return combined;
  }));
  const seen = new Set<string>();
  // Keep server totals and cursors: de-duplication only handles rows moving between requests.
  for (const page of pages) page.items = page.items.filter((item) => {
    if (seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
  return { ...first, groups: pages, errors };
}

export function Inbox() {
  const [raw, setParams] = useSearchParams();
  const navigate = useNavigate();
  const params = normalizeFilters(raw);
  const canonical = params.toString();
  const filters = withoutPages(params).toString();
  const location = useLocation();
  const legacy = raw.has("repo") && raw.has("configure");
  const query = useQuery<InboxData, ApiError>({
    queryKey: ["inbox", filters, canonical],
    queryFn: ({ signal }) => fetchInbox(new URLSearchParams(canonical), signal),
    enabled: !legacy,
    refetchInterval: groups.some((group) => params.has(`${group}_pages`)) ? false : 5000,
    retry: false,
    placeholderData: (previous) => previous,
  });
  useEffect(() => {
    if (!legacy && raw.toString() !== canonical) setParams(canonical, { replace: true });
  }, [raw, canonical, legacy, setParams]);
  useEffect(() => {
    if (!query.data || query.isPlaceholderData || query.isFetching) return;
    const bounded = new URLSearchParams(canonical);
    for (const group of groups) {
      const total = query.data.groups.find((g) => g.group === group)?.total ?? 0;
      const maximum = Math.max(1, Math.ceil(total / query.data.page_size));
      const requested = Number(bounded.get(`${group}_pages`) ?? 1);
      if (requested > maximum) {
        if (maximum === 1) bounded.delete(`${group}_pages`);
        else bounded.set(`${group}_pages`, String(maximum));
      }
    }
    if (bounded.toString() !== canonical) setParams(bounded, { replace: true });
  }, [query.data, query.isPlaceholderData, query.isFetching, canonical, setParams]);
  const search = useCallback((value: string) => {
    const next = new URLSearchParams(filters);
    if (value.trim()) next.set("q", value); else next.delete("q");
    // Unlike setParams, navigate stays stable when only the loaded pages change.
    navigate({ search: normalizeFilters(next).toString() }, { replace: true });
  }, [filters, navigate]);
  function change(key: Property | "ordering", values: string[]) {
    const next = withoutPages(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(normalizeFilters(next));
  }
  if (legacy) return <Navigate to={`/repositories?${raw}`} replace />;
  const data = query.error && [401, 403].includes(query.error.status) ? undefined : query.data;
  const visibleGroups = data?.groups.filter((group) => group.total > 0) ?? [];
  const defaultOpen = !["q", "repo", "state", "review", "author", "findings"].some((key) => params.has(key));
  let emptyTitle = "Your inbox is empty";
  if (data?.registered_total) emptyTitle = defaultOpen ? "No open pull requests" : "No matching pull requests";
  return <>
    <header className="mb-5">
      <h1 className="text-3xl font-semibold tracking-tight">Inbox</h1>
      <p className="mt-1 text-sm text-muted-foreground">Pull requests across your repositories.</p>
    </header>
    <InboxFilters params={params} data={data} onChange={change} onSearch={search} />
    <p role="status" aria-live="polite" className="my-4 text-xs text-muted-foreground">{data ? `${data.total} pull ${data.total === 1 ? "request" : "requests"} · ${data.repository_count} ${data.repository_count === 1 ? "repository" : "repositories"}` : "Loading pull requests…"}{query.isPlaceholderData && " · Updating pull requests…"}</p>
    {query.error && <div className="mb-4 space-y-2"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry inbox</Button></div>}
    {query.isPending && <Loading />}
    {data && data.total === 0 && !query.isPlaceholderData && <Empty title={emptyTitle}>
      {!data.registered_total && <>Registered pull requests will appear here. <Link to="/repositories" className="underline">Manage repositories</Link> to connect or sync a repository.</>}
      {!!data.registered_total && defaultOpen && <>Use the PR state filter to browse merged and closed pull requests.</>}
      {!!data.registered_total && !defaultOpen && <>Try another search or <button className="underline" onClick={() => setParams({})}>reset your filters</button>.</>}
    </Empty>}
    {data && data.total > 0 && <div className="space-y-6" aria-busy={query.isPlaceholderData}>
      {visibleGroups.map((group) => {
        const busy = query.isFetching && Number(params.get(`${group.group}_pages`) ?? 1) > group.page;
        const error = data.errors[group.group];
        return <section key={group.group} aria-label={titles[group.group]}>
          {visibleGroups.length > 1 && <h2 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">
            {titles[group.group]}<span className="text-xs font-normal tabular-nums text-muted-foreground">{group.total}</span>
          </h2>}
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="inbox-row-grid hidden border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground md:grid">
              <span>Pull request</span><span>Review status</span><span>Open findings</span><span>Created</span><span />
            </div>
            {group.items.map((pr) => <Link key={pr.id} to={`/pulls/${pr.id}`} state={{ inbox: `${location.pathname}${location.search}` }} className="inbox-row-grid group relative grid gap-y-3 border-b last:border-b-0 px-4 py-4 text-sm transition-colors hover:bg-muted/50 focus-visible:-outline-offset-2 md:items-center md:px-5 md:py-3">
              <div className="min-w-0 pr-5 md:pr-0"><p className="break-words font-medium group-hover:text-primary">{pr.title}</p><p className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted-foreground"><span className="break-all">{pr.repository_name}</span><span>#{pr.number}</span><span className="break-all">@{pr.author}</span>{pr.publication === "failed" && <span className="text-destructive">Publication failed</span>}{pr.publication === "partially_published" && <span className="text-amber-800 dark:text-amber-300">Partially published</span>}{pr.draft && <span>Draft</span>}</p></div>
              <div className="min-w-0 space-y-1"><div className="flex flex-wrap gap-1"><Status value={pr.review_status} />{group.group === "active" && pr.state !== "open" && <Badge variant="secondary" className="capitalize">{pr.state}</Badge>}</div>{pr.has_running_review && pr.review_status !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}</div>
              <div><Badge variant="outline" className={pr.open_findings ? "border-destructive/20 bg-destructive/5 text-destructive" : "bg-muted/50 text-muted-foreground"}>{pr.open_findings} unresolved<span className="sr-only"> findings</span></Badge></div>
              {pr.created_at ? <time dateTime={pr.created_at} title={date(pr.created_at)} className="text-xs tabular-nums text-muted-foreground">{createdFormatter.format(new Date(pr.created_at))}</time> : <span className="text-xs text-muted-foreground" title="Creation date will be available after the next sync">Pending sync</span>}
              <ChevronRight aria-hidden="true" className="absolute right-4 top-5 size-4 text-muted-foreground md:static" />
            </Link>)}
            {error && <div className="space-y-2 px-5 py-3"><ErrorNotice error={error} /><Button variant="outline" size="sm" onClick={() => query.refetch()}>Retry {titles[group.group].toLowerCase()}</Button></div>}
            {(group.has_more || busy) && group.page < INBOX_MAX_PAGES && !error && <div className="px-5 py-3"><Button variant="ghost" size="sm" disabled={busy || query.isPlaceholderData} onClick={() => {
              const next = new URLSearchParams(params);
              next.set(`${group.group}_pages`, String(group.page + 1));
              setParams(normalizeFilters(next));
            }}>{busy && <LoaderCircle className="size-3 motion-safe:animate-spin" />}{busy ? "Loading…" : "Load more"}<span className="sr-only"> {titles[group.group].toLowerCase()}</span></Button></div>}
            {group.has_more && group.page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining pull requests.</p>}
          </div>
        </section>;
      })}
    </div>}
  </>;
}
