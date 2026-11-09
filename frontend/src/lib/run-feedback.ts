import type { Run } from "../api";
import { resultDeliveryFeedback } from "./error-feedback";

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value.trim() : "";
const rows = (value: unknown) => Array.isArray(value) ? value.map(record) : [];

export function assessmentFeedback(error: string, stage: "assessment" | "verification" | "review" = "assessment"): { title: string; message: string; citation?: string } {
  const message = error.split("; response=", 1)[0];
  const name = stage[0].toUpperCase() + stage.slice(1);
  if (/Token usage is unavailable or invalid|did not report valid token usage/.test(message)) {
    return { title: "Token usage could not be verified", message: `The AI's token usage was unavailable, so this ${stage} could not continue. Check the technical details before starting another review.` };
  }
  const delivery = resultDeliveryFeedback(error);
  if (delivery) return delivery;
  const budget = /used (\d+) tokens \+ next-file estimate exceeds budget (\d+)/.exec(message);
  if (budget && [budget[1], budget[2]].every((value) => Number.isSafeInteger(Number(value)))) {
    const used = Number(budget[1]);
    const limit = Number(budget[2]).toLocaleString("en-US");
    return { title: `${name} token limit reached`, message: used === 0
      ? `This check stopped before contacting the AI: its estimated request exceeded the saved limit of ${limit} tokens. Start a new review to use current settings.`
      : `The next request would exceed this check's saved limit of ${limit} tokens (${used.toLocaleString("en-US")} already used). Start a new review to use current settings.` };
  }
  let detail: Record<string, unknown> = {};
  const start = message.indexOf('{"field":');
  if (start >= 0) {
    const payload = message.slice(start);
    let depth = 0;
    // Skip quoted strings so braces in paths or reasons do not end the object.
    for (const token of payload.matchAll(/"(?:\\.|[^"\\])*"|[{}]/g)) {
      if (token[0] === "{") depth++;
      else if (token[0] === "}" && --depth === 0) {
        try { detail = record(JSON.parse(payload.slice(0, token.index + 1))); } catch { /* Older diagnostics may be truncated. */ }
        break;
      }
    }
  }
  const field = text(detail.field);
  const evidence = /^evidence\[(\d+)\]/.exec(field);
  let citation: string | undefined;
  if (evidence && Number.isSafeInteger(Number(evidence[1]))) {
    citation = `Citation ${Number(evidence[1]) + 1}`;
    if (detail.revision === "current" || detail.revision === "original") citation += ` · ${detail.revision === "current" ? "Current" : "Original"} source`;
    if (typeof detail.path === "string") citation += `: ${detail.path}`;
    if (Number.isSafeInteger(detail.start_line) && Number.isSafeInteger(detail.end_line)
      && Number(detail.start_line) > 0 && Number(detail.end_line) >= Number(detail.start_line)) {
      citation += `:${detail.start_line}${detail.end_line === detail.start_line ? "" : `–${detail.end_line}`}`;
    }
  }
  if (message.includes("Assessment evidence does not match the supplied source") || evidence) {
    const reasons: Record<string, string> = {
      "unavailable source": "The AI cited source that was not supplied for this assessment.",
      "invalid source line range": "The AI cited a line range outside the supplied source.",
      "empty quote": "The AI did not include a source quote.",
      "quote is not verbatim text within the cited source lines": "The AI's evidence quote did not exactly match the cited source.",
    };
    return { title: "Evidence could not be verified", message: reasons[text(detail.reason)] ?? "The AI returned a source citation that could not be validated.", citation };
  }
  if (field === "finding_id" || message.includes("Assessment references an unavailable finding") || message.includes("Only a duplicate may reference")) {
    return { title: "Finding reference could not be verified", message: "The AI returned an invalid finding reference. Only findings supplied in the review history can be linked." };
  }
  if (/without returning a final assessment|did not return exactly one final assessment|unsupported response instead of a finding assessment|invalid (?:verification verdict|structured result)/.test(message)) {
    return { title: `No usable ${stage} returned`, message: `The AI did not return a complete ${stage} in the required format. Check the selected model and technical details before starting another review.` };
  }
  if (/OCR evidence prompt exceeds|Context limit exhausted|Assessment needs .* bytes|Token budget exhausted|No token budget remains/.test(message)) {
    const limit = message.split(" Technical details:", 1)[0];
    return { title: `${name} limit reached`, message: limit.length <= 240 && !/[{}]/.test(limit)
      ? limit : `The ${stage} could not continue within the configured limits. Review the prompt ceiling and run budget before starting another review.` };
  }
  if (/Assessment timed out|Time limit reached/.test(message)) {
    return { title: `${name} timed out`, message: `The time limit was reached before this ${stage} finished. Review the run's timeout before starting another review.` };
  }
  if (/Finding history changed (during assessment|before the assessment could be corrected)/.test(message)) {
    return { title: "Finding history changed", message: "Finding history changed while this candidate was being checked. Retry assessment to evaluate the updated history." };
  }
  if (message.includes("The pull request or finding history changed during assessment")) {
    return { title: "Assessment context changed", message: "The decision was withheld because its context changed. Check the current commit and access before retrying this assessment." };
  }
  if (/cancelled|replaced by a newer run|The review stopped or its commit|Access to the repository or AI connection changed/.test(message)) {
    return { title: `${name} stopped`, message: `The review stopped or its context changed before this ${stage} could finish. Check the run outcome and current access.` };
  }
  if (/authentication|HTTP 40[13]|quota|rate.limit|OCR verification process failed|AI did not finish checking|AI could not confirm or reject/i.test(message)) {
    return { title: "AI request could not complete", message: `The selected AI service could not complete this ${stage}. Check the connection and technical details before trying again.` };
  }
  return { title: `${name} could not be completed`, message: error.length <= 240 && !/[{}]/.test(error)
    ? error : `No usable ${stage} was saved. Inspect the technical details before starting another review.` };
}

