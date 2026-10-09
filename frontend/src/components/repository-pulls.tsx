import { useCallback } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useApi, type Schema } from "../api";
import { groups, INBOX_MAX_PAGES, normalizeFilters, selected, withoutPages, type Property } from "../lib/inbox-filters";
import { runFeedback } from "../lib/run-feedback";
import { Empty, ErrorNotice, Loading, Status } from "./common";
import { InboxFilters } from "./inbox-filters";
import { PullRequestListHeading, PullRequestRow } from "./pull-request-row";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";

const titles = { active: "Open", merged: "Merged", closed: "Closed" };
const PAGE_SIZE = 10;

export function RepositoryPulls({ repositoryId }: { repositoryId: number }) {
  const pulls = useApi<Schema["PullRequestOut"][]>(`/repositories/${repositoryId}/pulls`, true);
  const [raw, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const params = normalizeFilters(raw);
  params.delete("repo");
  if (["created_at", "repository"].includes(params.get("ordering")?.replace(/^-/, "") ?? "")) params.delete("ordering");
  const filters = withoutPages(params).toString();
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
  const query = (params.get("q") ?? "").toLowerCase();
  const review = (pr: Schema["PullRequestOut"]) => {
    const run = pr.latest_run;
    if (!run) return "not_reviewed";
    return run.head_sha !== pr.head_sha || run.base_sha !== pr.base_sha ? "outdated" : run.status;
  };
  const running = (pr: Schema["PullRequestOut"]) => pr.latest_run?.status === "running";
  const matches = rows?.filter((pr) =>
    (!query || `${pr.title} ${pr.head_ref}`.toLowerCase().includes(query) ||
      (!!query.replace(/^@+/, "") && pr.author.toLowerCase().includes(query.replace(/^@+/, ""))) ||
      String(pr.number) === query.replace(/^#/, "")) &&
    (!params.has("review") || params.getAll("review").includes(review(pr)) || (params.getAll("review").includes("running") && running(pr))) &&
    (!params.has("author") || params.getAll("author").includes(pr.author.toLowerCase())) &&
    (!params.has("findings") || (params.get("findings") === "open" ? pr.open_findings > 0 : pr.open_findings === 0)),
  ) ?? [];
  const filtered = matches.filter((pr) => selected(params, "state").includes(pr.state) || running(pr));
  const ordering = params.get("ordering") ?? "-updated_at";
  const field = ordering.replace(/^-/, "");
  const priority = (pr: Schema["PullRequestOut"]) => running(pr) ? 0 : review(pr) === "queued" ? 1 : 2;
  const sortValue = (pr: Schema["PullRequestOut"]) => field === "title" ? pr.title : field === "author" ? pr.author : pr.updated_at;
  filtered.sort((a, b) => priority(a) - priority(b) || sortValue(a).localeCompare(sortValue(b), undefined, { sensitivity: "base" }) * (ordering.startsWith("-") ? -1 : 1) || b.number - a.number);
  const sections = groups.map((group) => ({ group, items: filtered.filter((pr) => (running(pr) || pr.state === "open" ? "active" : pr.state) === group) })).filter(({ items }) => items.length);
  const choices = rows ? {
    repositories: [],
    authors: [...new Set(rows.map((pr) => pr.author))].sort(),
    state_counts: Object.fromEntries(["open", "merged", "closed"].map((state) => [state, matches.filter((pr) => pr.state === state).length])),
  } : undefined;

  return <section aria-labelledby="repository-pulls-heading">
    <h2 id="repository-pulls-heading" className="mb-4 text-xl font-semibold tracking-tight">Pull requests</h2>
    <InboxFilters repository params={params} data={choices} onChange={change} onSearch={search} />
    <p role="status" className="my-4 text-xs text-muted-foreground">{rows ? `${filtered.length} pull ${filtered.length === 1 ? "request" : "requests"}` : pulls.error ? "Pull requests unavailable" : "Loading pull requests…"}</p>
    {pulls.error && <div className="mb-4 space-y-2"><ErrorNotice error={pulls.error} />{!denied && <Button variant="outline" size="sm" onClick={() => pulls.refetch()}>Retry pull requests</Button>}</div>}
    {pulls.isPending && <Loading />}
    {rows && !filtered.length && <Empty title={rows.length ? "No matching pull requests" : "No pull requests here yet"}>
      {rows.length ? <>Try another search or <button className="underline underline-offset-4" onClick={() => setParams({})}>reset your filters</button>.</> : "Use Sync to fetch pull requests from GitHub. Reviews begin once this repository is enabled."}
    </Empty>}
    <div className="space-y-6">
      {sections.map(({ group, items }) => {
        const page = Math.min(Number(params.get(`${group}_pages`) ?? 1), Math.ceil(items.length / PAGE_SIZE));
        const shown = items.slice(0, page * PAGE_SIZE);
        return <section key={group} aria-label={titles[group]}>
          <h3 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">{titles[group]}<span className="text-xs font-normal tabular-nums text-muted-foreground">{items.length}</span></h3>
          <div className="overflow-hidden rounded-lg border bg-card">
            <PullRequestListHeading dateLabel="Updated" />
            {shown.map((pr) => {
              const feedback = pr.latest_run ? runFeedback(pr.latest_run) : null;
              return <PullRequestRow key={pr.id} id={pr.id} title={pr.title} findings={pr.open_findings} timestamp={pr.updated_at}
                state={{ repository: `${location.pathname}${location.search}` }}
                feedback={feedback?.needsAttention ? feedback.summary : undefined}
                metadata={<><span>#{pr.number} ·</span><span className="break-all">@{pr.author}</span><span className="max-w-full truncate font-mono" title={pr.head_ref}>{pr.head_ref}</span>{pr.draft && <span>Draft</span>}{pr.fork && <span>Fork · manual review</span>}</>}
                status={<><div className="flex flex-wrap gap-1"><Status value={review(pr)} />{group === "active" && pr.state !== "open" && <Badge variant="secondary" className="capitalize">{pr.state}</Badge>}</div>{running(pr) && review(pr) !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}</>} />;
            })}
            {shown.length < items.length && page < INBOX_MAX_PAGES && <div className="flex items-center justify-between gap-3 px-5 py-3">
              <Button variant="ghost" size="sm" onClick={() => {
                const next = new URLSearchParams(params);
                next.set(`${group}_pages`, String(page + 1));
                setParams(next);
              }}>Load more<span className="sr-only"> {titles[group].toLowerCase()}</span></Button>
              <span className="text-xs tabular-nums text-muted-foreground">{shown.length} of {items.length}</span>
            </div>}
            {shown.length < items.length && page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining pull requests.</p>}
          </div>
        </section>;
      })}
    </div>
  </section>;
}
