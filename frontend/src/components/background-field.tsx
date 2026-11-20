import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Sparkles } from "lucide-react";
import { backgroundFinished, isBackgroundConnectionError, useBackgroundStatus, useStartBackground } from "../hooks/use-background-generation";
import { ErrorNotice, Field } from "./common";
import { useContextGenerationGuard } from "./context-generation-guard";
import { useActivityRead } from "../hooks/use-activity-read";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type ContextDraft = { background: string; project_summary?: string | null };
// Keep aligned with BackgroundResult and RepoSettings.background in the API schema.
const BACKGROUND_MAX_LENGTH = 20_000;
const SUMMARY_MAX_LENGTH = 600;
const STALE_MESSAGE = "Your selections or context changed. Generate again to use the current values.";

export function BackgroundField({ repositoryId, connectionId, model, defaultModel, tokenBudget, timeoutSeconds, value, onChange, initialJobId, initialGenerationId, initialSummaryDraft, initialContextChanged }: {
  repositoryId: number;
  connectionId: string;
  model: string;
  defaultModel: string;
  tokenBudget: number | null;
  timeoutSeconds: number | null;
  value: ContextDraft;
  onChange: (value: Partial<ContextDraft>, generated?: boolean) => void;
  initialJobId?: string;
  initialGenerationId?: string;
  initialSummaryDraft: boolean;
  initialContextChanged: boolean;
}) {
  const [jobId, setJobId] = useState(initialJobId);
  const [generationId, setGenerationId] = useState(initialGenerationId);
  const [summaryDraft, setSummaryDraft] = useState(initialSummaryDraft);
  const cancelledId = useRef<string>(undefined);
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [failedJob, setFailedJob] = useState<string>();
  const [undo, setUndo] = useState<ContextDraft>();
  const revision = useRef(0);
  const request = useRef({ revision: initialContextChanged ? -1 : 0, previous: value });
  const selection = JSON.stringify([repositoryId, connectionId, model, tokenBudget, timeoutSeconds, value.background, value.project_summary]);
  const previousSelection = useRef(selection);
  const previousDefaultModel = useRef(defaultModel);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    if (previousSelection.current !== selection || (previousDefaultModel.current && previousDefaultModel.current !== defaultModel)) {
      revision.current += 1;
    }
    previousSelection.current = selection;
    previousDefaultModel.current = defaultModel;
  }, [selection, defaultModel]);
  useEffect(() => {
    setMessage("");
    setError(undefined);
    setFailedJob(undefined);
  }, [repositoryId, connectionId, model, tokenBudget, timeoutSeconds]);

  const start = useStartBackground(repositoryId);
  const result = useBackgroundStatus(repositoryId, jobId);
  const finished = backgroundFinished(result.data?.status);
  const activeGeneration = finished ? undefined : generationId;
  const { dialog } = useContextGenerationGuard(repositoryId, activeGeneration, (id) => {
    cancelledId.current = id;
    setGenerationId(undefined);
    setJobId(undefined);
  }, summaryDraft);
  useActivityRead(summaryDraft ? "repository_summary" : "context", result.data?.id, result.data?.finished_at,
    result.isSuccess ? result.dataUpdatedAt : 0);
  const lostStatus = !finished && !!result.error && isBackgroundConnectionError(result.error);

  useEffect(() => {
    if (!jobId || lostStatus) return;
    const failed = result.error || result.data?.status === "failed";
    if (!failed && result.data?.status !== "done" && result.data?.status !== "cancelled") return;
    const draft = summaryDraft ? result.data?.project_summary : result.data?.background;
    if (request.current.revision !== revision.current) {
      setMessage(STALE_MESSAGE);
      setError(undefined);
      setFailedJob(undefined);
    } else if (result.data?.status === "cancelled") {
      setMessage("");
    } else if (failed) {
      setError(result.error ?? new Error(result.data?.error || `${summaryDraft ? "Summary" : "Context"} generation failed. Try again.`));
      setFailedJob(jobId);
      setMessage("");
    } else if (draft) {
      setUndo(request.current.previous);
      onChange(summaryDraft ? { project_summary: draft }
        : { background: draft, project_summary: result.data?.project_summary ?? value.project_summary }, true);
      setMessage(`${summaryDraft ? "Summary" : "Context"} generated. Review it, then save.`);
    } else {
      setError(new Error(`The model returned no ${summaryDraft ? "summary" : "context"}. Try again.`));
    }
    setJobId(undefined);
    setGenerationId(undefined);
  }, [jobId, result.data, result.error, onChange, value.project_summary, lostStatus, summaryDraft]);

  const pending = start.isPending || !!jobId;
  let missing = "";
  if (!connectionId) {
    missing = "Choose an AI connection to generate context.";
  } else if (!(model.trim() || defaultModel.trim())) {
    missing = "Choose a model to generate context.";
  }
  const undoAction = undo !== undefined && (
    <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
      onChange(summaryDraft ? { project_summary: undo.project_summary } : undo);
      setUndo(undefined);
      setMessage(`Previous ${summaryDraft ? "summary" : "context"} restored. Save to keep it.`);
    }}>Undo</Button>
  );
  const feedback = <>
    <p role="status" aria-label={summaryDraft ? "Summary generation status" : "Context generation status"} className="text-xs leading-relaxed text-muted-foreground empty:sr-only">
      {pending ? summaryDraft ? "Generating repository summary…"
        : "Generating project context. Changing the context, connection, model, or limits will discard this result." : message}
    </p>
    <ErrorNotice error={lostStatus ? result.error : error} />
    {failedJob && <p className="break-all text-xs text-muted-foreground">Generation reference: {failedJob}</p>}
  </>;
  return (
    <div className="space-y-6">
      {dialog}
      <div className="flex flex-col gap-2">
        <Field
          label="Project context"
          hint={missing || "Context that helps reviewers understand the codebase. Generates from the default branch using the selected connection and model."}
          action={
            <div className="flex items-center gap-2">
              {!summaryDraft && undoAction}
              <Button type="button" variant="outline" size="sm" disabled={lostStatus && !summaryDraft ? result.isFetching : pending || !!missing} onClick={async () => {
                if (lostStatus) {
                  void result.refetch();
                  return;
                }
                const attemptId = generationId ?? crypto.randomUUID();
                setSummaryDraft(false);
                setGenerationId(attemptId);
                if (!generationId) request.current = { revision: revision.current, previous: value };
                setError(undefined);
                setFailedJob(undefined);
                setMessage("");
                try {
                  const created = await start.mutateAsync({
                    generation_id: attemptId, connection_id: connectionId, model,
                    limits: { token_budget: tokenBudget, timeout_seconds: timeoutSeconds },
                  });
                  if (mounted.current && cancelledId.current !== attemptId) setJobId(created.id);
                } catch (error) {
                  if (mounted.current && cancelledId.current !== attemptId) {
                    if (!isBackgroundConnectionError(error)) setGenerationId(undefined);
                    if (request.current.revision !== revision.current) setMessage(STALE_MESSAGE);
                    else setError(error);
                  }
                }
              }}>
                {pending && !summaryDraft ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Sparkles className="size-4" aria-hidden="true" />}
                {lostStatus && !summaryDraft ? "Check status" : pending && !summaryDraft ? "Generating…" : "Generate context"}
              </Button>
            </div>
          }
        >
          {(id) => <Textarea id={id} rows={4} className="min-h-48 max-h-96 resize-y overflow-y-auto" maxLength={BACKGROUND_MAX_LENGTH} value={value.background} onChange={(event) => {
            onChange({ background: event.target.value });
            setUndo(undefined);
            setMessage("");
            setError(undefined);
            setFailedJob(undefined);
          }} />}
        </Field>
        {!summaryDraft && feedback}
      </div>
      <div className="flex flex-col gap-2">
        <Field label="Repository summary" hint="Use Markdown for formatting. Save to update Overview."
          action={summaryDraft && (lostStatus ? <Button type="button" variant="outline" size="sm" disabled={result.isFetching}
            onClick={() => { void result.refetch(); }}>Check status</Button> : undoAction)}>
          {(id) => <Textarea id={id} rows={4} maxLength={SUMMARY_MAX_LENGTH}
            value={value.project_summary ?? ""} onChange={(event) => {
              onChange({ project_summary: event.target.value });
              setUndo(undefined);
              setMessage("");
              setError(undefined);
              setFailedJob(undefined);
            }} />}
        </Field>
        {summaryDraft && feedback}
      </div>
    </div>
  );
}
