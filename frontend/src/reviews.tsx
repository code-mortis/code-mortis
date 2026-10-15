import { useRef, useState, type MouseEventHandler, type ReactNode } from "react";
import { toast } from "sonner";
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
  ArrowLeft,
  ClipboardCheck,
  Copy,
  LockKeyhole,
  Globe,
  SlidersHorizontal,
  CircleCheck,
  CircleDot,
  CircleSlash,
  FileCode2,
  History,
  RotateCcw,
  UserRound,
  GitBranch,
  GitCommitHorizontal,
  LoaderCircle,
  Bug,
  Shield,
  Gauge,
  Wrench,
  Tag,
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
import { Badge } from "./components/ui/badge";
import { FilterBar } from "./components/filter-bar";
import { SearchSelect } from "./components/search-select";
import { label } from "./lib/inbox-filters";
import { RepositoryPulls } from "./components/repository-pulls";
import { getRepositoryReturn } from "./lib/navigation";
import { Textarea } from "./components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "./components/ui/tooltip";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetFooter,
  SheetClose,
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
  Status,
  words,
} from "./components/common";
import { RepositorySettings } from "./settings";
import { assessmentFeedback, runFeedback } from "./lib/run-feedback";
import { RunCoverage } from "./components/run-coverage";
import { useReviewFilters } from "./hooks/use-review-filters";
import { useUrlTab } from "./hooks/use-url-tab";
import { candidateOutcomes, findingSeverities, findingStatuses } from "./lib/review-filters";

