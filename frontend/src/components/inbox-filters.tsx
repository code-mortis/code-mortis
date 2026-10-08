import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, Search, SlidersHorizontal, X } from "lucide-react";
import type { Schema } from "../api";
import { label, properties, reviews, selected, states, type Property } from "../lib/inbox-filters";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

export function InboxFilters({ params, data, onChange, onSearch, onReset }: {
  params: URLSearchParams;
  data?: Schema["InboxOut"];
  onChange: (property: Property, values: string[]) => void;
  onSearch: (value: string) => void;
  onReset: () => void;
}) {
  const query = params.get("q") ?? "";
  const [draft, setDraft] = useState({ query, value: query });
  // A navigation wins over a pending search, including browser Back/Forward.
  if (draft.query !== query) setDraft({ query, value: query });
  useEffect(() => {
    if (draft.value === query || draft.query !== query) return;
    const timer = setTimeout(() => onSearch(draft.value), 300);
    return () => clearTimeout(timer);
  }, [draft, query, onSearch]);
  const [open, setOpen] = useState<string | null>(null);
  const [property, setProperty] = useState<Property | null>(null);
  const [optionQuery, setOptionQuery] = useState("");
  const filterButton = useRef<HTMLButtonElement>(null);
  const active = (Object.keys(properties) as Property[]).filter((key) => key === "state" ? (!params.getAll("state").includes("all") || open === "state") : selected(params, key).length);
  const summary = (key: Property) => selected(params, key).map((value) => key === "repo"
    ? data?.repositories.find((r) => String(r.id) === value)?.full_name ?? `Repository ${value}`
    : key === "author" ? `@${value}` : key === "findings" ? value === "open" ? "Has open findings" : "No open findings" : label(value)).join(", ");
  function options(key: Property) {
    let choices: { value: string; name: string; count?: number }[];
    if (key === "repo") choices = data?.repositories.map((r) => ({ value: String(r.id), name: r.full_name })) ?? [];
    else if (key === "author") choices = data?.authors.map((a) => ({ value: a, name: `@${a}` })) ?? [];
    else if (key === "state") choices = states.map((value) => ({ value, name: label(value), count: data?.state_counts[value] }));
    else if (key === "review") choices = reviews.map((value) => ({ value, name: label(value) }));
    else choices = [{ value: "open", name: "Has open findings" }, { value: "none", name: "No open findings" }];
    const values = selected(params, key);
    for (const value of values) if (!choices.some((c) => c.value === value)) choices.push({ value, name: key === "author" ? `@${value}` : `Repository ${value}` });
    return <>
      {(key === "repo" || key === "author") && <Input autoFocus aria-label={`Search ${properties[key].toLowerCase()} options`} placeholder={`Find ${key === "repo" ? "a repository" : "an author"}…`} value={optionQuery} onChange={(e) => setOptionQuery(e.target.value)} className="mb-2" />}
      <div className="max-h-64 overflow-y-auto" role="group" aria-label={properties[key]}>
        {choices.filter((c) => c.name.toLowerCase().includes(optionQuery.toLowerCase())).map((choice) => <label key={choice.value} className="flex min-h-10 cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-inset">
          <Checkbox checked={values.includes(choice.value)} disabled={key === "state" && values.length === 1 && values.includes(choice.value)} onCheckedChange={(checked) => {
            const next = key === "findings" ? checked === true ? [choice.value] : [] : checked === true ? [...values, choice.value] : values.filter((v) => v !== choice.value);
            onChange(key, next);
            if (!next.length) { setOpen(null); filterButton.current?.focus(); }
          }} />
          <span className="min-w-0 break-words">{choice.name}</span>
          {choice.count !== undefined && <span className="ml-auto tabular-nums text-muted-foreground">{choice.count}</span>}
        </label>)}
        {!choices.some((c) => c.name.toLowerCase().includes(optionQuery.toLowerCase())) && <p className="p-3 text-sm text-muted-foreground">No options found.</p>}
      </div>
    </>;
  }
  function opened(key: string, value: boolean) {
    setOpen(value ? key : null); setOptionQuery(""); setProperty(null);
    if (!value && key === "state" && params.getAll("state").includes("all")) filterButton.current?.focus();
  }
  return <div className="space-y-3">
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" aria-hidden="true" />
      <Input aria-label="Search pull requests" placeholder="Search pull requests…" className="h-10 bg-card pl-10" value={draft.value} maxLength={256} onChange={(e) => setDraft({ query, value: e.target.value })} />
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <Popover open={open === "add"} onOpenChange={(value) => opened("add", value)}>
        <PopoverTrigger asChild><Button ref={filterButton} variant="outline" className="bg-card"><SlidersHorizontal />Filter</Button></PopoverTrigger>
        <PopoverContent align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] border-0 p-2 ring-1 ring-border" aria-label={property ? `Filter by ${properties[property]}` : "Add filter"}>
          {property ? <><Button variant="ghost" size="sm" className="mb-2" onClick={() => { setProperty(null); setOptionQuery(""); }}><ArrowLeft />{properties[property]}</Button>{options(property)}</> : <>
            <Input aria-label="Find filter" placeholder="Find a filter…" value={optionQuery} onChange={(e) => setOptionQuery(e.target.value)} className="mb-2" />
            {(Object.keys(properties) as Property[]).filter((key) => properties[key].toLowerCase().includes(optionQuery.toLowerCase())).map((key) => <Button key={key} variant="ghost" className="w-full justify-start" onClick={() => { setProperty(key); setOptionQuery(""); }}>{properties[key]}</Button>)}
          </>}
        </PopoverContent>
      </Popover>
      {active.map((key) => <div key={key} className="flex max-w-full items-center rounded-md bg-accent text-accent-foreground">
        <Popover open={open === key} onOpenChange={(value) => opened(key, value)}>
          <PopoverTrigger asChild><Button variant="ghost" className="min-w-0 gap-2 rounded-r-none px-3" aria-label={`Edit ${properties[key]}: ${summary(key)}`}>
            <span className="shrink-0">{properties[key]}</span><span className="h-4 border-l border-current/20" /><span className="truncate">{summary(key)}</span><ChevronDown className="size-3" />
          </Button></PopoverTrigger>
          <PopoverContent align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] border-0 p-2 ring-1 ring-border" aria-label={`Filter by ${properties[key]}`}><p className="px-2 py-2 text-sm font-medium">{properties[key]}</p>{options(key)}</PopoverContent>
        </Popover>
        <Button variant="ghost" size="icon" className="rounded-l-none border-l border-current/10" aria-label={`Remove ${properties[key]} filter`} onClick={() => { onChange(key, key === "state" ? ["all"] : []); filterButton.current?.focus(); }}><X className="size-3.5" /></Button>
      </div>)}
      <Button variant="link" className="px-2 text-foreground underline" onClick={() => { setDraft({ query: "", value: "" }); onReset(); }}>Reset</Button>
    </div>
  </div>;
}
