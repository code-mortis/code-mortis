import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { GitMerge, GitPullRequest, GitPullRequestClosed } from "lucide-react";
import { reviewStatusLabel } from "../lib/inbox-filters";
import { FindingCount, ListDate, Status } from "./common";
import { Badge } from "./ui/badge";
import { PullRequestActions } from "./pull-request-actions";

export function PullRequestListHeading({ dateLabel = "Created" }: { dateLabel?: string }) {
  return <div aria-hidden="true" className="pull-request-row-grid hidden border-b bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground md:px-5">
    <span>Pull request</span><span>Review</span><span>PR state</span><span>Open findings</span><span>{dateLabel}</span><span />
  </div>;
}

const prStateIcons = new Map([["merged", GitMerge], ["closed", GitPullRequestClosed]]);

export function PullRequestRow({ id, number, repositoryId, admin, ignored, title, metadata, reviewStatus, prState, hasRunningReview, findings, timestamp, state }: {
  id: string;
  number: number;
  repositoryId: number;
  admin: boolean;
  ignored?: boolean;
  title: string;
  metadata: ReactNode;
  reviewStatus: string;
  prState: string;
  hasRunningReview: boolean;
  findings: number;
  timestamp: string | null;
  state: { inbox?: string; repository?: string };
}) {
  const StateIcon = prStateIcons.get(prState) ?? GitPullRequest;
  return <div data-pull-row className="pull-request-row-grid group relative grid items-center gap-y-3 border-b last:border-b-0 px-4 py-4 text-sm transition-colors hover:bg-muted/50 md:px-5 md:py-3">
    <div className="flex min-w-0 items-start gap-3 pr-12 sm:items-center">
      <GitPullRequest aria-hidden="true" className="mt-1 size-4 shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium group-hover:text-primary"><Link to={`/pulls/${id}`} state={state} title={title} className="after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring">{title}</Link></p>
        <p className="mt-2 flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted-foreground">{metadata}</p>
        {ignored && <Badge variant="secondary" className="mt-2">Ignored</Badge>}
      </div>
    </div>
    <div className="min-w-0 space-y-1">
      <span className="sr-only">Review status: </span><Status value={reviewStatus} label={reviewStatusLabel(reviewStatus)} />
      {hasRunningReview && reviewStatus !== "running" && <p className="text-xs text-muted-foreground">A review is still running</p>}
    </div>
    <div className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
      <span className="sr-only">Pull request state: </span><StateIcon aria-hidden="true" className="size-3 shrink-0" />
      <span className="capitalize">{prState}</span>
    </div>
    <div><FindingCount count={findings} /></div>
    {timestamp ? <ListDate value={timestamp} /> : <span className="text-xs text-muted-foreground" title="Creation date will be available after the next sync">Pending sync</span>}
    <div className="pull-request-row-actions absolute right-2 top-2 z-10"><PullRequestActions id={id} number={number} title={title} repositoryId={repositoryId} admin={admin} ignored={ignored} state={state} /></div>
  </div>;
}
