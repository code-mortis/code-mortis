import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Pencil, RefreshCw, Settings2, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, type Repository, type Schema } from "../api";
import { backgroundFinished, isBackgroundConnectionError, savedBackgroundInputs, useBackgroundStatus, useStartBackground, type BackgroundHandoff } from "../hooks/use-background-generation";
import { ErrorNotice, Markdown, PageTitle } from "./common";
import { useContextGenerationGuard } from "./context-generation-guard";
import { activityQueryKey, useActivityRead } from "../hooks/use-activity-read";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const headerActionClass = "min-h-11 min-w-11 text-muted-foreground sm:min-h-9 sm:min-w-9 [&_svg:not([class*='size-'])]:size-3.5";

export function RepositorySummary({ repository, admin }: { repository: Repository; admin: boolean }) {
  const summary = repository.settings.project_summary;
  const initiallySaved = useRef(!!summary);
  return <>
    <span role="status" className="sr-only">{summary && !initiallySaved.current ? "Repository summary saved." : ""}</span>
    {admin ? <GenerateRepositorySummary repository={repository} /> : (
      <PageTitle title="Overview">{summary && <Markdown>{summary}</Markdown>}</PageTitle>
    )}
  </>;
}

function GenerateRepositorySummary({ repository }: { repository: Repository }) {
  const summaryJobKey = `repository-summary-job:${repository.id}`;
  const [jobId, setJobId] = useState(() => sessionStorage.getItem(summaryJobKey) ?? undefined);
  const [generationId, setGenerationId] = useState<string>();
  const generationRequest = useRef<{ mode: NonNullable<Schema["BackgroundInput"]["mode"]>; inputs: string }>(undefined);
  const completedJobId = useRef<string>(undefined);
  const [contextGeneration, setContextGeneration] = useState<BackgroundHandoff>();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const start = useStartBackground(repository.id);
  const result = useBackgroundStatus(repository.id, jobId);
  useActivityRead("repository_summary", result.data?.id, result.data?.finished_at, result.isSuccess ? result.dataUpdatedAt : 0);
  const savedSummary = repository.settings.project_summary;
  const mode = (generationId && generationRequest.current?.mode)
    || (savedSummary ? "summary_refresh" : repository.settings.background?.trim() ? "summary" : "context");
  const finished = backgroundFinished(result.data?.status);
  const statusDenied = !!result.error && !isBackgroundConnectionError(result.error);
  const { dialog, blocked, handOff } = useContextGenerationGuard(repository.id,
    mode === "context" && !finished && !statusDenied ? generationId : undefined);
  const settings = `/repositories/${repository.id}/settings`;
  useEffect(() => {
    const job = result.data;
    if ((finished || statusDenied) && sessionStorage.getItem(summaryJobKey) === jobId) {
      sessionStorage.removeItem(summaryJobKey);
    }
    if (!job || !backgroundFinished(job.status) || completedJobId.current === job.id) return;
    completedJobId.current = job.id;
    setGenerationId(undefined);
    if (job.status !== "done" || !job.project_summary || generationRequest.current?.mode === "context") return;

    setJobId(undefined);
    void cache.invalidateQueries({ queryKey: ["/repositories"] }).then(() => {
      const saved = cache.getQueryData<Repository[]>(["/repositories"])
        ?.find((row) => row.id === repository.id)?.settings.project_summary;
      if (job.mode === "summary_refresh" && saved === job.project_summary) toast.success("Repository summary updated.");
    });
    void cache.invalidateQueries({ queryKey: activityQueryKey });
  }, [cache, finished, jobId, repository.id, result.data, statusDenied, summaryJobKey]);
  useEffect(() => {
    if (contextGeneration && !blocked) {
      handOff();
      navigate(`${settings}?tab=context`, { state: { contextGeneration } });
    }
  }, [contextGeneration, blocked, handOff, navigate, settings]);

  const error = start.error ?? result.error ?? (result.data?.status === "failed"
    ? new Error(result.data.error || "Summary generation failed. Try again.")
    : result.data?.status === "done" && !result.data.project_summary ? new Error("The model returned no summary. Try again.") : null);
  const lostStatus = !!jobId && !!result.error && isBackgroundConnectionError(result.error) && !finished;
  const pending = !error && (start.isPending || (!!jobId && !finished) || !!contextGeneration);
  const needsSetup = !repository.connection_id || repository.connection_requires_setup
    || (start.error instanceof ApiError && start.error.status === 422);
  const actionVariant = savedSummary ? "ghost" : "outline";
  const actionSize = savedSummary ? "icon" : "sm";
  const actionClass = savedSummary ? headerActionClass : undefined;
  const actionLabel = needsSetup ? "Configure AI connection" : lostStatus ? "Check status" : pending ? "Generating…"
    : savedSummary ? "Regenerate" : error ? "Retry generation" : mode === "summary" ? "Generate summary" : "Generate context";
  const generationAction = needsSetup ? <Button asChild variant={actionVariant} size={actionSize} className={actionClass}>
    <Link to={`${settings}?tab=connection`} aria-label={actionLabel}>{savedSummary ? <Settings2 aria-hidden="true" /> : actionLabel}</Link>
  </Button> : <Button type="button" variant={actionVariant} size={actionSize} className={actionClass} aria-label={actionLabel} disabled={pending || (lostStatus && result.isFetching)} onClick={() => {
    if (lostStatus) {
      void result.refetch();
      return;
    }
    if (!generationId || statusDenied) generationRequest.current = {
      mode, inputs: mode === "context" ? savedBackgroundInputs(repository) : "",
    };
    const attemptId = !statusDenied && generationId ? generationId : crypto.randomUUID();
    setGenerationId(attemptId);
    setJobId(undefined);
    void start.mutateAsync({ mode, generation_id: attemptId }).then(
      (job) => {
        if (mode === "context" && job.status !== "failed" && job.status !== "cancelled") {
          setContextGeneration({ mode, inputs: generationRequest.current?.inputs ?? "", jobId: job.id, generationId: attemptId });
        } else {
          if (mode !== "context") sessionStorage.setItem(summaryJobKey, job.id);
          setJobId(job.id);
        }
      },
      (error) => {
        if (!isBackgroundConnectionError(error)) setGenerationId(undefined);
        if (error instanceof ApiError && error.status === 409) {
          void cache.invalidateQueries({ queryKey: ["/repositories"] });
        }
      },
    );
  }}>
    {pending ? <LoaderCircle className="motion-safe:animate-spin" aria-hidden="true" />
      : savedSummary ? <RefreshCw aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
    {!savedSummary && actionLabel}
  </Button>;
  const actions = savedSummary && (
    <div className="ml-auto flex shrink-0 items-center gap-1 self-center">
      <Tooltip>
        <TooltipTrigger asChild>{generationAction}</TooltipTrigger>
        <TooltipContent sideOffset={4}>{actionLabel}</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="icon" className={headerActionClass}>
            <Link to={`${settings}?tab=context`} aria-label="Edit"><Pencil aria-hidden="true" /></Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent sideOffset={4}>Edit</TooltipContent>
      </Tooltip>
    </div>
  );
  return (
    <PageTitle title="Overview" titleMeta={actions} headingProps={{ className: "self-center" }}>
      <div className="space-y-2">
        {dialog}
        {savedSummary ? <>
          <Markdown>{savedSummary}</Markdown>
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
    </PageTitle>
  );
}
