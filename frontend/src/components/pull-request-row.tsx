import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { GitPullRequest } from "lucide-react";
import { date, Status } from "./common";
import { Badge } from "./ui/badge";
import { PullRequestActions } from "./pull-request-actions";

const formatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

export function PullRequestListHeading({ dateLabel = "Created" }: { dateLabel?: string }) {
  return <div aria-hidden="true" className="inbox-row-grid hidden border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground md:grid">
    <span>Pull request</span><span>Review / PR state</span><span>Open findings</span><span>{dateLabel}</span><span />
  </div>;
}

export function PullRequestStatus({ reviewStatus, state }: { reviewStatus: string; state: string }) {
  return <span className="inline-flex w-fit overflow-hidden rounded-full border border-border align-middle [&>[data-slot=badge]]:rounded-none [&>[data-slot=badge]]:border-0">
    <span className="sr-only">Review status: </span><Status value={reviewStatus} />
    <span className="sr-only">; Pull request state: </span><Badge variant="secondary" className="capitalize"><GitPullRequest aria-hidden="true" />{state}</Badge>
  </span>;
}

export function PullRequestRow({ id, number, repositoryId, admin, ignored, title, metadata, status, findings, timestamp, state }: {
  id: string;
  number: number;
  repositoryId: number;
  admin: boolean;
  ignored?: boolean;
  title: string;
  metadata: ReactNode;
  status: ReactNode;
  findings: number;
  timestamp: string | null;
  state: { inbox?: string; repository?: string };
}) {
  return <div data-pull-row className="inbox-row-grid group relative grid gap-y-3 border-b last:border-b-0 px-4 py-4 text-sm transition-colors hover:bg-muted/50 md:items-center md:px-5 md:py-3">
    <div className="flex min-w-0 items-start gap-3 pr-12 sm:items-center md:pr-0">
      <GitPullRequest aria-hidden="true" className="mt-1 size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <p className="break-words font-medium group-hover:text-primary"><Link to={`/pulls/${id}`} state={state} className="after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring">{title}</Link></p>
        <p className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted-foreground">{metadata}</p>
        {ignored && <Badge variant="secondary" className="mt-2">Ignored</Badge>}
      </div>
    </div>
    <div className="min-w-0 space-y-1">{status}</div>
    <div><Badge variant="outline" className={findings ? "border-destructive/20 bg-destructive/5 text-destructive" : "bg-muted/50 text-muted-foreground"}>{findings} unresolved<span className="sr-only"> findings</span></Badge></div>
    {timestamp ? <time dateTime={timestamp} title={date(timestamp)} className="text-xs tabular-nums text-muted-foreground">{formatter.format(new Date(timestamp))}</time> : <span className="text-xs text-muted-foreground" title="Creation date will be available after the next sync">Pending sync</span>}
    <div className="absolute right-2 top-2 z-10 md:static md:justify-self-end"><PullRequestActions id={id} number={number} title={title} repositoryId={repositoryId} admin={admin} ignored={ignored} state={state} /></div>
  </div>;
}
