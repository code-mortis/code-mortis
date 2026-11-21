import { History, Shield, ShieldQuestion } from "lucide-react";
import type { Schema } from "../api";
import { Status } from "./common";
import { securityIcons } from "./security-badge";
import { Link } from "react-router-dom";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

export const securityStates: Record<string, string> = {
  current: "Current", not_scanned: "Not scanned", incomplete: "Incomplete",
  outdated: "Outdated", unavailable: "Unable to verify freshness",
};
export const securityCoverage: Record<string, string> = { complete: "Coverage complete", incomplete: "Coverage incomplete", not_scanned: "Not scanned" };
export const securityFreshness: Record<string, string> = { current: "Current commit", outdated: "Outdated scan", unavailable: "Freshness unverified" };
const gradeDescriptions: Record<string, string> = {
  A: "No confirmed unresolved findings.", B: "Low-severity unresolved findings.",
  C: "Medium-severity unresolved findings.", D: "High-severity unresolved findings.", F: "Critical-severity unresolved findings.",
};
const freshnessDescriptions: Record<string, string> = {
  current: "The report matches the current default-branch commit.",
  outdated: "The report covers an earlier commit or default branch. Current changes have not been scanned.",
  unavailable: "The saved report's freshness could not be verified. Its grade may not cover the current commit.",
};
export function SecurityGradeShield({ grade }: { grade?: string | null }) {
  const letter = grade || "?";
  const color = letter === "?" ? "text-muted-foreground"
    : letter === "A" ? "text-emerald-800 dark:text-emerald-300"
    : ["D", "F"].includes(letter) ? "text-red-700 dark:text-red-300" : "text-amber-800 dark:text-amber-300";
  return <Shield data-slot="security-grade-shield" aria-hidden="true" className={`size-6 ${color}`} strokeWidth={1.5} fill="currentColor" fillOpacity={0.08}>
    <text x="12" y="12.5" textAnchor="middle" dominantBaseline="central" fill="currentColor" fillOpacity="1" stroke="none" fontSize="11" fontWeight="600">{letter}</text>
  </Shield>;
}

export function SecurityGrade({ repositoryId, summary, shield = false, showLabel = false, showIcon = true }: {
  repositoryId: number; summary?: Schema["SecuritySummary"] | null; shield?: boolean; showLabel?: boolean; showIcon?: boolean;
}) {
  const outdated = summary?.state === "outdated";
  const label = summary?.grade ?? (summary?.state === "not_scanned" || !summary ? "Unscanned" : "Unrated");
  const status = !summary?.grade ? "unknown" : summary.grade === "A" ? "completed" : ["D", "F"].includes(summary.grade) ? "failed" : "partial";
  const iconState = summary?.state === "incomplete" && summary.latest_status && summary.latest_status !== "completed" ? summary.latest_status : summary?.state ?? "not_scanned";
  const Icon = outdated ? History : securityIcons[iconState] ?? ShieldQuestion;
  const letter = summary?.state === "not_scanned" ? "?" : summary?.grade || "?";
  const accessible = letter === "?" ? "Security grade unknown" : `Security grade ${letter}`;
  const description = !summary || summary.state === "not_scanned" ? "No security scan has been recorded for this repository."
    : `${summary.grade ? `Grade ${summary.grade}: ${gradeDescriptions[summary.grade]} Based on current findings and triage decisions.` : "No security grade is available."} ${summary.coverage === "complete" ? "Coverage is complete." : "The latest assessment is incomplete. Unvalidated code may change the grade."} ${freshnessDescriptions[summary.freshness ?? "unavailable"]}`;
  return <Tooltip><TooltipTrigger asChild><Link data-security-badge aria-label={shield ? accessible : undefined} to={`/security/repositories/${repositoryId}`} className={`inline-flex no-underline outline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-ring ${shield ? "relative z-10 size-11 shrink-0 items-center justify-center rounded-md sm:size-8" : "w-fit max-w-full rounded-full"}`}>
    {shield ? <SecurityGradeShield grade={letter} /> : <Status value={status} label={showLabel && label !== "Unscanned" ? `Security ${label}` : label} icon={showIcon && <Icon aria-hidden="true" className={`size-3 ${showLabel ? "order-first" : ""}`} />} />}
  </Link></TooltipTrigger><TooltipContent sideOffset={6} collisionPadding={16} className="max-w-xs leading-relaxed">{description}</TooltipContent></Tooltip>;
}
