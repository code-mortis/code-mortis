import { ChevronRight, GitPullRequest, ShieldCheck, type LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi, type Repository, type Schema } from "../api";
import { date } from "./common";
import { Badge } from "./ui/badge";

const numberFormat = new Intl.NumberFormat();
const scanDateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const countLink = "inline-flex min-h-6 min-w-6 items-center justify-end rounded-sm whitespace-nowrap text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

export function RepositoryQuickView({ repository }: { repository: Repository }) {
  const { id, enabled, settings } = repository;
  const reviews = useApi<Schema["AnalyticsFindings"]>(`/analytics/findings?repo=${id}&all_time=true&status=open`);
  const reviewsPath = `/repositories/${id}`;
  const needsReviewFilters = "state=open&review=not_reviewed&review=outdated&strict_state=true";
  const needsReview = useApi<Pick<Schema["InboxOut"], "total">>(`${reviewsPath}/pulls?${needsReviewFilters}`);
  const needsReviewCount = needsReview.error ? undefined : needsReview.data?.total;
  const securityPath = `/security/repositories/${id}`;
  const security = useApi<Schema["SecurityRepositoryOut"]>(securityPath);
  const summary = security.error ? undefined : security.data?.summary;
  const counts = security.error ? undefined : security.data?.finding_state_counts;
  const openSeverities = security.error ? undefined : security.data?.open_finding_severity_counts;
  const lastCompletedScan = security.error ? undefined : security.data?.last_completed_scan_at;
  const outdatedGrade = summary?.grade && summary.freshness === "outdated";

  return <div className="mb-6 grid gap-3.5 md:grid-cols-2">
    <QuickViewCard
      title="Reviews" icon={GitPullRequest} to={reviewsPath}
      badge={<Badge variant={enabled ? "default" : "outline"}>
        {enabled ? "Enabled" : "Paused"}
      </Badge>}
      count={reviews.error ? undefined : reviews.data?.total}
      pending={reviews.isPending}
      details={[
        ["Automatic reviews", enabled && settings.automatic ? "On" : "Off"],
        ["PRs needing review", needsReviewCount === undefined ? needsReview.isPending ? "Loading…" : "Unavailable"
          : <Link className={`${countLink} underline decoration-primary/45 underline-offset-4 hover:decoration-current`} to={`${reviewsPath}?${needsReviewFilters}`}
            aria-label={`${numberFormat.format(needsReviewCount)} ${needsReviewCount === 1 ? "PR" : "PRs"} needing review`}>
            {numberFormat.format(needsReviewCount)}
          </Link>],
      ]}
    />
    <QuickViewCard
      title="Security" icon={ShieldCheck} to={securityPath}
      badge={summary && <Badge variant={summary.grade ? "default" : "outline"}
        title={outdatedGrade ? "Grade is from an older revision" : undefined}>
        {summary.grade ? `Grade ${summary.grade}` : summary.latest_scan_id ? "Unrated" : "Unscanned"}
        {outdatedGrade && " · Outdated"}
      </Badge>}
      count={counts ? counts.open ?? 0 : undefined}
      pending={security.isPending}
      details={[
        ["Priority findings", openSeverities ? <span className="flex flex-wrap justify-end gap-x-2 gap-y-1">
          {(["critical", "high"] as const).map((severity) => {
            const count = openSeverities[severity] ?? 0;
            return <Link key={severity} className={`${countLink} no-underline hover:text-foreground`} to={`${securityPath}?severity=${severity}`}
              aria-label={`${numberFormat.format(count)} open ${severity} ${count === 1 ? "finding" : "findings"}`}>
              {numberFormat.format(count)} {severity}
            </Link>;
          })}
        </span> : security.isPending ? "Loading…" : "Unavailable"],
        ["Latest completed scan", lastCompletedScan
          ? <time dateTime={lastCompletedScan} title={date(lastCompletedScan)}>{scanDateFormat.format(new Date(lastCompletedScan))}</time>
          : lastCompletedScan === null ? "Never" : security.isPending ? "Loading…" : "Unavailable"],
      ]}
    />
  </div>;
}

function QuickViewCard({ title, icon: Icon, to, badge, count, pending, details }: {
  title: string;
  icon: LucideIcon;
  to: string;
  badge: ReactNode;
  count?: number;
  pending: boolean;
  details: [string, ReactNode][];
}) {
  const heading = useId();
  return <section aria-labelledby={heading} className="flex min-w-0 flex-col rounded-lg border border-[#dce1d9] bg-card px-[17px] pt-3.5 text-[#202c26] [--quick-view-muted:#606a62] dark:border-border dark:text-foreground dark:[--quick-view-muted:var(--muted-foreground)]">
    <div className="flex min-h-[26px] flex-wrap items-center justify-between gap-2">
      <h2 id={heading} className="flex items-center gap-2 text-[15px] font-[650]">
        <Icon aria-hidden="true" className="size-[18px] text-primary" strokeWidth={1.65} />{title}
      </h2>
      {badge}
    </div>
    <p className="mt-[7px] mb-[11px] flex min-h-[42px] flex-wrap items-baseline gap-x-2 text-sm text-(--quick-view-muted)">
      {count === undefined ? pending ? "Loading findings…" : "Finding count unavailable" : <>
        <strong className="text-4xl leading-[1.18] font-[650] tracking-[-0.04em] text-[#202c26] tabular-nums dark:text-foreground">{numberFormat.format(count)}</strong>
        {count === 1 ? "open finding" : "open findings"}
      </>}
    </p>
    <dl className="mb-[11px] space-y-[5px] text-[13px] leading-[19px]">
      {details.map(([label, value]) => <div key={label} className="grid grid-cols-[1fr_minmax(0,1fr)] items-baseline gap-2">
        <dt className="text-(--quick-view-muted)">{label}</dt>
        <dd className="text-right font-[550] wrap-anywhere">{value}</dd>
      </div>)}
    </dl>
    <div className="mt-auto border-t border-inherit">
      <Link to={to} className="inline-flex min-h-11 items-center gap-0.5 rounded-sm text-xs font-[650] text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring sm:min-h-[36px]">
        View {title.toLowerCase()}<ChevronRight aria-hidden="true" className="size-3.5" />
      </Link>
    </div>
  </section>;
}
