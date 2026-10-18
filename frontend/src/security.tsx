import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, ArrowUpRight, Download, Globe, LockKeyhole, Play, RefreshCw, Square } from "lucide-react";
import { useAction, useApi, type Connection, type Schema } from "./api";
import { Code } from "./components/code";
import { date, Empty, ErrorNotice, Field, Loading, Markdown, PageTitle, Status, words } from "./components/common";
import { ModelPicker } from "./components/model-picker";
import { SearchSelect } from "./components/search-select";
import { SecurityGrade, securityStates } from "./components/security-grade";
import { Button } from "./components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Input } from "./components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { Textarea } from "./components/ui/textarea";
import { useUrlTab } from "./hooks/use-url-tab";

type User = Schema["UserOut"];
type Scan = Schema["SecurityScanOut"];
type Finding = Schema["SecurityFindingOut"];
type SecurityRepo = Schema["SecurityRepositoryOut"];
const active = (status: string) => ["queued", "running", "blocked"].includes(status);
const text = (value: unknown) => typeof value === "string" ? value : "";
const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const entries = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.map(object) : [];
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "Unknown";

function useReturnFocus() {
  const trigger = useRef<HTMLElement | null>(null);
  return {
    onOpenAutoFocus: () => { trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; },
    onCloseAutoFocus: (event: Event) => { event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus(); },
  };
}

function SeverityCounts({ counts }: { counts: Record<string, number> }) {
  const visible = ["critical", "high", "medium", "low", "unknown"].filter((key) => counts[key]);
  return <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">{visible.length
    ? visible.map((key) => <span key={key} className="tabular-nums">{counts[key]} {key}</span>)
    : "No confirmed unresolved findings"}</span>;
}

function Pagination({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (page: number) => void }) {
  return <nav aria-label="Security results pagination" className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
    <span>{total.toLocaleString()} results · Page {page} of {Math.max(1, Math.ceil(total / pageSize))}</span>
    <div className="flex gap-2"><Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>Previous</Button>
      <Button variant="outline" size="sm" disabled={page * pageSize >= total} onClick={() => onChange(page + 1)}>Next</Button></div>
  </nav>;
}

function useFilters() {
  const [params, setParams] = useSearchParams();
  function change(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (["search", "state", "severity", "kind", "grade", "status"].includes(key)) next.delete("page");
    setParams(next);
  }
  const parsed = Number(params.get("page"));
  return { params, change, page: Number.isSafeInteger(parsed) && parsed > 0 ? parsed : 1 };
}

