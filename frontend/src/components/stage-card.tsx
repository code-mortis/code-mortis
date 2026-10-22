import { useEffect, useRef, useState, type ReactNode } from "react";

export function StageCard({ label, status, title, progress, progressLabel, explanation, children }: {
  label: string; status: string; title: ReactNode; progress: string | null; progressLabel: string;
  explanation: string; children?: ReactNode;
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
  return <section ref={card} data-status={status} data-motion={status === "running" && motionActive ? "active" : "paused"}
    className="stage-card @container my-5 rounded-lg bg-muted/40 p-4 text-xs" aria-label={label}>
    <div role="status" className="grid grid-cols-1 gap-x-4 @min-[500px]:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0 h-12 @min-[500px]:h-6">{title}</div>
      <span className="h-9 whitespace-nowrap text-sm font-medium tabular-nums leading-6 @min-[500px]:h-6" aria-label={progressLabel}>{progress ?? "—"}</span>
    </div>
    {children}
    <div className="stage-divider relative mt-3 overflow-hidden border-t pt-3"><p className="h-9 leading-4.5 text-muted-foreground @min-[500px]:h-5">{explanation}</p></div>
  </section>;
}
