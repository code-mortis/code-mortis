import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchGroupedPages, type GroupedData } from "../lib/grouped-pagination";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, type Schema } from "../api";
import { getInboxReturn, groups, normalizeFilters, withoutPages, type Property } from "../lib/inbox-filters";
import { Empty, ErrorNotice, Loading } from "./common";
import { InboxFilters } from "./inbox-filters";
import { PullRequestListHeading, PullRequestRow, PullRequestStatus } from "./pull-request-row";
import { Button } from "./ui/button";
import { Pagination } from "./pagination";

const titles = { active: "Open", merged: "Merged", closed: "Closed", all: "Pull requests" };

export function RepositoryPulls({ repositoryId, admin }: { repositoryId: number; admin: boolean }) {
  const [raw, setParams] = useSearchParams();
  const { search: routeSearch, state } = useLocation();
  const inboxReturn = getInboxReturn(state) ?? undefined;
  const navigate = useNavigate();
  const params = normalizeFilters(raw, "open");
  params.delete("repo");
  params.delete("all_pages");
  if (["created_at", "repository"].includes(params.get("ordering")?.replace(/^-/, "") ?? "")) params.delete("ordering");
  const filters = withoutPages(params).toString();
  const canonical = params.toString();
  const configure = raw.get("configure");
  const withConfiguration = useCallback((next: URLSearchParams) => {
    if (configure !== null) next.set("configure", configure);
    return next;
  }, [configure]);
  const pulls = useQuery<GroupedData<Schema["InboxOut"]>, ApiError>({
    queryKey: ["repository-pulls", repositoryId, canonical],
    queryFn: ({ signal }) => fetchGroupedPages<Schema["InboxOut"]>(`/repositories/${repositoryId}/pulls`, new URLSearchParams(filters),
      Object.fromEntries(groups.map((group) => [group, Number(params.get(`${group}_pages`) ?? 1)])), signal),
    staleTime: 0,
    refetchInterval: 5000,
    retry: false,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === repositoryId ? previous : undefined,
  });
  useEffect(() => {
    const bounded = withConfiguration(new URLSearchParams(canonical));
    if (pulls.data && !pulls.isPlaceholderData && !pulls.isFetching) for (const group of groups) {
      const total = pulls.data.groups.find((item) => item.group === group)?.total ?? 0;
      const maximum = Math.max(1, Math.ceil(total / pulls.data.page_size));
      if (Number(bounded.get(`${group}_pages`) ?? 1) > maximum) {
        if (maximum === 1) bounded.delete(`${group}_pages`); else bounded.set(`${group}_pages`, String(maximum));
      }
    }
    if (bounded.toString() !== raw.toString()) setParams(bounded, { replace: true, state });
  }, [canonical, raw, setParams, state, withConfiguration, pulls.data, pulls.isPlaceholderData, pulls.isFetching]);
  const search = useCallback((value: string) => {
    const next = new URLSearchParams(filters);
    if (value.trim()) next.set("q", value); else next.delete("q");
    navigate({ search: withConfiguration(normalizeFilters(next, "open")).toString() }, { replace: true, state });
  }, [filters, navigate, state, withConfiguration]);
  function change(key: Property | "ordering", values: string[]) {
    const next = withoutPages(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(withConfiguration(normalizeFilters(next, "open")), { state });
  }
  const denied = pulls.error && [401, 403].includes(pulls.error.status);
  const rows = denied ? undefined : pulls.data;
  const sections = rows?.groups.filter((group) => group.total > 0) ?? [];
  const filtered = params.get("state") !== "all" || ["q", "review", "author", "findings", "visibility"].some((key) => params.has(key));

  return <section aria-labelledby="repository-pulls-heading">
    <p role="status" className="mb-4 text-xs tabular-nums text-muted-foreground">{rows ? <>
      {filtered ? `${rows.total} of ${rows.registered_total}` : rows.total} pull {rows.total === 1 ? "request" : "requests"}
    </> : pulls.error ? "Pull requests unavailable" : "Loading pull requests…"}</p>
    <div className="mb-4"><InboxFilters repository params={params} data={rows} onChange={change} onSearch={search} /></div>
    {pulls.error && <div className="mb-4 space-y-2"><ErrorNotice error={pulls.error} />{!denied && <Button variant="outline" size="sm" onClick={() => pulls.refetch()}>Retry pull requests</Button>}</div>}
    {pulls.isPending && <Loading />}
    {rows && !rows.total && !pulls.isPlaceholderData && <Empty title={rows.registered_total ? "No matching pull requests" : "No pull requests here yet"}>
      {rows.registered_total ? <>Try another search or <button className="underline underline-offset-4" onClick={() => setParams(withConfiguration(new URLSearchParams()), { state })}>reset your filters</button>.</> : "Use Sync to fetch pull requests from GitHub. Reviews begin once this repository is enabled."}
    </Empty>}
    <div className="space-y-6" aria-busy={pulls.isPlaceholderData}>
      {sections.map(({ group, items, page, total }) => {
        const error = rows?.errors[group];
        return <section key={group} aria-label={titles[group]}>
          <h2 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">{titles[group]}<span className="text-xs font-normal tabular-nums text-muted-foreground">{total}</span></h2>
          <div className="overflow-hidden rounded-lg border bg-card">
            <PullRequestListHeading dateLabel="Updated" />
            {items.map((pr) => <PullRequestRow key={pr.id} id={pr.id} number={pr.number} repositoryId={pr.repository_id} admin={admin} ignored={pr.ignored} title={pr.title} findings={pr.open_findings} timestamp={pr.updated_at}
                state={{ inbox: inboxReturn, repository: `/repositories/${repositoryId}${routeSearch}` }}
                metadata={<><span>#{pr.number} ·</span><span className="break-all">@{pr.author}</span><span className="max-w-full truncate font-mono" title={pr.head_ref}>{pr.head_ref}</span>{pr.draft && <span>Draft</span>}{pr.fork && <span>Fork · manual review</span>}</>}
                status={<><PullRequestStatus reviewStatus={pr.review_status} state={pr.state} />{pr.has_running_review && pr.review_status !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}</>} />)}
            <Pagination page={page} pageSize={rows!.page_size} total={total} loading={pulls.isFetching && (pulls.isPlaceholderData || !!error)}
              error={error ? new Error(error) : null} onRetry={pulls.refetch} label={titles[group].toLowerCase()} onPageChange={(page) => {
                const next = new URLSearchParams(params);
                if (page === 1) next.delete(`${group}_pages`); else next.set(`${group}_pages`, String(page));
                setParams(withConfiguration(next), { state });
              }} />
          </div>
        </section>;
      })}
    </div>
  </section>;
}