export function SecurityOverview({ user }: { user: User }) {
  const query = useApi<SecurityRepo[]>("/security/repositories", true);
  const { params, change } = useFilters();
  const [selected, setSelected] = useState<number>();
  const search = params.get("search") ?? "";
  const grade = params.get("grade") ?? "";
  const status = params.get("status") ?? "";
  const rows = (query.data ?? []).filter((row) => row.repository.full_name.toLowerCase().includes(search.toLowerCase())
    && (!grade || (grade === "unrated" ? !row.summary.grade : row.summary.grade === grade))
    && (!status || row.summary.state === status)).sort((a, b) => a.repository.full_name.localeCompare(b.repository.full_name));
  return <>
    <PageTitle title="Security">Scan default branches, inspect supported findings, and verify fixes.</PageTitle>
    <ErrorNotice error={query.error} />
    {!query.error && <>
      <div className="mb-6 flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1"><Field label="Search repositories">{(id) => <Input id={id} type="search" value={search} onChange={(event) => change("search", event.target.value)} placeholder="Owner or repository" />}</Field></div>
        <Field label="Grade">{(id) => <select id={id} className="h-9 rounded-md border border-input bg-card px-3 text-base md:text-sm min-w-32" value={grade} onChange={(event) => change("grade", event.target.value)}><option value="">All grades</option>{["A", "B", "C", "D", "F"].map((value) => <option key={value}>{value}</option>)}<option value="unrated">Not rated</option></select>}</Field>
        <Field label="Scan status">{(id) => <select id={id} className="h-9 rounded-md border border-input bg-card px-3 text-base md:text-sm min-w-44" value={status} onChange={(event) => change("status", event.target.value)}><option value="">All statuses</option>{Object.entries(securityStates).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>}</Field>
      </div>
      {query.isPending ? <Loading /> : !rows.length ? <Empty title={query.data?.length ? "No matching repositories" : "No repositories available"}>{query.data?.length ? "Change the search or filters to see other repositories." : "Connect a repository through GitHub settings to start scanning."}</Empty> :
        <div className="divide-y border-y">{rows.map(({ repository: repo, summary }) => {
          const Visibility = repo.private ? LockKeyhole : Globe;
          return <article key={repo.id} className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div className="min-w-0 flex-1 basis-72"><Link className="break-words font-medium underline-offset-4 hover:underline" to={`/security/repositories/${repo.id}`}>{repo.full_name}</Link>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2"><span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><Visibility aria-hidden="true" className="size-3.5" />{repo.private ? "Private" : "Public"}</span><SecurityGrade repositoryId={repo.id} summary={summary} /></div>
              <div className="mt-2"><SeverityCounts counts={summary.counts ?? {}} /></div></div>
            <div className="flex flex-wrap items-center gap-4"><div className="text-xs text-muted-foreground">{summary.latest_scan_id ? <><Link to={`/security/scans/${summary.latest_scan_id}`} className="underline underline-offset-4">{words(summary.latest_status ?? "Saved scan")}</Link><span className="mt-1 block">{summary.latest_at && date(summary.latest_at)}</span></> : "No saved scans"}</div>
              <Button variant="outline" disabled={active(summary.latest_status ?? "")} onClick={() => setSelected(repo.id)}><Play aria-hidden="true" className="size-4" />Run scan</Button></div>
          </article>;
        })}</div>}
    </>}
    {selected !== undefined && <ScanDialog key={selected} repositoryId={selected} user={user} onClose={() => setSelected(undefined)} />}
  </>;
}

function ScanDialog({ repositoryId, user, onClose, retry }: { repositoryId: number; user: User; onClose: () => void; retry?: Scan }) {
  const focus = useReturnFocus();
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
  const requestKey = useRef(crypto.randomUUID());
  useEffect(() => { requestKey.current = crypto.randomUUID(); }, [connectionId, model, tokens, timeout, unlimitedTokens, unlimitedTimeout]);
  function submit(event: FormEvent) {
    event.preventDefault();
    action.mutate({ path: retry ? `/security/scans/${retry.id}/retry` : `/security/repositories/${repository.repository.id}/scans`, body: {
      connection_id: connectionId, model, token_budget: unlimitedTokens ? null : Number(tokens),
      timeout_seconds: unlimitedTimeout ? null : Number(timeout), request_key: requestKey.current,
    } }, { onSuccess: (scan) => { onClose(); navigate(`/security/scans/${scan.id}`); } });
  }
  return <form onSubmit={submit} className="space-y-5">
    <div className="rounded-md border bg-muted/40 p-3 text-sm"><p className="break-words font-medium">{repository.repository.full_name}</p><p className="mt-1 text-xs text-muted-foreground">Default branch: {repository.summary.default_branch ?? "Resolved from GitHub when the scan starts"}</p></div>
    <Field label="AI connection">{(id) => <SearchSelect id={id} value={connectionId} onChange={(value) => { setConnectionId(value); setModel((value === repository.repository.connection_id && repository.repository.settings.model) || connections.find((row) => row.id === value)?.model || ""); }} options={[{ value: "", label: "Choose a connection" }, ...connections.map((row) => ({ value: row.id, label: row.name }))]} required disabled={action.isPending || !!retry} />}</Field>
    <ModelPicker value={model} onChange={setModel} path={connectionId ? `/connections/${connectionId}/models?repository_id=${repository.repository.id}` : undefined} enabled={!!selected} disabled={action.isPending || !!retry} required />
    <div className="grid gap-4 sm:grid-cols-2"><div className="space-y-2"><Field label="Token budget">{(id) => <Input id={id} type="number" min={1000} max={5000000} step={1000} value={tokens} placeholder="Unlimited" required={!unlimitedTokens} disabled={action.isPending || !!retry || unlimitedTokens} onChange={(event) => setTokens(event.target.value)} />}</Field>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label="Unlimited token budget" checked={unlimitedTokens} disabled={action.isPending || !!retry} onChange={(event) => { setUnlimitedTokens(event.target.checked); if (!tokens) setTokens("200000"); }} />Unlimited</label></div>
      <div className="space-y-2"><Field label="Timeout (seconds)">{(id) => <Input id={id} type="number" min={60} max={3600} value={timeout} placeholder="Unlimited" required={!unlimitedTimeout} disabled={action.isPending || !!retry || unlimitedTimeout} onChange={(event) => setTimeout(event.target.value)} />}</Field>
      <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label="Unlimited timeout" checked={unlimitedTimeout} disabled={action.isPending || !!retry} onChange={(event) => { setUnlimitedTimeout(event.target.checked); if (!timeout) setTimeout("900"); }} />Unlimited</label></div></div>
    <p className="text-xs leading-relaxed text-muted-foreground">Token budgets stop further work between requests; they are not a guaranteed billing cap. Incomplete analysis will not replace the repository grade.</p>
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
  const [params, setParams] = useSearchParams();
  if (query.isPending) return <Loading />;
  if (query.error || !query.data) return <ErrorNotice error={query.error || "Repository unavailable."} />;
  const { repository: repo, summary } = query.data;
  return <>
    <PageTitle title={repo.full_name} actions={query.data.can_write !== false && <Button disabled={active(summary.latest_status ?? "")} onClick={() => setScanning(true)}><Play aria-hidden="true" className="size-4" />Run scan</Button>}>
      <div className="flex flex-wrap items-center gap-3"><SecurityGrade repositoryId={repo.id} summary={summary} /><a className="inline-flex items-center gap-1 underline underline-offset-4" href={`https://github.com/${repo.full_name}`} target="_blank" rel="noreferrer">View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></a></div>
      <div className="mt-3"><SeverityCounts counts={summary.counts ?? {}} /></div>
      <p className="mt-2 text-xs text-muted-foreground">{summary.graded_at ? `Grade saved ${date(summary.graded_at)} for ${summary.grade_sha?.slice(0, 7)}.` : "A complete scan establishes the first grade."}{summary.checked_at ? ` Freshness checked ${date(summary.checked_at)}.` : ""}</p>
    </PageTitle>
    <Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="findings">Findings</TabsTrigger><TabsTrigger value="history">Scan history</TabsTrigger></TabsList>
      <TabsContent value="findings"><Findings repositoryId={repo.id} /></TabsContent>
      <TabsContent value="history"><ScanHistory repositoryId={repo.id} /></TabsContent>
    </Tabs>
    {scanning && <ScanDialog repositoryId={repo.id} user={user} onClose={() => setScanning(false)} />}
    <FindingSheet id={params.get("finding")} onClose={() => { const next = new URLSearchParams(params); next.delete("finding"); setParams(next); }} />
  </>;
}

function Findings({ repositoryId, scanId }: { repositoryId: number; scanId?: string }) {
  const { params, change, page } = useFilters();
  const queryParams = new URLSearchParams({ page: String(page) });
  if (!scanId) for (const key of ["search", "state", "severity", "kind"]) { const value = params.get(key); if (value) queryParams.set(key, value); }
  const query = useApi<Schema["SecurityFindingPage"]>(scanId ? `/security/scans/${scanId}/findings?${queryParams}` : `/security/repositories/${repositoryId}/findings?${queryParams}`, true);
  return <div className="pt-4">
    {!scanId && <div className="mb-5 flex flex-wrap items-end gap-3"><div className="min-w-40 flex-1"><Field label="Search findings">{(id) => <Input id={id} type="search" value={params.get("search") ?? ""} onChange={(event) => change("search", event.target.value)} placeholder="Title or path" />}</Field></div>
      {([ ["state", "State", ["open", "resolved", "false_positive", "accepted_risk"]], ["severity", "Severity", ["critical", "high", "medium", "low", "unknown"]], ["kind", "Type", ["code", "dependency", "secret", "infrastructure"]] ] as const).map(([key, label, values]) => <Field label={label} key={key}>{(id) => <select id={id} className="h-9 rounded-md border border-input bg-card px-3 text-base md:text-sm" value={params.get(key) ?? ""} onChange={(event) => change(key, event.target.value)}><option value="">All</option>{values.map((value) => <option key={value} value={value}>{words(value)}</option>)}</select>}</Field>)}
    </div>}
    <ErrorNotice error={query.error} />
    {!query.error && (query.isPending ? <Loading /> : !query.data?.items.length ? <Empty title="No findings in this view">Findings will appear here as security scans complete. Change filters to inspect other states.</Empty> : <>
      <div className="divide-y border-y">{query.data.items.map((finding) => <button type="button" key={finding.id} className="block w-full rounded-sm py-4 text-left outline-offset-4 hover:bg-muted/40 focus-visible:outline-2 focus-visible:outline-ring" onClick={() => change("finding", finding.id)}>
        <div className="flex flex-wrap items-start justify-between gap-3"><span className="min-w-0 flex-1 break-words text-sm font-medium">{finding.title}</span><Status value={finding.state} /></div>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground"><span className={finding.severity === "critical" || finding.severity === "high" ? "font-medium text-destructive" : ""}>{words(finding.severity)}</span><span>{words(finding.kind)}</span><span className="break-all">{finding.path}</span></div>
      </button>)}</div><Pagination page={page} total={query.data.total} pageSize={query.data.page_size} onChange={(value) => change("page", String(value))} />
    </>)}
  </div>;
}

function ScanHistory({ repositoryId }: { repositoryId: number }) {
  const { page, change } = useFilters();
  const query = useApi<Schema["SecurityScanPage"]>(`/security/repositories/${repositoryId}/scans?page=${page}`, true);
  if (query.error) return <ErrorNotice error={query.error} />;
  if (query.isPending) return <Loading />;
  return !query.data?.items.length ? <Empty title="No security scans yet">Run a scan to save findings, coverage, and a repository grade.</Empty> : <div className="pt-4"><div className="divide-y border-y">{query.data.items.map((scan) => <Link key={scan.id} to={`/security/scans/${scan.id}`} className="flex flex-wrap items-center justify-between gap-3 py-4 underline-offset-4 hover:underline">
    <div className="min-w-0"><span className="text-sm font-medium">{date(scan.created_at)}</span><span className="mt-1 block break-all text-xs text-muted-foreground">{scan.branch} · {scan.head_sha.slice(0, 7)}</span></div><div className="flex items-center gap-3">{scan.grade && <span className="text-sm">Grade {scan.grade}</span>}<Status value={scan.status} /></div>
  </Link>)}</div><Pagination page={page} total={query.data.total} pageSize={query.data.page_size} onChange={(value) => change("page", String(value))} /></div>;
}

export function SecurityScan({ user }: { user: User }) {
  const { id } = useParams();
  const query = useApi<Schema["SecurityScanDetail"]>(`/security/scans/${id}`, true);
  const action = useAction();
  const [retrying, setRetrying] = useState(false);
  const [tab, setTab] = useUrlTab("tab", ["report", "findings", "coverage", "activity"], "report");
  const [params, setParams] = useSearchParams();
  if (query.isPending) return <Loading />;
  if (query.error || !query.data) return <ErrorNotice error={query.error || "Security scan unavailable."} />;
  const { scan, repository: repo, can_write: canWrite } = query.data;
  const work = object(scan.progress.work);
  const report = object(scan.report);
  const reportProblems = (Array.isArray(report.problems) ? report.problems : [])
    .map((problem) => typeof problem === "string" ? problem : text(object(problem).reason))
    .filter((problem) => problem && problem !== scan.error);
  return <>
    <Link className="mb-5 inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-4" to={`/security/repositories/${repo.id}`}><ArrowLeft aria-hidden="true" className="size-3.5" />{repo.full_name}</Link>
    <PageTitle title="Security scan" actions={<>
      {canWrite && active(scan.status) && <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate({ path: `/security/scans/${scan.id}/cancel` })}><Square aria-hidden="true" className="size-4" />Cancel scan</Button>}
      {canWrite && ["partial", "failed", "cancelled", "blocked"].includes(scan.status) && <Button variant="outline" onClick={() => setRetrying(true)}><RefreshCw aria-hidden="true" className="size-4" />Retry scan</Button>}
      {canWrite && !active(scan.status) && <Button onClick={() => setRetrying(true)}><Play aria-hidden="true" className="size-4" />Rescan repository</Button>}
    </>}>
      <div className="flex flex-wrap items-center gap-3"><Status value={scan.status} />{scan.grade && <span className="text-sm font-medium">Grade {scan.grade}</span>}<span className="text-xs">{scan.branch} · {scan.head_sha.slice(0, 7)} · {date(scan.created_at)}</span></div>
      <p className="mt-2 text-xs text-muted-foreground">{count(work.analysis_completed)} of {count(work.analysis_total)} source files analyzed · {count(work.completed)} completed work units · {count(work.failed)} failed · {count(scan.progress.tokens)} tokens</p>
    </PageTitle>
    <ErrorNotice error={scan.error || action.error} />
    <div className="my-5 flex flex-wrap gap-x-5 gap-y-3 border-y py-4 text-xs" aria-label="Scan stages">{Object.entries(scan.stages).map(([name, raw]) => <div className="flex items-center gap-2" key={name}><span>{words(name)}</span><Status value={text(object(raw).status) || "pending"} />{typeof object(raw).total === "number" && <span className="tabular-nums text-muted-foreground">{count(object(raw).completed)} / {count(object(raw).total)}</span>}</div>)}</div>
    <Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full flex-wrap"><TabsTrigger value="report">Report</TabsTrigger><TabsTrigger value="findings">Findings</TabsTrigger><TabsTrigger value="coverage">Coverage</TabsTrigger><TabsTrigger value="activity">Activity</TabsTrigger></TabsList>
      <TabsContent value="report"><div className="space-y-6 pt-4">
        {Object.keys(report).length ? <><div className="flex flex-wrap gap-2">{(["markdown", "json"] as const).map((format) => <Button variant="outline" size="sm" asChild key={format}><a href={`/api/v1/security/scans/${scan.id}/report?format=${format}`} download><Download aria-hidden="true" className="size-4" />Download {format === "json" ? "JSON" : "Markdown"}</a></Button>)}</div>
          <p className="text-sm text-muted-foreground">Saved evidence for this commit. Current finding state is available in each finding’s details.</p>
          {entries(report.findings).length ? entries(report.findings).map((row) => <section className="border-t pt-5" key={text(row.finding_id)}><div className="flex flex-wrap items-start justify-between gap-3"><h2 className="text-base font-semibold">{text(row.title)}</h2><span className="text-xs text-muted-foreground">{text(row.severity)} · {words(text(row.outcome))}</span></div><p className="mt-2 break-all text-xs text-muted-foreground">{text(row.path)}</p><div className="mt-4"><Markdown>{text(object(row.evidence).explanation)}</Markdown></div><h3 className="mt-5 mb-2 text-sm font-medium">Remediation</h3><Markdown>{text(object(row.evidence).remediation)}</Markdown><h3 className="mt-5 mb-2 text-sm font-medium">Verification</h3><Markdown>{text(object(row.evidence).verification)}</Markdown><Button variant="link" className="mt-3 px-0" onClick={() => { const next = new URLSearchParams(params); next.set("finding", text(row.finding_id)); setParams(next); }}>Inspect finding</Button></section>) : <Empty title="No confirmed findings recorded">Check coverage and any incomplete work before interpreting this result.</Empty>}
          {entries(report.unsettled_candidates).map((row, index) => <section className="border-t pt-5" key={index}><h2 className="text-base font-semibold">Requires verification: {text(object(row.proposal).title)}</h2><p className="my-2 break-all text-xs text-muted-foreground">{text(object(row.proposal).path)}</p><Markdown>{text(object(row.assessment).explanation)}</Markdown></section>)}
          {reportProblems.length > 0 && <section><h2 className="mb-3 text-base font-semibold">Incomplete analysis</h2><ul className="space-y-2">{reportProblems.map((problem, index) => <li key={index}><ErrorNotice error={problem} /></li>)}</ul></section>}
        </> : <Empty title={active(scan.status) ? "Scan in progress" : "Report unavailable"}>Validated findings and the report are saved as the scan progresses. Coverage stays visible when analysis is incomplete.</Empty>}
      </div></TabsContent>
      <TabsContent value="findings"><Findings repositoryId={repo.id} scanId={scan.id} /></TabsContent>
      <TabsContent value="coverage"><Coverage scan={scan} /></TabsContent>
      <TabsContent value="activity"><Activity rows={query.data.activity} /></TabsContent>
    </Tabs>
    {retrying && <ScanDialog repositoryId={repo.id} user={user} onClose={() => setRetrying(false)} retry={["partial", "failed", "cancelled", "blocked"].includes(scan.status) ? scan : undefined} />}
    <FindingSheet id={params.get("finding")} onClose={() => { const next = new URLSearchParams(params); next.delete("finding"); setParams(next); }} />
  </>;
}

