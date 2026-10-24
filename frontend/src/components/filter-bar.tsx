import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ListFilter, PlusCircle, Search, X } from "lucide-react";
import { INBOX_QUERY_MAX_LENGTH, INBOX_SEARCH_DEBOUNCE_MS } from "../lib/inbox-filters";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Input } from "./ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Ordering, type OrderingProps } from "./ordering";

export type FilterDefinition<Key extends string> = {
  key: Key;
  label: string;
  values: string[];
  choices: { value: string; name: string; count?: number }[];
  active?: boolean;
  single?: boolean;
  required?: boolean;
  searchPlaceholder?: string;
  hint?: string;
  clearLabel?: string;
  clearValues?: string[];
};

export function FilterBar<Key extends string>({ query = "", searchResetKey = 0, searchLabel, searchPlaceholder, searchInputRef, filters, onChange, onSearch, children, ordering }: {
  query?: string;
  searchResetKey?: number;
  searchLabel?: string;
  searchPlaceholder?: string;
  searchInputRef?: RefObject<HTMLInputElement | null>;
  filters: FilterDefinition<Key>[];
  onChange: (key: Key, values: string[]) => void;
  onSearch?: (value: string) => void;
  children?: ReactNode;
  ordering?: OrderingProps;
}) {
  const [draft, setDraft] = useState({ query, searchResetKey, value: query });
  const localSearchInput = useRef<HTMLInputElement>(null);
  const searchInput = searchInputRef ?? localSearchInput;
  // Navigation wins before effects run: https://react.dev/reference/react/useState#storing-information-from-previous-renders
  if (draft.query !== query || draft.searchResetKey !== searchResetKey) setDraft({ query, searchResetKey, value: query });
  useEffect(() => {
    if (!onSearch || draft.value === query || draft.query !== query || draft.searchResetKey !== searchResetKey) return;
    const timer = setTimeout(() => onSearch(draft.value), INBOX_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, query, searchResetKey, onSearch]);
  const [open, setOpen] = useState<Key | null>(null);
  const [optionQuery, setOptionQuery] = useState("");
  const filtersButton = useRef<HTMLButtonElement>(null);
  const pendingFilter = useRef<Key | null>(null);
  const isActive = (filter: FilterDefinition<Key>) => filter.active ?? filter.values.length > 0;
  const available = filters.filter((filter) => !isActive(filter));
  function clear(filter: FilterDefinition<Key>) {
    onChange(filter.key, filter.clearValues ?? []);
    setOpen(null);
    filtersButton.current?.focus();
  }
  function options(filter: FilterDefinition<Key>) {
    const choices = filter.choices.filter((choice) => choice.name.toLowerCase().includes(optionQuery.toLowerCase()));
    return <>
      {filter.searchPlaceholder && <Input autoFocus aria-label={`Search ${filter.label.toLowerCase()} options`} placeholder={filter.searchPlaceholder} value={optionQuery} onChange={(event) => setOptionQuery(event.target.value)} className="mb-2" />}
      <div className="max-h-64 overflow-y-auto" role="group" aria-label={filter.label}>
        {choices.map((choice) => <label key={choice.value} className="flex min-h-10 cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm hover:bg-accent has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring has-[:focus-visible]:ring-inset">
          <Checkbox checked={filter.values.includes(choice.value)} disabled={filter.required && filter.values.length === 1 && filter.values.includes(choice.value)} onCheckedChange={(checked) => {
            const next = checked !== true ? filter.values.filter((value) => value !== choice.value)
              : filter.single ? [choice.value] : [...filter.values, choice.value];
            onChange(filter.key, next);
          }} />
          <span className="min-w-0 break-words">{choice.name}</span>
          {choice.count !== undefined && <span className="ml-auto tabular-nums text-muted-foreground">{choice.count}</span>}
        </label>)}
        {!choices.length && <p className="p-3 text-sm text-muted-foreground">No options found.</p>}
      </div>
      {filter.hint && <p className="px-2 pt-2 text-xs text-muted-foreground">{filter.hint}</p>}
      {isActive(filter) && <div className="mt-2 border-t pt-2"><Button variant="ghost" className="w-full" onClick={() => clear(filter)}>{filter.clearLabel ?? "Clear filters"}</Button></div>}
    </>;
  }
  return <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div className="flex min-w-0 flex-wrap items-center gap-2 sm:flex-1">
      {onSearch && <div className="relative w-full sm:w-56">
        <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" aria-hidden="true" />
        <Input ref={searchInput} aria-label={searchLabel} placeholder={searchPlaceholder} className="h-9 bg-card pl-9 pr-9" value={draft.value} maxLength={INBOX_QUERY_MAX_LENGTH} onChange={(event) => setDraft({ query, searchResetKey, value: event.target.value })} />
        {draft.value && <Button variant="ghost" size="icon" className="absolute right-0 top-0" aria-label="Clear search" onClick={() => { setDraft({ query, searchResetKey, value: "" }); onSearch(""); searchInput.current?.focus(); }}><X className="size-3.5" /></Button>}
      </div>}
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
          {available.map((filter) => <DropdownMenuItem key={filter.key} onSelect={() => { pendingFilter.current = filter.key; }}>{filter.label}</DropdownMenuItem>)}
          {available.length === 0 && <p className="px-2 py-1.5 text-sm text-muted-foreground">All filters are in use.</p>}
        </DropdownMenuContent>
      </DropdownMenu>
      {filters.map((filter) => {
        const names = filter.values.map((value) => filter.choices.find((choice) => choice.value === value)?.name ?? value);
        const active = isActive(filter);
        if (!active && open !== filter.key) return null;
        return <div key={filter.key} className="flex max-w-full min-w-0 items-center rounded-md border bg-card shadow-xs dark:border-input">
          <Popover open={open === filter.key} onOpenChange={(value) => { setOpen(value ? filter.key : null); setOptionQuery(""); }}>
            <PopoverTrigger asChild><Button variant="ghost" className={`h-[34px] min-w-0 shrink gap-2 px-3 ${active ? "rounded-r-none" : ""}`} aria-label={`Edit ${filter.label}: ${names.join(", ") || "Any"}`}>
              <PlusCircle className="size-4 text-muted-foreground" /><span className="shrink-0">{filter.label}</span>
              {active && <>
                <span className="h-4 border-l" aria-hidden="true" />
                <Badge variant="secondary" className="rounded-sm px-1.5 sm:hidden">{names.length}</Badge>
                <span className="hidden min-w-0 gap-1 sm:flex">
                  {names.length > 2 ? <Badge variant="secondary" className="rounded-sm px-1.5">{names.length} selected</Badge> : names.map((name, index) => <Badge key={filter.values[index]} variant="secondary" className="min-w-0 max-w-40 shrink rounded-sm px-1.5"><span className="truncate">{name}</span></Badge>)}
                </span>
              </>}
            </Button></PopoverTrigger>
            <PopoverContent align="start" sideOffset={8} collisionPadding={16} className="max-w-[calc(100vw-2rem)] border-0 p-2 ring-1 ring-border" aria-label={`Filter by ${filter.label}`} onCloseAutoFocus={(event) => { if (!active) { event.preventDefault(); filtersButton.current?.focus(); } }}>
              <p className="px-2 py-2 text-sm font-medium">{filter.label}</p>{options(filter)}
            </PopoverContent>
          </Popover>
          {active && <Button variant="ghost" size="icon" className="h-[34px] w-8 rounded-l-none" aria-label={`Remove ${filter.label} filter`} onClick={() => clear(filter)}><X className="size-3.5 text-muted-foreground" /></Button>}
        </div>;
      })}
      {children}
    </div>
    {ordering && <Ordering {...ordering} />}
  </div>;
}
