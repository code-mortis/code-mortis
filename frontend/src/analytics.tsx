import { useEffect, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarIcon, RefreshCw } from "lucide-react";
import { useApi, type Schema } from "./api";
import { Empty, ErrorNotice, Loading } from "./components/common";
import { AnalyticsPanel, BreakdownChart, DataTable, TrendChart } from "./components/analytics-charts";
import { FilterBar } from "./components/filter-bar";
import { SearchSelect } from "./components/search-select";
import { Button } from "./components/ui/button";
import { Calendar } from "./components/ui/calendar";
import { Label } from "./components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { useUrlTab } from "./hooks/use-url-tab";
import { analyticsLabel as label, analyticsNumber as number, analyticsPercent as percent, analyticsRange, analyticsTabs, calendarDate, localDate } from "./lib/analytics";

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
  return <dl className={`grid grid-cols-2 gap-x-5 gap-y-6 border-y py-5 ${items.length > 2 ? "lg:grid-cols-4" : ""}`}>
    {items.map((item) => <div key={item.label} className="min-w-0"><dt className="text-xs font-medium text-muted-foreground">{item.label}</dt><dd className="mt-2 text-2xl font-semibold tracking-tight tabular-nums sm:text-3xl">{item.value}</dd></div>)}
  </dl>;
}

function UsageTable({ title, rows, onRepository }: { title: string; rows: Schema["AnalyticsUsageGroup"][]; onRepository?: (id: number) => void }) {
  return <AnalyticsPanel title={title} description={onRepository ? "Averages include fully reported samples only. Counts beneath each average show its denominator." : "Saved execution settings, including summary retry overrides. Uncertain historical settings are shown as not recorded."}>
    <DataTable label={title} headings={[onRepository ? "Repository" : title === "Usage by model" ? "Model" : "Effort", "Reported tokens", "Missing reports", ...(onRepository ? ["Mean / run", "Mean / reviewed PR"] : [])]}
      rows={rows.map((row) => [row.repository_id != null && onRepository ? <button className="rounded-sm text-left font-medium text-primary underline-offset-4 hover:underline" onClick={() => onRepository(row.repository_id!)}>{row.key}</button> : row.key,
        number(row.usage.tokens), number(row.usage.missing_reports),
        ...(onRepository ? [
        <><span>{number(row.usage.mean_per_run)}</span><span className="mt-1 block text-xs text-muted-foreground">{number(row.usage.complete_runs)} runs</span></>,
        <><span>{number(row.usage.mean_per_reviewed_pr)}</span><span className="mt-1 block text-xs text-muted-foreground">{number(row.usage.reviewed_pr_samples)} PRs</span></>,
        ] : []),
      ])} />
  </AnalyticsPanel>;
}

