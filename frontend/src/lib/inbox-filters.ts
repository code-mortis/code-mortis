import type { Schema } from "../api";

export const groups = ["active", "reviewed", "merged", "closed"] as const;
export type Group = Schema["InboxPage"]["group"];
export const states = ["open", "merged", "closed"];
export const reviews = ["running", "queued", "blocked", "not_reviewed", "outdated", "completed", "partial", "failed", "skipped", "cancelled", "superseded"];
export const properties = { repo: "Repository", state: "PR state", review: "Review status", author: "Author", findings: "Open findings" };
export type Property = keyof typeof properties;
export const label = (value: string) => value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export function normalizeFilters(input: URLSearchParams) {
  const result = new URLSearchParams();
  const q = input.get("q")?.trim().slice(0, 256);
  if (q) result.set("q", q);
  for (const key of Object.keys(properties) as Property[]) {
    let values = [...new Set(input.getAll(key))];
    if (key === "repo") values = values.filter((v) => /^\d+$/.test(v) && Number.isSafeInteger(+v) && +v > 0).map((v) => String(+v));
    if (key === "state") {
      values = values.includes("all") ? ["all"] : states.filter((v) => values.includes(v));
      if (values.length === 3) values = ["all"];
      if (values.join(",") === "open,merged") values = [];
    }
    if (key === "review") values = reviews.filter((v) => values.includes(v));
    if (key === "author") values = values.map((v) => v.trim().toLowerCase()).filter((v) => v.length > 0 && v.length <= 100);
    if (key === "findings") values = values.filter((v) => v === "open" || v === "none").slice(0, 1);
    for (const value of [...new Set(values)].slice(0, 100)) result.append(key, value);
  }
  for (const group of groups) {
    const value = input.get(`${group}_pages`) ?? "1";
    if (/^\d+$/.test(value) && +value > 1) result.set(`${group}_pages`, String(Math.min(+value, 1000000)));
  }
  return result;
}

export function selected(params: URLSearchParams, property: Property) {
  const values = params.getAll(property);
  return property === "state" ? values.includes("all") ? states : values.length ? values : ["open", "merged"] : values;
}

export function withoutPages(params: URLSearchParams) {
  const result = new URLSearchParams(params);
  for (const group of groups) result.delete(`${group}_pages`);
  return result;
}
