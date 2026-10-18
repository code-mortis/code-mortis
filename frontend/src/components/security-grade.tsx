import { Link } from "react-router-dom";
import { ShieldCheck } from "lucide-react";
import type { Schema } from "../api";
import { Status } from "./common";

export const securityStates: Record<string, string> = {
  current: "Current", not_scanned: "Not scanned", incomplete: "Incomplete",
  outdated: "Outdated", unavailable: "Unable to verify freshness",
};

export function SecurityGrade({ repositoryId, summary }: {
  repositoryId: number; summary?: Schema["SecuritySummary"] | null;
}) {
  const current = summary?.state === "current";
  const label = summary?.grade
    ? `Security ${summary.grade}${current ? "" : ` · ${securityStates[summary.state ?? ""] ?? "Previous scan"}`}`
    : `Security · ${securityStates[summary?.state ?? "not_scanned"] ?? "Not rated"}`;
  const status = !current ? "unknown" : summary.grade === "A" ? "completed" : ["D", "F"].includes(summary.grade ?? "") ? "failed" : "partial";
  return <Link to={`/security/repositories/${repositoryId}`} className="inline-flex rounded-md outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
    title={`Highest unresolved severity: A none, B low, C medium, D high, F critical.${summary?.grade_sha ? ` Grade saved for ${summary.grade_sha.slice(0, 7)}.` : ""}`}>
    <Status value={status} label={label} icon={<ShieldCheck aria-hidden="true" className="size-3" />} />
  </Link>;
}
