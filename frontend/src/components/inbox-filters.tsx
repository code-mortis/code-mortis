import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import type { Schema } from "../api";
import { label, orderings, properties, reviews, selected, states, type OrderingField, type Property } from "../lib/inbox-filters";
import { FilterBar, type FilterDefinition } from "./filter-bar";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";

export function InboxFilters({ params, data, onChange, onSearch, repository = false }: {
  params: URLSearchParams;
  data?: Pick<Schema["InboxOut"], "repositories" | "authors" | "state_counts">;
  repository?: boolean;
  onChange: (property: Property | "ordering", values: string[]) => void;
  onSearch: (value: string) => void;
}) {
  const ordering = params.get("ordering") ?? (repository ? "-updated_at" : "-created_at");
  const field = ordering.replace(/^-/, "") as OrderingField;
  const descending = ordering.startsWith("-");
  let direction = descending ? "Z to A" : "A to Z";
  if (field.endsWith("_at")) direction = descending ? "Newest first" : "Oldest first";
  const filters: FilterDefinition<Property>[] = (Object.keys(properties) as Property[]).filter((key) => !repository || key !== "repo").map((key) => {
    const values = selected(params, key);
    let choices: { value: string; name: string; count?: number }[];
    if (key === "repo") choices = data?.repositories.map((r) => ({ value: String(r.id), name: r.full_name })) ?? [];
    else if (key === "author") choices = [...new Map(data?.authors.map((a) => [a.toLowerCase(), { value: a.toLowerCase(), name: `@${a}` }])).values()];
    else if (key === "state") choices = states.map((value) => ({ value, name: label(value), count: data?.state_counts[value] }));
    else if (key === "review") choices = reviews.map((value) => ({ value, name: label(value) }));
    else choices = [{ value: "open", name: "Has open findings" }, { value: "none", name: "No open findings" }];
    for (const value of values) if (!choices.some((choice) => choice.value === value)) choices.push({ value, name: key === "repo" ? `Repository ${value}` : key === "author" ? `@${value}` : label(value) });
    return {
      key, label: properties[key], values, choices,
      active: values.length > 0 && !(key === "state" && values.length === states.length),
      single: key === "findings", required: key === "state",
      searchPlaceholder: key === "repo" ? "Find a repository…" : key === "author" ? "Find an author…" : undefined,
      hint: key === "state" ? "Running reviews stay visible in every PR state." : undefined,
      clearLabel: key === "state" ? "Include all states" : undefined,
      clearValues: key === "state" ? ["all"] : undefined,
    };
  });
  return <FilterBar query={params.get("q") ?? ""} searchLabel="Search pull requests" searchPlaceholder="Search pull requests…" filters={filters} onChange={onChange} onSearch={onSearch}>
    <div className="flex shrink-0 items-center gap-2" role="group" aria-label="Ordering">
      <DropdownMenu>
        <DropdownMenuTrigger asChild><Button variant="outline" className="bg-card" aria-label={`Sort by ${orderings[field]}`}><ArrowUpDown className="text-muted-foreground" />{orderings[field]}</Button></DropdownMenuTrigger>
        <DropdownMenuContent align="end" collisionPadding={16} className="w-56 max-w-[calc(100vw-2rem)]">
          <DropdownMenuRadioGroup value={field} onValueChange={(value) => { if (value !== field) onChange("ordering", [value.endsWith("_at") ? `-${value}` : value]); }}>
            {(Object.keys(orderings) as OrderingField[]).filter((key) => !repository || (key !== "repository" && key !== "created_at")).map((key) => <DropdownMenuRadioItem key={key} value={key}>{orderings[key]}</DropdownMenuRadioItem>)}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <p className="px-2 py-1.5 text-xs text-muted-foreground">Within each group. Running and queued reviews stay first.</p>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button variant="outline" size="icon" className="bg-card" title={direction} aria-label={descending ? "Sort ascending" : "Sort descending"} onClick={() => onChange("ordering", [descending ? field : `-${field}`])}>{descending ? <ArrowDown /> : <ArrowUp />}</Button>
    </div>
  </FilterBar>;
}
