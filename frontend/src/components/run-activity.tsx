import type { Run } from "../api";
import { label } from "../lib/inbox-filters";
import { ActivityTimeline, type ActivityEvent } from "./activity-timeline";

export function RunActivity({ run }: { run: Run }) {
  const events: ActivityEvent[] = [{ id: "queued", title: "Review queued", at: run.created_at, status: "queued" }];
  if (run.started_at) events.push({ id: "started", title: "Review started", at: run.started_at, status: "running" });
  const terminal = ["completed", "partial", "failed", "skipped", "cancelled"];
  for (const [name, stage] of Object.entries(run.stages ?? {})) {
    const title = ({ ocr: "Code review", assessment: "Assessment", verification: "Fix verification", summary: "Summary" } as Record<string, string>)[name] ?? label(name);
    if (stage.started_at) events.push({ id: `${name}-started`, title: `${title} started`, at: stage.started_at, status: "running" });
    // A retry can retain the preceding attempt's finished_at while it starts again.
    if (stage.finished_at && terminal.includes(stage.status ?? "")
      && (!stage.started_at || Date.parse(stage.finished_at) >= Date.parse(stage.started_at))) {
      events.push({ id: `${name}-finished`, title: `${title} ${stage.status === "partial" ? "finished with incomplete work" : stage.status}`,
        at: stage.finished_at, status: stage.status, error: stage.error });
    }
  }
  if (run.finished_at && terminal.includes(run.status)) events.push({ id: "finished",
    title: run.status === "partial" ? "Review finished with incomplete work" : `Review ${run.status}`,
    at: run.finished_at, status: run.status, error: run.error });
  // shortcut: stage records retain only the latest attempt; a full retry history needs persisted events.
  events.reverse().sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return <section aria-label="Review activity" className="space-y-4">
    <p className="text-xs leading-relaxed text-muted-foreground">Saved run and stage milestones, newest first. Only recorded timestamps are shown; earlier retry attempts are not retained.</p>
    <ActivityTimeline events={events} />
  </section>;
}
