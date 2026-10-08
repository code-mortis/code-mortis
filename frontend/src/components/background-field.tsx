import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { api, type Schema } from "../api";
import { ErrorNotice, Field } from "./common";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

type Generation = Schema["BackgroundJobOut"];

export function BackgroundField({ repositoryId, connectionId, model, defaultModel, tokenBudget, timeoutSeconds, personal, value, onChange }: {
  repositoryId: number;
  connectionId: string;
  model: string;
  defaultModel: string;
  tokenBudget: number | null;
  timeoutSeconds: number | null;
  personal: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const [job, setJob] = useState<Generation>();
  const [error, setError] = useState<unknown>();
  const [message, setMessage] = useState("");
  const [failedJob, setFailedJob] = useState<string>();
  const [undo, setUndo] = useState<string>();
  const revision = useRef(0);
  const request = useRef({ revision: 0, previous: "" });
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    revision.current += 1;
  }, [repositoryId, connectionId, model, defaultModel, tokenBudget, timeoutSeconds, value]);

  const start = useMutation({
    mutationFn: () => api<Generation>(`/repositories/${repositoryId}/background`, "POST", {
      connection_id: connectionId, model, limits: { token_budget: tokenBudget, timeout_seconds: timeoutSeconds },
    }),
  });
  const result = useQuery({
    queryKey: [`/repositories/${repositoryId}/background/${job?.id}`],
    queryFn: () => api<Generation>(`/repositories/${repositoryId}/background/${job!.id}`),
    enabled: !!job,
    retry: false,
    refetchInterval: (query) => ["done", "failed"].includes(query.state.data?.status ?? "") ? false : 1000,
  });

  useEffect(() => {
    if (!job) return;
    if (result.error || result.data?.status === "failed") {
      setError(result.error ?? new Error(result.data?.error || "Background generation failed. Try again."));
      setFailedJob(job.id);
      setMessage("");
      setJob(undefined);
    } else if (result.data?.status === "done") {
      if (request.current.revision !== revision.current) {
        setMessage("Your selections or background changed. Generate again to use the current values.");
      } else if (result.data.background) {
        setUndo(request.current.previous);
        onChange(result.data.background);
        setMessage("Background generated. Review it, then save configuration.");
      } else {
        setError(new Error("The model returned no background. Try again."));
      }
      setJob(undefined);
    }
  }, [job, result.data, result.error, onChange]);

  const pending = start.isPending || !!job;
  const missing = !connectionId ? "Choose an AI connection to generate background."
    : !(model.trim() || defaultModel.trim()) ? "Choose a model to generate background." : "";
  return (
    <div className="space-y-2">
      <Field
        label="Project background"
        hint={missing || "Context that helps reviewers understand the codebase. Generates from the default branch using the selected connection and model."}
        action={
          <div className="flex items-center gap-2">
            {undo !== undefined && (
              <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
                onChange(undo);
                setUndo(undefined);
                setMessage("Previous background restored. Save configuration to keep it.");
              }}>Undo</Button>
            )}
            <Button type="button" variant="outline" size="sm" disabled={pending || !!missing} onClick={async () => {
              request.current = { revision: revision.current, previous: value };
              setError(undefined);
              setFailedJob(undefined);
              setMessage("");
              try {
                const created = await start.mutateAsync();
                if (mounted.current) setJob(created);
              } catch (error) {
                if (mounted.current) setError(error);
              }
            }}>
              {pending && <LoaderCircle className="size-4 motion-safe:animate-spin" />}
              {pending ? "Generating…" : "Generate background"}
            </Button>
          </div>
        }
      >
        {(id) => <Textarea id={id} rows={4} maxLength={20000} value={value} onChange={(event) => {
          onChange(event.target.value);
          setUndo(undefined);
          setMessage("");
        }} />}
      </Field>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Uses the current Review limits and rules settings:
        {personal ? "" : ` ${tokenBudget === null ? "unlimited tokens" : `${tokenBudget.toLocaleString()} tokens`},`}
        {timeoutSeconds === null ? " no overall timeout" : ` ${timeoutSeconds.toLocaleString()}-second timeout`}.
        {" Provider limits still apply."}
      </p>
      <p role="status" className="text-xs leading-relaxed text-muted-foreground">
        {pending ? "Generating project background. You can keep editing while it runs." : message}
      </p>
      <ErrorNotice error={error} />
      {failedJob && <p className="break-all text-xs text-muted-foreground">Generation reference: {failedJob}</p>}
    </div>
  );
}
