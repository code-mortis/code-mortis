import type { MouseEventHandler, ReactNode } from "react";
import { Bug, Gauge, Shield, Tag, Wrench } from "lucide-react";
import { Status } from "./common";
import { Badge } from "./ui/badge";
import { label } from "../lib/inbox-filters";

export function FindingCategory({ value }: { value: string }) {
  const icons = new Map([["bug", Bug], ["security", Shield], ["performance", Gauge], ["maintainability", Wrench]]);
  const Icon = icons.get(value) ?? Tag;
  return <Badge variant="outline" className="gap-1.5 text-muted-foreground"><Icon aria-hidden="true" />{label(value)}</Badge>;
}

export function FindingCard({ evidence, state, onClick }: {
  evidence: Record<string, unknown>;
  state: ReactNode; onClick: MouseEventHandler<HTMLButtonElement>;
}) {
  const path = String(evidence.path ?? "Path not recorded");
  const slash = path.lastIndexOf("/");
  return <button type="button" data-slot="finding-card" aria-haspopup="dialog" onClick={onClick}
    className="group grid w-full min-w-0 gap-x-4 gap-y-1.5 bg-card px-4 py-3 text-left text-card-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:grid-cols-[minmax(0,1fr)_auto] sm:px-5">
    <span className="line-clamp-2 min-w-0 break-words text-sm font-medium leading-5 [overflow-wrap:anywhere]">{String(evidence.content ?? "Finding evidence").replaceAll(/[*`#]/g, "")}</span>
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground sm:col-start-1 sm:flex-nowrap">
      <span className="flex min-w-0 flex-wrap items-center gap-2 sm:shrink-0">
        {evidence.severity != null ? <Status value={String(evidence.severity)} /> : <span className="shrink-0">Proposed finding</span>}
        {!!evidence.category && <FindingCategory value={String(evidence.category)} />}
      </span>
      <span className="flex min-w-0 items-baseline font-mono">
        <span className="min-w-0 truncate" title={path}>
          {slash >= 0 && <span>{path.slice(0, slash + 1)}</span>}<span className="font-medium text-card-foreground">{path.slice(slash + 1)}</span>
        </span>
        {evidence.start_line != null && <span className="shrink-0">:{String(evidence.start_line)}{evidence.end_line && evidence.end_line !== evidence.start_line ? `–${evidence.end_line}` : ""}</span>}
      </span>
    </span>
    <span className="flex min-w-0 items-start justify-self-start sm:col-start-2 sm:row-start-1 sm:row-span-2 sm:max-w-48 sm:justify-self-end">{state}</span>
  </button>;
}

