import { AssessmentStatus as CandidateStatus } from "./components/assessment-status";
import { useEffect, useRef, useState, type RefObject } from "react";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { useWorkspaceNavigation } from "./hooks/use-workspace-navigation";
import { useRunPages, useRunResource } from "./hooks/use-run-resource";
import { useActivityRead } from "./hooks/use-activity-read";
import { PullSummaryActivity } from "./components/generation-activity";
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
  CircleCheck,
  CircleDot,
  CircleSlash,
  FileCode2,
  History,
  RotateCcw,
  UserRound,
  GitBranch,
  GitCommitHorizontal,
  GitPullRequest,
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
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Badge, badgeRadius } from "./components/ui/badge";
import { FilterBar } from "./components/filter-bar";
import { SearchSelect } from "./components/search-select";
import { label } from "./lib/inbox-filters";
import { RepositoryHeader, RepositorySettingsShortcut, RepositorySummaryCard, repositoryActivityLink } from "./components/repository-header";
import { RepositoryPulls } from "./components/repository-pulls";
import { getRepositoryReturn } from "./lib/navigation";
import { Textarea } from "./components/ui/textarea";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "./components/ui/tooltip";
import { PopoverContent } from "./components/ui/popover";
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
  TechnicalDetails,
  ExternalLink,
  Field,
  Loading,
  PageTitle,
  Markdown,
  Status,
  words,
} from "./components/common";
import { RepositorySettings } from "./settings";
import { assessmentFeedback, runFeedback } from "./lib/run-feedback";
import { coverageFiles, RunCoverage } from "./components/run-coverage";
import { RunActivity, runActivityEvents } from "./components/run-activity";
import { assessmentExceptions, RunStages } from "./components/run-stages";
import { assessmentOrderings, findingOrderings, normalizeOrdering, runFindingOrderings } from "./lib/ordering";
import { useReviewFilters } from "./hooks/use-review-filters";
import { useUrlTab } from "./hooks/use-url-tab";
import { Pagination } from "./components/pagination";
import { usePagedList } from "./hooks/use-paged-list";
import { FindingCard, FindingCategory } from "./components/finding-card";
import { candidateOutcomes, findingSeverities, runFindingOutcomes } from "./lib/review-filters";
import { runDuration } from "./lib/run-duration";
import { ExecutionDuration } from "./components/execution-duration";
import { PRCode } from "./components/pr-code";

function RunFeedback({ run, repositoryId, admin }: {
  run: Run; repositoryId: number; admin: boolean;
}) {
  const routeState = useLocation().state;
  const inboxReturn = getInboxReturn(routeState);
  const repositoryReturn = getRepositoryReturn(routeState);
  const feedback = runFeedback(run);
  if (!feedback.needsAttention) return null;
  return (
    <section aria-label="Review outcome" className="mb-6 space-y-3 rounded-lg border bg-card p-5 text-sm">
      <p role="status" className="font-medium break-words">{feedback.summary}</p>
      <p className="text-muted-foreground break-words">
        {feedback.destination === "repository" && !admin ? "Ask your administrator to adjust the repository settings. " : ""}
        {feedback.nextStep}
      </p>
      <div className="flex flex-wrap gap-3">
        <Link className="underline underline-offset-4" to={`/runs/${run.id}`} state={{ inbox: inboxReturn, repository: repositoryReturn }}>View run details</Link>
        {feedback.destination === "repository" && admin && (
          <Link className="underline underline-offset-4" to={`/repositories/${repositoryId}/settings?tab=limits`}>Adjust review settings</Link>
        )}
        {feedback.destination === "connections" && <Link className="underline underline-offset-4" to="/settings?tab=connections">Manage AI connections</Link>}
      </div>
    </section>
  );
}

export function Repositories({ admin, configure = false }: { admin: boolean; configure?: boolean }) {
  const { repositoryId } = useParams();
  const location = useLocation();
  const repos = useApi<Repository[]>("/repositories", true);
  const [params] = useSearchParams();
  const requested = repositoryId ?? (params.get("repo") || null);
  const denied = repos.error && [401, 403].includes(repos.error.status);
  const available = denied ? [] : repos.data;
  const selected = requested ? available?.find((repo) => String(repo.id) === requested) : available?.[0];
  const { sync: action } = useWorkspaceNavigation();
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
  if (repos.error && (!repos.data || denied)) return <div className="space-y-3"><ErrorNotice error={repos.error} /><Button variant="outline" onClick={() => repos.refetch()}>Retry repositories</Button></div>;
  const repositoryPath = `/repositories/${requested ?? selected?.id}`;
  const returnSearch = new URLSearchParams(params);
  if (configure) returnSearch.delete("tab");
  const returnPath = repositoryPath + (returnSearch.size ? `?${returnSearch}` : "");
  if (configure) {
    if (!admin || !selected) return <Navigate to={returnPath} state={location.state} replace />;
    return <>{repos.error && <div className="mb-4 space-y-3"><ErrorNotice error={repos.error} /><Button variant="outline" onClick={() => repos.refetch()}>Retry repositories</Button></div>}<RepositorySettings key={selected.id} repository={selected} /></>;
  }
  const syncPath = `/repositories/${selected?.id}/sync`;
  const syncHere = action.variables?.path === syncPath;
  const syncPending = syncHere && action.isPending;
  const latestReview = !repos.error ? selected?.latest_completed_review : null;
  const lastCheck = !repos.error ? selected?.last_pr_sync_check_at : null;
  const reviewInProgress = !repos.error && selected?.has_running_review;
  const missingActivity = repos.error ? "Unavailable" : "None recorded";
  return <>
    {selected ? <RepositoryHeader title="Pull requests" repository={selected}
      headingProps={{ id: "repository-pulls-heading", tabIndex: -1, ...{ "data-pull-list-focus": true } }}
      actions={<><Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={syncPending} onClick={() => action.mutate({ path: syncPath }, {
        onSuccess: () => toast.success("Sync queued", { description: "Pull requests will refresh when it finishes." }),
      })}>
        <RefreshCw aria-hidden="true" className={`size-4 ${syncPending ? "motion-safe:animate-spin" : ""}`} />{syncPending ? "Queuing sync…" : "Sync"}
      </Button>{admin && <RepositorySettingsShortcut repositoryId={selected.id} section="reviews" />}</>}
    >
      <RepositorySummaryCard label="Review policy" icon={GitPullRequest} regionLabel="Review configuration" badgeInHeading
        badge={<Badge variant={selected.enabled ? "default" : "outline"}>{selected.enabled ? "Reviews enabled" : "Paused"}</Badge>}
        details={selected.enabled ? [["File limit", selected.settings.max_files == null ? "No limit" : `${selected.settings.max_files} files per review`]] : undefined}
        note={selected.enabled ? undefined : admin ? "Choose an AI connection in Repository settings to enable them." : "Your administrator can enable them."}>
        {selected.enabled && (selected.settings.automatic ? "Automatic reviews" : "Manual reviews only")}
      </RepositorySummaryCard>
      <RepositorySummaryCard label="GitHub PR check" icon={RefreshCw} timestamp={lastCheck}
        details={!repos.error && selected.open_prs != null ? [["Open PRs", selected.open_prs.toLocaleString()]] : undefined}>
        {!lastCheck && missingActivity}
      </RepositorySummaryCard>
      <RepositorySummaryCard icon={History}
        label={reviewInProgress && !latestReview ? "Current review" : "Last completed review"}
        details={latestReview ? [["Reviewed commit", <code>{latestReview.head_sha.slice(0, 7)}</code>]] : undefined}
        note={latestReview && reviewInProgress ? "Review in progress" : undefined}
        action={latestReview && <Link className={repositoryActivityLink} to={`/runs/${latestReview.run_id}`}
          state={{ inbox: getInboxReturn(location.state), repository: returnPath }}
          aria-label={`View review for PR #${latestReview.number}`} title={`Reviewed commit ${latestReview.head_sha}`}>
          View review<ChevronRight aria-hidden="true" className="size-3.5" />
        </Link>}
      >
        {latestReview ? `PR #${latestReview.number}` : reviewInProgress ? "Review in progress" : missingActivity}
      </RepositorySummaryCard>
    </RepositoryHeader> : <PageTitle title="Repositories">Follow each pull request from first review to final resolution.</PageTitle>}
    <ErrorNotice error={repos.error || (syncHere && action.error)} />
    {repos.error && <Button variant="outline" className="mb-4" onClick={() => repos.refetch()}>Retry repositories</Button>}
    {requested && !selected && <Empty title="Repository unavailable">
      This repository was not found or you no longer have access. <Link className="underline" to="/repositories">View available repositories</Link>.
    </Empty>}
    {!requested && !available?.length && <Empty title="Connect your first repository">
      {admin ? <>Install your GitHub App, then connect the installation in <Link className="underline" to="/settings?tab=github">settings</Link>.</> : "Ask your administrator to connect a repository you can access on GitHub."}
    </Empty>}
    {selected && <RepositoryPulls key={selected.id} repositoryId={selected.id} admin={admin} />}
    {admin && <div className="mt-6 text-xs text-muted-foreground"><Link className="rounded-sm underline underline-offset-4 hover:text-foreground" to="/settings?tab=github">{available?.length ? "Manage repositories" : "Connect repository"}</Link></div>}
  </>;
}

