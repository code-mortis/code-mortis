import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { normalizeOrdering } from "../lib/ordering";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";

export type OrderingProps = {
  value?: string | null;
  options: Record<string, string>;
  defaultValue: string;
  onChange: (value: string) => void;
  hint?: string;
};

export function Ordering({ value, options, defaultValue, onChange, hint }: OrderingProps) {
  const ordering = normalizeOrdering(value, options, defaultValue);
  const field = ordering.replace(/^-/, "");
  const descending = ordering.startsWith("-");
  const date = field.endsWith("_at");
  const numeric = ["severity", "count"].includes(field);
  const direction = date ? descending ? "Newest first" : "Oldest first" : numeric ? descending ? "Highest first" : "Lowest first"
    : field === "position" ? descending ? "Reverse order" : "Original order" : descending ? "Z to A" : "A to Z";
  return <div className="flex shrink-0 self-end items-center gap-2 sm:self-auto" role="group" aria-label="Ordering">
    <DropdownMenu>
      <DropdownMenuTrigger asChild><Button variant="outline" className="bg-card" aria-label={`Sort by ${options[field]}`}><ArrowUpDown className="text-muted-foreground" />{options[field]}</Button></DropdownMenuTrigger>
      <DropdownMenuContent align="end" collisionPadding={16} className="w-56 max-w-[calc(100vw-2rem)]">
        <DropdownMenuRadioGroup value={field} onValueChange={(next) => { if (next !== field) onChange(next.endsWith("_at") || ["severity", "count"].includes(next) ? `-${next}` : next); }}>
          {Object.entries(options).map(([key, name]) => <DropdownMenuRadioItem key={key} value={key}>{name}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
        {hint && <><DropdownMenuSeparator /><p className="px-2 py-1.5 text-xs text-muted-foreground">{hint}</p></>}
      </DropdownMenuContent>
    </DropdownMenu>
    <Button variant="outline" size="icon" className="bg-card" title={direction} aria-label={descending ? "Sort ascending" : "Sort descending"} onClick={() => onChange(descending ? field : `-${field}`)}>{descending ? <ArrowDown /> : <ArrowUp />}</Button>
  </div>;
}
