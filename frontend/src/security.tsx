import { useEffect, useRef, useState, type FormEvent } from "react";
import { usePagedList } from "./hooks/use-paged-list";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, ChevronDown, ChevronRight, CircleCheck, CircleDot, CircleSlash, ClipboardCheck, Download, FileCode2, GitBranch, GitCommitHorizontal, Globe, LockKeyhole, MoreHorizontal, Play, RefreshCw, Settings2, Shield } from "lucide-react";
import { api, ApiError, useAction, useApi, type Connection, type Schema } from "./api";
import { Code } from "./components/code";
import { AssessmentStatus } from "./components/assessment-status";
import { date, Empty, ErrorNotice, Field, FindingCount, ListDate, Loading, Markdown, PageTitle, StatusGroup, words } from "./components/common";
import { ModelPicker } from "./components/model-picker";
import { FilterBar } from "./components/filter-bar";
import { findingOrderings, normalizeOrdering, snapshotOrderings } from "./lib/ordering";
import { Pagination } from "./components/pagination";
import { FindingCard, FindingCategory } from "./components/finding-card";
import { SearchSelect } from "./components/search-select";
import { SecurityGrade, SecurityGradeShield, securityStates } from "./components/security-grade";
import { Button } from "./components/ui/button";
import { Badge } from "./components/ui/badge";
import { Checkbox } from "./components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import { Input } from "./components/ui/input";
import { Sheet, SheetContent, SheetClose, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "./components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { Textarea } from "./components/ui/textarea";
import { SecurityBadge, SecurityScanBadge, SecurityScanStatus, SecurityStatus } from "./components/security-badge";
import { useUrlTab } from "./hooks/use-url-tab";
import { useRunResource } from "./hooks/use-run-resource";
import { findingSeverities } from "./lib/review-filters";
import { label } from "./lib/inbox-filters";
import { errorFeedback } from "./lib/error-feedback";
import { runDuration } from "./lib/run-duration";

type User = Schema["UserOut"];
type Scan = Schema["SecurityScanOut"];
type Finding = Schema["SecurityFindingOut"];
type SecurityAssessment = Schema["SecurityAssessmentOut"];
type SecurityRepo = Schema["SecurityRepositoryOut"];
type IssueCandidate = Schema["SecurityIssueCandidate"];
const active = (status: string) => ["queued", "running", "blocked"].includes(status);
const text = (value: unknown) => typeof value === "string" ? value : "";
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const entries = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "Unknown";
const metadataLink = "rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring";
const disclosure = "group rounded-lg border bg-card p-4 sm:p-5";
const disclosureSummary = "flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-sm font-medium outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden";
const stageDescriptions: Record<string, [string, string]> = {
  snapshot: ["Snapshot", "Prepare regular repository files from the saved commit without executing repository code."],
  trivy: ["Trivy", "Check supported dependencies, exposed credential material, and infrastructure configuration."],
  analysis: ["Analysis", "Audit source files with the selected AI model. A file counts only after its result passes validation."],
  assessment: ["Assessment", "Check proposed findings against source evidence, protections, and finding history."],
  verification: ["Fix verification", "Check earlier findings for evidence that the original defect was actually fixed."],
  report: ["Report", "Save the report and grade confirmed findings. Coverage, verification, and freshness remain explicit."],
};

function useReturnFocus(returnTo?: HTMLElement | null) {
  const trigger = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: () => { trigger.current = returnTo ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null); },
    onCloseAutoFocus: (event: Event) => { event.preventDefault(); const target = returnTo?.isConnected ? returnTo : trigger.current; if (target?.isConnected) target.focus(); },
  };
}


function useFilters() {
  const [params, setParams] = useSearchParams();
  function change(key: string, value: string | string[]) {
    const next = new URLSearchParams(params);
    next.delete(key);
    for (const item of Array.isArray(value) ? value : [value]) if (item) next.append(key, item);
    if (["search", "state", "severity", "kind", "grade", "status", "outcome", "ordering"].includes(key)) next.delete("page");
    setParams(next);
  }
  return { params, change, setParams };
}

export function SecurityOverview({ user }: { user: User }) {
  const { params, change } = useFilters();
  const [selected, setSelected] = useState<number>();
  const scanTrigger = useRef<HTMLButtonElement | null>(null);
  const search = params.get("search") ?? "";
  const requestedOrdering = params.get("ordering") ?? "-updated_at";
  const ordering = ["updated_at", "-updated_at", "repository", "-repository"].includes(requestedOrdering) ? requestedOrdering : "-updated_at";
  const orderings: Record<string, string> = { updated_at: "Updated", repository: "Repository" };
  const request = new URLSearchParams();
  for (const key of ["search", "grade", "status"]) for (const value of params.getAll(key)) request.append(key, value);
  request.set("ordering", ordering);
  const { query, items: rows, denied, pagination } = usePagedList<SecurityRepo>(`/security/repositories?${request}`);
  const data = query.data;
  return <>
    <header className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Security</h1>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">Scan default branches, inspect supported findings, and verify fixes.</p>
    </header>
    {denied ? <ErrorNotice error={query.error} /> : <>
      <div className="mb-6"><FilterBar query={search} searchLabel="Search repositories" searchPlaceholder="Search repositories…"
        onSearch={(value) => change("search", value)} onChange={change} filters={[
          { key: "grade", label: "Grade", values: params.getAll("grade"), choices: ["A", "B", "C", "D", "F", "unrated"].map((value) => ({ value, name: value === "unrated" ? "Not rated" : value, count: data?.grade_counts?.[value] ?? 0 })) },
          { key: "status", label: "Scan status", values: params.getAll("status"), choices: Object.entries(securityStates).map(([value, name]) => ({ value, name, count: data?.status_counts?.[value] ?? 0 })) },
        ]} ordering={{ value: ordering, defaultValue: "-updated_at", options: orderings, onChange: (value) => change("ordering", value) }} /></div>
      {query.isPending ? <Loading /> : !rows.length && !query.error ? <Empty title={data?.registered_total ? "No matching repositories" : "No repositories available"}>{data?.registered_total ? "Change the search or filters to see other repositories." : "Connect a repository through GitHub settings to start scanning."}</Empty> :
        <div className="overflow-hidden rounded-lg border bg-card">
          <div aria-hidden="true" data-slot="security-repository-heading" className="inbox-row-grid hidden border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground md:grid"><span>Repository</span><span>Scan status</span><span>Open findings</span><span>Latest scan</span><span /></div>
          {rows.map(({ repository: repo, summary }) => {
            const Visibility = repo.private ? LockKeyhole : Globe;
            const slash = repo.full_name.lastIndexOf("/");
            const counts = summary.counts ?? {};
            return <article key={repo.id} data-slot="security-repository-row" className="inbox-row-grid group relative grid gap-y-3 border-b px-4 py-4 text-sm transition-colors last:border-b-0 hover:bg-muted/50 md:items-center md:px-5 md:py-3">
              <div className="flex min-w-0 items-center gap-3 pr-12 md:pr-0"><SecurityGrade shield repositoryId={repo.id} summary={summary} /><div className="min-w-0">
                <Link className="break-words font-medium group-hover:text-primary after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring [overflow-wrap:anywhere]" to={`/security/repositories/${repo.id}`}><span className="font-normal text-muted-foreground group-hover:text-primary">{repo.full_name.slice(0, slash + 1)}</span>{repo.full_name.slice(slash + 1)}</Link>
                <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground"><Visibility aria-hidden="true" className="size-3.5" />{repo.private ? "Private" : "Public"}</div>
              </div></div>
              <div className="relative z-10 w-fit min-w-0"><StatusGroup>{summary.latest_scan_id ? <SecurityScanBadge status={summary.latest_status ?? "unknown"} scanId={summary.latest_scan_id} /> : <SecurityGrade repositoryId={repo.id} summary={summary} />}</StatusGroup></div>
              <div><FindingCount count={Object.values(counts).reduce((sum, value) => sum + value, 0)} /><span className="sr-only">{Object.entries(counts).filter(([, value]) => value > 0).map(([severity, value]) => `${value} ${severity}`).join(", ")}</span></div>
              {summary.latest_at ? <ListDate value={summary.latest_at} /> : <span className="text-xs text-muted-foreground">Not scanned</span>}
              <div className="absolute right-2 top-2 z-10 md:static md:justify-self-end"><SecurityRepositoryActions repository={repo} summary={summary} admin={user.role === "admin"} onRun={(trigger) => { scanTrigger.current = trigger; setSelected(repo.id); }} /></div>
            </article>;
          })}
          <Pagination {...pagination} label="repositories" />
        </div>}
    </>}
    {selected !== undefined && <ScanDialog key={selected} repositoryId={selected} user={user} returnTo={scanTrigger.current} onClose={() => setSelected(undefined)} />}
  </>;
}

function SecurityRepositoryActions({ repository, summary, admin, onRun }: {
  repository: SecurityRepo["repository"]; summary: SecurityRepo["summary"]; admin: boolean; onRun: (trigger: HTMLButtonElement) => void;
}) {
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState<"top" | "bottom">("bottom");
  const trigger = useRef<HTMLButtonElement>(null);
  const openingDialog = useRef(false);
  const detail = useApi<SecurityRepo>(`/security/repositories/${repository.id}`, false, open);
  const data = detail.error ? undefined : detail.data;
  const latest = data?.summary ?? summary;
  return <DropdownMenu open={open} onOpenChange={(value) => {
    if (value) setSide((trigger.current?.getBoundingClientRect().top ?? 0) > window.innerHeight / 2 ? "top" : "bottom");
    setOpen(value);
  }}>
    <DropdownMenuTrigger asChild><Button ref={trigger} variant="ghost" size="icon" className="size-11 text-muted-foreground sm:size-8" aria-label={`Actions for ${repository.full_name}`}><MoreHorizontal aria-hidden="true" className="size-4" /></Button></DropdownMenuTrigger>
    <DropdownMenuContent side={side} align="end" collisionPadding={16} className="w-64 max-w-[calc(100vw-2rem)] [&_[role=menuitem]]:min-h-11 sm:[&_[role=menuitem]]:min-h-8" onCloseAutoFocus={(event) => {
      if (openingDialog.current) { event.preventDefault(); openingDialog.current = false; }
    }}>
      {detail.isLoading && <p role="status" className="px-2 py-2 text-xs text-muted-foreground">Loading scan actions…</p>}
      {detail.error && <><p role="alert" className="px-2 py-2 text-xs text-destructive">{errorFeedback(detail.error).message}</p><DropdownMenuItem onSelect={(event) => { event.preventDefault(); void detail.refetch(); }}><RefreshCw aria-hidden="true" />Retry actions</DropdownMenuItem></>}
      {data && (data.can_write ? <><DropdownMenuItem disabled={detail.isFetching || active(latest.latest_status ?? "")} onSelect={() => {
        if (trigger.current) { openingDialog.current = true; onRun(trigger.current); }
      }}><Play aria-hidden="true" />Run scan</DropdownMenuItem><DropdownMenuSeparator /></> : <p className="px-2 py-2 text-xs text-muted-foreground">You have read-only access to this repository.</p>)}
      {latest.latest_scan_id && <DropdownMenuItem asChild><Link to={`/security/scans/${latest.latest_scan_id}`}><ClipboardCheck aria-hidden="true" />View latest scan</Link></DropdownMenuItem>}
      <DropdownMenuItem asChild><Link to={`/security/repositories/${repository.id}`}><Shield aria-hidden="true" />View repository security</Link></DropdownMenuItem>
      <DropdownMenuItem asChild><a href={`https://github.com/${repository.full_name}`} target="_blank" rel="noreferrer"><ArrowUpRight aria-hidden="true" />Open on GitHub</a></DropdownMenuItem>
      {admin && <DropdownMenuItem asChild><Link to={`/repositories/${repository.id}/settings`}><Settings2 aria-hidden="true" />Repository settings</Link></DropdownMenuItem>}
    </DropdownMenuContent>
  </DropdownMenu>;
}