function RunList({ prId }: { prId: string }) {
  const routeState = useLocation().state;
  const inboxReturn = getInboxReturn(routeState);
  const repositoryReturn = getRepositoryReturn(routeState);
  const { query, items: runs, pagination, denied } = usePagedList<Run>(`/pulls/${prId}/runs`);
  if (query.isPending) return <Loading />;
  if (denied) return <ErrorNotice error={query.error} />;
  if (!query.data || (query.error && query.data.total === 0)) return <div className="space-y-2"><ErrorNotice error={query.error} /><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}>Retry run history</Button></div>;
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
      <Pagination {...pagination} label="run history" />
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
  const query = useApi<Schema["PRDetail"]>(`/pulls/${id}`, (data) => data?.has_active_run || data?.pull_request.state === "open" ? 5000 : 30000);
  const action = useAction<Run>();
  const ignoreAction = useAction();
  const [restoring, setRestoring] = useState(false);
  const restoreTrigger = useRef<HTMLButtonElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const connections = useApi<Schema["ConnectionOut"][]>("/connections");
  const [connection, setConnection] = useState("");
  const [urlTab, setTab] = useUrlTab("tab", ["findings", "resolved", "dismissed", "history", "code", "impact"], "findings");
  const tab = urlTab === "impact" ? "code" : urlTab;
  const resolved = tab === "resolved";
  const dismissed = tab === "dismissed";
  const filters = useReviewFilters(resolved ? "resolved" : dismissed ? "dismissed" : "pull");
  const status = resolved ? "resolved" : dismissed ? "dismissed" : "open";
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
  const pages = Object.fromEntries([status].map((status) => {
    const page = Number(params.get(`${status}_page`) ?? 1);
    return [status, Number.isInteger(page) && page >= 1 && page <= INBOX_MAX_PAGES ? page : 1];
  }));
  function changePage(status: string, page: number) {
    const next = new URLSearchParams(params);
    if (page > 1) next.set(`${status}_page`, String(page)); else next.delete(`${status}_page`);
    setParams(next, { state: location.state });
  }
  const findingFilters = new URLSearchParams(filters.params);
  findingFilters.delete("status");
  findingFilters.set("status", status);
  const findingsQuery = useQuery<GroupedData<Schema["FindingsOut"]>, ApiError>({
    queryKey: ["pull-findings", id, status, filters.canonical, pages],
    queryFn: ({ signal }) => fetchGroupedPages<Schema["FindingsOut"]>(`/pulls/${id}/findings`, findingFilters, pages, signal),
    enabled: ["findings", "resolved", "dismissed"].includes(tab) && !!query.data && !(query.error && [401, 403].includes(query.error.status)),
    staleTime: 0,
    refetchInterval: 5000,
    retry: false,
    placeholderData: (previous, previousQuery) => previousQuery?.queryKey[1] === id && previousQuery?.queryKey[2] === status ? previous : undefined,
  });
  const savedCounts = useRef<{ id: typeof id; counts: Record<string, number> } | null>(null);
  if (findingsQuery.data) savedCounts.current = { id, counts: findingsQuery.data.status_counts };
  const counts = ["code", "history"].includes(tab) ? query.data?.finding_counts : savedCounts.current?.id === id ? savedCounts.current?.counts : undefined;
  useEffect(() => {
    if (!["findings", "resolved", "dismissed"].includes(tab) || !findingsQuery.data || findingsQuery.isPlaceholderData) return;
    const next = new URLSearchParams(params);
    for (const group of findingsQuery.data.groups) {
      if (findingsQuery.data.errors[group.group] || pages[group.group] === group.page) continue;
      if (group.page > 1) next.set(`${group.group}_page`, String(group.page)); else next.delete(`${group.group}_page`);
    }
    if (next.toString() !== params.toString()) setParams(next, { replace: true, state: location.state });
  }, [tab, findingsQuery.data, findingsQuery.isPlaceholderData, pages, params, setParams, location.state]);
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  if (findingsQuery.error && [401, 403].includes(findingsQuery.error.status)) return <ErrorNotice error={findingsQuery.error} />;
  const { pull_request: pr, repository, run_count, has_active_run: running, can_write } = query.data;
  const findings = findingsQuery.data;
  const total = findings?.registered_total ?? 0;
  const latest = pr.latest_run;
  const term = search.trim().toLowerCase();
  const count = (state: string) => counts?.[state] ?? 0;
  const scopeTotal = count(status);
  const sections = findings?.groups.filter((group) => group.total > 0) ?? [];
  const noOpenFindings = status === "open" && total > 0 && !count("open") && !severities.length && !categories.length && !term;
  const needsSharedConnection = !connection && repository.connection_requires_setup;
  const reviewUnavailable = !can_write ? "You have read-only access to this pull request."
    : pr.ignored ? "Reviews are stopped while this pull request is ignored. Restore it to run reviews."
    : running || pr.state !== "open" ? null
    : !repository.enabled ? "Reviews are paused. Enable them in repository configuration."
    : needsSharedConnection ? "The repository's connection is retired. Choose a shared API connection for this review, or update repository configuration."
    : pr.fork ? "Fork reviews require a manual start." : null;
  function showAllFindings() {
    filters.clear();
    searchInput.current?.focus();
  }
  return <>
    {pr.ignored && <section aria-label="Ignored pull request" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/50 p-4 text-sm">
      <div><p className="font-medium">This pull request is ignored</p><p className="mt-1 text-xs text-muted-foreground">Hidden from the default lists for everyone. Reviews stay stopped until restored.</p></div>
      {can_write && <Button ref={restoreTrigger} variant="outline" disabled={ignoreAction.isPending} onClick={() => { ignoreAction.reset(); setRestoring(true); }}>Restore pull request</Button>}
    </section>}
    <Dialog open={restoring} onOpenChange={(value) => { if (!ignoreAction.isPending) setRestoring(value); }}>
      <DialogContent showCloseButton={!ignoreAction.isPending} className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={(event) => {
        event.preventDefault();
        (ignoreAction.isSuccess ? heading.current : restoreTrigger.current)?.focus();
      }}>
        <DialogHeader>
          <DialogTitle>Restore pull request #{pr.number}?</DialogTitle>
          <DialogDescription>This makes the pull request visible for everyone and allows reviews again. Cancelled reviews will not resume.</DialogDescription>
        </DialogHeader>
        <ErrorNotice error={ignoreAction.error} />
        <DialogFooter>
          <Button variant="outline" disabled={ignoreAction.isPending} onClick={() => setRestoring(false)}>Cancel</Button>
          <Button disabled={ignoreAction.isPending} onClick={() => ignoreAction.mutate({ path: `/pulls/${id}/ignore`, method: "PUT", body: { ignored: false } }, {
            onSuccess: () => setRestoring(false),
          })}>{ignoreAction.isPending ? "Restoring…" : "Restore pull request"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
    <div className="mb-6 min-w-0">
      <PageTitle className="mb-0" headingProps={{ ref: heading, tabIndex: -1 }} title={<><span className="font-normal tabular-nums text-muted-foreground">#{pr.number}</span> {pr.title}</>}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <Status value={pr.state} />
          <span className="inline-flex min-w-0 items-center gap-1.5"><UserRound aria-hidden="true" className="size-3.5 shrink-0" /><span className="break-words [overflow-wrap:anywhere]">@{pr.author}</span></span>
          {pr.draft && <span>Draft</span>}
          <span className="inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-1.5 gap-y-1"><GitBranch aria-hidden="true" className="size-3.5 shrink-0" /><code className="min-w-0 break-words [overflow-wrap:anywhere]">{pr.head_ref}</code><span>·</span><code title={pr.head_sha}>{pr.head_sha.slice(0, 7)}</code></span>
          <a href={pr.url} target="_blank" rel="noreferrer" className="inline-flex min-h-6 items-center gap-1 rounded-sm underline-offset-4 hover:text-foreground hover:underline">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
        </div>
      </PageTitle>
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
          {can_write && pr.state === "open" && <div className="flex w-full flex-wrap gap-2 sm:w-auto">
            <SearchSelect aria-label="Review connection" aria-describedby={reviewUnavailable ? "review-unavailable" : undefined} disabled={action.isPending || running || pr.ignored || pr.state !== "open" || !repository.enabled} value={connection} onChange={setConnection} className="min-w-0 flex-1 basis-48 sm:w-56 sm:flex-none" placeholder="Repository default"
              options={[{ value: "", label: "Repository default" }, ...(connections.data ?? []).filter((item) => item.kind === "api").map((item) => ({ value: item.id, label: item.name }))]} />
            <Button aria-describedby={reviewUnavailable ? "review-unavailable" : undefined} disabled={action.isPending || running || pr.ignored || pr.state !== "open" || !repository.enabled || needsSharedConnection} onClick={async () => {
              const run = await action.mutateAsync({ path: `/pulls/${id}/runs`, body: connection ? { connection_id: connection } : {} }).catch(() => null);
              if (run) navigate(`/runs/${run.id}`, { state: { inbox: inboxReturn, repository: repositoryReturn } });
            }}><Play aria-hidden="true" className="size-4" />{running ? "Review in progress" : action.isPending ? "Starting review…" : "Run review"}</Button>
          </div>}
        </div>
        {reviewUnavailable && <p id="review-unavailable" className="mt-3 text-xs text-muted-foreground">{reviewUnavailable}</p>}
      </section>
    </div>
    <ErrorNotice error={action.error || query.error} />
    {latest && <RunFeedback run={latest} repositoryId={repository.id} admin={admin} />}
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList variant="page" aria-label="Pull request views" className="mb-4">
        <TabsTrigger value="findings" aria-live="polite" aria-atomic="true">Findings <span className="ml-1 text-xs tabular-nums text-muted-foreground">{counts ? count("open") : <span className="sr-only">{findingsQuery.error ? "unavailable" : "loading…"}</span>}</span></TabsTrigger>
        <TabsTrigger value="resolved">Resolved <span className="ml-1 text-xs tabular-nums text-muted-foreground">{counts ? count("resolved") : "…"}</span></TabsTrigger>
        <TabsTrigger value="dismissed">Dismissed <span className="ml-1 text-xs tabular-nums text-muted-foreground">{counts ? count("dismissed") : "…"}</span></TabsTrigger>
        <TabsTrigger value="history">Run history <span className="ml-1 text-xs tabular-nums text-muted-foreground">{run_count}</span></TabsTrigger>
        <TabsTrigger value="code">Code</TabsTrigger>
      </TabsList>
      <TabsContent value="code" className="min-w-0">{tab === "code" && <PRCode key={`${pr.id}:${pr.base_sha}:${pr.head_sha}`} prId={pr.id} repositoryId={repository.id} admin={admin} canWrite={can_write} renderFinding={(item) => <FindingPanel key={item.occurrence_id} id={item.finding_id} canWrite={can_write} snapshot={item.snapshot} headSha={item.source_sha ?? undefined} />} />}</TabsContent>
      {["findings", "resolved", "dismissed"].map((value) => <TabsContent key={value} value={value} className="min-w-0">
        <div className="mb-4">
          <FilterBar query={search} searchResetKey={filters.searchResetKey} ordering={{ value: filters.params.get("ordering"), defaultValue: "-severity", options: findingOrderings, onChange: (value) => filters.change("ordering", [value]) }} searchLabel={resolved ? "Search resolved findings" : dismissed ? "Search dismissed findings" : "Search findings"} searchPlaceholder="Search findings…" searchInputRef={searchInput} onSearch={filters.search}
            filters={[
              { key: "severity", label: "Severity", values: severities, choices: findingSeverities.map((value) => ({ value, name: label(value) })) },
              { key: "category", label: "Type", values: categories, choices: findingTypeChoices(findings?.category_counts ?? {}), searchPlaceholder: "Search types…" },
            ]}
            onChange={filters.change} />
        </div>
        {findingsQuery.error && <div className="mb-4 space-y-2"><ErrorNotice error={findingsQuery.error} /><Button variant="outline" size="sm" onClick={() => findingsQuery.refetch()}>Retry findings</Button></div>}
        {findingsQuery.isPending && <Loading />}
        {findings && (findings.total > 0 ? <section aria-label={resolved ? "Resolved pull request findings" : "Findings"} className="space-y-6" aria-busy={findingsQuery.isPlaceholderData}>
          {sections.map(({ group: status, items, page, total: groupTotal }) => {
            const error = findings.errors[status];
            return <section key={status} aria-label={`${label(status)} findings`}>
              <div className="flex flex-col divide-y overflow-hidden rounded-lg border bg-card">
                {items.map((item) => <FindingCard key={item.id} evidence={item}
                  onClick={(event) => { findingTrigger.current = event.currentTarget; setFinding(item.id); }}
                  state={<span className="flex flex-col items-start gap-1.5">
                    <Status value={item.status} icon={item.status === "resolved" ? <CircleCheck className="size-3.5" aria-hidden="true" /> : undefined} />
                    {item.desired_status && <span className="text-xs text-muted-foreground">{item.sync_error ? "Sync needs attention" : "Sync pending"}</span>}
                  </span>} />)}
                <Pagination page={page} pageSize={findings.page_size} total={groupTotal} loading={findingsQuery.isFetching && (findingsQuery.isPlaceholderData || !!error)}
                  error={error ? new Error(error) : null} onRetry={findingsQuery.refetch} label={`${status} findings`}
                  onPageChange={(next) => changePage(status, next)} />
              </div>
            </section>;
          })}
        </section> : !findingsQuery.isPlaceholderData && <Empty title={noOpenFindings ? "No open findings" : scopeTotal ? "No findings match these filters" : resolved ? "No resolved findings" : dismissed ? "No dismissed findings" : run_count ? "No findings recorded" : "No review findings yet"}>
          <p>{noOpenFindings ? "Earlier findings are resolved or dismissed. Their saved evidence and history are preserved." : scopeTotal ? "Try another search, severity, or type." : resolved ? "Findings appear here after they are resolved. Findings missing from a later review remain open." : dismissed ? "Dismissed findings retain their evidence and dismissal reason." : run_count ? "Check run history for completion and coverage. An incomplete review is not a clean result." : "Findings will appear here after a review. Each finding keeps its evidence and resolution history."}</p>
          {noOpenFindings && count("resolved") > 0 ? <Button variant="outline" className="mt-4" onClick={() => setTab("resolved")}>View resolved findings</Button> : scopeTotal > 0 && <Button variant="outline" className="mt-4" onClick={showAllFindings}>Clear filters</Button>}
        </Empty>)}
        <p className="mt-5 text-xs leading-relaxed text-muted-foreground">Resolving or reopening a GitHub review thread updates its finding here. A finding missing from a later run stays open until someone resolves it.</p>
      </TabsContent>)}
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
          {snapshot && <TabsTrigger value="snapshot">Saved evidence</TabsTrigger>}
          <TabsTrigger value="evidence">{snapshot ? "Current finding" : "Evidence"}</TabsTrigger>
          <TabsTrigger ref={activityTab} value="activity">Activity <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data ? activities.length : "…"}</span></TabsTrigger>
        </TabsList></div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" data-slot="finding-scroll">
          {snapshot && <TabsContent value="snapshot" className="space-y-6"><p className="text-xs leading-relaxed text-muted-foreground">Evidence recorded by this review. Current status is shown above. {headSha ? <>Saved at <span className="font-mono">{headSha.slice(0, 7)}</span>.</> : "Source revision was not recorded."}</p><FindingEvidence evidence={snapshot} /></TabsContent>}
          <TabsContent value="evidence" className="space-y-6">{snapshot && <p className="text-xs leading-relaxed text-muted-foreground">Current finding details. Saved evidence stays unchanged when this finding changes.</p>}{f ? <FindingEvidence evidence={f} /> : query.isPending ? <Loading /> : <p className="text-sm text-muted-foreground">Current evidence could not be loaded.</p>}</TabsContent>
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
                <div className="mt-2"><Code path={evidence.path} startLine={evidence.start_line}>{evidence.quote}</Code></div>
              </details>
            ))}
            <p className="text-sm text-muted-foreground">
              Automatic resolution: {words(item.resolution)}
            </p>
            {item.error && <InferenceError error={item.error} stage="verification" />}
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
    <section className="space-y-3"><h3 className="text-sm font-semibold">What we found</h3><Markdown>{String(evidence.content ?? "")}</Markdown></section>
    <CodeComparison original={String(evidence.existing_code ?? "")} suggested={String(evidence.suggestion ?? "")} path={String(evidence.path ?? "")} startLine={Number(evidence.start_line)} />
    <p className="flex items-center gap-2 text-xs leading-relaxed text-muted-foreground"><History className="size-3.5 shrink-0" aria-hidden="true" />Each review keeps its original evidence.</p>
  </>;
}

