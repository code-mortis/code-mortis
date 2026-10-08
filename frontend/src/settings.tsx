import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
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
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Textarea } from "./components/ui/textarea";
import { Switch } from "./components/ui/switch";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "./components/ui/tabs";
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
  ExternalLink,
  Field,
  Loading,
  NativeSelect,
  PageTitle,
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
      <Field
        label="Project background"
        hint="Context that helps reviewers understand the codebase."
      >
        {(id) => (
          <Textarea
            id={id}
            rows={4}
            value={settings.background}
            onChange={(e) => change("background", e.target.value)}
          />
        )}
      </Field>
      <details className="space-y-4" open={showLimits || undefined}>
        <summary className="cursor-pointer text-sm font-medium">
          Review limits and rules
        </summary>
        <div className="grid gap-4 pt-4 sm:grid-cols-2">
          {([
            { key: "timeout_seconds", label: "Timeout (seconds)", min: 60, max: 3600,
              hint: "Unlimited removes the review deadline. Provider and connection timeouts still apply." },
            { key: "token_budget", label: "Token budget", min: 1000, max: 5000000,
              hint: "Unlimited removes the review token budget. Provider and account limits still apply." },
            { key: "max_files", label: "Maximum files", min: 1, max: undefined,
              hint: "Skips the entire review when reviewable files exceed this limit. Excluded files do not count." },
          ] as const).filter(({ key }) => !personal || key !== "token_budget").map(({ key, label, min, max, hint }) => (
            <Field key={key} label={label} hint={hint}>
              {(id) => (
                <div className="space-y-2">
                  <Input
                    id={id}
                    type="number"
                    min={min}
                    max={max}
                    required
                    disabled={settings[key] === null}
                    value={settings[key] ?? ""}
                    onChange={(e) => {
                      const value = e.target.valueAsNumber;
                      if (!Number.isFinite(value)) return;
                      if (e.target.validity.valid) limitedValues.current[key] = value;
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
}: {
  connection?: Connection;
  providers: Provider[];
  canCreateApi: boolean;
  onSaved: (connection: Connection) => void;
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
        }
      }}
    >
      <ErrorNotice error={error} />
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
      {kind === "api" && (
        <>
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
        </>
      )}
      {kind === "api" && (
        <>
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
          <details>
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
        </>
      )}
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
      <Button disabled={action.isPending} type="submit">
        {action.isPending ? "Saving…" : "Save connection"}
      </Button>
    </form>
  );
}

export function SettingsPage({ user }: { user: Schema["UserOut"] }) {
  const isAdmin = user.role === "admin";
  const [params, setParams] = useSearchParams();
  const tab =
    isAdmin &&
    ["connections", "github", "team"].includes(params.get("tab") ?? "")
      ? params.get("tab")!
      : "connections";
  const query = useApi<Instance>("/settings", true, isAdmin);
  const connections = useApi<Connection[]>("/connections", true);
  const providers = useApi<Provider[]>("/providers");
  const available = useApi<{ id: number; account: string }[]>(
    "/installations/available",
    false,
    isAdmin && tab === "github",
  );
  const action = useAction();
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const [personal, setPersonal] = useState<Connection | null>(null);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  if (isAdmin && query.isPending) return <Loading />;
  if (isAdmin && !query.data) return <ErrorNotice error={query.error} />;
  const instance = query.data ?? {
    github_app: { id: "", slug: "", install_url: "" },
    installations: [],
    members: [],
    failed_jobs: [],
    public_url: "",
  };
  return (
    <>
      <PageTitle title="Settings">
        Manage review connections and your personal subscriptions.
      </PageTitle>
      <ErrorNotice error={query.error || action.error} />
      <Tabs value={tab} onValueChange={(value) => setParams({ tab: value })}>
        <TabsList className="mb-8">
          <TabsTrigger value="connections">AI connections</TabsTrigger>
          {isAdmin && (
            <>
              <TabsTrigger value="github">GitHub</TabsTrigger>
              <TabsTrigger value="team">Team</TabsTrigger>
            </>
          )}
        </TabsList>
        <TabsContent value="connections">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">Review connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Connections and default models for manual and automatic reviews.
              </p>
            </div>
            <Button
              onClick={() => setEditing("new")}
              disabled={!providers.data}
            >
              <Plus className="size-4" />
              Add connection
            </Button>
          </div>
          <ErrorNotice error={connections.error || providers.error} />
          {connections.isPending ? (
            <Loading />
          ) : !connections.data?.length ? (
            <Empty title="Choose your review model">
              Connect an API or your personal subscription, then choose it for
              reviews.
            </Empty>
          ) : (
            <div className="divide-y rounded-lg border bg-card">
              {connections.data.map((connection) => (
                <article
                  key={connection.id}
                  className="flex flex-col items-start gap-4 p-5 sm:flex-row"
                >
                  <div className="min-w-0 w-full flex-1">
                    <h3 className="text-sm font-semibold">{connection.name}</h3>
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      {connection.provider} · Default model: {connection.model || "Choose a model"} ·{" "}
                      {(connection.kind ?? "api") === "api"
                        ? connection.protocol
                        : `Personal · ${connection.auth_status}`}
                    </p>
                    {connection.last_test && (
                      <p
                        role="status"
                        className="mt-3 text-xs text-muted-foreground"
                      >
                        {connection.last_test}
                        {connection.tested_at &&
                          ` · ${date(connection.tested_at)}`}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(connection.kind ?? "api") !== "api" ? (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setPersonal(connection)}
                      >
                        Manage account
                      </Button>
                    ) : (
                      isAdmin && (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={
                            action.isPending ||
                            connection.last_test === "Testing connection…"
                          }
                          onClick={() =>
                            action.mutate({
                              path: `/connections/${connection.id}/test`,
                            })
                          }
                        >
                          Test
                        </Button>
                      )
                    )}
                    {((isAdmin && (connection.kind ?? "api") === "api") ||
                      connection.owner_id === user.id) && (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setEditing(connection)}
                        >
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleting(connection)}
                        >
                          Delete
                        </Button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
            API connections are shared. Personal subscriptions belong to you;
            automatic reviews require your explicit authorization per
            repository.
          </p>
        </TabsContent>
        <TabsContent value="github">
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">{instance.github_app.slug}</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Comments are published as this GitHub App.
              </p>
            </div>
            <ExternalLink href={instance.github_app.install_url}>
              Install on GitHub
            </ExternalLink>
          </div>
          <div className="mb-8 rounded-lg border bg-card p-5">
            <h3 className="mb-3 text-sm font-semibold">
              Connected installations
            </h3>
            {instance.installations.length ? (
              <ul className="divide-y">
                {instance.installations.map((item) => (
                  <li
                    key={item.id}
                    className="flex justify-between py-3 text-sm"
                  >
                    <span>{item.account}</span>
                    <span className="text-muted-foreground">
                      {item.active ? "Active" : "Inactive"}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                Install the app on GitHub, then connect the installation below.
              </p>
            )}
          </div>
          <div className="mb-4 flex items-center gap-3">
            <h3 className="font-semibold">Available installations</h3>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => available.refetch()}
            >
              Refresh
            </Button>
          </div>
          <ErrorNotice error={available.error} />
          {available.isPending ? (
            <Loading />
          ) : available.data?.length ? (
            <div className="divide-y rounded-lg border bg-card">
              {available.data.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between p-4 text-sm"
                >
                  <span>{item.account}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate({ path: `/installations/${item.id}` })
                    }
                  >
                    {instance.installations.some(
                      (i) => i.id === item.id && i.active,
                    )
                      ? "Resync repositories"
                      : "Connect"}
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No installations available">
              Install the app using the button above, then refresh this list.
            </Empty>
          )}
          <p className="mt-6 break-words text-xs text-muted-foreground">
            Webhook endpoint:{" "}
            {instance.webhook_url ??
              `${instance.public_url}/api/v1/webhooks/github`}
          </p>
        </TabsContent>
        <TabsContent value="team">
          <h2 className="font-semibold">Allowed team members</h2>
          <p className="mb-6 mt-2 text-sm text-muted-foreground">
            Each person signs in with GitHub and only sees repositories they can
            access there.
          </p>
          <form
            className="mb-7 flex flex-wrap items-end gap-3"
            onSubmit={async (e) => {
              e.preventDefault();
              const result = await action
                .mutateAsync({ path: "/members", body: { login, role } })
                .then(() => true)
                .catch(() => false);
              if (result) setLogin("");
            }}
          >
            <div className="min-w-48 flex-1">
              <Field label="GitHub username">
                {(id) => (
                  <Input
                    id={id}
                    required
                    value={login}
                    onChange={(e) => setLogin(e.target.value)}
                    placeholder="username"
                  />
                )}
              </Field>
            </div>
            <Field label="Role">
              {(id) => (
                <NativeSelect
                  id={id}
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                >
                  <option value="member">Member</option>
                  <option value="admin">Administrator</option>
                </NativeSelect>
              )}
            </Field>
            <Button disabled={action.isPending}>Add or update member</Button>
          </form>
          <div className="divide-y rounded-lg border bg-card">
            {instance.members.map((member) => (
              <div key={member.id} className="flex items-center gap-3 p-4">
                <span className="flex-1 text-sm font-medium">
                  @{member.login}
                </span>
                <span className="text-xs capitalize text-muted-foreground">
                  {member.role}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({
                      path: `/members/${member.id}`,
                      method: "DELETE",
                    })
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>
      {!!instance.failed_jobs.length && (
        <details className="mt-10 border-t pt-5">
          <summary className="cursor-pointer text-sm text-muted-foreground">
            Recent background errors ({instance.failed_jobs.length})
          </summary>
          <ul className="mt-4 space-y-3">
            {instance.failed_jobs.map((job) => (
              <li key={job.id} className="rounded-md border p-3 text-xs">
                <span className="font-semibold">{job.kind}</span>
                <p className="mt-1 text-muted-foreground">{job.error}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      <Dialog
        open={!!editing}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>
              {editing === "new" ? "Add AI connection" : "Edit AI connection"}
            </DialogTitle>
            <DialogDescription>
              Credentials are encrypted on this instance.
            </DialogDescription>
          </DialogHeader>
          {editing && providers.data && (
            <ConnectionForm
              key={editing === "new" ? "new" : editing.id}
              connection={editing === "new" ? undefined : editing}
              providers={providers.data}
              canCreateApi={isAdmin}
              onSaved={(saved) => {
                setEditing(null);
                if (saved.kind !== "api" && !saved.model) setPersonal(saved);
              }}
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!personal}
        onOpenChange={(open) => {
          if (!open) setPersonal(null);
        }}
      >
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{personal?.name}</DialogTitle>
            <DialogDescription>
              Connect your official account and authorize automatic reviews.
            </DialogDescription>
          </DialogHeader>
          {personal && (
            <PersonalConnection key={personal.id} connection={personal} />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {deleting?.name}?</DialogTitle>
            <DialogDescription>
              Repositories must stop using this connection before it can be
              deleted.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={action.error} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={action.isPending}
              onClick={async () => {
                if (deleting)
                  await action
                    .mutateAsync({
                      path: `/connections/${deleting.id}`,
                      method: "DELETE",
                    })
                    .then(() => setDeleting(null))
                    .catch(() => {});
              }}
            >
              Delete connection
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
