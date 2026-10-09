import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowUpRight, ChevronDown, Github, MoreHorizontal, Plug, Plus, UserRound } from "lucide-react";
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
import { BackgroundField } from "./components/background-field";
import { GitHubInstallations } from "./components/github-installations";
import { Button } from "./components/ui/button";
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
  Empty,
  ErrorNotice,
  Field,
  Loading,
  NativeSelect,
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
  failed_jobs: { id: string; kind: string; error: string }[];
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
function parseObject(value: string, label: string) {
  const data = JSON.parse(value || "{}");
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
  showLimits = false,
}: {
  repository: Repository;
  onSaved: () => void;
  showLimits?: boolean;
}) {
  const connections = useApi<Connection[]>(
    `/connections?repository_id=${repo.id}`,
  );
  const action = useAction();
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
      className="space-y-6"
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
                rules: rules.trim() ? parseObject(rules, "Rules") : null,
              },
            },
          });
          onSaved();
        } catch (e) {
          setError(e);
        }
      }}
    >
      <ErrorNotice error={error || connections.error} />
      <Field label="Enable reviews">
        {(id) => (
          <Switch id={id} checked={enabled} onCheckedChange={setEnabled} />
        )}
      </Field>
      <Field label="AI connection">
        {(id) => (
          <NativeSelect
            id={id}
            value={connection}
            required={enabled}
            onChange={(e) => {
              setConnection(e.target.value);
              change("model", "");
            }}
          >
            <option value="">Choose a connection</option>
            {connections.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </NativeSelect>
        )}
      </Field>
      <Field
        label="Automatic reviews"
        hint="Reviews new commits on open, non-draft pull requests. Forks require a manual start."
      >
        {(id) => (
          <Switch
            id={id}
            checked={settings.automatic}
            onCheckedChange={(value) => change("automatic", value)}
          />
        )}
      </Field>
      <Field
        label="Automatically resolve verified fixes"
        hint="Requires Automatic reviews and GitHub App Contents: Read and write, approved on the installation. Uses the remaining review allowance; uncertain findings stay open."
      >
        {(id) => (
          <Switch
            id={id}
            checked={settings.auto_resolve ?? false}
            disabled={!settings.automatic}
            onCheckedChange={(value) => change("auto_resolve", value)}
          />
        )}
      </Field>
      <div className="grid grid-cols-2 gap-4">
        <Field label="Review effort">
          {(id) => (
            <NativeSelect
              id={id}
              value={settings.effort}
              onChange={(e) =>
                change("effort", e.target.value as "low" | "medium" | "high")
              }
            >
              <option>low</option>
              <option>medium</option>
              <option>high</option>
            </NativeSelect>
          )}
        </Field>
        <Field label="Language">
          {(id) => (
            <Input
              id={id}
              required
              value={settings.language}
              onChange={(e) => change("language", e.target.value)}
            />
          )}
        </Field>
      </div>
      <ModelPicker
        key={connection}
        label="Model override (optional)"
        value={settings.model}
        onChange={(value) => change("model", value)}
        path={connection ? `/connections/${connection}/models?repository_id=${repo.id}` : undefined}
        enabled={!!connection}
        emptyLabel={selectedConnection?.model || "Use connection model"}
        hint="Choose an AI connection to load its models."
      />
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
        personal={personal}
        value={settings.background}
        onChange={(value) => setSettings((current) => ({ ...current, background: value }))}
      />
      <details className="space-y-4" open={showLimits || undefined}>
        <summary className="cursor-pointer text-sm font-medium">
          Review limits and rules
        </summary>
        <div className="grid gap-4 pt-4 sm:grid-cols-2">
          {([
            { key: "timeout_seconds", label: "Timeout (seconds)", min: 60, max: 3600,
              hint: "Applies to reviews and background generation. Unlimited removes the deadline. Provider and connection timeouts still apply." },
            { key: "token_budget", label: "Token budget", min: 1000, max: 5000000,
              hint: "Applies to reviews and background generation. Unlimited removes the token budget. Provider and account limits still apply." },
            { key: "max_files", label: "Maximum files", min: 1, max: undefined,
              hint: "Skips the entire review when reviewable files exceed this limit. Excluded files do not count." },
          ] as const).filter(({ key }) => !personal || key !== "token_budget").map(({ key, label, min, max, hint }) => (
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
          {personal && (
            <Field
              label="Native context budget (bytes)"
              hint="Bounds the code and rules supplied to the official client. Subscription usage limits still apply; this is not an output-token cap."
            >
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min={4096}
                  max={20000000}
                  required
                  value={settings.delegated_context_bytes ?? 800000}
                  onChange={(event) =>
                    change(
                      "delegated_context_bytes",
                      Number(event.target.value),
                    )
                  }
                />
              )}
            </Field>
          )}
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
                id={id}
                type="number"
                min={1}
                max={8}
                required
                disabled={personal}
                value={personal ? 1 : settings.concurrency}
                onChange={(e) => change("concurrency", Number(e.target.value))}
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
      </details>
      <Button type="submit" disabled={action.isPending}>
        {action.isPending ? "Saving…" : "Save configuration"}
      </Button>
    </form>
  );
}

