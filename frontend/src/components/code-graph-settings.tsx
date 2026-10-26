import { useId } from "react";
import { useAction, useApi, type Schema } from "../api";
import { date, ErrorNotice, Status } from "./common";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";

const messages: Record<Schema["CodeGraphStatus"]["state"], string> = {
  disabled: "Save to start indexing the default branch.",
  not_indexed: "Waiting for an indexing slot. Updates start automatically.",
  queued: "Waiting to index the default branch.",
  running: "Indexing the default branch.",
  ready: "The index is ready for this revision.",
  partial: "Some files or relationships could not be indexed. Available results remain usable.",
  stale: "The saved index is from an earlier revision or configuration. An update is pending.",
  failed: "Indexing failed. The previous usable revision is preserved.",
  cancelled: "Indexing is paused. Retry to resume automatic updates.",
};

export function CodeGraphSettings({ repositoryId, enabled, savedEnabled, visible, onChange }: {
  repositoryId: number;
  enabled: boolean;
  savedEnabled: boolean;
  visible: boolean;
  onChange: (enabled: boolean) => void;
}) {
  const id = useId();
  const status = useApi<Schema["CodeGraphStatus"]>(`/repositories/${repositoryId}/code-graph`, true, visible && savedEnabled);
  const action = useAction();
  const current = status.data;
  const dirty = enabled !== savedEnabled;
  const active = current?.refreshing || ["queued", "running"].includes(current?.state ?? "");
  return <div className="space-y-3 border-b pb-6">
    <div className="flex items-start justify-between gap-6">
      <div className="min-w-0 space-y-1">
        <label htmlFor={id} className="text-sm font-medium">Code graph</label>
        <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted-foreground">
          Index definitions and file dependencies without using AI tokens. Review and agent integration are not available yet.
        </p>
      </div>
      <Switch id={id} aria-describedby={`${id}-hint`} checked={enabled} onCheckedChange={onChange} className="mt-1 shrink-0" />
    </div>
    {dirty ? <p role="status" className="text-xs text-muted-foreground">{enabled ? "Save to start indexing." : "Save to disable indexing and code access."}</p> : savedEnabled && <>
      {status.isLoading && <p role="status" className="text-xs text-muted-foreground">Loading index status…</p>}
      <ErrorNotice error={status.error} />
      {current && <div className="space-y-2">
        <div role="status" className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <Status value={current.state === "ready" ? "completed" : current.state === "stale" ? "outdated" : current.state} label={current.state === "ready" ? "Ready" : current.state === "not_indexed" ? "Not indexed" : undefined} />
          {current.commit_sha && <span>Revision <code title={current.commit_sha}>{current.commit_sha.slice(0, 8)}</code></span>}
          {current.refreshing && <span>Checking for updates…</span>}
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">{messages[current.state]}</p>
        {current.indexed_sha && current.indexed_sha !== current.commit_sha && <p className="text-xs text-muted-foreground">Previous index: <code title={current.indexed_sha}>{current.indexed_sha.slice(0, 8)}</code></p>}
        {current.checked_at && <p className="text-xs text-muted-foreground">Last checked {date(current.checked_at)}</p>}
        <ErrorNotice error={current.error} />
        {(active || ["failed", "cancelled", "stale", "not_indexed"].includes(current.state)) && <Button
          type="button" variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={action.isPending}
          onClick={() => action.mutate({ path: `/repositories/${repositoryId}/code-graph/${active ? "cancel" : "retry"}` })}
        >{action.isPending ? "Updating…" : active ? "Cancel indexing" : "Retry indexing"}</Button>}
      </div>}
      <ErrorNotice error={action.error} />
    </>}
  </div>;
}
