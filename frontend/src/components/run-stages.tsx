import type { ReactNode } from "react";
import { Circle, CircleCheck, CircleDot, CircleSlash, CircleX, Pause, TriangleAlert } from "lucide-react";
import type { Run, Schema } from "../api";
import { coverageFiles } from "./run-coverage";
import { Popover, PopoverTrigger } from "./ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const descriptions = {
  ocr: ["Code review", "Reviewing changed files and validating their results."],
  assessment: ["Assessment", "Checking proposed findings against source evidence."],
  verification: ["Fix verification", "Checking whether earlier findings are fixed."],
  summary: ["PR summary", "Preparing the pull request summary."],
} as const;

export function RunStages({ run, assessmentStatus, summaryStage, children }: {
  run: Run; assessmentStatus: string; summaryStage?: Schema["StageState"] | Schema["SummaryState"]; children: ReactNode;
}) {
  const stages = (Object.keys(descriptions) as (keyof typeof descriptions)[]).flatMap((key) => {
    const stage = key === "summary" ? summaryStage : run.stages?.[key];
    return stage ? [{ key, status: key === "assessment" ? assessmentStatus : stage.status }] : [];
  });
  const current = ["running", "waiting", "pending"].flatMap((status) => stages.filter((stage) => stage.status === status))[0];
  let status = current?.status ?? (stages.length ? stages.every((stage) => stage.status === "skipped") ? "skipped"
    : stages.some((stage) => stage.status === "failed") && !stages.some((stage) => ["completed", "partial"].includes(stage.status)) ? "failed"
    : stages.every((stage) => ["cancelled", "skipped"].includes(stage.status)) ? "cancelled"
    : stages.some((stage) => ["partial", "failed", "cancelled"].includes(stage.status)) ? "partial" : "completed" : run.status);
  if (["queued", "blocked", "failed", "cancelled", "superseded", "skipped"].includes(run.status)) status = run.status;
  if (["running", "partial"].includes(run.status) && status === "completed") status = run.status;
  const phase = current?.key ?? (run.progress.phase === "assessment" ? "assessment"
    : run.progress.phase === "fix verification" ? "verification" : run.progress.phase === "summary" ? "summary" : "ocr");
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
  const coverage = coverageFiles(run);
  const total = coverage.hasScope ? coverage.counts.selected : run.progress.selected;
  const completed = coverage.hasScope ? coverage.counts.completed + coverage.counts.reused : run.progress.completed;
  const progress = status !== "queued" && typeof total === "number" && typeof completed === "number"
    && Number.isSafeInteger(total) && Number.isSafeInteger(completed) && total >= 0 && completed >= 0 && completed <= total
    ? `${completed.toLocaleString()} / ${total.toLocaleString()} files` : null;
  const Icon = status === "completed" ? CircleCheck : status === "running" ? CircleDot : status === "failed" ? CircleX
    : status === "partial" ? TriangleAlert : ["blocked", "waiting"].includes(status) ? Pause : ["cancelled", "superseded", "skipped"].includes(status) ? CircleSlash : Circle;
  const color = status === "completed" ? "text-emerald-700 dark:text-emerald-300" : status === "failed" ? "text-red-700 dark:text-red-300"
    : ["partial", "blocked"].includes(status) ? "text-amber-800 dark:text-amber-300" : status === "running" ? "text-primary" : "text-muted-foreground";
  const title = <><Icon aria-hidden="true" className={`mt-1 size-4 shrink-0 ${color}`} /><span>{titles[status] ?? "Review status unavailable"}</span></>;
  return <section className="@container my-5 rounded-lg bg-muted/40 p-4 text-xs" aria-label="Review stages">
    <Popover>
      <div role="status" className="grid grid-cols-1 gap-x-4 @min-[500px]:grid-cols-[minmax(0,1fr)_auto]">
        <div className="min-w-0 h-12 @min-[500px]:h-6">
          {stages.length ? <Tooltip><TooltipTrigger asChild><PopoverTrigger asChild>
            <button type="button" aria-label="Review stage details" data-status={status} className="inline-flex items-start gap-2 rounded-sm text-left text-base font-medium leading-6 outline-offset-2 hover:text-primary focus-visible:outline-2 focus-visible:outline-ring">{title}</button>
          </PopoverTrigger></TooltipTrigger><TooltipContent sideOffset={6}>View stage details.</TooltipContent></Tooltip>
            : <span className="inline-flex items-start gap-2 text-base font-medium leading-6">{title}</span>}
        </div>
        <span className="h-9 whitespace-nowrap text-sm font-medium tabular-nums leading-6 @min-[500px]:h-6" aria-label={progress ? "Review progress count" : "File progress unavailable"}>{progress ?? "—"}</span>
      </div>
      {children}
    </Popover>
    <div className="mt-3 border-t pt-3"><p className="h-9 leading-4.5 text-muted-foreground @min-[500px]:h-5">{explanations[status] ?? "Stage details are not available yet."}</p></div>
  </section>;
}
