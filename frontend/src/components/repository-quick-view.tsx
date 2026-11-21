import { ChevronRight, GitPullRequest, ShieldCheck, type LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useApi, type Repository, type Schema } from "../api";
import { date, words } from "./common";
import { Badge } from "./ui/badge";

const numberFormat = new Intl.NumberFormat();
const scanDateFormat = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const badgeClass = "rounded-[5px] px-2 py-[3px] leading-[17px] font-semibold data-[variant=secondary]:bg-primary/10 data-[variant=secondary]:text-primary";

export function RepositoryQuickView({ repository }: { repository: Repository }) {
  const { id, enabled, settings } = repository;
  const reviews = useApi<Schema["AnalyticsFindings"]>(`/analytics/findings?repo=${id}&all_time=true&status=open`);
  const securityPath = `/security/repositories/${id}`;
  const security = useApi<Schema["SecurityRepositoryOut"]>(securityPath);
  const summary = security.error ? undefined : security.data?.summary;
  const counts = security.error ? undefined : security.data?.finding_state_counts;

  return <div className="mb-6 grid gap-3.5 md:grid-cols-2">
    <QuickViewCard
      title="Reviews" icon={GitPullRequest} to={`/repositories/${id}`}
      badge={<Badge variant={enabled ? "secondary" : "outline"} className={badgeClass}>
        {enabled ? "Enabled" : "Paused"}
      </Badge>}
      count={reviews.error ? undefined : reviews.data?.total}
      pending={reviews.isPending}
      details={[
        ["Automatic reviews", enabled && settings.automatic ? "On" : "Off"],
        ["Review language", settings.language || "—"],
      ]}
    />
    <QuickViewCard
      title="Security" icon={ShieldCheck} to={securityPath}
      badge={summary && <Badge variant={summary.grade ? "secondary" : "outline"} className={badgeClass}
        title={summary.grade && summary.freshness === "outdated" ? "Grade is from an older revision" : undefined}>
        {summary.grade ? `Grade ${summary.grade}` : summary.latest_scan_id ? "Unrated" : "Unscanned"}
        {summary.grade && summary.freshness === "outdated" && <span className="sr-only"> (outdated)</span>}
      </Badge>}
      count={counts ? counts.open ?? 0 : undefined}
      pending={security.isPending}
      details={[
        ["Latest scan", summary?.latest_at
          ? <time dateTime={summary.latest_at} title={date(summary.latest_at)}>{scanDateFormat.format(new Date(summary.latest_at))}</time>
          : summary && !summary.latest_scan_id ? "Never" : "—"],
        ["Scan status", summary?.latest_status ? <span className="capitalize">{words(summary.latest_status)}</span> : "—"],
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
