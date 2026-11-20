import { useCallback, useEffect, useRef, useState } from "react";
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
  const starting = useRef(false);
  const generationRequest = useRef<{ mode: NonNullable<Schema["BackgroundInput"]["mode"]>; inputs: string }>(undefined);
  const completedJobId = useRef<string>(undefined);
  const [contextGeneration, setContextGeneration] = useState<BackgroundHandoff>();
  const [stayOnOverview, setStayOnOverview] = useState(false);
  const deferredContext = stayOnOverview ? contextGeneration : undefined;
  const activeJobId = jobId ?? deferredContext?.jobId;
  const navigate = useNavigate();
  const cache = useQueryClient();
  const start = useStartBackground(repository.id);
  const result = useBackgroundStatus(repository.id, activeJobId);
  const isContextJob = !!deferredContext || result.data?.mode === "context" || generationRequest.current?.mode === "context";
  const summaryJob = jobId && !isContextJob ? result.data : undefined;
  useActivityRead("repository_summary", summaryJob?.id, summaryJob?.finished_at, result.isSuccess ? result.dataUpdatedAt : 0);
  const savedSummary = repository.settings.project_summary;
  const mode = (generationId && generationRequest.current?.mode)
    || (savedSummary ? "summary_refresh" : repository.settings.background?.trim() ? "summary" : "context");
  const finished = backgroundFinished(result.data?.status);
  const statusDenied = !!result.error && !isBackgroundConnectionError(result.error);
  const { dialog, blocked, handOff } = useContextGenerationGuard(repository.id,
    mode === "context" && !finished && !statusDenied ? generationId : undefined,
    undefined, false, () => setStayOnOverview(true));
  const settings = `/repositories/${repository.id}/settings`;
  const openContext = useCallback((handoff: BackgroundHandoff) => {
    setContextGeneration(undefined);
    handOff();
    navigate(`${settings}?tab=context`, { state: { contextGeneration: handoff } });
  }, [handOff, navigate, settings]);
  useEffect(() => {
    const job = result.data;
    if ((finished || statusDenied) && sessionStorage.getItem(summaryJobKey) === jobId) {
      sessionStorage.removeItem(summaryJobKey);
    }
    if (!job || !backgroundFinished(job.status) || completedJobId.current === job.id) return;
    completedJobId.current = job.id;
    setGenerationId(undefined);
    if (job.status !== "done" || !job.project_summary || isContextJob) return;

    setJobId(undefined);
    void cache.invalidateQueries({ queryKey: ["/repositories"] }).then(() => {
      const saved = cache.getQueryData<Repository[]>(["/repositories"])
        ?.find((row) => row.id === repository.id)?.settings.project_summary;
      if (job.mode === "summary_refresh" && saved === job.project_summary) toast.success("Repository summary updated.");
    });
    void cache.invalidateQueries({ queryKey: activityQueryKey });
  }, [cache, finished, isContextJob, jobId, repository.id, result.data, statusDenied, summaryJobKey]);
  useEffect(() => {
    if (contextGeneration && !blocked && !stayOnOverview) {
      openContext(contextGeneration);
    }
  }, [contextGeneration, blocked, openContext, stayOnOverview]);

  const error = start.error ?? result.error ?? (result.data?.status === "failed"
    ? new Error(result.data.error || (isContextJob ? "Project context generation failed. Try again." : "Summary generation failed. Try again."))
    : result.data?.status === "done" && !isContextJob && !result.data.project_summary ? new Error("The model returned no summary. Try again.") : null);
  const lostStatus = !!activeJobId && !!result.error && isBackgroundConnectionError(result.error) && !finished;
  const pending = !error && (start.isPending || ((!!activeJobId || !!contextGeneration) && !finished));
  const readyContext = !error && result.data?.status !== "cancelled" ? deferredContext : undefined;
  const needsSetup = !repository.connection_id || repository.connection_requires_setup
    || (start.error instanceof ApiError && start.error.status === 422);
  const actionVariant = savedSummary ? "ghost" : "outline";
  const actionSize = savedSummary ? "icon" : "sm";
  const actionClass = savedSummary ? headerActionClass : undefined;
  const actionLabel = needsSetup ? "Configure AI connection" : lostStatus ? "Check status"
    : readyContext ? "Open project context" : pending ? "Generating…"
    : savedSummary ? "Regenerate" : error ? "Retry generation" : mode === "summary" ? "Generate summary" : "Generate context";
  const generationAction = needsSetup ? <Button asChild variant={actionVariant} size={actionSize} className={actionClass}>
    <Link to={`${settings}?tab=connection`} aria-label={actionLabel}>{savedSummary ? <Settings2 aria-hidden="true" /> : actionLabel}</Link>
  </Button> : <Button type="button" variant={actionVariant} size={actionSize} className={actionClass} aria-label={actionLabel} disabled={(pending && !readyContext) || (lostStatus && result.isFetching)} onClick={() => {
    if (lostStatus) {
      void result.refetch();
      return;
    }
    if (readyContext) {
      openContext(readyContext);
      return;
    }
    if (starting.current) return;
    if (!generationId || statusDenied) {
      setStayOnOverview(false);
      generationRequest.current = { mode, inputs: mode === "context" ? savedBackgroundInputs(repository) : "" };
    }
    const attemptId = !statusDenied && generationId ? generationId : crypto.randomUUID();
    setGenerationId(attemptId);
    setJobId(undefined);
    setContextGeneration(undefined);
    starting.current = true;
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
    ).finally(() => { starting.current = false; });
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
