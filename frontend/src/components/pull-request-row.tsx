import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, GitPullRequest } from "lucide-react";
import { date } from "./common";
import { Badge } from "./ui/badge";

const formatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export function PullRequestListHeading({ dateLabel = "Created" }: { dateLabel?: string }) {
  return <div aria-hidden="true" className="inbox-row-grid hidden border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground md:grid">
    <span>Pull request</span><span>Review status</span><span>Open findings</span><span>{dateLabel}</span><span />
  </div>;
}

export function PullRequestRow({ id, title, metadata, status, findings, timestamp, state, feedback }: {
  id: string;
  title: string;
  metadata: ReactNode;
  status: ReactNode;
  findings: number;
  timestamp: string | null;
  state: { inbox?: string; repository?: string };
  feedback?: string;
}) {
  return <Link to={`/pulls/${id}`} state={state} className="inbox-row-grid group relative grid gap-y-3 border-b last:border-b-0 px-4 py-4 text-sm transition-colors hover:bg-muted/50 focus-visible:-outline-offset-2 md:items-center md:px-5 md:py-3">
    <div className="flex min-w-0 items-start gap-3 pr-5 sm:items-center md:pr-0">
      <GitPullRequest aria-hidden="true" className="mt-1 size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <p className="break-words font-medium group-hover:text-primary">{title}</p>
        <p className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted-foreground">{metadata}</p>
        {feedback && <p className="mt-2 break-words text-xs text-muted-foreground">{feedback}</p>}
      </div>
    </div>
    <div className="min-w-0 space-y-1">{status}</div>
    <div><Badge variant="outline" className={findings ? "border-destructive/20 bg-destructive/5 text-destructive" : "bg-muted/50 text-muted-foreground"}>{findings} unresolved<span className="sr-only"> findings</span></Badge></div>
    {timestamp ? <time dateTime={timestamp} title={date(timestamp)} className="text-xs tabular-nums text-muted-foreground">{formatter.format(new Date(timestamp))}</time> : <span className="text-xs text-muted-foreground" title="Creation date will be available after the next sync">Pending sync</span>}
    <ChevronRight aria-hidden="true" className="absolute right-4 top-5 size-4 text-muted-foreground md:static" />
  </Link>;
}
