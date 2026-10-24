import type { ReactNode } from "react";
import { ChevronDown } from "lucide-react";

export function CoverageGroup({ title, count, children }: {
  title: string; count: ReactNode; children: ReactNode;
}) {
  return <section aria-label={title} className="min-w-0">
    <details className="group/coverage overflow-hidden rounded-lg border bg-card text-card-foreground">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 p-4 transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5 [&::-webkit-details-marker]:hidden">
        <h3 className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 text-sm font-medium">{title}<span className="text-xs font-normal tabular-nums text-muted-foreground">{count}</span></h3>
        <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open/coverage:rotate-180" />
      </summary>
      <div className="divide-y border-t">{children}</div>
    </details>
  </section>;
}
