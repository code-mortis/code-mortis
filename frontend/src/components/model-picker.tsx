import { useEffect, useRef, useState } from "react";
import { useApi, type Schema } from "../api";
import { ErrorNotice, Field, NativeSelect } from "./common";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

type Catalog = Schema["ModelCatalog"];

export function ModelPicker({
  value, onChange, path, loadDraft, context = path ?? "", enabled = true,
  required = false, label = "Model", emptyLabel = "Choose a model", hint,
}: {
  value: string;
  onChange: (value: string) => void;
  path?: string;
  loadDraft?: () => Promise<Catalog>;
  context?: string;
  enabled?: boolean;
  required?: boolean;
  label?: string;
  emptyLabel?: string;
  hint?: string;
}) {
  const query = useApi<Catalog>(path ?? "", false, enabled && !!path);
  const [custom, setCustom] = useState(false);
  const [draft, setDraft] = useState<{ context: string; data?: Catalog; error?: unknown; pending?: boolean }>();
  const request = useRef(0);
  useEffect(() => {
    request.current++;
    return () => { request.current++; };
  }, [context, enabled]);
  const current = draft?.context === context ? draft : undefined;
  const catalog = enabled && !query.isFetching ? (current ? current.data : query.data) : undefined;
  const pending = enabled && (current?.pending || query.isFetching);
  const models = catalog?.models ?? [];
  const error = current ? current.error : enabled && path ? query.error : undefined;
  const canLoad = enabled && (!!path || !!loadDraft);
  async function refresh() {
    if (!loadDraft) {
      await query.refetch();
      return;
    }
    const identity = ++request.current;
    setDraft({ context, pending: true });
    try {
      const data = await loadDraft();
      if (identity === request.current) setDraft({ context, data });
    } catch (error) {
      if (identity === request.current) setDraft({ context, error });
    }
  }
  return (
    <div className="min-w-0 space-y-2">
      <Field label={label}>
        {(id) => (
          <div className="flex flex-wrap gap-2">
            <NativeSelect
              id={id}
              className="min-w-0 flex-1 basis-48"
              value={custom ? "\u0000custom" : value}
              required={required}
              disabled={!enabled}
              onChange={(event) => {
                const next = event.target.value;
                setCustom(next === "\u0000custom");
                if (next !== "\u0000custom") onChange(next);
              }}
            >
              <option value="">{emptyLabel}</option>
              {value && !models.some((model) => model.id === value) && (
                <option value={value}>{value} (current)</option>
              )}
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name === model.id ? model.id : `${model.name} · ${model.id}`}
                </option>
              ))}
              <option value={"\u0000custom"}>Enter a custom model ID</option>
            </NativeSelect>
            <Button type="button" variant="outline" disabled={!canLoad || !!pending} onClick={refresh}>
              {pending ? "Loading models…" : catalog || path ? "Refresh models" : "Load models"}
            </Button>
          </div>
        )}
      </Field>
      {custom && (
        <Field label="Custom model ID">
          {(id) => (
            <Input id={id} value={value} required={required} disabled={!enabled} maxLength={150}
              placeholder="Provider model ID" onChange={(event) => onChange(event.target.value)} />
          )}
        </Field>
      )}
      <ErrorNotice error={error} />
      <p role="status" className="text-xs leading-relaxed text-muted-foreground">
        {pending ? "Loading models for this connection…" : catalog?.message ?? (
          catalog?.status === "ready"
            ? models.length
              ? catalog.source === "client"
                ? "Models reported by the official client. Its catalog may be cached."
                : "Models reported by this endpoint. Tool support is checked by Test connection."
              : "No models were returned. Refresh or enter a custom model ID."
            : hint ?? "Load models from this connection or enter a custom model ID."
        )}
      </p>
    </div>
  );
}
