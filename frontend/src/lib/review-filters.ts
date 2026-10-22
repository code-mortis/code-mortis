import { assessmentOrderings, findingOrderings, normalizeOrdering, runFindingOrderings } from "./ordering";
import { INBOX_QUERY_MAX_LENGTH } from "./inbox-filters";

export const findingSeverities = ["critical", "high", "medium", "low"];
export const runFindingOutcomes = {
  new: "New finding",
  repeated: "Repeated finding",
  recorded: "Recorded finding",
};
export const candidateOutcomes = [
  { value: "new", name: "New validated finding" },
  { value: "repeated", name: "Repeated observation" },
  { value: "rejected", name: "Not supported" },
  { value: "withheld", name: "Withheld" },
  { value: "pending", name: "Awaiting assessment" },
];
export type ReviewFilterScope = "pull" | "run" | "assessment" | "resolved";
export type ReviewFilterKey = "q" | "status" | "severity" | "category" | "outcome" | "ordering";
export const reviewFilterKeys = {
  pull: ["q", "status", "severity", "category", "ordering"],
  run: ["q", "outcome", "severity", "category", "ordering"],
  assessment: ["q", "outcome", "severity", "category", "ordering"],
  resolved: ["q", "severity", "category", "ordering"],
} as const;
export const reviewFilterPrefixes = { pull: "", run: "", assessment: "assessment_", resolved: "resolved_" };

export function normalizeReviewFilters(input: URLSearchParams, scope: ReviewFilterScope) {
  const result = new URLSearchParams();
  const prefix = reviewFilterPrefixes[scope];
  const q = input.get(`${prefix}q`)?.trim().slice(0, INBOX_QUERY_MAX_LENGTH);
  if (q) result.set("q", q);
  for (const key of reviewFilterKeys[scope]) {
    if (key === "q" || key === "ordering") continue;
    let values = [...new Set(input.getAll(`${prefix}${key}`))];
    if (key === "status") {
      values = ["open"];
    }
    if (key === "severity") values = values.filter((value) => findingSeverities.includes(value));
    if (key === "outcome") {
      if (scope === "run") values = values.map((value) => ["open_repeat", "resolved_repeat", "dismissed_repeat"].includes(value) ? "repeated" : value);
      const outcomes = scope === "run" ? Object.keys(runFindingOutcomes) : candidateOutcomes.map((outcome) => outcome.value);
      values = values.filter((value) => outcomes.includes(value));
    }
    if (key === "category") values = values.map((value) => value.trim()).filter((value) => value.length > 0 && value.length <= 50);
    for (const value of [...new Set(values)].slice(0, 100)) result.append(key, value);
  }
  const orderings = scope === "run" ? runFindingOrderings : scope === "assessment" ? assessmentOrderings : findingOrderings;
  const defaultOrdering = "-severity";
  const ordering = normalizeOrdering(input.get(`${prefix}ordering`), orderings, defaultOrdering);
  if (ordering !== defaultOrdering) result.set("ordering", ordering);
  return result;
}

export function withReviewFilters(input: URLSearchParams, filters: URLSearchParams, scope: ReviewFilterScope) {
  const result = new URLSearchParams(input);
  const prefix = reviewFilterPrefixes[scope];
  for (const key of reviewFilterKeys[scope]) result.delete(`${prefix}${key}`);
  for (const [key, value] of filters) result.append(`${prefix}${key}`, value);
  return result;
}
