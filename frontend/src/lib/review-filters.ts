import { INBOX_QUERY_MAX_LENGTH } from "./inbox-filters";

export const findingStatuses = ["open", "resolved", "dismissed"] as const;
export const findingSeverities = ["critical", "high", "medium", "low"];
export const runFindingOutcomes = {
  new: "New finding",
  open_repeat: "Open repeat",
  resolved_repeat: "Resolved repeat",
  dismissed_repeat: "Dismissed repeat",
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
export type ReviewFilterKey = "q" | "status" | "severity" | "category" | "outcome";
export const reviewFilterKeys = {
  pull: ["q", "status", "severity", "category"],
  run: ["q", "outcome", "severity", "category"],
  assessment: ["q", "outcome", "severity", "category"],
  resolved: ["q", "severity", "category"],
} as const;
export const reviewFilterPrefixes = { pull: "", run: "", assessment: "assessment_", resolved: "resolved_" };

export function normalizeReviewFilters(input: URLSearchParams, scope: ReviewFilterScope) {
  const result = new URLSearchParams();
  const prefix = reviewFilterPrefixes[scope];
  const q = input.get(`${prefix}q`)?.trim().slice(0, INBOX_QUERY_MAX_LENGTH);
  if (q) result.set("q", q);
  for (const key of reviewFilterKeys[scope]) {
    if (key === "q") continue;
    let values = [...new Set(input.getAll(`${prefix}${key}`))];
    if (key === "status") {
      values = values.includes("all") ? ["all"] : values.filter((value) => findingStatuses.some((status) => status === value));
      if (!values.length) values = ["open", "resolved"];
    }
    if (key === "severity") values = values.filter((value) => findingSeverities.includes(value));
    if (key === "outcome") {
      const outcomes = scope === "run" ? Object.keys(runFindingOutcomes) : candidateOutcomes.map((outcome) => outcome.value);
      values = values.filter((value) => outcomes.includes(value));
    }
    if (key === "category") values = values.map((value) => value.trim()).filter((value) => value.length > 0 && value.length <= 50);
    for (const value of [...new Set(values)].slice(0, 100)) result.append(key, value);
  }
  return result;
}

export function withReviewFilters(input: URLSearchParams, filters: URLSearchParams, scope: ReviewFilterScope) {
  const result = new URLSearchParams(input);
  const prefix = reviewFilterPrefixes[scope];
  for (const key of reviewFilterKeys[scope]) result.delete(`${prefix}${key}`);
  for (const [key, value] of filters) result.append(`${prefix}${key}`, value);
  return result;
}
