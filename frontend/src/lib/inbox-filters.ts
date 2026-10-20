export const groups = ["active", "merged", "closed"] as const;
export const states = ["open", "merged", "closed"];
export const reviews = ["running", "queued", "blocked", "not_reviewed", "outdated", "completed", "partial", "failed", "skipped", "cancelled", "superseded"];
export const properties = { repo: "Repository", state: "PR state", review: "Review status", author: "Author", findings: "Open findings", visibility: "Visibility" };
export type Property = keyof typeof properties;
export const orderings = { created_at: "Created", updated_at: "Updated", title: "Title", repository: "Repository", author: "Author" };
export type OrderingField = keyof typeof orderings;
export const label = (value: string) => value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
export const INBOX_QUERY_MAX_LENGTH = 256;
export const INBOX_SEARCH_DEBOUNCE_MS = 300;
export const INBOX_MAX_PAGES = 1000000;
const MAX_FILTER_VALUES = 100;
const MAX_AUTHOR_LENGTH = 100;

export function normalizeFilters(input: URLSearchParams, defaultState: "open" | "all" = "all") {
  const result = new URLSearchParams();
  const q = input.get("q")?.trim().slice(0, INBOX_QUERY_MAX_LENGTH);
  if (q) result.set("q", q);
  for (const key of Object.keys(properties) as Property[]) {
    let values = [...new Set(input.getAll(key))];
    if (key === "repo") values = values.filter((v) => /^\d+$/.test(v) && Number.isSafeInteger(+v) && +v > 0).map((v) => String(+v));
    if (key === "state") {
      values = values.includes("all") ? states : states.filter((v) => values.includes(v));
      if (values.length === states.length) values = defaultState === "open" ? ["all"] : [];
      else if (!values.length && defaultState === "open") values = ["open"];
    }
    if (key === "review") values = reviews.filter((v) => values.includes(v));
    if (key === "author") values = values.map((v) => v.trim().replace(/^@+/, "").toLowerCase()).filter((v) => v.length > 0 && v.length <= MAX_AUTHOR_LENGTH);
    if (key === "findings") values = values.filter((v) => v === "open" || v === "none").slice(0, 1);
    if (key === "visibility") values = values.filter((v) => v === "ignored" || v === "all").slice(0, 1);
    for (const value of [...new Set(values)].slice(0, MAX_FILTER_VALUES)) result.append(key, value);
  }
  const ordering = input.get("ordering");
  if (ordering && ordering !== "-created_at" && Object.prototype.hasOwnProperty.call(orderings, ordering.replace(/^-/, ""))) result.set("ordering", ordering);
  for (const group of [...groups, "all"]) {
    const value = input.get(`${group}_pages`) ?? (group === "active" ? input.get("reviewed_pages") : null) ?? "1";
    if (/^\d+$/.test(value) && +value > 1) result.set(`${group}_pages`, String(Math.min(+value, INBOX_MAX_PAGES)));
  }
  return result;
}

// Accept raw state=all links as well as normalized filter params.
export function selected(params: URLSearchParams, property: Property) {
  const values = params.getAll(property);
  if (property === "visibility") return values.length ? values : ["visible"];
  if (property !== "state") return values;
  if (values.includes("all")) return [...states];
  if (values.length) return values;
  return [...states];
}

export function withoutPages(params: URLSearchParams) {
  const result = new URLSearchParams(params);
  for (const group of [...groups, "all"]) result.delete(`${group}_pages`);
  result.delete("reviewed_pages");
  return result;
}

export function getInboxReturn(state: unknown): string | null {
  if (!state || typeof state !== "object" || !("inbox" in state)) return null;
  return typeof state.inbox === "string" && /^\/(?:\?|$)/.test(state.inbox) ? state.inbox : null;
}
