import { useEffect, useState } from "react";
import { runDuration } from "../lib/run-duration";

export function ExecutionDuration({ run }: { run: { status: string; started_at: string | null; finished_at: string | null } }) {
  const running = run.status === "running" && !run.finished_at;
  const [time, setTime] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setTime(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const duration = runDuration(run, running ? time : undefined);
  return <span className="whitespace-nowrap tabular-nums">{duration ?? (run.started_at ? "Unavailable" : "Not started")}{running && duration && " elapsed"}</span>;
}