function Coverage({ scan }: { scan: Scan }) {
  const inventory = object(scan.coverage.inventory);
  const scanner = object(scan.coverage.trivy);
  return <div className="space-y-6 pt-5"><p className="text-sm leading-relaxed text-muted-foreground">Coverage records prepared source files and scanner targets. A completed process does not establish that every possible vulnerability was detected.</p>
    <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">Prepared files</dt><dd className="mt-1 tabular-nums">{Object.keys(inventory).length}</dd></div><div><dt className="text-muted-foreground">Trivy version</dt><dd className="mt-1">{text(scanner.version) || "Not recorded"}</dd></div><div><dt className="text-muted-foreground">Vulnerability database</dt><dd className="mt-1">{scanner.database_fresh === true ? "Within 24-hour freshness limit" : "Not verified"}</dd></div><div><dt className="text-muted-foreground">Scanner targets</dt><dd className="mt-1 tabular-nums">{Object.keys(object(scan.coverage.targets)).length}</dd></div></dl>
    <details className="rounded-md border p-4"><summary className="cursor-pointer text-sm font-medium">Prepared source inventory</summary><ul className="mt-4 max-h-80 space-y-2 overflow-auto text-xs">{Object.keys(inventory).map((path) => <li className="break-all" key={path}>{path}</li>)}</ul></details>
    <details className="rounded-md border p-4"><summary className="cursor-pointer text-sm font-medium">Excluded and unsupported files</summary><ul className="mt-4 max-h-80 space-y-3 overflow-auto text-xs">{entries(scan.coverage.excluded).map((row, index) => <li key={index}><span className="break-all">{text(row.path)}</span><span className="mt-1 block text-muted-foreground">{text(row.reason)}</span></li>)}</ul></details>
    <details className="rounded-md border p-4"><summary className="cursor-pointer text-sm font-medium">Analysis work units</summary><ul className="mt-4 max-h-80 space-y-3 overflow-auto text-xs">{entries(scan.coverage.units).map((row, index) => <li key={index} className="flex flex-wrap justify-between gap-2"><span className="break-all">{text(row.task)}</span><Status value={text(row.status)} />{text(row.error) && <div className="w-full"><ErrorNotice error={text(row.error)} /></div>}</li>)}</ul></details>
    {entries(scan.coverage.problems).map((row, index) => <ErrorNotice key={index} error={`${text(row.path)}: ${text(row.reason)}`} />)}
  </div>;
}

