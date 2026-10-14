import { useId, useLayoutEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ArrowUpRight, Check, Copy, FolderGit2, Github, MoreHorizontal, Plug, Plus, UserRound } from "lucide-react";
import {
  api,
  useAction,
  useApi,
  type Connection,
  type Repository,
  type Schema,
} from "./api";
import { PersonalConnection } from "./subscriptions";
import { ModelPicker } from "./components/model-picker";
import { SearchSelect } from "./components/search-select";
import { BackgroundField } from "./components/background-field";
import { GitHubInstallations } from "./components/github-installations";
import { Button } from "./components/ui/button";
import { AppearanceSettings } from "./components/appearance-settings";
import type { ColorMode, ThemeId } from "./lib/appearance";
import { Avatar, AvatarFallback } from "./components/ui/avatar";
import { Badge } from "./components/ui/badge";
import { Input } from "./components/ui/input";
import { Textarea } from "./components/ui/textarea";
import { Switch } from "./components/ui/switch";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
import {
  date,
  ErrorNotice,
  Field,
  Loading,
  Status,
} from "./components/common";

type Provider = {
  name: string;
  displayName: string;
  protocol: string;
  baseUrl: string;
  ambientAuth?: boolean;
};
type Instance = {
  github_app: { id: string; slug: string; install_url: string };
  installations: { id: number; account: string; active: boolean }[];
  members: { id: number; login: string; role: string }[];
  failed_jobs: { id: string; kind: string; error: string; available_at?: string | null; attempts?: number }[];
  public_url: string;
  webhook_url?: string;
  configuration_errors?: string[];
};
const protocols = [
  "openai",
  "openai-responses",
  "anthropic",
  "anthropic-bedrock",
] as const;
const apiProviderLogos = new Map([
  ["anthropic", "claude"],
  ["baidu-qianfan", "baiducloud"],
  ["bedrock", "bedrock"],
  ["dashscope", "alibabacloud"],
  ["dashscope-tokenplan", "alibabacloud"],
  ["deepseek", "deepseek"],
  ["edenai", "edenai"],
  ["gemini", "gemini"],
  ["hy-tokenplan", "hunyuan"],
  ["iflytek", "iflytekcloud"],
  ["kimi", "kimi"],
  ["kimi-global", "kimi"],
  ["litellm", "litellm"],
  ["mimo", "xiaomimimo"],
  ["minimax", "minimax"],
  ["minimax-cn", "minimax"],
  ["mistral", "mistral"],
  ["novita", "novita"],
  ["ollama-cloud", "ollama"],
  ["openai", "openai"],
  ["openai-responses", "openai"],
  ["openrouter", "openrouter"],
  ["siliconflow", "siliconcloud"],
  ["siliconflow-cn", "siliconcloud"],
  ["tencent-tokenhub", "tencent"],
  ["volcengine", "volcengine"],
  ["xai", "xai"],
  ["z-ai", "zai"],
  ["z-ai-coding", "zai"],
  ["opencode", "opencode"],
]);
function ProviderLogo({ logo, className = "" }: { logo: string; className?: string }) {
  return <img
    src={`/brands/${logo}.svg`}
    alt="" aria-hidden="true" width={16} height={16}
    className={`size-4 shrink-0 ${logo === "claude" ? "" : logo === "litellm" ? "dark:brightness-0 dark:invert" : "dark:invert"} ${className}`}
  />;
}
const languages = ["Arabic", "Bengali", "Bulgarian", "Catalan", "Chinese (Simplified)", "Chinese (Traditional)", "Croatian", "Czech", "Danish", "Dutch", "English", "Finnish", "French", "German", "Greek", "Hebrew", "Hindi", "Hungarian", "Indonesian", "Italian", "Japanese", "Korean", "Malay", "Norwegian", "Persian", "Polish", "Portuguese", "Romanian", "Russian", "Serbian", "Slovak", "Spanish", "Swedish", "Tamil", "Thai", "Turkish", "Ukrainian", "Urdu", "Vietnamese"];
function parseObject(value: string, label: string) {
  let data: unknown;
  try {
    data = JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label} must contain valid JSON. Check quotes, commas, and braces, then try again.`);
  }
  if (!data || typeof data !== "object" || Array.isArray(data))
    throw new Error(`${label} must be a JSON object.`);
  return data;
}

function memberRoleLabel(role: string) {
  if (role === "admin") return "Administrator";
  if (role === "member") return "Member";
  return role;
}

export function RepositorySettings({
  repository: repo,
  onSaved,
}: {
  repository: Repository;
  onSaved: () => void;
}) {
  const connections = useApi<Connection[]>(
    `/connections?repository_id=${repo.id}`,
  );
  const action = useAction();
  const id = useId();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const sections = [["reviews", "Review behavior"], ["connection", "AI connection"], ["context", "Project context"], ["limits", "Limits & rules"]];
  const section = sections.some(([value]) => value === params.get("tab")) ? params.get("tab")! : "reviews";
  const invalidField = useRef<HTMLInputElement | null>(null);
  useLayoutEffect(() => {
    invalidField.current?.focus();
    invalidField.current = null;
  }, [section]);
  function sectionSearch(value: string) {
    const next = new URLSearchParams(params);
    next.set("tab", value);
    return next;
  }
  const [enabled, setEnabled] = useState(repo.enabled);
  const [connection, setConnection] = useState(repo.connection_id ?? "");
  const [settings, setSettings] = useState(repo.settings);
  const limitedValues = useRef({
    timeout_seconds: repo.settings.timeout_seconds ?? 900,
    token_budget: repo.settings.token_budget ?? 200000,
    max_files: repo.settings.max_files ?? 40,
  });
  const [rules, setRules] = useState(
    repo.settings.rules ? JSON.stringify(repo.settings.rules, null, 2) : "",
  );
  const [error, setError] = useState<unknown>();
  const selectedConnection = connections.data?.find((item) => item.id === connection);
  const personal = ["chatgpt", "claude_code"].includes(
    selectedConnection?.kind ?? "api",
  );
  function change<K extends keyof typeof settings>(
    key: K,
    value: (typeof settings)[K],
  ) {
    setSettings({ ...settings, [key]: value });
  }
  return (
    <form
      aria-label="Repository configuration"
      className="space-y-6"
      onInvalidCapture={(event) => {
        event.preventDefault();
        const invalid = event.currentTarget.querySelector<HTMLInputElement>(":invalid");
        if (!invalid || invalid !== event.target) return;
        const field = invalid.getAttribute("aria-hidden") === "true"
          ? invalid.parentElement?.querySelector<HTMLInputElement>('input[role="combobox"]') ?? invalid
          : invalid;
        const target = invalid.closest<HTMLElement>("[data-settings-section]")?.dataset.settingsSection;
        setError(new Error(invalid.validationMessage));
        if (target && target !== section) {
          invalidField.current = field;
          setParams(sectionSearch(target), { state: location.state });
        } else field.focus();
      }}
      onSubmit={async (e) => {
        e.preventDefault();
        setError(undefined);
        try {
          await action.mutateAsync({
            path: `/repositories/${repo.id}`,
            method: "PUT",
            body: {
              enabled,
              connection_id: connection || null,
              settings: {
                ...settings,
                concurrency: personal ? 1 : settings.concurrency,
                rules: rules.trim() ? parseObject(rules, "Rules") : null,
              },
            },
          });
          toast.success("Repository configuration saved.");
          onSaved();
        } catch (e) {
          setError(e);
        }
      }}
    >
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Repository configuration</h1>
        <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
          <FolderGit2 className="size-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">{repo.full_name}</span>
        </p>
      </header>
      <div className="grid gap-7 border-t pt-6 lg:grid-cols-[180px_minmax(0,1fr)] lg:gap-10">
        <nav aria-label="Repository settings sections" className="flex flex-wrap self-start gap-1 lg:flex-col">
          {sections.map(([value, label]) => <Link key={value} to={`?${sectionSearch(value)}`} state={location.state}
            aria-current={section === value ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${section === value ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
            {label}
          </Link>)}
        </nav>
        <div className="min-w-0 max-w-2xl">
          <section hidden={section !== "reviews"} data-settings-section="reviews" aria-labelledby={`${id}-reviews`} className="space-y-6">
            <div>
              <h2 id={`${id}-reviews`} className="text-xl font-semibold tracking-tight">Review behavior</h2>
              <p className="mt-1 text-sm text-muted-foreground">Choose when reviews run and how findings are handled.</p>
            </div>
            <div className="divide-y rounded-lg border bg-card">
              <div className="flex items-start justify-between gap-4 p-5">
                <div className="space-y-2">
                  <label htmlFor={`${id}-enabled`} className="text-sm font-medium">Enable reviews</label>
                  <p id={`${id}-enabled-hint`} className="text-xs leading-relaxed text-muted-foreground">Allow reviews for this repository using the selected AI connection.</p>
                </div>
                <Switch id={`${id}-enabled`} aria-describedby={`${id}-enabled-hint`} checked={enabled} onCheckedChange={setEnabled} className="mt-0.5 shrink-0" />
              </div>
              <div className="flex items-start justify-between gap-4 p-5">
                <div className="space-y-2">
                  <label htmlFor={`${id}-automatic`} className="text-sm font-medium">Automatic reviews</label>
                  <p id={`${id}-automatic-hint`} className="text-xs leading-relaxed text-muted-foreground">Reviews new commits on open, non-draft pull requests. Forks require a manual start.</p>
                </div>
                <Switch id={`${id}-automatic`} aria-describedby={`${id}-automatic-hint`} checked={settings.automatic} onCheckedChange={(value) => change("automatic", value)} className="mt-0.5 shrink-0" />
              </div>
              <div className="flex items-start justify-between gap-4 p-5">
                <div className="space-y-2">
                  <label htmlFor={`${id}-resolve`} className="text-sm font-medium">Automatically resolve verified fixes</label>
                  <p id={`${id}-resolve-hint`} className="text-xs leading-relaxed text-muted-foreground">Requires Automatic reviews and GitHub App Contents: Read and write, approved on the installation. Uses the remaining review allowance; uncertain findings stay open.</p>
                </div>
                <Switch id={`${id}-resolve`} aria-describedby={`${id}-resolve-hint`} checked={settings.auto_resolve ?? false} disabled={!settings.automatic} onCheckedChange={(value) => change("auto_resolve", value)} className="mt-0.5 shrink-0" />
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Language">
                {(id) => (
                  <SearchSelect
                    id={id}
                    required
                    value={settings.language}
                    onChange={(value) => change("language", value)}
                    options={languages.map((value) => ({ value, label: value }))}
                    placeholder="Choose or enter a language"
                    allowCustom
                  />
                )}
              </Field>
              <Field label="Review effort">
                {(id) => (
                  <SearchSelect
                    id={id}
                    required
                    value={settings.effort}
                    onChange={(value) =>
                      change("effort", value as "low" | "medium" | "high")
                    }
                    options={["low", "medium", "high"].map((value) => ({ value, label: value }))}
                    placeholder="Choose review effort"
                  />
                )}
              </Field>
            </div>
          </section>
          <section hidden={section !== "connection"} data-settings-section="connection" aria-labelledby={`${id}-connection`} className="space-y-6">
            <div>
              <h2 id={`${id}-connection`} className="text-xl font-semibold tracking-tight">Connection and model</h2>
              <p className="mt-1 text-sm text-muted-foreground">Choose the connection and model used to review this repository.</p>
            </div>
            <Field label="AI connection">
              {(id) => (
                <SearchSelect
                  id={id}
                  value={connection}
                  disabled={connections.isPending}
                  required={enabled}
                  placeholder={connections.isPending ? "Loading connections…" : "Choose a connection"}
                  onChange={(value) => {
                    setConnection(value);
                    change("model", "");
                  }}
                  options={[
                    { value: "", label: "No connection" },
                    ...(connection && !selectedConnection ? [{ value: connection, label: connections.isPending ? "Loading connections…" : "Connection unavailable" }] : []),
                    ...(connections.data ?? []).map((item) => ({ value: item.id, label: item.name })),
                  ]}
                />
              )}
            </Field>
            <ModelPicker
              key={connection}
              label="Model override (optional)"
              value={settings.model}
              onChange={(value) => change("model", value)}
              path={connection ? `/connections/${connection}/models?repository_id=${repo.id}` : undefined}
              enabled={!!selectedConnection}
              emptyLabel={selectedConnection?.model || "Use connection model"}
              hint="Choose an AI connection to load its models."
            />
          </section>
          <section hidden={section !== "context"} data-settings-section="context" aria-labelledby={`${id}-context`} className="space-y-6">
            <div>
              <h2 id={`${id}-context`} className="text-xl font-semibold tracking-tight">Project context</h2>
              <p className="mt-1 text-sm text-muted-foreground">Give reviewers context and choose which files to exclude.</p>
            </div>
            <Field
              label="Exclude files"
              hint="Comma-separated OCR glob patterns, such as **/*.lock,dist/**."
            >
              {(id) => (
                <Input
                  id={id}
                  value={settings.exclusions}
                  onChange={(e) => change("exclusions", e.target.value)}
                />
              )}
            </Field>
            <BackgroundField
              repositoryId={repo.id}
              connectionId={connection}
              model={settings.model}
              defaultModel={selectedConnection?.model ?? ""}
              tokenBudget={settings.token_budget}
              timeoutSeconds={settings.timeout_seconds}
              value={settings.background}
              onChange={(value) => setSettings((current) => ({ ...current, background: value }))}
            />
          </section>
          <section hidden={section !== "limits"} data-settings-section="limits" aria-labelledby={`${id}-limits`} className="space-y-6">
            <div>
              <h2 id={`${id}-limits`} className="text-xl font-semibold tracking-tight">Limits & rules</h2>
              <p className="mt-1 text-sm text-muted-foreground">Control review size and execution, and optionally customize OCR rules.</p>
            </div>
            <div className="grid gap-6 sm:grid-cols-2">
              {([
                { key: "timeout_seconds", label: "Timeout (seconds)", min: 60, max: 3600,
                  hint: "Applies to reviews and background generation. Unlimited removes the deadline. Provider and connection timeouts still apply." },
                { key: "token_budget", label: "Token budget", min: 1000, max: 5000000,
                  hint: "Applies to reviews and background generation. Unlimited removes the token budget. Provider and account limits still apply." },
                { key: "max_files", label: "Maximum files", min: 1, max: undefined,
                  hint: "Skips the entire review when reviewable files exceed this limit. Excluded files do not count." },
              ] as const).map(({ key, label, min, max, hint }) => (
                <Field key={key} label={label} hint={hint}>
                  {(id) => (
                    <div className="space-y-2">
                      <Input
                        key={settings[key] === null ? "unlimited" : "limited"}
                        id={id}
                        type="number"
                        min={min}
                        max={max}
                        required
                        disabled={settings[key] === null}
                        className="disabled:bg-muted disabled:opacity-100 disabled:shadow-none dark:disabled:bg-muted"
                        defaultValue={settings[key] ?? ""}
                        onChange={(e) => {
                          const value = e.target.valueAsNumber;
                          if (!Number.isFinite(value) || !e.target.validity.valid) return;
                          limitedValues.current[key] = value;
                          change(key, value);
                        }}
                      />
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          className="size-4 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                          aria-label={`Unlimited ${label.toLowerCase()}`}
                          checked={settings[key] === null}
                          onChange={(e) => change(key, e.target.checked ? null : limitedValues.current[key])}
                        />
                        Unlimited
                      </label>
                    </div>
                  )}
                </Field>
              ))}
              <Field
                label="Concurrency"
                hint={
                  personal
                    ? "Personal subscriptions run one review at a time."
                    : undefined
                }
              >
                {(id) => (
                  <Input
                    key={personal ? "personal" : "api"}
                    id={id}
                    type="number"
                    min={1}
                    max={8}
                    required
                    disabled={personal}
                    defaultValue={personal ? 1 : settings.concurrency}
                    onChange={(event) => {
                      const value = event.target.valueAsNumber;
                      if (!Number.isFinite(value) || !event.target.validity.valid) return;
                      change("concurrency", value);
                    }}
                  />
                )}
              </Field>
            </div>
            <Field
              label="OCR rules JSON"
              hint="Optional rule-file object. Empty uses rules from the trusted base revision."
            >
              {(id) => (
                <Textarea
                  id={id}
                  rows={6}
                  className="font-mono text-xs"
                  value={rules}
                  onChange={(e) => setRules(e.target.value)}
                />
              )}
            </Field>
          </section>
          <footer className="mt-8 space-y-3 py-5">
            <ErrorNotice error={connections.error} />
            <ErrorNotice error={error} />
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button type="submit" disabled={action.isPending}>
                {action.isPending ? "Saving…" : "Save"}
              </Button>
            </div>
          </footer>
        </div>
      </div>
    </form>
  );
}

function ConnectionForm({
  connection,
  providers,
  canCreateApi,
  initialKind,
  onSaved,
  onSavingChange,
}: {
  connection?: Connection;
  providers: Provider[];
  canCreateApi: boolean;
  initialKind: string;
  onSaved: (connection: Connection) => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const [kind, setKind] = useState(connection?.kind ?? initialKind);
  const first = providers.find((p) => p.name === "openai") ?? providers[0];
  const [name, setName] = useState(connection?.name ?? "");
  const [provider, setProvider] = useState(
    connection?.provider ?? first?.name ?? "openai",
  );
  const [protocol, setProtocol] = useState(
    connection?.protocol ?? first?.protocol ?? "openai-responses",
  );
  const [url, setUrl] = useState(
    connection?.base_url ?? first?.baseUrl ?? "https://api.openai.com/v1",
  );
  const [model, setModel] = useState(connection?.model ?? "");
  const [key, setKey] = useState("");
  const [headers, setHeaders] = useState("");
  const { prompt_token_ceiling: savedCeiling, ...providerOptions } = connection?.options ?? {};
  const [promptCeiling, setPromptCeiling] = useState(String(savedCeiling ?? 58888));
  const [options, setOptions] = useState(
    JSON.stringify(providerOptions, null, 2),
  );
  const [error, setError] = useState<unknown>();
  const action = useAction<Connection>();
  // Draft credentials stay in component memory, never in query keys or browser storage.
  const context = JSON.stringify([kind, provider, protocol, url, key, headers, options]);
  const initialContext = useRef(context);
  const customEndpoint = provider === "custom";
  const preset = providers.find((item) => item.name === connection?.provider);
  function promptTokenCeiling() {
    const ceiling = Number(promptCeiling);
    if (!Number.isInteger(ceiling) || ceiling < 4096 || ceiling > 2000000) {
      throw new Error(`${kind === "api" ? "Evidence prompt" : "Prompt"} ceiling must be an integer between 4096 and 2000000.`);
    }
    return ceiling;
  }
  function apiBody() {
    return {
      kind, provider, protocol, base_url: url,
      ...(key ? { api_key: key } : {}),
      ...(headers.trim() ? { extra_headers: parseObject(headers, "Headers") } : {}),
      options: { ...parseObject(options, "Options"), prompt_token_ceiling: promptTokenCeiling() },
    };
  }
  function choose(value: string) {
    const preset = providers.find((p) => p.name === value);
    setProvider(value);
    if (value !== provider) {
      setModel("");
      setKey("");
      setHeaders("");
    }
    if (preset) {
      setProtocol(preset.protocol);
      setUrl(preset.baseUrl);
    }
  }
  const endpointFields = <>
    <Field label="API protocol">
      {(id) => <SearchSelect id={id} value={protocol} onChange={setProtocol}
        options={protocols.map((value) => ({ value, label: value }))} />}
    </Field>
    {protocol !== "anthropic-bedrock" && <Field label="API base URL">
      {(id) => <Input id={id} required type="url" value={url} onChange={(event) => setUrl(event.target.value)} />}
    </Field>}
  </>;
  return (
    <form
      className="space-y-5"
      onSubmit={async (e) => {
        e.preventDefault();
        if (action.isPending) return;
        onSavingChange(true);
        setError(undefined);
        try {
          const body =
            kind !== "api"
              ? { name, kind, model: connection ? model : "", options: { prompt_token_ceiling: promptTokenCeiling() } }
              : { name, model, ...apiBody() };
          const saved = await action.mutateAsync({
            path: connection ? `/connections/${connection.id}` : "/connections",
            method: connection ? "PUT" : "POST",
            body,
          });
          toast.success(connection ? "Connection saved." : "Connection created.");
          onSaved(saved);
        } catch (e) {
          setError(e);
        } finally {
          onSavingChange(false);
        }
      }}
    >
      <ErrorNotice error={error} />
      <section className="space-y-4" aria-label="Connection details">
        <h3 className="text-sm font-semibold">Connection details</h3>
        <Field label="Connection type">
          {(id) => (
            <SearchSelect
              id={id}
              value={kind}
              disabled={!!connection}
              onChange={setKind}
              options={[
                ...(canCreateApi ? [{ value: "api", label: "Shared API connection", icon: <Plug aria-hidden="true" className="size-4 shrink-0 text-primary" /> }] : []),
                { value: "chatgpt", label: "Personal ChatGPT subscription", icon: <ProviderLogo logo="openai" /> },
                { value: "claude_code", label: "Personal Claude Code subscription", icon: <ProviderLogo logo="claude" /> },
              ]}
            />
          )}
        </Field>
        <Field label="Connection name">
          {(id) => (
            <Input
              id={id}
              required
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Team review model"
            />
          )}
        </Field>
      </section>
      {kind === "api" && (
        <section className="space-y-4 border-t pt-5" aria-label="Provider and authentication">
          <h3 className="text-sm font-semibold">Provider and authentication</h3>
          <Field label="Provider">
            {(id) => (
              <SearchSelect
                id={id}
                value={provider}
                onChange={choose}
                options={providers.map((p) => {
                  const logo = apiProviderLogos.get(p.name);
                  return { value: p.name, label: p.displayName, icon: logo ? <ProviderLogo logo={logo} /> : <Plug aria-hidden="true" className="size-4 shrink-0 text-primary" /> };
                })}
              />
            )}
          </Field>
          {customEndpoint && endpointFields}
          <Field
            label={
              connection?.has_key ? "Replace API key (optional)" : "API key"
            }
            hint={
              protocol === "anthropic-bedrock"
                ? "Bedrock can use the worker’s AWS credentials."
                : connection?.has_key
                  ? "Leave blank to keep the stored key. Keys are encrypted and never returned to the browser."
                  : customEndpoint
                    ? "For a trusted local endpoint without authentication, enter a placeholder."
                    : "Keys are encrypted and never returned to the browser."
            }
          >
            {(id) => (
              <Input
                id={id}
                type="password"
                autoComplete="new-password"
                required={
                  !connection?.has_key && protocol !== "anthropic-bedrock"
                }
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
            )}
          </Field>
        </section>
      )}
      {kind === "api" && <section className="space-y-4 border-t pt-5" aria-label="Default model">
        <h3 className="text-sm font-semibold">Default model</h3>
        <ModelPicker
          value={model}
          onChange={setModel}
          required={kind === "api"}
          context={context}
          path={connection && (kind !== "api" || context === initialContext.current)
            ? `/connections/${connection.id}/models` : undefined}
          loadDraft={kind === "api" ? () => api<Schema["ModelCatalog"]>("/connections/models", "POST", {
            ...apiBody(), ...(connection ? { connection_id: connection.id } : {}),
          }) : undefined}
          hint={kind !== "api" ? "Save and sign in to load models. You can choose a model afterward." : undefined}
        />
      </section>}
      {kind !== "api" && <Field
        label="Prompt ceiling (tokens)"
        hint="Each prompt, including instructions and output schema, is checked conservatively against 80% of this value. The total review Token budget stays separate."
      >
        {(id) => <Input id={id} required type="number" min={4096} max={2000000} step={1}
          value={promptCeiling} onChange={(event) => setPromptCeiling(event.target.value)} />}
      </Field>}
      {kind !== "api" && !connection && <p className="text-sm text-muted-foreground">
        First save your connection, then sign in with the official client and choose a model.
        Automatic reviews require your separate authorization per repository.
      </p>}
      {kind === "api" && (
        <details className="border-t pt-5" open={!!connection && !!preset && (
          connection.protocol !== preset.protocol || connection.base_url !== preset.baseUrl
        )}>
          <summary className="cursor-pointer text-sm font-medium">
            Advanced provider settings
          </summary>
          <div className="mt-4 space-y-4">
            {!customEndpoint && endpointFields}
            <Field
              label="Evidence prompt ceiling (tokens)"
              hint="For assessments and fix verification. OCR accepts prompts up to 80% of this value. Choose a value your model supports; the total review token budget stays separate."
            >
              {(id) => <Input id={id} required type="number" min={4096} max={2000000} step={1}
                value={promptCeiling} onChange={(event) => setPromptCeiling(event.target.value)} />}
            </Field>
            <Field
              label="Extra headers JSON"
              hint={
                connection?.header_names?.length
                  ? `Stored headers: ${connection.header_names!.join(", ")}. Leave blank to keep them; use {} to clear.`
                  : "Values are encrypted alongside the API key."
              }
            >
              {(id) => (
                <Textarea
                  id={id}
                  rows={3}
                  className="font-mono text-xs"
                  value={headers}
                  onChange={(e) => setHeaders(e.target.value)}
                  placeholder={'{"X-Custom-Header": "value"}'}
                />
              )}
              </Field>
              <Field
                label="Provider options JSON"
                hint="timeout_sec, extra_body, retry_codes, aws_region, aws_profile, or auth_header."
              >
                {(id) => (
                  <Textarea
                    id={id}
                    rows={4}
                    className="font-mono text-xs"
                    value={options}
                    onChange={(e) => setOptions(e.target.value)}
                  />
                )}
              </Field>
            </div>
          </details>
      )}
      <div className="sticky bottom-0 border-t bg-background py-4">
        <Button disabled={action.isPending} type="submit">
          {action.isPending ? "Saving…" : kind !== "api" && !connection ? "Continue to sign in" : "Save connection"}
        </Button>
      </div>
    </form>
  );
}

export function SettingsPage({ user, mode, theme, dark, onModeChange, onThemeChange }: {
  user: Schema["UserOut"];
  mode: ColorMode;
  theme: ThemeId;
  dark: boolean;
  onModeChange: (mode: ColorMode) => void;
  onThemeChange: (theme: ThemeId) => void;
}) {
  const isAdmin = user.role === "admin";
  const [params] = useSearchParams();
  const sections = [["connections", "AI connections"], ...(isAdmin ? [["github", "GitHub"], ["team", "Team"]] : []), ["appearance", "Appearance"]];
  const tab = sections.some(([value]) => value === params.get("tab"))
    ? params.get("tab")! : "connections";
  const query = useApi<Instance>("/settings", true, isAdmin && tab !== "appearance");
  const connections = useApi<Connection[]>("/connections", true, tab === "connections");
  const providers = useApi<Provider[]>("/providers", false, tab === "connections");
  const action = useAction();
  const memberAction = useAction();
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const [newKind, setNewKind] = useState(isAdmin ? "api" : "chatgpt");
  const [savingConnection, setSavingConnection] = useState(false);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const [personal, setPersonal] = useState<Connection | null>(null);
  const [personalTab, setPersonalTab] = useState<"general" | "account">("account");
  const [member, setMember] = useState<Instance["members"][number] | "new" | null>(null);
  const [removingMember, setRemovingMember] = useState<Instance["members"][number] | null>(null);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  const [webhookCopy, setWebhookCopy] = useState<"copied" | "failed" | null>(null);
  const memberSubmitLabel = member === "new" ? "Add or update member" : "Save role";
  const returnFocus = useRef<HTMLElement | null>(null);
  const connectionHeading = useRef<HTMLHeadingElement>(null);
  const memberHeading = useRef<HTMLHeadingElement>(null);
  const instance = query.data;
  const administrators = instance?.members.filter((item) => item.role === "admin") ?? [];
  const lastAdministrator = administrators.length === 1 ? administrators[0].id : undefined;
  const editedMember = member === "new" ? instance?.members.find((item) => item.login.toLowerCase() === login.toLowerCase()) : member;
  const memberRoleLocked = editedMember?.role === "admin" && (editedMember.id === user.id || editedMember.id === lastAdministrator);
  const webhookEndpoint = instance?.webhook_url ?? `${instance?.public_url}/api/v1/webhooks/github`;
  const groups = [
    { title: "Shared API connections", kind: "api", items: connections.data?.filter((item) => (item.kind ?? "api") === "api") ?? [] },
    { title: "Your subscriptions", kind: "chatgpt", items: connections.data?.filter((item) => (item.kind ?? "api") !== "api") ?? [] },
  ];

  function openMember(value: Instance["members"][number] | "new") {
    if (memberAction.isPending) return;
    memberAction.reset();
    setLogin(value === "new" ? "" : value.login);
    setRole(value === "new" ? "member" : value.role);
    setMember(value);
  }
  function restoreFocus(event: Event) {
    event.preventDefault();
    returnFocus.current?.focus();
  }

  return <>
    <header className="mb-5">
      <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {isAdmin ? "Manage AI connections, GitHub installations, team access, and appearance." : "Manage your AI connections, personal subscriptions, and appearance."}
      </p>
    </header>
    <div className="grid min-w-0 gap-6 lg:grid-cols-[176px_minmax(0,1fr)]">
      <nav aria-label="Settings sections" className="flex flex-wrap self-start gap-1 lg:flex-col">
        {sections.map(([value, label]) => (
          <Link key={value} to={`/settings?tab=${value}`} aria-current={tab === value ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${tab === value ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
            {label}
          </Link>
        ))}
      </nav>
      <div className="settings-content min-w-0 space-y-6">
        {tab !== "appearance" && <ErrorNotice error={query.error} />}
        {tab === "appearance" && <AppearanceSettings mode={mode} theme={theme} dark={dark} onModeChange={onModeChange} onThemeChange={onThemeChange} />}
        {tab === "connections" && <section aria-labelledby="connections-heading" className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="connections-heading" ref={connectionHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">AI connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">Connections and default models for manual and automatic reviews.</p>
            </div>
            <Button disabled={!providers.data} onClick={(event) => {
              returnFocus.current = event.currentTarget;
              setNewKind(isAdmin ? "api" : "chatgpt");
              setEditing("new");
            }}><Plus className="size-4" />Add connection</Button>
          </div>
          <ErrorNotice error={connections.error || providers.error || (!deleting && action.error)} />
          {providers.isError && <Button variant="outline" size="sm" disabled={providers.isFetching} onClick={() => { void providers.refetch(); }}>
            Retry providers
          </Button>}
          {connections.isPending && <Loading />}
          {connections.data && <div className="space-y-6">
            {groups.map((group) => <section key={group.title} aria-label={group.title}>
              <h3 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">
                {group.title}<span className="text-xs font-normal tabular-nums text-muted-foreground">{group.items.length}</span>
              </h3>
              {!group.items.length ? <div className="flex flex-wrap items-center justify-between gap-4 rounded-lg border border-dashed p-5">
                <p className="max-w-md text-sm leading-relaxed text-muted-foreground">{group.kind === "api"
                  ? isAdmin ? "Add an API connection for reviews shared with your team." : "An administrator can add an API connection for your team."
                  : "Connect your ChatGPT or Claude Code subscription."}</p>
                {(group.kind !== "api" || isAdmin) && <Button variant="outline" size="sm" disabled={!providers.data} onClick={(event) => {
                  returnFocus.current = event.currentTarget;
                  setNewKind(group.kind);
                  setEditing("new");
                }}>{group.kind === "api" ? "Add API connection" : "Connect subscription"}</Button>}
              </div> : <div className="overflow-hidden rounded-lg border bg-card">
                <div aria-hidden="true" className="settings-record settings-connection-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
                  <span>Connection</span><span>Default model</span><span>Account status</span><span />
                </div>
                {group.items.map((connection) => {
                  const personalConnection = (connection.kind ?? "api") !== "api";
                  let kindLabel = `${connection.provider} · ${connection.protocol}`;
                  if (connection.kind === "chatgpt") kindLabel = "ChatGPT subscription";
                  else if (connection.kind === "claude_code") kindLabel = "Claude Code subscription";
                  else if (personalConnection) kindLabel = "Personal subscription";
                  const canManage = personalConnection ? connection.owner_id === user.id : isAdmin;
                  const Icon = personalConnection ? UserRound : Plug;
                  const logo = connection.kind === "chatgpt" ? "openai" : connection.kind === "claude_code" ? "claude"
                    : !personalConnection ? apiProviderLogos.get(connection.provider) : undefined;
                  const testing = connection.last_test === "Testing connection…"
                    || (action.isPending && action.variables?.path === `/connections/${connection.id}/test`);
                  const testStatus = testing ? "running" : !connection.last_test ? "not_tested"
                    : connection.last_test.startsWith("Connected.") ? "connected" : "failed";
                  const testDetail = testing ? "Testing connection…" : connection.last_test ?? undefined;
                  let nextAction = "Sign in";
                  if (personalConnection) {
                    if (connection.auth_status === "connected") nextAction = connection.model.trim() ? "Edit" : "Choose model";
                    else if (connection.auth_status === "connecting") nextAction = "Continue setup";
                    else if (connection.auth_status === "unavailable") nextAction = "Check account";
                  }
                  const needsSignIn = personalConnection && (nextAction === "Sign in" || nextAction === "Continue setup");
                  return <article key={connection.id} aria-label={connection.name}
                    className="settings-record settings-connection-row border-b px-4 py-4 text-sm last:border-b-0 sm:px-5">
                    <div className="settings-identity flex min-w-0 items-start gap-3">
                      {logo ? <ProviderLogo logo={logo} className="mt-0.5" /> : <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />}
                      <div className="min-w-0">
                        <h4 className="break-words font-medium">{connection.name}</h4>
                        <p className="mt-1 break-words text-xs text-muted-foreground">
                          {kindLabel}
                        </p>
                      </div>
                    </div>
                    <div className="min-w-0">
                      <span aria-hidden="true" className="settings-cell-label mb-1 text-xs text-muted-foreground">Default model</span>
                      <p className="break-words text-xs leading-relaxed"><span className="sr-only">Default model: </span>{connection.model || "Choose a model"}</p>
                    </div>
                    <div className="min-w-0 text-xs text-muted-foreground" role="status"
                      title={personalConnection ? undefined : testDetail}
                      aria-description={personalConnection ? undefined : testDetail}>
                      <span className="settings-cell-label mb-1">Account status</span>
                      {canManage && needsSignIn ? <Button variant="link" size="sm" className="px-0 text-xs underline" disabled={action.isPending}
                        onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          setPersonalTab("account");
                          setPersonal(connection);
                        }}>Complete sign in</Button> : personalConnection ? <Status value={connection.auth_status} /> : <Status value={testStatus}
                        label={testing ? "Testing" : testStatus === "not_tested" ? "Not tested" : undefined}
                        icon={testStatus === "connected" ? <Check aria-hidden="true" className="size-3" /> : undefined} />}
                    </div>
                    <div className="settings-actions flex items-center justify-end gap-1">
                      {canManage && personalConnection && !needsSignIn && nextAction !== "Edit" && <Button variant="outline" size="sm" disabled={action.isPending}
                        onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          setPersonalTab(nextAction === "Choose model" ? "general" : "account");
                          setPersonal(connection);
                        }}>
                        {nextAction}
                      </Button>}
                      {canManage && <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-8" disabled={action.isPending} aria-label={`Actions for ${connection.name}`}
                              onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                              onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (editing || deleting || personal) event.preventDefault(); }}>
                            <DropdownMenuItem disabled={!personalConnection && !providers.data} onSelect={() => {
                              if (personalConnection) {
                                setPersonalTab(connection.auth_status === "connected" ? "general" : "account");
                                setPersonal(connection);
                              } else setEditing(connection);
                            }}>Edit</DropdownMenuItem>
                            {!personalConnection && <DropdownMenuItem disabled={testing} onSelect={() => {
                              action.reset(); action.mutate({ path: `/connections/${connection.id}/test` });
                            }}>{testing ? "Testing…" : testStatus === "connected" ? "Test again" : "Test connection"}</DropdownMenuItem>}
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onSelect={() => { action.reset(); setDeleting(connection); }}>Delete</DropdownMenuItem>
                          </DropdownMenuContent>
                      </DropdownMenu>}
                    </div>
                  </article>;
                })}
              </div>}
            </section>)}
          </div>}
          <p className="text-xs leading-relaxed text-muted-foreground">
            API connections are shared. Personal subscriptions belong to you; automatic reviews require your explicit authorization per repository.
          </p>
        </section>}
        {(tab === "github" || tab === "team") && query.isPending && <Loading />}
        {tab === "github" && instance && <section aria-label="GitHub" className="space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-xl font-semibold tracking-tight">GitHub</h2>
              <p className="mt-1 text-sm text-muted-foreground">Manage the accounts and repositories connected to your review app.</p>
            </div>
            <Button asChild><a href={instance.github_app.install_url} target="_blank" rel="noreferrer">
              Install on GitHub<ArrowUpRight className="size-4" />
            </a></Button>
          </div>
          <section aria-label="GitHub App">
            <h3 className="mb-2.5 text-sm font-semibold">GitHub App</h3>
            <dl className="divide-y rounded-lg border bg-card text-sm">
              <div className="grid gap-2 px-5 py-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <dt className="text-xs text-muted-foreground">App identity</dt>
                <dd className="min-w-0">
                  <p className="flex items-center gap-2 font-medium">
                    <Github aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><span className="min-w-0 break-words">{instance.github_app.slug}</span>
                    {instance.github_app.slug && <Button asChild variant="ghost" size="icon" className="-my-1 size-8 shrink-0 text-muted-foreground">
                      <a href={`https://github.com/apps/${encodeURIComponent(instance.github_app.slug)}`} target="_blank" rel="noreferrer"
                        aria-label="Open GitHub App on GitHub" title="Open GitHub App on GitHub"><ArrowUpRight aria-hidden="true" className="size-3.5" /></a>
                    </Button>}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">Comments are published as this GitHub App.</p>
                </dd>
              </div>
              <div className="grid gap-2 px-5 py-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <dt className="text-xs text-muted-foreground">Webhook endpoint</dt>
                <dd className="min-w-0">
                  <div className="flex items-start gap-2">
                    <span className="min-w-0 break-all font-mono text-xs leading-relaxed">{webhookEndpoint}</span>
                    <Button variant="ghost" size="icon" className="-my-1.5 size-8 shrink-0 text-muted-foreground" aria-label="Copy webhook endpoint"
                      title={webhookCopy === "copied" ? "Copied" : "Copy webhook endpoint"} onClick={async () => {
                        setWebhookCopy(null);
                        try {
                          await navigator.clipboard.writeText(webhookEndpoint);
                          setWebhookCopy("copied");
                        } catch { setWebhookCopy("failed"); }
                      }}>
                      {webhookCopy === "copied" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
                    </Button>
                  </div>
                  {webhookCopy === "copied" && <span role="status" className="sr-only">Webhook endpoint copied.</span>}
                  {webhookCopy === "failed" && <p role="alert" className="mt-2 text-xs text-destructive">Could not copy. Select the endpoint and copy it manually.</p>}
                </dd>
              </div>
            </dl>
          </section>
          <GitHubInstallations />
        </section>}
        {tab === "team" && instance && <section aria-labelledby="members-heading" className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="members-heading" ref={memberHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">Team</h2>
              <p className="mt-1 text-sm text-muted-foreground">Manage who can sign in to this instance.</p>
            </div>
            <Button disabled={memberAction.isPending} onClick={(event) => { returnFocus.current = event.currentTarget; openMember("new"); }}>
              <Plus className="size-4" />Add member
            </Button>
          </div>
          {!member && !removingMember && <ErrorNotice error={memberAction.error} />}
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="settings-member-row settings-member-heading border-b bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground sm:px-5">
              <span>Member</span><span>Role</span><span />
            </div>
            {instance.members.map((item) => <div key={item.id} className="settings-member-row border-b px-4 py-4 last:border-b-0 sm:px-5">
              <div className="settings-member-identity flex min-w-0 items-center">
                <Avatar className="settings-member-avatar size-(--settings-avatar-size) rounded-md" aria-hidden="true"><AvatarFallback className="rounded-md text-xs">{item.login.slice(0, 2).toUpperCase()}</AvatarFallback></Avatar>
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium">@{item.login}</p>
                  {item.id === user.id && <p className="mt-0.5 text-xs text-muted-foreground">You</p>}
                </div>
              </div>
              <div className="settings-member-role"><Badge variant="outline" className="font-normal">{memberRoleLabel(item.role)}</Badge></div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="settings-member-actions size-8" disabled={memberAction.isPending} aria-label={`Actions for @${item.login}`}
                    onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                    onFocus={(event) => { returnFocus.current = event.currentTarget; }}><MoreHorizontal className="size-4" /></Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (member || removingMember) event.preventDefault(); }}>
                  <DropdownMenuItem disabled={item.id === user.id || item.id === lastAdministrator}
                    onSelect={() => openMember(item)}>Edit role</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" disabled={item.id === user.id || item.id === lastAdministrator} onSelect={() => {
                    memberAction.reset();
                    setRemovingMember(item);
                  }}>Remove</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>)}
          </div>
          <p className="flex items-start gap-2 text-xs leading-relaxed text-muted-foreground">
            <Github aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            Each person signs in with GitHub and only sees repositories they can access there.
          </p>
        </section>}
        {tab !== "appearance" && !!instance?.failed_jobs.length && <details className="pt-5">
          <summary className="cursor-pointer text-sm text-muted-foreground">Recent background errors ({instance.failed_jobs.length})</summary>
          <ul className="mt-4 space-y-3">
            {instance.failed_jobs.map((job) => <li key={job.id} className="rounded-md border p-3 text-xs">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="font-semibold">{job.kind}</span>
                {job.available_at && <time dateTime={job.available_at} className="text-muted-foreground">Scheduled {date(job.available_at)}</time>}
              </div>
              <p className="mt-1 break-words text-muted-foreground">{job.error}</p>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                {job.attempts !== undefined && <span>{job.attempts} {job.attempts === 1 ? "attempt" : "attempts"}</span>}
                <span className="min-w-0 break-all">Job <code>{job.id}</code></span>
              </div>
            </li>)}
          </ul>
        </details>}
      </div>
    </div>
    <Dialog open={!!editing || !!personal} onOpenChange={(open) => {
      if (!open && !savingConnection) { setEditing(null); setPersonal(null); }
    }}>
      <DialogContent className={personal ? "flex h-[min(660px,90dvh)] overflow-hidden sm:max-w-lg" : "max-h-[90dvh] overflow-y-auto sm:max-w-xl"} onCloseAutoFocus={restoreFocus}>
        {editing && <DialogHeader>
          <DialogTitle>{editing === "new" ? "Add AI connection" : "Edit AI connection"}</DialogTitle>
          <DialogDescription>Credentials are encrypted on this instance.</DialogDescription>
        </DialogHeader>}
        {editing && providers.data && <ConnectionForm key={editing === "new" ? "new" : editing.id}
          connection={editing === "new" ? undefined : editing} providers={providers.data} canCreateApi={isAdmin}
          initialKind={newKind}
          onSavingChange={setSavingConnection}
          onSaved={(saved) => { setEditing(null); if (saved.kind !== "api") { setPersonalTab("account"); setPersonal(saved); } }} />}
        {personal && <PersonalConnection key={personal.id} connection={personal} initialTab={personalTab}
          onSavingChange={setSavingConnection} onDone={() => setPersonal(null)} />}
      </DialogContent>
    </Dialog>
    <Dialog open={!!deleting} onOpenChange={(open) => { if (!open && !action.isPending) { setDeleting(null); action.reset(); } }}>
      <DialogContent onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Delete {deleting?.name}?</DialogTitle>
          <DialogDescription>Repositories must stop using this connection before it can be deleted.</DialogDescription>
        </DialogHeader>
        <ErrorNotice error={action.error} />
        <div className="flex justify-end gap-2">
          <Button variant="outline" disabled={action.isPending} onClick={() => { setDeleting(null); action.reset(); }}>Cancel</Button>
          <Button variant="destructive" disabled={action.isPending} onClick={() => {
            if (deleting) action.mutate({ path: `/connections/${deleting.id}`, method: "DELETE" }, {
              onSuccess: () => { returnFocus.current = connectionHeading.current; setDeleting(null); },
            });
          }}>{action.isPending ? "Deleting…" : "Delete connection"}</Button>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!removingMember} onOpenChange={(open) => { if (!open && !memberAction.isPending) { setRemovingMember(null); memberAction.reset(); } }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Remove @{removingMember?.login}?</DialogTitle>
          <DialogDescription>
            This revokes their access, ends their sessions, and cancels their queued, running, or blocked reviews.
            To restore access, add them again.
          </DialogDescription>
        </DialogHeader>
        <ErrorNotice error={memberAction.error} />
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" disabled={memberAction.isPending} onClick={() => { setRemovingMember(null); memberAction.reset(); }}>Cancel</Button>
          <Button variant="destructive" disabled={memberAction.isPending} onClick={() => {
            if (removingMember) memberAction.mutate({ path: `/members/${removingMember.id}`, method: "DELETE" }, {
              onSuccess: () => { returnFocus.current = memberHeading.current; setRemovingMember(null); },
            });
          }}>{memberAction.isPending ? "Removing…" : "Remove member"}</Button>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!member} onOpenChange={(open) => { if (!open && !memberAction.isPending) { setMember(null); memberAction.reset(); } }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>{member === "new" ? "Add member" : "Edit role"}</DialogTitle>
          <DialogDescription>{member === "new" ? "Allow a GitHub account to sign in. Entering an existing member updates their role." : "Update this member’s access to instance settings."}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={async (event) => {
          event.preventDefault();
          if (memberAction.isPending) return;
          try {
            await memberAction.mutateAsync({ path: "/members", body: { login, role: memberRoleLocked ? "admin" : role } });
            setMember(null);
          } catch { /* Keep the draft and display the action error. */ }
        }}>
          <ErrorNotice error={memberAction.error} />
          <Field label="GitHub username">{(id) => <Input id={id} required readOnly={member !== "new"} value={login}
            onChange={(event) => setLogin(event.target.value)} placeholder="username" />}</Field>
          <Field label="Role" hint={memberRoleLocked ? "Administrator access must be retained. Add another administrator before removing the last one." : undefined}>{(id) => <SearchSelect id={id} value={memberRoleLocked ? "admin" : role} disabled={memberRoleLocked} onChange={setRole}
            options={[{ value: "member", label: "Member" }, { value: "admin", label: "Administrator" }]} />}</Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="outline" disabled={memberAction.isPending} onClick={() => { setMember(null); memberAction.reset(); }}>Cancel</Button>
            <Button type="submit" disabled={memberAction.isPending}>{memberAction.isPending ? "Saving…" : memberSubmitLabel}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
