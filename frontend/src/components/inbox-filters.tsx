import type { Schema } from "../api";
import { label, orderings, properties, reviews, selected, states, type Property } from "../lib/inbox-filters";
import { FilterBar, type FilterDefinition } from "./filter-bar";

export function InboxFilters({ params, data, onChange, onSearch, repository = false }: {
  params: URLSearchParams;
  data?: Pick<Schema["InboxOut"], "repositories" | "authors" | "state_counts">;
  repository?: boolean;
  onChange: (property: Property | "ordering", values: string[]) => void;
  onSearch: (value: string) => void;
}) {
  const ordering = params.get("ordering") ?? (repository ? "-updated_at" : "-created_at");
  const filters: FilterDefinition<Property>[] = (Object.keys(properties) as Property[]).filter((key) => !repository || key !== "repo").map((key) => {
    const values = selected(params, key);
    let choices: { value: string; name: string; count?: number }[];
    if (key === "repo") choices = data?.repositories.map((r) => ({ value: String(r.id), name: r.full_name })) ?? [];
    else if (key === "author") choices = [...new Map(data?.authors.map((a) => [a.toLowerCase(), { value: a.toLowerCase(), name: `@${a}` }])).values()];
    else if (key === "state") choices = states.map((value) => ({ value, name: label(value), count: data?.state_counts[value] }));
    else if (key === "review") choices = reviews.map((value) => ({ value, name: label(value) }));
    else if (key === "visibility") choices = [{ value: "visible", name: "Visible" }, { value: "ignored", name: "Ignored" }, { value: "all", name: "All" }];
    else choices = [{ value: "open", name: "Has open findings" }, { value: "none", name: "No open findings" }];
    for (const value of values) if (!choices.some((choice) => choice.value === value)) choices.push({ value, name: key === "repo" ? `Repository ${value}` : key === "author" ? `@${value}` : label(value) });
    return {
      key, label: properties[key], values, choices,
      active: values.length > 0 && !(key === "state" && values.length === states.length) && !(key === "visibility" && values[0] === "visible"),
      single: key === "findings" || key === "visibility", required: key === "state" || key === "visibility",
      searchPlaceholder: key === "repo" ? "Find a repository…" : key === "author" ? "Find an author…" : undefined,
      hint: key === "state" ? "Running reviews stay visible in every PR state." : undefined,
      clearLabel: key === "state" ? "Include all states" : undefined,
      clearValues: key === "state" ? ["all"] : undefined,
    };
  });
  return <FilterBar query={params.get("q") ?? ""} searchLabel="Search pull requests" searchPlaceholder="Search pull requests…" filters={filters} onChange={onChange} onSearch={onSearch}
    ordering={{ value: ordering, defaultValue: repository ? "-updated_at" : "-created_at",
      options: Object.fromEntries(Object.entries(orderings).filter(([key]) => !repository || !["repository", "created_at"].includes(key))),
      onChange: (value) => onChange("ordering", [value]), hint: "Within each group. Running and queued reviews stay first." }} />;
}