function ScanDialog({ repositoryId, user, onClose, retry, returnTo }: { repositoryId: number; user: User; onClose: () => void; retry?: Scan; returnTo?: HTMLElement | null }) {
  const focus = useReturnFocus(returnTo);
  const repo = useApi<SecurityRepo>(`/security/repositories/${repositoryId}`);
  const connections = useApi<Connection[]>(`/connections?repository_id=${repositoryId}`);
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent {...focus} className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
    <DialogHeader><DialogTitle>{retry ? "Retry security scan" : "Run security scan"}</DialogTitle><DialogDescription>Scan the full default branch with OCR and Trivy. This uses the selected AI connection and its quota.</DialogDescription></DialogHeader>
    <ErrorNotice error={repo.error || connections.error} />
    {repo.isPending || connections.isPending ? <Loading /> : repo.data && connections.data && !repo.error && !connections.error &&
      <ScanForm repository={repo.data} connections={connections.data.filter((connection) => connection.kind === "api" || connection.owner_id === user.id)} onClose={onClose} retry={retry} />}
  </DialogContent></Dialog>;
}

function ScanForm({ repository, connections, onClose, retry }: { repository: SecurityRepo; connections: Connection[]; onClose: () => void; retry?: Scan }) {
  const navigate = useNavigate();
  const action = useAction<Scan>();
  const savedConnection = object(retry?.config.connection);
  const savedLimits = object(retry?.config.limits);
  const [connectionId, setConnectionId] = useState(retry ? text(savedConnection.id) : repository.repository.connection_id ?? "");
  const selected = connections.find((item) => item.id === connectionId);
  const [model, setModel] = useState(retry ? text(savedConnection.model) : (selected?.id === repository.repository.connection_id && repository.repository.settings.model) || selected?.model || "");
  const initialTokens = retry ? savedLimits.token_budget : repository.repository.settings.token_budget;
  const initialTimeout = retry ? savedLimits.timeout_seconds : repository.repository.settings.timeout_seconds;
  const [tokens, setTokens] = useState(initialTokens == null ? "" : String(initialTokens));
  const [timeout, setTimeout] = useState(initialTimeout == null ? "" : String(initialTimeout));
  const [unlimitedTokens, setUnlimitedTokens] = useState(initialTokens == null);
  const [unlimitedTimeout, setUnlimitedTimeout] = useState(initialTimeout == null);
  const [concurrency, setConcurrency] = useState(String(retry ? savedLimits.concurrency ?? 1 : repository.repository.settings.concurrency));
  const requestKey = useRef(crypto.randomUUID());
  useEffect(() => { requestKey.current = crypto.randomUUID(); }, [connectionId, model, tokens, timeout, unlimitedTokens, unlimitedTimeout, concurrency]);
  function submit(event: FormEvent) {
    event.preventDefault();
    action.mutate({ path: retry ? `/security/scans/${retry.id}/retry` : `/security/repositories/${repository.repository.id}/scans`, body: {
      connection_id: connectionId, model, token_budget: unlimitedTokens ? null : Number(tokens),
      timeout_seconds: unlimitedTimeout ? null : Number(timeout), request_key: requestKey.current,
      concurrency: selected?.kind === "api" ? Number(concurrency) : 1,
    } }, { onSuccess: (scan) => { onClose(); navigate(`/security/scans/${scan.id}`); } });
  }
  return <form onSubmit={submit} className="space-y-5">
    <div className="rounded-md border bg-muted/30 p-4 text-sm"><p className="break-words font-medium">{repository.repository.full_name}</p><p className="mt-2 flex items-center gap-1.5 font-mono text-xs text-muted-foreground"><GitBranch aria-hidden="true" className="size-3.5 shrink-0" />{repository.summary.default_branch ?? "Resolved from GitHub when the scan starts"}</p></div>
    <Field label="AI connection">{(id) => <SearchSelect id={id} value={connectionId} onChange={(value) => { setConnectionId(value); setModel((value === repository.repository.connection_id && repository.repository.settings.model) || connections.find((row) => row.id === value)?.model || ""); }} options={[{ value: "", label: "Choose a connection" }, ...connections.map((row) => ({ value: row.id, label: row.name }))]} required disabled={action.isPending || !!retry} />}</Field>
    <ModelPicker value={model} onChange={setModel} path={connectionId ? `/connections/${connectionId}/models?repository_id=${repository.repository.id}` : undefined} enabled={!!selected} disabled={action.isPending || !!retry} required />
    <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Field label="Token budget">{(id) => <Input id={id} type="number" min={1000} max={5000000} step={1000} value={tokens} placeholder="Unlimited" required={!unlimitedTokens} disabled={action.isPending || !!retry || unlimitedTokens} onChange={(event) => setTokens(event.target.value)} />}</Field>
      <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs sm:min-h-8"><Checkbox aria-label="Unlimited token budget" checked={unlimitedTokens} disabled={action.isPending || !!retry} onCheckedChange={(checked) => { setUnlimitedTokens(checked === true); if (!tokens) setTokens("200000"); }} />Unlimited</label></div>
      <div className="space-y-2"><Field label="Timeout (seconds)">{(id) => <Input id={id} type="number" min={60} max={3600} value={timeout} placeholder="Unlimited" required={!unlimitedTimeout} disabled={action.isPending || !!retry || unlimitedTimeout} onChange={(event) => setTimeout(event.target.value)} />}</Field>
      <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs sm:min-h-8"><Checkbox aria-label="Unlimited timeout" checked={unlimitedTimeout} disabled={action.isPending || !!retry} onCheckedChange={(checked) => { setUnlimitedTimeout(checked === true); if (!timeout) setTimeout("900"); }} />Unlimited</label></div></div>
    <div className="space-y-2"><Field label="Concurrent tasks">{(id) => <Input id={id} type="number" min={1} max={8} step={1} value={selected?.kind === "api" ? concurrency : "1"} required disabled={action.isPending || selected?.kind !== "api"} onChange={(event) => setConcurrency(event.target.value)} aria-describedby="security-concurrency-help" />}</Field>
      <p id="security-concurrency-help" className="text-xs leading-relaxed text-muted-foreground">{selected?.kind === "api" ? "Run 1–8 file analyses or independent finding assessments at a time. Related findings are assessed in order." : "Personal connections run one task at a time."}{retry ? " Completed work is reused when its inputs match; the saved model and limits stay the same." : selected?.kind === "api" ? " Defaults to the repository concurrency setting." : ""}</p></div>
    <p className="text-xs leading-relaxed text-muted-foreground">Concurrent tasks share the remaining token budget. Budgets stop further work between requests; they are not a guaranteed billing cap. Finished reports grade confirmed findings; incomplete coverage remains visible.</p>
    {selected?.kind !== "api" && selected && <p className="text-xs leading-relaxed text-muted-foreground">Authorize this repository on your personal connection in <Link className="underline" to="/settings" onClick={onClose}>AI connection settings</Link> before scanning.</p>}
    <ErrorNotice error={action.error} />
    {repository.can_write === false && <p className="text-sm text-muted-foreground">Repository write access is required to run security scans.</p>}
    <DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" disabled={action.isPending || !selected || !model.trim() || repository.can_write === false}><Play aria-hidden="true" className="size-4" />{action.isPending ? "Queuing scan…" : retry ? "Retry scan" : "Run scan"}</Button></DialogFooter>
  </form>;
}

