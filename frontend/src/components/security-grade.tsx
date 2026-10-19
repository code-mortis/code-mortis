import { Shield, ShieldQuestion } from "lucide-react";
import type { Schema } from "../api";
import { Status } from "./common";
import { securityIcons } from "./security-badge";
import { Link } from "react-router-dom";

export const securityStates: Record<string, string> = {
  current: "Current", not_scanned: "Not scanned", incomplete: "Incomplete",
  outdated: "Outdated", unavailable: "Unable to verify freshness",
};
const badgeStates: Record<string, string> = { ...securityStates, not_scanned: "Unscanned", unavailable: "Unverified" };
export function SecurityGradeShield({ grade }: { grade?: string | null }) {
  const letter = grade || "?";
  const color = letter === "?" ? "text-muted-foreground"
    : letter === "A" ? "text-emerald-800 dark:text-emerald-300"
    : ["D", "F"].includes(letter) ? "text-red-700 dark:text-red-300" : "text-amber-800 dark:text-amber-300";
  return <Shield data-slot="security-grade-shield" aria-hidden="true" className={`size-6 ${color}`} strokeWidth={1.5} fill="currentColor" fillOpacity={0.08}>
    <text x="12" y="12.5" textAnchor="middle" dominantBaseline="central" fill="currentColor" fillOpacity="1" stroke="none" fontSize="11" fontWeight="600">{letter}</text>
  </Shield>;
}

export function SecurityGrade({ repositoryId, summary, shield = false, showLabel = false }: {
  repositoryId: number; summary?: Schema["SecuritySummary"] | null; shield?: boolean; showLabel?: boolean;
}) {
  const current = summary?.state === "current";
  const partial = summary?.state === "incomplete" && summary.latest_status === "partial" && summary.grade_scan_id === summary.latest_scan_id && summary.grade;
  const label = current || partial ? summary?.grade ?? "Unrated" : badgeStates[summary?.state ?? "not_scanned"] ?? "Unrated";
  const status = !current && !partial ? "unknown" : summary?.grade === "A" ? "completed" : ["D", "F"].includes(summary?.grade ?? "") ? "failed" : "partial";
  const iconState = summary?.state === "incomplete" && summary.latest_status && summary.latest_status !== "completed" ? summary.latest_status : summary?.state ?? "not_scanned";
  const Icon = securityIcons[iconState] ?? ShieldQuestion;
  const letter = summary?.state === "not_scanned" ? "?" : summary?.grade || "?";
  const accessible = letter === "?" ? "Security grade unknown" : `${current || partial ? "Security" : "Historical security"} grade ${letter}${current || partial ? "" : `, ${label.toLowerCase()}`}`;
  return <Link data-security-badge aria-label={shield ? accessible : undefined} to={`/security/repositories/${repositoryId}`} className={`inline-flex no-underline outline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-ring ${shield ? "relative z-10 size-11 shrink-0 items-center justify-center rounded-md sm:size-8" : "w-fit max-w-full rounded-full"}`}>
    {shield ? <SecurityGradeShield grade={letter} /> : <Status value={status} label={showLabel && label !== "Unscanned" ? `Security ${label}` : label} icon={<Icon aria-hidden="true" className={`size-3 ${showLabel ? "order-first" : ""}`} />} />}
  </Link>;
}