function Activity({ rows }: { rows: Schema["SecurityActivityOut"][] }) {
  return <ol className="divide-y pt-4">{rows.map((row) => <li key={row.id} className="py-4"><p className="text-sm font-medium">{words(row.action)}</p><p className="mt-1 text-xs text-muted-foreground">{date(row.created_at)}</p>{text(row.detail.reason) && (row.action === "scan_failed" ? <div className="mt-2"><ErrorNotice error={text(row.detail.reason)} /></div> : <p className="mt-2 break-words text-sm">{text(row.detail.reason)}</p>)}</li>)}</ol>;
}

function FindingSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const focus = useReturnFocus();
  const query = useApi<Schema["SecurityFindingDetail"]>(`/security/findings/${id}`, true, !!id);
  return <Sheet open={!!id} onOpenChange={(open) => { if (!open) onClose(); }}><SheetContent {...focus} className="w-full overflow-y-auto sm:max-w-3xl">
    <SheetHeader className="pr-12"><SheetTitle>{query.data && !query.error ? query.data.finding.title : "Security finding"}</SheetTitle><SheetDescription>Current finding state, source evidence, and remediation history.</SheetDescription></SheetHeader>
    <div className="space-y-6 px-4 pb-8"><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data && !query.error && <FindingContents key={id} detail={query.data} />}</div>
  </SheetContent></Sheet>;
}

