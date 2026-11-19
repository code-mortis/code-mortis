import type { ReactNode } from "react";
import { Circle, CircleCheck, CircleDot, CircleSlash, CircleX, Pause, TriangleAlert } from "lucide-react";
import type { Run, Schema } from "../api";
import { coverageFiles } from "./run-coverage";
import { StageCard } from "./stage-card";

const descriptions = {
  analysis: ["Code review", "Reviewing changed files and validating their results."],
  assessment: ["Assessment", "Checking proposed findings against source evidence."],
  verification: ["Fix verification", "Checking whether earlier findings are fixed."],
  summary: ["PR summary", "Preparing the pull request summary."],
} as const;

export function assessmentExceptions(assessment?: Schema["AssessmentSummary"] | null) {
  return (["failed", "skipped"] as const).flatMap((status) => {
    const count = assessment?.progress?.[status] ?? assessment?.[status];
    return typeof count === "number" && Number.isSafeInteger(count) && count > 0 ? [`${count.toLocaleString()} ${status}`] : [];
  }).join(" · ");
}

export function RunStages({ run, assessment, assessmentStatus, summaryStage, summaryPublicationPending, summarySyncFailed, children }: {
  run: Run; assessmentStatus: string; summaryStage?: Schema["StageState"] | Schema["SummaryState"]; children: ReactNode;
  assessment?: Schema["AssessmentSummary"] | null;
  summaryPublicationPending?: boolean;
  summarySyncFailed?: boolean;
}) {
  const stages = (Object.keys(descriptions) as (keyof typeof descriptions)[]).flatMap((key) => {
    const stage = key === "summary" ? summaryStage
      : key === "analysis" ? run.stages?.analysis ?? run.stages?.ocr : run.stages?.[key];
    return stage ? [{ key, status: key === "assessment" ? assessmentStatus : stage.status }] : [];
  });
  const current = ["running", "waiting", "pending"].flatMap((status) => stages.filter((stage) => stage.status === status))[0];
  const running = stages.filter((stage) => stage.status === "running");
  let status = current?.status ?? (stages.length ? stages.every((stage) => stage.status === "skipped") ? "skipped"
    : stages.some((stage) => stage.status === "failed") && !stages.some((stage) => ["completed", "partial"].includes(stage.status)) ? "failed"
    : stages.every((stage) => ["cancelled", "skipped"].includes(stage.status)) ? "cancelled"
    : stages.some((stage) => ["partial", "failed", "cancelled"].includes(stage.status)) ? "partial" : "completed" : run.status);
  if (["queued", "blocked", "failed", "cancelled", "superseded", "skipped"].includes(run.status)) status = run.status;
  if (["running", "partial"].includes(run.status) && status === "completed") status = run.status;
  const phase = current?.key ?? (run.progress.phase === "assessment" ? "assessment"
    : run.progress.phase === "fix verification" ? "verification" : run.progress.phase === "summary" ? "summary" : "analysis");
  const [stageName, stageExplanation] = descriptions[phase];
  const titles: Record<string, string> = {
    queued: "Waiting to start", blocked: "Review paused", completed: "Review complete", partial: "Review complete with warnings",
    failed: "Review failed", cancelled: "Review cancelled", superseded: "Review superseded", skipped: "Review skipped",
    pending: `${stageName} pending`, waiting: `${stageName} waiting`, running: current || !stages.length ? stageName : "Finalizing review",
  };
  const explanations: Record<string, string> = {
    queued: "Waiting for a worker to start the review.", blocked: "Waiting to continue. Completed results are saved.",
    completed: "Review findings and coverage below.", partial: "Some review work could not be completed.",
    failed: "Review stopped after an error. Inspect stage details.", cancelled: "Review cancelled. Completed results remain available.",
    superseded: "The pull request changed. This run keeps its saved results.", skipped: "Review was skipped. See coverage for the saved reason.",
    pending: `Waiting to start ${stageName.toLowerCase()}.`, waiting: `Waiting to continue ${stageName.toLowerCase()}.`,
    running: current || !stages.length ? stageExplanation : "Saving review results and finishing remaining work.",
  };
  if (running.length && status === "running") {
    titles.running = running.map((stage) => descriptions[stage.key][0]).join(" · ");
  }
  if (["running", "completed", "partial"].includes(run.status)) {
    if (summarySyncFailed) {
      if (status === "running" && current) titles.running += " · Summary sync failed";
      else if (!current) {
        status = "partial";
        titles[status] = "Summary sync failed";
        explanations[status] = "Open stage details to retry GitHub sync.";
      }
    } else if (summaryPublicationPending && !current) {
      explanations[status] = "Waiting to publish the summary to GitHub.";
    }
  }
  const coverage = coverageFiles(run);
  const progressPhase = ["running", "pending", "waiting"].includes(status)
    ? current?.key ?? (summaryPublicationPending ? "summary" : phase) : "analysis";
  let unit = "files";
  let total = coverage.hasScope ? coverage.counts.selected : run.progress.selected;
  let completed = coverage.hasScope ? coverage.counts.completed + coverage.counts.reused : run.progress.completed;
  if (progressPhase === "assessment") {
    unit = "candidates";
    total = assessment?.candidates;
    completed = assessment?.progress?.completed ?? (assessment && Number.isSafeInteger(assessment.pending)
      && assessment.pending >= 0 && assessment.pending <= assessment.candidates
      ? assessment.candidates - assessment.pending - (assessment.failed ?? 0) - (assessment.skipped ?? 0) : undefined);
  } else if (progressPhase === "verification") {
    unit = "findings";
    const verification = run.progress.verification;
    total = verification && typeof verification === "object" && "selected" in verification ? verification.selected : undefined;
    completed = verification && typeof verification === "object" && "completed" in verification ? verification.completed : undefined;
  } else if (progressPhase === "summary") {
    total = completed = undefined;
  }
  const progress = status !== "queued" && typeof total === "number" && typeof completed === "number"
    && Number.isSafeInteger(total) && Number.isSafeInteger(completed) && total >= 0 && completed >= 0 && completed <= total
    ? `${completed.toLocaleString()} / ${total.toLocaleString()} ${unit}` : null;
  const exceptions = progressPhase === "assessment" ? assessmentExceptions(assessment) : "";
  const Icon = status === "completed" ? CircleCheck : status === "running" ? CircleDot : status === "failed" ? CircleX
    : status === "partial" ? TriangleAlert : ["blocked", "waiting"].includes(status) ? Pause : ["cancelled", "superseded", "skipped"].includes(status) ? CircleSlash : Circle;
  const color = status === "completed" ? "text-emerald-700 dark:text-emerald-300" : status === "failed" ? "text-red-700 dark:text-red-300"
    : ["partial", "blocked"].includes(status) ? "text-amber-800 dark:text-amber-300" : status === "running" ? "text-primary" : "text-muted-foreground";
  const titleText = titles[status] ?? "Review status unavailable";
  const title = <><Icon aria-hidden="true" className={`mt-1 size-4 shrink-0 ${color}`} /><span className="line-clamp-2 @min-[500px]:line-clamp-1">{titleText}</span></>;
  return <StageCard label="Review stages" status={status} progress={progress} detailsLabel="Review stage details"
    progressLabel={progress ? "Review progress count" : "Stage progress unavailable"}
    explanation={exceptions ? `Assessment: ${exceptions}.` : explanations[status] ?? "Stage details are not available yet."}
    title={<span className="inline-flex max-w-full items-start gap-2 text-base font-medium leading-6">{title}
      {exceptions && <span className="sr-only">{exceptions}</span>}
    </span>}>
    {stages.length ? children : null}
  </StageCard>;
}
