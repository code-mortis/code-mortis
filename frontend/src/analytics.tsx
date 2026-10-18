import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronDown, RefreshCw } from "lucide-react";
import { useApi, type Schema } from "./api";
import { Empty, ErrorNotice, Loading } from "./components/common";
import { AnalyticsPanel, BreakdownChart, DataTable, TrendChart } from "./components/analytics-charts";
import { Button } from "./components/ui/button";
import { Checkbox } from "./components/ui/checkbox";
import { Input } from "./components/ui/input";
import { Label } from "./components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Tabs, TabsList, TabsTrigger } from "./components/ui/tabs";
import { useUrlTab } from "./hooks/use-url-tab";
import { analyticsLabel as label, analyticsNumber as number, analyticsPercent as percent, analyticsRange, analyticsTabs, calendarDate, localDate } from "./lib/analytics";

type Totals = Schema["AnalyticsUsageTotals"];
const selectClass = "h-9 w-full rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function Metrics({ items }: { items: { label: string; value: ReactNode; hint: string }[] }) {
  return <dl className={`grid grid-cols-2 gap-x-5 gap-y-6 border-y py-5 ${items.length > 2 ? "lg:grid-cols-4" : ""}`}>
    {items.map((item) => <div key={item.label} className="min-w-0"><dt className="text-xs font-medium text-muted-foreground">{item.label}</dt><dd className="mt-2 text-2xl font-semibold tracking-tight tabular-nums sm:text-3xl">{item.value}</dd><dd className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{item.hint}</dd></div>)}
  </dl>;
}

function ChoiceFilter({ name, values, choices, onChange, searchable = false }: {
  name: string; values: string[]; choices: { value: string; label: string }[]; onChange: (values: string[]) => void; searchable?: boolean;
}) {
  const [search, setSearch] = useState("");
  const selected = values.map((value) => choices.find((choice) => choice.value === value)?.label ?? value);
  return <Popover onOpenChange={() => setSearch("")}>
    <PopoverTrigger asChild><Button variant="outline" className="max-w-full justify-between bg-card" aria-label={`${name}: ${selected.join(", ") || "All"}`}>
      <span className="truncate">{selected.length === 1 ? selected[0] : selected.length ? `${name}: ${selected.length}` : `All ${name.toLowerCase()}`}</span><ChevronDown aria-hidden="true" />
    </Button></PopoverTrigger>
    <PopoverContent align="start" collisionPadding={16} className="w-72 max-w-[calc(100vw-2rem)] p-2" aria-label={`Filter ${name.toLowerCase()}`}>
      {searchable && <Input aria-label={`Search ${name.toLowerCase()}`} placeholder={`Search ${name.toLowerCase()}…`} value={search} onChange={(event) => setSearch(event.target.value)} className="mb-2" />}
      <div className="max-h-64 overflow-y-auto">{choices.filter((choice) => choice.label.toLowerCase().includes(search.toLowerCase())).map((choice) => <label key={choice.value} className="flex min-h-10 cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring">
        <Checkbox checked={values.includes(choice.value)} onCheckedChange={(checked) => onChange(checked === true ? [...values, choice.value] : values.filter((value) => value !== choice.value))} />
        <span className="min-w-0 break-words">{choice.label}</span>
      </label>)}{!choices.some((choice) => choice.label.toLowerCase().includes(search.toLowerCase())) && <p className="p-3 text-sm text-muted-foreground">No matches.</p>}</div>
      {!!values.length && <Button variant="ghost" className="mt-2 w-full border-t" onClick={() => onChange([])}>Clear {name.toLowerCase()}</Button>}
    </PopoverContent>
  </Popover>;
}