function findingActionLabel(finding: Schema["FindingOut"]) {
  if (finding.sync_error) return "Retry sync";
  if (finding.status === "open") return "Resolve finding";
  return "Reopen finding";
}

function RunFindings({ runId, state, running, revision, tick, searchInput, onOpen }: {
  runId: string; state: "open" | "resolved"; running: boolean; revision: string; tick: number;
  searchInput: RefObject<HTMLInputElement | null>; onOpen: (finding: Schema["OccurrenceOut"], trigger: HTMLButtonElement) => void;
}) {
  const resolved = state === "resolved";
  const filters = useReviewFilters(resolved ? "resolved" : "run");
  const { params, canonical } = filters;
  const query = useRunPages<Schema["RunFindingPage"]>(
    `/runs/${runId}/findings?status=${state}&${canonical}`, revision, tick, `${state}_page`,
  );
  if (query.error && [401, 403, 404].includes(query.error.status)) return <ErrorNotice error={query.error} />;
  const data = query.data;
  const findings = data?.items ?? [];
  return <div className="space-y-4">
    <FilterBar query={params.get("q") ?? ""} searchResetKey={filters.searchResetKey} ordering={{ value: params.get("ordering"), defaultValue: "-severity", options: resolved ? findingOrderings : runFindingOrderings, onChange: (value) => filters.change("ordering", [value]) }} searchLabel={resolved ? "Search resolved findings" : "Search run findings"} searchPlaceholder="Search findings…" searchInputRef={searchInput} onSearch={filters.search}
      filters={[
        ...(!resolved ? [{ key: "outcome" as const, label: "Result", values: params.getAll("outcome"), choices: Object.entries(runFindingOutcomes).map(([value, name]) => ({ value,
          name: value === "repeated" ? "Repeated findings" : value === "recorded" ? "Unclassified findings" : `${name}s`, count: data?.outcome_counts[value] ?? 0 })) }] : []),
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: findingSeverities.map((value) => ({ value, name: label(value), count: data?.severity_counts[value] })) },
        { key: "category", label: "Type", values: params.getAll("category"), choices: findingTypeChoices(data?.category_counts ?? {}), searchPlaceholder: "Search types…" },
      ]} onChange={filters.change}>
      {data && <span role="status" className="sr-only">{data.total} of {data.registered_total} {resolved ? "resolved findings" : "findings"}</span>}
    </FilterBar>
    <section aria-label={resolved ? "Findings resolved by this run" : "Findings at this run"} className={findings.length ? "flex flex-col divide-y overflow-hidden rounded-lg border bg-card" : undefined}>
      {!data && query.isPending ? <Loading /> : findings.length ? findings.map((item) => <FindingCard key={item.finding_id} evidence={item.snapshot}
        state={<Status value={resolved ? "resolved" : "recorded"} label={resolved ? "Resolved" : item.is_new === true ? "New finding" : item.is_new === false ? "Repeated finding" : "Recorded finding"} />}
        onClick={(event) => onOpen(item, event.currentTarget)} />) : !query.error && <Empty title={data?.registered_total ? resolved ? "No resolved findings match these filters" : "No findings match these filters" : !resolved && running ? "Review in progress" : resolved ? "No findings resolved by this run" : "No findings recorded in this run"}>
        <p>{data?.registered_total ? "Try another search, severity, or type." : resolved ? "Fixes appear here after verification and GitHub synchronization complete. Findings absent from a review are not automatically resolved." : running ? "Saved findings appear here as the review progresses." : "Check coverage and assessment before treating this as a clean result. Saved evidence and finding history are preserved."}</p>
        {!!data?.registered_total && <Button variant="outline" className="mt-4" onClick={() => { filters.clear(); searchInput.current?.focus(); }}>Clear filters</Button>}
      </Empty>}
      <Pagination {...query.pagination} label={`${state} findings`} />
    </section>
  </div>;
}

