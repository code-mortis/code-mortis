import { useState } from "react";
import { getInboxReturn } from "./lib/inbox-filters";
import {
  Link,
  Navigate,
  useNavigate,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ChevronRight,
  GitPullRequest,
  Play,
  RefreshCw,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import {
  useAction,
  useApi,
  type Repository,
  type Run,
  type Schema,
} from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
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
  NativeSelect,
  PageTitle,
  Status,
  words,
} from "./components/common";
import { RepositorySettings } from "./settings";
import { fileFeedback, runFeedback } from "./lib/run-feedback";

function RunFeedback({ run, repositoryId, admin, canWrite, detail = false }: {
  run: Run; repositoryId: number; admin: boolean; canWrite: boolean; detail?: boolean;
}) {
  const inboxReturn = getInboxReturn(useLocation().state);
  const feedback = runFeedback(run);
  if (!detail && !feedback.needsAttention) return null;
  return (
    <section aria-label="Review outcome" className="mb-6 space-y-3 rounded-lg border bg-card p-5 text-sm">
      <p role="status" className="font-medium break-words">{feedback.summary}</p>
      <p className="text-muted-foreground break-words">
        {feedback.destination === "repository" && !admin ? "Ask your administrator to adjust the repository settings. " : ""}
        {feedback.nextStep}
      </p>
      <div className="flex flex-wrap gap-3">
        {!detail && <Link className="underline underline-offset-4" to={`/runs/${run.id}`} state={{ inbox: inboxReturn }}>View run details</Link>}
        {feedback.destination === "repository" && admin && (
          <Link className="underline underline-offset-4" to={`/repositories/${repositoryId}?configure=limits`}>Adjust review settings</Link>
        )}
        {feedback.destination === "connections" && <Link className="underline underline-offset-4" to="/settings?tab=connections">Manage AI connections</Link>}
        {detail && feedback.needsAttention && (
          <Link className="underline underline-offset-4" to={`/pulls/${run.pr_id}`} state={{ inbox: inboxReturn }}>
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
  const selected = requested ? repos.data?.find((repo) => String(repo.id) === requested) : repos.data?.[0];
  const pulls = useApi<Schema["PullRequestOut"][]>(
    `/repositories/${selected?.id}/pulls`,
    true,
    !!repositoryId && !!selected,
  );
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("open");
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
  if (repos.error && !repos.data) return <ErrorNotice error={repos.error} />;
  const rows =
    pulls.data?.filter(
      (pr) =>
        (scope === "all" || pr.state === scope) &&
        `${pr.title} ${pr.number} ${pr.author}`
          .toLowerCase()
          .includes(query.toLowerCase()),
    ) ?? [];
  return (
    <>
      <PageTitle
        title={<span className="break-all">{selected?.full_name ?? "Repositories"}</span>}
        actions={
          admin && (
            <Button asChild variant="outline">
              <Link to="/settings?tab=github">{repos.data?.length ? "Manage repositories" : "Connect repository"}</Link>
            </Button>
          )
        }
      >
        Follow each pull request from first review to final resolution.
      </PageTitle>
      <ErrorNotice error={repos.error || action.error} />
      {requested && !selected && (
        <Empty title="Repository unavailable">
          This repository was not found or you no longer have access. <Link className="underline" to="/repositories">View available repositories</Link>.
        </Empty>
      )}
      {!requested && !repos.data?.length && (
        <Empty title="Connect your first repository">
          {admin ? (
            <>
              Install your GitHub App, then connect the installation in{" "}
              <Link className="underline" to="/settings?tab=github">
                settings
              </Link>
              .
            </>
          ) : (
            "Ask your administrator to connect a repository you can access on GitHub."
          )}
        </Empty>
      )}
      {selected && (
        <>
          <div className="mb-7 flex flex-wrap items-center gap-3">
            <Status value={selected?.enabled ? "enabled" : "paused"} />
            <div className="ml-auto flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate({ path: `/repositories/${selected!.id}/sync` })
                }
              >
                <RefreshCw className="size-4" />
                Sync
              </Button>
              {admin && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEdit(true)}
                >
                  <SlidersHorizontal className="size-4" />
                  Configure
                </Button>
              )}
            </div>
          </div>
          {selected && !selected.enabled && (
            <div className="mb-6 rounded-lg border bg-card p-4 text-sm text-muted-foreground">
              Reviews are paused for this repository.{" "}
              {admin
                ? "Choose an AI connection in Configure to enable them."
                : "Your administrator can enable them."}
            </div>
          )}
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h2 className="font-semibold">Pull requests</h2>
              <span className="text-xs text-muted-foreground tabular">
                {rows.length}
              </span>
            </div>
            <div className="flex w-full gap-2 sm:w-auto">
              <div className="relative min-w-0 flex-1">
                <Search
                  aria-hidden="true"
                  className="absolute left-3 top-2.5 size-4 text-muted-foreground"
                />
                <Input
                  aria-label="Search pull requests"
                  className="pl-9 sm:w-60"
                  placeholder="Search pull requests…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <NativeSelect
                aria-label="Pull request state"
                className="w-28"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
              >
                <option value="open">Open</option>
                <option value="merged">Merged</option>
                <option value="closed">Closed</option>
                <option value="all">All</option>
              </NativeSelect>
            </div>
          </div>
          <ErrorNotice error={pulls.error} />
          {pulls.isPending ? (
            <Loading />
          ) : rows.length ? (
            <div className="overflow-hidden rounded-lg border bg-card divide-y">
              {rows.map((pr) => {
                const feedback = pr.latest_run ? runFeedback(pr.latest_run) : null;
                return (
                  <Link
                    key={pr.id}
                    to={`/pulls/${pr.id}`}
                    className="group flex items-start gap-3 p-5 hover:bg-muted/50 sm:items-center"
                  >
                    <GitPullRequest
                      aria-hidden="true"
                      className="mt-1 size-4 shrink-0 text-primary"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="break-words text-sm font-medium group-hover:underline">
                        {pr.title}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                        <span>
                          #{pr.number} · {pr.author}
                        </span>
                        <span className="max-w-60 truncate font-mono">
                          {pr.head_ref}
                        </span>
                        {pr.draft && <span>Draft</span>}
                        {pr.fork && <span>Fork · manual review</span>}
                      </div>
                      <div className="mt-3 flex flex-wrap items-center gap-2 sm:hidden">
                        <Status value={pr.latest_run?.status ?? "not_reviewed"} />
                        <span className="text-xs text-muted-foreground">
                          {pr.open_findings} open findings
                        </span>
                      </div>
                      {feedback?.needsAttention && (
                        <p className="mt-2 text-xs text-muted-foreground break-words">{feedback.summary}</p>
                      )}
                    </div>
                    <div className="hidden items-center gap-6 sm:flex">
                      <span className="text-xs text-muted-foreground tabular">
                        {pr.open_findings} open findings
                      </span>
                      <Status value={pr.latest_run?.status ?? "not_reviewed"} />
                    </div>
                    <ChevronRight
                      aria-hidden="true"
                      className="size-4 shrink-0 text-muted-foreground"
                    />
                  </Link>
                );
              })}
            </div>
          ) : (
            <Empty
              title={
                query
                  ? "No matching pull requests"
                  : "No pull requests here yet"
              }
            >
              {query
                ? "Try another search or change the state filter."
                : "Use Sync to fetch pull requests from GitHub. Reviews begin once this repository is enabled."}
            </Empty>
          )}
          {selected && (
            <Sheet open={edit} onOpenChange={setEdit}>
              <SheetContent className="sm:max-w-xl overflow-y-auto">
                <SheetHeader>
                  <SheetTitle>Repository configuration</SheetTitle>
                  <SheetDescription>{selected.full_name}</SheetDescription>
                </SheetHeader>
                <div className="p-6">
                  <RepositorySettings
                    key={selected.id}
                    repository={selected}
                    showLimits={params.get("configure") === "limits"}
                    onSaved={() => setEdit(false)}
                  />
                </div>
              </SheetContent>
            </Sheet>
          )}
        </>
      )}
    </>
  );
}

