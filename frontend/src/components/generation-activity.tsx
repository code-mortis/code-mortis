import { useApi, type Schema } from "../api";
import { backgroundFinished, useBackgroundStatus } from "../hooks/use-background-generation";
import { useActivityRead } from "../hooks/use-activity-read";
import { Button } from "./ui/button";
import { ErrorNotice, Loading, Status } from "./common";

export function GenerationActivity({ repositoryId, jobId }: {
  repositoryId: number; jobId: string;
}) {
  const result = useBackgroundStatus(repositoryId, jobId);
  const mode = result.data?.mode;
  const isSummary = mode === "summary" || mode === "summary_draft" || mode === "summary_refresh";
  useActivityRead(isSummary ? "repository_summary" : "context", result.data?.id, result.data?.finished_at,
    result.isSuccess ? result.dataUpdatedAt : 0);
  const title = isSummary ? "Repository summary generation" : "Project context generation";
  return <section aria-label={title} className="space-y-2 rounded-md border p-4">
    <h3 className="text-sm font-medium">{title}</h3>
    {result.isPending && <Loading />}
    <ErrorNotice error={result.error ?? result.data?.error} />
    {result.isSuccess && <Status value={result.data.status === "done" ? "completed" : result.data.status} />}
    {result.error && <Button variant="outline" size="sm" onClick={() => void result.refetch()}>Reload generation</Button>}
    {result.isSuccess && result.data.background && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{result.data.background}</p>}
    {result.isSuccess && result.data.project_summary && <p className="whitespace-pre-wrap break-words text-sm text-muted-foreground">{result.data.project_summary}</p>}
  </section>;
}

export function PullSummaryActivity({ runId, jobId }: { runId: string; jobId: string }) {
  const summary = useApi<Schema["SummaryAttemptOut"]>(
    `/runs/${encodeURIComponent(runId)}/summary/attempts/${encodeURIComponent(jobId)}`,
    (data) => data?.finished_at || backgroundFinished(data?.status) ? false : 5000,
  );
  useActivityRead("pr_summary", summary.data?.id, summary.data?.finished_at, summary.isSuccess ? summary.dataUpdatedAt : 0);
  return <section aria-label="PR summary generation" className="my-4 space-y-2 rounded-md border p-4">
    <h2 className="text-sm font-medium">PR summary generation</h2>
    {summary.isPending && <Loading />}
    <ErrorNotice error={summary.error} />
    {summary.isSuccess && <Status value={summary.data.status === "done" ? "completed" : summary.data.status} />}
  </section>;
}