function FindingContents({ detail }: { detail: Schema["SecurityFindingDetail"] }) {
  const { finding, repository: repo, can_write: canWrite } = detail;
  const action = useAction();
  const evidence = finding.evidence;
  const [state, setState] = useState<"open" | "false_positive" | "accepted_risk">("false_positive");
  const [reason, setReason] = useState("");
  const [issueOpen, setIssueOpen] = useState(false);
  const issue = finding.issue;
  const hasIssue = Object.keys(issue).length > 0;
  const sourceUrl = `https://github.com/${repo.full_name}/blob/${text(evidence.source_sha)}/${finding.path.split("/").map(encodeURIComponent).join("/")}`;
  return <>
    <div className="flex flex-wrap items-center gap-3"><Status value={finding.state} /><span className="text-sm">{words(finding.severity)} · {words(finding.kind)}</span></div>
    <a href={sourceUrl} target="_blank" rel="noreferrer" className="block break-all text-xs underline underline-offset-4">{finding.path} · {text(evidence.source_sha).slice(0, 7)}</a>
    <Markdown>{text(evidence.explanation)}</Markdown>
    {text(evidence.trigger) && <section><h3 className="mb-2 text-sm font-semibold">Trigger</h3><Markdown>{text(evidence.trigger)}</Markdown></section>}
    {text(evidence.impact) && <section><h3 className="mb-2 text-sm font-semibold">Impact</h3><Markdown>{text(evidence.impact)}</Markdown></section>}
    {entries(evidence.evidence).map((citation, index) => <section key={index}><p className="mb-2 break-all text-xs text-muted-foreground">{text(citation.path)}:{count(citation.start_line)}–{count(citation.end_line)}</p><Code path={text(citation.path)}>{text(citation.quote)}</Code></section>)}
    <section><h3 className="mb-2 text-sm font-semibold">Remediation</h3><Markdown>{text(evidence.remediation)}</Markdown></section>
    <section><h3 className="mb-2 text-sm font-semibold">Verification</h3><Markdown>{text(evidence.verification)}</Markdown></section>
    <section className="space-y-3 border-t pt-5"><h3 className="text-sm font-semibold">GitHub issue</h3>
      {text(issue.url) ? <a href={text(issue.url)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm underline underline-offset-4">View issue #{count(issue.number)}<ArrowUpRight aria-hidden="true" className="size-3.5" /></a> : hasIssue ? <p className="text-sm">Publication {words(text(issue.publication_status) || "pending")}</p> : <p className="text-sm text-muted-foreground">Publish this finding only when you choose to share it.</p>}
      {hasIssue && <p className="text-xs text-muted-foreground">{words(text(issue.sync_status))}{text(issue.remote_state) ? ` · GitHub issue ${text(issue.remote_state)}` : ""}</p>}
      <ErrorNotice error={text(issue.error)} />
      {canWrite && !hasIssue && ["open", "accepted_risk"].includes(finding.state) && <Button variant="outline" onClick={() => setIssueOpen(true)}>Create GitHub issue</Button>}
      {canWrite && hasIssue && !issue.number && !issue.started && ["open", "accepted_risk"].includes(finding.state) && <Button variant="outline" onClick={() => setIssueOpen(true)}>Review issue again</Button>}
      {canWrite && hasIssue && ["failed", "uncertain"].includes(text(issue.sync_status)) && <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate({ path: `/security/findings/${finding.id}/github-issue/sync` })}>Retry issue synchronization</Button>}
    </section>
    {canWrite && <form className="space-y-4 border-t pt-5" onSubmit={(event) => { event.preventDefault(); action.mutate({ path: `/security/findings/${finding.id}`, method: "PATCH", body: { state, reason, version: finding.version } }, { onSuccess: () => setReason("") }); }}>
      <h3 className="text-sm font-semibold">Triage</h3><Field label="Decision">{(id) => <select id={id} className="h-9 rounded-md border border-input bg-card px-3 text-base md:text-sm w-full" value={state} onChange={(event) => setState(event.target.value as typeof state)}><option value="false_positive">False positive</option><option value="accepted_risk">Accept risk</option><option value="open">Reopen finding</option></select>}</Field>
      <Field label="Reason">{(id) => <Textarea id={id} required value={reason} maxLength={4000} onChange={(event) => setReason(event.target.value)} />}</Field>
      <p className="text-xs text-muted-foreground">Accepted risks still affect the grade. Triage changes are reflected in the next complete scan.</p>
      <Button type="submit" variant="outline" disabled={action.isPending || !reason.trim()}>Save decision</Button>
      {finding.kind === "secret" && ["open", "accepted_risk"].includes(finding.state) && <div className="space-y-3 border-t pt-4"><p className="text-sm">Removing a secret does not revoke it. Confirm rotation or revocation before a clean rescan can close this finding.</p>{finding.rotation ? <p className="text-xs text-muted-foreground">Rotation acknowledged. A successful rescan must still verify removal.</p> : <Button type="button" variant="outline" disabled={action.isPending || !reason.trim()} onClick={() => action.mutate({ path: `/security/findings/${finding.id}/rotation`, body: { reason, version: finding.version } })}>I rotated or revoked this credential</Button>}</div>}
    </form>}
    <ErrorNotice error={action.error} />
    <section className="border-t pt-5"><h3 className="mb-3 text-sm font-semibold">Observations</h3><ul className="space-y-3 text-xs">{detail.observations.map((row, index) => <li key={index}><Link className="underline underline-offset-4" to={`/security/scans/${text(row.scan_id)}`}>{words(text(row.outcome))}</Link><span className="ml-3 text-muted-foreground">{text(row.created_at) && date(text(row.created_at))}</span></li>)}</ul></section>
    <section className="border-t pt-5"><h3 className="text-sm font-semibold">Activity</h3><Activity rows={detail.activity} /></section>
    {issueOpen && <IssueDialog findingId={finding.id} onClose={() => setIssueOpen(false)} />}
  </>;
}

function IssueDialog({ findingId, onClose }: { findingId: string; onClose: () => void }) {
  const focus = useReturnFocus();
  const query = useApi<Schema["SecurityIssuePreview"]>(`/security/findings/${findingId}/github-issue`);
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}><DialogContent {...focus} className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl"><DialogHeader><DialogTitle>Create GitHub issue</DialogTitle><DialogDescription>Review the exact issue before publishing this finding.</DialogDescription></DialogHeader>
    <ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data && !query.error && <IssueForm key={query.data.version} preview={query.data} findingId={findingId} onClose={onClose} />}
  </DialogContent></Dialog>;
}