function RunFeedback({ run, repositoryId, admin, canWrite, detail = false }: {
  run: Run; repositoryId: number; admin: boolean; canWrite: boolean; detail?: boolean;
}) {
  const routeState = useLocation().state;
  const inboxReturn = getInboxReturn(routeState);
  const repositoryReturn = getRepositoryReturn(routeState);
  const feedback = runFeedback(run);
  if (!feedback.needsAttention && (!detail || run.status !== "queued")) return null;
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
          <Link className="underline underline-offset-4" to={`/repositories/${repositoryId}/settings?tab=limits`}>Adjust review settings</Link>
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

export function Repositories({ admin, configure = false }: { admin: boolean; configure?: boolean }) {
  const { repositoryId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const repos = useApi<Repository[]>("/repositories", true);
  const [params] = useSearchParams();
  const requested = repositoryId ?? (params.get("repo") || null);
  const denied = repos.error && [401, 403].includes(repos.error.status);
  const available = denied ? [] : repos.data;
  const selected = requested ? available?.find((repo) => String(repo.id) === requested) : available?.[0];
  const action = useAction();
  const target = requested ?? selected?.id;
  if (target && (!repositoryId || params.has("repo") || params.has("configure"))) {
    const search = new URLSearchParams(params);
    search.delete("repo");
    const legacyConfigure = admin && params.has("configure");
    if (legacyConfigure && params.get("configure") === "limits") search.set("tab", "limits");
    search.delete("configure");
    return <Navigate to={{ pathname: `/repositories/${encodeURIComponent(target)}${configure || legacyConfigure ? "/settings" : ""}`, search: search.toString(), hash: legacyConfigure ? "" : location.hash }} state={location.state} replace />;
  }
  if (repos.isPending) return <Loading />;
  if (repos.error && (!repos.data || denied)) return <ErrorNotice error={repos.error} />;
  const repositoryPath = `/repositories/${requested ?? selected?.id}`;
  const returnSearch = new URLSearchParams(params);
  if (configure) returnSearch.delete("tab");
  const returnPath = repositoryPath + (returnSearch.size ? `?${returnSearch}` : "");
  if (configure) {
    if (!admin || !selected) return <Navigate to={returnPath} state={location.state} replace />;
    const finish = () => navigate(returnPath, { state: location.state });
    return <RepositorySettings key={selected.id} repository={selected} onSaved={finish} />;
  }
  const syncPath = `/repositories/${selected?.id}/sync`;
  const syncHere = action.variables?.path === syncPath;
  const syncPending = syncHere && action.isPending;
  const Visibility = selected?.private ? LockKeyhole : Globe;
  const repositoryNameStart = (selected?.full_name.lastIndexOf("/") ?? -1) + 1;
  return <>
    <header className="mb-6 min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 basis-80">
          <h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl [overflow-wrap:anywhere]">
            {selected ? <><span className="font-normal text-muted-foreground">{selected.full_name.slice(0, repositoryNameStart)}</span>{selected.full_name.slice(repositoryNameStart)}</> : "Repositories"}
          </h1>
          {selected ? <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5"><Visibility aria-hidden="true" className="size-3.5" />{selected.private ? "Private repository" : "Public repository"}</span>
            <a href={`https://github.com/${selected.full_name}`} target="_blank" rel="noreferrer" className="inline-flex min-h-6 items-center gap-1 rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
          </div> : <p className="mt-1 text-sm text-muted-foreground">Follow each pull request from first review to final resolution.</p>}
        </div>
        {selected && <div className="flex shrink-0 flex-wrap gap-2">
          <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={syncPending} onClick={() => action.mutate({ path: syncPath }, {
            onSuccess: () => toast.success("Sync queued", { description: "Pull requests will refresh when it finishes." }),
          })}>
            <RefreshCw aria-hidden="true" className={`size-4 ${syncPending ? "motion-safe:animate-spin" : ""}`} />{syncPending ? "Queuing sync…" : "Sync"}
          </Button>
          {admin && <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" onClick={() => navigate(`${repositoryPath}/settings${location.search}`, { state: location.state })}><SlidersHorizontal aria-hidden="true" className="size-4" />Configure</Button>}
        </div>}
      </div>
      {selected && <section aria-label="Review configuration" className="mt-6 border-t pt-4 text-xs">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <div className="flex items-center gap-2"><span className="text-muted-foreground">Reviews</span><Status value={selected.enabled ? "connected" : "paused"} label={selected.enabled ? "Enabled" : "Paused"} /></div>
          {selected.enabled ? <>
            <span>{selected.settings.automatic ? "Automatic reviews" : "Manual reviews only"}</span>
            <span className="text-muted-foreground">{selected.settings.max_files == null ? "No file limit" : `${selected.settings.max_files} files per review`} · {selected.settings.language} comments</span>
          </> : <span className="text-muted-foreground">{admin ? "Choose an AI connection in Configure to enable them." : "Your administrator can enable them."}</span>}
        </div>
      </section>}
    </header>
    <ErrorNotice error={repos.error || (syncHere && action.error)} />
    {requested && !selected && <Empty title="Repository unavailable">
      This repository was not found or you no longer have access. <Link className="underline" to="/repositories">View available repositories</Link>.
    </Empty>}
    {!requested && !available?.length && <Empty title="Connect your first repository">
      {admin ? <>Install your GitHub App, then connect the installation in <Link className="underline" to="/settings?tab=github">settings</Link>.</> : "Ask your administrator to connect a repository you can access on GitHub."}
    </Empty>}
    {selected && <RepositoryPulls key={selected.id} repositoryId={selected.id} />}
    {admin && <div className="mt-6 text-xs text-muted-foreground"><Link className="rounded-sm underline underline-offset-4 hover:text-foreground" to="/settings?tab=github">{available?.length ? "Manage repositories" : "Connect repository"}</Link></div>}
  </>;
}

function runDuration(run: Run) {
  const seconds = run.started_at && run.finished_at
    ? Math.max(0, Math.round((+new Date(run.finished_at) - +new Date(run.started_at)) / 1000)) : null;
  return seconds === null ? null : seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
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
        const duration = runDuration(run);
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
                {duration && <><span className="tabular-nums whitespace-nowrap"><span className="sr-only">Duration: </span>{duration}</span> ·{" "}</>}
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

function findingTypeChoices(counts: Record<string, number>) {
  return Object.entries(counts).filter(([value]) => value).sort(([a], [b]) => a.localeCompare(b))
    .map(([value, count]) => ({ value, name: label(value), count }));
}

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
  const [tab, setTab] = useUrlTab("tab", ["findings", "history"], "findings");
  const filters = useReviewFilters("pull");
  const statuses = filters.params.getAll("status").filter((value) => value !== "all");
  const severities = filters.params.getAll("severity");
  const categories = filters.params.getAll("category");
  const search = filters.params.get("q") ?? "";
  const searchInput = useRef<HTMLInputElement>(null);
  const findingTrigger = useRef<HTMLButtonElement>(null);
  const [params, setParams] = useSearchParams();
  const finding = params.get("finding");
  function setFinding(value: string | null) {
    const next = new URLSearchParams(params);
    next.delete("finding_tab");
    if (value) next.set("finding", value); else next.delete("finding");
    setParams(next, { state: location.state });
  }
  const filterKey = JSON.stringify([id, filters.canonical]);
  const [pagination, setPagination] = useState({ filters: filterKey, pages: {} as Partial<Record<typeof findingStatuses[number], number>> });
  if (pagination.filters !== filterKey) setPagination({ filters: filterKey, pages: {} });
  const findingFilters = new URLSearchParams(filters.params);
  if (findingFilters.get("status") === "all") findingFilters.delete("status");
  const findingsQuery = useQuery<GroupedData<Schema["FindingsOut"]>, ApiError>({
    queryKey: ["pull-findings", id, filters.canonical, pagination.pages],
    queryFn: ({ signal }) => fetchGroupedPages<Schema["FindingsOut"]>(`/pulls/${id}/findings`, findingFilters, pagination.pages, signal),
    enabled: !!query.data && !(query.error && [401, 403].includes(query.error.status)),
    refetchInterval: Object.values(pagination.pages).some((page) => page! > 1) ? false : 5000,
    retry: false,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === id ? previous : undefined,
  });
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  if (findingsQuery.error && [401, 403].includes(findingsQuery.error.status)) return <ErrorNotice error={findingsQuery.error} />;
  const { pull_request: pr, repository, run_count, has_active_run: running, can_write } = query.data;
  const findings = findingsQuery.data;
  const total = findings?.registered_total ?? 0;
  const latest = pr.latest_run;
  const term = search.trim().toLowerCase();
  const count = (state: string) => findings?.status_counts[state] ?? 0;
  const sections = findings?.groups.filter((group) => group.total > 0) ?? [];
  const noOpenFindings = total > 0 && statuses.length === 1 && statuses[0] === "open" && !count("open") && !severities.length && !categories.length && !term;
  const reviewUnavailable = !can_write ? "You have read-only access to this pull request."
    : running ? null : pr.state !== "open" ? "Reviews can only run on open pull requests."
    : !repository.enabled ? "Reviews are paused. Enable them in repository configuration."
    : pr.fork ? "Fork reviews require a manual start." : null;
  function showAllFindings() {
    filters.clear();
    searchInput.current?.focus();
  }
  return <>
    <header className="mb-6 min-w-0">
      <div>
        <h1 className="break-words text-2xl font-semibold tracking-tight sm:text-3xl [overflow-wrap:anywhere]">
          <span className="font-normal tabular-nums text-muted-foreground">#{pr.number}</span> {pr.title}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <Status value={pr.state} />
          <span className="inline-flex min-w-0 items-center gap-1.5"><UserRound aria-hidden="true" className="size-3.5 shrink-0" /><span className="break-words [overflow-wrap:anywhere]">@{pr.author}</span></span>
          {pr.draft && <span>Draft</span>}
          <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1.5 gap-y-1"><GitBranch aria-hidden="true" className="size-3.5 shrink-0" /><code className="min-w-0 break-words [overflow-wrap:anywhere]">{pr.head_ref}</code><span>·</span><code title={pr.head_sha}>{pr.head_sha.slice(0, 7)}</code></span>
          <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex min-h-6 items-center gap-1 rounded-sm underline-offset-4 hover:text-foreground hover:underline">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
        </div>
      </div>
      <section aria-label="Latest review" className="mt-6 border-t pt-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium">{latest ? "Latest review" : "No reviews yet"}</span>
              {latest && <Status value={latest.status} />}
              {latest && <Link className="inline-flex min-h-6 items-center gap-1 rounded-sm text-xs underline-offset-4 hover:text-primary hover:underline" to={`/runs/${latest.id}`} state={{ inbox: inboxReturn, repository: repositoryReturn }}>Inspect review<ChevronRight aria-hidden="true" className="size-3.5" /></Link>}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {latest ? <><span>{date(latest.created_at)}</span><span>{latest.head_sha === pr.head_sha ? "Current commit" : "Earlier commit"} · <code>{latest.head_sha.slice(0, 7)}</code></span></> : <span>A review records findings and coverage for this commit.</span>}
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
    </header>
    <ErrorNotice error={action.error || query.error} />
    {latest && <RunFeedback run={latest} repositoryId={repository.id} admin={admin} canWrite={can_write} />}
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList className="mb-4">
        <TabsTrigger value="findings" aria-live="polite" aria-atomic="true">Findings <span className="ml-1 text-xs tabular-nums text-muted-foreground">{findings ? total : <span className="sr-only">{findingsQuery.error ? "unavailable" : "loading…"}</span>}</span></TabsTrigger>
        <TabsTrigger value="history">Run history <span className="ml-1 text-xs tabular-nums text-muted-foreground">{run_count}</span></TabsTrigger>
      </TabsList>
      <TabsContent value="findings" className="min-w-0">
        <div className="mb-4">
          <FilterBar query={search} searchResetKey={filters.searchResetKey} searchLabel="Search findings" searchPlaceholder="Search findings…" searchInputRef={searchInput} onSearch={filters.search}
            filters={[
              { key: "status", label: "Status", values: statuses, choices: findingStatuses.map((value) => ({ value, name: label(value), count: count(value) })) },
              { key: "severity", label: "Severity", values: severities, choices: findingSeverities.map((value) => ({ value, name: label(value) })) },
              { key: "category", label: "Type", values: categories, choices: findingTypeChoices(findings?.category_counts ?? {}), searchPlaceholder: "Search types…" },
            ]}
            onChange={filters.change} />
        </div>
        {findingsQuery.error && <div className="mb-4 space-y-2"><ErrorNotice error={findingsQuery.error} /><Button variant="outline" size="sm" onClick={() => findingsQuery.refetch()}>Retry findings</Button></div>}
        {findingsQuery.isPending && <Loading />}
        {findings && (findings.total > 0 ? <section aria-label="Findings" className="space-y-6" aria-busy={findingsQuery.isPlaceholderData}>
          {sections.map(({ group: status, items, page, total: groupTotal, has_more }) => {
            const busy = findingsQuery.isFetching && (pagination.pages[status] ?? 1) > page;
            const error = findings.errors[status];
            return <section key={status} aria-label={`${label(status)} findings`}>
              <h2 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">{label(status)}<span className="text-xs font-normal tabular-nums text-muted-foreground">{groupTotal}</span></h2>
              <div className="flex flex-col divide-y overflow-hidden rounded-lg border bg-card">
                {items.map((item) => <FindingCard key={item.id} evidence={item}
                  onClick={(event) => { findingTrigger.current = event.currentTarget; setFinding(item.id); }}
                  state={<span className="flex flex-col items-start gap-1.5">
                    <Status value={item.status} icon={item.status === "resolved" ? <CircleCheck className="size-3.5" aria-hidden="true" /> : undefined} />
                    {item.desired_status && <span className="text-xs text-muted-foreground">{item.sync_error ? "Sync needs attention" : "Sync pending"}</span>}
                  </span>} />)}
                {error && <div className="space-y-2 px-5 py-3"><ErrorNotice error={error} /><Button variant="outline" size="sm" onClick={() => findingsQuery.refetch()}>Retry {status} findings</Button></div>}
                {has_more && page >= INBOX_MAX_PAGES && <p className="px-5 py-3 text-sm text-muted-foreground">Use filters to narrow the remaining findings.</p>}
                {(has_more || busy) && page < INBOX_MAX_PAGES && !error && <div className="flex items-center justify-between gap-3 px-5 py-3">
                  <Button variant="ghost" size="sm" disabled={busy || findingsQuery.isPlaceholderData} onClick={() => setPagination({ filters: filterKey, pages: { ...pagination.pages, [status]: page + 1 } })}>{busy && <LoaderCircle className="size-3 motion-safe:animate-spin" />}{busy ? "Loading…" : "Load more"}<span className="sr-only"> {status} findings</span></Button>
                  <span className="text-xs tabular-nums text-muted-foreground">{items.length} of {groupTotal}</span>
                </div>}
              </div>
            </section>;
          })}
        </section> : !findingsQuery.isPlaceholderData && <Empty title={noOpenFindings ? "No open findings" : total ? "No findings match these filters" : run_count ? "No findings recorded" : "No review findings yet"}>
          <p>{noOpenFindings ? "Earlier findings are resolved or dismissed. You can still inspect their evidence and history." : total ? "Try another search, status, severity, or type." : run_count ? "Check run history for completion and coverage. An incomplete review is not a clean result." : "Findings will appear here after a review. Each finding keeps its evidence and resolution history."}</p>
          {total > 0 && <Button variant="outline" className="mt-4" onClick={showAllFindings}>View all findings</Button>}
        </Empty>)}
        <p className="mt-5 text-xs leading-relaxed text-muted-foreground">Resolving or reopening a GitHub review thread updates its finding here. A finding missing from a later run stays open until someone resolves it.</p>
      </TabsContent>
      <TabsContent value="history" className="min-w-0"><RunList prId={pr.id} /></TabsContent>
    </Tabs>
    <Sheet open={!!finding} onOpenChange={(open) => { if (!open) setFinding(null); }}>
      <SheetContent className="w-full gap-0 overflow-hidden sm:max-w-2xl" onCloseAutoFocus={(event) => {
        event.preventDefault();
        (findingTrigger.current?.isConnected ? findingTrigger.current : searchInput.current)?.focus();
      }}>
        <SheetHeader className="shrink-0 px-6 pb-4 pt-5"><SheetTitle>Finding details</SheetTitle><SheetDescription className="sr-only">Review the evidence and its resolution history.</SheetDescription></SheetHeader>
        {finding && <FindingPanel key={finding} id={finding} canWrite={can_write} />}
      </SheetContent>
    </Sheet>
  </>;
}

function FindingCategory({ value }: { value: string }) {
  const icons = new Map([["bug", Bug], ["security", Shield], ["performance", Gauge], ["maintainability", Wrench]]);
  const Icon = icons.get(value) ?? Tag;
  return <Badge variant="outline" className="gap-1.5 text-muted-foreground"><Icon aria-hidden="true" />{label(value)}</Badge>;
}

function FindingCard({ evidence, state, onClick }: {
  evidence: Schema["FindingOut"] | Schema["OccurrenceOut"]["snapshot"];
  state: ReactNode; onClick: MouseEventHandler<HTMLButtonElement>;
}) {
  const path = String(evidence.path ?? "Path not recorded");
  const slash = path.lastIndexOf("/");
  return <button type="button" data-slot="finding-card" onClick={onClick}
    className="group w-full min-w-0 bg-card p-4 text-left text-card-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:p-5">
    <span className="flex flex-col items-start gap-3 sm:flex-row sm:justify-between">
      <span className="flex min-w-0 max-w-full items-center gap-2">
        <FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 font-mono text-xs leading-[22px]">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1 [overflow-wrap:anywhere]">
            <span className="min-w-0">
              {slash >= 0 && <span className="text-muted-foreground">{path.slice(0, slash + 1)}</span>}<span className="font-medium">{path.slice(slash + 1)}</span>
            </span>
            {evidence.start_line != null && <span className="text-muted-foreground">{String(evidence.start_line)}{evidence.end_line && evidence.end_line !== evidence.start_line ? `–${evidence.end_line}` : ""}</span>}
          </span>
        </span>
      </span>
      <span className="flex shrink-0">{state}</span>
    </span>
    <span className="mt-4 line-clamp-3 break-words text-sm font-medium leading-relaxed [overflow-wrap:anywhere]">{String(evidence.content ?? "Finding evidence").replaceAll(/[*`#]/g, "")}</span>
    <span className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <span className="flex flex-wrap items-center gap-2">{evidence.severity != null ? <Status value={String(evidence.severity)} /> : <span className="text-xs text-muted-foreground">Proposed finding</span>}{!!evidence.category && <FindingCategory value={String(evidence.category)} />}</span>
    </span>
  </button>;
}

function FindingPanel({ id, canWrite, snapshot, headSha }: {
  id: string; canWrite: boolean; snapshot?: Schema["OccurrenceOut"]["snapshot"]; headSha?: string;
}) {
  const query = useApi<Schema["FindingDetail"]>(`/findings/${id}`, true);
  const action = useAction();
  const [dismiss, setDismiss] = useState(false);
  const [reason, setReason] = useState("");
  const [tab, setTab] = useUrlTab("finding_tab", snapshot ? ["snapshot", "evidence", "activity"] : ["evidence", "activity"], snapshot ? "snapshot" : "evidence");
  const activityTab = useRef<HTMLButtonElement>(null);
  if (query.isPending && !snapshot)
    return (
      <div className="px-6">
        <Loading />
      </div>
    );
  if (!query.data && !snapshot) return <div className="px-6"><ErrorNotice error={query.error} /></div>;
  const f = query.data?.finding;
  const activities = query.data?.activities ?? [];
  const verifications = query.data?.verifications ?? [];
  const evidence = tab === "snapshot" && snapshot ? snapshot : f;
  const path = String(evidence?.path ?? "");
  const latestVerification = verifications[0];
  const StatusIcon = f?.status === "resolved" ? CircleCheck : f?.status === "dismissed" ? CircleSlash : CircleDot;
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
    <div className="flex min-h-0 flex-1 flex-col">
      {evidence && <div className="shrink-0 space-y-3 px-6 pb-5">
        <div className="flex items-center gap-2.5">
          <FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="min-w-0 font-mono text-xs">
            <p className="break-words [overflow-wrap:anywhere]"><span className="text-muted-foreground">{path.includes("/") ? path.slice(0, path.lastIndexOf("/") + 1) : ""}</span><span className="font-medium">{path.slice(path.lastIndexOf("/") + 1)}</span></p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2"><Status value={String(evidence.severity ?? "unknown")} />{!!evidence.category && <FindingCategory value={String(evidence.category)} />}</div>
      </div>}
      <div className="grid shrink-0 grid-cols-[18px_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-y bg-muted/50 px-6 py-4 sm:grid-cols-[18px_minmax(0,1fr)_auto]">
        <StatusIcon className={`mt-0.5 size-[18px] ${f?.status === "resolved" ? "text-emerald-800 dark:text-emerald-300" : "text-muted-foreground"}`} aria-hidden="true" />
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Current status</span>{f ? <Status value={f.status} /> : <span className="text-xs">{query.isPending ? "Loading…" : "Unavailable"}</span>}</div>
          <p className="text-xs leading-relaxed text-muted-foreground">{latestVerification ? <>Latest verification: {words(latestVerification.verdict ?? latestVerification.status)} at <span className="font-mono">{latestVerification.head_sha.slice(0, 7)}</span>.</> : "Original review evidence and resolution history are preserved."}</p>
        </div>
        <Button variant="link" size="sm" className="col-start-2 min-h-11 justify-self-start px-0 text-xs sm:col-start-auto sm:min-h-8" onClick={() => { setTab("activity"); activityTab.current?.focus(); }}>{verifications.length ? "View verification" : "View activity"}</Button>
      </div>
      <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
        <div className="shrink-0 px-6 pt-5"><TabsList aria-label="Finding information" className={snapshot ? "max-w-full [&>button]:px-2 [&>button]:text-xs sm:[&>button]:text-sm" : ""}>
          {snapshot && <TabsTrigger value="snapshot">At this run</TabsTrigger>}
          <TabsTrigger value="evidence">{snapshot ? "Current status" : "Evidence"}</TabsTrigger>
          <TabsTrigger ref={activityTab} value="activity">Activity <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data ? activities.length : "…"}</span></TabsTrigger>
        </TabsList></div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" data-slot="finding-scroll">
          {snapshot && <TabsContent value="snapshot" className="space-y-6"><p className="text-xs text-muted-foreground">Saved at <span className="font-mono">{headSha?.slice(0, 7)}</span></p><FindingEvidence evidence={snapshot} /></TabsContent>}
          <TabsContent value="evidence" className="space-y-6">{f ? <FindingEvidence evidence={f} /> : query.isPending ? <Loading /> : <p className="text-sm text-muted-foreground">Current evidence could not be loaded.</p>}</TabsContent>
          <TabsContent value="activity" className="space-y-6">
          {query.isPending && <Loading />}
          {!query.isPending && !query.data && <p className="text-sm text-muted-foreground">Activity could not be loaded.</p>}
          {verifications.map((item) => (
          <section key={item.id} className="space-y-3 border-b pb-6">
            <div className="flex flex-wrap gap-2">
              <h3 className="text-sm font-semibold">Fix verification</h3>
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
          </section>
        ))}
        <h3 className="text-sm font-semibold">Activity</h3>
        <ol className="space-y-5">
          {activities.map((event) => (
            <li key={event.id} className="relative ml-1 border-l pl-5 before:absolute before:-left-1 before:top-1.5 before:size-[7px] before:rounded-full before:bg-muted-foreground">
              <p className="text-sm font-medium">{label(event.action)}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{event.actor} · {words(event.source)} · {date(event.created_at)}</p>
              {event.reason && <details className="mt-2 text-sm"><summary className="cursor-pointer text-xs text-primary">View reason</summary><p className="mt-2 break-words leading-relaxed">{event.reason}</p></details>}
            </li>
          ))}
        </ol>
        {query.data && !activities.length && <p className="text-sm text-muted-foreground">No activity recorded.</p>}
          </TabsContent>
        </div>
      </Tabs>
      <SheetFooter className="max-h-[50dvh] shrink-0 gap-3 overflow-y-auto border-t px-6 py-4">
        <ErrorNotice error={action.error || f?.sync_error || query.error} />
        {f?.desired_status && <p role="status" className="text-xs leading-relaxed text-muted-foreground">{f.sync_error ? "GitHub has not confirmed the change. Retry to sync again." : `Waiting for GitHub to confirm: ${f.desired_status}.`}</p>}
        {f && <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
          {f.github_url && <span className="[&>a]:min-h-11 [&>a]:w-full sm:[&>a]:min-h-9 sm:[&>a]:w-auto"><ExternalLink href={f.github_url}>GitHub thread</ExternalLink></span>}
          {canWrite && <div className="flex flex-wrap gap-2 sm:ml-auto">
            <Button className="min-h-11 flex-1 sm:min-h-9 sm:flex-none" disabled={action.isPending || (!!f.desired_status && !f.sync_error)} onClick={() => f.sync_error ? retrySync() : change(f.desired_status ?? (f.status === "open" ? "resolved" : "open"))}>
              {f.sync_error ? <RefreshCw aria-hidden="true" /> : f.status === "open" ? <CircleCheck aria-hidden="true" /> : <RotateCcw aria-hidden="true" />}{findingActionLabel(f)}
            </Button>
            {f.desired_status === "resolved" && !!f.sync_intent?.verification_id && <Button variant="outline" className="min-h-11 sm:min-h-9" disabled={action.isPending} onClick={() => change("open")}>Keep finding open</Button>}
            {f.status === "open" && !f.desired_status && <Button variant="outline" className="min-h-11 sm:min-h-9" onClick={() => setDismiss(!dismiss)}>Dismiss</Button>}
          </div>}
        </div>}
        {dismiss && f && <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void change("dismissed"); }}>
          <Field label="Why dismiss this finding?">{(id) => <Textarea id={id} autoFocus required maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="For example, this is intentional and covered by…" />}</Field>
          <Button disabled={!reason.trim() || action.isPending}>Confirm dismissal</Button>
        </form>}
      </SheetFooter>
    </div>
  );
}

function FindingEvidence({ evidence }: { evidence: Schema["FindingOut"] | Schema["OccurrenceOut"]["snapshot"] }) {
  return <>
    <section className="space-y-3"><h3 className="text-sm font-semibold">Explanation</h3><Markdown>{String(evidence.content ?? "")}</Markdown></section>
    <CodeComparison original={String(evidence.existing_code ?? "")} suggested={String(evidence.suggestion ?? "")} path={String(evidence.path ?? "")} />
    <p className="flex items-center gap-2 text-xs leading-relaxed text-muted-foreground"><History className="size-3.5 shrink-0" aria-hidden="true" />Each review keeps its original evidence.</p>
  </>;
}

function findingActionLabel(finding: Schema["FindingOut"]) {
  if (finding.sync_error) return "Retry sync";
  if (finding.status === "open") return "Resolve finding";
  return "Reopen finding";
}

function getCandidateFeedback(candidate: Schema["CandidateOut"]) {
  if (candidate.finding_id && candidate.is_new) return { label: "New validated finding", description: "Confirmed by evidence and saved as a new finding." };
  if (candidate.finding_id) return { label: "Repeated observation", description: "Matches an existing finding from review history." };
  if (candidate.result?.verdict === "rejected") return { label: "Not supported", description: "The evidence did not support this proposed issue." };
  if (active(candidate.status)) return { label: "Awaiting assessment", description: "Queued or being checked against evidence." };
  return { label: "Withheld", description: "Not accepted as a finding because assessment was uncertain or incomplete." };
}

function CandidateStatus({ candidate, inCard = false }: { candidate: Schema["CandidateOut"]; inCard?: boolean }) {
  const feedback = getCandidateFeedback(candidate);
  let value = "partial";
  if (candidate.finding_id) value = "completed";
  else if (candidate.result?.verdict === "rejected") value = "rejected";
  else if (active(candidate.status)) value = candidate.status;
  return <Tooltip>
    <TooltipTrigger asChild><span tabIndex={inCard ? undefined : 0} className="inline-flex cursor-pointer rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
      <Status value={value} label={feedback.label} />
      {inCard && <span className="sr-only">{feedback.description}</span>}
    </span></TooltipTrigger>
    <TooltipContent className="max-w-xs" sideOffset={6}>{feedback.description}</TooltipContent>
  </Tooltip>;
}

function ResolvedList({ runId, running, onOpen }: {
  runId: string; running: boolean; onOpen: (finding: Schema["FindingOut"], trigger: HTMLButtonElement) => void;
}) {
  const filters = useReviewFilters("resolved");
  const { params, canonical } = filters;
  const [list, setList] = useState({ filters: canonical, version: 0 });
  if (list.filters !== canonical) setList({ filters: canonical, version: list.version + 1 });
  const searchInput = useRef<HTMLInputElement>(null);
  const query = useInfiniteQuery<Schema["ResolvedPage"], ApiError>({
    queryKey: ["run-resolved", runId, canonical, list.version],
    queryFn: ({ pageParam, signal }) => api<Schema["ResolvedPage"]>(`/runs/${runId}/resolved?${params}&page=${pageParam}`, "GET", undefined, undefined, signal),
    initialPageParam: 1,
    getNextPageParam: (last, pages) => last.has_more && pages.length < INBOX_MAX_PAGES ? last.page + 1 : undefined,
    refetchInterval: (query) => running && (query.state.data?.pages.length ?? 0) <= 1 ? 5000 : false,
    retry: false,
  });
  if (query.error && [401, 403, 404].includes(query.error.status)) return <ErrorNotice error={query.error} />;
  const first = query.data?.pages[0];
  const last = query.data?.pages.at(-1);
  const seen = new Set<string>();
  const findings = (query.data?.pages.flatMap((page) => page.items) ?? []).filter((finding) => {
    if (seen.has(finding.id)) return false;
    seen.add(finding.id);
    return true;
  });
  return <div className="space-y-4">
    <FilterBar query={params.get("q") ?? ""} searchResetKey={filters.searchResetKey} searchLabel="Search resolved findings" searchPlaceholder="Search findings…" searchInputRef={searchInput} onSearch={filters.search}
      filters={[
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: findingSeverities.map((value) => ({ value, name: label(value), count: first?.severity_counts[value] })) },
        { key: "category", label: "Type", values: params.getAll("category"), choices: findingTypeChoices(first?.category_counts ?? {}), searchPlaceholder: "Search types…" },
      ]} onChange={filters.change}>
      {last && <span role="status" className="sr-only">{last.total} of {last.registered_total} resolved findings</span>}
    </FilterBar>
    <ErrorNotice error={query.error} />
    <section aria-label="Findings resolved by this run" className={findings.length ? "flex flex-col divide-y overflow-hidden rounded-lg border bg-card" : undefined}>
      {query.isPending ? <Loading /> : findings.length ? findings.map((item) => <FindingCard key={item.id} evidence={item}
        state={<Status value={item.status} label={item.status === "resolved" ? "Resolved" : `Currently ${words(item.status)}`} />}
        onClick={(event) => onOpen(item, event.currentTarget)} />) : !query.error && <Empty title={first?.registered_total ? "No resolved findings match these filters" : running ? "No confirmed resolutions yet" : "No findings resolved by this run"}>
        <p>{first?.registered_total ? "Try another search, severity, or type." : "Fixes appear here after verification and GitHub synchronization complete. Findings absent from a review are not automatically resolved."}</p>
        {!!first?.registered_total && <Button variant="outline" className="mt-4" onClick={() => { filters.clear(); searchInput.current?.focus(); }}>Clear filters</Button>}
      </Empty>}
      {(query.hasNextPage || (query.data?.pages.length ?? 0) > 1 || query.error) && <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
        {query.hasNextPage ? <Button variant="ghost" size="sm" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
          {query.isFetchingNextPage && <LoaderCircle className="size-3 motion-safe:animate-spin" />}{query.isFetchingNextPage ? "Loading…" : query.isFetchNextPageError ? "Retry" : "Load more"}<span className="sr-only"> resolved findings</span>
        </Button> : query.error ? <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry resolved findings</Button>
          : last?.has_more ? <p className="text-xs text-muted-foreground">Narrow the filters to see the remaining resolved findings.</p> : null}
        {last && <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">{findings.length} of {last.total} shown</span>}
      </div>}
    </section>
  </div>;
}

function AssessmentList({ runId, running, onOpen }: {
  runId: string; running: boolean; onOpen: (candidate: Schema["CandidateOut"], trigger: HTMLButtonElement) => void;
}) {
  const filters = useReviewFilters("assessment");
  const { params, canonical } = filters;
  const [list, setList] = useState({ filters: canonical, version: 0 });
  if (list.filters !== canonical) setList({ filters: canonical, version: list.version + 1 });
  const searchInput = useRef<HTMLInputElement>(null);
  const query = useInfiniteQuery<Schema["CandidatePage"], ApiError>({
    queryKey: ["run-candidates", runId, canonical, list.version],
    queryFn: ({ pageParam, signal }) => api<Schema["CandidatePage"]>(`/runs/${runId}/candidates?${params}&page=${pageParam}`, "GET", undefined, undefined, signal),
    initialPageParam: 1,
    getNextPageParam: (last, pages) => last.has_more && pages.length < INBOX_MAX_PAGES ? last.page + 1 : undefined,
    refetchInterval: (query) => running && (query.state.data?.pages.length ?? 0) <= 1 ? 5000 : false,
    retry: false,
  });
  if (query.error && [401, 403].includes(query.error.status)) return <ErrorNotice error={query.error} />;
  const first = query.data?.pages[0];
  const last = query.data?.pages.at(-1);
  const seen = new Set<string>();
  const candidates = (query.data?.pages.flatMap((page) => page.items) ?? []).filter((candidate) => {
    if (seen.has(candidate.id)) return false;
    seen.add(candidate.id);
    return true;
  });
  return <div className="mt-6 space-y-4">
    <FilterBar query={params.get("q") ?? ""} searchResetKey={filters.searchResetKey} searchLabel="Search assessments" searchPlaceholder="Search assessments…" searchInputRef={searchInput} onSearch={filters.search}
      filters={[
        { key: "outcome", label: "Result", values: params.getAll("outcome"), choices: candidateOutcomes.map((choice) => ({ ...choice, count: first?.outcome_counts[choice.value] })) },
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: findingSeverities.map((value) => ({ value, name: label(value), count: first?.severity_counts[value] })) },
        { key: "category", label: "Type", values: params.getAll("category"), choices: findingTypeChoices(first?.category_counts ?? {}), searchPlaceholder: "Search types…" },
      ]} onChange={filters.change}>
      {last && <span role="status" className="sr-only">{last.total} of {last.registered_total} assessments</span>}
    </FilterBar>
    <ErrorNotice error={query.error} />
    <div className={candidates.length ? "flex flex-col divide-y overflow-hidden rounded-lg border bg-card" : undefined}>
      {query.isPending ? <Loading /> : candidates.length ? candidates.map((item) => <FindingCard key={item.id} evidence={item.snapshot}
        state={<CandidateStatus candidate={item} inCard />}
        onClick={(event) => onOpen(item, event.currentTarget)} />) : !query.error && <Empty title={first?.registered_total ? "No assessments match these filters" : "No candidates to assess"}>
        <p>{first?.registered_total ? "Try another search, result, severity, or type." : running ? "Candidates appear after the initial review finishes." : "No candidates were produced for assessment."}</p>
        {!!first?.registered_total && <Button variant="outline" className="mt-4" onClick={() => { filters.clear(); searchInput.current?.focus(); }}>View all assessments</Button>}
      </Empty>}
      {(query.hasNextPage || (query.data?.pages.length ?? 0) > 1 || query.error) && <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
        {query.hasNextPage ? <Button variant="ghost" size="sm" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
          {query.isFetchingNextPage && <LoaderCircle className="size-3 motion-safe:animate-spin" />}{query.isFetchingNextPage ? "Loading…" : query.isFetchNextPageError ? "Retry" : "Load more"}<span className="sr-only"> assessments</span>
        </Button> : query.error ? <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry assessments</Button>
          : last?.has_more ? <p className="text-xs text-muted-foreground">Narrow the filters to see the remaining assessments.</p> : null}
        {last && <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">{candidates.length} of {last.total} shown</span>}
      </div>}
    </div>
  </div>;
}

function AssessmentError({ error, withheld }: { error: string; withheld: boolean }) {
  const [copying, setCopying] = useState(false);
  const feedback = assessmentFeedback(error);
  return <div className="space-y-3">
    <ErrorNotice title={feedback.title} error={`${feedback.message}${withheld ? " This candidate was withheld from publication." : ""}`} />
    {feedback.citation && <p className="break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">{feedback.citation}</p>}
    <details className="group text-sm">
      <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 font-medium outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden">Technical details<ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary>
      <div className="mt-3 space-y-3">
        <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={copying} onClick={async () => {
          setCopying(true);
          try {
            await navigator.clipboard.writeText(error);
            toast.success("Assessment details copied");
          } catch {
            toast.error("Could not copy details", { description: "Select the text in Technical details and copy it manually." });
          } finally { setCopying(false); }
        }}>{copying ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : <Copy aria-hidden="true" />}{copying ? "Copying…" : "Copy details"}</Button>
        <pre role="region" aria-label="Assessment technical details" tabIndex={0} className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring [overflow-wrap:anywhere]">{error}</pre>
      </div>
    </details>
  </div>;
}

function AssessmentPanel({ candidate, headSha, runId, canRetry, onOpenFinding }: {
  candidate: Schema["CandidateOut"]; headSha: string; runId: string; canRetry: boolean; onOpenFinding: () => void;
}) {
  const [tab, setTab] = useUrlTab("assessment_tab", ["assessment", "evidence"], "assessment");
  const retry = useAction<Schema["CandidateOut"]>();
  const { snapshot, result } = candidate;
  const path = String(snapshot.path ?? "Path not recorded");
  const slash = path.lastIndexOf("/");
  const heading = candidate.finding_id ? candidate.is_new ? "Why it was validated" : "Why it is repeated" : result?.verdict === "rejected" ? "Why it was not supported" : "Why it was withheld";
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="shrink-0 space-y-3 px-6 pb-5">
      <div className="flex min-w-0 items-center gap-2.5">
        <FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 font-mono text-xs">
          <p className="break-words [overflow-wrap:anywhere]">{slash >= 0 && <span className="text-muted-foreground">{path.slice(0, slash + 1)}</span>}<span className="font-medium">{path.slice(slash + 1)}</span></p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {snapshot.severity != null && <Status value={String(snapshot.severity)} />}
        {!!snapshot.category && <FindingCategory value={String(snapshot.category)} />}
      </div>
    </div>
    <div className="flex shrink-0 items-start gap-3 border-y bg-muted/50 px-6 py-4">
      <ClipboardCheck className="mt-0.5 size-[18px] shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Assessment result</span><CandidateStatus candidate={candidate} /></div>
        <p className="text-xs leading-relaxed text-muted-foreground">Reviewed commit <span className="font-mono">{headSha.slice(0, 7)}</span>. Finding resolution is tracked separately.</p>
      </div>
    </div>
    <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
      <div className="shrink-0 px-6 pt-5"><TabsList aria-label="Assessment information">
        <TabsTrigger autoFocus={tab === "assessment"} value="assessment">Assessment</TabsTrigger>
        <TabsTrigger autoFocus={tab === "evidence"} value="evidence">Evidence <span className="text-xs font-normal tabular-nums text-muted-foreground">{result?.evidence.length ?? 0}</span></TabsTrigger>
      </TabsList></div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" data-slot="assessment-scroll">
        <TabsContent value="assessment" className="space-y-6">
          {candidate.error && <AssessmentError error={candidate.error} withheld={!candidate.finding_id} />}
          <ErrorNotice error={retry.error} />
          {result ? <>
            <section className="space-y-3"><h3 className="text-sm font-semibold">{heading}</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.explanation}</p></section>
            <section className="space-y-3"><h3 className="text-sm font-semibold">Earlier reviews</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.history_explanation || "No history explanation was recorded."}</p></section>
            <section className="space-y-3"><h3 className="text-sm font-semibold">Changed conditions</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.changed_conditions || "No changed conditions were recorded."}</p></section>
          </> : !candidate.error && <p role={active(candidate.status) ? "status" : undefined} className="text-sm text-muted-foreground">{active(candidate.status) ? "Assessment is waiting for the review worker." : "No assessment result was recorded."}</p>}
          {!!candidate.attempts?.length && <details className="space-y-4 text-sm">
            <summary className="cursor-pointer font-medium">Previous attempts ({candidate.attempts.length})</summary>
            {candidate.attempts.map((attempt, index) => <section key={index} className="space-y-3 border-t pt-4">
              <h3 className="text-sm font-semibold">Attempt {index + 1}{attempt.finished_at && <span className="ml-2 text-xs font-normal text-muted-foreground">{date(attempt.finished_at)}</span>}</h3>
              {attempt.error && <AssessmentError error={attempt.error} withheld />}
              {typeof attempt.usage.tokens === "number" && <p className="text-xs text-muted-foreground">{attempt.usage.tokens.toLocaleString()} tokens used or reserved.</p>}
              {typeof attempt.usage.context_bytes === "number" && <p className="text-xs text-muted-foreground">{attempt.usage.context_bytes.toLocaleString()} context bytes used.</p>}
            </section>)}
          </details>}
        </TabsContent>
        <TabsContent value="evidence" className="space-y-6">
          <section className="space-y-3"><h3 className="text-sm font-semibold">Supporting evidence</h3><p className="text-xs text-muted-foreground">Saved from the reviewed commit <span className="font-mono">{headSha.slice(0, 7)}</span>.</p></section>
          {result?.evidence.length ? result.evidence.map((evidence, index) => <section key={`${evidence.revision}-${evidence.path}-${evidence.start_line}-${index}`} className="space-y-3">
            <p className="break-words font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{evidence.revision === "original" ? "Original" : "Current"} evidence: {evidence.path}:{evidence.start_line}–{evidence.end_line}</p>
            <Code path={evidence.path}>{evidence.quote}</Code>
          </section>) : <p className="text-sm text-muted-foreground">{active(candidate.status) ? "Evidence will appear when assessment completes." : "No supporting evidence was recorded."}</p>}
        </TabsContent>
        <details className="mt-6 border-t pt-4 text-sm"><summary className="cursor-pointer font-medium">Original proposal</summary><div className="mt-3 max-w-[75ch]"><Markdown>{String(snapshot.content ?? "No proposal text was recorded.")}</Markdown></div></details>
      </div>
    </Tabs>
    <SheetFooter className="shrink-0 border-t px-6 py-4">
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs leading-relaxed text-muted-foreground">{canRetry && candidate.retryable ? "Retry uses the same connection and AI quota." : candidate.finding_id ? "Finding resolution is tracked separately." : "Not linked to a finding."}</p>
        <div className="flex flex-wrap gap-2">
          {canRetry && candidate.retryable && <Button className="min-h-11 sm:min-h-9" disabled={retry.isPending} onClick={() => retry.mutate({ path: `/runs/${runId}/candidates/${candidate.id}/retry` })}>
            <RefreshCw aria-hidden="true" className={retry.isPending ? "motion-safe:animate-spin" : ""} />{retry.isPending ? "Retrying assessment…" : "Retry assessment"}
          </Button>}
          {candidate.finding_id ? <Button className="min-h-11 sm:min-h-9" onClick={onOpenFinding}><ArrowUpRight aria-hidden="true" />Open finding</Button> : <SheetClose asChild><Button variant="outline" className="min-h-11 sm:min-h-9">Close</Button></SheetClose>}
        </div>
      </div>
    </SheetFooter>
  </div>;
}

export function RunDetail({ user }: { user: Schema["UserOut"] }) {
  const { id } = useParams();
  const query = useApi<Schema["RunDetail"]>(`/runs/${id}`, true);
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  return <RunWorkspace key={id} detail={query.data} user={user} error={query.error} />;
}

const checkDescriptions: Record<string, string> = {
  queued: "Waiting for the review to start.",
  in_progress: "Review, publication, or fix synchronization is still in progress.",
  completed: "The GitHub check has finished.",
  success: "The review completed and comments were handled. Findings are informational, not approval to merge.",
  neutral: "The review completed, but assessment or fix verification is incomplete.",
  failure: "The review failed, coverage is incomplete, or comments could not be fully published.",
  cancelled: "The review was cancelled or no longer applies to this pull request.",
  skipped: "The review was skipped. Inspect the run outcome for the reason.",
  timed_out: "The review exceeded its time limit.",
  action_required: "The review or GitHub synchronization needs attention.",
};

function RunWorkspace({ detail, user, error }: { detail: Schema["RunDetail"]; user: Schema["UserOut"]; error: ApiError | null }) {
  const { run, pull_request: pr, repository, occurrences, resolved_count = 0, assessment, can_write } = detail;
  const action = useAction();
  const location = useLocation();
  const returnState = { inbox: getInboxReturn(location.state), repository: getRepositoryReturn(location.state) };
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useUrlTab("tab", ["findings", "resolved", "assessment", "coverage", "details"], "findings");
  const filters = useReviewFilters("run");
  const search = filters.params.get("q") ?? "";
  const severities = filters.params.getAll("severity");
  const categories = filters.params.getAll("category");
  const findingId = params.get("finding");
  const finding = findingId ? { finding_id: findingId, snapshot: occurrences.find((item) => item.finding_id === findingId)?.snapshot } : null;
  const candidateId = params.get("candidate");
  const [candidateSeed, setCandidateSeed] = useState<Schema["CandidateOut"] | null>(null);
  function showSheet(key: "finding" | "candidate", value: string | null) {
    const next = new URLSearchParams(params);
    next.delete(`${key === "finding" ? "finding" : "assessment"}_tab`);
    if (key === "candidate") { next.delete("finding"); next.delete("finding_tab"); }
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { state: location.state });
  }
  function closeSheet() {
    const next = new URLSearchParams(params);
    for (const key of ["finding", "finding_tab", "candidate", "assessment_tab"]) next.delete(key);
    setParams(next, { state: location.state });
  }
  const candidateQuery = useQuery<Schema["CandidateOut"], ApiError>({
    queryKey: ["run-candidate", run.id, candidateId],
    queryFn: ({ signal }) => api<Schema["CandidateOut"]>(`/runs/${run.id}/candidates/${encodeURIComponent(candidateId!)}`, "GET", undefined, undefined, signal),
    enabled: !!candidateId, initialData: candidateSeed?.id === candidateId ? candidateSeed : undefined,
    refetchInterval: (query) => candidateId && !query.state.error && active(query.state.data?.status ?? "") ? 5000 : false,
    retry: false,
  });
  const candidateDenied = candidateId && candidateQuery.error && [401, 403, 404].includes(candidateQuery.error.status);
  const candidate = candidateDenied ? undefined : candidateQuery.data;
  const searchInput = useRef<HTMLInputElement>(null);
  const findingTrigger = useRef<HTMLButtonElement>(null);
  const resolvedTab = useRef<HTMLButtonElement>(null);
  const assessmentTab = useRef<HTMLButtonElement>(null);
  const duration = runDuration(run);
  const connection = run.config.connection as Record<string, unknown> | undefined;
  const checkValue = run.check_conclusion || run.check_status;
  const feedback = runFeedback(run);
  const verification = run.progress?.verification;
  const failedFixVerifications = verification && typeof verification === "object" && "failed" in verification ? verification.failed : null;
  const term = search.trim().toLowerCase();
  const filtered = occurrences.filter(({ snapshot }) => (!severities.length || severities.includes(String(snapshot.severity)))
    && (!categories.length || categories.includes(String(snapshot.category)))
    && (!term || [snapshot.path, snapshot.content, snapshot.category].some((value) => String(value ?? "").toLowerCase().includes(term))));
  const categoryCounts = Object.fromEntries([...new Set(occurrences.map(({ snapshot }) => String(snapshot.category ?? "")).filter(Boolean))]
    .map((value) => [value, occurrences.filter(({ snapshot }) => snapshot.category === value).length]));
  const engine = run.engine === "chatgpt" ? "Codex (ChatGPT)" : run.engine === "claude_code" ? "Claude Code" : "Open Code Review";
  return <>
    <header className="mb-6 min-w-0">
      <div>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-semibold tracking-tight sm:text-3xl">Review run <Status value={run.status} /></h1>
          {can_write && (active(run.status) || run.status === "blocked" || ["failed", "partially_published"].includes(run.publication)) && <div className="flex flex-wrap gap-2">
            {run.status === "blocked" && (run.owner_id == null || run.owner_id === user.id) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/retry` })}>Resume review</Button>}
            {(active(run.status) || run.status === "blocked") && <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/cancel` })}>Cancel review</Button>}
            {["failed", "partially_published"].includes(run.publication) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/publish` })}>Retry GitHub publication</Button>}
          </div>}
        </div>
        <Link className="mt-2 block break-words rounded-sm text-sm underline-offset-4 hover:text-primary hover:underline [overflow-wrap:anywhere]" to={`/pulls/${pr.id}`} state={returnState}>#{pr.number} · {pr.title}</Link>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <span>{date(run.created_at)}</span>
          <span>{words(run.trigger)}{duration && ` · ${duration}`}</span>
          <span className="inline-flex flex-wrap items-center gap-1.5"><GitCommitHorizontal aria-hidden="true" className="size-3.5 shrink-0" /><code title={run.head_sha}>{run.head_sha.slice(0, 7)}</code><span>· {run.head_sha === pr.head_sha ? "Current commit" : "Earlier commit"}</span></span>
        </div>
      </div>
      <section aria-label="GitHub publication" className="mt-6 space-y-3 border-t pt-4 text-xs">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <div className="flex flex-wrap items-center gap-2"><span className="text-muted-foreground">GitHub comments</span>
            <Tooltip><TooltipTrigger asChild><span tabIndex={0} className="inline-flex cursor-pointer rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"><Status value={run.publication} label={feedback.publicationLabel} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>{feedback.publicationDetail || "Saved review results are waiting to be sent to GitHub."}</TooltipContent>
            </Tooltip>
          </div>
          {assessment && (assessment.incomplete > 0 || assessment.pending > 0) && <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">Assessment</span>
            <Tooltip><TooltipTrigger asChild><span tabIndex={0} className="inline-flex cursor-pointer rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring" role="status" aria-label={assessment.incomplete ? `Partial assessment: ${assessment.incomplete} candidates have incomplete assessments` : `${assessment.pending} candidates awaiting assessment`}><Status value={assessment.incomplete ? "partial" : "running"} label={assessment.incomplete ? "Partial" : "Pending"} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>{assessment.incomplete ? "Some candidates could not be fully assessed." : "Candidates are waiting for assessment."}</TooltipContent>
            </Tooltip>
          </div>}
          {typeof failedFixVerifications === "number" && Number.isSafeInteger(failedFixVerifications) && failedFixVerifications > 0 && <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Fix verification</span>
            <Tooltip><TooltipTrigger asChild><span tabIndex={0} className="inline-flex cursor-pointer rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring" role="status" aria-label={`Failed fix verifications: ${failedFixVerifications}`}><Status value="failed" label={`Failed ${failedFixVerifications}`} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>Automatic checks could not verify whether earlier findings were fixed. See Activity → Fix verification for the saved error.</TooltipContent>
            </Tooltip>
          </div>}
          {run.check_enabled && (checkValue || run.check_url || run.check_pending || run.check_error) && <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">GitHub check</span>
            {(run.check_url || checkValue) ? <Tooltip><TooltipTrigger asChild>
              {run.check_url ? <a className="inline-flex cursor-pointer rounded-full outline-offset-2 underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring" href={run.check_url} target="_blank" rel="noreferrer" aria-label={`View check on GitHub: ${label(checkValue || "check")}`}>
                <Status value={checkValue || "check"} icon={<ArrowUpRight aria-hidden="true" className="size-3" />} />
              </a> : <span tabIndex={0} className="inline-flex cursor-pointer rounded-full outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring"><Status value={checkValue || "check"} /></span>}
            </TooltipTrigger><TooltipContent className="max-w-xs" sideOffset={6}>{checkValue && Object.hasOwn(checkDescriptions, checkValue) ? checkDescriptions[checkValue] : "The saved GitHub check status is unavailable."}</TooltipContent></Tooltip>
              : <Status value={run.check_error ? "failed" : "pending"} label={run.check_error ? "Error" : "Pending"} />}
          </div>}
        </div>
        {feedback.publicationDetail && !["published", "no_new_comments"].includes(run.publication) && <p className="leading-relaxed text-muted-foreground">{feedback.publicationDetail}</p>}
        <ErrorNotice error={run.publication_error} />
        {run.check_enabled && <ErrorNotice error={run.check_error} />}
      </section>
    </header>
    <ErrorNotice error={action.error || error} />
    <RunFeedback run={run} repositoryId={repository.id} admin={user.role === "admin"} canWrite={can_write} detail />
    <ErrorNotice error={run.error} />
    {(active(run.status) || run.status === "blocked") && typeof run.progress?.selected === "number" && <p role="status" className="mb-4 text-sm text-muted-foreground">
      {String(run.progress.completed ?? 0)} of {String(run.progress.selected)} assigned files completed.
      {run.status === "blocked" && " Completed files are saved and reused when you resume."}
    </p>}
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList className="mb-4 grid w-full grid-cols-2 sm:flex sm:w-fit group-data-[orientation=horizontal]/tabs:h-auto">
        <TabsTrigger value="findings">Findings <span className="text-xs font-normal tabular-nums text-muted-foreground">{occurrences.length}</span></TabsTrigger>
        <TabsTrigger ref={resolvedTab} value="resolved">Resolved <span className="text-xs font-normal tabular-nums text-muted-foreground">{resolved_count}</span></TabsTrigger>
        <TabsTrigger ref={assessmentTab} value="assessment">Assessment {assessment && <span className="text-xs font-normal tabular-nums text-muted-foreground">{assessment.candidates}</span>}</TabsTrigger>
        <TabsTrigger value="coverage">Coverage</TabsTrigger>
        <TabsTrigger value="details" className="col-span-2 sm:col-span-1">Details</TabsTrigger>
      </TabsList>
      <TabsContent value="findings" className="min-w-0">
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">These are the findings saved at this commit. Open a finding to inspect its original evidence and current resolution separately.</p>
        <div className="mb-4"><FilterBar query={search} searchResetKey={filters.searchResetKey} searchLabel="Search run findings" searchPlaceholder="Search findings…" searchInputRef={searchInput} onSearch={filters.search}
          filters={[
            { key: "severity", label: "Severity", values: severities, choices: findingSeverities.map((value) => ({ value, name: label(value), count: occurrences.filter((item) => item.snapshot.severity === value).length })) },
            { key: "category", label: "Type", values: categories, choices: findingTypeChoices(categoryCounts), searchPlaceholder: "Search types…" },
          ]}
          onChange={filters.change}>
          <span role="status" className="sr-only">{filtered.length} of {occurrences.length} findings</span>
        </FilterBar></div>
        {!assessment && !active(run.status) && <p className="mb-4 text-xs text-muted-foreground">This run predates candidate assessment. Its findings have not been assessed by this policy.</p>}
        {filtered.length ? <section aria-label="Findings at this run" className="flex flex-col divide-y overflow-hidden rounded-lg border bg-card">
          {filtered.map((item) => <FindingCard key={item.finding_id} evidence={item.snapshot}
            state={<span className="text-xs text-muted-foreground">{item.inline_line ? "Inline comment" : "Summary comment"}</span>}
            onClick={(event) => { findingTrigger.current = event.currentTarget; showSheet("finding", item.finding_id); }} />)}
        </section> : <Empty title={occurrences.length ? "No findings match these filters" : active(run.status) ? "Review in progress" : "No findings recorded in this run"}>
          <p>{occurrences.length ? "Try another search, severity, or type." : active(run.status) ? "Saved findings appear here as the review progresses." : "Check coverage and assessment before treating this as a clean result."}</p>
          {occurrences.length ? <Button variant="outline" className="mt-4" onClick={() => { filters.clear(); searchInput.current?.focus(); }}>Clear filters</Button> : <Button variant="outline" className="mt-4" onClick={() => setTab("coverage")}>Inspect coverage</Button>}
        </Empty>}
      </TabsContent>
      <TabsContent value="resolved" className="min-w-0">
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">Findings automatically resolved by this run after fix verification and GitHub confirmation. Open a finding to inspect its current status and verification history.</p>
        <ResolvedList runId={run.id} running={active(run.status)} onOpen={(item, trigger) => { findingTrigger.current = trigger; showSheet("finding", item.id); }} />
      </TabsContent>
      <TabsContent value="coverage" className="min-w-0">
        <RunCoverage run={run} />
      </TabsContent>
      <TabsContent value="assessment" className="min-w-0">
        <section aria-label="Candidate assessment">
          <p className="text-xs leading-relaxed text-muted-foreground">Proposed findings are checked against evidence and earlier reviews before becoming new comments.</p>
          {assessment ? <>
            <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">{[
              ["validated findings", assessment.new_findings],
              ["repeated", assessment.repeated_observations],
              ["not supported", assessment.rejected_candidates],
              ["withheld", assessment.withheld_candidates],
              ["comments published", assessment.published_comments],
            ].map(([name, value]) => <div key={name} className="flex items-baseline gap-2">
              <dt className="text-xs capitalize text-muted-foreground">{name}</dt>
              <dd className="text-sm tabular-nums">{value}</dd>
            </div>)}</dl>
            {!!assessment.pending && <p role="status" className="mt-2 text-sm text-muted-foreground">{assessment.pending} candidates awaiting assessment.</p>}
            <AssessmentList runId={run.id} running={active(run.status)} onOpen={(item, trigger) => { findingTrigger.current = trigger; setCandidateSeed(item); showSheet("candidate", item.id); }} />
          </> : <div className="mt-4"><Empty title={active(run.status) ? "Assessment pending" : "Assessment was not recorded"}>{active(run.status) ? "Candidates appear after the initial review finishes." : "This run predates candidate assessment. Its findings have not been assessed by this policy."}</Empty></div>}
        </section>
      </TabsContent>
      <TabsContent value="details" className="min-w-0">
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">Review setup, execution details, revisions, and the configuration saved for this run.</p>
        <div className="grid min-w-0 gap-6">{[
          { title: "Review setup", rows: [
            ["Connection", String(connection?.name ?? "Not recorded")], ["Model", String(connection?.model ?? "Not recorded")], ["Engine", engine],
            ["Account owner", run.owner_id == null ? "Shared connection" : `Member #${run.owner_id}`],
          ] },
          { title: "Execution", rows: [
            ["Started by", run.actor_id == null ? words(run.trigger) : `Member #${run.actor_id}`],
            ["Started", run.started_at ? date(run.started_at) : "Not started"], ["Finished", run.finished_at ? date(run.finished_at) : "Not finished"],
          ] },
          { title: "Revisions", rows: [["Head commit", run.head_sha], ["Base commit", run.base_sha]] },
        ].map(({ title, rows }) => <section key={title} aria-label={title} className="min-w-0">
          <h2 className="mb-2 text-sm font-semibold">{title}</h2>
          <dl className="divide-y border-y text-xs">{rows.map(([key, value]) => <div key={key} className="grid grid-cols-[104px_minmax(0,1fr)] items-baseline gap-x-4 gap-y-1 py-2.5">
            <dt className="text-muted-foreground">{key}</dt><dd className={`min-w-0 break-words [overflow-wrap:anywhere] ${title === "Revisions" ? "font-mono" : ""}`}>{value}</dd>
          </div>)}</dl>
        </section>)}</div>
        <details className="group mt-6 pt-4 text-sm">
          <summary className="flex min-h-11 w-fit max-w-full cursor-pointer list-none items-center gap-2 font-medium outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden">Configuration used<ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary>
          <p className="mt-2 text-xs text-muted-foreground">The saved configuration for this run, independent of today's repository settings.</p><div className="mt-3"><Code language="json">{JSON.stringify(run.config, null, 2)}</Code></div>
        </details>
      </TabsContent>
    </Tabs>
    <Sheet open={!!finding || !!candidateId} onOpenChange={(open) => { if (!open) closeSheet(); }}>
      <SheetContent className="w-full gap-0 overflow-hidden sm:max-w-2xl" onCloseAutoFocus={(event) => { event.preventDefault(); (findingTrigger.current?.isConnected ? findingTrigger.current : tab === "assessment" ? assessmentTab.current : tab === "resolved" ? resolvedTab.current : searchInput.current)?.focus(); }}>
        <SheetHeader className="shrink-0 px-6 pb-4 pt-5">
          {finding && candidate && <Button autoFocus variant="link" size="sm" className="min-h-11 justify-start self-start px-0 text-xs sm:min-h-8" onClick={() => showSheet("finding", null)}><ArrowLeft aria-hidden="true" />Back to assessment</Button>}
          <SheetTitle>{finding ? "Finding details" : "Assessment details"}</SheetTitle>
          <SheetDescription className="sr-only">{finding ? "Inspect the evidence saved in this run and its current resolution separately." : "Inspect the candidate's assessment decision and saved supporting evidence."}</SheetDescription>
        </SheetHeader>
        {candidateId && <ErrorNotice error={candidateQuery.error} />}
        {candidateId && !finding && candidateQuery.isPending && <div className="px-6"><Loading /></div>}
        {finding ? <FindingPanel key={finding.finding_id} id={finding.finding_id} canWrite={can_write} snapshot={finding.snapshot} headSha={run.head_sha} /> : candidate && <AssessmentPanel key={candidate.id} candidate={candidate} headSha={run.head_sha} runId={run.id}
          canRetry={can_write && (run.owner_id === null || run.owner_id === user.id) && ["completed", "partial"].includes(run.status) && ["published", "no_new_comments"].includes(run.publication)}
          onOpenFinding={() => { if (candidate.finding_id) showSheet("finding", candidate.finding_id); }} />}
      </SheetContent>
    </Sheet>
  </>;
}
