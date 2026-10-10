import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchGroupedPages, type GroupedData } from "../lib/grouped-pagination";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, type Schema } from "../api";
import { groups, INBOX_MAX_PAGES, normalizeFilters, withoutPages, type Property } from "../lib/inbox-filters";
import { runFeedback } from "../lib/run-feedback";
import { Empty, ErrorNotice, Loading, Status } from "./common";
import { InboxFilters } from "./inbox-filters";
import { PullRequestListHeading, PullRequestRow } from "./pull-request-row";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

const titles = { active: "Open", merged: "Merged", closed: "Closed" };

export function RepositoryPulls({ repositoryId }: { repositoryId: number }) {
  const [raw, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const params = normalizeFilters(raw);
  params.delete("repo");
  if (["created_at", "repository"].includes(params.get("ordering")?.replace(/^-/, "") ?? "")) params.delete("ordering");
  const filters = withoutPages(params).toString();
  const canonical = params.toString();
  const pulls = useQuery<GroupedData<Schema["InboxOut"]>, ApiError>({
    queryKey: ["repository-pulls", repositoryId, canonical],
    queryFn: ({ signal }) => fetchGroupedPages<Schema["InboxOut"]>(`/repositories/${repositoryId}/pulls`, new URLSearchParams(filters),
      Object.fromEntries(groups.map((group) => [group, Number(params.get(`${group}_pages`) ?? 1)])), signal),
    refetchInterval: groups.some((group) => params.has(`${group}_pages`)) ? false : 5000,
    retry: false,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === repositoryId ? previous : undefined,
  });
  useEffect(() => {
    const bounded = new URLSearchParams(canonical);
    if (raw.has("configure")) bounded.set("configure", raw.get("configure")!);
    if (pulls.data && !pulls.isPlaceholderData && !pulls.isFetching) for (const group of groups) {
      const total = pulls.data.groups.find((item) => item.group === group)?.total ?? 0;
      const maximum = Math.max(1, Math.ceil(total / pulls.data.page_size));
      if (Number(bounded.get(`${group}_pages`) ?? 1) > maximum) {
        if (maximum === 1) bounded.delete(`${group}_pages`); else bounded.set(`${group}_pages`, String(maximum));
      }
    }
    if (bounded.toString() !== raw.toString()) setParams(bounded, { replace: true });
  }, [canonical, raw, setParams, pulls.data, pulls.isPlaceholderData, pulls.isFetching]);
  const search = useCallback((value: string) => {
    const next = new URLSearchParams(filters);
    if (value.trim()) next.set("q", value); else next.delete("q");
    navigate({ search: normalizeFilters(next).toString() }, { replace: true });
  }, [filters, navigate]);
  function change(key: Property | "ordering", values: string[]) {
    const next = withoutPages(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(normalizeFilters(next));
  }
  const denied = pulls.error && [401, 403].includes(pulls.error.status);
  const rows = denied ? undefined : pulls.data;
  const sections = rows?.groups.filter((group) => group.total > 0) ?? [];

  return <section aria-labelledby="repository-pulls-heading">
    <h2 id="repository-pulls-heading" className="mb-4 text-xl font-semibold tracking-tight">Pull requests</h2>
    <InboxFilters repository params={params} data={rows} onChange={change} onSearch={search} />
    <p role="status" className="my-4 text-xs text-muted-foreground">{rows ? `${rows.total} pull ${rows.total === 1 ? "request" : "requests"}` : pulls.error ? "Pull requests unavailable" : "Loading pull requests…"}</p>
    {pulls.error && <div className="mb-4 space-y-2"><ErrorNotice error={pulls.error} />{!denied && <Button variant="outline" size="sm" onClick={() => pulls.refetch()}>Retry pull requests</Button>}</div>}
    {pulls.isPending && <Loading />}
    {rows && !rows.total && !pulls.isPlaceholderData && <Empty title={rows.registered_total ? "No matching pull requests" : "No pull requests here yet"}>
      {rows.registered_total ? <>Try another search or <button className="underline underline-offset-4" onClick={() => setParams({})}>reset your filters</button>.</> : "Use Sync to fetch pull requests from GitHub. Reviews begin once this repository is enabled."}
    </Empty>}
    <div className="space-y-6" aria-busy={pulls.isPlaceholderData}>
      {sections.map(({ group, items, page, total, has_more }) => {
        const busy = pulls.isFetching && Number(params.get(`${group}_pages`) ?? 1) > page;
        const error = rows?.errors[group];
        return <section key={group} aria-label={titles[group]}>
          <h3 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">{titles[group]}<span className="text-xs font-normal tabular-nums text-muted-foreground">{total}</span></h3>
          <div className="overflow-hidden rounded-lg border bg-card">
            <PullRequestListHeading dateLabel="Updated" />
            {items.map((pr) => {
              const feedback = pr.latest_run ? runFeedback(pr.latest_run) : null;
              return <PullRequestRow key={pr.id} id={pr.id} title={pr.title} findings={pr.open_findings} timestamp={pr.updated_at}
                state={{ repository: `${location.pathname}${location.search}` }}
                feedback={feedback?.needsAttention ? feedback.summary : undefined}
                metadata={<><span>#{pr.number} ·</span><span className="break-all">@{pr.author}</span><span className="max-w-full truncate font-mono" title={pr.head_ref}>{pr.head_ref}</span>{pr.draft && <span>Draft</span>}{pr.fork && <span>Fork · manual review</span>}</>}
                status={<><div className="flex flex-wrap gap-1"><Status value={pr.review_status} />{group === "active" && pr.state !== "open" && <Badge variant="secondary" className="capitalize">{pr.state}</Badge>}</div>{pr.has_running_review && pr.review_status !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}</>} />;
            })}
            {error && <div className="space-y-2 px-5 py-3"><ErrorNotice error={error} /><Button variant="outline" size="sm" onClick={() => pulls.refetch()}>Retry {titles[group].toLowerCase()}</Button></div>}
            {(has_more || busy) && page < INBOX_MAX_PAGES && !error && <div className="flex items-center justify-between gap-3 px-5 py-3">
              <Button variant="ghost" size="sm" disabled={busy || pulls.isPlaceholderData} onClick={() => {
                const next = new URLSearchParams(params);
                next.set(`${group}_pages`, String(page + 1));
                setParams(next);
              }}>{busy ? "Loading…" : "Load more"}<span className="sr-only"> {titles[group].toLowerCase()}</span></Button>
              <span className="text-xs tabular-nums text-muted-foreground">{items.length} of {total}</span>
            </div>}
            {has_more && page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining pull requests.</p>}
          </div>
        </section>;
      })}
    </div>
  </section>;
}