function AssessmentList({ runId, running, revision, tick, onOpen }: {
  runId: string; running: boolean; revision: number; tick: number; onOpen: (candidate: Schema["CandidateOut"], trigger: HTMLButtonElement) => void;
}) {
  const filters = useReviewFilters("assessment");
  const { params, canonical } = filters;
  const searchInput = useRef<HTMLInputElement>(null);
  const query = useRunPages<Schema["CandidatePage"]>(
    `/runs/${runId}/candidates?${canonical}`, revision, tick,
  );
  if (query.error && [401, 403, 404].includes(query.error.status)) return <ErrorNotice error={query.error} />;
  const first = query.data;
  const last = query.data;
  const candidates = query.data?.items ?? [];
  return <div className="mt-6 space-y-4">
    <FilterBar query={params.get("q") ?? ""} searchResetKey={filters.searchResetKey} ordering={{ value: filters.params.get("ordering"), defaultValue: "-severity", options: assessmentOrderings, onChange: (value) => filters.change("ordering", [value]) }} searchLabel="Search assessments" searchPlaceholder="Search assessments…" searchInputRef={searchInput} onSearch={filters.search}
      filters={[
        { key: "outcome", label: "Result", values: params.getAll("outcome"), choices: candidateOutcomes.map((choice) => ({ ...choice, count: first?.outcome_counts[choice.value] })) },
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: findingSeverities.map((value) => ({ value, name: label(value), count: first?.severity_counts[value] })) },
        { key: "category", label: "Type", values: params.getAll("category"), choices: findingTypeChoices(first?.category_counts ?? {}), searchPlaceholder: "Search types…" },
      ]} onChange={filters.change}>
      {last && <span role="status" className="sr-only">{last.total} of {last.registered_total} assessments</span>}
    </FilterBar>
    <div className={candidates.length ? "flex flex-col divide-y overflow-hidden rounded-lg border bg-card" : undefined}>
      {!last && query.isPending ? <Loading /> : candidates.length ? candidates.map((item) => <FindingCard key={item.id} evidence={item.snapshot}
        state={<CandidateStatus candidate={item} inCard />}
        onClick={(event) => onOpen(item, event.currentTarget)} />) : !query.error && <Empty title={first?.registered_total ? "No assessments match these filters" : "No candidates to assess"}>
        <p>{first?.registered_total ? "Try another search, result, severity, or type." : running ? "Candidates appear after the initial review finishes." : "No candidates were produced for assessment."}</p>
        {!!first?.registered_total && <Button variant="outline" className="mt-4" onClick={() => { filters.clear(); searchInput.current?.focus(); }}>View all assessments</Button>}
      </Empty>}
      <Pagination {...query.pagination} label="assessments" />
    </div>
  </div>;
}

