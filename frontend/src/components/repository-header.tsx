import { ArrowUpRight, Globe, History, LockKeyhole, type LucideIcon } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { PageTitle } from "./common";

export const repositoryHeaderLink = "inline-flex min-h-11 items-center gap-1 rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring sm:min-h-6";
export const repositoryActivityLink = `${repositoryHeaderLink} w-fit text-xs font-semibold text-primary sm:min-h-8`;
const summaryCard = "min-w-0 rounded-xl border bg-card px-[17px] pt-3.5 text-card-foreground";

export function RepositoryHeader({
  repository, children, ...titleProps
}: Pick<ComponentProps<typeof PageTitle>, "title" | "headingProps" | "actions"> & {
  repository: { full_name: string; private: boolean };
  children: ReactNode;
}) {
  const Visibility = repository.private ? LockKeyhole : Globe;
  return (
    <PageTitle {...titleProps} footer={
      <div data-slot="repository-header-summary" className="@container mt-3.5">
        <div className="grid gap-3.5 @min-[700px]:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          {children}
        </div>
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

function SummaryHeading({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <h2 className="flex min-w-0 items-center gap-2 text-[15px] leading-5 font-semibold">
      <Icon aria-hidden="true" strokeWidth={1.65} className="size-[18px] shrink-0 text-primary" />
      {children}
    </h2>
  );
}

export function RepositoryStatusCard({
  label, regionLabel, icon, badge, children, details, note,
}: {
  label: string;
  regionLabel?: string;
  icon: LucideIcon;
  badge: ReactNode;
  children?: ReactNode;
  details?: [string, ReactNode][];
  note?: string;
}) {
  return (
    <section aria-label={regionLabel ?? label} className={`${summaryCard} flex flex-col gap-3 pb-3.5 only:col-span-full`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SummaryHeading icon={icon}>{label}</SummaryHeading>{badge}
      </div>
      {children && <div className="text-sm font-medium">{children}</div>}
      {details && <dl className="mt-auto space-y-1 border-t pt-2.5 text-xs leading-[18px] tabular-nums">
        {details.map(([key, value]) => value != null && <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.45fr)] items-baseline gap-2">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="text-right wrap-anywhere">{value}</dd>
        </div>)}
      </dl>}
      {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
    </section>
  );
}

export function RepositoryActivityCard({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className={summaryCard}>
      <SummaryHeading icon={History}>{label}</SummaryHeading>
      <div className="mt-2 divide-y">{children}</div>
    </section>
  );
}

export function RepositoryActivityRow({
  label, regionLabel, children, detail, action,
}: { label: string; regionLabel?: string; children?: ReactNode; detail?: ReactNode; action?: ReactNode }) {
  return (
    <section aria-label={regionLabel ?? label} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5">
      <div className="min-w-0 space-y-1 wrap-anywhere">
        <h3 className="text-xs text-muted-foreground">{label}</h3>
        {children && <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] leading-5 font-medium tabular-nums">{children}</div>}
        {detail && <div className="text-xs leading-normal text-muted-foreground tabular-nums">{detail}</div>}
      </div>
      {action}
    </section>
  );
}
