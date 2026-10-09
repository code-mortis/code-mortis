import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ListFilter, PlusCircle, Search, X } from "lucide-react";
import type { Schema } from "../api";
import { INBOX_QUERY_MAX_LENGTH, INBOX_SEARCH_DEBOUNCE_MS, label, orderings, properties, reviews, selected, states, type OrderingField, type Property } from "../lib/inbox-filters";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

export function InboxFilters({ params, data, onChange, onSearch }: {
  params: URLSearchParams;
  data?: Schema["InboxOut"];
  onChange: (property: Property | "ordering", values: string[]) => void;
  onSearch: (value: string) => void;
}) {
  const query = params.get("q") ?? "";
  const [draft, setDraft] = useState({ query, value: query });
  const searchInput = useRef<HTMLInputElement>(null);
  // Navigation wins before effects run: https://react.dev/reference/react/useState#storing-information-from-previous-renders
  if (draft.query !== query) setDraft({ query, value: query });
  useEffect(() => {
    if (draft.value === query || draft.query !== query) return;
    const timer = setTimeout(() => onSearch(draft.value), INBOX_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, query, onSearch]);
  const [open, setOpen] = useState<Property | null>(null);
  const [optionQuery, setOptionQuery] = useState("");
  const filtersButton = useRef<HTMLButtonElement>(null);
  const pendingFilter = useRef<Property | null>(null);
  const sortButton = useRef<HTMLButtonElement>(null);
  const ordering = params.get("ordering") ?? "-created_at";
  const field = ordering.replace(/^-/, "") as OrderingField;
  const descending = ordering.startsWith("-");
  let direction = descending ? "Z to A" : "A to Z";
  if (field.endsWith("_at")) direction = descending ? "Newest first" : "Oldest first";
  function summary(key: Property, value: string) {
    if (key === "repo") return data?.repositories.find((r) => String(r.id) === value)?.full_name ?? `Repository ${value}`;
    if (key === "author") return `@${value}`;
    if (key === "findings") return value === "open" ? "Has open findings" : "No open findings";
    return label(value);
  }
  function isActive(key: Property) {
    const values = selected(params, key);
    return values.length > 0 && !(key === "state" && values.length === states.length);
  }
  const available = (Object.keys(properties) as Property[]).filter((key) => !isActive(key));
  function clear(key: Property) {
    onChange(key, key === "state" ? ["all"] : []);
    setOpen(null);
    filtersButton.current?.focus();
  }
  function options(key: Property) {
    let choices: { value: string; name: string; count?: number }[];
    if (key === "repo") choices = data?.repositories.map((r) => ({ value: String(r.id), name: r.full_name })) ?? [];
    else if (key === "author") choices = [...new Map(data?.authors.map((a) => [a.toLowerCase(), { value: a.toLowerCase(), name: `@${a}` }])).values()];
    else if (key === "state") choices = states.map((value) => ({ value, name: label(value), count: data?.state_counts[value] }));
    else if (key === "review") choices = reviews.map((value) => ({ value, name: label(value) }));
    else choices = [{ value: "open", name: "Has open findings" }, { value: "none", name: "No open findings" }];
    const values = selected(params, key);
    for (const value of values) if (!choices.some((c) => c.value === value)) choices.push({ value, name: key === "repo" || key === "author" ? summary(key, value) : label(value) });
    return <>
      {(key === "repo" || key === "author") && <Input autoFocus aria-label={`Search ${properties[key].toLowerCase()} options`} placeholder={`Find ${key === "repo" ? "a repository" : "an author"}…`} value={optionQuery} onChange={(e) => setOptionQuery(e.target.value)} className="mb-2" />}
      <div className="max-h-64 overflow-y-auto" role="group" aria-label={properties[key]}>
        {choices.filter((c) => c.name.toLowerCase().includes(optionQuery.toLowerCase())).map((choice) => <label key={choice.value} className="flex min-h-10 cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-inset">
          <Checkbox checked={values.includes(choice.value)} disabled={key === "state" && values.length === 1 && values.includes(choice.value)} onCheckedChange={(checked) => {
            let next: string[];
            if (checked !== true) next = values.filter((v) => v !== choice.value);
            else if (key === "findings") next = [choice.value];
            else next = [...values, choice.value];
            onChange(key, next);
          }} />
          <span className="min-w-0 break-words">{choice.name}</span>
          {choice.count !== undefined && <span className="ml-auto tabular-nums text-muted-foreground">{choice.count}</span>}
        </label>)}
        {!choices.some((c) => c.name.toLowerCase().includes(optionQuery.toLowerCase())) && <p className="p-3 text-sm text-muted-foreground">No options found.</p>}
      </div>
      {key === "state" && <p className="px-2 pt-2 text-xs text-muted-foreground">Running reviews stay visible in every PR state.</p>}
      {values.length > 0 && !(key === "state" && values.length === states.length) && <div className="mt-2 border-t pt-2"><Button variant="ghost" className="w-full" onClick={() => clear(key)}>{key === "state" ? "Include all states" : "Clear filters"}</Button></div>}
    </>;
  }
  return <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div className="flex min-w-0 flex-wrap items-center gap-2 sm:flex-1">
      <div className="relative w-full sm:w-56">
        <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" aria-hidden="true" />
        <Input ref={searchInput} aria-label="Search pull requests" placeholder="Search pull requests…" className="h-9 bg-card pl-9 pr-9" value={draft.value} maxLength={INBOX_QUERY_MAX_LENGTH} onChange={(e) => setDraft({ query, value: e.target.value })} />
        {draft.value && <Button variant="ghost" size="icon" className="absolute right-0 top-0" aria-label="Clear search" onClick={() => { setDraft({ query, value: "" }); onSearch(""); searchInput.current?.focus(); }}><X className="size-3.5" /></Button>}
      </div>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild><Button ref={filtersButton} variant="outline" className="bg-card"><ListFilter className="text-muted-foreground" />Filters</Button></DropdownMenuTrigger>
        <DropdownMenuContent align="start" collisionPadding={16} className="w-56 max-w-[calc(100vw-2rem)]" onCloseAutoFocus={(event) => {
          if (!pendingFilter.current) return;
          // Open after the menu releases focus so it cannot dismiss the popover.
          event.preventDefault();
          setOptionQuery("");
          setOpen(pendingFilter.current);
          pendingFilter.current = null;
        }}>
          {available.map((key) => <DropdownMenuItem key={key} onSelect={() => { pendingFilter.current = key; }}>{properties[key]}</DropdownMenuItem>)}
          {available.length === 0 && <p className="px-2 py-1.5 text-sm text-muted-foreground">All filters are in use.</p>}
        </DropdownMenuContent>
      </DropdownMenu>
      {(Object.keys(properties) as Property[]).map((key) => {
        const values = selected(params, key);
        const names = values.map((value) => summary(key, value));
        const active = isActive(key);
        if (!active && open !== key) return null;
        return <div key={key} className="flex max-w-full min-w-0 items-center rounded-md border bg-card shadow-xs dark:border-input">
          <Popover open={open === key} onOpenChange={(value) => { setOpen(value ? key : null); setOptionQuery(""); }}>
            <PopoverTrigger asChild><Button variant="ghost" className={`h-[34px] min-w-0 shrink gap-2 px-3 ${active ? "rounded-r-none" : ""}`} aria-label={`Edit ${properties[key]}: ${names.join(", ") || "Any"}`}>
              <PlusCircle className="size-4 text-muted-foreground" /><span className="shrink-0">{properties[key]}</span>
              {active && <>
                <span className="h-4 border-l" aria-hidden="true" />
                <Badge variant="secondary" className="rounded-sm px-1.5 sm:hidden">{names.length}</Badge>
                <span className="hidden min-w-0 gap-1 sm:flex">
                  {names.length > 2 ? <Badge variant="secondary" className="rounded-sm px-1.5">{names.length} selected</Badge> : names.map((name, index) => <Badge key={values[index]} variant="secondary" className="min-w-0 max-w-40 shrink rounded-sm px-1.5"><span className="truncate">{name}</span></Badge>)}
                </span>
              </>}
            </Button></PopoverTrigger>
            <PopoverContent align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] border-0 p-2 ring-1 ring-border" aria-label={`Filter by ${properties[key]}`} onCloseAutoFocus={(event) => { if (!active) { event.preventDefault(); filtersButton.current?.focus(); } }}>
              <p className="px-2 py-2 text-sm font-medium">{properties[key]}</p>{options(key)}
            </PopoverContent>
          </Popover>
          {active && <Button variant="ghost" size="icon" className="h-[34px] w-8 rounded-l-none" aria-label={`Remove ${properties[key]} filter`} onClick={() => clear(key)}><X className="size-3.5 text-muted-foreground" /></Button>}
        </div>;
      })}
    </div>
    <div className="flex shrink-0 items-center gap-2" role="group" aria-label="Ordering">
      <DropdownMenu>
        <DropdownMenuTrigger asChild><Button ref={sortButton} variant="outline" className="bg-card" aria-label={`Sort by ${orderings[field]}`}><ArrowUpDown className="text-muted-foreground" />{orderings[field]}</Button></DropdownMenuTrigger>
        <DropdownMenuContent align="end" collisionPadding={16} className="w-56 max-w-[calc(100vw-2rem)]">
          <DropdownMenuRadioGroup value={field} onValueChange={(value) => { if (value !== field) onChange("ordering", [value.endsWith("_at") ? `-${value}` : value]); }}>
            {(Object.keys(orderings) as OrderingField[]).map((key) => <DropdownMenuRadioItem key={key} value={key}>{orderings[key]}</DropdownMenuRadioItem>)}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <p className="px-2 py-1.5 text-xs text-muted-foreground">Within each group. Running and queued reviews stay first.</p>
        </DropdownMenuContent>
      </DropdownMenu>
      <Button variant="outline" size="icon" className="bg-card" title={direction} aria-label={descending ? "Sort ascending" : "Sort descending"} onClick={() => onChange("ordering", [descending ? field : `-${field}`])}>{descending ? <ArrowDown /> : <ArrowUp />}</Button>
      {params.has("ordering") && <Button variant="ghost" size="icon" aria-label="Reset ordering" title="Reset to newest first" onClick={() => { onChange("ordering", []); sortButton.current?.focus(); }}><X /></Button>}
    </div>
  </div>;
}