function InferenceError({ error, withheld = false, stage = "assessment" }: {
  error: string; withheld?: boolean; stage?: "assessment" | "verification" | "review";
}) {
  const feedback = assessmentFeedback(error, stage);
  const name = stage[0].toUpperCase() + stage.slice(1);
  return <div className="space-y-3">
    <ErrorNotice title={feedback.title} error={`${feedback.message}${withheld ? " This candidate was withheld from publication." : ""}`} />
    {feedback.citation && <p className="break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">{feedback.citation}</p>}
    <TechnicalDetails error={error} name={name} />
  </div>;
}


function AssessmentPanel({ candidate, headSha, runId, canRetry, waitingForVerification, merged, onOpenFinding }: {
  candidate: Schema["CandidateOut"]; headSha: string; runId: string; canRetry: boolean; waitingForVerification: boolean; merged: boolean; onOpenFinding: () => void;
}) {
  const [tab, setTab] = useUrlTab("assessment_tab", ["assessment", "evidence"], "assessment");
  const retry = useAction<Schema["CandidateOut"]>();
  const { snapshot, result } = candidate;
  const path = String(snapshot.path ?? "Path not recorded");
  const slash = path.lastIndexOf("/");
  const heading = candidate.finding_id ? candidate.is_new ? "Why it was validated" : "Why it is repeated" : candidate.assessment_only && result?.verdict === "confirmed" ? "Why it was confirmed at the original commit" : result?.verdict === "rejected" ? "Why it was not supported" : "Why it was withheld";
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
          {candidate.error && <InferenceError error={candidate.error} withheld={!candidate.finding_id} />}
          <ErrorNotice error={retry.error} />
          {candidate.assessment_only && <p className="text-sm text-muted-foreground">This assessment checks the original commit after merge. Findings, comments, and resolution states are unchanged.</p>}
          {result ? <>
            <section className="space-y-3"><h3 className="text-sm font-semibold">{heading}</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.explanation}</p></section>
            <section className="space-y-3"><h3 className="text-sm font-semibold">Earlier reviews</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.history_explanation || "No history explanation was recorded."}</p></section>
            <section className="space-y-3"><h3 className="text-sm font-semibold">Changed conditions</h3><p className="max-w-[75ch] whitespace-pre-wrap break-words text-sm leading-relaxed [overflow-wrap:anywhere]">{result.changed_conditions || "No changed conditions were recorded."}</p></section>
          </> : !candidate.error && <p role={active(candidate.status) ? "status" : undefined} className="text-sm text-muted-foreground">{candidate.progress_phase === "validating" ? "Response received; validating against the current pull request and finding history." : active(candidate.status) ? waitingForVerification ? "Waiting for fix verification to finish updating finding history." : "Assessment is waiting for the review worker." : "No assessment result was recorded."}</p>}
          {!!candidate.attempts?.length && <details className="space-y-4 text-sm">
            <summary className="cursor-pointer font-medium">Previous attempts ({candidate.attempts.length})</summary>
            {candidate.attempts.map((attempt, index) => <section key={index} className="space-y-3 border-t pt-4">
              <h3 className="text-sm font-semibold">Attempt {index + 1}{attempt.finished_at && <span className="ml-2 text-xs font-normal text-muted-foreground">{date(attempt.finished_at)}</span>}</h3>
              {attempt.error && <InferenceError error={attempt.error} withheld />}
              {typeof attempt.usage.tokens === "number" && <p className="text-xs text-muted-foreground">{attempt.usage.tokens.toLocaleString()} tokens used or reserved.</p>}
              {typeof attempt.usage.context_bytes === "number" && <p className="text-xs text-muted-foreground">{attempt.usage.context_bytes.toLocaleString()} context bytes used.</p>}
            </section>)}
          </details>}
        </TabsContent>
        <TabsContent value="evidence" className="space-y-6">
          <section className="space-y-3"><h3 className="text-sm font-semibold">Supporting evidence</h3><p className="text-xs text-muted-foreground">Saved from the reviewed commit <span className="font-mono">{headSha.slice(0, 7)}</span>.</p></section>
          {result?.evidence.length ? result.evidence.map((evidence, index) => <section key={`${evidence.revision}-${evidence.path}-${evidence.start_line}-${index}`} className="space-y-3">
            <p className="break-words font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{evidence.revision === "original" ? "Original" : "Current"} evidence: {evidence.path}:{evidence.start_line}–{evidence.end_line}</p>
            <Code path={evidence.path} startLine={evidence.start_line}>{evidence.quote}</Code>
          </section>) : <p className="text-sm text-muted-foreground">{active(candidate.status) ? "Evidence will appear when assessment completes." : "No supporting evidence was recorded."}</p>}
        </TabsContent>
        <details className="mt-6 border-t pt-4 text-sm"><summary className="cursor-pointer font-medium">Original proposal</summary><div className="mt-3 max-w-[75ch]"><Markdown>{String(snapshot.content ?? "No proposal text was recorded.")}</Markdown></div></details>
      </div>
    </Tabs>
    <SheetFooter className="shrink-0 border-t px-6 py-4">
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs leading-relaxed text-muted-foreground">{canRetry && candidate.retryable ? merged ? "Retry checks the original commit using the same connection and AI quota. No findings or comments will be created." : "Retry uses the same connection and AI quota." : candidate.finding_id ? "Finding resolution is tracked separately." : "Not linked to a finding."}</p>
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
  const [params] = useSearchParams();
  const ordering = normalizeOrdering(params.get("ordering"), runFindingOrderings, "-severity");
  const path = `/runs/${id}${ordering === "-severity" ? "" : `?ordering=${ordering}`}`;
  const query = useQuery<Schema["RunDetail"], ApiError>({
    queryKey: ["run-detail", id, ordering], queryFn: ({ signal }) => api(path, "GET", undefined, undefined, signal),
    refetchInterval: 5000, staleTime: 0, retry: false,
    placeholderData: (previous) => previous?.run.id === id ? previous : undefined,
  });
  const run = query.data?.run;
  const finishedAt = run?.activity_finished_at === undefined ? run?.finished_at : run.activity_finished_at;
  const openingReview = query.isSuccess && !query.isPlaceholderData && !params.get("summaryJob");
  useActivityRead("review", run?.id, finishedAt, openingReview ? query.dataUpdatedAt : 0);
  if (query.isPending) return <Loading />;
  if (!query.data || (query.error && [401, 403].includes(query.error.status))) return <ErrorNotice error={query.error} />;
  return <RunWorkspace key={id} detail={query.data} user={user} error={query.error} tick={query.dataUpdatedAt} />;
}

