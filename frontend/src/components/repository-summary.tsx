import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, type Repository } from "../api";
import { isBackgroundConnectionError, savedBackgroundInputs, useBackgroundStatus, useStartBackground } from "../hooks/use-background-generation";
import { ErrorNotice, Markdown } from "./common";
import { useContextGenerationGuard } from "./context-generation-guard";
import { Button } from "./ui/button";

export function RepositorySummary({ repository, admin }: { repository: Repository; admin: boolean }) {
  const summary = repository.settings.project_summary;
  const initiallySaved = useRef(!!summary);
  return <>
    <span role="status" className="sr-only">{summary && !initiallySaved.current ? "Repository summary saved." : ""}</span>
    {summary ? <Markdown>{summary}</Markdown>
      : admin ? <GenerateRepositorySummary repository={repository} /> : null}
  </>;
}

function GenerateRepositorySummary({ repository }: { repository: Repository }) {
  const [jobId, setJobId] = useState<string>();
  const [generationId, setGenerationId] = useState<string>();
  const generationInputs = useRef("");
  const [contextGeneration, setContextGeneration] = useState<{ jobId: string; generationId: string; inputs: string }>();
  const { dialog, blocked, handOff } = useContextGenerationGuard(repository.id, generationId);
  const navigate = useNavigate();
  const cache = useQueryClient();
  const start = useStartBackground(repository.id);
  const result = useBackgroundStatus(repository.id, jobId);
  const mode = !generationId && repository.settings.background?.trim() ? "summary" : "context";
  const settings = `/repositories/${repository.id}/settings`;
  const success = result.data?.status === "done" && !!result.data.project_summary;
  useEffect(() => {
    if (success) void cache.invalidateQueries({ queryKey: ["/repositories"] });
  }, [cache, success]);
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
  return (
    <div className="space-y-2">
      {dialog}
      <ErrorNotice error={error} />
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed p-5">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground">No repository summary yet</h2>
          <p className="mt-1 text-sm leading-relaxed text-muted-foreground">Give readers a quick overview of what this repository does.</p>
        </div>
        {needsSetup ? <Button asChild variant="outline" size="sm">
          <Link to={`${settings}?tab=connection`}>Configure AI connection</Link>
        </Button> : <Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => {
          if (!generationId) generationInputs.current = savedBackgroundInputs(repository);
          const attemptId = mode === "context" ? generationId ?? crypto.randomUUID() : undefined;
          setGenerationId(attemptId);
          setJobId(undefined);
          start.mutate({ mode, generation_id: attemptId }, {
            onSuccess(job) {
              if (attemptId) {
                setContextGeneration({ jobId: job.id, generationId: attemptId, inputs: generationInputs.current });
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
          {pending ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
          {pending ? "Generating…" : error ? "Retry generation" : mode === "summary" ? "Generate summary" : "Generate context"}
        </Button>}
      </div>
      <span role="status" className="sr-only">{pending ? `Generating project ${mode}.` : ""}</span>
    </div>
  );
}