export function SecurityRepository({ user }: { user: User }) {
  const { id } = useParams();
  const query = useApi<SecurityRepo>(`/security/repositories/${id}`, true);
  const [tab, setTab] = useUrlTab("tab", ["findings", "history"], "findings");
  const [scanning, setScanning] = useState(false);
  const [creatingIssues, setCreatingIssues] = useState(false);
  const [queuedIssues, setQueuedIssues] = useState(0);
  const [params, setParams] = useSearchParams();
  if (query.isPending) return <Loading />;
  if (query.error || !query.data) return <ErrorNotice error={query.error || "Repository unavailable."} />;
  const { repository: repo, summary } = query.data;
  const slash = repo.full_name.lastIndexOf("/");
  const Visibility = repo.private ? LockKeyhole : Globe;
  return <>
    <PageTitle title={<span className="break-words [overflow-wrap:anywhere]"><span className="font-normal text-muted-foreground">{repo.full_name.slice(0, slash + 1)}</span>{repo.full_name.slice(slash + 1)}</span>} actions={query.data.can_write !== false && <div className="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" onClick={() => { setQueuedIssues(0); setCreatingIssues(true); }}>Create GitHub issues…</Button>
      <Button size="sm" className="min-h-11 sm:min-h-8" disabled={active(summary.latest_status ?? "")} onClick={() => setScanning(true)}><Play aria-hidden="true" className="size-3.5" />Run scan</Button>
    </div>}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground"><span className="inline-flex items-center gap-1.5"><Visibility aria-hidden="true" className="size-3.5" />{repo.private ? "Private repository" : "Public repository"}</span><SecurityGrade showLabel repositoryId={repo.id} summary={summary} /><a className={`inline-flex min-h-6 items-center gap-1 ${metadataLink}`} href={`https://github.com/${repo.full_name}`} target="_blank" rel="noreferrer">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a></div>
      <p className="mt-2 text-xs text-muted-foreground">{summary.graded_at ? `${summary.state === "current" || (summary.state === "incomplete" && summary.latest_status === "partial" && summary.grade_scan_id === summary.latest_scan_id) ? "Saved" : "Historical report saved"} ${date(summary.graded_at)} for ${summary.grade_sha?.slice(0, 7)}.` : "A finished report establishes the first grade."}{summary.checked_at ? ` Freshness checked ${date(summary.checked_at)}.` : ""}</p>
    </PageTitle>
    {queuedIssues > 0 && <p role="status" className="mb-4 text-sm">{queuedIssues} GitHub {queuedIssues === 1 ? "issue" : "issues"} queued. Publication status is shown on each finding.</p>}
    {summary.latest_scan_id && <section aria-label="Latest security scan" className="mb-6 border-t pt-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Latest scan</span><SecurityStatus value={summary.latest_status ?? "unknown"} />
        <Link className={`inline-flex min-h-6 items-center gap-1 text-xs ${metadataLink}`} to={`/security/scans/${summary.latest_scan_id}`}>Inspect scan<ChevronRight aria-hidden="true" className="size-3.5" /></Link>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {summary.latest_at && <time dateTime={summary.latest_at}>{date(summary.latest_at)}</time>}
        {summary.head_sha && <span>Current commit · <code title={summary.head_sha}>{summary.head_sha.slice(0, 7)}</code></span>}
      </div>
    </section>}
    <Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="findings">Findings <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data.finding_count ?? "…"}</span></TabsTrigger><TabsTrigger value="history">Scan history <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data.scan_count ?? "…"}</span></TabsTrigger></TabsList>
      <TabsContent value="findings"><Findings repositoryId={repo.id} /></TabsContent>
      <TabsContent value="history"><ScanHistory repositoryId={repo.id} /></TabsContent>
    </Tabs>
    {scanning && <ScanDialog repositoryId={repo.id} user={user} onClose={() => setScanning(false)} />}
    {creatingIssues && <BulkIssueDialog key={repo.id} repositoryId={repo.id} onClose={() => setCreatingIssues(false)} onQueued={(total) => { setQueuedIssues(total); setCreatingIssues(false); }} />}
    <FindingSheet id={params.get("finding")} onClose={() => { const next = new URLSearchParams(params); next.delete("finding"); next.delete("finding_tab"); setParams(next); }} />
  </>;
}

const scanFindingResults = {
  new: "New findings", open_repeat: "Open repeats", resolved_repeat: "Resolved repeats",
  false_positive_repeat: "False-positive repeats", accepted_risk_repeat: "Accepted-risk repeats",
  repeated: "Repeats with unknown status", recorded: "Unclassified observations",
  fixed: "Verified fixes", not_observed: "Not observed", uncertain: "Verification uncertain",
};

function Findings({ repositoryId, scanId }: { repositoryId: number; scanId?: string }) {
  const { params, change, setParams } = useFilters();
  const searchInput = useRef<HTMLInputElement>(null);
  const ordering = normalizeOrdering(params.get("ordering"), findingOrderings, "-updated_at");
  const queryParams = new URLSearchParams();
  if (ordering !== "-updated_at") queryParams.set("ordering", ordering);
  for (const key of ["search", "severity", "kind", ...(!scanId ? ["state"] : [])]) for (const value of params.getAll(key)) queryParams.append(key, value);
  if (scanId) for (const value of params.getAll("outcome")) queryParams.append("outcome", value);
  const path = scanId ? `/security/scans/${scanId}/findings` : `/security/repositories/${repositoryId}/findings`;
  const { query, items, total, denied, resultCounts, pagination } = usePagedList<Finding>(`${path}${queryParams.size ? `?${queryParams}` : ""}`);
  const registered = Math.max(total, Object.values(resultCounts).reduce((sum, value) => sum + value, 0));
  const counts = query.data;
  return <div className="pt-4">
    {scanId && <p className="mb-4 text-xs leading-relaxed text-muted-foreground">New findings were first reported in this scan. Repeats were observed again; their labels show the current status. Fix verification outcomes remain separate. Saved observations stay unchanged.</p>}
    <div className="mb-4"><FilterBar query={params.get("search") ?? ""} searchLabel={scanId ? "Search scan findings" : "Search findings"} searchPlaceholder="Search findings…" searchInputRef={searchInput}
      ordering={{ value: ordering, defaultValue: "-updated_at", options: findingOrderings, onChange: (value) => change("ordering", value) }}
      onSearch={(value) => change("search", value)} onChange={change} filters={[
        ...(scanId ? [{ key: "outcome", label: "Result", values: params.getAll("outcome"), choices: Object.entries(scanFindingResults).map(([value, name]) => ({ value, name, count: resultCounts[value] ?? 0 })) }]
          : [{ key: "state", label: "State", values: params.getAll("state"), choices: ["open", "resolved", "false_positive", "accepted_risk"].map((value) => ({ value, name: label(value), count: counts?.state_counts?.[value] ?? 0 })) }]),
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: ["critical", "high", "medium", "low", "unknown"].map((value) => ({ value, name: label(value), count: counts?.severity_counts?.[value] ?? 0 })) },
        { key: "kind", label: "Type", values: params.getAll("kind"), choices: ["code", "dependency", "secret", "infrastructure"].map((value) => ({ value, name: label(value), count: counts?.kind_counts?.[value] ?? 0 })) },
      ]}>{scanId && <span role="status" className="sr-only">{total} of {registered} findings</span>}</FilterBar></div>
    {query.isPending ? <Loading /> : denied ? <ErrorNotice error={query.error} /> : !items.length && !query.error ? <Empty title={scanId && registered ? "No findings match these filters" : "No findings in this view"}>
      {scanId && registered ? <><p>Try another search, result, severity, or type.</p><Button variant="outline" className="mt-4" onClick={() => { const next = new URLSearchParams(params); for (const key of ["search", "outcome", "severity", "kind", "page"]) next.delete(key); setParams(next); searchInput.current?.focus(); }}>Clear filters</Button></> : "Findings will appear here as security scans complete. Change filters to inspect other states."}</Empty> :
      <section aria-label={scanId ? "Current findings for this scan" : "Repository findings"} className="divide-y overflow-hidden rounded-lg border bg-card"><div className="divide-y">{items.map((finding) => <FindingCard key={finding.id}
        evidence={{ path: finding.path, content: finding.title, severity: finding.severity, category: finding.kind }}
        state={<SecurityStatus value={finding.state} />} onClick={() => change("finding", finding.id)} />)}</div><Pagination {...pagination} label="findings" /></section>
    }
  </div>;
}

