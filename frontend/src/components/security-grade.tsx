import { ShieldCheck } from "lucide-react";
import type { Schema } from "../api";
import { Status } from "./common";
import { Link } from "react-router-dom";

export const securityStates: Record<string, string> = {
  current: "Current", not_scanned: "Not scanned", incomplete: "Incomplete",
  outdated: "Outdated", unavailable: "Unable to verify freshness",
};
const badgeStates: Record<string, string> = { ...securityStates, not_scanned: "Unscanned", unavailable: "Unverified" };
export function SecurityGrade({ repositoryId, summary }: {
  repositoryId: number; summary?: Schema["SecuritySummary"] | null;
}) {
  const current = summary?.state === "current";
  const label = current ? summary?.grade ?? "Unrated" : badgeStates[summary?.state ?? "not_scanned"] ?? "Unrated";
  const status = !current ? "unknown" : summary.grade === "A" ? "completed" : ["D", "F"].includes(summary.grade ?? "") ? "failed" : "partial";
  return <Link data-security-badge to={`/security/repositories/${repositoryId}`} className="inline-flex w-fit max-w-full rounded-full no-underline outline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-ring">
    <Status value={status} label={label} icon={<ShieldCheck aria-hidden="true" className="size-3" />} />
  </Link>;
}
