import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { useAction, useApi, type Connection, type Repository } from "./api";
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
}: {
  repository: Repository;
  onSaved: () => void;
}) {
  const connections = useApi<Connection[]>("/connections");
  const action = useAction();
  const [enabled, setEnabled] = useState(repo.enabled);
  const [connection, setConnection] = useState(repo.connection_id ?? "");
  const [settings, setSettings] = useState(repo.settings);
  const [rules, setRules] = useState(
    repo.settings.rules ? JSON.stringify(repo.settings.rules, null, 2) : "",
  );
  const [error, setError] = useState<unknown>();
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
            onChange={(e) => setConnection(e.target.value)}
          >
            <option value="">Choose a connection</option>
            {connections.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} · {item.model}
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
      <Field label="Model override (optional)">
        {(id) => (
          <Input
            id={id}
            value={settings.model}
            onChange={(e) => change("model", e.target.value)}
            placeholder="Use connection model"
          />
        )}
      </Field>
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
      <details className="space-y-4">
        <summary className="cursor-pointer text-sm font-medium">
          Review limits and rules
        </summary>
        <div className="grid grid-cols-2 gap-4 pt-4">
          <Field label="Timeout (seconds)">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={60}
                max={3600}
                required
                value={settings.timeout_seconds}
                onChange={(e) =>
                  change("timeout_seconds", Number(e.target.value))
                }
              />
            )}
          </Field>
          <Field label="Token budget">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={1000}
                max={5000000}
                required
                value={settings.token_budget}
                onChange={(e) => change("token_budget", Number(e.target.value))}
              />
            )}
          </Field>
          <Field label="Concurrency">
            {(id) => (
              <Input
                id={id}
                type="number"
                min={1}
                max={8}
                required
                value={settings.concurrency}
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
  onSaved,
}: {
  connection?: Connection;
  providers: Provider[];
  onSaved: () => void;
}) {
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
  const action = useAction();
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
          const body = {
            name,
            provider,
            protocol,
            model,
            base_url: url,
            ...(key ? { api_key: key } : {}),
            ...(headers.trim()
              ? { extra_headers: parseObject(headers, "Headers") }
              : {}),
            options: parseObject(options, "Options"),
          };
          await action.mutateAsync({
            path: connection ? `/connections/${connection.id}` : "/connections",
            method: connection ? "PUT" : "POST",
            body,
          });
          onSaved();
        } catch (e) {
          setError(e);
        }
      }}
    >
      <ErrorNotice error={error} />
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
        label="Model identifier"
        hint="Use a model available to your account that supports tool calls."
      >
        {(id) => (
          <Input
            id={id}
            required
            maxLength={150}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="Provider model ID"
          />
        )}
      </Field>
      <Field
        label={connection?.has_key ? "Replace API key (optional)" : "API key"}
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
            required={!connection?.has_key && protocol !== "anthropic-bedrock"}
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
      <Button disabled={action.isPending} type="submit">
        {action.isPending ? "Saving…" : "Save connection"}
      </Button>
    </form>
  );
}

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = ["connections", "github", "team"].includes(
    params.get("tab") ?? "",
  )
    ? params.get("tab")!
    : "connections";
  const query = useApi<Instance>("/settings", true);
  const connections = useApi<Connection[]>("/connections", true);
  const providers = useApi<Provider[]>("/providers");
  const available = useApi<{ id: number; account: string }[]>(
    "/installations/available",
    false,
    tab === "github",
  );
  const action = useAction();
  const [editing, setEditing] = useState<Connection | "new" | null>(null);
  const [deleting, setDeleting] = useState<Connection | null>(null);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  const instance = query.data;
  return (
    <>
      <PageTitle title="Settings">
        Manage shared AI connections, repository access, and your team.
      </PageTitle>
      <ErrorNotice error={query.error || action.error} />
      <Tabs value={tab} onValueChange={(value) => setParams({ tab: value })}>
        <TabsList className="mb-8">
          <TabsTrigger value="connections">AI connections</TabsTrigger>
          <TabsTrigger value="github">GitHub</TabsTrigger>
          <TabsTrigger value="team">Team</TabsTrigger>
        </TabsList>
        <TabsContent value="connections">
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-semibold">Shared connections</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Models available for manual and automatic reviews.
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
              Add an API connection, test it, then assign it to a repository.
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
                      {connection.provider} · {connection.model} ·{" "}
                      {connection.protocol}
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
                  <div className="flex gap-1">
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
                  </div>
                </article>
              ))}
            </div>
          )}
          <p className="mt-5 text-xs leading-relaxed text-muted-foreground">
            API connections are shared by this instance. Personal ChatGPT and
            Claude Code sign-in are planned for a later release.
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
            Webhook endpoint: {instance.public_url}/api/v1/webhooks/github
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
              onSaved={() => setEditing(null)}
            />
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
