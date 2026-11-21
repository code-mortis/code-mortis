import { ArrowUpRight, Globe, LockKeyhole } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { PageTitle } from "./common";

export const repositoryHeaderLink = "inline-flex min-h-11 items-center gap-1 rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring sm:min-h-6";
export const repositoryActivityLink = `${repositoryHeaderLink} w-fit text-xs font-semibold text-primary`;

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

export function RepositorySummaryCard({
  label, regionLabel, badge, badgeInHeading = false, children, action, details, note,
}: {
  label: string;
  regionLabel?: string;
  badge?: ReactNode;
  badgeInHeading?: boolean;
  children?: ReactNode;
  action?: ReactNode;
  details?: [string, ReactNode][];
  note?: string;
}) {
  return (
    <section aria-label={regionLabel ?? label} className="flex min-w-0 flex-col gap-1 rounded-lg border bg-card px-3.5 py-2.5 text-card-foreground">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2.5 gap-y-1">
        <h2 className="min-w-0 text-xs leading-5 font-medium text-muted-foreground">{label}</h2>
        {action}
        {badgeInHeading && badge}
      </div>
      {(children || (!badgeInHeading && badge)) && <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-base leading-5 font-semibold tabular-nums">
        {children && <span className="min-w-0 wrap-anywhere">{children}</span>}
        {!badgeInHeading && badge}
      </div>}
      {details && <dl className="space-y-0.5 text-xs leading-4 tabular-nums">
        {details.map(([key, value]) => value != null && <div key={key} className="flex flex-wrap items-baseline gap-x-2">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="min-w-0 wrap-anywhere">{value}</dd>
        </div>)}
      </dl>}
      {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
    </section>
  );
}