function ScanDuration({ scan }: { scan: Scan }) {
  const running = scan.status === "running" && !scan.finished_at;
  const [time, setTime] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setTime(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const duration = runDuration(scan, running ? time : undefined);
  return <span className="whitespace-nowrap tabular-nums">{duration ?? (scan.started_at ? "Unavailable" : "Not started")}{running && duration && " elapsed"}</span>;
}

function ScanHistory({ repositoryId }: { repositoryId: number }) {
  const { query, items, denied, pagination } = usePagedList<Scan>(`/security/repositories/${repositoryId}/scans`);
  if (denied) return <ErrorNotice error={query.error} />;
  if (query.isPending) return <Loading />;
  return !items.length && !query.error ? <Empty title="No security scans yet">Run a scan to save findings, coverage, and a repository grade.</Empty> : <div className="pt-4"><div className="divide-y overflow-hidden rounded-lg border bg-card">
    <div aria-hidden="true" className="hidden grid-cols-[minmax(0,1fr)_8rem_7rem_7rem_1rem] gap-6 bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground md:grid"><span>Security scan</span><span>Duration</span><span>Grade</span><span>Status</span><span /></div>
    <div className="divide-y">{items.map((scan) => <Link key={scan.id} to={`/security/scans/${scan.id}`} data-slot="security-scan-row" className="group grid grid-cols-[minmax(0,1fr)_auto] items-center no-underline hover:no-underline gap-3 px-4 py-4 text-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring md:grid-cols-[minmax(0,1fr)_8rem_7rem_7rem_1rem] md:gap-6 md:px-5">
      <div className="min-w-0"><span className="font-medium group-hover:text-primary">{date(scan.created_at)}</span><span className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground"><span className="inline-flex items-center gap-1.5"><GitBranch aria-hidden="true" className="size-3.5" />{scan.branch}</span><span className="inline-flex items-center gap-1.5"><GitCommitHorizontal aria-hidden="true" className="size-3.5" />{scan.head_sha.slice(0, 7)}</span></span></div>
      <span className="text-xs text-muted-foreground"><span className="md:sr-only">Duration: </span><ScanDuration scan={scan} /></span>
      <span className="inline-flex w-fit" role={scan.grade ? "img" : undefined} aria-label={scan.grade ? `Saved security grade ${scan.grade}` : undefined}>{scan.grade ? <SecurityGradeShield grade={scan.grade} /> : <Badge variant="outline" className="text-muted-foreground">Unrated</Badge>}</span><SecurityScanStatus status={scan.status} /><ChevronRight aria-hidden="true" className="hidden size-4 text-muted-foreground md:block" />
    </Link>)}</div><Pagination {...pagination} label="scan history" /></div></div>;
}

export function SecurityScan({ user }: { user: User }) {
  const { id } = useParams();
  const query = useApi<Schema["SecurityScanDetail"]>(`/security/scans/${id}`, true);
  const action = useAction();
  const [retrying, setRetrying] = useState<"retry" | "new">();
  const [tab, setTab] = useUrlTab("tab", ["report", "findings", "assessment", "coverage", "activity"], "report");
  const [params, setParams] = useSearchParams();
  const assessmentTrigger = useRef<HTMLButtonElement | null>(null);
  const assessmentTab = useRef<HTMLButtonElement | null>(null);
  if (query.isPending) return <Loading />;
  if (query.error || !query.data) return <ErrorNotice error={query.error || "Security scan unavailable."} />;
  const { scan, repository: repo, can_write: canWrite } = query.data;
  const work = object(scan.progress.work);
  const report = object(scan.report);
  const reportCount = query.data.report_count ?? entries(report.findings).length;
  const reportProblems = (Array.isArray(report.problems) ? report.problems : [])
    .map((problem) => typeof problem === "string" ? problem : text(object(problem).reason))
    .filter((problem) => problem && problem !== scan.error);
  return <>
    <Link className={`mb-5 inline-flex min-h-6 max-w-full items-center gap-1.5 break-words text-xs text-muted-foreground ${metadataLink}`} to={`/security/repositories/${repo.id}`}><ArrowLeft aria-hidden="true" className="size-3.5 shrink-0" />{repo.full_name}</Link>
    <PageTitle title="Security scan" actions={<>
      {canWrite && active(scan.status) && <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={action.isPending} onClick={() => action.mutate({ path: `/security/scans/${scan.id}/cancel` })}>Cancel scan</Button>}
      {canWrite && ["partial", "failed", "cancelled", "blocked"].includes(scan.status) && <Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" onClick={() => setRetrying("retry")}><RefreshCw aria-hidden="true" className="size-3.5" />Retry scan</Button>}
      {canWrite && !active(scan.status) && <Button size="sm" className="min-h-11 sm:min-h-8" onClick={() => setRetrying("new")}><Play aria-hidden="true" className="size-3.5" />Rescan repository</Button>}
    </>}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">{scan.grade && <SecurityBadge description={scan.status === "partial" ? "Grade based on confirmed findings." : "The grade saved with this scan. Current repository risk is shown separately."}><span className="sr-only">Security grade {scan.grade}</span><SecurityGradeShield grade={scan.grade} /></SecurityBadge>}<SecurityScanStatus status={scan.status} /><span className="inline-flex items-center gap-1.5 font-mono"><GitBranch aria-hidden="true" className="size-3.5" />{scan.branch}</span><span className="inline-flex items-center gap-1.5 font-mono"><GitCommitHorizontal aria-hidden="true" className="size-3.5" />{scan.head_sha.slice(0, 7)}</span><time dateTime={scan.created_at}>{date(scan.created_at)}</time></div>
      <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"><span>Duration: <ScanDuration scan={scan} /></span><span>{count(work.analysis_completed)} of {count(work.analysis_total)} source files analyzed · {count(work.completed)} completed work units · {count(work.failed)} failed · {count(scan.progress.tokens)} tokens</span></p>
    </PageTitle>
    <ErrorNotice error={scan.error || action.error} />
    <div className="my-5 flex flex-wrap gap-x-4 gap-y-2 border-y py-3 text-xs" aria-label="Scan stages">{Object.entries(scan.stages).map(([name, raw]) => {
      const stage = object(raw);
      const [label, description] = stageDescriptions[name] ?? [words(name), "Saved stage status for this scan."];
      return <div role="group" aria-label={`${label} stage`} key={name} className="flex min-h-8 items-center gap-2"><span className="font-medium">{label}</span><SecurityStatus value={text(stage.status) || "pending"} description={description} />{typeof stage.total === "number" && <span className="tabular-nums text-muted-foreground">{count(stage.completed)} / {count(stage.total)}{name === "analysis" && " files"}</span>}</div>;
    })}</div>
    <Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full flex-wrap group-data-[orientation=horizontal]/tabs:h-auto"><TabsTrigger value="report">Report <span className="text-xs font-normal tabular-nums text-muted-foreground">{reportCount}</span></TabsTrigger><TabsTrigger value="findings">Findings <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data.finding_count}</span></TabsTrigger><TabsTrigger ref={assessmentTab} value="assessment">Assessment <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data.assessment?.total ?? "…"}</span></TabsTrigger><TabsTrigger value="coverage">Coverage</TabsTrigger><TabsTrigger value="activity">Activity <span className="text-xs font-normal tabular-nums text-muted-foreground">{query.data.activity_count}</span></TabsTrigger></TabsList>
      <TabsContent value="report"><div className="space-y-6 pt-4">
        {Object.keys(report).length ? <><div className="flex flex-wrap gap-2">{(["markdown", "json"] as const).map((format) => <Button variant="outline" size="sm" asChild key={format}><a href={`/api/v1/security/scans/${scan.id}/report?format=${format}`} download><Download aria-hidden="true" className="size-4" />Download {format === "json" ? "JSON" : "Markdown"}</a></Button>)}</div>
          <p className="text-xs leading-relaxed text-muted-foreground">Observations saved at <span className="font-mono">{scan.head_sha.slice(0, 7)}</span>. Open a finding to inspect its saved evidence and current status separately.</p>
          <ReportFindings scanId={scan.id} registeredTotal={reportCount} onOpen={(id) => { const next = new URLSearchParams(params); next.set("finding", id); next.delete("finding_tab"); setParams(next); }} />
          {entries(report.unsettled_candidates).length > 0 && <section className="space-y-3"><h2 className="text-sm font-semibold">Requires verification</h2>{entries(report.unsettled_candidates).map((row, index) => <details className={disclosure} key={index}><summary className={disclosureSummary}><span className="min-w-0 break-words [overflow-wrap:anywhere]">{text(object(row.proposal).title) || "Security candidate"}</span><ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary><div className="mt-5 space-y-6"><section className="space-y-3"><h3 className="text-sm font-semibold">Assessment</h3><Markdown>{text(object(row.assessment).explanation)}</Markdown></section><SecurityEvidence title={text(object(row.proposal).title)} evidence={object(row.proposal)} commit={scan.head_sha} /></div></details>)}</section>}
          {reportProblems.length > 0 && <section><h2 className="mb-3 text-base font-semibold">Incomplete analysis</h2><ul className="space-y-2">{reportProblems.map((problem, index) => <li key={index}><ErrorNotice error={problem} /></li>)}</ul></section>}
        </> : <Empty title={active(scan.status) ? "Scan in progress" : "Report unavailable"}>Validated findings and the report are saved as the scan progresses. Coverage stays visible when analysis is incomplete.</Empty>}
      </div></TabsContent>
      <TabsContent value="findings"><Findings repositoryId={repo.id} scanId={scan.id} /></TabsContent>
      <TabsContent value="assessment"><SecurityAssessments scan={scan} summary={query.data.assessment} onOpen={(id, trigger) => { assessmentTrigger.current = trigger; const next = new URLSearchParams(params); next.set("assessment", id); next.delete("assessment_tab"); setParams(next); }} /></TabsContent>
      <TabsContent value="coverage"><Coverage scan={scan} /></TabsContent>
      <TabsContent value="activity"><ScanActivity scanId={scan.id} /></TabsContent>
    </Tabs>
    {retrying && <ScanDialog repositoryId={repo.id} user={user} onClose={() => setRetrying(undefined)} retry={retrying === "retry" ? scan : undefined} />}
    <FindingSheet id={params.get("finding")} scanId={scan.id} headSha={scan.head_sha} onClose={() => { const next = new URLSearchParams(params); next.delete("finding"); next.delete("finding_tab"); setParams(next); }} />
    <SecurityAssessmentSheet scan={scan} revision={query.data.assessment?.revision ?? ""} tick={query.dataUpdatedAt} id={params.get("assessment")} returnTo={assessmentTrigger.current?.isConnected ? assessmentTrigger.current : assessmentTab.current}
      onClose={() => { const next = new URLSearchParams(params); next.delete("assessment"); next.delete("assessment_tab"); setParams(next); }}
      onOpenFinding={(id) => { const next = new URLSearchParams(params); next.delete("assessment"); next.delete("assessment_tab"); next.set("finding", id); setParams(next); }} />
  </>;
}

function ReportFindings({ scanId, registeredTotal, onOpen }: { scanId: string; registeredTotal: number; onOpen: (id: string) => void }) {
  const [params, setParams] = useSearchParams();
  const searchInput = useRef<HTMLInputElement>(null);
  const query = params.get("report_q") ?? "";
  const ordering = normalizeOrdering(params.get("report_ordering"), snapshotOrderings, "position");
  const request = new URLSearchParams();
  if (ordering !== "position") request.set("ordering", ordering);
  for (const key of ["q", "outcome", "severity", "kind"]) for (const value of params.getAll(`report_${key}`)) request.append(key, value);
  const { query: pages, items, total, denied, pagination } = usePagedList<Record<string, unknown> & { id: string }>(`/security/scans/${scanId}/report/findings?${request}`);
  const data = pages.data;
  const filters = [{ key: "outcome", name: "Result", counts: data?.outcome_counts ?? {} },
    { key: "severity", name: "Severity", counts: data?.severity_counts ?? {} },
    { key: "kind", name: "Type", counts: data?.kind_counts ?? {} }].map(({ key, name, counts }) => {
    const values = key === "severity" ? [...new Set([...findingSeverities, ...Object.keys(counts)])] : Object.keys(counts).sort();
    return { key, label: name, values: params.getAll(`report_${key}`), searchPlaceholder: key === "kind" ? "Search types…" : undefined,
      choices: values.map((value) => ({ value, name: label(value), count: counts[value] ?? 0 })) };
  });
  function change(key: string, values: string[]) {
    const next = new URLSearchParams(params);
    next.delete(`report_${key}`);
    for (const value of values) if (value) next.append(`report_${key}`, value);
    setParams(next, { replace: key === "q" });
  }
  if (denied) return <ErrorNotice error={pages.error} />;
  return <div className="space-y-4">
    <FilterBar query={query} searchLabel="Search report findings" searchPlaceholder="Search report findings…" searchInputRef={searchInput}
      ordering={{ value: ordering, defaultValue: "position", options: snapshotOrderings, onChange: (value) => change("ordering", [value]) }}
      onSearch={(value) => change("q", [value])} onChange={change} filters={filters}>
      <span role="status" className="sr-only">{total} of {data?.registered_total ?? registeredTotal} report findings</span>
    </FilterBar>
    {pages.isPending ? <Loading /> : items.length ? <section aria-label="Findings at this scan" className="divide-y overflow-hidden rounded-lg border bg-card"><div className="divide-y">{items.map((row) => <FindingCard key={text(row.finding_id)}
      evidence={{ path: row.path, content: row.title, severity: row.severity, category: row.kind }}
      state={<SecurityStatus value={text(row.outcome) || "unknown"} />} onClick={() => onOpen(text(row.finding_id))} />)}</div><Pagination {...pagination} label="report findings" /></section>
      : !pages.error && (registeredTotal ? <Empty title="No report findings match these filters"><p>Try another search, result, severity, or type.</p>
        <Button variant="outline" className="mt-4" onClick={() => {
          const next = new URLSearchParams(params);
          for (const key of ["q", "outcome", "severity", "kind"]) next.delete(`report_${key}`);
          setParams(next); searchInput.current?.focus();
        }}>Clear filters</Button>
      </Empty> : <Empty title="No confirmed findings recorded">Check coverage and any incomplete work before interpreting this result.</Empty>)}
    {!items.length && pages.error && <><ErrorNotice error={pages.error} /><Button variant="outline" size="sm" onClick={() => pages.refetch()}>Retry report findings</Button></>}
  </div>;
}

const assessmentOutcomes = [
  { value: "confirmed", name: "Validated finding" }, { value: "rejected", name: "Not supported" },
  { value: "pending", name: "Awaiting assessment" }, { value: "uncertain", name: "Uncertain" },
  { value: "failed", name: "Failed" }, { value: "interrupted", name: "Interrupted" }, { value: "withheld", name: "Withheld" },
];

function AssessmentBadge({ item, inCard = false }: { item: SecurityAssessment; inCard?: boolean }) {
  return <AssessmentStatus inCard={inCard} candidate={{ status: item.status,
    finding_id: item.finding_id, is_new: item.finding_id ? !text(item.result?.finding_id) : false,
    result: item.result ? { verdict: text(item.result.verdict) } : null,
    progress_phase: item.progress_phase }} />;
}

function SecurityAssessments({ scan, summary, onOpen }: {
  scan: Scan; summary?: Schema["SecurityAssessmentSummary"];
  onOpen: (id: string, trigger: HTMLButtonElement) => void;
}) {
  const [params, setParams] = useSearchParams();
  const searchInput = useRef<HTMLInputElement>(null);
  const ordering = normalizeOrdering(params.get("assessment_ordering"), snapshotOrderings, "position");
  const filters = new URLSearchParams();
  if (ordering !== "position") filters.set("ordering", ordering);
  for (const key of ["q", "outcome", "severity", "category"]) for (const value of params.getAll(`assessment_${key}`)) filters.append(key, value);
  const { query, items, denied, pagination } = usePagedList<SecurityAssessment>(
    `/security/scans/${scan.id}/assessments?${filters}`, true, summary?.revision,
  );
  function change(key: string, values: string[]) {
    const next = new URLSearchParams(params);
    next.delete(`assessment_${key}`);
    for (const value of values) if (value) next.append(`assessment_${key}`, value);
    setParams(next, { replace: key === "q" });
  }
  if (denied) return <ErrorNotice error={query.error} />;
  const data = query.data;
  return <div className="mt-6 space-y-4">
    {summary && <p role="status" aria-label="Assessment progress" className="text-xs tabular-nums text-muted-foreground">{summary.status_counts?.queued ?? 0} queued · {summary.status_counts?.running ?? 0} running · {summary.status_counts?.completed ?? 0} completed · {summary.status_counts?.failed ?? 0} failed{(summary.status_counts?.interrupted ?? 0) > 0 && ` · ${summary.status_counts?.interrupted} interrupted`}</p>}
    <FilterBar query={filters.get("q") ?? ""} searchLabel="Search assessments" searchPlaceholder="Search assessments…" searchInputRef={searchInput}
      ordering={{ value: ordering, defaultValue: "position", options: { ...snapshotOrderings, position: "Assessment order" }, onChange: (value) => change("ordering", [value]) }}
      onSearch={(value) => change("q", [value])} onChange={change} filters={[
        { key: "outcome", label: "Result", values: filters.getAll("outcome"), choices: assessmentOutcomes.map((choice) => ({ ...choice, count: data?.outcome_counts?.[choice.value] ?? 0 })) },
        { key: "severity", label: "Severity", values: filters.getAll("severity"), choices: findingSeverities.map((value) => ({ value, name: label(value), count: data?.severity_counts?.[value] ?? 0 })) },
        { key: "category", label: "Type", values: filters.getAll("category"), choices: Object.entries(data?.category_counts ?? {}).map(([value, count]) => ({ value, name: label(value), count })), searchPlaceholder: "Search types…" },
      ]}>
      {data && <span role="status" className="sr-only">{data.total} of {data.registered_total} assessments</span>}
    </FilterBar>
    <section aria-label="Security assessments" className={items.length ? "flex flex-col divide-y overflow-hidden rounded-lg border bg-card" : undefined}>
      {!data && query.isPending ? <Loading /> : items.length ? items.map((item) => <FindingCard key={item.id}
        evidence={{ ...item.proposal, content: item.proposal.title }} state={<AssessmentBadge item={item} inCard />}
        onClick={(event) => onOpen(item.id, event.currentTarget)} />) : !query.error && <Empty title={data?.registered_total ? "No assessments match these filters" : "No candidates to assess"}>
        <p>{data?.registered_total ? "Try another search, result, severity, or type." : active(scan.status) ? "Candidates appear after file analysis finishes." : "No candidates were produced for assessment."}</p>
        {!!data?.registered_total && <Button variant="outline" className="mt-4" onClick={() => { const next = new URLSearchParams(params); for (const key of ["q", "outcome", "severity", "category"]) next.delete(`assessment_${key}`); setParams(next); searchInput.current?.focus(); }}>View all assessments</Button>}
      </Empty>}
      <Pagination {...pagination} label="assessments" />
    </section>
  </div>;
}

function SecurityAssessmentSheet({ scan, revision, tick, id, returnTo, onClose, onOpenFinding }: {
  scan: Scan; revision: string; tick: number; id: string | null; returnTo: HTMLElement | null;
  onClose: () => void; onOpenFinding: (id: string) => void;
}) {
  const focus = useReturnFocus(returnTo);
  const query = useRunResource<SecurityAssessment>(`/security/scans/${scan.id}/assessments/${encodeURIComponent(id ?? "")}`, revision, tick, { enabled: id !== null });
  return <Sheet open={id !== null} onOpenChange={(open) => { if (!open) onClose(); }}><SheetContent {...focus} className="w-full gap-0 overflow-hidden sm:max-w-2xl">
    <SheetHeader className="shrink-0 px-6 pb-4 pt-5"><SheetTitle>Assessment details</SheetTitle><SheetDescription className="sr-only">Inspect the proposed security finding, assessment decision, and evidence.</SheetDescription></SheetHeader>
    {query.error && [401, 403, 404].includes(query.error.status) ? <div className="px-6"><ErrorNotice error={query.error} /></div>
      : query.data ? <SecurityAssessmentContents key={id} item={query.data} scan={scan} error={query.error} reload={() => query.refetch()} onOpenFinding={onOpenFinding} />
      : <div className="px-6">{query.error ? <><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Reload assessment</Button></> : <Loading />}</div>}
  </SheetContent></Sheet>;
}

function SecurityAssessmentContents({ item, scan, error, reload, onOpenFinding }: {
  item: SecurityAssessment; scan: Scan; error: Error | null; reload: () => void; onOpenFinding: (id: string) => void;
}) {
  const [tab, setTab] = useUrlTab("assessment_tab", ["assessment", "evidence"], "assessment");
  const proposal = item.proposal;
  const result = object(item.result);
  const evidence = entries(result.evidence);
  const validating = item.progress_phase === "validating";
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="shrink-0 space-y-3 px-6 pb-5"><p className="break-words font-mono text-xs [overflow-wrap:anywhere]">{text(proposal.path) || "Path not recorded"}</p><div className="flex flex-wrap items-center gap-2"><SecurityStatus value={text(proposal.severity) || "unknown"} /><FindingCategory value={text(proposal.category)} /></div></div>
    <div className="flex shrink-0 items-start gap-3 border-y bg-muted/50 px-6 py-4"><ClipboardCheck aria-hidden="true" className="mt-0.5 size-[18px] shrink-0 text-muted-foreground" /><div className="min-w-0 space-y-1"><div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Assessment result</span><AssessmentBadge item={item} /></div><p className="text-xs leading-relaxed text-muted-foreground">Reviewed commit <span className="font-mono">{scan.head_sha.slice(0, 7)}</span>. Finding resolution is tracked separately.</p></div></div>
    <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0"><div className="shrink-0 px-6 pt-5"><TabsList aria-label="Assessment information"><TabsTrigger autoFocus={tab === "assessment"} value="assessment">Assessment</TabsTrigger><TabsTrigger autoFocus={tab === "evidence"} value="evidence">Evidence <span className="text-xs font-normal tabular-nums text-muted-foreground">{evidence.length}</span></TabsTrigger></TabsList></div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" data-slot="assessment-scroll">
        <ErrorNotice error={error} />{error && <Button variant="outline" size="sm" onClick={reload}>Reload assessment</Button>}
        <TabsContent value="assessment" className="space-y-6">
          <ErrorNotice error={item.error} />
          {validating ? <p role="status" className="text-sm text-muted-foreground">Response received; validating against the current source and finding history.</p>
            : item.result ? <section className="space-y-3"><h3 className="text-sm font-semibold">{item.finding_id ? "Why it was validated" : item.outcome === "rejected" ? "Why it was not supported" : "Why it was withheld"}</h3><Markdown>{text(result.explanation)}</Markdown></section>
            : !item.error && <p role={item.outcome === "pending" ? "status" : undefined} className="text-sm text-muted-foreground">{item.status === "running" ? "Assessment is being checked against source evidence and finding history." : item.status === "queued" ? "Assessment is waiting for the security worker." : "No assessment result was recorded."}</p>}
          <p className="text-xs text-muted-foreground">{item.tokens == null ? "Token usage not recorded." : `${item.tokens.toLocaleString()} tokens used.`}{item.reused && " Reused from a previous scan."}</p>
        </TabsContent>
        <TabsContent value="evidence" className="space-y-6"><section className="space-y-3"><h3 className="text-sm font-semibold">Supporting evidence</h3><p className="text-xs text-muted-foreground">Saved from the reviewed commit <span className="font-mono">{scan.head_sha.slice(0, 7)}</span>.</p></section>
          {evidence.length ? evidence.map((citation, index) => <section className="space-y-3" key={index}><p className="break-words font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{text(citation.revision) === "original" ? "Original" : "Current"} evidence: {text(citation.path)}:{count(citation.start_line)}–{count(citation.end_line)}</p><Code path={text(citation.path)}>{text(citation.quote)}</Code></section>) : <p className="text-sm text-muted-foreground">{item.outcome === "pending" ? "Evidence will appear when assessment completes." : "No supporting evidence was recorded."}</p>}
        </TabsContent>
        <details className="mt-6 border-t pt-4 text-sm"><summary className="cursor-pointer font-medium">Original proposal</summary><div className="mt-5 space-y-6"><SecurityEvidence title={text(proposal.title) || "Security candidate"} evidence={proposal} commit={scan.head_sha} /></div></details>
      </div>
    </Tabs>
    <SheetFooter className="shrink-0 border-t px-6 py-4"><div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between"><p className="text-xs leading-relaxed text-muted-foreground">{item.finding_id ? "Finding resolution is tracked separately." : "Not linked to a finding."}</p>{item.finding_id ? <Button className="min-h-11 sm:min-h-9" onClick={() => onOpenFinding(item.finding_id!)}><ArrowUpRight aria-hidden="true" />Open finding</Button> : <SheetClose asChild><Button variant="outline" className="min-h-11 sm:min-h-9">Close</Button></SheetClose>}</div></SheetFooter>
  </div>;
}

function Coverage({ scan }: { scan: Scan }) {
  const scanner = object(scan.coverage.trivy);
  return <div className="space-y-6 pt-5"><p className="text-sm leading-relaxed text-muted-foreground">Coverage records prepared source files and scanner targets. A completed process does not establish that every possible vulnerability was detected.</p>
    <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">Prepared files</dt><dd className="mt-1 tabular-nums">{count(scan.coverage.inventory_count)}</dd></div><div><dt className="text-muted-foreground">Trivy version</dt><dd className="mt-1">{text(scanner.version) || "Not recorded"}</dd></div><div><dt className="text-muted-foreground">Vulnerability database</dt><dd className="mt-1">{scanner.database_fresh === true ? "Within 24-hour freshness limit" : "Not verified"}</dd></div><div><dt className="text-muted-foreground">Scanner targets</dt><dd className="mt-1 tabular-nums">{Object.keys(object(scan.coverage.targets)).length}</dd></div></dl>
    {([ ["inventory", "Prepared source inventory"], ["excluded", "Excluded and unsupported files"], ["units", "Analysis work units"] ] as const).map(([section, title]) => <CoverageSection key={section} scanId={scan.id} section={section} title={title} />)}
    <CoverageRows scanId={scan.id} section="problems" />
  </div>;
}

function CoverageSection({ scanId, section, title }: { scanId: string; section: string; title: string }) {
  const [open, setOpen] = useState(false);
  return <details className={disclosure} onToggle={(event) => setOpen(event.currentTarget.open)}><summary className={disclosureSummary}>{title}<ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" /></summary>{open && <div className="mt-4"><CoverageRows scanId={scanId} section={section} /></div>}</details>;
}

function CoverageRows({ scanId, section }: { scanId: string; section: string }) {
  const { query, items, denied, pagination } = usePagedList<Record<string, unknown> & { id: string }>(`/security/scans/${scanId}/coverage/${section}`);
  if (denied) return <ErrorNotice error={query.error} />;
  return <>{query.isPending ? <Loading /> : <ul className="max-h-80 space-y-3 overflow-auto text-xs">{items.map((row) => <li key={row.id} className="flex flex-wrap justify-between gap-2">
    {section === "problems" ? <ErrorNotice error={`${text(row.path)}: ${text(row.reason)}`} /> : <><span className="break-words font-mono [overflow-wrap:anywhere]">{text(row.path) || text(row.task)}</span>
      {text(row.status) && <SecurityStatus value={text(row.status)} />}{text(row.reason) && <span className="w-full text-muted-foreground">{text(row.reason)}</span>}
      {text(row.error) && <div className="w-full"><ErrorNotice error={text(row.error)} /></div>}</>}
  </li>)}</ul>}<Pagination {...pagination} label={section === "inventory" ? "prepared files" : section === "units" ? "work units" : section === "excluded" ? "excluded files" : "coverage problems"} /></>;
}

function ScanActivity({ scanId }: { scanId: string }) {
  const { query, items, denied, pagination } = usePagedList<Schema["SecurityActivityOut"]>(`/security/scans/${scanId}/activity`);
  if (denied) return <ErrorNotice error={query.error} />;
  return query.isPending ? <Loading /> : <><Activity rows={items} /><Pagination {...pagination} label="activities" /></>;
}

function Activity({ rows }: { rows: Schema["SecurityActivityOut"][] }) {
  return <div className="pt-4"><p className="text-xs leading-relaxed text-muted-foreground">Recorded events, newest first. Current stage status is shown above.</p><ol className="divide-y">{rows.map((row) => {
    const stage = text(row.detail.stage);
    const status = text(row.detail.status);
    const transition = status === "running" ? "started" : status === "partial" ? "finished with incomplete work" : words(status) || "updated";
    return <li key={row.id} className="py-4"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-medium">{row.action === "scan_stage"
      ? `${stageDescriptions[stage]?.[0] ?? words(stage)} ${transition}` : label(row.action)}</p>{row.action !== "scan_stage" && status && <SecurityStatus value={status} />}</div><time dateTime={row.created_at} className="mt-1 block text-xs tabular-nums text-muted-foreground">{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(row.created_at))}</time>{text(row.detail.reason) && (row.action === "scan_failed" ? <div className="mt-2"><ErrorNotice error={text(row.detail.reason)} /></div> : <p className="mt-2 break-words text-sm">{text(row.detail.reason)}</p>)}</li>;
  })}</ol></div>;
}

function FindingHistory({ findingId }: { findingId: string }) {
  const observations = usePagedList<Record<string, unknown> & { id: string }>(`/security/findings/${findingId}/observations`);
  const activity = usePagedList<Schema["SecurityActivityOut"]>(`/security/findings/${findingId}/activity`);
  if (observations.denied || activity.denied) return <ErrorNotice error={observations.denied ? observations.query.error : activity.query.error} />;
  if (observations.query.isPending || activity.query.isPending) return <Loading />;
  return <>
          <section className="space-y-3"><h3 className="text-sm font-semibold">Observations</h3>{observations.items.length ? <ol className="space-y-5">{observations.items.map((row, index) => <li key={index} className="relative ml-1 border-l pl-5 before:absolute before:-left-1 before:top-1.5 before:size-[7px] before:rounded-full before:bg-muted-foreground"><Link className={`inline-flex min-h-6 items-center text-sm font-medium ${metadataLink}`} to={`/security/scans/${text(row.scan_id)}`}>{label(text(row.outcome))}<ChevronRight aria-hidden="true" className="size-3.5" /></Link><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{text(row.created_at) && date(text(row.created_at))}</p></li>)}</ol> : <p className="text-sm text-muted-foreground">No observations recorded.</p>}<Pagination {...observations.pagination} label="observations" /></section>
          <section className="space-y-3"><h3 className="text-sm font-semibold">Activity</h3>{activity.items.length ? <ol className="space-y-5">{activity.items.map((row) => <li key={row.id} className="relative ml-1 border-l pl-5 before:absolute before:-left-1 before:top-1.5 before:size-[7px] before:rounded-full before:bg-muted-foreground"><p className="text-sm font-medium">{label(row.action)}</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{row.actor_id === null ? "Code Mortis" : `Member #${row.actor_id}`} · {date(row.created_at)}</p>{text(row.detail.reason) && <details className="mt-2 text-sm"><summary className="cursor-pointer text-xs text-primary">View reason</summary><p className="mt-2 break-words leading-relaxed">{text(row.detail.reason)}</p></details>}</li>)}</ol> : <p className="text-sm text-muted-foreground">No activity recorded.</p>}<Pagination {...activity.pagination} label="finding activity" /></section>
  </>;
}

function SecurityEvidence({ title, evidence, commit }: { title: string; evidence: Record<string, unknown>; commit: string }) {
  return <>
    <section className="space-y-3"><h3 className="break-words text-sm font-semibold [overflow-wrap:anywhere]">{title}</h3><Markdown>{text(evidence.explanation)}</Markdown></section>
    {text(evidence.trigger) && <section className="space-y-3"><h3 className="text-sm font-semibold">Trigger</h3><Markdown>{text(evidence.trigger)}</Markdown></section>}
    {text(evidence.impact) && <section className="space-y-3"><h3 className="text-sm font-semibold">Impact</h3><Markdown>{text(evidence.impact)}</Markdown></section>}
    {entries(evidence.evidence).map((citation, index) => <section key={index} className="space-y-3"><p className="break-words font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{text(citation.path)}:{count(citation.start_line)}–{count(citation.end_line)}</p><Code path={text(citation.path)}>{text(citation.quote)}</Code></section>)}
    <section className="space-y-3"><h3 className="text-sm font-semibold">Remediation</h3><Markdown>{text(evidence.remediation)}</Markdown></section>
    <section className="space-y-3"><h3 className="text-sm font-semibold">Verification</h3><Markdown>{text(evidence.verification)}</Markdown></section>
    <p className="text-xs leading-relaxed text-muted-foreground">Evidence saved at <span className="font-mono">{commit.slice(0, 7)}</span>. Each scan keeps its original observations; absence from a later scan does not prove a fix.</p>
  </>;
}

function FindingSheet({ id, onClose, scanId, headSha }: { id: string | null; onClose: () => void; scanId?: string; headSha?: string }) {
  const focus = useReturnFocus();
  const query = useApi<Schema["SecurityFindingDetail"]>(`/security/findings/${id}`, true, !!id);
  const saved = useApi<Schema["SecurityReportPage"]>(`/security/scans/${scanId}/report/findings?finding_id=${encodeURIComponent(id ?? "")}`, false, !!id && !!scanId);
  const error = query.error || (scanId ? saved.error : null);
  return <Sheet open={!!id} onOpenChange={(open) => { if (!open) onClose(); }}><SheetContent {...focus} className="w-full gap-0 overflow-hidden sm:max-w-2xl">
    <SheetHeader className="shrink-0 px-6 pb-4 pt-5"><SheetTitle>Finding details</SheetTitle><SheetDescription className="sr-only">Inspect security evidence, current status, and resolution history.</SheetDescription></SheetHeader>
    {error ? <div className="px-6"><ErrorNotice error={error} /></div> : query.isPending || (scanId && saved.isPending) ? <div className="px-6"><Loading /></div> : query.data && <FindingContents key={id} detail={query.data} snapshot={saved.data?.items[0]} headSha={headSha} />}
  </SheetContent></Sheet>;
}

function FindingContents({ detail, snapshot, headSha }: { detail: Schema["SecurityFindingDetail"]; snapshot?: Record<string, unknown>; headSha?: string }) {
  const { finding, repository: repo, can_write: canWrite } = detail;
  const action = useAction();
  const evidence = finding.evidence;
  const [tab, setTab] = useUrlTab("finding_tab", snapshot ? ["snapshot", "evidence", "activity"] : ["evidence", "activity"], snapshot ? "snapshot" : "evidence");
  const activityTab = useRef<HTMLButtonElement>(null);
  const [triaging, setTriaging] = useState(false);
  const [state, setState] = useState<"open" | "false_positive" | "accepted_risk">("false_positive");
  const [reason, setReason] = useState("");
  const [issueOpen, setIssueOpen] = useState(false);
  const [syncApprovalOpen, setSyncApprovalOpen] = useState(false);
  const issue = finding.issue;
  const hasIssue = Object.keys(issue).length > 0;
  const sourcePath = tab === "snapshot" && snapshot ? text(snapshot.path) || finding.path : finding.path;
  const sourceCommit = tab === "snapshot" && snapshot ? headSha ?? text(object(snapshot.evidence).source_sha) : text(evidence.source_sha);
  const sourceUrl = `https://github.com/${repo.full_name}/blob/${sourceCommit}/${sourcePath.split("/").map(encodeURIComponent).join("/")}`;
  const path = text(snapshot?.path) || finding.path;
  const slash = path.lastIndexOf("/");
  const StatusIcon = finding.state === "resolved" ? CircleCheck : finding.state === "false_positive" ? CircleSlash : CircleDot;
  return <div className="flex min-h-0 flex-1 flex-col">
    <div className="shrink-0 space-y-3 px-6 pb-5">
      <div className="flex min-w-0 items-center gap-2.5"><FileCode2 aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><p className="min-w-0 break-words font-mono text-xs [overflow-wrap:anywhere]"><span className="text-muted-foreground">{path.slice(0, slash + 1)}</span><span className="font-medium">{path.slice(slash + 1)}</span></p></div>
      <div className="flex flex-wrap items-center gap-2"><SecurityStatus value={text(snapshot?.severity) || finding.severity} /><FindingCategory value={text(snapshot?.kind) || finding.kind} /></div>
    </div>
    <div className="grid shrink-0 grid-cols-[18px_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-y bg-muted/50 px-6 py-4 sm:grid-cols-[18px_minmax(0,1fr)_auto]" data-slot="security-finding-status">
      <StatusIcon className={`mt-0.5 size-[18px] ${finding.state === "resolved" ? "text-emerald-800 dark:text-emerald-300" : "text-muted-foreground"}`} aria-hidden="true" />
      <div className="space-y-1"><div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Current status</span><SecurityStatus value={finding.state} /></div><p className="text-xs leading-relaxed text-muted-foreground">Original scan evidence and resolution history are preserved.</p></div>
      <Button variant="link" size="sm" className="col-start-2 min-h-11 justify-self-start px-0 text-xs sm:col-start-auto sm:min-h-8" onClick={() => { setTab("activity"); activityTab.current?.focus(); }}>View activity</Button>
    </div>
    <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
      <div className="shrink-0 px-6 pt-5"><TabsList aria-label="Finding information" className={snapshot ? "max-w-full [&>button]:px-2 [&>button]:text-xs sm:[&>button]:text-sm" : ""}>{snapshot && <TabsTrigger value="snapshot">At this scan</TabsTrigger>}<TabsTrigger value="evidence">{snapshot ? "Current status" : "Evidence"}</TabsTrigger><TabsTrigger ref={activityTab} value="activity">Activity <span className="text-xs font-normal tabular-nums text-muted-foreground">{(detail.activity_count ?? detail.activity.length) + (detail.observation_count ?? detail.observations.length)}</span></TabsTrigger></TabsList></div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" data-slot="finding-scroll">
        {snapshot && <TabsContent value="snapshot" className="space-y-6"><div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span>Saved at <span className="font-mono">{headSha?.slice(0, 7)}</span></span><SecurityStatus value={text(snapshot.outcome) || "unknown"} /></div>
          {text(object(snapshot.verification).explanation) && <section aria-label="Fix verification" className="space-y-3"><h3 className="text-sm font-semibold">Fix verification</h3><Markdown>{text(object(snapshot.verification).explanation)}</Markdown></section>}
          <SecurityEvidence title={text(snapshot.title)} evidence={object(snapshot.evidence)} commit={headSha ?? text(object(snapshot.evidence).source_sha)} /></TabsContent>}
        <TabsContent value="evidence" className="space-y-6">
          <SecurityEvidence title={finding.title} evidence={evidence} commit={text(evidence.source_sha)} />
        </TabsContent>
        <TabsContent value="activity" className="space-y-6">
          <FindingHistory findingId={finding.id} />
        </TabsContent>
      </div>
    </Tabs>
    <SheetFooter className="max-h-[50dvh] shrink-0 gap-3 overflow-y-auto border-t px-6 py-4" data-slot="security-finding-actions">
      <ErrorNotice error={action.error || text(issue.error)} />
      {hasIssue && <div className="flex flex-wrap items-center gap-2 text-xs">{!text(issue.url) && <><span className="text-muted-foreground">Publication</span><SecurityStatus value={text(issue.publication_status) || "pending"} /></>}<span className="text-muted-foreground">GitHub sync</span><SecurityStatus value={text(issue.sync_status) || "pending"} />{text(issue.remote_state) && <Badge variant="outline" className="text-muted-foreground">GitHub issue {text(issue.remote_state)}</Badge>}</div>}
      {text(issue.remote_state) === "closed" && ["open", "accepted_risk"].includes(finding.state) && <p className="text-xs leading-relaxed text-muted-foreground">The GitHub issue is closed. This finding remains unresolved until a fix is verified.</p>}
      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
        <a href={text(issue.url) || sourceUrl} target="_blank" rel="noreferrer" className={`inline-flex min-h-11 w-full items-center gap-1 text-sm text-muted-foreground sm:min-h-9 sm:w-auto ${metadataLink}`}>{text(issue.url) ? `View issue #${count(issue.number)}` : "View source"}<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
        <div className="flex flex-wrap gap-2 sm:ml-auto">
          {canWrite && !hasIssue && ["open", "accepted_risk"].includes(finding.state) && <Button className="min-h-11 flex-1 sm:min-h-9 sm:flex-none" onClick={() => setIssueOpen(true)}>Create GitHub issue</Button>}
          {canWrite && hasIssue && !issue.number && (!issue.started || ["failed", "uncertain"].includes(text(issue.sync_status))) && ["open", "accepted_risk"].includes(finding.state) && <Button className="min-h-11 flex-1 sm:min-h-9 sm:flex-none" onClick={() => setIssueOpen(true)}>Review issue again</Button>}
          {canWrite && hasIssue && ["failed", "uncertain"].includes(text(issue.sync_status)) && <Button variant="outline" className="min-h-11 sm:min-h-9" disabled={action.isPending} onClick={() => action.mutate({ path: `/security/findings/${finding.id}/github-issue/sync` })}>Retry issue synchronization</Button>}
          {canWrite && !!issue.number && !!issue.operation_id && ["failed", "uncertain"].includes(text(issue.sync_status)) && <Button variant="outline" className="min-h-11 h-auto whitespace-normal sm:min-h-9" onClick={() => setSyncApprovalOpen(true)}>Review sync approval</Button>}
          {canWrite ? <Button variant="outline" className="min-h-11 sm:min-h-9" aria-expanded={triaging} onClick={() => setTriaging(!triaging)}>Triage</Button> : <SheetClose asChild><Button variant="outline" className="min-h-11 sm:min-h-9">Close</Button></SheetClose>}
        </div>
      </div>
      {canWrite && triaging && <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); action.mutate({ path: `/security/findings/${finding.id}`, method: "PATCH", body: { state, reason, version: finding.version } }, { onSuccess: () => { setReason(""); setTriaging(false); } }); }}>
        <Field label="Decision">{(id) => <select id={id} className="h-9 w-full rounded-md border border-input bg-card px-3 text-base md:text-sm" value={state} onChange={(event) => setState(event.target.value as typeof state)}><option value="false_positive">False positive</option><option value="accepted_risk">Accept risk</option><option value="open">Reopen finding</option></select>}</Field>
        <Field label="Reason">{(id) => <Textarea id={id} autoFocus required value={reason} maxLength={4000} onChange={(event) => setReason(event.target.value)} />}</Field>
        <p className="text-xs leading-relaxed text-muted-foreground">Accepted risks still affect the grade. Triage changes are reflected in the next complete scan.</p>
        <Button type="submit" variant="outline" className="min-h-11 sm:min-h-9" disabled={action.isPending || !reason.trim()}>Save decision</Button>
        {finding.kind === "secret" && ["open", "accepted_risk"].includes(finding.state) && <div className="space-y-3 border-t pt-4"><p className="text-sm">Removing a secret does not revoke it. Confirm rotation or revocation before a clean rescan can close this finding.</p>{finding.rotation ? <p className="text-xs text-muted-foreground">Rotation acknowledged. A successful rescan must still verify removal.</p> : <Button type="button" variant="outline" className="min-h-11 h-auto whitespace-normal sm:min-h-9" disabled={action.isPending || !reason.trim()} onClick={() => action.mutate({ path: `/security/findings/${finding.id}/rotation`, body: { reason, version: finding.version } })}>I rotated or revoked this credential</Button>}</div>}
      </form>}
    </SheetFooter>
    {issueOpen && <IssueDialog findingId={finding.id} onClose={() => setIssueOpen(false)} />}
    {syncApprovalOpen && canWrite && <SyncApprovalDialog finding={finding} repository={repo.full_name} onClose={() => setSyncApprovalOpen(false)} />}
  </div>;
}

function issueDraftError(title: string, body: string) {
  if (!title.trim()) return "Enter an issue title.";
  if (Array.from(title).length > 200) return "Issue titles must be 200 characters or fewer.";
  if (!body.trim()) return "Enter an issue body.";
  if (Array.from(body).length > 50000) return "Issue bodies must be 50,000 characters or fewer. Shorten the draft before publishing.";
  return null;
}

function SyncApprovalDialog({ finding, repository, onClose }: { finding: Finding; repository: string; onClose: () => void }) {
  const focus = useReturnFocus();
  const action = useAction<Finding>();
  const [reason, setReason] = useState("");
  const issue = finding.issue;
  const approval = object(issue.sync_approval);
  return <Dialog open onOpenChange={(open) => { if (!open && !action.isPending) onClose(); }}><DialogContent {...focus} className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
    <DialogHeader><DialogTitle>Approve issue synchronization</DialogTitle><DialogDescription>Review this existing issue before taking responsibility for its automatic status updates.</DialogDescription></DialogHeader>
    <form className="space-y-5" onSubmit={(event) => {
      event.preventDefault();
      if (!reason.trim() || Array.from(reason).length > 4000 || action.isPending) return;
      const body: Schema["SecurityIssueReapproval"] = { version: finding.version, operation_id: text(issue.operation_id),
        approval_revision: typeof approval.revision === "number" ? approval.revision : 0, reason };
      action.mutate({ path: `/security/findings/${finding.id}/github-issue/reapprove`, body }, { onSuccess: onClose });
    }}>
      <div className="space-y-2 text-sm"><p className="break-words">Repository: <strong>{repository}</strong></p>
        <a href={text(issue.url)} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-1 text-primary underline underline-offset-4">View issue #{count(issue.number)}<ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
        <p className="text-muted-foreground">Original publication approved by member #{count(issue.actor_id)}. Current synchronization approval: member #{count(approval.actor_id ?? issue.actor_id)}.</p></div>
      <p className="text-sm leading-relaxed">Your current repository write access will authorize synchronization. Code Mortis can close this issue after a verified fix and reopen it after a verified recurrence. Human closure overrides and current-branch verification still apply.</p>
      {text(issue.desired_state) && <p className="text-sm">Pending issue state: <strong>{text(issue.desired_state)}</strong>.</p>}
      <Field label="Reason for taking responsibility">{(id) => <Textarea id={id} value={reason} required maxLength={4000} disabled={action.isPending} onChange={(event) => setReason(event.target.value)} />}</Field>
      <ErrorNotice error={action.error} />
      <DialogFooter><Button type="button" variant="outline" disabled={action.isPending} onClick={onClose}>Cancel</Button><Button type="submit" disabled={action.isPending || !reason.trim() || Array.from(reason).length > 4000}>{action.isPending ? "Queuing synchronization…" : "Approve synchronization"}</Button></DialogFooter>
    </form>
  </DialogContent></Dialog>;
}

function BulkIssueDialog({ repositoryId, onClose, onQueued }: { repositoryId: number; onClose: () => void; onQueued: (total: number) => void }) {
  const focus = useReturnFocus();
  const action = useAction<Finding[]>();
  const path = `/security/repositories/${repositoryId}/github-issues`;
  const { query, items, total, denied, pagination } = usePagedList<IssueCandidate>(path, false);
  const [selected, setSelected] = useState<Record<string, IssueCandidate>>({});
  const [reviewing, setReviewing] = useState(false);
  const drafts = Object.values(selected);
  const preview = query.data as Schema["SecurityIssueCandidatePage"] | undefined;
  const isPublic = drafts[0]?.public ?? preview?.public;
  const draftError = drafts.map((draft) => issueDraftError(draft.title, draft.body)).find(Boolean);
  const invalid = !!draftError || drafts.some((draft) => draft.public !== isPublic);
  function choose(item: IssueCandidate, checked: boolean) {
    setSelected((current) => {
      const next = { ...current };
      if (checked && Object.keys(next).length < 100) next[item.id] = item; else if (!checked) delete next[item.id];
      return next;
    });
  }
  function edit(id: string, patch: Partial<Pick<IssueCandidate, "title" | "body">>) {
    setSelected((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }
  function submit() {
    if (!reviewing || !drafts.length || invalid || query.error || action.isPending) return;
    action.mutate({ path, body: { issues: drafts.map((draft) => ({ finding_id: draft.id, title: draft.title, body: draft.body, version: draft.version, public: draft.public })) } },
      { onSuccess: (findings) => onQueued(findings.length) });
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !action.isPending) onClose(); }}><DialogContent {...focus} className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-3xl">
    <DialogHeader><DialogTitle>Create GitHub issues</DialogTitle><DialogDescription>{reviewing ? "Review and edit the selected issue drafts before publishing." : "Select up to 100 findings. Resolved findings, false positives, and findings with an issue or pending publication are excluded."}</DialogDescription></DialogHeader>
    {preview && !denied && <div className="shrink-0 space-y-2 text-sm"><p className="break-words">Destination: <strong>{preview.repository}</strong></p>
      <p role="note" className={isPublic ? "rounded-md border border-destructive/30 bg-destructive/5 p-3 text-destructive" : "text-muted-foreground"}>{isPublic ? "These issues will be public. Review the evidence and details before publishing." : "These issues will be private, visible to people with repository access."}</p>
      <p role="status" className="tabular-nums">{drafts.length} {drafts.length === 1 ? "issue" : "issues"} selected{reviewing ? " for creation" : ` · ${total} eligible findings`}</p>
    </div>}
    <ErrorNotice error={query.error || action.error || (reviewing ? draftError : null)} />
    <div className="min-h-0 flex-1 overflow-y-auto">
      {query.isPending ? <Loading /> : denied ? null : reviewing ? <div className="space-y-3">{drafts.map((draft, index) => <details key={draft.id} open={index === 0} className={disclosure}>
        <summary className={disclosureSummary}><span className="min-w-0 break-words">{draft.title || "Untitled issue"}</span><ChevronDown aria-hidden="true" className="size-4 shrink-0" /></summary>
        <div className="mt-4 space-y-4"><p className="break-words font-mono text-xs text-muted-foreground">{draft.path}</p><SecurityStatus value={draft.severity} />
          <Field label="Issue title">{(id) => <Input id={id} value={draft.title} maxLength={200} required disabled={action.isPending} onChange={(event) => edit(draft.id, { title: event.target.value })} />}</Field>
          <Tabs defaultValue="preview"><TabsList><TabsTrigger value="preview">Preview</TabsTrigger><TabsTrigger value="edit">Edit</TabsTrigger></TabsList>
            <TabsContent value="preview"><Markdown>{draft.body}</Markdown></TabsContent>
            <TabsContent value="edit"><Field label="Issue body">{(id) => <Textarea id={id} value={draft.body} maxLength={50000} required className="min-h-48" disabled={action.isPending} onChange={(event) => edit(draft.id, { body: event.target.value })} />}</Field></TabsContent>
          </Tabs>
        </div>
      </details>)}</div> : !items.length && !query.error ? <Empty title="No eligible findings">Every finding is resolved, marked as a false positive, or already has an issue or pending publication.</Empty> : <>
        <div className="mb-3 flex flex-wrap gap-2"><Button variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={!items.length || !!query.error} onClick={() => setSelected((current) => {
          const next = { ...current };
          for (const item of items) { if (Object.keys(next).length >= 100) break; if (!next[item.id]) next[item.id] = item; }
          return next;
        })}>Select shown</Button><Button variant="ghost" size="sm" className="min-h-11 sm:min-h-8" disabled={!drafts.length} onClick={() => setSelected({})}>Clear selection</Button>
          <Button variant="ghost" size="sm" className="min-h-11 sm:min-h-8" onClick={() => { setSelected({}); action.reset(); void query.refetch(); }}>Refresh findings</Button></div>
        <div className="divide-y rounded-md border">{items.map((item) => <label key={item.id} className="flex min-h-11 cursor-pointer items-start gap-3 p-3">
          <Checkbox className="mt-0.5" aria-label={`Select ${item.title}`} checked={!!selected[item.id]} disabled={!selected[item.id] && drafts.length >= 100} onCheckedChange={(checked) => choose(item, checked === true)} />
          <span className="min-w-0 space-y-1"><span className="block break-words text-sm font-medium">{item.title}</span><span className="block break-words font-mono text-xs text-muted-foreground">{item.path}</span><SecurityStatus value={item.severity} /></span>
        </label>)}<Pagination {...pagination} label="findings" /></div>
      </>}
    </div>
    <DialogFooter className="shrink-0 border-t pt-4"><Button type="button" variant="outline" disabled={action.isPending} onClick={reviewing ? () => setReviewing(false) : onClose}>{reviewing ? "Back to selection" : "Cancel"}</Button>
      {reviewing ? <Button type="button" disabled={action.isPending || !drafts.length || invalid || !!query.error} onClick={submit}>{action.isPending ? "Queuing issues…" : `Create ${drafts.length} ${drafts.length === 1 ? "issue" : "issues"}`}</Button>
        : <Button type="button" disabled={!drafts.length || !!query.error || query.isPending} onClick={() => setReviewing(true)}>Review {drafts.length} {drafts.length === 1 ? "issue" : "issues"}</Button>}
    </DialogFooter>
  </DialogContent></Dialog>;
}

function IssueDialog({ findingId, onClose }: { findingId: string; onClose: () => void }) {
  const focus = useReturnFocus();
  const query = useApi<Schema["SecurityIssuePreview"]>(`/security/findings/${findingId}/github-issue`);
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent {...focus} className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>{query.data?.recovery_token ? "Retry GitHub issue creation" : "Create GitHub issue"}</DialogTitle><DialogDescription>Review the exact issue before publishing this finding.</DialogDescription></DialogHeader>
    <ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data && !query.error && <IssueForm key={`${query.data.version}:${query.data.recovery_token ?? ""}`} preview={query.data} findingId={findingId} onClose={onClose} />}
  </DialogContent></Dialog>;
}