export default function Analytics() {
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useUrlTab("tab", [...analyticsTabs], "overview");
  const range = analyticsRange(params);
  useEffect(() => {
    if (range.period !== "custom" || range.error || (params.get("period") === "custom" && params.has("start") && params.has("end"))) return;
    const next = new URLSearchParams(params);
    next.set("period", "custom");
    next.set("start", range.start);
    next.set("end", range.end);
    setParams(next, { replace: true });
  }, [params, setParams, range.period, range.error, range.start, range.end]);
  const repositories = useApi<Schema["RepositoryOut"][]>("/repositories");
  const valid = !range.error;
  const base = range.query?.toString() ?? "";
  const findingQuery = new URLSearchParams(base);
  for (const key of ["severity", "category", "status"]) for (const value of params.getAll(key)) findingQuery.append(key, value);
  const overview = useApi<Schema["AnalyticsOverview"]>(`/analytics/overview?${base}`, false, valid && tab === "overview");
  const findings = useApi<Schema["AnalyticsFindings"]>(`/analytics/findings?${findingQuery}`, false, valid && tab === "findings");
  const reviews = useApi<Schema["AnalyticsReviews"]>(`/analytics/reviews?${base}`, false, valid && tab === "reviews");
  const usage = useApi<Schema["AnalyticsUsage"]>(`/analytics/usage?${base}`, false, valid && tab === "usage");
  const query = tab === "findings" ? findings : tab === "reviews" ? reviews : tab === "usage" ? usage : overview;
  const scope = !query.error && valid ? query.data?.scope : undefined;
  function change(key: string, values: string[]) {
    const next = new URLSearchParams(params);
    next.delete(key);
    values.forEach((value) => next.append(key, value));
    setParams(next);
  }
  function findingFilter(key: "severity" | "category", value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", "findings");
    for (const filter of ["severity", "category", "status"]) next.delete(filter);
    next.set(key, value);
    setParams(next);
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
    setParams(next);
  }
  const selectRepository = (id: number) => change("repo", [String(id)]);
  const empty = scope && (tab === "overview" ? !overview.data?.findings && !overview.data?.trend.some((point) => point.runs)
    : tab === "findings" ? !findings.data?.unfiltered_total : tab === "reviews" ? !reviews.data?.runs : !usage.data?.total.total_runs);
  const choices = (values: string[]) => values.map((value) => ({ value, name: label(value) }));
  return <div className="min-w-0 space-y-6">
    <h1 className="sr-only">Analytics</h1>
    <div className="flex flex-wrap items-center gap-3" role="group" aria-label="Analytics filters">
      <div className="min-w-0 max-w-full"><FilterBar filters={[{ key: "repo", label: "Repository", values: params.getAll("repo"), choices: (query.error || repositories.error ? [] : repositories.data ?? []).map((repo) => ({ value: String(repo.id), name: repo.full_name })), searchPlaceholder: "Find a repository…" }]} onChange={change} /></div>
      <div className="w-40"><Label htmlFor="analytics-period" className="sr-only">Date range</Label><SearchSelect id="analytics-period" value={range.period} onChange={setPeriod} required options={[
        { value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" }, { value: "all", label: "All time" }, { value: "custom", label: "Custom dates" },
      ]} /></div>
      {range.period === "custom" && <div className="flex max-w-full flex-wrap gap-3">{(["start", "end"] as const).map((key) => <DateFilter key={key} id={`analytics-${key}`} name={key === "start" ? "From" : "Through"} value={params.get(key) ?? range[key] ?? null} onChange={(value) => change(key, [value])} />)}</div>}
      <Button variant="outline" className="sm:ml-auto" disabled={!valid || query.isFetching} onClick={() => query.refetch()}><RefreshCw aria-hidden="true" className={query.isFetching ? "motion-safe:animate-spin" : ""} />Refresh</Button>
    </div>
    <Tabs value={tab} onValueChange={setTab}><TabsList className="max-w-full">{analyticsTabs.map((value) => <TabsTrigger key={value} value={value} id={`analytics-tab-${value}`} aria-controls="analytics-panel">{label(value)}</TabsTrigger>)}</TabsList></Tabs>
    <div id="analytics-panel" role="tabpanel" aria-labelledby={`analytics-tab-${tab}`} aria-busy={valid && query.isFetching} className="min-w-0">
      <p className="mb-4 text-xs leading-relaxed text-muted-foreground">{{
        overview: "Review activity, findings, and resolution for the selected repositories and period.",
        findings: "Explore findings by severity, category, and current resolution status.",
        reviews: "Review outcomes, execution time, validated file coverage, assessments, and contributor activity.",
        usage: "Token usage by stage, repository, model, and effort, with averages per run and reviewed PR.",
      }[tab]}</p>
      <div className="min-w-0 space-y-6">
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
        {["severity", "category", "status"].some((key) => params.has(key)) && <Button variant="ghost" onClick={() => { const next = new URLSearchParams(params); ["severity", "category", "status"].forEach((key) => next.delete(key)); setParams(next); }}>Clear finding filters</Button>}
      </FilterBar></div>}
      {range.error ? <ErrorNotice error={range.error} /> : query.error ? <div className="space-y-3"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry analytics</Button><Button variant="ghost" onClick={() => change("repo", [])}>Clear repository selection</Button></div> : query.isPending ? <Loading /> : <>
        {scope && !scope.repositories.length ? <Empty title="No repositories available">Analytics includes repositories you can currently access. <Link className="underline underline-offset-4" to="/settings?tab=github">Manage GitHub connections</Link>.</Empty> : empty ? <Empty title={tab === "findings" ? "No findings recorded in this period" : "No review activity in this period"}>Choose a wider date range or another repository. No findings does not establish complete review coverage.<Button variant="outline" className="mt-4" onClick={() => setPeriod("all")}>View all time</Button></Empty> : <>
          {tab === "overview" && overview.data && <>
            <Metrics items={[
              { label: "PRs reviewed", value: number(overview.data.reviewed_prs) },
              { label: "Findings detected", value: number(overview.data.findings) },
              { label: "Resolved findings", value: number(overview.data.resolved) },
              { label: "Reported tokens", value: number(overview.data.usage.tokens) },
            ]} />
            <TrendChart title="Review activity" description="Runs are grouped by creation date; findings by first detection. One PR can have several runs." data={overview.data.trend} keys={["runs", "findings"]} />
            <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="Severity" description="Explore findings first detected in this period." data={overview.data.severities} severity onSelect={(value) => findingFilter("severity", value)} /><BreakdownChart title="Categories" description="Saved finding classifications, including custom categories." data={overview.data.categories} onSelect={(value) => findingFilter("category", value)} /></div>
            <AnalyticsPanel title="Top repositories" description="Up to five repositories, ordered by distinct reviewed PRs. Select one to narrow this page.">
              <DataTable label="Top repositories" headings={["Repository", "PRs reviewed", "Findings", "Resolved"]} rows={overview.data.repositories.map((repo) => [<button className="rounded-sm text-left font-medium text-primary hover:underline underline-offset-4" onClick={() => selectRepository(repo.id)}>{repo.name}</button>, number(repo.reviewed_prs), number(repo.findings), <>{number(repo.resolved)}<span className="mt-1 block text-xs text-muted-foreground">{percent(repo.resolved, repo.findings)}</span></>])} />
            </AnalyticsPanel>
          </>}
          {tab === "findings" && findings.data && <>
            {!findings.data.total ? <Empty title="No findings match these filters">Change the severity, category, or status selection.</Empty> : <>
              <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="Severity" data={findings.data.severities} severity /><BreakdownChart title="Categories" data={findings.data.categories} /></div>
              <BreakdownChart title="Current resolution" description="Confirmed current state of findings detected in this period. Dismissal is separate from resolution." data={findings.data.statuses} />
              <TrendChart title="Finding trends" description="Unique findings grouped by first detection date, using the selected filters." data={findings.data.trend} keys={["findings"]} />
            </>}
          </>}
          {tab === "reviews" && reviews.data && <>
            <Metrics items={[
              { label: "Review runs", value: number(reviews.data.runs) }, { label: "PRs reviewed", value: number(reviews.data.reviewed_prs) },
              { label: "Average review duration", value: reviews.data.mean_duration_seconds == null ? "Unavailable" : reviews.data.mean_duration_seconds < 60 ? `${number(reviews.data.mean_duration_seconds)} sec` : `${number(reviews.data.mean_duration_seconds / 60)} min` },
              { label: "Contributors", value: number(reviews.data.contributors.length) },
            ]} />
            <BreakdownChart title="Review outcomes" description="Execution status is separate from GitHub publication and finding resolution." data={reviews.data.outcomes} />
            <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="File review results" description={`Validated results per run; a file reviewed again counts again. ${number(reviews.data.coverage_missing_runs)} runs have no valid saved coverage.`} data={reviews.data.coverage} />
              <BreakdownChart title="Assessment outcomes" description={`Each candidate appears in one outcome. ${number(reviews.data.assessment_missing_runs)} legacy runs have no recorded assessment policy.`} data={reviews.data.assessments} /></div>
            <AnalyticsPanel title="Contributor activity" description="Distinct reviewed PRs by saved PR author. Activity counts do not measure code quality."><DataTable label="Contributor activity" headings={["PR author", "PRs reviewed"]} rows={reviews.data.contributors.map((row) => [row.key, number(row.count)])} /></AnalyticsPanel>
          </>}
          {tab === "usage" && usage.data && <>
            <Metrics items={[
              { label: "Reported tokens", value: number(usage.data.total.tokens) },
              { label: "Fully reported runs", value: `${number(usage.data.total.complete_runs)} / ${number(usage.data.total.total_runs)}` },
              { label: "Mean tokens / run", value: number(usage.data.total.mean_per_run) },
              { label: "Mean tokens / reviewed PR", value: number(usage.data.total.mean_per_reviewed_pr) },
            ]} />
            <TrendChart title="Token usage" description="Saved consumption attributed to each run’s creation date, including its retries. Gaps mean no known usage; partial totals remain identified in the data table." data={usage.data.trend} keys={["tokens"]} />
            <AnalyticsPanel title="Usage by stage" description="Each saved consumption record is counted once."><DataTable label="Usage by stage" headings={["Stage", "Reported tokens", "Missing reports"]} rows={usage.data.stages.map((row) => [label(row.key), number(row.usage.tokens), number(row.usage.missing_reports)])} /></AnalyticsPanel>
            <UsageTable title="Usage by repository" rows={usage.data.repositories} onRepository={selectRepository} /><UsageTable title="Usage by model" rows={usage.data.models} /><UsageTable title="Usage by effort" rows={usage.data.efforts} />
          </>}
        </>}
      </>}
      </div>
    </div>
    <details className="border-t pt-4 text-xs leading-relaxed text-muted-foreground"><summary className="cursor-pointer rounded-sm font-medium text-foreground">About these metrics</summary><div className="mt-3 max-w-[75ch] space-y-2">
      <p>Analytics includes saved history for open, merged, closed, and ignored pull requests in repositories you can currently access. Review runs use their creation date; findings use the date they were first detected.</p>
      <p>Resolved means the confirmed current status of findings first detected in the selected period. Reopening changes this count. A resolved thread is not necessarily a verified bug fix; dismissal and absence from later reviews do not count as resolution.</p>
      <p>Reported tokens are saved consumption, not billing estimates. Missing reports, reservations, and ambiguous legacy values are excluded. Run and PR averages use fully reported samples. Models and effort come from saved execution settings, including summary retries.</p>
    </div></details>
  </div>;
}
