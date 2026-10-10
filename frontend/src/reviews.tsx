import { useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { fetchGroupedPages, type GroupedData } from "./lib/grouped-pagination";
import { getInboxReturn, INBOX_MAX_PAGES } from "./lib/inbox-filters";
import {
  Link,
  Navigate,
  useNavigate,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ChevronDown,
  ChevronRight,
  Play,
  RefreshCw,
  ArrowUpRight,
  LockKeyhole,
  Globe,
  SlidersHorizontal,
} from "lucide-react";
import {
  ApiError,
  api,
  useAction,
  useApi,
  type Repository,
  type Run,
  type Schema,
} from "./api";
import { Button } from "./components/ui/button";
import { FilterBar } from "./components/filter-bar";
import { SearchSelect } from "./components/search-select";
import { label } from "./lib/inbox-filters";
import { RepositoryPulls } from "./components/repository-pulls";
import { getRepositoryReturn } from "./lib/navigation";
import { Textarea } from "./components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "./components/ui/sheet";
import {
  active,
  Code,
  CodeComparison,
  date,
  Empty,
  ErrorNotice,
  ExternalLink,
  Field,
  Loading,
  Markdown,
  PageTitle,
  Status,
  words,
} from "./components/common";
import { RepositorySettings } from "./settings";
import { fileFeedback, runFeedback } from "./lib/run-feedback";

function RunFeedback({ run, repositoryId, admin, canWrite, detail = false }: {
  run: Run; repositoryId: number; admin: boolean; canWrite: boolean; detail?: boolean;
}) {
  const routeState = useLocation().state;
  const inboxReturn = getInboxReturn(routeState);
  const repositoryReturn = getRepositoryReturn(routeState);
  const feedback = runFeedback(run);
  if (!feedback.needsAttention && (!detail || !active(run.status))) return null;
  return (
    <section aria-label="Review outcome" className={detail ? "mb-4 space-y-2 text-sm" : "mb-6 space-y-3 rounded-lg border bg-card p-5 text-sm"}>
      <p role="status" className="font-medium break-words">{feedback.summary}</p>
      <p className="text-muted-foreground break-words">
        {feedback.destination === "repository" && !admin ? "Ask your administrator to adjust the repository settings. " : ""}
        {feedback.nextStep}
      </p>
      <div className="flex flex-wrap gap-3">
        {!detail && <Link className="underline underline-offset-4" to={`/runs/${run.id}`} state={{ inbox: inboxReturn, repository: repositoryReturn }}>View run details</Link>}
        {feedback.destination === "repository" && admin && (
          <Link className="underline underline-offset-4" to={`/repositories/${repositoryId}?configure=limits`}>Adjust review settings</Link>
        )}
        {feedback.destination === "connections" && <Link className="underline underline-offset-4" to="/settings?tab=connections">Manage AI connections</Link>}
        {detail && feedback.needsAttention && (
          <Link className="underline underline-offset-4" to={`/pulls/${run.pr_id}`} state={{ inbox: inboxReturn, repository: repositoryReturn }}>
            {canWrite && ["failed", "partial", "skipped", "cancelled"].includes(run.status) ? "Open pull request to run a new review" : "View pull request"}
          </Link>
        )}
      </div>
    </section>
  );
}

export function Repositories({ admin }: { admin: boolean }) {
  const { repositoryId } = useParams();
  const location = useLocation();
  const repos = useApi<Repository[]>("/repositories", true);
  const [params, setParams] = useSearchParams();
  const requested = repositoryId ?? (params.get("repo") || null);
  const denied = repos.error && [401, 403].includes(repos.error.status);
  const available = denied ? [] : repos.data;
  const selected = requested ? available?.find((repo) => String(repo.id) === requested) : available?.[0];
  const edit = admin && params.has("configure");
  function setEdit(open: boolean) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (open) next.set("configure", "true");
      else next.delete("configure");
      return next;
    });
  }
  const action = useAction();
  const target = requested ?? selected?.id;
  if (target && (!repositoryId || params.has("repo"))) {
    const search = new URLSearchParams(params);
    search.delete("repo");
    return <Navigate to={{ pathname: `/repositories/${encodeURIComponent(target)}`, search: search.toString(), hash: location.hash }} replace />;
  }
  if (repos.isPending) return <Loading />;
  if (repos.error && (!repos.data || denied)) return <ErrorNotice error={repos.error} />;
  const syncPath = `/repositories/${selected?.id}/sync`;
  const syncHere = action.variables?.path === syncPath;
  const syncPending = syncHere && action.isPending;
  const Visibility = selected?.private ? LockKeyhole : Globe;
  return <>
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1 basis-80">
        <h1 className="break-words text-3xl font-semibold tracking-tight [overflow-wrap:anywhere]">{selected?.full_name ?? "Repositories"}</h1>
        {selected ? <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><Visibility aria-hidden="true" className="size-3.5" />{selected.private ? "Private repository" : "Public repository"}</span>
          <a href={`https://github.com/${selected.full_name}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-sm underline-offset-4 hover:text-foreground hover:underline">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
        </div> : <p className="mt-1 text-sm text-muted-foreground">Follow each pull request from first review to final resolution.</p>}
      </div>
      {selected && <div className="flex shrink-0 flex-wrap gap-2">
        <Button variant="outline" disabled={syncPending} onClick={() => action.mutate({ path: syncPath })}>
          <RefreshCw aria-hidden="true" className={`size-4 ${syncPending ? "motion-safe:animate-spin" : ""}`} />{syncPending ? "Queuing sync…" : "Sync"}
        </Button>
        {admin && <Button variant="outline" onClick={() => setEdit(true)}><SlidersHorizontal aria-hidden="true" className="size-4" />Configure</Button>}
      </div>}
    </header>
    <ErrorNotice error={repos.error || (syncHere && action.error)} />
    {syncHere && action.isSuccess && <p role="status" className="mb-4 text-sm text-muted-foreground">Sync queued. Pull requests will refresh when it finishes.</p>}
    {requested && !selected && <Empty title="Repository unavailable">
      This repository was not found or you no longer have access. <Link className="underline" to="/repositories">View available repositories</Link>.
    </Empty>}
    {!requested && !available?.length && <Empty title="Connect your first repository">
      {admin ? <>Install your GitHub App, then connect the installation in <Link className="underline" to="/settings?tab=github">settings</Link>.</> : "Ask your administrator to connect a repository you can access on GitHub."}
    </Empty>}
    {selected && <>
      <section aria-label="Review configuration" className="mb-7 flex flex-wrap items-center gap-x-4 gap-y-2 border-y py-4 text-sm">
        <Status value={selected.enabled ? "connected" : "paused"} label={selected.enabled ? "Reviews enabled" : "Reviews paused"} />
        <span>{selected.enabled ? (selected.settings.automatic ? "Automatic reviews" : "Manual reviews only") : "Reviews are paused for this repository."}</span>
        {selected.enabled ? <span className="text-xs text-muted-foreground">{selected.settings.max_files == null ? "No file limit" : `${selected.settings.max_files} files per review`} · {selected.settings.language} comments</span> : <span className="text-xs text-muted-foreground">{admin ? "Choose an AI connection in Configure to enable them." : "Your administrator can enable them."}</span>}
      </section>
      <RepositoryPulls key={selected.id} repositoryId={selected.id} />
      <Sheet open={edit} onOpenChange={setEdit}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader><SheetTitle>Repository configuration</SheetTitle><SheetDescription>{selected.full_name}</SheetDescription></SheetHeader>
          <div className="p-6"><RepositorySettings key={selected.id} repository={selected} showLimits={params.get("configure") === "limits"} onSaved={() => setEdit(false)} /></div>
        </SheetContent>
      </Sheet>
    </>}
    {admin && <div className="mt-6 text-xs text-muted-foreground"><Link className="rounded-sm underline underline-offset-4 hover:text-foreground" to="/settings?tab=github">{available?.length ? "Manage repositories" : "Connect repository"}</Link></div>}
  </>;
}

function RunList({ prId }: { prId: string }) {
  const routeState = useLocation().state;
  const inboxReturn = getInboxReturn(routeState);
  const repositoryReturn = getRepositoryReturn(routeState);
  const query = useInfiniteQuery({
    queryKey: ["pull-runs", prId],
    queryFn: ({ pageParam, signal }) => api<Schema["RunPage"]>(`/pulls/${prId}/runs?page=${pageParam}`, "GET", undefined, undefined, signal),
    initialPageParam: 1,
    getNextPageParam: (last) => last.has_more ? last.page + 1 : undefined,
    refetchInterval: (query) => (query.state.data?.pages.length ?? 0) <= 1 ? 5000 : false,
    retry: false,
  });
  if (query.isPending) return <Loading />;
  if (query.error instanceof ApiError && [401, 403].includes(query.error.status)) return <ErrorNotice error={query.error} />;
  if (!query.data || (query.error && query.data.pages[0].total === 0)) return <div className="space-y-2"><ErrorNotice error={query.error} /><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry run history</Button></div>;
  const seen = new Set<string>();
  const runs = query.data.pages.flatMap((page) => page.items).filter((run) => {
    if (seen.has(run.id)) return false;
    seen.add(run.id);
    return true;
  });
  const total = query.data.pages.at(-1)!.total;
  const paginated = query.hasNextPage || query.data.pages.length > 1;
  if (!runs.length)
    return (
      <Empty title="No reviews yet">
        Start a review to record findings and coverage for this commit.
      </Empty>
    );
  return (
    <div className="divide-y overflow-hidden rounded-lg border bg-card">
      <div aria-hidden="true" className="review-row-grid hidden items-center bg-muted/35 px-5 py-2.5 text-xs font-medium text-muted-foreground md:grid">
        <span>Review run</span><span>Review status</span><span>GitHub publication</span><span />
      </div>
      {runs.map((run) => {
        const feedback = runFeedback(run);
        return (
          <Link
            className="review-row-grid grid items-center gap-y-3 px-5 py-4 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            key={run.id}
            to={`/runs/${run.id}`}
            state={{ inbox: inboxReturn, repository: repositoryReturn }}
          >
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{date(run.created_at)}</div>
              <div className="mt-1 break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">
                <span className="capitalize">{run.trigger}</span> ·{" "}
                <code>{run.head_sha.slice(0, 7)}</code> ·{" "}
                {String(
                  (run.config.connection as Record<string, unknown> | undefined)
                    ?.model ?? "",
                )}
              </div>
              {feedback.needsAttention && (
                <p className="mt-2 text-sm text-muted-foreground break-words">{feedback.summary}</p>
              )}
            </div>
            <div><Status value={run.status} /></div>
            <span className="text-xs text-muted-foreground">
              <span className="md:sr-only">GitHub: </span>{feedback.publicationLabel}
            </span>
            <ChevronRight aria-hidden="true" className="hidden size-4 text-muted-foreground md:block" />
          </Link>
        );
      })}
      {(paginated || query.error) && <div className="space-y-2 px-5 py-3">
        <ErrorNotice error={query.error} />
        <div className="flex items-center justify-between gap-3">
          {query.hasNextPage ? <Button variant="ghost" size="sm" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
            {query.isFetchingNextPage ? "Loading…" : query.isFetchNextPageError ? "Retry" : "Load more"}<span className="sr-only"> run history</span>
          </Button> : query.error ? <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry run history</Button> : null}
          {paginated && <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">{runs.length} of {total}</span>}
        </div>
      </div>}
    </div>
  );
}

const findingStatuses = ["open", "resolved", "dismissed"] as const;

export function PullDetail({ admin }: { admin: boolean }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const inboxReturn = getInboxReturn(location.state);
  const repositoryReturn = getRepositoryReturn(location.state);
  const query = useApi<Schema["PRDetail"]>(`/pulls/${id}`, true);
  const action = useAction<Run>();
  const connections = useApi<Schema["ConnectionOut"][]>("/connections");
  const [connection, setConnection] = useState("");
  const [statuses, setStatuses] = useState<string[]>(["open"]);
  const [severities, setSeverities] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [searchResetKey, setSearchResetKey] = useState(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const findingTrigger = useRef<HTMLButtonElement>(null);
  const [finding, setFinding] = useState<string | null>(null);
  const filters = JSON.stringify([id, statuses, severities, search]);
  const [pagination, setPagination] = useState({ filters, pages: {} as Partial<Record<typeof findingStatuses[number], number>> });
  if (pagination.filters !== filters) setPagination({ filters, pages: {} });
  const findingFilters = new URLSearchParams();
  if (search.trim()) findingFilters.set("q", search.trim());
  statuses.forEach((value) => findingFilters.append("status", value));
  severities.forEach((value) => findingFilters.append("severity", value));
  const findingsQuery = useQuery<GroupedData<Schema["FindingsOut"]>, ApiError>({
    queryKey: ["pull-findings", id, filters, pagination.pages],
    queryFn: ({ signal }) => fetchGroupedPages<Schema["FindingsOut"]>(`/pulls/${id}/findings`, findingFilters, pagination.pages, signal),
    enabled: !!query.data && !(query.error && [401, 403].includes(query.error.status)),
    refetchInterval: Object.values(pagination.pages).some((page) => page! > 1) ? false : 5000,
    retry: false,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === id ? previous : undefined,
  });
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  if (findingsQuery.error && [401, 403].includes(findingsQuery.error.status)) return <ErrorNotice error={findingsQuery.error} />;
  const { pull_request: pr, repository, run_count, has_active_run: running, latest_observed_run_id: observed, can_write } = query.data;
  const findings = findingsQuery.data;
  const total = findings?.registered_total ?? 0;
  const latest = pr.latest_run;
  const term = search.trim().toLowerCase();
  const count = (state: string) => findings?.status_counts[state] ?? 0;
  const sections = findings?.groups.filter((group) => group.total > 0) ?? [];
  const noOpenFindings = total > 0 && statuses.length === 1 && statuses[0] === "open" && !count("open") && !severities.length && !term;
  const reviewUnavailable = !can_write ? "You have read-only access to this pull request."
    : running ? "A review is already queued or running."
    : pr.state !== "open" ? "Reviews can only run on open pull requests."
    : !repository.enabled ? "Reviews are paused. Enable them in repository configuration."
    : pr.fork ? "Fork reviews require a manual start." : null;
  function showAllFindings() {
    setStatuses([]);
    setSeverities([]);
    setSearch("");
    setSearchResetKey((key) => key + 1);
    searchInput.current?.focus();
  }
  return <>
    <header className="mb-6">
      <h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl [overflow-wrap:anywhere]">{pr.title}</h1>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
        <Status value={pr.state} />
        <span>#{pr.number} · @{pr.author}</span>
        {pr.draft && <span>Draft</span>}
        <code className="min-w-0 max-w-full truncate" title={`${pr.head_ref} · ${pr.head_sha}`}>{pr.head_ref} · {pr.head_sha.slice(0, 7)}</code>
        <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-sm underline-offset-4 hover:text-foreground hover:underline">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
      </div>
    </header>
    <ErrorNotice error={action.error || query.error} />
    <section aria-label="Latest review" className="mb-6 border-y py-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-medium">{latest ? "Latest review" : "No reviews yet"}</span>
            {latest && <Status value={latest.status} />}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {latest ? <>
              <span>{date(latest.created_at)}</span>
              <span>{latest.head_sha === pr.head_sha ? "Current commit" : "Earlier commit"} · <code>{latest.head_sha.slice(0, 7)}</code></span>
              <Link className="inline-flex items-center gap-1 rounded-sm text-foreground underline-offset-4 hover:underline" to={`/runs/${latest.id}`} state={{ inbox: inboxReturn, repository: repositoryReturn }}>Inspect review<ChevronRight aria-hidden="true" className="size-3" /></Link>
            </> : <span>A review records findings and coverage for this commit.</span>}
          </div>
        </div>
        {can_write && <div className="flex w-full flex-wrap gap-2 sm:w-auto">
          <SearchSelect aria-label="Review connection" disabled={action.isPending || running || pr.state !== "open" || !repository.enabled} value={connection} onChange={setConnection} className="min-w-0 flex-1 basis-48 sm:w-56 sm:flex-none" placeholder="Repository default"
            options={[{ value: "", label: "Repository default" }, ...(connections.data ?? []).map((item) => ({ value: item.id, label: item.name }))]} />
          <Button disabled={action.isPending || running || pr.state !== "open" || !repository.enabled} onClick={async () => {
            const run = await action.mutateAsync({ path: `/pulls/${id}/runs`, body: connection ? { connection_id: connection } : {} }).catch(() => null);
            if (run) navigate(`/runs/${run.id}`, { state: { inbox: inboxReturn, repository: repositoryReturn } });
          }}><Play aria-hidden="true" className="size-4" />{running ? "Review in progress" : action.isPending ? "Starting review…" : "Run review"}</Button>
        </div>}
      </div>
      {reviewUnavailable && <p className="mt-3 text-xs text-muted-foreground">{reviewUnavailable}</p>}
    </section>
    {latest && <RunFeedback run={latest} repositoryId={repository.id} admin={admin} canWrite={can_write} />}
    <Tabs defaultValue="findings">
      <TabsList className="mb-4">
        <TabsTrigger value="findings">Findings <span className="ml-1 text-xs tabular-nums text-muted-foreground">{findings ? total : ""}</span></TabsTrigger>
        <TabsTrigger value="history">Run history <span className="ml-1 text-xs tabular-nums text-muted-foreground">{run_count}</span></TabsTrigger>
      </TabsList>
      <TabsContent value="findings" className="min-w-0">
        <div className="mb-4">
          <FilterBar query={search} searchResetKey={searchResetKey} searchLabel="Search findings" searchPlaceholder="Search findings or files…" searchInputRef={searchInput} onSearch={setSearch}
            filters={[
              { key: "status", label: "Status", values: statuses, choices: findingStatuses.map((value) => ({ value, name: label(value), count: count(value) })) },
              { key: "severity", label: "Severity", values: severities, choices: ["critical", "high", "medium", "low"].map((value) => ({ value, name: label(value) })) },
            ]}
            onChange={(key, values) => key === "status" ? setStatuses(values) : setSeverities(values)}>
            <span role="status" className="shrink-0 text-xs tabular-nums text-muted-foreground sm:pt-2.5">{findings ? `${findings.total} of ${total} findings` : findingsQuery.error ? "Findings unavailable" : "Loading findings…"}</span>
          </FilterBar>
        </div>
        {findingsQuery.error && <div className="mb-4 space-y-2"><ErrorNotice error={findingsQuery.error} /><Button variant="outline" size="sm" onClick={() => findingsQuery.refetch()}>Retry findings</Button></div>}
        {findingsQuery.isPending && <Loading />}
        {findings && (findings.total > 0 ? <section aria-label="Findings" className="space-y-6" aria-busy={findingsQuery.isPlaceholderData}>
          {sections.map(({ group: status, items, page, total: groupTotal, has_more }) => {
            const busy = findingsQuery.isFetching && (pagination.pages[status] ?? 1) > page;
            const error = findings.errors[status];
            return <section key={status} aria-label={`${label(status)} findings`}>
              <h2 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">{label(status)}<span className="text-xs font-normal tabular-nums text-muted-foreground">{groupTotal}</span></h2>
              <div className="divide-y overflow-hidden rounded-lg border bg-card">
                <div aria-hidden="true" className="review-row-grid hidden items-center bg-muted/35 px-5 py-2.5 text-xs font-medium text-muted-foreground md:grid">
                  <span>Finding</span><span>Severity</span><span>Status</span><span />
                </div>
                {items.map((item) => <button key={item.id} className="review-row-grid grid w-full items-center gap-y-3 px-5 py-4 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={(event) => { findingTrigger.current = event.currentTarget; setFinding(item.id); }}>
                  <div className="min-w-0">
                    <p className="line-clamp-2 break-words text-sm font-medium leading-relaxed [overflow-wrap:anywhere]">{item.content.replaceAll(/[*`#]/g, "")}</p>
                    <p className="mt-1.5 truncate font-mono text-xs text-muted-foreground" title={`${item.path}:${item.start_line}${item.end_line !== item.start_line ? `–${item.end_line}` : ""}`}>{item.path}:{item.start_line}{item.end_line !== item.start_line && `–${item.end_line}`}</p>
                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span className="capitalize">{words(item.category)}</span>
                      {observed && item.last_seen_run_id !== observed && <span>Not observed in latest review</span>}
                    </div>
                  </div>
                  <div><Status value={item.severity} /></div>
                  <div className="space-y-1.5">
                    <Status value={item.status} />
                    {item.desired_status && <p className="text-xs text-muted-foreground">{item.sync_error ? "Sync needs attention" : "Sync pending"}</p>}
                  </div>
                  <ChevronRight aria-hidden="true" className="hidden size-4 text-muted-foreground md:block" />
                </button>)}
                {error && <div className="space-y-2 px-5 py-3"><ErrorNotice error={error} /><Button variant="outline" size="sm" onClick={() => findingsQuery.refetch()}>Retry {status} findings</Button></div>}
                {(has_more || busy) && page < INBOX_MAX_PAGES && !error && <div className="flex items-center justify-between gap-3 px-5 py-3">
                  <Button variant="ghost" size="sm" disabled={busy || findingsQuery.isPlaceholderData} onClick={() => setPagination({ filters, pages: { ...pagination.pages, [status]: page + 1 } })}>{busy ? "Loading…" : "Load more"}<span className="sr-only"> {status} findings</span></Button>
                  <span className="text-xs tabular-nums text-muted-foreground">{items.length} of {groupTotal}</span>
                </div>}
                {has_more && page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining findings.</p>}
              </div>
            </section>;
          })}
        </section> : !findingsQuery.isPlaceholderData && <Empty title={noOpenFindings ? "No open findings" : total ? "No findings match these filters" : run_count ? "No findings recorded" : "No review findings yet"}>
          <p>{noOpenFindings ? "Earlier findings are resolved or dismissed. You can still inspect their evidence and history." : total ? "Try another search, status, or severity." : run_count ? "Check run history for completion and coverage. An incomplete review is not a clean result." : "Findings will appear here after a review. Each finding keeps its evidence and resolution history."}</p>
          {total > 0 && <Button variant="outline" className="mt-4" onClick={showAllFindings}>View all findings</Button>}
        </Empty>)}
        <p className="mt-5 text-xs leading-relaxed text-muted-foreground">Resolving or reopening a GitHub review thread updates its finding here. A finding missing from a later run stays open until someone resolves it.</p>
      </TabsContent>
      <TabsContent value="history" className="min-w-0"><RunList prId={pr.id} /></TabsContent>
    </Tabs>
    <Sheet open={!!finding} onOpenChange={(open) => { if (!open) setFinding(null); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={(event) => {
        event.preventDefault();
        (findingTrigger.current?.isConnected ? findingTrigger.current : searchInput.current)?.focus();
      }}>
        <SheetHeader><SheetTitle>Finding details</SheetTitle><SheetDescription>Review the evidence and its resolution history.</SheetDescription></SheetHeader>
        {finding && <FindingPanel key={finding} id={finding} canWrite={can_write} />}
      </SheetContent>
    </Sheet>
  </>;
}

function FindingPanel({ id, canWrite }: { id: string; canWrite: boolean }) {
  const query = useApi<Schema["FindingDetail"]>(`/findings/${id}`, true);
  const action = useAction();
  const [dismiss, setDismiss] = useState(false);
  const [reason, setReason] = useState("");
  if (query.isPending)
    return (
      <div className="px-6">
        <Loading />
      </div>
    );
  if (!query.data) return <ErrorNotice error={query.error} />;
  const { finding: f, activities, verifications = [] } = query.data;
  async function change(status: string) {
    await action
      .mutateAsync({
        path: `/findings/${id}`,
        method: "PATCH",
        body: { status, reason },
      })
      .catch(() => null);
    setDismiss(false);
  }
  async function retrySync() {
    await action.mutateAsync({ path: `/findings/${id}/sync`, method: "POST" }).catch(() => null);
  }
  return (
    <div className="space-y-6 px-6 pb-10">
      <div className="flex flex-wrap items-center gap-2">
        <Status value={f.severity} />
        <Status value={f.status} />
        {f.github_url && (
          <span className="ml-auto">
            <ExternalLink href={f.github_url}>GitHub thread</ExternalLink>
          </span>
        )}
      </div>
      <p className="break-all font-mono text-xs text-muted-foreground">
        {f.path}:{f.start_line}–{f.end_line}
      </p>
      <Markdown>{f.content}</Markdown>
      <CodeComparison original={f.existing_code} suggested={f.suggestion} path={f.path} />
      <ErrorNotice error={action.error || f.sync_error || query.error} />
      {f.desired_status && (
        <p role="status" className="text-sm text-muted-foreground">
          {f.sync_error
            ? "GitHub has not confirmed the change. Retry to sync again."
            : `Waiting for GitHub to confirm: ${f.desired_status}.`}
        </p>
      )}
      {canWrite && (
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={action.isPending || (!!f.desired_status && !f.sync_error)}
            onClick={() =>
              f.sync_error ? retrySync() : change(
                f.desired_status ?? (f.status === "open" ? "resolved" : "open"),
              )
            }
          >
            {findingActionLabel(f)}
          </Button>
          {f.desired_status === "resolved" && !!f.sync_intent?.verification_id && (
            <Button variant="outline" disabled={action.isPending} onClick={() => change("open")}>
              Keep finding open
            </Button>
          )}
          {f.status === "open" && !f.desired_status && (
            <Button variant="outline" onClick={() => setDismiss(!dismiss)}>
              Dismiss
            </Button>
          )}
        </div>
      )}
      {dismiss && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void change("dismissed");
          }}
        >
          <Field label="Why dismiss this finding?">
            {(id) => (
              <Textarea
                id={id}
                required
                maxLength={2000}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="For example, this is intentional and covered by…"
              />
            )}
          </Field>
          <Button disabled={!reason.trim() || action.isPending}>
            Confirm dismissal
          </Button>
        </form>
      )}
      <div className="border-t pt-6">
        <h3 className="mb-5 text-sm font-semibold">Activity</h3>
        {verifications.map((item) => (
          <div key={item.id} className="mb-5 space-y-3 rounded-lg border p-4">
            <div className="flex flex-wrap gap-2">
              <span className="text-sm font-medium">Fix verification</span>
              {item.verdict && <Status value={item.verdict} />}
              <Status value={item.status} />
            </div>
            <p className="break-all text-xs text-muted-foreground">
              Reviewed commit: {item.head_sha} · {date(item.created_at)}
            </p>
            {!item.error && <p className="text-sm">{item.result?.explanation ?? "Verification is waiting for the review worker."}</p>}
            {item.error && item.result && <p className="text-sm">{item.result.explanation}</p>}
            {item.result?.change_explanation && <p className="text-sm">{item.result.change_explanation}</p>}
            {item.result?.dismissal_recommended && (
              <p className="text-sm">Possible false positive. Review this finding and use Dismiss if appropriate.</p>
            )}
            {item.result?.evidence.map((evidence, index) => (
              <details key={index} className="text-sm">
                <summary className="cursor-pointer break-all">
                  {evidence.revision === "original" ? "Original" : "Current"} evidence: {evidence.path}:{evidence.start_line}–{evidence.end_line}
                </summary>
                <div className="mt-2"><Code path={evidence.path}>{evidence.quote}</Code></div>
              </details>
            ))}
            <p className="text-sm text-muted-foreground">
              Automatic resolution: {words(item.resolution)}
            </p>
            <ErrorNotice error={item.error} />
          </div>
        ))}
        <ol className="space-y-5">
          {activities.map((event) => (
            <li key={event.id} className="relative border-l-2 pl-4">
              <p className="text-sm">
                <span className="font-medium">{event.actor}</span> ·{" "}
                {event.action}{" "}
                <span className="text-muted-foreground">
                  via {event.source}
                </span>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {date(event.created_at)}
              </p>
              {event.reason && <p className="mt-2 text-sm">{event.reason}</p>}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function findingActionLabel(finding: Schema["FindingOut"]) {
  if (finding.sync_error) return "Retry sync";
  if (finding.status === "open") return "Resolve finding";
  return "Reopen finding";
}

function getCandidateLabel(candidate: Schema["CandidateOut"]) {
  if (candidate.finding_id && candidate.is_new) return "New validated finding";
  if (candidate.finding_id) return "Repeated observation";
  if (candidate.result?.verdict === "rejected") return "Not supported";
  if (candidate.status === "queued" || candidate.status === "running") return "Awaiting assessment";
  return "Withheld";
}

function verificationSummary(value: unknown): Record<string, number> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  const counts: Record<string, number> = {};
  for (const [key, count] of Object.entries(value)) {
    if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) return;
    counts[key] = count;
  }
  return counts;
}

type Coverage = {
  selected?: unknown[];
  completed?: unknown[];
  reused?: unknown[];
  failed?: unknown[];
  waived?: unknown[];
};
export function RunDetail({ user }: { user: Schema["UserOut"] }) {
  const { id } = useParams();
  const query = useApi<Schema["RunDetail"]>(`/runs/${id}`, true);
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  return <RunWorkspace key={id} detail={query.data} user={user} error={query.error} />;
}

function RunWorkspace({ detail, user, error }: { detail: Schema["RunDetail"]; user: Schema["UserOut"]; error: ApiError | null }) {
  const { run, pull_request: pr, repository, occurrences, candidates = [], assessment, can_write } = detail;
  const action = useAction();
  const location = useLocation();
  const returnState = { inbox: getInboxReturn(location.state), repository: getRepositoryReturn(location.state) };
  const [tab, setTab] = useState("findings");
  const [search, setSearch] = useState("");
  const [severities, setSeverities] = useState<string[]>([]);
  const [finding, setFinding] = useState<Schema["OccurrenceOut"] | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const findingTrigger = useRef<HTMLButtonElement>(null);
  const coverage = ((run.report?.manifest as { coverage?: Coverage } | undefined)?.coverage ?? {}) as Coverage;
  const seconds = run.started_at && run.finished_at
    ? Math.max(0, Math.round((+new Date(run.finished_at) - +new Date(run.started_at)) / 1000)) : null;
  const duration = seconds === null ? null : seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const connection = run.config.connection as Record<string, unknown> | undefined;
  const checkValue = run.check_conclusion || run.check_status;
  const verification = verificationSummary(run.progress?.verification);
  const feedback = runFeedback(run);
  const files = fileFeedback(run);
  const term = search.trim().toLowerCase();
  const filtered = occurrences.filter(({ snapshot }) => (!severities.length || severities.includes(String(snapshot.severity)))
    && (!term || [snapshot.path, snapshot.content, snapshot.category].some((value) => String(value ?? "").toLowerCase().includes(term))));
  const reviewed = [...(coverage.completed ?? []), ...(coverage.reused ?? [])].flatMap((item) =>
    item && typeof item === "object" && "path" in item && typeof item.path === "string" ? [item.path] : []);
  const engine = run.engine === "chatgpt" ? "Codex (ChatGPT)" : run.engine === "claude_code" ? "Claude Code" : "Open Code Review";
  return <>
    <PageTitle title={<span className="flex flex-wrap items-center gap-3">Review run <Status value={run.status} /></span>} actions={can_write && (active(run.status) || run.status === "blocked" || ["failed", "partially_published"].includes(run.publication)) && <>
      {run.status === "blocked" && (run.owner_id == null || run.owner_id === user.id) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/retry` })}>Resume review</Button>}
      {(active(run.status) || run.status === "blocked") && <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/cancel` })}>Cancel review</Button>}
      {["failed", "partially_published"].includes(run.publication) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/publish` })}>Retry GitHub publication</Button>}
    </>}>
      <Link className="mb-2 inline-block break-words text-foreground underline-offset-4 hover:underline" to={`/pulls/${pr.id}`} state={returnState}>#{pr.number} · {pr.title}</Link>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span>{date(run.created_at)} · {run.trigger} · <code>{run.head_sha.slice(0, 7)}</code>{duration && ` · ${duration}`}</span>
        <span>{run.head_sha === pr.head_sha ? "Current commit" : "Earlier commit"}</span>
      </div>
    </PageTitle>
    <ErrorNotice error={action.error || error} />
    <RunFeedback run={run} repositoryId={repository.id} admin={user.role === "admin"} canWrite={can_write} detail />
    <ErrorNotice error={run.error} />
    <div className="mb-6 border-y">
      <section aria-label="GitHub publication" className="space-y-3 py-3 text-xs">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="flex items-center gap-2"><span className="text-muted-foreground">GitHub comments</span><Status value={run.publication} label={feedback.publicationLabel} /></div>
          {run.check_enabled && (checkValue || run.check_url || run.check_pending || run.check_error) && <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">GitHub check</span>{checkValue && <Status value={checkValue} />}
            {run.check_url && <a className="inline-flex min-h-6 items-center gap-1 underline-offset-4 hover:underline" href={run.check_url} target="_blank" rel="noreferrer" aria-label="View check on GitHub">View check<ArrowUpRight aria-hidden="true" className="size-3" /></a>}
          </div>}
        </div>
        {run.publication !== "published" && <p className="leading-relaxed text-muted-foreground">{feedback.publicationDetail}</p>}
        <ErrorNotice error={run.publication_error} />
        {run.check_enabled && <>
          {run.check_pending && <p role="status" className="text-muted-foreground">{run.check_error ? "Check update pending. Review results are saved separately." : "Updating the check on GitHub…"}</p>}
          <ErrorNotice error={run.check_error} />
        </>}
      </section>
      {!!verification?.selected && <details className="group border-t">
        <summary className="flex min-h-11 cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-1 py-3 text-xs outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          <span className="font-medium">Fix verification</span>
          <span className="text-muted-foreground">{verification.resolved ?? 0} resolved</span>
          {!!verification.pending && <span className="text-muted-foreground">{verification.pending} pending sync</span>}
          {!!verification.failed && <span className="text-destructive">{verification.failed} failed</span>}
          <ChevronDown aria-hidden="true" className="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="space-y-2 pb-4 text-xs leading-relaxed text-muted-foreground">
          <p>{verification.completed ?? 0} of {verification.selected} findings verified. {verification.resolved ?? 0} automatically resolved, {verification.pending ?? 0} pending synchronization.</p>
          <p>{verification.still_present ?? 0} still present, {verification.uncertain ?? 0} uncertain, {verification.skipped ?? 0} skipped, {verification.failed ?? 0} failed.</p>
          <p>Verified fixes remain open until GitHub confirms resolution. Evidence is saved in each finding’s Activity.</p>
        </div>
      </details>}
    </div>
    {(active(run.status) || run.status === "blocked") && typeof run.progress?.selected === "number" && <p role="status" className="mb-4 text-sm text-muted-foreground">
      {String(run.progress.completed ?? 0)} of {String(run.progress.selected)} assigned files completed.
      {run.status === "blocked" && " Completed files are saved and reused when you resume."}
    </p>}
    {assessment && (assessment.pending > 0 || assessment.incomplete > 0) && tab !== "assessment" && <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <p className="text-muted-foreground">{assessment.incomplete > 0 ? `Assessment is incomplete for ${assessment.incomplete} candidate${assessment.incomplete === 1 ? "" : "s"}. This is not a clean review.` : `${assessment.pending} candidates awaiting assessment.`}</p>
      <Button variant="link" size="sm" className="h-auto px-0" onClick={() => setTab("assessment")}>Inspect assessment<ChevronRight aria-hidden="true" /></Button>
    </div>}
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList className="mb-4 grid w-full grid-cols-2 sm:flex sm:w-fit group-data-[orientation=horizontal]/tabs:h-auto">
        <TabsTrigger value="findings">Findings <span className="text-xs font-normal tabular-nums text-muted-foreground">{occurrences.length}</span></TabsTrigger>
        <TabsTrigger value="coverage">Coverage</TabsTrigger>
        <TabsTrigger value="assessment">Assessment {assessment && <span className="text-xs font-normal tabular-nums text-muted-foreground">{assessment.candidates}</span>}</TabsTrigger>
        <TabsTrigger value="details">Details</TabsTrigger>
      </TabsList>
      <TabsContent value="findings" className="min-w-0">
        <div className="mb-4"><FilterBar query={search} searchLabel="Search run findings" searchPlaceholder="Search findings or files…" searchInputRef={searchInput} onSearch={setSearch}
          filters={[{ key: "severity", label: "Severity", values: severities, choices: ["critical", "high", "medium", "low"].map((value) => ({ value, name: label(value), count: occurrences.filter((item) => item.snapshot.severity === value).length })) }]}
          onChange={(_, values) => setSeverities(values)}>
          <span role="status" className="text-xs tabular-nums text-muted-foreground sm:pt-2.5">{filtered.length} of {occurrences.length} findings</span>
        </FilterBar></div>
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">These are the findings saved at this commit. Open a finding to inspect its original evidence and current resolution separately.</p>
        {!assessment && !active(run.status) && <p className="mb-4 text-xs text-muted-foreground">This run predates candidate assessment. Its findings have not been assessed by this policy.</p>}
        {filtered.length ? <section aria-label="Findings at this run" className="divide-y overflow-hidden rounded-lg border bg-card">
          <div aria-hidden="true" className="review-row-grid hidden items-center bg-muted/35 px-5 py-2.5 text-xs font-medium text-muted-foreground md:grid"><span>Finding</span><span>Severity</span><span>Comment placement</span><span /></div>
          {filtered.map((item) => <button key={item.finding_id} className="review-row-grid grid w-full items-center gap-y-3 px-5 py-4 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onClick={(event) => { findingTrigger.current = event.currentTarget; setFinding(item); }}>
            <div className="min-w-0"><p className="line-clamp-2 break-words text-sm font-medium leading-relaxed [overflow-wrap:anywhere]">{String(item.snapshot.content ?? "Finding evidence").replaceAll(/[*`#]/g, "")}</p>
              <p className="mt-1.5 truncate font-mono text-xs text-muted-foreground" title={`${item.snapshot.path}:${item.snapshot.start_line}`}>{String(item.snapshot.path ?? "Path not recorded")}:{String(item.snapshot.start_line ?? "")}</p>
              {!!item.snapshot.category && <p className="mt-1 text-xs text-muted-foreground">{String(item.snapshot.category)}</p>}
            </div>
            <div><Status value={String(item.snapshot.severity ?? "unknown")} /></div>
            <span className="text-xs text-muted-foreground">{item.inline_line ? "Inline comment" : "Summary comment"}</span>
            <ChevronRight aria-hidden="true" className="hidden size-4 text-muted-foreground md:block" />
          </button>)}
        </section> : <Empty title={occurrences.length ? "No findings match these filters" : active(run.status) ? "Review in progress" : "No findings recorded in this run"}>
          <p>{occurrences.length ? "Try another search or severity." : active(run.status) ? "Saved findings appear here as the review progresses." : "Check coverage and assessment before treating this as a clean result."}</p>
          {occurrences.length ? <Button variant="outline" className="mt-4" onClick={() => { setSearch(""); setSeverities([]); searchInput.current?.focus(); }}>Clear filters</Button> : <Button variant="outline" className="mt-4" onClick={() => setTab("coverage")}>Inspect coverage</Button>}
        </Empty>}
      </TabsContent>
      <TabsContent value="coverage" className="min-w-0">
        <section aria-label="Coverage">
          <h2 className="text-sm font-semibold">Coverage</h2>
          <p className="mt-2 text-sm text-muted-foreground">Validated file results determine coverage. Reused results are from earlier work; waived and failed items are not completed reviews.</p>
          {run.report ? <>
            <dl className="my-4 flex flex-wrap gap-x-8 gap-y-4 border-y py-4">{(["selected", "completed", "reused", "failed", "waived"] as const).map((key) => <div key={key} className="flex items-baseline gap-2"><dt className="text-xs capitalize text-muted-foreground">{key}</dt><dd className="text-sm font-medium tabular-nums">{coverage[key]?.length ?? 0}</dd></div>)}</dl>
            {!!files.length && <details className="border-b py-4 text-sm" open={run.status === "skipped"}>
              <summary className="cursor-pointer font-medium">Files not reviewed ({files.length})</summary>
              <ul className="mt-3 divide-y">{files.map((file) => <li key={file.path} className="space-y-1 py-3"><code className="break-all text-xs">{file.path}</code><p className="break-words text-muted-foreground">{file.reason}</p></li>)}</ul>
            </details>}
            {!!reviewed.length && <details className="border-b py-4 text-sm"><summary className="cursor-pointer font-medium">Reviewed files ({reviewed.length})</summary><ul className="mt-3 space-y-2">{reviewed.map((path, index) => <li key={`${path}-${index}`}><code className="break-all text-xs">{path}</code></li>)}</ul></details>}
            <details className="py-4 text-sm"><summary className="cursor-pointer font-medium">Inspect coverage report</summary><div className="mt-3"><Code language="json">{JSON.stringify(run.report, null, 2)}</Code></div></details>
          </> : <Empty title={active(run.status) ? "Coverage is being recorded" : "No coverage report was produced"}>{active(run.status) ? "Validated file results will appear as the review progresses." : "This run has no saved coverage evidence. No findings does not mean every file was reviewed."}</Empty>}
        </section>
      </TabsContent>
      <TabsContent value="assessment" className="min-w-0">
        <section aria-label="Candidate assessment">
          <h2 className="mb-2 text-sm font-semibold">Candidate assessment</h2>
          <p className="mb-4 text-xs text-muted-foreground">Proposed findings are checked against evidence and earlier reviews before becoming new comments.</p>
          {assessment ? <>
            <p className="text-sm">{assessment.new_findings} new validated findings · {assessment.repeated_observations} repeated observations · {assessment.withheld_candidates} withheld candidates · {assessment.published_comments} comments published</p>
            {!!assessment.pending && <p role="status" className="mt-2 text-sm text-muted-foreground">{assessment.pending} candidates awaiting assessment.</p>}
            {!!assessment.incomplete && <p className="mt-2 text-sm text-muted-foreground">Assessment is incomplete for {assessment.incomplete} candidate{assessment.incomplete === 1 ? "" : "s"}. This is not a clean review.</p>}
            {candidates.length ? <div className="mt-4 divide-y overflow-hidden rounded-lg border bg-card">{candidates.map((candidate) => <details key={candidate.id} className="group text-sm">
              <summary className="cursor-pointer break-words px-5 py-4 marker:text-muted-foreground hover:bg-muted/50 [overflow-wrap:anywhere]"><span className="font-medium">{String(candidate.snapshot.path ?? "Path not recorded")}:{String(candidate.snapshot.start_line ?? "")} · {getCandidateLabel(candidate)}</span><p className="mt-1 line-clamp-2 break-words text-xs text-muted-foreground">{String(candidate.snapshot.content ?? "")}</p></summary>
              <div className="space-y-3 border-t px-5 py-4">
                <Markdown>{String(candidate.snapshot.content ?? "")}</Markdown>
                <ErrorNotice error={candidate.error} />
                {candidate.result && <>
                  <p>{candidate.result.explanation}</p>
                  {candidate.result.history_explanation && <p>{candidate.result.history_explanation}</p>}
                  {candidate.result.changed_conditions && <p>{candidate.result.changed_conditions}</p>}
                  {candidate.result.evidence.map((evidence, index) => <div key={`${evidence.revision}-${evidence.path}-${evidence.start_line}-${index}`}><p className="mb-2 break-all text-xs text-muted-foreground">{evidence.revision}: {evidence.path}:{evidence.start_line}–{evidence.end_line}</p><Code path={evidence.path}>{evidence.quote}</Code></div>)}
                </>}
                {candidate.finding_id && <p className="break-all text-xs text-muted-foreground">Existing finding: {candidate.finding_id}. Resolution state is preserved.</p>}
              </div>
            </details>)}</div> : <Empty title="No candidates to assess">{active(run.status) ? "Candidates appear after the initial review finishes." : "No candidates were produced for assessment."}</Empty>}
          </> : <Empty title={active(run.status) ? "Assessment pending" : "Assessment was not recorded"}>{active(run.status) ? "Candidates appear after the initial review finishes." : "This run predates candidate assessment. Its findings have not been assessed by this policy."}</Empty>}
        </section>
      </TabsContent>
      <TabsContent value="details" className="min-w-0">
        <h2 className="mb-4 text-sm font-semibold">Run details</h2>
        <dl className="divide-y border-y text-sm">{[
          ["Connection", String(connection?.name ?? "Not recorded")], ["Model", String(connection?.model ?? "Not recorded")], ["Engine", engine],
          ["Account owner", run.owner_id == null ? "Shared connection" : `GitHub #${run.owner_id}`], ["Started by", run.actor_id == null ? words(run.trigger) : `GitHub #${run.actor_id}`],
          ["Head commit", run.head_sha], ["Base commit", run.base_sha], ["Started", run.started_at ? date(run.started_at) : "Not started"], ["Finished", run.finished_at ? date(run.finished_at) : "Not finished"],
        ].map(([key, value]) => <div key={key} className="grid gap-1 py-3 sm:grid-cols-[160px_minmax(0,1fr)] sm:gap-4"><dt className="text-xs text-muted-foreground">{key}</dt><dd className={`break-words [overflow-wrap:anywhere] ${key.includes("commit") ? "font-mono text-xs" : ""}`}>{value}</dd></div>)}</dl>
        <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium">Configuration used</summary><p className="mt-3 text-xs text-muted-foreground">The saved configuration for this run, independent of today's repository settings.</p><div className="mt-3"><Code language="json">{JSON.stringify(run.config, null, 2)}</Code></div></details>
      </TabsContent>
    </Tabs>
    <Sheet open={!!finding} onOpenChange={(open) => { if (!open) setFinding(null); }}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={(event) => { event.preventDefault(); (findingTrigger.current?.isConnected ? findingTrigger.current : searchInput.current)?.focus(); }}>
        <SheetHeader><SheetTitle>Finding details</SheetTitle><SheetDescription>Inspect the evidence saved in this run and its current resolution separately.</SheetDescription></SheetHeader>
        {finding && <Tabs key={finding.finding_id} defaultValue="snapshot">
          <TabsList className="mx-6 mb-4"><TabsTrigger value="snapshot">At this run</TabsTrigger><TabsTrigger value="current">Current status</TabsTrigger></TabsList>
          <TabsContent value="snapshot" className="min-w-0 space-y-4 px-6 pb-8">
            <div className="flex items-center gap-2"><Status value={String(finding.snapshot.severity ?? "unknown")} /><span className="text-xs text-muted-foreground">Saved at {run.head_sha.slice(0, 7)}</span></div>
            <p className="break-all font-mono text-xs text-muted-foreground">{String(finding.snapshot.path ?? "")}:{String(finding.snapshot.start_line ?? "")}{finding.snapshot.end_line && finding.snapshot.end_line !== finding.snapshot.start_line ? `–${finding.snapshot.end_line}` : ""}</p>
            <Markdown>{String(finding.snapshot.content ?? "")}</Markdown>
            <CodeComparison original={String(finding.snapshot.existing_code ?? "")} suggested={String(finding.snapshot.suggestion ?? "")} path={String(finding.snapshot.path ?? "")} />
          </TabsContent>
          <TabsContent value="current" className="min-w-0"><FindingPanel id={finding.finding_id} canWrite={can_write} /></TabsContent>
        </Tabs>}
      </SheetContent>
    </Sheet>
  </>;
}
