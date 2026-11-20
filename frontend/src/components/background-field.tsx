import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Sparkles } from "lucide-react";
import { isBackgroundConnectionError, useBackgroundStatus, useStartBackground } from "../hooks/use-background-generation";
import { ErrorNotice, Field } from "./common";
import { useContextGenerationGuard } from "./context-generation-guard";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type ContextDraft = { background: string; project_summary?: string | null };
// Keep aligned with BackgroundResult and RepoSettings.background in the API schema.
const BACKGROUND_MAX_LENGTH = 20_000;
const SUMMARY_MAX_LENGTH = 600;
const STALE_MESSAGE = "Your selections or context changed. Generate again to use the current values.";

export function BackgroundField({ repositoryId, connectionId, model, defaultModel, tokenBudget, timeoutSeconds, value, onChange, initialJobId, initialGenerationId, initialContextChanged }: {
  repositoryId: number;
  connectionId: string;
  model: string;
  defaultModel: string;
  tokenBudget: number | null;
  timeoutSeconds: number | null;
  value: ContextDraft;
  onChange: (value: ContextDraft) => void;
  initialJobId?: string;
  initialGenerationId?: string;
  initialContextChanged: boolean;
}) {
  const [jobId, setJobId] = useState(initialJobId);
  const [generationId, setGenerationId] = useState(initialGenerationId);
  const cancelledId = useRef<string>(undefined);
  const { dialog } = useContextGenerationGuard(repositoryId, generationId, (id) => {
    cancelledId.current = id;
    setGenerationId(undefined);
    setJobId(undefined);
  });
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
  const lostStatus = !!result.error && isBackgroundConnectionError(result.error);

  useEffect(() => {
    if (!jobId || lostStatus) return;
    const failed = result.error || result.data?.status === "failed";
    if (!failed && result.data?.status !== "done" && result.data?.status !== "cancelled") return;
    if (request.current.revision !== revision.current) {
      setMessage(STALE_MESSAGE);
      setError(undefined);
      setFailedJob(undefined);
    } else if (result.data?.status === "cancelled") {
      setMessage("");
    } else if (failed) {
      setError(result.error ?? new Error(result.data?.error || "Context generation failed. Try again."));
      setFailedJob(jobId);
      setMessage("");
    } else if (result.data?.background) {
      setUndo(request.current.previous);
      onChange({ background: result.data.background, project_summary: result.data.project_summary ?? value.project_summary });
      setMessage("Context generated. Review it, then save.");
    } else {
      setError(new Error("The model returned no context. Try again."));
    }
    setJobId(undefined);
    setGenerationId(undefined);
  }, [jobId, result.data, result.error, onChange, value.project_summary, lostStatus]);

  const pending = start.isPending || !!jobId;
  let missing = "";
  if (!connectionId) {
    missing = "Choose an AI connection to generate context.";
  } else if (!(model.trim() || defaultModel.trim())) {
    missing = "Choose a model to generate context.";
  }
  return (
    <div className="space-y-6">
      {dialog}
      <div className="flex flex-col gap-2">
        <Field
          label="Project context"
          hint={missing || "Context that helps reviewers understand the codebase. Generates from the default branch using the selected connection and model."}
          action={
            <div className="flex items-center gap-2">
              {undo !== undefined && (
                <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
                  onChange(undo);
                  setUndo(undefined);
                  setMessage("Previous context restored. Save to keep it.");
                }}>Undo</Button>
              )}
              <Button type="button" variant="outline" size="sm" disabled={lostStatus ? result.isFetching : pending || !!missing} onClick={async () => {
                if (lostStatus) {
                  void result.refetch();
                  return;
                }
                const attemptId = generationId ?? crypto.randomUUID();
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
                {pending ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <Sparkles className="size-4" aria-hidden="true" />}
                {lostStatus ? "Check status" : pending ? "Generating…" : "Generate context"}
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
        <p role="status" aria-label="Context generation status" className="text-xs leading-relaxed text-muted-foreground empty:sr-only">
          {pending ? "Generating project context. Changing the context, connection, model, or limits will discard this result." : message}
        </p>
        <ErrorNotice error={lostStatus ? result.error : error} />
        {failedJob && <p className="break-all text-xs text-muted-foreground">Generation reference: {failedJob}</p>}
      </div>
      <Field label="Repository summary" hint="Use Markdown for formatting. Save to update Overview.">
        {(id) => <Textarea id={id} rows={4} maxLength={SUMMARY_MAX_LENGTH}
          value={value.project_summary ?? ""} onChange={(event) => {
            onChange({ background: value.background, project_summary: event.target.value });
            setUndo(undefined);
            setMessage("");
            setError(undefined);
            setFailedJob(undefined);
          }} />}
      </Field>
    </div>
  );
}
