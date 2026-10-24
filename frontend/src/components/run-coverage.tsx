import { useState } from "react";
import { CircleCheck, FileCode2, History } from "lucide-react";
import type { Run } from "../api";
import { fileFeedback } from "../lib/run-feedback";
import { errorFeedback } from "../lib/error-feedback";
import { active, Code, Empty, Status, TechnicalDetails } from "./common";
import { Pagination } from "./pagination";
import { CoverageGroup } from "./coverage-group";

const outcomes = {
  completed: { label: "Reviewed", value: "completed", reason: "Validated file result saved in this run." },
  reused: { label: "Reused", value: "completed", reason: "Validated result reused from earlier work." },
  failed: { label: "Failed", value: "failed", reason: "This file did not complete review." },
  waived: { label: "Waived", value: "partial", reason: "Review of this file was waived." },
  pending: { label: "Awaiting review", value: "queued", reason: "Waiting for a validated file result." },
  not_reviewed: { label: "Not reviewed", value: "partial", reason: "No validated review result was recorded." },
  excluded: { label: "Outside scope", value: "not_reviewed", reason: "Outside the selected review scope." },
};
type Outcome = keyof typeof outcomes;
type FileResult = { path: string; outcome: Outcome; reason: string };
const countKeys = ["selected", "completed", "reused", "failed", "waived"] as const;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function coverageFiles(run: Run) {
  const coverage = record(record(run.report?.manifest).coverage);
  const rows = (key: typeof countKeys[number]) => (Array.isArray(coverage[key]) ? coverage[key] : []).flatMap((value: unknown) => {
    const row = record(value);
    return typeof row.path === "string" && row.path.trim() ? [{ ...row, path: row.path }] : [];
  });
  const selected = rows("selected");
  const paths = new Set(selected.map((item) => item.path));
  const files = new Map<string, FileResult>();
  const feedback = fileFeedback(run);
  const reasons = new Map(feedback.map((item) => [item.path, item.reason]));
  const add = (row: Record<string, unknown> & { path: string }, outcome: Outcome) => {
    const reason = typeof row.reason === "string" && row.reason.trim() ? row.reason
      : typeof row.message === "string" && row.message.trim() ? row.message : reasons.get(row.path) ?? outcomes[outcome].reason;
    files.set(row.path, { path: row.path, outcome, reason });
  };
  selected.forEach((row) => add(row, active(run.status) ? "pending" : "not_reviewed"));
  for (const key of ["completed", "reused", "waived", "failed"] as const) rows(key).forEach((row) => add(row, key));
  const hasScope = Array.isArray(coverage.selected);
  for (const item of feedback) if (!files.has(item.path)) {
    files.set(item.path, { ...item, outcome: hasScope && !paths.has(item.path) ? "excluded" : "not_reviewed" });
  }
  const items = [...files.values()];
  return { items, hasScope, counts: Object.fromEntries(countKeys.map((key) => [key, rows(key).length])) as Record<typeof countKeys[number], number> };
}

function FileStatus({ file }: { file: FileResult }) {
  const status = outcomes[file.outcome];
  return <Status value={status.value} label={status.label} icon={file.outcome === "completed" ? <CircleCheck className="size-3.5" aria-hidden="true" /> : file.outcome === "reused" ? <History className="size-3.5" aria-hidden="true" /> : undefined} />;
}

export function RunCoverage({ run }: { run: Run }) {
  const [pages, setPages] = useState<Record<string, number>>({});
  const { items, hasScope, counts } = coverageFiles(run);
  return <section aria-label="Coverage" className="min-w-0">
    <p className="text-xs leading-relaxed text-muted-foreground">See which files were reviewed or reused from earlier runs, and why others were left unreviewed.</p>
    {!run.report ? <div className="mt-4"><Empty title={active(run.status) ? "Coverage is being recorded" : "No coverage report was produced"}>{active(run.status) ? "Validated file results will appear as the review progresses." : "This run has no saved coverage evidence. No findings does not mean every file was reviewed."}</Empty></div> : <>
      {!hasScope && <p role="status" className="mt-2 text-xs text-muted-foreground">Selected review scope was not recorded.</p>}
      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">{countKeys.map((key) => <div key={key} className="flex items-baseline gap-2"><dt className="text-xs capitalize text-muted-foreground">{key}</dt><dd className={`text-sm tabular-nums ${key === "failed" && counts.failed ? "text-destructive" : ""}`}>{counts[key]}</dd></div>)}</dl>
      <div className="mt-6 space-y-3">
        {(["attention", "reviewed"] as const).map((group) => {
          const files = items.filter((item) => ["completed", "reused"].includes(item.outcome) === (group === "reviewed"));
          if (!files.length) return null;
          const page = Math.min(pages[group] ?? 1, Math.ceil(files.length / 10));
          const visible = files.slice((page - 1) * 10, page * 10);
          const heading = group === "attention" ? "Files not reviewed" : "Reviewed files";
          return <CoverageGroup key={group} title={heading} count={files.length.toLocaleString()}>
            <ul className="divide-y">{visible.map((file) => {
              const feedback = errorFeedback(file.reason);
              return <li key={file.path} data-slot="coverage-file" className="flex min-w-0 items-start gap-2 px-4 py-3 sm:px-5">
              <FileCode2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <code className="block break-words text-xs leading-5 [overflow-wrap:anywhere]">{file.path}</code>
                {file.reason !== outcomes[file.outcome].reason && <p className="mt-1 max-w-[75ch] break-words text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{feedback.message}</p>}
                {feedback.details && <TechnicalDetails error={feedback.details} name={file.path} />}
              </div>
              <FileStatus file={file} />
            </li>; })}</ul>
            <Pagination page={page} pageSize={10} total={files.length} label={heading.toLowerCase()}
              onPageChange={(next) => setPages((current) => ({ ...current, [group]: next }))} />
          </CoverageGroup>;
        })}
        {!items.length && <Empty title="No file coverage recorded">Inspect the saved report for scope and review details. No selected files does not imply a clean review.</Empty>}
      </div>
      <details className="mt-6 pt-4 text-sm"><summary className="cursor-pointer font-medium">Inspect coverage report</summary><div className="mt-3"><Code language="json">{JSON.stringify(run.report, null, 2)}</Code></div></details>
    </>}
  </section>;
}