function IssueForm({ preview, findingId, onClose }: { preview: Schema["SecurityIssuePreview"]; findingId: string; onClose: () => void }) {
  const action = useAction();
  const [title, setTitle] = useState(preview.title);
  const [body, setBody] = useState(preview.body);
  return <form className="space-y-5" onSubmit={(event) => { event.preventDefault(); action.mutate({ path: `/security/findings/${findingId}/github-issue`, body: { title, body, version: preview.version, public: preview.public } }, { onSuccess: onClose }); }}>
    <p className="break-words text-sm">Destination: <strong>{preview.repository}</strong></p>
    {preview.public && <div role="note" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">This issue will be public. Check the evidence and details before posting.</div>}
    <Field label="Issue title">{(id) => <Input id={id} value={title} maxLength={200} required onChange={(event) => setTitle(event.target.value)} />}</Field>
    <Tabs defaultValue="preview"><TabsList><TabsTrigger value="preview">Preview</TabsTrigger><TabsTrigger value="edit">Edit</TabsTrigger></TabsList><TabsContent value="preview"><div className="max-h-80 overflow-y-auto rounded-md border p-4"><Markdown>{body}</Markdown></div></TabsContent><TabsContent value="edit"><Field label="Issue body">{(id) => <Textarea id={id} className="min-h-64" value={body} maxLength={50000} required onChange={(event) => setBody(event.target.value)} />}</Field></TabsContent></Tabs>
    <p className="text-xs leading-relaxed text-muted-foreground">Code Mortis will close its linked issue after a verified fix and reopen it if the same finding returns, provided no human override prevents synchronization.</p>
    <ErrorNotice error={action.error} /><DialogFooter><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button type="submit" disabled={action.isPending || !title.trim() || !body.trim()}>{action.isPending ? "Queuing issue…" : "Create issue"}</Button></DialogFooter>
  </form>;
}