function IssueForm({ preview, findingId, onClose }: { preview: Schema["SecurityIssuePreview"]; findingId: string; onClose: () => void }) {
  const action = useAction();
  const [title, setTitle] = useState(preview.title);
  const [body, setBody] = useState(preview.body);
  const [recoveryApproved, setRecoveryApproved] = useState(false);
  const draftError = issueDraftError(title, body);
  return <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); if (action.isPending || draftError || (preview.recovery_token && !recoveryApproved)) return; action.mutate({ path: `/security/findings/${findingId}/github-issue`, body: { title, body, version: preview.version, public: preview.public, ...(preview.recovery_token ? { recovery_token: preview.recovery_token } : {}) } }, { onSuccess: onClose }); }}>
    <p className="break-words text-sm">Destination: <strong>{preview.repository}</strong></p>
    {preview.public && <div role="note" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">This issue will be public. Check the evidence and details before posting.</div>}
    {preview.recovery_token && <div role="note" className="space-y-3 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"><p>The previous request has an unknown outcome. Retry synchronization and check GitHub first. Another attempt may create a duplicate if the earlier issue appears later.</p><label className="flex min-h-11 cursor-pointer items-start gap-3"><Checkbox className="mt-0.5" checked={recoveryApproved} onCheckedChange={(checked) => setRecoveryApproved(checked === true)} /><span>I checked GitHub and approve another attempt despite the risk of a duplicate issue.</span></label></div>}
    <Field label="Issue title">{(id) => <Input id={id} value={title} maxLength={200} required onChange={(event) => setTitle(event.target.value)} />}</Field>
    <Tabs defaultValue="preview"><TabsList><TabsTrigger value="preview">Preview</TabsTrigger><TabsTrigger value="edit">Edit</TabsTrigger></TabsList><TabsContent value="preview"><div className="max-h-80 overflow-y-auto rounded-md border p-4"><Markdown>{body}</Markdown></div></TabsContent><TabsContent value="edit"><Field label="Issue body">{(id) => <Textarea id={id} className="min-h-64" value={body} maxLength={50000} required onChange={(event) => setBody(event.target.value)} />}</Field></TabsContent></Tabs>
    <p className="text-xs leading-relaxed text-muted-foreground">Code Mortis will close its linked issue after a verified fix and reopen it if the same finding returns, provided no human override prevents synchronization.</p>
    <ErrorNotice error={action.error || draftError} /><DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" disabled={action.isPending || !!draftError || (!!preview.recovery_token && !recoveryApproved)}>{action.isPending ? "Queuing issue…" : preview.recovery_token ? "Approve another attempt" : "Create issue"}</Button></DialogFooter>
  </form>;
}
