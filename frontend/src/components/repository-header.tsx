import { ArrowUpRight, Globe, LockKeyhole } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { PageTitle } from "./common";

export const repositoryHeaderLink = "inline-flex min-h-11 items-center gap-1 rounded-sm outline-offset-2 underline-offset-4 hover:text-foreground hover:underline focus-visible:outline-2 focus-visible:outline-ring sm:min-h-6";

export function RepositoryHeader({
  repository, children, ...titleProps
}: Pick<ComponentProps<typeof PageTitle>, "title" | "headingProps" | "actions"> & {
  repository: { full_name: string; private: boolean };
  children: ReactNode;
}) {
  const Visibility = repository.private ? LockKeyhole : Globe;
  return (
    <PageTitle {...titleProps} footer={
      <div data-slot="repository-header-summary" className="@container mt-4 border-t pt-3.5 text-foreground">
        <div className="grid gap-3.5 @min-[700px]:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.05fr)] @min-[700px]:gap-4.5 @min-[700px]:[&>*+*]:border-l @min-[700px]:[&>*+*]:pl-4.5">
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

export function RepositoryHeaderFact({
  label, regionLabel, children, detail,
}: { label: string; regionLabel?: string; children?: ReactNode; detail?: ReactNode }) {
  return (
    <section aria-label={regionLabel ?? label} className="min-w-0 [overflow-wrap:anywhere]">
      <h2 className="text-xs text-muted-foreground">{label}</h2>
      {children && <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] leading-5 font-medium tabular-nums">{children}</div>}
      {detail && <div className="mt-1 text-xs leading-normal text-muted-foreground tabular-nums">{detail}</div>}
    </section>
  );
}