function tokenUsage(tokens: number | null | undefined, status: string) {
  if (tokens != null) return `${tokens.toLocaleString()} tokens`;
  return ["pending", "queued", "running", "waiting", "blocked"].includes(status)
    ? "Token usage pending" : "Token usage unavailable";
}

function RunMetrics({ run, tokens }: { run: Run; tokens?: number | null }) {
  const coverage = coverageFiles(run);
  const knownCount = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
  const count = (value: unknown) => knownCount(value) ? value.toLocaleString() : "—";
  const selected = coverage.hasScope ? coverage.counts.selected : run.progress.selected;
  const completed = coverage.hasScope ? coverage.items.filter((file) => file.outcome === "completed").length : run.progress.completed;
  const reused = coverage.hasScope ? coverage.items.filter((file) => file.outcome === "reused").length : run.progress.reused;
  const failed = coverage.hasScope ? coverage.items.filter((file) => file.outcome === "failed").length : run.progress.failed;
  const hasMetrics = [selected, completed, reused, failed, tokens].some(knownCount);
  const awaitingResults = active(run.status) || run.status === "blocked";
  return <p aria-label="Run statistics" className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
    <span>Duration: <ExecutionDuration run={run} /></span>
    <span>{hasMetrics
      ? <>{count(reused)} reused · {count(failed)} failed · {tokenUsage(tokens, run.status)}</>
      : awaitingResults ? "Awaiting review results" : "Review statistics unavailable"}</span>
  </p>;
}

const checkDescriptions: Record<string, string> = {
  queued: "Waiting for the review to start.",
  in_progress: "Review, publication, or fix synchronization is still in progress.",
  completed: "The GitHub check has finished.",
  success: "Findings are informational, not approval to merge.",
  neutral: "The review completed, but assessment or fix verification is incomplete.",
  failure: "The review failed, coverage is incomplete, or comments could not be fully published.",
  cancelled: "The review was cancelled or no longer applies to this pull request.",
  skipped: "The review was skipped. Inspect coverage for the reason.",
  timed_out: "The review exceeded its time limit.",
  action_required: "The review or GitHub synchronization needs attention.",
};

const statusTriggerClass = `inline-flex cursor-pointer ${badgeRadius} outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring`;

