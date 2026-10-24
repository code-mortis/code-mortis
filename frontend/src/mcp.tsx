import { useState, type ReactNode } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { api, useAction, useApi, type Repository } from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Textarea } from "./components/ui/textarea";
import { Switch } from "./components/ui/switch";
import { Badge } from "./components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "./components/ui/dialog";
import { date, Empty, ErrorNotice, Field, Loading } from "./components/common";

export type McpSettings = {
  reviews_enabled: boolean;
  security_enabled: boolean;
  management_enabled: boolean;
  allowed_client_domains: string[];
  public_url: string;
  can_manage: boolean;
};

export type McpClient = {
  client_id: string;
  client_name: string;
  redirect_uris: string[];
};

export type McpGrant = {
  id: string;
  client_id: string;
  client_name: string;
  domain: "reviews" | "security";
  scopes: string[];
  repository_ids: number[];
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

type McpAuthorizePreview = {
  request_id: string;
  client_name: string;
  client_id: string;
  redirect_uri: string;
  redirect_host?: string;
  domain: "reviews" | "security";
  scopes: { name: string; label: string; description: string }[];
  repositories: { id: number; full_name: string }[];
  expires_at: string;
};

type McpApproval = {
  id: string;
  tool: string;
  domain: string;
  summary: string;
  arguments: Record<string, unknown>;
  expires_at: string;
  approved_at: string | null;
  consumed_at: string | null;
  denied_at: string | null;
};

function ToggleRow({
  id, label, hint, checked, disabled, onChange, children,
}: {
  id: string; label: string; hint?: string; checked: boolean; disabled?: boolean; onChange: (value: boolean) => void;
  children?: ReactNode;
}) {
  return (
    <div className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <label htmlFor={id} className="text-sm font-medium">{label}</label>
          {hint && <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}
        </div>
        <Switch id={id} aria-describedby={`${id}-hint`} checked={checked} disabled={disabled} onCheckedChange={onChange} className="mt-0.5 shrink-0" />
      </div>
      {children && <div className="mt-2">{children}</div>}
    </div>
  );
}

function ConnectionInstructions({ domain, publicUrl, enabled }: {
  domain: "reviews" | "security"; publicUrl: string; enabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<"endpoint" | "prompt" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const label = domain === "reviews" ? "Reviews" : "Security";
  const description = domain === "reviews"
    ? "Read findings, resolve or reopen them, and start or stop reviews."
    : "Read and triage findings, manage GitHub issues, and run or stop scans.";
  const endpoint = `${publicUrl.replace(/\/+$/, "")}/mcp/${domain}`;
  const name = `codemortis-${domain}`;
  const quotedEndpoint = `'${endpoint.replaceAll("'", "'\\''")}'`;
  const prompt = `Connect this AI client to the Code Mortis ${label} MCP server.

Server URL: ${endpoint}
Connection name: ${name}
OAuth scopes: ${domain}:read, ${domain}:write, ${domain}:run${domain === "security" ? ", security:issues" : ""}

Detect which client you are running in and configure this remote Streamable HTTP MCP server in its user settings. Preserve existing MCP servers and unrelated settings. Use OAuth with PKCE and dynamic client registration (DCR). If this client cannot use that setup, explain what it supports instead of claiming it is connected.

For Codex CLI, use:
codex mcp add ${name} --url ${quotedEndpoint} --oauth-resource ${quotedEndpoint} --oauth-client-registration dcr

If that Codex connection already exists, check its URL and use:
codex mcp login ${name} --oauth-client-registration dcr --scopes ${domain}:read,${domain}:write,${domain}:run${domain === "security" ? ",security:issues" : ""}

Let me complete sign-in and choose repositories and permissions in the browser. Do not enter credentials or grant management permissions on my behalf. The ${label} MCP must be enabled in Code Mortis before authorization; local Codex using the command above does not need allowed client domains or manual client registration.

Verify the connection by listing tools or reading visible repositories. Do not start reviews, scans or GitHub writes during setup. Report configuration and completed authorization separately, and tell me if this client needs a new session to load the server.`;
  async function copy(value: string, kind: "endpoint" | "prompt") {
    setError(null);
    setCopied(null);
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      setError(`Could not copy. Select the ${kind} and copy it manually.`);
    }
  }
  if (!publicUrl) return <p className="text-sm text-muted-foreground">Ask an administrator to configure the public URL before connecting an agent.</p>;
  return (
    <div className="space-y-3">
      <p id={`mcp-${domain}-hint`} className="text-xs leading-relaxed text-muted-foreground">
        {description}{" "}
        <Dialog open={expanded} onOpenChange={(open) => { setExpanded(open); setError(null); setCopied(null); }}>
          <DialogTrigger asChild>
            <Button type="button" variant="link" className="h-auto p-0 align-baseline text-xs font-normal underline" aria-label={`${label} installation instructions`}>Installation instructions</Button>
          </DialogTrigger>
          <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>{label} installation instructions</DialogTitle>
              <DialogDescription>Use this URL in your client or paste the prompt into your coding agent. Complete authorization in your browser.</DialogDescription>
            </DialogHeader>
            {!enabled && <p className="text-xs text-muted-foreground">{label} MCP is off. An administrator must enable it and save before you connect.</p>}
            <div className="min-w-0 space-y-2">
              <p className="text-sm font-medium">Server URL</p>
              <div className="flex items-start gap-2">
                <code aria-label={`${label} MCP endpoint`} className="min-w-0 flex-1 select-text break-all font-mono text-xs leading-relaxed">{endpoint}</code>
                <Button type="button" variant="ghost" size="icon" className="-my-1.5 size-8 shrink-0 text-muted-foreground" aria-label={`Copy ${label} endpoint`} title={copied === "endpoint" ? "Copied" : "Copy endpoint"} onClick={() => void copy(endpoint, "endpoint")}>
                  {copied === "endpoint" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
                </Button>
              </div>
            </div>
            <div className="group/installation relative min-w-0">
              <Textarea aria-label={`${label} installation prompt text`} readOnly value={prompt} className="h-64 resize-y pr-14 font-mono text-xs leading-relaxed sm:pr-12 md:text-xs" />
              <Button type="button" variant="ghost" size="icon" aria-label={`Copy ${label} installation prompt`} title={copied === "prompt" ? "Copied" : "Copy prompt"} className="absolute right-1 top-1 size-11 text-muted-foreground opacity-0 transition-opacity group-hover/installation:opacity-100 group-focus-within/installation:opacity-100 sm:size-8 [@media(hover:none)]:opacity-100" onClick={() => void copy(prompt, "prompt")}>
                {copied === "prompt" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
              </Button>
            </div>
            {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
            {copied && <p role="status" className="sr-only">{label} {copied === "endpoint" ? "endpoint" : "installation prompt"} copied.</p>}
          </DialogContent>
        </Dialog>
      </p>
    </div>
  );
}

function AdminSettings({ settings, onSaved }: { settings: McpSettings; onSaved: () => void }) {
  const action = useAction<McpSettings>();
  const [reviews, setReviews] = useState(settings.reviews_enabled);
  const [security, setSecurity] = useState(settings.security_enabled);
  const [management, setManagement] = useState(settings.management_enabled);
  const [domains, setDomains] = useState(settings.allowed_client_domains.join("\n"));
  const [error, setError] = useState<unknown>();
  const dirty = reviews !== settings.reviews_enabled || security !== settings.security_enabled
    || management !== settings.management_enabled || domains !== settings.allowed_client_domains.join("\n");
  return (
    <form
      aria-label="MCP instance settings"
      className="space-y-4"
      onSubmit={async (event) => {
        event.preventDefault();
        setError(undefined);
        try {
          await action.mutateAsync({
            path: "/mcp/settings",
            method: "PUT",
            body: {
              reviews_enabled: reviews,
              security_enabled: security,
              management_enabled: management,
              allowed_client_domains: domains.split(/[\n,]/).map((d) => d.trim()).filter(Boolean),
            },
          });
          toast.success("MCP settings saved.");
          onSaved();
        } catch (e) {
          setError(e);
        }
      }}
    >
      <div className="divide-y rounded-lg border bg-card">
        <ToggleRow id="mcp-reviews" label="Reviews MCP" checked={reviews} onChange={setReviews}>
          <ConnectionInstructions domain="reviews" publicUrl={settings.public_url} enabled={settings.reviews_enabled} />
        </ToggleRow>
        <ToggleRow id="mcp-security" label="Security MCP" checked={security} onChange={setSecurity}>
          <ConnectionInstructions domain="security" publicUrl={settings.public_url} enabled={settings.security_enabled} />
        </ToggleRow>
        <ToggleRow id="mcp-management" label="Allow management through MCP" hint="Adds administration tools to Reviews and Security, using the same two endpoints. Agents need separate permission to manage AI connections, members or GitHub installations." checked={management} onChange={setManagement} />
      </div>
      <Field
        label="Allowed client domains"
        hint="Optional. Leave empty for the local Codex commands in the installation instructions. Other clients may need their registration or sign-in hostname, one per line."
      >
        {(id) => (
          <Textarea
            id={id}
            rows={3}
            className="font-mono text-xs"
            value={domains}
            onChange={(e) => setDomains(e.target.value)}
            placeholder={"agent.example.com"}
          />
        )}
      </Field>
      <ErrorNotice error={error ?? action.error} />
      <div className="flex justify-end">
        <Button type="submit" disabled={action.isPending || !dirty}>{action.isPending ? "Saving…" : "Save MCP settings"}</Button>
      </div>
    </form>
  );
}

function ClientRegistration() {
  const clients = useApi<McpClient[]>("/mcp/clients");
  const action = useAction();
  const [clientId, setClientId] = useState("");
  const [clientName, setClientName] = useState("");
  const [redirects, setRedirects] = useState("");
  const [error, setError] = useState<unknown>();
  return (
    <details>
      <summary className="cursor-pointer text-base font-semibold">Pre-registered clients</summary>
      <div className="mt-4 space-y-4">
        <p className="text-sm text-muted-foreground">Manual registration for clients that need a fixed client ID and exact redirect URIs. The Codex installation instructions use automatic registration.</p>
        <ErrorNotice error={clients.error} />
        {clients.isPending ? <Loading /> : clients.data?.length ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="hidden grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-4 border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground sm:grid">
              <span>Client</span><span>Redirect URIs</span><span />
            </div>
            {clients.data.map((client) => (
              <div key={client.client_id} className="grid gap-2 border-b px-5 py-4 text-sm last:border-b-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:gap-4 sm:items-center">
                <div className="min-w-0">
                  <p className="break-words font-medium">{client.client_name}</p>
                  <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{client.client_id}</p>
                </div>
                <ul className="min-w-0 space-y-1">
                  {client.redirect_uris.map((uri) => <li key={uri} className="break-all font-mono text-xs text-muted-foreground">{uri}</li>)}
                </ul>
                <div className="flex sm:justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={action.isPending}
                    onClick={async () => {
                      setError(undefined);
                      try {
                        await action.mutateAsync({ path: `/mcp/clients/${encodeURIComponent(client.client_id)}`, method: "DELETE" });
                        toast.success("Client removed. Its grants were revoked.");
                        void clients.refetch();
                      } catch (e) {
                        setError(e);
                      }
                    }}
                  >
                    Remove
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : !clients.error ? (
          <div className="rounded-lg border border-dashed px-6 py-8 text-center">
            <p className="text-sm text-muted-foreground">No registered clients. Local clients can register automatically when you connect them.</p>
          </div>
        ) : null}
        <form
          aria-label="Pre-register a client"
          className="space-y-4 rounded-lg border bg-card p-5"
          onSubmit={async (event) => {
            event.preventDefault();
            setError(undefined);
            const uris = redirects.split(/[\n,]/).map((u) => u.trim()).filter(Boolean);
            if (!clientId.trim() || !clientName.trim() || !uris.length) {
              setError(new Error("Enter a client ID, a display name, and at least one exact redirect URI."));
              return;
            }
            try {
              await action.mutateAsync({
                path: "/mcp/clients",
                method: "POST",
                body: { client_id: clientId.trim(), client_name: clientName.trim(), redirect_uris: uris },
              });
              toast.success("Client registered.");
              setClientId("");
              setClientName("");
              setRedirects("");
              void clients.refetch();
            } catch (e) {
              setError(e);
            }
          }}
        >
          <ErrorNotice error={error} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Client ID">{(id) => <Input id={id} required value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="my-agent" />}</Field>
            <Field label="Display name">{(id) => <Input id={id} required value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="My agent" />}</Field>
          </div>
          <Field label="Redirect URIs" hint="One per line. Must match the authorization request exactly.">
            {(id) => <Textarea id={id} rows={2} className="font-mono text-xs" value={redirects} onChange={(e) => setRedirects(e.target.value)} placeholder="https://agent.example.com/callback" />}
          </Field>
          <div className="flex justify-end">
            <Button type="submit" disabled={action.isPending}>{action.isPending ? "Registering…" : "Register client"}</Button>
          </div>
        </form>
      </div>
    </details>
  );
}

function Grants() {
  const grants = useApi<McpGrant[]>("/mcp/grants");
  const repositories = useApi<Repository[]>("/repositories", false, !grants.isPending);
  const action = useAction();
  const [error, setError] = useState<unknown>();
  const names = new Map((repositories.data ?? []).map((r) => [r.id, r.full_name]));
  return (
    <section aria-labelledby="mcp-grants-heading" className="space-y-4">
      <div>
        <h3 id="mcp-grants-heading" className="text-base font-semibold">Connected grants</h3>
        <p className="mt-1 text-sm text-muted-foreground">Connections appear here after browser authorization. Review their repositories and permissions or revoke access.</p>
      </div>
      <ErrorNotice error={grants.error ?? repositories.error} />
      {grants.error && <Button variant="outline" size="sm" disabled={grants.isFetching} onClick={() => void grants.refetch()}>Retry grants</Button>}
      {grants.isPending ? <Loading /> : grants.data?.length ? (
        <div className="overflow-hidden rounded-lg border bg-card">
          <div aria-hidden="true" className="hidden grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] gap-4 border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground sm:grid">
            <span>Client</span><span>Access</span><span>Expiry</span><span />
          </div>
          {grants.data.map((grant) => (
            <article key={grant.id} aria-label={`${grant.client_name} ${grant.domain} grant`} className="grid gap-3 border-b px-5 py-4 text-sm last:border-b-0 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-start sm:gap-4">
              <div className="min-w-0">
                <p className="break-words font-medium">{grant.client_name}</p>
                <p className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge variant="outline" className="font-normal">{grant.domain}</Badge>
                  {grant.revoked_at ? <Badge variant="destructive">Revoked</Badge> : null}
                </p>
                <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{grant.client_id}</p>
              </div>
              <div className="min-w-0 text-xs leading-relaxed text-muted-foreground">
                <p className="break-words"><span className="font-medium text-foreground">Scopes: </span>{grant.scopes.join(", ") || "None"}</p>
                <p className="mt-1 break-words"><span className="font-medium text-foreground">Repositories: </span>
                  {grant.repository_ids.length ? grant.repository_ids.map((id) => names.get(id) ?? `#${id}`).join(", ") : "None selected"}
                </p>
                {grant.last_used_at && <p className="mt-1">Last used {date(grant.last_used_at)}</p>}
              </div>
              <div className="text-xs text-muted-foreground">
                <p>Granted {date(grant.created_at)}</p>
                <p className="mt-1">Expires {date(grant.expires_at)}</p>
              </div>
              <div className="flex sm:justify-end">
                {!grant.revoked_at && (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={action.isPending}
                    onClick={async () => {
                      setError(undefined);
                      try {
                        await action.mutateAsync({ path: `/mcp/grants/${encodeURIComponent(grant.id)}`, method: "DELETE" });
                        toast.success("Grant revoked.");
                        void grants.refetch();
                      } catch (e) {
                        setError(e);
                      }
                    }}
                  >
                    Revoke
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : !grants.error ? (
        <Empty title="No connected agents">Approve an agent from its authorization page to see it here.</Empty>
      ) : null}
      <ErrorNotice error={error ?? action.error} />
    </section>
  );
}

export function McpSettingsSection({ canAdminister }: { canAdminister: boolean }) {
  const settings = useApi<McpSettings>("/mcp/settings");
  return (
    <section aria-labelledby="mcp-heading" className="space-y-6">
      <div>
        <h2 id="mcp-heading" className="text-xl font-semibold tracking-tight">MCP access</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect an agent to reviews or security. Authorize each connection in your browser.
        </p>
      </div>
      <ErrorNotice error={settings.error} />
      {settings.isPending ? <Loading /> : settings.data ? (
        <>
          {canAdminister && settings.data.can_manage ? (
            <>
              <AdminSettings settings={settings.data} onSaved={() => void settings.refetch()} />
            </>
          ) : (
            <>
            <p className="text-sm leading-relaxed text-muted-foreground">
              Reviews MCP {settings.data.reviews_enabled ? "is on" : "is off"}, security MCP {settings.data.security_enabled ? "is on" : "is off"}
              {settings.data.management_enabled ? ", management through MCP is on" : ""}. Only administrators change instance switches.
            </p>
            <div className="divide-y rounded-lg border bg-card">
              {(["reviews", "security"] as const).map((domain) => (
                <div key={domain} className="space-y-4 p-5">
                  <h3 className="text-sm font-medium">{domain === "reviews" ? "Reviews" : "Security"} MCP</h3>
                  <ConnectionInstructions domain={domain} publicUrl={settings.data.public_url} enabled={settings.data[`${domain}_enabled`]} />
                </div>
              ))}
            </div>
            </>
          )}
          <Grants />
          {canAdminister && settings.data.can_manage && <ClientRegistration />}
        </>
      ) : (
        <Button variant="outline" size="sm" disabled={settings.isFetching} onClick={() => void settings.refetch()}>Retry MCP settings</Button>
      )}
    </section>
  );
}

export function McpAuthorizePage() {
  const [params] = useSearchParams();
  const requestId = params.get("request_id") ?? "";
  const preview = useApi<McpAuthorizePreview>(`/mcp/authorize?request_id=${encodeURIComponent(requestId)}`, false, !!requestId);
  const [repos, setRepos] = useState<number[] | null>(null);
  const [scopeNames, setScopeNames] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  if (!requestId) {
    return (
      <div className="mx-auto max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-tight">Agent authorization</h1>
        <div className="mt-4"><ErrorNotice error={new Error("This authorization link is missing its request. Return to the agent and start again.")} /></div>
      </div>
    );
  }
  const data = preview.data;
  const selectedRepos = repos ?? data?.repositories.map((r) => r.id) ?? [];
  const selectedScopes = scopeNames ?? data?.scopes.filter((s) => s.name.endsWith(":read")).map((s) => s.name) ?? [];
  function toggle(list: number[] | string[], value: number | string): (number | string)[] {
    return list.includes(value as never) ? list.filter((v) => v !== value) : [...list, value as never];
  }
  async function decide(approve: boolean) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<{ redirect_url: string }>("/mcp/authorize", "POST", {
        request_id: requestId,
        repository_ids: approve ? selectedRepos : [],
        scopes: approve ? selectedScopes : [],
        approve,
      });
      window.location.assign(result.redirect_url);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-3xl font-semibold tracking-tight">Authorize this agent?</h1>
      <p className="mt-1 text-sm text-muted-foreground">Signing in does not approve the agent. Choose exactly what it may access.</p>
      <div className="mt-4"><ErrorNotice error={preview.error} /></div>
      {preview.isPending ? <Loading /> : data ? (
        <div className="mt-6 space-y-6">
          <dl className="divide-y rounded-lg border bg-card text-sm">
            <div className="grid gap-1 px-5 py-4 sm:grid-cols-[160px_minmax(0,1fr)]">
              <dt className="text-muted-foreground">Agent</dt>
              <dd className="min-w-0 break-words font-medium">{data.client_name} <span className="font-mono text-xs font-normal text-muted-foreground">({data.client_id})</span></dd>
            </div>
            <div className="grid gap-1 px-5 py-4 sm:grid-cols-[160px_minmax(0,1fr)]">
              <dt className="text-muted-foreground">Returns to</dt>
              <dd className="min-w-0 break-all font-mono text-xs">{data.redirect_uri}</dd>
            </div>
            <div className="grid gap-1 px-5 py-4 sm:grid-cols-[160px_minmax(0,1fr)]">
              <dt className="text-muted-foreground">Domain</dt>
              <dd><Badge variant="outline" className="font-normal">{data.domain}</Badge></dd>
            </div>
            <div className="grid gap-1 px-5 py-4 sm:grid-cols-[160px_minmax(0,1fr)]">
              <dt className="text-muted-foreground">Request expires</dt>
              <dd className="text-muted-foreground">{date(data.expires_at)}</dd>
            </div>
          </dl>
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold">Repositories</legend>
            {data.repositories.length ? data.repositories.map((repo) => (
              <label key={repo.id} className="flex cursor-pointer items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm has-checked:border-primary">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={selectedRepos.includes(repo.id)}
                  onChange={() => setRepos(toggle(selectedRepos, repo.id) as number[])}
                />
                <span className="min-w-0 break-words">{repo.full_name}</span>
              </label>
            )) : <p className="text-sm text-muted-foreground">No repositories available for this request.</p>}
          </fieldset>
          <fieldset className="space-y-3">
            <legend className="text-base font-semibold">Permissions</legend>
            <p className="text-xs leading-relaxed text-muted-foreground">Read permissions start selected. Select any write, run, or management permission explicitly.</p>
            {data.scopes.map((scope) => (
              <label key={scope.name} className="flex cursor-pointer items-start gap-3 rounded-lg border bg-card px-4 py-3 text-sm has-checked:border-primary">
                <input
                  type="checkbox"
                  className="mt-0.5 size-4 accent-primary"
                  checked={selectedScopes.includes(scope.name)}
                  onChange={() => setScopeNames(toggle(selectedScopes, scope.name) as string[])}
                />
                <span className="min-w-0">
                  <span className="block font-medium">{scope.label} <span className="font-mono text-xs font-normal text-muted-foreground">({scope.name})</span></span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{scope.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          <ErrorNotice error={error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={busy} onClick={() => void decide(false)}>Deny</Button>
            <Button disabled={busy || !selectedRepos.length || !selectedScopes.length} onClick={() => void decide(true)}>
              {busy ? "Working…" : "Approve selected access"}
            </Button>
          </div>
          {!selectedRepos.length || !selectedScopes.length ? (
            <p className="text-xs text-muted-foreground">Select at least one repository and one permission to approve.</p>
          ) : null}
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-4" disabled={preview.isFetching} onClick={() => void preview.refetch()}>Retry authorization</Button>
      )}
    </div>
  );
}

export function McpApprovalPage() {
  const { id } = useParams();
  const approval = useApi<McpApproval>(`/mcp/approvals/${encodeURIComponent(id ?? "")}`, false, !!id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [decided, setDecided] = useState<boolean | null>(null);
  async function decide(approve: boolean) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<{ approved: boolean }>(`/mcp/approvals/${encodeURIComponent(id ?? "")}`, "POST", { approve });
      setDecided(result.approved);
      void approval.refetch();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const data = approval.data;
  const settled = decided ?? (data?.approved_at != null ? true : data?.denied_at != null ? false : null);
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-3xl font-semibold tracking-tight">Review this action</h1>
      <p className="mt-1 text-sm text-muted-foreground">The agent waits until you approve the exact arguments below.</p>
      <div className="mt-4"><ErrorNotice error={approval.error} /></div>
      {approval.isPending ? <Loading /> : data ? (
        <div className="mt-6 space-y-6">
          <div className="rounded-lg border bg-card p-5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="font-normal">{data.domain}</Badge>
              <span className="font-mono text-xs text-muted-foreground">{data.tool}</span>
            </div>
            <p className="mt-3 text-sm leading-relaxed">{data.summary}</p>
            <p className="mt-2 text-xs text-muted-foreground">Expires {date(data.expires_at)}</p>
          </div>
          <section aria-label="Exact arguments" className="space-y-2">
            <h2 className="text-base font-semibold">Exact arguments</h2>
            <pre tabIndex={0} className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-4 font-mono text-xs leading-relaxed">
              {JSON.stringify(data.arguments, null, 2)}
            </pre>
          </section>
          {data.consumed_at ? <p role="status" className="text-sm text-muted-foreground">This approval was already used and cannot be reused.</p>
            : settled === true ? <p role="status" className="text-sm">Approved. The agent may now run this exact action once.</p>
              : settled === false ? <p role="status" className="text-sm">Denied. This action cannot run with this approval.</p>
              : (
                <>
                  <ErrorNotice error={error} />
                  <div className="flex flex-wrap justify-end gap-2">
                    <Button variant="outline" disabled={busy} onClick={() => void decide(false)}>Deny</Button>
                    <Button disabled={busy} onClick={() => void decide(true)}>{busy ? "Working…" : "Approve this action"}</Button>
                  </div>
                </>
              )}
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-4" disabled={approval.isFetching} onClick={() => void approval.refetch()}>Retry approval</Button>
      )}
    </div>
  );
}