function RunList({ runs }: { runs: Run[] }) {
  const inboxReturn = getInboxReturn(useLocation().state);
  if (!runs.length)
    return (
      <Empty title="No reviews yet">
        Start a review to record findings and coverage for this commit.
      </Empty>
    );
  return (
    <div className="divide-y rounded-lg border bg-card">
      {runs.map((run) => {
        const feedback = runFeedback(run);
        return (
          <Link
            className="flex flex-wrap items-center gap-3 px-5 py-4 hover:bg-muted/50"
            key={run.id}
            to={`/runs/${run.id}`}
            state={{ inbox: inboxReturn }}
          >
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{date(run.created_at)}</div>
              <div className="mt-1 text-xs text-muted-foreground">
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
            <Status value={run.status} />
            <span className="text-xs text-muted-foreground">
              GitHub: {feedback.publicationLabel}
            </span>
            <ChevronRight className="size-4 text-muted-foreground" />
          </Link>
        );
      })}
    </div>
  );
}

export function PullDetail({ admin }: { admin: boolean }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const inboxReturn = getInboxReturn(location.state);
  const query = useApi<Schema["PRDetail"]>(`/pulls/${id}`, true);
  const action = useAction<Run>();
  const connections = useApi<Schema["ConnectionOut"][]>("/connections");
  const [connection, setConnection] = useState("");
  const [status, setStatus] = useState("open");
  const [severity, setSeverity] = useState("all");
  const [finding, setFinding] = useState<string | null>(null);
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  const {
    pull_request: pr,
    repository,
    runs,
    findings,
    can_write,
  } = query.data;
  const running = runs.some((run) => active(run.status));
  const observed = runs.find((run) =>
    ["completed", "partial"].includes(run.status),
  );
  const visible = findings.filter(
    (item) =>
      (status === "all" || item.status === status) &&
      (severity === "all" || item.severity === severity),
  );
  const count = (state: string) =>
    findings.filter((item) => item.status === state).length;
  return (
    <>
      <PageTitle
        title={pr.title}
        actions={
          <>
            <ExternalLink href={pr.url}>GitHub</ExternalLink>
            {can_write && (
              <NativeSelect
                aria-label="Review connection"
                value={connection}
                onChange={(event) => setConnection(event.target.value)}
                className="max-w-64"
              >
                <option value="">Repository default connection</option>
                {connections.data?.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </NativeSelect>
            )}
            {can_write && (
              <Button
                disabled={
                  action.isPending ||
                  running ||
                  pr.state !== "open" ||
                  !repository.enabled
                }
                onClick={async () => {
                  const run = await action
                    .mutateAsync({
                      path: `/pulls/${id}/runs`,
                      body: connection ? { connection_id: connection } : {},
                    })
                    .catch(() => null);
                  if (run) navigate(`/runs/${run.id}`, { state: { inbox: inboxReturn } });
                }}
              >
                <Play className="size-4" />
                {running ? "Review in progress" : "Run review"}
              </Button>
            )}
          </>
        }
      >
        <div className="flex flex-wrap items-center gap-3">
          <Status value={pr.state} />
          <span>@{pr.author}</span>
          <code className="max-w-full truncate text-xs">
            {pr.head_ref} · {pr.head_sha.slice(0, 7)}
          </code>
          {pr.fork && <span>Fork reviews require a manual start.</span>}
        </div>
      </PageTitle>
      <ErrorNotice error={action.error || query.error} />
      {runs[0] && <RunFeedback run={runs[0]} repositoryId={repository.id} admin={admin} canWrite={can_write} />}
      <div className="mb-8 grid grid-cols-3 border-y py-5">
        <div>
          <p className="text-2xl font-semibold tabular">{count("open")}</p>
          <p className="mt-1 text-xs text-muted-foreground">Open findings</p>
        </div>
        <div>
          <p className="text-2xl font-semibold tabular">{count("resolved")}</p>
          <p className="mt-1 text-xs text-muted-foreground">Resolved</p>
        </div>
        <div>
          <p className="text-2xl font-semibold tabular">{runs.length}</p>
          <p className="mt-1 text-xs text-muted-foreground">Review runs</p>
        </div>
      </div>
      <Tabs defaultValue="findings">
        <TabsList className="mb-6">
          <TabsTrigger value="findings">Findings</TabsTrigger>
          <TabsTrigger value="history">Run history</TabsTrigger>
        </TabsList>
        <TabsContent value="findings">
          <div className="mb-4 flex flex-wrap gap-2">
            <NativeSelect
              aria-label="Finding status"
              className="w-40"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="open">Open findings</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
              <option value="all">All findings</option>
            </NativeSelect>
            <NativeSelect
              aria-label="Finding severity"
              className="w-36"
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
            >
              <option value="all">All severities</option>
              {["critical", "high", "medium", "low"].map((value) => (
                <option key={value} value={value}>
                  {value[0].toUpperCase() + value.slice(1)}
                </option>
              ))}
            </NativeSelect>
          </div>
          {visible.length ? (
            <div className="divide-y overflow-hidden rounded-lg border bg-card">
              {visible.map((item) => (
                <button
                  key={item.id}
                  className="flex w-full gap-4 p-5 text-left hover:bg-muted/50"
                  onClick={() => setFinding(item.id)}
                >
                  <div className="min-w-0 flex-1">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <Status value={item.severity} />
                      <span className="text-xs text-muted-foreground">
                        {item.category}
                      </span>
                      {item.desired_status && (
                        <span className="text-xs text-muted-foreground">
                          {item.sync_error
                            ? "Sync needs attention"
                            : "Sync pending"}
                        </span>
                      )}
                      {observed && item.last_seen_run_id !== observed.id && (
                        <span className="text-xs text-muted-foreground">
                          Not observed in latest review
                        </span>
                      )}
                    </div>
                    <p className="line-clamp-2 text-sm font-medium leading-relaxed">
                      {item.content.replaceAll(/[*`#]/g, "")}
                    </p>
                    <p className="mt-3 truncate font-mono text-xs text-muted-foreground">
                      {item.path}:{item.start_line}
                      {item.end_line !== item.start_line && `–${item.end_line}`}
                    </p>
                  </div>
                  <ChevronRight className="mt-1 size-4 shrink-0 text-muted-foreground" />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              title={
                findings.length
                  ? "No findings match these filters"
                  : runs.length
                    ? "No findings recorded"
                    : "This PR is ready for a review"
              }
            >
              {findings.length
                ? "Choose another status or severity to see more findings."
                : runs.length
                  ? "Check run history for completion and coverage. An incomplete review is not a clean result."
                  : "Run a review to find issues and publish comments to GitHub."}
            </Empty>
          )}
          <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
            Resolving or reopening a GitHub review thread updates its finding
            here. A finding missing from a later run stays open until someone
            resolves it.
          </p>
        </TabsContent>
        <TabsContent value="history">
          <RunList runs={runs} />
        </TabsContent>
      </Tabs>
      <Sheet
        open={!!finding}
        onOpenChange={(open) => {
          if (!open) setFinding(null);
        }}
      >
        <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Finding details</SheetTitle>
            <SheetDescription>
              Review the evidence and its resolution history.
            </SheetDescription>
          </SheetHeader>
          {finding && (
            <FindingPanel key={finding} id={finding} canWrite={can_write} />
          )}
        </SheetContent>
      </Sheet>
    </>
  );
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
  const action = useAction();
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  const {
    run,
    repository,
    occurrences,
    candidates = [],
    assessment,
    can_write,
  } = query.data;
  const coverage = ((
    run.report?.manifest as { coverage?: Coverage } | undefined
  )?.coverage ?? {}) as Coverage;
  const duration =
    run.started_at && run.finished_at
      ? `${Math.max(0, Math.round((+new Date(run.finished_at) - +new Date(run.started_at)) / 1000))}s`
      : null;
  const checkValue = run.check_conclusion || run.check_status;
  const verification = verificationSummary(run.progress?.verification);
  const feedback = runFeedback(run);
  const files = fileFeedback(run);
  return (
    <>
      <PageTitle
        title="Review run"
        actions={
          can_write && (
            <>
              {run.status === "blocked" && (run.owner_id === null || run.owner_id === undefined || run.owner_id === user.id) && (
                <Button
                  disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/runs/${id}/retry` })}
                >
                  Resume review
                </Button>
              )}
              {(active(run.status) || run.status === "blocked") && (
                <Button
                  variant="outline"
                  disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/runs/${id}/cancel` })}
                >
                  Cancel review
                </Button>
              )}
              {["failed", "partially_published"].includes(run.publication) && (
                <Button
                  disabled={action.isPending}
                  onClick={() => action.mutate({ path: `/runs/${id}/publish` })}
                >
                  Retry GitHub publication
                </Button>
              )}
            </>
          )
        }
      >
        <span>
          {date(run.created_at)} · {run.trigger} ·{" "}
          <code>{run.head_sha.slice(0, 7)}</code>
          {duration && ` · ${duration}`}
        </span>
      </PageTitle>
      <ErrorNotice error={action.error || query.error} />
      <RunFeedback run={run} repositoryId={repository.id} admin={user.role === "admin"} canWrite={can_write} detail />
      <div className="mb-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border bg-card p-5">
          <p className="mb-3 text-xs text-muted-foreground">Review execution</p>
          <Status value={run.status} />
          <p className="mt-3 text-sm text-muted-foreground">
            {String(
              (run.config.connection as Record<string, unknown> | undefined)
                ?.name ?? "",
            )}{" "}
            ·{" "}
            {String(
              (run.config.connection as Record<string, unknown> | undefined)
                ?.model ?? "",
            )}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            Engine: {run.engine === "chatgpt" ? "Codex (ChatGPT)" : run.engine === "claude_code" ? "Claude Code" : "Open Code Review"}
            {run.owner_id != null && ` · Account owner: GitHub #${run.owner_id}`}
            {run.actor_id != null && ` · Started by: GitHub #${run.actor_id}`}
          </p>
        </div>
        <div className="rounded-lg border bg-card p-5">
          <p className="mb-3 text-xs text-muted-foreground">GitHub comments</p>
          <Status value={run.publication} label={feedback.publicationLabel} />
          <p className="mt-3 text-sm text-muted-foreground">
            {feedback.publicationDetail}
          </p>
          <ErrorNotice error={run.publication_error} />
          {run.check_enabled && (checkValue || run.check_url || run.check_pending || run.check_error) && (
            <div className="mt-5 space-y-3 border-t pt-4">
              <p className="text-xs text-muted-foreground">GitHub check</p>
              {(checkValue || run.check_url) && (
                <div className="flex flex-wrap items-center gap-3">
                  {checkValue && <Status value={checkValue} />}
                  {run.check_url && (
                    <a className="text-sm underline underline-offset-4" href={run.check_url} target="_blank" rel="noreferrer">
                      View check on GitHub
                    </a>
                  )}
                </div>
              )}
              {run.check_pending && (
                <p role="status" className="text-sm text-muted-foreground">
                  {run.check_error ? "Check update pending. Review results are saved separately." : "Updating the check on GitHub…"}
                </p>
              )}
              <ErrorNotice error={run.check_error} />
            </div>
          )}
        </div>
      </div>
      <div className="space-y-3">
        <ErrorNotice error={run.error} />
        {!!verification?.selected && (
          <div className="space-y-2 rounded-lg border p-4 text-sm">
            <p className="font-medium">Fix verification</p>
            <p>{verification.completed ?? 0} of {verification.selected} findings verified. {verification.resolved ?? 0} automatically resolved, {verification.pending ?? 0} pending synchronization.</p>
            <p className="text-muted-foreground">{verification.still_present ?? 0} still present, {verification.uncertain ?? 0} uncertain, {verification.skipped ?? 0} skipped, {verification.failed ?? 0} failed.</p>
            <p className="text-muted-foreground">Verified fixes remain open until GitHub confirms resolution. Evidence is saved in each finding’s Activity.</p>
          </div>
        )}
        {typeof run.progress?.selected === "number" && (
          <p role="status" className="text-sm text-muted-foreground">
            {String(run.progress.completed ?? 0)} of {String(run.progress.selected)} assigned files completed.
            {run.status === "blocked" && " Completed files are saved and reused when you resume."}
          </p>
        )}
      </div>
      {!active(run.status) && (
        <section className="my-8">
          <h2 className="mb-4 font-semibold">Coverage</h2>
          {run.report ? (
            <>
              <div className="grid grid-cols-2 gap-4 border-y py-5 sm:grid-cols-5">
                {(
                  [
                    "selected",
                    "completed",
                    "reused",
                    "failed",
                    "waived",
                  ] as const
                ).map((key) => (
                  <div key={key}>
                    <p className="text-xl font-semibold tabular">
                      {coverage[key]?.length ?? 0}
                    </p>
                    <p className="mt-1 text-xs capitalize text-muted-foreground">
                      {key}
                    </p>
                  </div>
                ))}
              </div>
              {!!files.length && (
                <details className="mt-4 text-sm" open={run.status === "skipped"}>
                  <summary className="cursor-pointer font-medium">Files not reviewed ({files.length})</summary>
                  <ul className="mt-3 max-h-72 space-y-3 overflow-auto">
                    {files.map((file) => <li key={file.path} className="space-y-1">
                      <code className="break-all text-xs">{file.path}</code>
                      <p className="text-muted-foreground break-words">{file.reason}</p>
                    </li>)}
                  </ul>
                </details>
              )}
              <details className="mt-4 text-sm">
                <summary className="cursor-pointer text-muted-foreground">
                  Inspect coverage report
                </summary>
                <div className="mt-3">
                  <Code language="json">{JSON.stringify(run.report, null, 2)}</Code>
                </div>
              </details>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              No coverage report was produced.
            </p>
          )}
        </section>
      )}
      <section className="mt-8">
        <h2 className="mb-4 font-semibold">Candidate assessment</h2>
        {assessment ? (
          <>
            <p className="text-sm">
              {assessment.new_findings} new validated findings · {assessment.repeated_observations} repeated observations · {assessment.withheld_candidates} withheld candidates · {assessment.published_comments} comments published
            </p>
            {!!assessment.pending && <p role="status" className="mt-2 text-sm text-muted-foreground">{assessment.pending} candidates awaiting assessment.</p>}
            {!!assessment.incomplete && <p className="mt-2 text-sm text-muted-foreground">Assessment is incomplete for {assessment.incomplete} candidate{assessment.incomplete === 1 ? "" : "s"}. No new comments does not mean the review is clean.</p>}
            <div className="mt-4 space-y-3">
              {candidates.map((candidate) => (
                <details key={candidate.id} className="rounded-lg border p-4 text-sm">
                  <summary className="cursor-pointer break-words">
                    {String(candidate.snapshot.path)}:{String(candidate.snapshot.start_line)} · {getCandidateLabel(candidate)}
                  </summary>
                  <div className="mt-4 space-y-3">
                    <Markdown>{String(candidate.snapshot.content)}</Markdown>
                    <ErrorNotice error={candidate.error} />
                    {candidate.result && (
                      <>
                        <p>{candidate.result.explanation}</p>
                        {candidate.result.history_explanation && <p>{candidate.result.history_explanation}</p>}
                        {candidate.result.changed_conditions && <p>{candidate.result.changed_conditions}</p>}
                        {candidate.result.evidence.map((evidence, index) => (
                          <div key={index}>
                            <p className="mb-2 break-all text-xs text-muted-foreground">{evidence.revision}: {evidence.path}:{evidence.start_line}–{evidence.end_line}</p>
                            <Code path={evidence.path}>{evidence.quote}</Code>
                          </div>
                        ))}
                      </>
                    )}
                    {candidate.finding_id && <p className="break-all text-xs text-muted-foreground">Existing finding: {candidate.finding_id}. Resolution state is preserved.</p>}
                  </div>
                </details>
              ))}
            </div>
          </>
        ) : <p className="text-sm text-muted-foreground">This run predates candidate assessment. Its findings have not been assessed by this policy.</p>}
      </section>
      <section className="mt-8">
        <h2 className="mb-4 font-semibold">
          Findings at this run{" "}
          <span className="ml-2 font-normal text-muted-foreground">
            {occurrences.length}
          </span>
        </h2>
        {occurrences.length ? (
          <div className="space-y-4">
            {occurrences.map((item) => (
              <article
                key={item.finding_id}
                className="rounded-lg border bg-card p-5"
              >
                <div className="mb-3 flex flex-wrap items-center gap-3">
                  <Status value={String(item.snapshot.severity)} />
                  <code className="break-all text-xs text-muted-foreground">
                    {String(item.snapshot.path)}:
                    {String(item.snapshot.start_line)}
                  </code>
                  {!item.inline_line && (
                    <span className="text-xs text-muted-foreground">
                      Summary comment
                    </span>
                  )}
                </div>
                <Markdown>{String(item.snapshot.content)}</Markdown>
              </article>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No findings recorded in this run.
          </p>
        )}
      </section>
      <details className="mt-8 text-sm">
        <summary className="cursor-pointer text-muted-foreground">
          Configuration used
        </summary>
        <div className="mt-3">
          <Code language="json">{JSON.stringify(run.config, null, 2)}</Code>
        </div>
      </details>
    </>
  );
}
