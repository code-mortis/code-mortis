import type { ReactNode } from "react";
import { ShieldAlert, ShieldCheck, ShieldEllipsis, ShieldOff, ShieldQuestion, ShieldX, type LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";
import { Status } from "./common";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

export const securityIcons: Record<string, LucideIcon> = {
  current: ShieldCheck, completed: ShieldCheck, partial: ShieldAlert, incomplete: ShieldAlert,
  not_scanned: ShieldQuestion, unavailable: ShieldQuestion, outdated: ShieldEllipsis,
  failed: ShieldX, cancelled: ShieldOff, running: ShieldEllipsis, queued: ShieldEllipsis, blocked: ShieldAlert,
};

export function SecurityBadge({ children, description }: {
  children: ReactNode; description: ReactNode;
}) {
  const className = "inline-flex w-fit max-w-full cursor-pointer rounded-full no-underline outline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-ring [&_[data-slot=badge]]:whitespace-normal";
  return <Tooltip><TooltipTrigger asChild><span data-security-badge tabIndex={0} className={className}>{children}</span>
  </TooltipTrigger><TooltipContent sideOffset={6} className="max-w-xs leading-relaxed">{description}</TooltipContent></Tooltip>;
}

export function SecurityStatus({ value, label, description }: {
  value: string; label?: string; description?: ReactNode;
}) {
  const badge = <Status value={value} label={label} />;
  return description ? <SecurityBadge description={description}>{badge}</SecurityBadge> : badge;
}

export function SecurityScanStatus({ status }: { status: string }) {
  const Icon = securityIcons[status] ?? ShieldQuestion;
  return <span className="inline-flex w-fit"><Status value={status} label={status === "partial" ? "Completed" : undefined}
    icon={["queued", "running"].includes(status) ? undefined : <Icon aria-hidden="true" className="size-3" />} />
    {status === "partial" && <span className="sr-only"> with warnings</span>}
  </span>;
}

export function SecurityScanBadge({ status, scanId }: {
  status: string; scanId: string;
}) {
  return <Link data-security-badge to={`/security/scans/${scanId}`} aria-label={`View latest scan: ${status === "partial" ? "completed with warnings" : status.replaceAll("_", " ")}`}
    className="inline-flex w-fit max-w-full rounded-full no-underline outline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-ring">
    <SecurityScanStatus status={status} />
  </Link>;
}
