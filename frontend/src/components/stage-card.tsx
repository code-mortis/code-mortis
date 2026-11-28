import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Popover, PopoverTrigger } from "./ui/popover";

export function StageCard({ label, status, title, progress, progressLabel, explanation, statistics, detailsLabel, children }: {
  label: string; status: string; title: ReactNode; progress: string | null; progressLabel: string;
  explanation: string; statistics?: ReactNode; detailsLabel?: string; children?: ReactNode;
}) {
  const card = useRef<HTMLElement>(null);
  const [motionActive, setMotionActive] = useState(false);
  useEffect(() => {
    if (status !== "running" || !card.current) return;
    let inView = false;
    const update = () => setMotionActive(inView && !document.hidden);
    const observer = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; update(); });
    observer.observe(card.current);
    document.addEventListener("visibilitychange", update);
    return () => { observer.disconnect(); document.removeEventListener("visibilitychange", update); };
  }, [status]);
  const header = <span role="status" className="grid min-w-0 flex-1 grid-cols-1 gap-x-4 @min-[500px]:grid-cols-[minmax(0,1fr)_auto]">
    <span className="min-w-0 h-12 @min-[500px]:h-6">{title}</span>
    <span className="h-9 whitespace-nowrap text-sm font-medium tabular-nums leading-6 @min-[500px]:h-6" aria-label={progressLabel}>{progress ?? "—"}</span>
  </span>;
  return <Popover><section ref={card} data-status={status} data-motion={status === "running" && motionActive ? "active" : "paused"}
    className="stage-card @container mt-3 rounded-lg border bg-muted/40 p-4 text-xs" aria-label={label}>
    {children ? <PopoverTrigger asChild><button type="button" aria-label={detailsLabel ?? label} data-status={status}
      className="group flex w-full cursor-pointer items-start gap-3 rounded-sm text-left outline-offset-4 hover:text-primary focus-visible:outline-2 focus-visible:outline-ring">
      {header}<ChevronDown aria-hidden="true" className="mt-1 size-4 shrink-0 text-muted-foreground group-data-[state=open]:rotate-180" />
    </button></PopoverTrigger> : header}
    <div className="stage-divider relative mt-3 overflow-hidden border-t pt-3">
      {statistics ? <div className="flex min-h-19 flex-wrap content-start items-baseline justify-between gap-x-4 gap-y-1 leading-4.5 text-muted-foreground @min-[500px]:min-h-10 @min-[760px]:min-h-5">
        {statistics}
        {explanation && <p>{explanation}</p>}
      </div> : <p className="h-9 leading-4.5 text-muted-foreground @min-[500px]:h-5">{explanation}</p>}
    </div>
  </section>{children}</Popover>;
}
