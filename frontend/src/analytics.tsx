import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { CalendarIcon, Download, LoaderCircle, RefreshCw } from "lucide-react";
import { api, useApi, type Schema } from "./api";
import { Empty, ErrorNotice, Loading, PageTitle } from "./components/common";
import { AnalyticsNumber, AnalyticsPanel, BreakdownChart, DataTable, TrendChart } from "./components/analytics-charts";
import { normalizeOrdering } from "./lib/ordering";
import { downloadAnalyticsCsv } from "./lib/analytics-csv";
import { FilterBar } from "./components/filter-bar";
import { SearchSelect } from "./components/search-select";
import { Button } from "./components/ui/button";
import { Calendar } from "./components/ui/calendar";
import { Label } from "./components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { useUrlTab } from "./hooks/use-url-tab";
import { analyticsLabel as label, analyticsPercent as percent, analyticsRange, analyticsTabs, calendarDate, localDate } from "./lib/analytics";

const number = (value: number | null | undefined) => <AnalyticsNumber value={value} />;
const panelGrid = "grid min-w-0 gap-4 @min-[48rem]/analytics:grid-cols-12";
const leadPanel = "bg-[color-mix(in_srgb,var(--card)_96%,var(--primary))]";

function DateFilter({ id, name, value, onChange }: { id: string; name: string; value: string | null; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const selected = calendarDate(value) ?? undefined;
  return <div className="w-40">
    <Label htmlFor={id} className="sr-only">{name}</Label>
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild><Button id={id} variant="outline" className="w-full justify-between bg-card font-normal" aria-label={name} aria-describedby={`${id}-value`}>
        <span id={`${id}-value`}>{selected ? selected.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Pick a date"}</span><CalendarIcon aria-hidden="true" />
      </Button></PopoverTrigger>
      <PopoverContent align="start" collisionPadding={16} className="w-auto p-0" aria-label={`${name} date`}>
        <Calendar mode="single" required selected={selected} defaultMonth={selected} disabled={{ after: new Date() }} autoFocus onSelect={(date) => { onChange(localDate(date)); setOpen(false); }} />
      </PopoverContent>
    </Popover>
  </div>;
}

function Metrics({ items }: { items: { label: string; value: ReactNode }[] }) {
  return <dl className="grid grid-cols-2 rounded-lg border bg-card @min-[34rem]/analytics:grid-cols-4">
    {items.map((item) => <div key={item.label}
      className="min-w-0 px-3.5 py-3 even:border-s [&:nth-child(n+3)]:border-t @min-[34rem]/analytics:[&:nth-child(n+2)]:border-s @min-[34rem]/analytics:[&:nth-child(n+3)]:border-t-0">
      <dt className="text-xs leading-tight font-medium text-muted-foreground">{item.label}</dt>
      <dd className="mt-1 text-[clamp(1.25rem,4cqi,1.875rem)] leading-tight font-semibold tracking-tight tabular-nums">{item.value}</dd>
    </div>)}
  </dl>;
}

function UsageTable({ title, rows, className }: { title: string; rows: Schema["AnalyticsUsageGroup"][]; className: string }) {
  const byRepository = title === "Usage by repository";
  return <AnalyticsPanel title={title} className={className} description={byRepository ? "Averages include fully reported samples only. Counts beneath each average show its denominator." : "Saved execution settings, including summary retry overrides. Uncertain historical settings are shown as not recorded."}>
    <DataTable label={title} headings={[byRepository ? "Repository" : title === "Usage by model" ? "Model" : "Effort", "Reported tokens", "Missing reports", ...(byRepository ? ["Mean / run", "Mean / reviewed PR"] : [])]}
      rows={rows.map((row) => [row.key,
        number(row.usage.tokens), number(row.usage.missing_reports),
        ...(byRepository ? [
        <><span>{number(row.usage.mean_per_run)}</span><span className="mt-1 block text-xs text-muted-foreground">{number(row.usage.complete_runs)} runs</span></>,
        <><span>{number(row.usage.mean_per_reviewed_pr)}</span><span className="mt-1 block text-xs text-muted-foreground">{number(row.usage.reviewed_pr_samples)} PRs</span></>,
        ] : []),
      ])} />
  </AnalyticsPanel>;
}

export default function Analytics({ repository }: { repository: Pick<Schema["RepositoryOut"], "id" | "full_name"> }) {
  const [params, setParams] = useSearchParams();
  const { state } = useLocation();
  const [tab, setTab] = useUrlTab("tab", [...analyticsTabs], "overview");
  const csvRequest = useRef<AbortController | null>(null);
  const [csvDownload, setCsvDownload] = useState<{ pending: boolean; error?: unknown }>({ pending: false });
  useLayoutEffect(() => () => csvRequest.current?.abort(), []);
  const range = analyticsRange(params);
  useEffect(() => {
    if (range.period !== "custom" || range.error || (params.get("period") === "custom" && params.has("start") && params.has("end"))) return;
    const next = new URLSearchParams(params);
    next.set("period", "custom");
    next.set("start", range.start);
    next.set("end", range.end);
    setParams(next, { replace: true, state });
  }, [params, setParams, state, range.period, range.error, range.start, range.end]);
  const valid = !range.error;
  const orderings = { count: tab === "usage" ? "Tokens" : "Count", key: "Name" };
  const ordering = normalizeOrdering(params.get("ordering"), orderings, "-count");
  const baseParams = new URLSearchParams(range.query);
  baseParams.set("repo", String(repository.id));
  baseParams.set("ordering", ordering);
  const base = baseParams.toString();
  const findingQuery = new URLSearchParams(base);
  for (const key of ["severity", "category", "status"]) for (const value of params.getAll(key)) findingQuery.append(key, value);
  const paths = {
    overview: `/analytics/overview?${base}`,
    findings: `/analytics/findings?${findingQuery}`,
    reviews: `/analytics/reviews?${base}`,
    usage: `/analytics/usage?${base}`,
    security: `/analytics/security?${base}`,
  };
  const overview = useApi<Schema["AnalyticsOverview"]>(paths.overview, false, valid && tab === "overview");
  const findings = useApi<Schema["AnalyticsFindings"]>(paths.findings, false, valid && tab === "findings");
  const reviews = useApi<Schema["AnalyticsReviews"]>(paths.reviews, false, valid && tab === "reviews");
  const usage = useApi<Schema["AnalyticsUsage"]>(paths.usage, false, valid && tab === "usage");
  const security = useApi<Schema["AnalyticsSecurity"]>(paths.security, false, valid && tab === "security");
  const query = tab === "findings" ? findings : tab === "reviews" ? reviews : tab === "usage" ? usage : tab === "security" ? security : overview;
  const scope = !query.error && valid ? query.data?.scope : undefined;
  async function downloadCsv() {
    if (range.error || csvRequest.current) return;
    const controller = new AbortController();
    csvRequest.current = controller;
    setCsvDownload({ pending: true });
    try {
      const read = <T,>(path: string) => api<T>(path, "GET", undefined, undefined, controller.signal);
      const [overview, findings, reviews, usage, security] = await Promise.all([
        read<Schema["AnalyticsOverview"]>(paths.overview),
        read<Schema["AnalyticsFindings"]>(paths.findings),
        read<Schema["AnalyticsReviews"]>(paths.reviews),
        read<Schema["AnalyticsUsage"]>(paths.usage),
        read<Schema["AnalyticsSecurity"]>(paths.security),
      ]);
      if (controller.signal.aborted) return;
      const period = range.period === "all" ? `all-time-through-${range.end}` : `${range.start}-to-${range.end}`;
      downloadAnalyticsCsv({ overview, findings, reviews, usage, security }, repository, period, findingQuery);
      setCsvDownload({ pending: false });
    } catch (error) {
      if (!controller.signal.aborted) {
        controller.abort();
        setCsvDownload({ pending: false, error });
      }
    } finally {
      if (csvRequest.current === controller) csvRequest.current = null;
    }
  }
  function change(key: string, values: string[]) {
    const next = new URLSearchParams(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(next, { state });
  }
  function findingFilter(key: "severity" | "category", value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", "findings");
    for (const filter of ["severity", "category", "status"]) next.delete(filter);
    next.set(key, value);
    setParams(next, { state });
  }
  function setPeriod(value: string) {
    const next = new URLSearchParams(params);
    next.set("period", value);
    if (value === "custom") {
      const defaults = analyticsRange(new URLSearchParams());
      next.set("start", defaults.start!);
      next.set("end", defaults.end!);
    } else {
      next.delete("start"); next.delete("end");
    }
    setParams(next, { state });
  }
  const empty = scope && (tab === "overview" ? !overview.data?.findings && !overview.data?.trend.some((point) => point.runs)
    : tab === "findings" ? !findings.data?.unfiltered_total : tab === "reviews" ? !reviews.data?.runs
    : tab === "security" ? !security.data?.scans && !security.data?.findings : !usage.data?.total.total_runs);
  const choices = (values: string[]) => values.map((value) => ({ value, name: label(value) }));
  return <div className="@container/analytics min-w-0 space-y-3.5">
    <PageTitle title="Analytics" className="mb-0 [&>div]:items-center" actions={<div role="group" aria-label="Analytics filters">
      <FilterBar filters={[]} onChange={change}>
      <div className="w-40"><Label htmlFor="analytics-period" className="sr-only">Date range</Label><SearchSelect id="analytics-period" value={range.period} onChange={setPeriod} required options={[
        { value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" }, { value: "all", label: "All time" }, { value: "custom", label: "Custom dates" },
      ]} /></div>
      {range.period === "custom" && <div className="flex max-w-full flex-wrap gap-3">{(["start", "end"] as const).map((key) => <DateFilter key={key} id={`analytics-${key}`} name={key === "start" ? "From" : "Through"} value={params.get(key) ?? range[key] ?? null} onChange={(value) => change(key, [value])} />)}</div>}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={!valid || query.isFetching} onClick={() => query.refetch()}><RefreshCw aria-hidden="true" className={query.isFetching ? "motion-safe:animate-spin" : ""} />Refresh</Button>
        <Button variant="outline" disabled={!valid || csvDownload.pending} aria-busy={csvDownload.pending} onClick={downloadCsv}>
          {csvDownload.pending ? <LoaderCircle aria-hidden="true" className="motion-safe:animate-spin" /> : <Download aria-hidden="true" />}Download
        </Button>
        <span role="status" className="sr-only">{csvDownload.pending ? "Preparing Analytics CSV" : ""}</span>
      </div>
      </FilterBar>
    </div>} />
    <ErrorNotice error={csvDownload.error} title="Could not download Analytics CSV" />
    <Tabs value={tab} onValueChange={setTab}><TabsList variant="page" className="gap-x-3 sm:gap-x-6" aria-label="Analytics views">{analyticsTabs.map((value) => <TabsTrigger key={value} value={value} id={`analytics-tab-${value}`} aria-controls="analytics-panel">{label(value)}</TabsTrigger>)}</TabsList></Tabs>
    <div id="analytics-panel" role="tabpanel" aria-labelledby={`analytics-tab-${tab}`} aria-busy={valid && query.isFetching} className="min-w-0">
      <div className="min-w-0 space-y-4">
      {tab === "findings" && scope && scope.repositories.length > 0 && !empty && findings.data && <div role="status"><Metrics items={[
        { label: "Findings", value: number(findings.data.total) },
        { label: "Open", value: number(findings.data.statuses.find((row) => row.key === "open")?.count ?? 0) },
        { label: "Resolved", value: number(findings.data.statuses.find((row) => row.key === "resolved")?.count ?? 0) },
        { label: "Dismissed", value: number(findings.data.statuses.find((row) => row.key === "dismissed")?.count ?? 0) },
      ]} /></div>}
      {tab === "findings" && !query.error && <div role="group" aria-label="Finding filters"><FilterBar filters={[
        { key: "severity", label: "Severity", values: params.getAll("severity"), choices: choices(["critical", "high", "medium", "low"]) },
        { key: "category", label: "Category", values: params.getAll("category"), choices: choices(findings.data?.category_options ?? params.getAll("category")), searchPlaceholder: "Find a category…" },
        { key: "status", label: "Status", values: params.getAll("status"), choices: choices(["open", "resolved", "dismissed"]) },
      ]} onChange={change}>
        {["severity", "category", "status"].some((key) => params.has(key)) && <Button variant="ghost" onClick={() => { const next = new URLSearchParams(params); ["severity", "category", "status"].forEach((key) => next.delete(key)); setParams(next, { state }); }}>Clear finding filters</Button>}
      </FilterBar></div>}
      {range.error ? <ErrorNotice error={range.error} /> : query.error ? <div className="space-y-3"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry analytics</Button></div> : query.isPending ? <Loading /> : <>
        {scope && !scope.repositories.length ? <Empty title="No repositories available">This repository is unavailable. <Link className="underline underline-offset-4" to="/settings?tab=github">Manage GitHub connections</Link>.</Empty> : empty ? <Empty title={tab === "findings" ? "No findings recorded in this period" : tab === "security" ? "No security activity in this period" : "No review activity in this period"}>Choose a wider date range. No findings does not establish complete review coverage.<Button variant="outline" className="mt-4" onClick={() => setPeriod("all")}>View all time</Button></Empty> : <>
          {tab === "overview" && overview.data && <>
            <Metrics items={[
              { label: "PRs reviewed", value: number(overview.data.reviewed_prs) },
              { label: "Findings detected", value: number(overview.data.findings) },
              { label: "Resolved findings", value: <>
                {number(overview.data.resolved)}
                {overview.data.findings > 0 && <> <span className="whitespace-nowrap text-xs font-medium text-muted-foreground">
                  {percent(overview.data.resolved, overview.data.findings)}<span className="sr-only"> of findings detected</span>
                </span></>}
              </> },
              { label: "Reported tokens", value: number(overview.data.usage.tokens) },
            ]} />
            <div className={panelGrid}>
              <TrendChart title="Review activity" className={`${leadPanel} @min-[48rem]/analytics:col-span-8`} description="Runs are grouped by creation date; findings by first detection. One PR can have several runs." data={overview.data.trend} keys={["runs", "findings"]} />
              <BreakdownChart title="Severity" className="@min-[48rem]/analytics:col-span-4" description="Explore findings first detected in this period." data={overview.data.severities} severity onSelect={(value) => findingFilter("severity", value)} />
              <BreakdownChart title="Categories" className="col-span-full" description="Saved finding classifications, including custom categories." data={overview.data.categories} onSelect={(value) => findingFilter("category", value)} />
            </div>
          </>}
          {tab === "findings" && findings.data && <>
            {!findings.data.total ? <Empty title="No findings match these filters">Change the severity, category, or status selection.</Empty> : <div className={panelGrid}>
              <BreakdownChart title="Severity" className="@min-[48rem]/analytics:col-span-5" data={findings.data.severities} severity />
              <BreakdownChart title="Categories" className="@min-[48rem]/analytics:col-span-7" data={findings.data.categories} />
              <BreakdownChart title="Current resolution" className="@min-[48rem]/analytics:col-span-5" description="Confirmed current state of findings detected in this period. Dismissal is separate from resolution." data={findings.data.statuses} />
              <TrendChart title="Finding trends" className={`${leadPanel} @min-[48rem]/analytics:col-span-7`} description="Unique findings grouped by first detection date, using the selected filters." data={findings.data.trend} keys={["findings"]} />
            </div>}
          </>}
          {tab === "reviews" && reviews.data && <>
            <Metrics items={[
              { label: "Review runs", value: number(reviews.data.runs) }, { label: "PRs reviewed", value: number(reviews.data.reviewed_prs) },
              { label: "Average review duration", value: reviews.data.mean_duration_seconds == null ? "Unavailable" : reviews.data.mean_duration_seconds < 60 ? <>{number(reviews.data.mean_duration_seconds)} sec</> : <>{number(reviews.data.mean_duration_seconds / 60)} min</> },
              { label: "Contributors", value: number(reviews.data.contributors.length) },
            ]} />
            <div className={panelGrid}>
              <BreakdownChart title="Review outcomes" className={`${leadPanel} @min-[48rem]/analytics:col-span-7`} description="Execution status is separate from GitHub publication and finding resolution." data={reviews.data.outcomes} />
              <BreakdownChart title="File review results" className="@min-[48rem]/analytics:col-span-5" description={<>Validated results per run; a file reviewed again counts again. {number(reviews.data.coverage_missing_runs)} runs have no valid saved coverage.</>} data={reviews.data.coverage} />
              <BreakdownChart title="Assessment outcomes" className="@min-[48rem]/analytics:col-span-7" description={<>Each candidate appears in one outcome. {number(reviews.data.assessment_missing_runs)} legacy runs have no recorded assessment policy.</>} data={reviews.data.assessments} />
              <AnalyticsPanel title="Contributor activity" className="@min-[48rem]/analytics:col-span-5" description="Distinct reviewed PRs by saved PR author. Activity counts do not measure code quality."><DataTable label="Contributor activity" headings={["PR author", "PRs reviewed"]} rows={reviews.data.contributors.map((row) => [row.key, number(row.count)])} /></AnalyticsPanel>
            </div>
          </>}
          {tab === "usage" && usage.data && <>
            <Metrics items={[
              { label: "Reported tokens", value: number(usage.data.total.tokens) },
              { label: "Fully reported runs", value: <>{number(usage.data.total.complete_runs)} / {number(usage.data.total.total_runs)}</> },
              { label: "Mean tokens / run", value: number(usage.data.total.mean_per_run) },
              { label: "Mean tokens / reviewed PR", value: number(usage.data.total.mean_per_reviewed_pr) },
            ]} />
            <div className={panelGrid}>
              <TrendChart title="Token usage" className={`${leadPanel} @min-[48rem]/analytics:col-span-8`} description="Saved consumption by run creation date, including retries. Gaps mean no usage was reported for the period." data={usage.data.trend} keys={["tokens"]} />
              <AnalyticsPanel title="Usage by stage" className="@min-[48rem]/analytics:col-span-4" description="Each saved consumption record is counted once."><DataTable label="Usage by stage" headings={["Stage", "Reported tokens", "Missing reports"]} rows={usage.data.stages.map((row) => [label(row.key), number(row.usage.tokens), number(row.usage.missing_reports)])} /></AnalyticsPanel>
              <UsageTable title="Usage by repository" rows={usage.data.repositories} className="col-span-full" />
              <UsageTable title="Usage by model" rows={usage.data.models} className="@min-[48rem]/analytics:col-span-6" />
              <UsageTable title="Usage by effort" rows={usage.data.efforts} className="@min-[48rem]/analytics:col-span-6" />
            </div>
          </>}
          {tab === "security" && security.data && <>
            <Metrics items={[
              { label: "Security scans", value: number(security.data.scans) },
              { label: "Findings detected", value: number(security.data.findings) },
              { label: "Open findings", value: number(security.data.open) },
              { label: "Resolved findings", value: number(security.data.resolved) },
            ]} />
            <div className={panelGrid}>
              <TrendChart title="Security activity" className={`${leadPanel} @min-[48rem]/analytics:col-span-8`} description="Scans are grouped by creation date; unique findings by first detection. Repeated observations do not add findings." data={security.data.trend} keys={["runs", "findings"]} runLabel="Security scans" />
              <BreakdownChart title="Severity" className="@min-[48rem]/analytics:col-span-4" description="Severity of unique findings first detected in this period, across all current states." data={security.data.severities} severity />
              <BreakdownChart title="Finding types" className="@min-[48rem]/analytics:col-span-4" description="Saved security classifications, including code, dependencies, secrets, and infrastructure." data={security.data.kinds} />
              <BreakdownChart title="Current resolution" className="@min-[48rem]/analytics:col-span-4" description="Current state of findings first detected in this period. False positives and accepted risk are separate from resolved findings." data={security.data.states} />
              <BreakdownChart title="Scan outcomes" className="@min-[48rem]/analytics:col-span-4" description="Saved execution status for scans started in this period. Partial, failed, and unscanned work never implies a clean repository." data={security.data.outcomes} />
            </div>
          </>}
        </>}
      </>}
      </div>
    </div>
    <details className="border-t pt-4 text-xs leading-relaxed text-muted-foreground"><summary className="cursor-pointer rounded-sm font-medium text-foreground">About these metrics</summary><div className="mt-3 max-w-[75ch] space-y-2">
      {tab === "security" ? <>
        <p>Security analytics includes saved repository scans and unique findings in this repository. Scans use their creation date; findings use their first detection date. Retried scans count as separate scans.</p>
        <p>Open and resolved counts describe the current state of findings first detected in the selected period, not the entire backlog. Reopening changes these counts. Accepted risk and false positives remain separate; absence from a later scan never establishes a fix.</p>
      </> : <>
      <p>Analytics includes saved history for open, merged, closed, and ignored pull requests in this repository. Review runs use their creation date; findings use the date they were first detected.</p>
      <p>Resolved means the confirmed current status of findings first detected in the selected period. Reopening changes this count. A resolved thread is not necessarily a verified bug fix; dismissal and absence from later reviews do not count as resolution.</p>
      <p>Reported tokens are saved consumption, not billing estimates. Missing reports, reservations, and ambiguous legacy values are excluded. Run and PR averages use fully reported samples. Models and effort come from saved execution settings, including summary retries.</p>
      </>}
    </div></details>
  </div>;
}