export function runFeedback(run: Run) {
  const report = record(run.report);
  const manifest = record(report.manifest);
  const coverage = record(manifest.coverage);
  const settings = record(run.config.settings);
  const failures = [...rows(coverage.failed), ...rows(coverage.waived)];
  const exclusions = rows(report.excluded_files);
  const selected = Array.isArray(coverage.selected) ? coverage.selected.length : null;
  const completed = rows(coverage.completed).length + rows(coverage.reused).length;
  const fileLimitSkip = run.status === "skipped" && manifest.schema_version === "code-mortis.file-limit/v1"
    && selected !== null && typeof settings.max_files === "number";
  let summary = "Review status is unavailable.";
  let nextStep = "Open the run details to inspect the saved result.";
  let destination: "repository" | "connections" | "pull" | undefined;

  if (run.status === "skipped") {
    destination = "repository";
    if (fileLimitSkip) {
      summary = `${selected} reviewable files exceed the limit of ${settings.max_files}. No files were reviewed.`;
      nextStep = "An enabled Code Mortis member with repository write access can post the command shown in the GitHub skip notice to bypass the file limit for one review. Choose a shared API connection. Or adjust the review settings and start a new review.";
    } else if (selected === 0) {
      summary = "No changed files were eligible for review.";
      if (exclusions.length && exclusions.every((item) => item.exclude_reason === "unsupported_ext")) {
        summary = "The changed file types are not supported by the review engine.";
        nextStep = "These changes need a manual review. Start a new run when supported source files change.";
        destination = "pull";
      } else {
        nextStep = "Check file exclusions and review rules. Start a new review if you change the scope.";
      }
    } else {
      summary = text(report.message) || "This review was skipped. No specific reason was saved.";
      nextStep = "Inspect the coverage report and repository settings before starting a new review.";
    }
  } else if (run.status === "cancelled") {
    summary = "This review was cancelled before it finished.";
    nextStep = "Start a new review from the pull request when you are ready.";
    destination = "pull";
  } else if (run.status === "superseded" || run.failure_reason === "superseded") {
    summary = "The pull request changed or closed, so this run no longer applies to it.";
    nextStep = "Open the pull request to inspect the latest commit and review runs.";
    destination = "pull";
  } else if (run.blocked_reason || run.status === "blocked") {
    const reasons: Record<string, [string, string]> = {
      authentication: ["The AI connection needs authentication.", "Ask the administrator to check the shared connection credentials before retrying."],
      quota: ["The AI connection reached its usage limit.", "Retry with the same shared connection when usage becomes available. No other connection is used automatically."],
      busy: ["The selected AI connection is busy.", "This review will retry automatically when the connection is available."],
      unavailable: ["The review service is unavailable.", "Ask the administrator to check the review worker and selected shared API connection. Queued reviews retry automatically."],
    };
    [summary, nextStep] = reasons[run.blocked_reason ?? ""] ?? [run.error || "This review needs attention.", "Check the selected AI connection before resuming."];
    if (!["busy", "unavailable"].includes(run.blocked_reason ?? "")) destination = "connections";
  } else if (run.status === "failed" || run.status === "partial") {
    if (run.status === "partial") {
      summary = selected === null
        ? "The review finished with incomplete coverage."
        : `${completed} of ${selected} review items completed. Coverage is incomplete.`;
    } else {
      summary = "The review could not complete. This is not a clean review.";
    }
    nextStep = "Inspect the error and file results in the run details, fix the cause, then start a new review.";
    destination = "pull";
    if (run.failure_reason === "timeout" || failures.some((item) => item.classification === "timeout")) {
      summary = run.status === "partial" ? `${summary} The time limit was reached.` : "The review reached its time limit.";
      if (typeof settings.timeout_seconds === "number") summary += ` Configured limit: ${settings.timeout_seconds.toLocaleString("en-US")} seconds.`;
      nextStep = "Increase Timeout, choose Unlimited, or reduce the review scope, then start a new review.";
      destination = "repository";
    } else if (record(report.summary).budget_exceeded === true || failures.some((item) => item.classification === "budget")) {
      summary = `${summary} The token budget was reached.`;
      const used = record(report.summary).total_tokens;
      if (typeof settings.token_budget === "number") {
        summary += typeof used === "number" && Number.isFinite(used) && used >= 0
          ? ` ${used.toLocaleString("en-US")} / ${settings.token_budget.toLocaleString("en-US")} tokens used.`
          : ` Configured limit: ${settings.token_budget.toLocaleString("en-US")} tokens; usage was not reported.`;
      }
      nextStep = "Increase Token budget, choose Unlimited, or reduce the review scope, then start a new review.";
      destination = "repository";
    } else if (rows(report.errors).some((item) => text(item.message).startsWith("Delegated context budget"))) {
      summary = `${summary} The native context budget was reached.`;
      nextStep = "Start a new Mortis review using a shared API connection and the Token budget configured in repository settings.";
      destination = "repository";
    } else if (run.failure_reason === "worker_stopped") {
      summary = "The review worker stopped before the run could finish.";
      nextStep = "Ask the administrator to check the worker, then start a new review. Interrupted AI usage may already have been charged.";
    }
  } else if (run.status === "queued") {
    summary = "Waiting for a review worker.";
    nextStep = "This page updates automatically. If the wait persists, ask the administrator to check the review worker and scheduler.";
  } else if (run.status === "running") {
    summary = "The review is in progress.";
    nextStep = "This page updates automatically as results are saved.";
  } else if (run.status === "completed") {
    summary = "The selected review scope completed.";
    nextStep = "Inspect the saved findings and coverage in the run details.";
  }

  const publications: Record<string, [string, string]> = {
    pending: ["Waiting to publish", ""],
    publishing: ["Publishing comments", "Saved review results are being sent to GitHub."],
    published: ["Published", "The review results were published on GitHub."],
    no_new_comments: ["No new comments", "No additional GitHub comments were needed."],
    failed: ["Publication failed", "Check the GitHub error, then retry publication. Retrying sends saved results without rerunning AI analysis."],
    partially_published: ["Some comments published", "Some saved comments could not be published. Retry publication to send the remaining comments without rerunning AI analysis."],
    not_applicable: ["No comments published", ""],
  };
  if (fileLimitSkip) {
    publications.pending = ["Waiting to publish skip notice", "The file-limit notice is waiting to be sent to GitHub."];
    publications.publishing = ["Publishing skip notice", "The file-limit notice is being sent to GitHub."];
    publications.published = ["Skip notice published", "The file-limit notice was published on GitHub. No files were reviewed."];
    publications.failed = ["Skip notice publication failed", "Retry publication to send the saved file-limit notice without running AI analysis."];
    publications.partially_published = publications.failed;
  }
  const [publicationLabel, publicationDetail] = publications[run.publication] ?? ["Publication status unavailable", "Inspect the saved run for details."];
  if (["failed", "partially_published"].includes(run.publication)) {
    summary = `${summary} ${publicationLabel}.`;
    nextStep += fileLimitSkip
      ? " Retry GitHub publication from the run details to send the saved file-limit notice without running AI analysis."
      : " Retry GitHub publication from the run details to send saved results without rerunning the review.";
  }
  const needsAttention = ["failed", "partial", "skipped", "blocked", "cancelled", "superseded"].includes(run.status)
    || !!run.blocked_reason || ["failed", "partially_published"].includes(run.publication);
  return { summary, nextStep, destination, publicationLabel, publicationDetail, needsAttention };
}

export function fileFeedback(run: Run) {
  const report = record(run.report);
  const coverage = record(record(report.manifest).coverage);
  const reasons: Record<string, string> = {
    unsupported_ext: "File type is not supported by the review engine.",
    user_exclude: "Excluded by the review rules or repository settings.",
    default_path: "Excluded by the review engine's default path rules.",
    provider_directory: "Dependency or generated directory excluded by the review engine.",
    secret_exclude: "Excluded to protect credentials and secrets.",
    deleted: "Deleted file is outside the review engine's scope.",
    binary: "Binary files cannot be reviewed.",
    too_large: "File exceeds the review engine's context limit.",
  };
  const items = [...rows(report.errors), ...rows(report.excluded_files), ...rows(coverage.failed), ...rows(coverage.waived)];
  const seen = new Set<string>();
  return items.filter((item) => text(item.path)).map((item) => ({
    path: text(item.path),
    reason: text(item.message) || text(item.reason) || reasons[text(item.exclude_reason)] || "This file was not reviewed. Inspect the coverage report for details.",
  })).filter((item) => {
    if (seen.has(item.path)) return false;
    seen.add(item.path);
    return true;
  });
}
