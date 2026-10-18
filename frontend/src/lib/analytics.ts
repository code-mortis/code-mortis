export const analyticsTabs = ["overview", "findings", "reviews", "usage"] as const;
export type AnalyticsTab = typeof analyticsTabs[number];
export const analyticsPeriods = ["7", "30", "90", "all", "custom"];

export function localDate(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
}

export function calendarDate(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isFinite(date.getTime()) && localDate(date) === value ? date : null;
}

export function analyticsRange(params: URLSearchParams, today = new Date()) {
  const period = analyticsPeriods.includes(params.get("period") ?? "") ? params.get("period")! : "custom";
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const start = new Date(end);
  start.setDate(start.getDate() - (period === "7" ? 7 : period === "90" ? 90 : 30));
  if (period === "custom") {
    const from = calendarDate(params.get("start") ?? localDate(start));
    const to = calendarDate(params.get("end") ?? localDate(today));
    if (!from || !to || from > to) return { period, error: "Choose a valid start and end date, with the start on or before the end." } as const;
    start.setTime(from.getTime());
    end.setTime(to.getTime());
    end.setDate(end.getDate() + 1);
  }
  const query = new URLSearchParams({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, to_at: end.toISOString() });
  if (period === "all") query.set("all_time", "true");
  else query.set("from_at", start.toISOString());
  for (const repo of params.getAll("repo")) query.append("repo", repo);
  return { period, query, start: localDate(start), end: localDate(new Date(end.getFullYear(), end.getMonth(), end.getDate() - 1)) } as const;
}

export const analyticsNumber = (value: number | null | undefined) => value == null ? "Unavailable" : value.toLocaleString(undefined, { maximumFractionDigits: 0 });
export const analyticsPercent = (part: number, total: number) => total ? `${Math.round(part / total * 100)}%` : "No findings";
export const analyticsLabel = (value: string) => ({
  new: "New findings", repeated: "Repeated observations", rejected: "Not supported", withheld: "Withheld",
  pending: "Awaiting assessment", review: "Review", assessment: "Assessment", verification: "Fix verification", summary: "Summary",
}[value] ?? value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()));