function RunWorkspace({ detail, user, error, tick }: { detail: Schema["RunDetail"]; user: Schema["UserOut"]; error: ApiError | null; tick: number }) {
  const { run, pull_request: pr, occurrences, finding_state_counts = {}, findings_revision = "", assessment_revision = 0, assessment, can_write } = detail;
  const action = useAction();
  const location = useLocation();
  const returnState = { inbox: getInboxReturn(location.state), repository: getRepositoryReturn(location.state) };
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useUrlTab("tab", ["findings", "resolved", "assessment", "coverage", "details", "activity"], "findings");
  const activityEvents = runActivityEvents(run);
  const summaryJob = params.get("summaryJob");
  const findingId = params.get("finding");
  const [findingSeed, setFindingSeed] = useState<{ item: Schema["OccurrenceOut"]; tab: string } | null>(null);
  const savedFinding = findingSeed?.tab === tab && findingSeed.item.finding_id === findingId ? findingSeed.item
    : tab !== "resolved" ? occurrences.find((item) => item.finding_id === findingId) : undefined;
  const findingQuery = useApi<Schema["OccurrenceOut"]>(
    `/runs/${run.id}/findings/${encodeURIComponent(findingId ?? "")}?status=${tab === "resolved" ? "resolved" : "open"}`,
    false, !!findingId && !savedFinding,
  );
  const finding = findingId ? savedFinding ?? findingQuery.data : undefined;
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
  const candidateQuery = useRunResource<Schema["CandidateDetail"]>(
    `/runs/${run.id}/candidates/${encodeURIComponent(candidateId ?? "")}`, assessment_revision, tick,
    { enabled: !!candidateId, placeholder: candidateSeed?.id === candidateId ? { ...candidateSeed, revision: assessment_revision } : undefined },
  );
  const candidateDenied = candidateId && candidateQuery.error && [401, 403, 404].includes(candidateQuery.error.status);
  const candidate = candidateDenied ? undefined : candidateQuery.data;
  const searchInput = useRef<HTMLInputElement>(null);
  const findingTrigger = useRef<HTMLButtonElement>(null);
  const resolvedTab = useRef<HTMLButtonElement>(null);
  const assessmentTab = useRef<HTMLButtonElement>(null);
  const connection = run.config.connection as Record<string, unknown> | undefined;
  const checkValue = run.check_conclusion || run.check_status;
  const feedback = runFeedback(run);
  const savedAssessmentStatus = run.stages?.assessment?.status;
  const assessmentStatus = run.status === "running" && (assessment?.pending ?? 0) > 0
    && (!savedAssessmentStatus || savedAssessmentStatus === "partial")
    ? "running" : savedAssessmentStatus ?? (assessment?.incomplete ? "partial" : "pending");
  const summaryStage = run.summary?.status === "not_applicable" ? undefined
    : run.config.pipeline_version === 2 ? run.stages?.summary ?? run.summary
    : run.summary ?? run.stages?.summary;
  const summaryDiagnostics = [summaryStage?.error, run.summary?.diagram_warning, pr.summary_error]
    .filter(Boolean).join("\n\n");
  const currentSummary = pr.latest_run?.id === run.id && summaryStage?.status === "completed";
  // A sync job can reconcile an already published comment without changing it.
  const summaryPublicationPending = currentSummary && !pr.summary_url && pr.summary_pending;
  const summarySyncFailed = currentSummary && Boolean(pr.summary_error);
  const stageDetails = <PopoverContent align="start" aria-label="Review stage details" className="max-h-[var(--radix-popover-content-available-height)] w-80 max-w-[calc(100vw-2rem)] space-y-4 overflow-y-auto text-xs">
    {summaryStage && <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        {pr.summary_url ? <a className="inline-flex items-center gap-1 rounded-sm font-medium no-underline outline-offset-2 hover:text-primary focus-visible:outline-2 focus-visible:outline-ring" href={pr.summary_url} target="_blank" rel="noreferrer">PR summary<ArrowUpRight aria-hidden="true" className="size-3" /></a> : <span className="font-medium">PR summary</span>}
        <Status value={summaryStage.status} />
      </div>
      <p className="text-muted-foreground">{tokenUsage(summaryStage.tokens, summaryStage.status)}</p>
      {summaryStage.status === "waiting" && <p className="text-muted-foreground">Waiting to generate the summary.</p>}
      {summaryDiagnostics && <TechnicalDetails error={summaryDiagnostics} name="Summary" />}
      {run.engine !== "mortis" && summaryStage.status === "failed" && <p className="text-muted-foreground">Generating a new Mortis summary uses the repository's current shared API connection and limits.</p>}
      {can_write && summaryStage.status === "failed" && !active(run.status) && run.status !== "blocked" && run.head_sha === pr.head_sha && pr.latest_run?.id === run.id && <Button size="sm" variant="outline" disabled={action.isPending || pr.ignored} onClick={() => action.mutate({ path: `/runs/${run.id}/summary/retry` })}>{run.engine === "mortis" ? "Retry summary generation" : "Generate summary with Mortis"}</Button>}
      {can_write && pr.summary_error && <Button size="sm" variant="outline" disabled={action.isPending || pr.ignored} onClick={() => action.mutate({ path: `/pulls/${pr.id}/summary` })}>Retry GitHub sync</Button>}
    </div>}
    {([["analysis", "Analysis"], ["assessment", "Assessment"], ["verification", "Fix verification"]] as const).map(([key, name]) => {
      const stage = key === "analysis" ? run.stages?.analysis ?? run.stages?.ocr : run.stages?.[key];
      if (!stage) return null;
      const status = key === "assessment" ? assessmentStatus : stage.status;
      const exceptions = key === "assessment" ? assessmentExceptions(assessment) : "";
      return <div key={key} className="space-y-2">
        <div className="flex items-center justify-between gap-2"><span className="font-medium">{name}</span><Status value={status} /></div>
        <p className="text-muted-foreground">{tokenUsage(stage.tokens, status)}</p>
        {exceptions && <p className="tabular-nums text-muted-foreground">{exceptions}</p>}
        {stage.waiting_for === "verification" && <p className="text-muted-foreground">Waiting for fix verification to finish updating finding history.</p>}
        {stage.error && <TechnicalDetails error={stage.error} name={name} />}
      </div>;
    })}
  </PopoverContent>;
  const verification = run.progress?.verification;
  const failedFixVerifications = verification && typeof verification === "object" && "failed" in verification ? verification.failed : null;
  const engine = run.engine === "chatgpt" ? "Codex (ChatGPT)" : run.engine === "claude_code" ? "Claude Code" : run.engine === "mortis" ? "Mortis" : "Retired engine";
  return <>
    {run.engine !== "mortis" && <p role="status" className="mb-6 text-sm text-muted-foreground">This historical review used a retired engine. Its saved results remain available. Start a new Mortis review from the pull request to run analysis again.</p>}
    {pr.ignored && <p className="mb-6 rounded-lg border bg-muted/50 p-4 text-sm"><span role="status">This pull request is ignored.</span> <Link to={`/pulls/${pr.id}`} state={returnState} className="underline underline-offset-4">Open the pull request</Link> to restore reviews.</p>}
    <div className="mb-6 min-w-0">
      {summaryJob && <PullSummaryActivity runId={run.id} jobId={summaryJob} />}
      <PageTitle className="mb-0" title="Review run" titleMeta={<Status value={run.status} />} actions={can_write && !pr.ignored && (active(run.status) || run.status === "blocked" || ["failed", "partially_published"].includes(run.publication)) && <div className="flex flex-wrap gap-2">
            {run.engine === "mortis" && run.status === "blocked" && (run.owner_id == null || run.owner_id === user.id) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/retry` })}>Resume review</Button>}
            {(active(run.status) || run.status === "blocked") && <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/cancel` })}>Cancel review</Button>}
            {["failed", "partially_published"].includes(run.publication) && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ path: `/runs/${run.id}/publish` })}>Retry GitHub publication</Button>}
          </div>} footer={<>
            <ErrorNotice error={action.error || error} />
            <ErrorNotice error={run.error} />
            <RunStages run={run} assessment={assessment} assessmentStatus={assessmentStatus} summaryStage={summaryStage} summaryPublicationPending={summaryPublicationPending} summarySyncFailed={summarySyncFailed}>{stageDetails}</RunStages>
            <RunMetrics run={run} tokens={detail.total_tokens} />
          </>}>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <span>{date(run.created_at)}</span>
          <span>{words(run.trigger)}</span>
          <span className="inline-flex flex-wrap items-center gap-1.5"><GitCommitHorizontal aria-hidden="true" className="size-3.5 shrink-0" /><code title={run.head_sha}>{run.head_sha.slice(0, 7)}</code><span>· {run.head_sha === pr.head_sha ? "Current commit" : "Earlier commit"}</span></span>
        </div>
      </PageTitle>
      <section aria-label="GitHub publication" className="mt-2 space-y-3 text-xs">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          <div className="flex flex-wrap items-center gap-2"><span className="text-muted-foreground">GitHub comments</span>
            {run.publication === "not_applicable" ? <Status value={run.publication} label={feedback.publicationLabel} /> : <Tooltip><TooltipTrigger asChild><span tabIndex={0} className={statusTriggerClass}><Status value={run.publication} label={feedback.publicationLabel} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>{feedback.publicationDetail || "Saved review results are waiting to be sent to GitHub."}</TooltipContent>
            </Tooltip>}
          </div>
          {!run.stages?.assessment && assessment && (assessment.incomplete > 0 || assessment.pending > 0) && <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">Assessment</span>
            <Tooltip><TooltipTrigger asChild><span tabIndex={0} className={statusTriggerClass} role="status" aria-label={assessmentStatus === "partial" ? `Partial assessment: ${assessment.incomplete} candidates have incomplete assessments` : `${assessment.pending} candidates awaiting assessment`}><Status value={assessmentStatus} label={label(assessmentStatus)} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>{assessmentStatus === "partial" ? "Some candidates could not be fully assessed." : assessmentStatus === "running" ? "Candidates are being assessed." : "Candidates are waiting for assessment."}</TooltipContent>
            </Tooltip>
          </div>}
          {!run.stages?.verification && typeof failedFixVerifications === "number" && Number.isSafeInteger(failedFixVerifications) && failedFixVerifications > 0 && <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Fix verification</span>
            <Tooltip><TooltipTrigger asChild><span tabIndex={0} className={statusTriggerClass} role="status" aria-label={`Failed fix verifications: ${failedFixVerifications}`}><Status value="failed" label={`Failed ${failedFixVerifications}`} /></span></TooltipTrigger>
              <TooltipContent className="max-w-xs" sideOffset={6}>Automatic checks could not verify whether earlier findings were fixed. See Activity → Fix verification for the saved error.</TooltipContent>
            </Tooltip>
          </div>}
          {run.check_enabled && (checkValue || run.check_url || run.check_pending || run.check_error) && <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">GitHub check</span>
            {(run.check_url || checkValue) ? <Tooltip><TooltipTrigger asChild>
              {run.check_url ? <a className={`${statusTriggerClass} underline-offset-4`} href={run.check_url} target="_blank" rel="noreferrer" aria-label={`View check on GitHub: ${label(checkValue || "check")}`}>
                <Status value={checkValue || "check"} icon={<ArrowUpRight aria-hidden="true" className="size-3" />} />
              </a> : <span tabIndex={0} className={statusTriggerClass}><Status value={checkValue || "check"} /></span>}
            </TooltipTrigger><TooltipContent className="max-w-xs" sideOffset={6}>{checkValue && Object.hasOwn(checkDescriptions, checkValue) ? checkDescriptions[checkValue] : "The saved GitHub check status is unavailable."}</TooltipContent></Tooltip>
              : <Status value={run.check_error ? "failed" : "pending"} label={run.check_error ? "Error" : "Pending"} />}
          </div>}
        </div>
        <ErrorNotice error={run.publication_error} />
        {run.check_enabled && <ErrorNotice error={run.check_error} />}
      </section>
    </div>
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList variant="page" aria-label="Review run views" className="mb-4">
        <TabsTrigger value="findings">Findings <span className="text-xs font-normal tabular-nums text-muted-foreground">{finding_state_counts.open ?? 0}</span></TabsTrigger>
        <TabsTrigger ref={resolvedTab} value="resolved">Resolved <span className="text-xs font-normal tabular-nums text-muted-foreground">{finding_state_counts.resolved ?? 0}</span></TabsTrigger>
        <TabsTrigger ref={assessmentTab} value="assessment">Assessment {assessment && <span className="text-xs font-normal tabular-nums text-muted-foreground">{assessment.candidates}</span>}</TabsTrigger>
        <TabsTrigger value="coverage">Coverage</TabsTrigger>
        <TabsTrigger value="details">Details</TabsTrigger>
        <TabsTrigger value="activity">Activity <span className="text-xs font-normal tabular-nums text-muted-foreground">{activityEvents.length}</span></TabsTrigger>
      </TabsList>
      <TabsContent value="findings" className="min-w-0">
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">Findings recorded in this run, with their saved evidence. Later status changes do not change this list.</p>
        {!assessment && !active(run.status) && <p className="mb-4 text-xs text-muted-foreground">This run predates candidate assessment. Its findings have not been assessed by this policy.</p>}
        <RunFindings runId={run.id} state="open" running={active(run.status)} revision={findings_revision} tick={tick} searchInput={searchInput} onOpen={(item, trigger) => { findingTrigger.current = trigger; setFindingSeed({ item, tab }); showSheet("finding", item.finding_id); }} />
      </TabsContent>
      <TabsContent value="resolved" className="min-w-0">
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">Findings resolved by this run after fix verification and GitHub confirmation. Later status changes do not change this list.</p>
        <RunFindings runId={run.id} state="resolved" running={active(run.status)} revision={findings_revision} tick={tick} searchInput={searchInput} onOpen={(item, trigger) => { findingTrigger.current = trigger; setFindingSeed({ item, tab }); showSheet("finding", item.finding_id); }} />
      </TabsContent>
      <TabsContent value="coverage" className="min-w-0">
        <RunCoverage run={run} />
      </TabsContent>
      <TabsContent value="activity" className="min-w-0">
        <RunActivity events={activityEvents} />
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
            <AssessmentList runId={run.id} running={active(run.status)} revision={assessment_revision} tick={tick} onOpen={(item, trigger) => { findingTrigger.current = trigger; setCandidateSeed(item); showSheet("candidate", item.id); }} />
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
    <Sheet open={!!findingId || !!candidateId} onOpenChange={(open) => { if (!open) closeSheet(); }}>
      <SheetContent className="w-full gap-0 overflow-hidden sm:max-w-2xl" onCloseAutoFocus={(event) => { event.preventDefault(); (findingTrigger.current?.isConnected ? findingTrigger.current : tab === "assessment" ? assessmentTab.current : tab === "resolved" ? resolvedTab.current : searchInput.current)?.focus(); }}>
        <SheetHeader className="shrink-0 px-6 pb-4 pt-5">
          {findingId && candidate && <Button autoFocus variant="link" size="sm" className="min-h-11 justify-start self-start px-0 text-xs sm:min-h-8" onClick={() => showSheet("finding", null)}><ArrowLeft aria-hidden="true" />Back to assessment</Button>}
          <SheetTitle>{findingId ? "Finding details" : "Assessment details"}</SheetTitle>
          <SheetDescription className="sr-only">{findingId ? "Inspect the evidence saved in this run and its current resolution separately." : "Inspect the candidate's assessment decision and saved supporting evidence."}</SheetDescription>
        </SheetHeader>
        {candidateId && <ErrorNotice error={candidateQuery.error} />}
        {candidateId && !finding && candidateQuery.isPending && <div className="px-6"><Loading /></div>}
        {findingId ? finding ? <FindingPanel key={finding.finding_id} id={finding.finding_id} canWrite={can_write} snapshot={finding.snapshot} headSha={run.head_sha} /> : <div className="px-6">{findingQuery.isPending ? <Loading /> : <ErrorNotice error={findingQuery.error} />}</div> : candidate && <AssessmentPanel key={candidate.id} candidate={candidate} headSha={run.head_sha} runId={run.id}
          merged={pr.state === "merged"}
          waitingForVerification={run.stages?.assessment?.waiting_for === "verification"}
          canRetry={can_write && run.engine === "mortis" && !pr.ignored && (run.owner_id === null || run.owner_id === user.id) && ["completed", "partial"].includes(run.status) && ["published", "no_new_comments"].includes(run.publication)}
          onOpenFinding={() => { if (candidate.finding_id) showSheet("finding", candidate.finding_id); }} />}
      </SheetContent>
    </Sheet>
  </>;
}
