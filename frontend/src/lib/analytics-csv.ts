import type { Schema } from "../api";
import type { AnalyticsTab } from "./analytics";

type AnalyticsData = {
  overview: Schema["AnalyticsOverview"];
  findings: Schema["AnalyticsFindings"];
  reviews: Schema["AnalyticsReviews"];
  usage: Schema["AnalyticsUsage"];
  security: Schema["AnalyticsSecurity"];
};
type CsvCell = string | number | null | undefined;

function csvCell(value: CsvCell) {
  if (typeof value !== "string") return value ?? "";
  // Spreadsheet apps can interpret quoted strings with these prefixes as formulas.
  const safe = /^(?:[\t\r\n]|\s*[=+\-@＝＋－＠])/u.test(value) ? "'" + value : value;
  return '"' + safe.replaceAll('"', '""') + '"';
}

export function downloadAnalyticsCsv(
  data: AnalyticsData,
  repository: Pick<Schema["RepositoryOut"], "id" | "full_name">,
  period: string,
  findingQuery: URLSearchParams,
) {
  const rows = ["repository,repository_id,from_at,to_at_exclusive,timezone,bucket,tab,section,item,date,metric,value"];
  function add(tab: AnalyticsTab, section: string, metrics: Record<string, CsvCell>, item = "", date = "") {
    // All-time review and security trends may begin on different dates.
    const { from_at, to_at, timezone, bucket } = data[tab].scope;
    const prefix = [repository.full_name, repository.id, from_at, to_at, timezone, bucket, tab, section, item, date].map(csvCell).join(",");
    for (const [metric, value] of Object.entries(metrics)) rows.push(prefix + "," + csvCell(metric) + "," + csvCell(value));
  }
  function addCounts(tab: AnalyticsTab, sections: Record<string, Schema["AnalyticsCount"][]>) {
    for (const [section, counts] of Object.entries(sections)) {
      for (const { key, count } of counts) add(tab, section, { count }, key);
    }
  }

  const { overview, findings, reviews, usage, security } = data;
  add("overview", "summary", {
    reviewed_prs: overview.reviewed_prs,
    findings: overview.findings,
    resolved: overview.resolved,
    resolved_percent: overview.findings ? 100 * overview.resolved / overview.findings : null,
  });
  add("overview", "usage", overview.usage);
  for (const { date, runs, findings } of overview.trend) add("overview", "activity", { review_runs: runs, findings }, "", date);
  addCounts("overview", { severities: overview.severities, categories: overview.categories });
  for (const { id, name, ...metrics } of overview.repositories) add("overview", "by_repository", { id, ...metrics }, name);

  for (const filter of ["severity", "category", "status"]) {
    for (const value of findingQuery.getAll(filter)) add("findings", "filters", { [filter]: value });
  }
  add("findings", "summary", { total: findings.total, unfiltered_total: findings.unfiltered_total });
  for (const point of findings.trend) add("findings", "activity", { findings: point.findings }, "", point.date);
  addCounts("findings", { severities: findings.severities, categories: findings.categories, statuses: findings.statuses });

  const { scope: _reviewScope, outcomes, coverage, assessments, contributors, ...reviewTotals } = reviews;
  add("reviews", "summary", { ...reviewTotals, contributor_count: contributors.length });
  addCounts("reviews", { outcomes, coverage, assessments, contributors });

  add("usage", "summary", usage.total);
  for (const { date, tokens, missing_reports } of usage.trend) add("usage", "activity", { tokens, missing_reports }, "", date);
  const usageGroups = { by_stage: usage.stages, by_repository: usage.repositories, by_model: usage.models, by_effort: usage.efforts };
  for (const [section, groups] of Object.entries(usageGroups)) {
    for (const group of groups) add("usage", section, group.usage, group.key);
  }

  add("security", "summary", { scans: security.scans, findings: security.findings, open: security.open, resolved: security.resolved });
  for (const { date, runs, findings } of security.trend) add("security", "activity", { scans: runs, findings }, "", date);
  addCounts("security", { severities: security.severities, kinds: security.kinds, states: security.states, outcomes: security.outcomes });

  const url = URL.createObjectURL(new Blob(["\uFEFF", rows.join("\r\n"), "\r\n"], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `analytics-${repository.full_name.replace(/[^a-zA-Z0-9._-]+/g, "-")}-${period}.csv`;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
}
