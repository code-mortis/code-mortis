import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Pencil, RefreshCw, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, type Repository } from "../api";
import { isBackgroundConnectionError, savedBackgroundInputs, useBackgroundStatus, useStartBackground, type BackgroundHandoff } from "../hooks/use-background-generation";
import { ErrorNotice, Markdown } from "./common";
import { useContextGenerationGuard } from "./context-generation-guard";
import { useActivityRead } from "../hooks/use-activity-read";
import { Button } from "./ui/button";

export function RepositorySummary({ repository, admin }: { repository: Repository; admin: boolean }) {
  const summary = repository.settings.project_summary;
  const initiallySaved = useRef(!!summary);
  return <>
    <span role="status" className="sr-only">{summary && !initiallySaved.current ? "Repository summary saved." : ""}</span>
    {admin ? <GenerateRepositorySummary repository={repository} /> : summary && <Markdown>{summary}</Markdown>}
  </>;
}

function GenerateRepositorySummary({ repository }: { repository: Repository }) {
  const [jobId, setJobId] = useState<string>();
  const [generationId, setGenerationId] = useState<string>();
  const generationRequest = useRef<Pick<BackgroundHandoff, "mode" | "inputs">>(undefined);
  const [contextGeneration, setContextGeneration] = useState<BackgroundHandoff>();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const start = useStartBackground(repository.id);
  const result = useBackgroundStatus(repository.id, jobId);
  useActivityRead("repository_summary", result.data?.id, result.data?.finished_at, result.isSuccess ? result.dataUpdatedAt : 0);
  const savedSummary = repository.settings.project_summary;
  const mode = (generationId && generationRequest.current?.mode)
    || (savedSummary ? "summary_draft" : repository.settings.background?.trim() ? "summary" : "context");
  const { dialog, blocked, handOff } = useContextGenerationGuard(repository.id, generationId, undefined, mode === "summary_draft");
  const settings = `/repositories/${repository.id}/settings`;
  const success = result.data?.status === "done" && !!result.data.project_summary;
  useEffect(() => {
    if (!success) return;
    if (savedSummary) setJobId(undefined);
    else void cache.invalidateQueries({ queryKey: ["/repositories"] });
  }, [cache, savedSummary, success]);
  useEffect(() => {
    if (contextGeneration && !blocked) {
      handOff();
      navigate(`${settings}?tab=context`, { state: { contextGeneration } });
    }
  }, [contextGeneration, blocked, handOff, navigate, settings]);

  const error = start.error ?? result.error ?? (result.data?.status === "failed"
    ? new Error(result.data.error || "Summary generation failed. Try again.")
    : result.data?.status === "done" && !success ? new Error("The model returned no summary. Try again.") : null);
  const pending = !error && (start.isPending || !!jobId);
  const needsSetup = !repository.connection_id || repository.connection_requires_setup
    || (start.error instanceof ApiError && start.error.status === 422);
  const actionVariant = savedSummary ? "ghost" : "outline";
  const generationAction = needsSetup ? <Button asChild variant={actionVariant} size="sm">
    <Link to={`${settings}?tab=connection`}>Configure AI connection</Link>
  </Button> : <Button type="button" variant={actionVariant} size="sm" disabled={pending} onClick={() => {
    if (!generationId) generationRequest.current = mode === "summary" ? undefined : { mode, inputs: savedBackgroundInputs(repository) };
    const handoff = generationRequest.current;
    const attemptId = handoff ? generationId ?? crypto.randomUUID() : undefined;
    setGenerationId(attemptId);
    setJobId(undefined);
    start.mutate({ mode, generation_id: attemptId }, {
      onSuccess(job) {
        if (handoff && attemptId) {
          setContextGeneration({ ...handoff, jobId: job.id, generationId: attemptId });
        } else {
          setJobId(job.id);
        }
      },
      onError(error) {
        if (!isBackgroundConnectionError(error)) setGenerationId(undefined);
        if (error instanceof ApiError && error.status === 409) {
          void cache.invalidateQueries({ queryKey: ["/repositories"] });
        }
      },
    });
  }}>
    {pending ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />
      : savedSummary ? <RefreshCw aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
    {pending ? "Generating…" : savedSummary ? "Regenerate"
      : error ? "Retry generation" : mode === "summary" ? "Generate summary" : "Generate context"}
  </Button>;
  return (
    <div className="space-y-2">
      {dialog}
      {savedSummary ? <>
        <Markdown>{savedSummary}</Markdown>
        <div className="-ml-2 flex flex-wrap items-center gap-1">
          {generationAction}
          <Button asChild variant="ghost" size="sm">
            <Link to={`${settings}?tab=context`}><Pencil aria-hidden="true" /> Edit</Link>
          </Button>
        </div>
        <ErrorNotice error={error} />
      </> : <>
        <ErrorNotice error={error} />
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed p-5">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-foreground">No repository summary yet</h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">Give readers a quick overview of what this repository does.</p>
          </div>
          {generationAction}
        </div>
      </>}
      <span role="status" className="sr-only">{pending ? mode === "context" ? "Generating project context." : "Generating repository summary." : ""}</span>
    </div>
  );
}
