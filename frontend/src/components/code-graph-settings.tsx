import { useId } from "react";
import { useAction, useApi, type Schema } from "../api";
import { CodeGraphCoverage } from "./code-graph-coverage";
import { date, ErrorNotice, Status } from "./common";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";

const messages: Record<Schema["CodeGraphStatus"]["state"], string> = {
  disabled: "Save to start indexing the default branch.",
  not_indexed: "Waiting for an indexing slot. Updates start automatically.",
  queued: "Waiting to index the default branch.",
  running: "Indexing the default branch.",
  ready: "Indexing finished for supported files in this revision.",
  partial: "Indexing finished with warnings. Available results remain usable.",
  stale: "The saved index is from an earlier revision or configuration. An update is pending.",
  failed: "Indexing failed. The previous usable revision is preserved.",
  cancelled: "Indexing is paused. Retry to resume automatic updates.",
};

export function CodeGraphSettings({ repositoryId, enabled, savedEnabled, visible, dirty, onChange }: {
  repositoryId: number;
  enabled: boolean;
  savedEnabled: boolean;
  visible: boolean;
  dirty: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const id = useId();
  const status = useApi<Schema["CodeGraphStatus"]>(`/repositories/${repositoryId}/code-graph`, true, visible && savedEnabled);
  const action = useAction();
  const current = status.data;
  const active = current?.refreshing || ["queued", "running"].includes(current?.state ?? "");
  return <div className="space-y-3 border-b pb-6">
    <div className="flex items-start justify-between gap-6">
      <div className="min-w-0 space-y-1">
        <label htmlFor={id} className="text-sm font-medium">Code graph</label>
        <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted-foreground">
          When the matching index is ready, new ChatGPT and Claude Code reviews can use related source.
          All connections can use related-file hints during evidence checks. Indexing uses no AI tokens; added context does. Authorized Code MCP connections can read indexed code.
        </p>
      </div>
      <Switch id={id} aria-describedby={`${id}-hint`} checked={enabled} onCheckedChange={onChange} className="mt-1 shrink-0" />
    </div>
    {dirty && <p role="status" className="text-xs text-muted-foreground">{enabled ? "Save graph settings before indexing." : "Save to disable indexing and code access."}</p>}
    {savedEnabled && <>
      {status.isLoading && <p role="status" className="text-xs text-muted-foreground">Loading index status…</p>}
      <ErrorNotice error={status.error} />
      {current && <div className="space-y-2">
        <div role="status" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Status value={current.state === "ready" ? "completed" : current.state === "stale" ? "outdated" : current.state} label={current.state === "ready" ? "Indexed" : current.state === "partial" ? "Indexed with warnings" : current.state === "not_indexed" ? "Not indexed" : undefined} />
          {current.commit_sha && <span>Revision <code title={current.commit_sha}>{current.commit_sha.slice(0, 8)}</code></span>}
          {current.refreshing && <span>Checking for updates…</span>}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">{messages[current.state]}</p>
        <CodeGraphCoverage coverage={current.coverage} />
        {current.indexed_sha && current.indexed_sha !== current.commit_sha && <p className="text-xs text-muted-foreground">Previous index: <code title={current.indexed_sha}>{current.indexed_sha.slice(0, 8)}</code></p>}
        {current.checked_at && <p className="text-xs text-muted-foreground">Last checked {date(current.checked_at)}</p>}
        <ErrorNotice error={current.error} />
        {current.failure && <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer">Indexing error details</summary>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 break-words">
            <dt>Stage</dt><dd>{current.failure.stage.replaceAll("_", " ")}</dd>
            <dt>Code</dt><dd>{current.failure.code}</dd>
            <dt>Attempt</dt><dd className="min-w-0 break-all">{current.failure.attempt_reference ?? current.snapshot_id}</dd>
          </dl>
        </details>}
        {(active || ["failed", "cancelled", "stale", "not_indexed"].includes(current.state)) && <Button
          type="button" variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={action.isPending || (!active && dirty)}
          onClick={() => action.mutate({ path: `/repositories/${repositoryId}/code-graph/${active ? "cancel" : "retry"}` })}
        >{action.isPending ? "Updating…" : active ? "Cancel indexing" : "Retry indexing"}</Button>}
      </div>}
      <ErrorNotice error={action.error} />
    </>}
  </div>;
}
