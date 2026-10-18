import type { MouseEventHandler, ReactNode } from "react";
import { Bug, FileCode2, Gauge, Shield, Tag, Wrench } from "lucide-react";
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
  return <button type="button" data-slot="finding-card" onClick={onClick}
    className="group w-full min-w-0 bg-card p-4 text-left text-card-foreground transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:p-5">
    <span className="flex flex-col items-start gap-3 sm:flex-row sm:justify-between">
      <span className="flex min-w-0 max-w-full items-center gap-2">
        <FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 font-mono text-xs leading-[22px]">
          <span className="flex flex-wrap items-baseline gap-x-2 gap-y-1 [overflow-wrap:anywhere]">
            <span className="min-w-0">
              {slash >= 0 && <span className="text-muted-foreground">{path.slice(0, slash + 1)}</span>}<span className="font-medium">{path.slice(slash + 1)}</span>
            </span>
            {evidence.start_line != null && <span className="text-muted-foreground">{String(evidence.start_line)}{evidence.end_line && evidence.end_line !== evidence.start_line ? `–${evidence.end_line}` : ""}</span>}
          </span>
        </span>
      </span>
      <span className="flex shrink-0">{state}</span>
    </span>
    <span className="mt-4 line-clamp-3 break-words text-sm font-medium leading-relaxed [overflow-wrap:anywhere]">{String(evidence.content ?? "Finding evidence").replaceAll(/[*`#]/g, "")}</span>
    <span className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <span className="flex flex-wrap items-center gap-2">{evidence.severity != null ? <Status value={String(evidence.severity)} /> : <span className="text-xs text-muted-foreground">Proposed finding</span>}{!!evidence.category && <FindingCategory value={String(evidence.category)} />}</span>
    </span>
  </button>;
}

