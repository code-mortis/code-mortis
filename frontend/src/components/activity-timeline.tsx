import { Circle, CircleCheck, CirclePlay, CircleSlash, CircleX, Clock3, TriangleAlert } from "lucide-react";
import { errorFeedback } from "../lib/error-feedback";
import { TechnicalDetails } from "./common";

export type ActivityEvent = {
  id: string;
  title: string;
  at: string;
  status?: string;
  description?: string;
  error?: string | null;
};

export function ActivityTimeline({ events }: { events: ActivityEvent[] }) {
  return <div className="overflow-hidden rounded-lg border bg-card">
    {events.length ? <ol aria-label="Activity events" className="divide-y">{events.map((event) => {
      const Icon = event.status === "completed" ? CircleCheck : event.status === "failed" ? CircleX
        : ["partial", "blocked"].includes(event.status ?? "") ? TriangleAlert
          : ["cancelled", "skipped"].includes(event.status ?? "") ? CircleSlash
            : event.status === "running" ? CirclePlay : event.status === "queued" ? Clock3 : Circle;
      const color = event.status === "completed" ? "text-emerald-700 dark:text-emerald-300"
        : event.status === "failed" ? "text-destructive"
          : ["partial", "blocked"].includes(event.status ?? "") ? "text-amber-800 dark:text-amber-300"
            : "text-muted-foreground";
      const feedback = event.error ? errorFeedback(event.error) : null;
      return <li key={event.id} className="flex gap-3 px-4 py-3 sm:px-5">
        <Icon aria-hidden="true" className={`mt-0.5 size-4 shrink-0 ${color}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between sm:gap-4">
            <p className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">{event.title}</p>
            <time dateTime={event.at} className="shrink-0 text-xs tabular-nums text-muted-foreground">
              {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(event.at))}
            </time>
          </div>
          {event.description && <p className="mt-1 max-w-[75ch] text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{event.description}</p>}
          {feedback && <div className="mt-2 text-xs leading-relaxed [overflow-wrap:anywhere]">
            <p>{feedback.message}</p>
            {feedback.details && <TechnicalDetails error={feedback.details} name={event.title} />}
          </div>}
        </div>
      </li>;
    })}</ol> : <p className="px-4 py-8 text-center text-sm text-muted-foreground">No activity recorded yet.</p>}
  </div>;
}