function UsageNote({ value }: { value: Totals }) {
  return <p className="text-xs leading-relaxed text-muted-foreground" role="status">
    {value.missing_reports ? <>Partial reporting: {number(value.missing_reports)} missing or uncertain reports. Known usage is shown; averages use fully reported samples.</>
      : value.total_runs ? <>All saved usage reports are available for {number(value.total_runs)} review runs.</> : "No review usage recorded in this period."}
  </p>;
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
  const choices = (values: string[]) => values.map((value) => ({ value, label: label(value) }));
  return <div className="min-w-0 space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Analytics</h1><p className="mt-2 text-sm text-muted-foreground">Review impact and model usage across your repositories.</p></div>
      <Button variant="outline" disabled={!valid || query.isFetching} onClick={() => query.refetch()}><RefreshCw aria-hidden="true" className={query.isFetching ? "motion-safe:animate-spin" : ""} />Refresh</Button>
    </div>
    <div className="flex flex-wrap items-end gap-3" aria-label="Analytics filters">
      <div className="min-w-0 max-w-full space-y-2"><p className="text-xs font-medium">Repositories</p><ChoiceFilter name="Repositories" values={params.getAll("repo")} choices={(query.error || repositories.error ? [] : repositories.data ?? []).map((repo) => ({ value: String(repo.id), label: repo.full_name }))} onChange={(values) => change("repo", values)} searchable /></div>
      <div className="w-40 space-y-2"><Label htmlFor="analytics-period" className="text-xs">Date range</Label><select id="analytics-period" className={selectClass} value={range.period} onChange={(event) => setPeriod(event.target.value)}>
        <option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option><option value="all">All time</option><option value="custom">Custom dates</option>
      </select></div>
      {range.period === "custom" && <div className="flex max-w-full flex-wrap gap-3">{(["start", "end"] as const).map((key) => <div key={key} className="w-40 space-y-2"><Label htmlFor={`analytics-${key}`} className="text-xs">{key === "start" ? "From" : "Through"}</Label><Input id={`analytics-${key}`} type="date" value={calendarDate(params.get(key)) ? params.get(key)! : ""} max={localDate(new Date())} onChange={(event) => change(key, [event.target.value])} /></div>)}</div>}
      {params.has("repo") && <Button variant="ghost" onClick={() => change("repo", [])}>All repositories</Button>}
    </div>
    <Tabs value={tab} onValueChange={setTab}><TabsList variant="line" className="max-w-full">{analyticsTabs.map((value) => <TabsTrigger key={value} value={value} id={`analytics-tab-${value}`} aria-controls="analytics-panel">{label(value)}</TabsTrigger>)}</TabsList></Tabs>
    <div id="analytics-panel" role="tabpanel" aria-labelledby={`analytics-tab-${tab}`} aria-busy={valid && query.isFetching} className="min-w-0 space-y-6">
      {range.error ? <ErrorNotice error={range.error} /> : query.error ? <div className="space-y-3"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry analytics</Button><Button variant="ghost" onClick={() => change("repo", [])}>Clear repository selection</Button></div> : query.isPending ? <Loading /> : <>
        {scope && <p className="text-xs text-muted-foreground">{range.period === "all" ? "All saved history" : `${range.start} through ${range.end}`} · {scope.timezone.replaceAll("_", " ")} · {{ day: "Daily", week: "Weekly", month: "Monthly" }[scope.bucket]} chart buckets</p>}
        {scope && !scope.repositories.length ? <Empty title="No repositories available">Analytics includes repositories you can currently access. <Link className="underline underline-offset-4" to="/settings?tab=github">Manage GitHub connections</Link>.</Empty> : empty ? <Empty title={tab === "findings" ? "No findings recorded in this period" : "No review activity in this period"}>Choose a wider date range or another repository. No findings does not establish complete review coverage.<Button variant="outline" className="mt-4" onClick={() => setPeriod("all")}>View all time</Button></Empty> : <>
          {tab === "overview" && overview.data && <>
            <Metrics items={[
              { label: "PRs reviewed", value: number(overview.data.reviewed_prs), hint: "Distinct PRs with completed or partial reviews" },
              { label: "Findings detected", value: number(overview.data.findings), hint: "First detected in this period; repeats excluded" },
              { label: "Resolved findings", value: number(overview.data.resolved), hint: `${percent(overview.data.resolved, overview.data.findings)} of this period’s findings are now resolved` },
              { label: "Reported tokens", value: number(overview.data.usage.tokens), hint: overview.data.usage.missing_reports ? `${number(overview.data.usage.missing_reports)} reports unavailable` : "Saved consumption across review stages" },
            ]} />
            <TrendChart title="Review activity" description="Runs are grouped by creation date; findings by first detection. One PR can have several runs." data={overview.data.trend} keys={["runs", "findings"]} />
            <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="Severity" description="Explore findings first detected in this period." data={overview.data.severities} severity onSelect={(value) => findingFilter("severity", value)} /><BreakdownChart title="Categories" description="Saved finding classifications, including custom categories." data={overview.data.categories} onSelect={(value) => findingFilter("category", value)} /></div>
            <AnalyticsPanel title="Top repositories" description="Up to five repositories, ordered by distinct reviewed PRs. Select one to narrow this page.">
              <DataTable label="Top repositories" headings={["Repository", "PRs reviewed", "Findings", "Resolved"]} rows={overview.data.repositories.map((repo) => [<button className="rounded-sm text-left font-medium text-primary hover:underline underline-offset-4" onClick={() => selectRepository(repo.id)}>{repo.name}</button>, number(repo.reviewed_prs), number(repo.findings), <>{number(repo.resolved)}<span className="mt-1 block text-xs text-muted-foreground">{percent(repo.resolved, repo.findings)}</span></>])} />
            </AnalyticsPanel><UsageNote value={overview.data.usage} />
          </>}
          {tab === "findings" && findings.data && <>
            <div className="flex flex-wrap gap-2" aria-label="Finding filters"><ChoiceFilter name="Severities" values={params.getAll("severity")} choices={choices(["critical", "high", "medium", "low"])} onChange={(values) => change("severity", values)} /><ChoiceFilter name="Categories" values={params.getAll("category")} choices={choices(findings.data.category_options)} onChange={(values) => change("category", values)} searchable /><ChoiceFilter name="Statuses" values={params.getAll("status")} choices={choices(["open", "resolved", "dismissed"])} onChange={(values) => change("status", values)} />
              {["severity", "category", "status"].some((key) => params.has(key)) && <Button variant="ghost" onClick={() => { const next = new URLSearchParams(params); ["severity", "category", "status"].forEach((key) => next.delete(key)); setParams(next); }}>Clear finding filters</Button>}
            </div>
            <p className="text-sm tabular-nums" role="status">{number(findings.data.total)} of {number(findings.data.unfiltered_total)} findings match</p>
            {!findings.data.total ? <Empty title="No findings match these filters">Change the severity, category, or status selection.</Empty> : <>
              <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="Severity" data={findings.data.severities} severity /><BreakdownChart title="Categories" data={findings.data.categories} /></div>
              <BreakdownChart title="Current resolution" description="Confirmed current state of findings detected in this period. Dismissal is separate from resolution." data={findings.data.statuses} />
              <TrendChart title="Finding trends" description="Unique findings grouped by first detection date, using the selected filters." data={findings.data.trend} keys={["findings"]} />
            </>}
          </>}
          {tab === "reviews" && reviews.data && <>
            <Metrics items={[
              { label: "Review runs", value: number(reviews.data.runs), hint: "All saved execution outcomes" }, { label: "PRs reviewed", value: number(reviews.data.reviewed_prs), hint: "Completed or partial; counted once per PR" },
            ]} />
            <BreakdownChart title="Review outcomes" description="Execution status is separate from GitHub publication and finding resolution." data={reviews.data.outcomes} />
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm"><span>Median execution duration</span><strong className="text-lg tabular-nums">{reviews.data.median_duration_seconds == null ? "Unavailable" : reviews.data.median_duration_seconds < 60 ? `${number(reviews.data.median_duration_seconds)} sec` : `${number(reviews.data.median_duration_seconds / 60)} min`}</strong><span className="text-xs text-muted-foreground">{number(reviews.data.duration_samples)} finished runs with recorded timing</span></div>
            <div className="grid min-w-0 gap-6 lg:grid-cols-2"><BreakdownChart title="File review results" description={`Validated results per run; a file reviewed again counts again. ${number(reviews.data.coverage_missing_runs)} runs have no valid saved coverage.`} data={reviews.data.coverage} />
              <BreakdownChart title="Assessment outcomes" description={`Each candidate appears in one outcome. ${number(reviews.data.assessment_missing_runs)} legacy runs have no recorded assessment policy.`} data={reviews.data.assessments} /></div>
            <AnalyticsPanel title="Contributor activity" description="Distinct reviewed PRs by saved PR author. Activity counts do not measure code quality."><DataTable label="Contributor activity" headings={["PR author", "PRs reviewed"]} rows={reviews.data.contributors.map((row) => [row.key, number(row.count)])} /></AnalyticsPanel>
          </>}
          {tab === "usage" && usage.data && <>
            <Metrics items={[
              { label: "Reported tokens", value: number(usage.data.total.tokens), hint: "Includes saved retries; reservations excluded" },
              { label: "Fully reported runs", value: `${number(usage.data.total.complete_runs)} / ${number(usage.data.total.total_runs)}`, hint: `${number(usage.data.total.missing_reports)} missing or uncertain reports` },
              { label: "Mean tokens / run", value: number(usage.data.total.mean_per_run), hint: `${number(usage.data.total.complete_runs)} fully reported runs` },
              { label: "Mean tokens / reviewed PR", value: number(usage.data.total.mean_per_reviewed_pr), hint: `${number(usage.data.total.reviewed_pr_samples)} PRs with all selected runs reported` },
            ]} /><UsageNote value={usage.data.total} />
            <TrendChart title="Token usage" description="Saved consumption attributed to each run’s creation date, including its retries. Gaps mean no known usage; partial totals remain identified in the data table." data={usage.data.trend} keys={["tokens"]} />
            <AnalyticsPanel title="Usage by stage" description="Each saved consumption record is counted once."><DataTable label="Usage by stage" headings={["Stage", "Reported tokens", "Missing reports"]} rows={usage.data.stages.map((row) => [label(row.key), number(row.usage.tokens), number(row.usage.missing_reports)])} /></AnalyticsPanel>
            <UsageTable title="Usage by repository" rows={usage.data.repositories} onRepository={selectRepository} /><UsageTable title="Usage by model" rows={usage.data.models} /><UsageTable title="Usage by effort" rows={usage.data.efforts} />
          </>}
        </>}
      </>}
    </div>
    <details className="border-t pt-4 text-xs leading-relaxed text-muted-foreground"><summary className="cursor-pointer rounded-sm font-medium text-foreground">About these metrics</summary><div className="mt-3 max-w-[75ch] space-y-2">
      <p>Analytics includes saved history for open, merged, closed, and ignored pull requests in repositories you can currently access. Review runs use their creation date; findings use the date they were first detected.</p>
      <p>Resolved means the confirmed current status of findings first detected in the selected period. Reopening changes this count. A resolved thread is not necessarily a verified bug fix; dismissal and absence from later reviews do not count as resolution.</p>
      <p>Reported tokens are saved consumption, not billing estimates. Missing reports, reservations, and ambiguous legacy values are excluded. Run and PR averages use fully reported samples. Models and effort come from saved execution settings, including summary retries.</p>
    </div></details>
  </div>;
}
