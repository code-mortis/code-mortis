import type { Run } from "../api";

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value.trim() : "";
const rows = (value: unknown) => Array.isArray(value) ? value.map(record) : [];

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
      nextStep = "An enabled Code Mortis member with repository write access can post the command shown in the GitHub skip notice to bypass the file limit for one review. Personal connections remain owner-only. Or adjust the review settings and start a new review.";
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
      authentication: ["The AI connection needs to sign in again.", "The connection owner must reconnect the account, then resume this review."],
      quota: ["The AI connection reached its usage limit.", "The connection owner can resume this review when usage becomes available. No other connection is used automatically."],
      busy: ["The selected personal connection is reviewing another pull request.", "This review will retry automatically when the connection is available."],
      unavailable: ["The review service is unavailable.", "Ask the instance administrator to check the runner. Queued reviews retry automatically."],
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
      nextStep = "Increase Timeout, choose Unlimited, or reduce the review scope, then start a new review.";
      destination = "repository";
    } else if (record(report.summary).budget_exceeded === true || failures.some((item) => item.classification === "budget")) {
      summary = `${summary} The token budget was reached.`;
      nextStep = "Increase Token budget, choose Unlimited, or reduce the review scope, then start a new review.";
      destination = "repository";
    } else if (rows(report.errors).some((item) => text(item.message).startsWith("Delegated context budget"))) {
      summary = `${summary} The native context budget was reached.`;
      nextStep = "Increase Native context budget or reduce the review scope, then start a new review.";
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
    nextStep = "This page updates automatically as results are saved. You can cancel the review if needed.";
  } else if (run.status === "completed") {
    summary = "The selected review scope completed.";
    nextStep = "Inspect the saved findings and coverage in the run details.";
  }

  const publications: Record<string, [string, string]> = {
    pending: ["Waiting to publish", "Comments are published after review results are saved."],
    publishing: ["Publishing comments", "Saved review results are being sent to GitHub."],
    published: ["Published", "The review results were published on GitHub."],
    failed: ["Publication failed", "Check the GitHub error, then retry publication. Retrying sends saved results without rerunning AI analysis."],
    partially_published: ["Some comments published", "Some saved comments could not be published. Retry publication to send the remaining comments without rerunning AI analysis."],
    not_applicable: ["No comments published", run.status === "skipped"
      ? "This review was skipped, so there were no new review results to publish."
      : "No review comments were published for this run. See the review outcome for the reason."],
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