function ConnectionForm({
  connection,
  providers,
  canCreateApi,
  onSaved,
  onSavingChange,
}: {
  connection?: Connection;
  providers: Provider[];
  canCreateApi: boolean;
  onSaved: (connection: Connection) => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const [kind, setKind] = useState(
    connection?.kind ?? (canCreateApi ? "api" : "chatgpt"),
  );
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
  const [options, setOptions] = useState(
    JSON.stringify(connection?.options ?? {}, null, 2),
  );
  const [error, setError] = useState<unknown>();
  const action = useAction<Connection>();
  // Draft credentials stay in component memory, never in query keys or browser storage.
  const context = JSON.stringify([kind, provider, protocol, url, key, headers, options]);
  const initialContext = useRef(context);
  function apiBody() {
    return {
      kind, provider, protocol, base_url: url,
      ...(key ? { api_key: key } : {}),
      ...(headers.trim() ? { extra_headers: parseObject(headers, "Headers") } : {}),
      options: parseObject(options, "Options"),
    };
  }
  function choose(value: string) {
    const preset = providers.find((p) => p.name === value);
    setProvider(value);
    if (preset) {
      setProtocol(preset.protocol);
      setUrl(preset.baseUrl);
    }
  }
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
              ? { name, kind, model }
              : { name, model, ...apiBody() };
          const saved = await action.mutateAsync({
            path: connection ? `/connections/${connection.id}` : "/connections",
            method: connection ? "PUT" : "POST",
            body,
          });
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
            <NativeSelect
              id={id}
              value={kind}
              disabled={!!connection}
              onChange={(e) => setKind(e.target.value)}
            >
              {canCreateApi && <option value="api">Shared API connection</option>}
              <option value="chatgpt">Personal ChatGPT subscription</option>
              <option value="claude_code">
                Personal Claude Code subscription
              </option>
            </NativeSelect>
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
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Provider">
              {(id) => (
                <NativeSelect
                  id={id}
                  value={provider}
                  onChange={(e) => choose(e.target.value)}
                >
                  {providers.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.displayName}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            <Field label="API protocol">
              {(id) => (
                <NativeSelect
                  id={id}
                  value={protocol}
                  onChange={(e) => setProtocol(e.target.value)}
                >
                  {protocols.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
          </div>
          {protocol !== "anthropic-bedrock" && (
            <Field label="API base URL">
              {(id) => (
                <Input
                  id={id}
                  required
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              )}
            </Field>
          )}
          <Field
            label={
              connection?.has_key ? "Replace API key (optional)" : "API key"
            }
            hint={
              protocol === "anthropic-bedrock"
                ? "Bedrock can use the worker’s AWS credentials."
                : connection?.has_key
                  ? "Leave blank to keep the stored key. Keys are encrypted and never returned to the browser."
                  : "For a trusted local endpoint without authentication, enter a placeholder."
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
      <section className="space-y-4 border-t pt-5" aria-label="Default model">
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
        {kind !== "api" && (
          <p className="text-sm text-muted-foreground">
            Save your personal connection, then complete sign-in with the official
            client. You control which repositories may use your subscription
            automatically.
          </p>
        )}
      </section>
      {kind === "api" && (
        <details className="border-t pt-5">
          <summary className="cursor-pointer text-sm font-medium">
            Advanced provider settings
          </summary>
          <div className="mt-4 space-y-4">
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
      <Button disabled={action.isPending} type="submit">
        {action.isPending ? "Saving…" : "Save connection"}
      </Button>
    </form>
  );
}

export function SettingsPage({ user }: { user: Schema["UserOut"] }) {
  const isAdmin = user.role === "admin";
  const [params] = useSearchParams();
  const tab = isAdmin && ["connections", "github", "team"].includes(params.get("tab") ?? "")
    ? params.get("tab")! : "connections";
  const query = useApi<Instance>("/settings", true, isAdmin);
  const connections = useApi<Connection[]>("/connections", true);
  const providers = useApi<Provider[]>("/providers");
  const action = useAction();
  const memberAction = useAction();
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const [savingConnection, setSavingConnection] = useState(false);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const [personal, setPersonal] = useState<Connection | null>(null);
  const [member, setMember] = useState<Instance["members"][number] | "new" | null>(null);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  const memberSubmitLabel = member === "new" ? "Add or update member" : "Save role";
  const returnFocus = useRef<HTMLElement | null>(null);
  const connectionHeading = useRef<HTMLHeadingElement>(null);
  const memberHeading = useRef<HTMLHeadingElement>(null);
  const instance = query.data;
  const groups = [
    { title: "Shared API connections", personal: false, items: connections.data?.filter((item) => (item.kind ?? "api") === "api") ?? [] },
    { title: "Your subscriptions", personal: true, items: connections.data?.filter((item) => (item.kind ?? "api") !== "api") ?? [] },
  ].filter((group) => group.items.length);

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
        {isAdmin ? "Manage AI connections, GitHub installations, and team access." : "Manage your AI connections and personal subscriptions."}
      </p>
    </header>
    <div className={isAdmin ? "grid min-w-0 gap-6 lg:grid-cols-[176px_minmax(0,1fr)]" : "min-w-0"}>
      {isAdmin && <nav aria-label="Settings sections" className="flex flex-wrap self-start gap-1 lg:flex-col">
        {[["connections", "AI connections"], ["github", "GitHub"], ["team", "Team"]].map(([value, label]) => (
          <Link key={value} to={`/settings?tab=${value}`} aria-current={tab === value ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${tab === value ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
            {label}
          </Link>
        ))}
      </nav>}
      <div className="settings-content min-w-0 space-y-6">
        <ErrorNotice error={query.error} />
        {tab === "connections" && <section aria-labelledby="connections-heading" className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 id="connections-heading" ref={connectionHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">AI connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">Connections and default models for manual and automatic reviews.</p>
            </div>
            <Button disabled={!providers.data} onClick={(event) => {
              returnFocus.current = event.currentTarget;
              setEditing("new");
            }}><Plus className="size-4" />Add connection</Button>
          </div>
          <ErrorNotice error={connections.error || providers.error || (!deleting && action.error)} />
          {connections.isPending && <Loading />}
          {connections.isSuccess && !connections.data.length && (
            <Empty title="Choose your review model">Connect an API or your personal subscription, then choose it for reviews.</Empty>
          )}
          {!connections.isPending && groups.length > 0 && <div className="space-y-6">
            {groups.map((group) => <section key={group.title} aria-label={group.title}>
              <h3 className="mb-2.5 ml-0.5 flex items-baseline gap-2 text-sm font-semibold">
                {group.title}<span className="text-xs font-normal tabular-nums text-muted-foreground">{group.items.length}</span>
              </h3>
              <div className="overflow-hidden rounded-lg border bg-card">
                <div aria-hidden="true" className="settings-record settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
                  <span>Connection</span><span>Default model</span><span>{group.personal ? "Account status" : "Last test"}</span><span />
                </div>
                {group.items.map((connection) => {
                  const personalConnection = (connection.kind ?? "api") !== "api";
                  let kindLabel = `${connection.provider} · ${connection.protocol}`;
                  if (connection.kind === "chatgpt") kindLabel = "ChatGPT subscription";
                  else if (connection.kind === "claude_code") kindLabel = "Claude Code subscription";
                  else if (personalConnection) kindLabel = "Personal subscription";
                  const canManage = personalConnection ? connection.owner_id === user.id : isAdmin;
                  const Icon = personalConnection ? UserRound : Plug;
                  return <article key={connection.id} aria-label={connection.name}
                    className="settings-record border-b px-4 py-4 text-sm last:border-b-0 sm:px-5">
                    <div className="settings-identity flex min-w-0 items-start gap-3">
                      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                      <div className="min-w-0">
                        <h4 className="break-words font-medium">{connection.name}</h4>
                        <p className="mt-1 break-words text-xs text-muted-foreground">
                          {kindLabel}
                        </p>
                      </div>
                    </div>
                    <div className="min-w-0">
                      <span className="settings-cell-label mb-1 text-xs text-muted-foreground">Default model</span>
                      <p className="break-words text-xs leading-relaxed"><span className="sr-only">Default model: </span>{connection.model || "Choose a model"}</p>
                    </div>
                    <div className="min-w-0 text-xs text-muted-foreground">
                      <span className="settings-cell-label mb-1">{personalConnection ? "Account status" : "Last test"}</span>
                      {personalConnection && <Status value={connection.auth_status} />}
                      {!personalConnection && connection.last_test && <>
                        <details className="group/test">
                          <summary className="flex cursor-pointer list-none items-start gap-1 text-foreground [&::-webkit-details-marker]:hidden">
                            <span className="line-clamp-2 break-words group-open/test:line-clamp-none"><span className="sr-only">Last test: </span>{connection.last_test}</span>
                            <ChevronDown aria-hidden="true" className="mt-0.5 size-3 shrink-0 group-open/test:rotate-180" />
                          </summary>
                        </details>
                        {connection.tested_at && <time dateTime={connection.tested_at} className="mt-1 block tabular-nums">{date(connection.tested_at)}</time>}
                      </>}
                      {!personalConnection && !connection.last_test && <span>Not tested</span>}
                    </div>
                    <div className="settings-actions flex items-center justify-end gap-1">
                      {canManage && <>
                        {personalConnection ? <Button size="sm" variant="outline" onClick={(event) => {
                          returnFocus.current = event.currentTarget;
                          setPersonal(connection);
                        }}>Manage<span className="sr-only"> account</span></Button> : <Button size="sm" variant="outline"
                          disabled={action.isPending || connection.last_test === "Testing connection…"}
                          onClick={() => { action.reset(); action.mutate({ path: `/connections/${connection.id}/test` }); }}>Test</Button>}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="size-8" disabled={action.isPending} aria-label={`Actions for ${connection.name}`}
                              onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                              onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                              <MoreHorizontal className="size-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (editing || deleting) event.preventDefault(); }}>
                            <DropdownMenuItem onSelect={() => setEditing(connection)}>Edit</DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" onSelect={() => { action.reset(); setDeleting(connection); }}>Delete</DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>}
                    </div>
                  </article>;
                })}
              </div>
            </section>)}
          </div>}
          <p className="text-xs leading-relaxed text-muted-foreground">
            API connections are shared. Personal subscriptions belong to you; automatic reviews require your explicit authorization per repository.
          </p>
        </section>}
        {tab !== "connections" && query.isPending && <Loading />}
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
                  <p className="flex items-start gap-2 font-medium"><Github aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" /><span className="break-words">{instance.github_app.slug}</span></p>
                  <p className="mt-1 text-xs text-muted-foreground">Comments are published as this GitHub App.</p>
                </dd>
              </div>
              <div className="grid gap-2 px-5 py-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <dt className="text-xs text-muted-foreground">Webhook endpoint</dt>
                <dd className="min-w-0 break-all font-mono text-xs leading-relaxed">{instance.webhook_url ?? `${instance.public_url}/api/v1/webhooks/github`}</dd>
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
          {!member && <ErrorNotice error={memberAction.error} />}
          <p className="text-xs tabular-nums text-muted-foreground">{instance.members.length} {instance.members.length === 1 ? "member" : "members"}</p>
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="settings-member-row settings-member-heading border-b bg-muted/30 px-4 py-3 text-xs font-medium text-muted-foreground sm:px-5">
              <span>Member</span><span>Role</span><span />
            </div>
            {instance.members.map((item) => <div key={item.id} className="settings-member-row border-b px-4 py-4 last:border-b-0 sm:px-5">
              <div className="settings-member-identity flex min-w-0 items-center">
                <Avatar className="settings-member-avatar rounded-md" aria-hidden="true"><AvatarFallback className="rounded-md text-xs">{item.login.slice(0, 2).toUpperCase()}</AvatarFallback></Avatar>
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
                <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (member) event.preventDefault(); }}>
                  <DropdownMenuItem onSelect={() => openMember(item)}>Edit role</DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem variant="destructive" onSelect={() => {
                    memberAction.reset();
                    memberAction.mutate({ path: `/members/${item.id}`, method: "DELETE" }, {
                      onSuccess: () => memberHeading.current?.focus(),
                    });
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
        {!!instance?.failed_jobs.length && <details className="border-t pt-5">
          <summary className="cursor-pointer text-sm text-muted-foreground">Recent background errors ({instance.failed_jobs.length})</summary>
          <ul className="mt-4 space-y-3">
            {instance.failed_jobs.map((job) => <li key={job.id} className="rounded-md border p-3 text-xs">
              <span className="font-semibold">{job.kind}</span><p className="mt-1 break-words text-muted-foreground">{job.error}</p>
            </li>)}
          </ul>
        </details>}
      </div>
    </div>
    <Dialog open={!!editing} onOpenChange={(open) => { if (!open && !savingConnection) setEditing(null); }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl" onCloseAutoFocus={(event) => {
        event.preventDefault();
        if (!personal) returnFocus.current?.focus();
      }}>
        <DialogHeader>
          <DialogTitle>{editing === "new" ? "Add AI connection" : "Edit AI connection"}</DialogTitle>
          <DialogDescription>Credentials are encrypted on this instance.</DialogDescription>
        </DialogHeader>
        {editing && providers.data && <ConnectionForm key={editing === "new" ? "new" : editing.id}
          connection={editing === "new" ? undefined : editing} providers={providers.data} canCreateApi={isAdmin}
          onSavingChange={setSavingConnection}
          onSaved={(saved) => { setEditing(null); if (saved.kind !== "api" && !saved.model) setPersonal(saved); }} />}
      </DialogContent>
    </Dialog>
    <Dialog open={!!personal} onOpenChange={(open) => { if (!open) setPersonal(null); }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>{personal?.name}</DialogTitle>
          <DialogDescription>Connect your official account and authorize automatic reviews.</DialogDescription>
        </DialogHeader>
        {personal && <PersonalConnection key={personal.id} connection={personal} />}
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
            await memberAction.mutateAsync({ path: "/members", body: { login, role } });
            setMember(null);
          } catch { /* Keep the draft and display the action error. */ }
        }}>
          <ErrorNotice error={memberAction.error} />
          <Field label="GitHub username">{(id) => <Input id={id} required readOnly={member !== "new"} value={login}
            onChange={(event) => setLogin(event.target.value)} placeholder="username" />}</Field>
          <Field label="Role">{(id) => <NativeSelect id={id} value={role} onChange={(event) => setRole(event.target.value)}>
            <option value="member">Member</option><option value="admin">Administrator</option>
          </NativeSelect>}</Field>
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="outline" disabled={memberAction.isPending} onClick={() => { setMember(null); memberAction.reset(); }}>Cancel</Button>
            <Button type="submit" disabled={memberAction.isPending}>{memberAction.isPending ? "Saving…" : memberSubmitLabel}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </>;
}
