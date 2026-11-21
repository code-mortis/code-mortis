import { ArrowUpRight, Globe, LockKeyhole, Settings2, type LucideIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { Link } from "react-router-dom";
import { date, PageTitle } from "./common";
import { Button } from "./ui/button";

export const repositoryHeaderLink = "inline-flex min-h-11 items-center gap-1 rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring sm:min-h-6";
export const repositoryActivityLink = `${repositoryHeaderLink} w-fit text-xs font-semibold text-primary`;

export function RepositorySettingsShortcut({ repositoryId, section }: {
  repositoryId: number;
  section: "reviews" | "scan-limits";
}) {
  const label = section === "reviews" ? "Review settings" : "Scan settings";
  return <Button asChild variant="outline" size="icon-sm" className="size-11 sm:size-8 pointer-coarse:size-11">
    <Link to={`/repositories/${repositoryId}/settings?tab=${section}`} aria-label={label} title={label}>
      <Settings2 aria-hidden="true" />
    </Link>
  </Button>;
}

export function RepositoryHeader({
  repository, children, ...titleProps
}: Pick<ComponentProps<typeof PageTitle>, "title" | "headingProps" | "actions"> & {
  repository: { full_name: string; private: boolean };
  children: ReactNode;
}) {
  const Visibility = repository.private ? LockKeyhole : Globe;
  return (
    <PageTitle {...titleProps} footer={
      <div data-slot="repository-header-summary" className="mt-3.5 grid grid-cols-[repeat(auto-fit,minmax(min(100%,15rem),1fr))] gap-3">
        {children}
      </div>
    }>
      <div className="flex flex-wrap items-center gap-x-4 text-xs">
        <span className="inline-flex items-center gap-1.5">
          <Visibility aria-hidden="true" className="size-3.5" />
          {repository.private ? "Private repository" : "Public repository"}
        </span>
        <a href={`https://github.com/${repository.full_name}`} target="_blank" rel="noreferrer" className={repositoryHeaderLink}>
          View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" />
        </a>
      </div>
    </PageTitle>
  );
}

export function RepositoryCardHeading({ label, icon: Icon, id, children }: {
  label: string;
  icon: LucideIcon;
  id?: string;
  children?: ReactNode;
}) {
  return <div className="flex min-h-[26px] min-w-0 flex-wrap items-center justify-between gap-x-2 gap-y-1">
    <h2 id={id} className="flex min-w-0 items-center gap-2 text-[15px] font-[650]">
      <Icon aria-hidden="true" className="size-[18px] shrink-0 text-primary" strokeWidth={1.65} />
      <span className="min-w-0 wrap-anywhere">{label}</span>
    </h2>
    <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-2 empty:hidden">{children}</div>
  </div>;
}

export function RepositorySummaryCard({
  label, icon, regionLabel, badge, badgeInHeading = false, children, action, details, timestamp, note,
}: {
  label: string;
  icon: LucideIcon;
  regionLabel?: string;
  badge?: ReactNode;
  badgeInHeading?: boolean;
  children?: ReactNode;
  action?: ReactNode;
  details?: [string, ReactNode][];
  timestamp?: string | null;
  note?: string;
}) {
  const detailsOnRight = children && !timestamp;
  return (
    <section aria-label={regionLabel ?? label} className="flex min-w-0 flex-col gap-2 rounded-lg border bg-card px-3.5 py-2.5 text-card-foreground">
      <RepositoryCardHeading label={label} icon={icon}>
        {action}
        {badgeInHeading && badge}
      </RepositoryCardHeading>
      {(children || details || timestamp || (!badgeInHeading && badge)) && <div className="flex min-w-0 items-baseline gap-2 text-xs leading-4 tabular-nums">
        {children && <span className="min-w-0 wrap-anywhere text-muted-foreground">{children}</span>}
        {!badgeInHeading && badge}
        {details && <dl className={`min-w-0 space-y-0.5 ${detailsOnRight ? "ml-auto text-right" : ""}`}>
          {details.map(([key, value]) => value != null && <div key={key} className={`flex flex-wrap items-baseline gap-x-2 ${detailsOnRight ? "justify-end" : ""}`}>
            <dt className="text-muted-foreground">{key}</dt>
            <dd className="min-w-0 wrap-anywhere">{value}</dd>
          </div>)}
        </dl>}
        {timestamp && <time dateTime={timestamp} className="ml-auto min-w-0 wrap-anywhere text-right text-muted-foreground">{date(timestamp)}</time>}
      </div>}
      {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
    </section>
  );
}
