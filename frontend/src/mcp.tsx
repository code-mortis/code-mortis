import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Bot, Check, ChevronDown, ChevronRight, Copy } from "lucide-react";
import { toast } from "sonner";
import { api, useAction, useApi, type Repository, type Schema } from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { Textarea } from "./components/ui/textarea";
import { Switch } from "./components/ui/switch";
import { Badge } from "./components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "./components/ui/dialog";
import { date, Empty, ErrorNotice, Field, Loading, PageTitle, Status } from "./components/common";

const groupLabels = { reviews: "Reviews", security: "Security", code: "Code", management: "Management" } as const;
type McpGroup = keyof typeof groupLabels;
type GroupSelections = Partial<Record<McpGroup, number[]>>;

const groups: McpGroup[] = ["reviews", "security", "code", "management"];

function groupForScope(scope: string): McpGroup | undefined {
  if (scope === "instance:manage" || scope === "connections:manage") return "management";
  return groups.find((group) => scope.startsWith(group + ":"));
}

export type McpSettings = Schema["McpSettingsView"];
export type McpClient = Schema["McpClientInput"];
export type McpGrant = Schema["McpGrantSummary"];
type McpAuthorizePreview = Schema["McpConsentPreview"];

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
  id, label, hint, checked, onChange,
}: {
  id: string; label: string; hint?: string; checked: boolean; onChange: (value: boolean) => void;
}) {
  return (
    <div className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <label htmlFor={id} className="text-sm font-medium">{label}</label>
          {hint && <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted-foreground">{hint}</p>}
        </div>
        <Switch id={id} aria-describedby={hint ? `${id}-hint` : undefined} checked={checked} onCheckedChange={onChange} className="mt-0.5 shrink-0" />
      </div>
    </div>
  );
}

function ConnectionInstructions({ settings }: { settings: McpSettings }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<"endpoint" | "prompt" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const endpoint = `${settings.public_url.replace(/\/+$/, "")}/mcp`;
  const hasEnabledGroup = groups.some((group) => settings[`${group}_enabled`]);
  const quotedEndpoint = "'" + endpoint.replaceAll("'", "'\\''") + "'";
  const prompt = `Connect this client to Code Mortis over Streamable HTTP.

Server URL: ${endpoint}
Connection name: codemortis

Use OAuth with PKCE and dynamic client registration. Use the permissions advertised by the server. Let me choose permissions and repositories in the browser. Only read permissions should be preselected.

Use the instructions for the current client.

For Claude Code, add the HTTP server with
claude mcp add --transport http codemortis ${quotedEndpoint}

For Codex CLI, use the following entry in the active config.toml. Reuse the entry if it already points to this URL. Leave its optional scopes setting unset for OAuth discovery.

[mcp_servers.codemortis]
url = ${JSON.stringify(endpoint)}
oauth_resource = ${JSON.stringify(endpoint)}

If authorization is needed, run this command once and wait for the browser callback:
codex mcp login codemortis --oauth-client-registration dcr

Verify the connection by listing the available tools.`;
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
  if (!settings.public_url) return <p className="text-sm text-muted-foreground">Ask an administrator to configure the public URL before connecting an agent.</p>;
  return (
    <Dialog open={expanded} onOpenChange={(open) => { setExpanded(open); setError(null); setCopied(null); }}>
      <DialogTrigger asChild>
        <Button type="button" variant="link" className="h-auto p-0 align-baseline text-xs font-normal underline" aria-label="MCP installation instructions">Installation instructions</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>MCP installation instructions</DialogTitle>
          <DialogDescription>Use this URL in your client or paste the prompt into your coding agent. Complete authorization in your browser.</DialogDescription>
        </DialogHeader>
        {!settings.enabled && <p className="text-xs text-muted-foreground">MCP is off. An administrator must enable it and save before you connect.</p>}
        {settings.enabled && !hasEnabledGroup && <p className="text-xs text-muted-foreground">An administrator must enable a tool group before you can connect.</p>}
        <div className="min-w-0 space-y-2">
          <p className="text-sm font-medium">Server URL</p>
          <div className="flex items-start gap-2">
            <code aria-label="MCP endpoint" className="min-w-0 flex-1 select-text break-all font-mono text-xs leading-relaxed">{endpoint}</code>
            <Button type="button" variant="ghost" size="icon" className="-my-1.5 size-8 shrink-0 text-muted-foreground" aria-label="Copy MCP endpoint" title={copied === "endpoint" ? "Copied" : "Copy endpoint"} onClick={() => void copy(endpoint, "endpoint")}>
              {copied === "endpoint" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
            </Button>
          </div>
        </div>
        <div className="group/installation relative min-w-0">
          <Textarea aria-label="MCP installation prompt text" readOnly value={prompt} className="h-64 resize-y pr-14 font-mono text-xs leading-relaxed sm:pr-12 md:text-xs" />
          <Button type="button" variant="ghost" size="icon" aria-label="Copy MCP installation prompt" title={copied === "prompt" ? "Copied" : "Copy prompt"} className="absolute right-1 top-1 size-11 text-muted-foreground opacity-0 transition-opacity group-hover/installation:opacity-100 group-focus-within/installation:opacity-100 sm:size-8 [@media(hover:none)]:opacity-100" onClick={() => void copy(prompt, "prompt")}>
            {copied === "prompt" ? <Check aria-hidden="true" className="size-3.5" /> : <Copy aria-hidden="true" className="size-3.5" />}
          </Button>
        </div>
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {copied && <p role="status" className="sr-only">MCP {copied === "endpoint" ? "endpoint" : "installation prompt"} copied.</p>}
      </DialogContent>
    </Dialog>
  );
}

function AdminSettings({ settings, onSaved }: { settings: McpSettings; onSaved: () => void }) {
  const action = useAction<McpSettings>();
  const [enabled, setEnabled] = useState(settings.enabled);
  const [reviews, setReviews] = useState(settings.reviews_enabled);
  const [security, setSecurity] = useState(settings.security_enabled);
  const [code, setCode] = useState(settings.code_enabled);
  const [management, setManagement] = useState(settings.management_enabled);
  const savedDomains = (settings.allowed_client_domains ?? []).join("\n");
  const [domains, setDomains] = useState(savedDomains);
  const [error, setError] = useState<unknown>();
  const dirty = enabled !== settings.enabled || reviews !== settings.reviews_enabled || security !== settings.security_enabled
    || code !== settings.code_enabled
    || management !== settings.management_enabled || domains !== savedDomains;
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
              enabled,
              reviews_enabled: reviews,
              security_enabled: security,
              code_enabled: code,
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
        <ToggleRow id="mcp-enabled" label="Enable MCP" hint="Turning MCP off revokes every grant and cancels outstanding MCP work." checked={enabled} onChange={setEnabled} />
        <ToggleRow id="mcp-reviews" label="Reviews" hint="Read and triage findings or start and stop reviews." checked={reviews} onChange={setReviews} />
        <ToggleRow id="mcp-security" label="Security" hint="Read and triage findings, manage GitHub issues, or run scans." checked={security} onChange={setSecurity} />
        <ToggleRow id="mcp-code" label="Code" hint="Read source and relationships from code graphs that a repository has enabled." checked={code} onChange={setCode} />
        <ToggleRow id="mcp-management" label="Allow management through MCP" hint="Authorize management of permitted AI connections, members, or GitHub installations separately." checked={management} onChange={setManagement} />
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
      <summary className="cursor-pointer text-base font-semibold">Registered clients</summary>
      <div className="mt-4 space-y-4">
        <p className="text-sm text-muted-foreground">Includes clients registered automatically when connecting an agent, such as Codex, and clients registered manually. Repository access is managed through connected grants.</p>
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
          aria-labelledby="mcp-client-registration-heading"
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
          <div className="space-y-1">
            <h4 id="mcp-client-registration-heading" className="text-sm font-medium">Register a client manually</h4>
            <p className="text-xs text-muted-foreground">For clients that need a fixed client ID and exact redirect URIs. Codex uses automatic registration.</p>
          </div>
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

function grantGroups(grant: McpGrant): McpGroup[] {
  if (grant.domain !== "unified") return [grant.domain];
  return groups.filter((group) => grant.scopes.some((scope) => groupForScope(scope) === group));
}

function repositoriesForGrant(grant: McpGrant, group: McpGroup): number[] {
  return grant.domain === "unified" ? grant.repository_ids_by_group[group] ?? [] : grant.repository_ids;
}

function grantStatus(grant: McpGrant) {
  return grant.revoked_at ? "revoked" : new Date(grant.expires_at).getTime() <= Date.now() ? "expired" : "active";
}

function Grants() {
  const grants = useApi<McpGrant[]>("/mcp/grants");
  const repositories = useApi<Repository[]>("/repositories", false, !grants.isPending);
  const action = useAction();
  const [error, setError] = useState<unknown>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const selected = grants.data?.find((grant) => grant.id === selectedId);
  const detailsHeading = useRef<HTMLHeadingElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const currentGrants = (grants.data ?? []).filter((grant) => !grant.revoked_at);
  const revokedGrants = (grants.data ?? []).filter((grant) => grant.revoked_at);
  const names = new Map((repositories.data ?? []).map((r) => [r.id, r.full_name]));
  const renderGrants = (items: McpGrant[]) => (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div aria-hidden="true" className="settings-record settings-connection-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
        <span>Connection</span><span>Access</span><span>Status</span><span />
      </div>
      {items.map((grant) => {
        const status = grantStatus(grant);
        const toolGroups = grantGroups(grant);
        const repositoryCount = new Set(toolGroups.flatMap((group) => repositoriesForGrant(grant, group))).size;
        return <article key={grant.id} aria-label={`${grant.client_name} ${grant.domain} grant`}
          className="settings-record settings-connection-row group relative border-b px-4 py-4 text-sm transition-colors last:border-b-0 hover:bg-muted/50 sm:px-5">
          <div className="settings-identity flex min-w-0 items-start gap-3">
            <Bot aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <h4 className="break-words font-medium group-hover:text-primary">
                <button type="button" aria-label={`View ${grant.client_name} ${grant.domain} grant`} aria-haspopup="dialog"
                  className="max-w-full cursor-pointer break-words text-left after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                  onClick={(event) => { returnFocus.current = event.currentTarget; setSelectedId(grant.id); setError(undefined); action.reset(); }}>
                  {grant.client_name}
                </button>
              </h4>
              <p className="mt-1 text-xs text-muted-foreground">{toolGroups.map((group) => groupLabels[group]).join(", ") || "No tool groups"}</p>
            </div>
          </div>
          <div className="min-w-0 text-xs leading-relaxed">
            <span aria-hidden="true" className="settings-cell-label mb-1 text-muted-foreground">Access</span>
            <p>{repositoryCount} {repositoryCount === 1 ? "repository" : "repositories"}</p>
            <p className="mt-1 text-muted-foreground">Expires {date(grant.expires_at)}</p>
          </div>
          <div className="min-w-0 text-xs text-muted-foreground">
            <span aria-hidden="true" className="settings-cell-label mb-1">Status</span>
            <Status value={status} label={status === "active" ? "Active" : status === "expired" ? "Expired" : "Revoked"}
              icon={status === "active" ? <Check aria-hidden="true" className="size-3" /> : undefined} />
          </div>
          <div className="settings-actions flex items-center justify-end gap-1">
            <ChevronRight aria-hidden="true" className="m-2 size-4 text-muted-foreground" />
          </div>
        </article>;
      })}
    </div>
  );
  return (
    <section aria-labelledby="mcp-grants-heading" className="space-y-4">
      <div>
        <h3 id="mcp-grants-heading" ref={heading} tabIndex={-1} className="text-base font-semibold">Connected grants</h3>
        <p className="mt-1 text-sm text-muted-foreground">Connections appear here after browser authorization. Review their repositories and permissions or revoke access.</p>
      </div>
      <ErrorNotice error={grants.error ?? repositories.error} />
      {grants.error && <Button variant="outline" size="sm" disabled={grants.isFetching} onClick={() => void grants.refetch()}>Retry grants</Button>}
      {grants.isPending ? <Loading /> : currentGrants.length ? (
        renderGrants(currentGrants)
      ) : !grants.error ? (
        <Empty title="No connected agents">Approve an agent from its authorization page to see it here.</Empty>
      ) : null}
      {revokedGrants.length > 0 && <details className="group">
        <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 text-sm font-medium text-muted-foreground outline-offset-4 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          Revoked grants <span className="text-xs tabular-nums">{revokedGrants.length}</span>
          <ChevronDown aria-hidden="true" className="size-3 shrink-0 transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-3">{renderGrants(revokedGrants)}</div>
      </details>}
      <Dialog open={!!selected} onOpenChange={(open) => { if (!open && !action.isPending) setSelectedId(null); }}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg" onOpenAutoFocus={(event) => {
          event.preventDefault();
          detailsHeading.current?.focus();
        }} onCloseAutoFocus={(event) => {
          event.preventDefault();
          (returnFocus.current?.isConnected ? returnFocus.current : heading.current)?.focus();
        }}>
          {selected && <>
            <DialogHeader className="min-w-0 pr-6 text-left">
              <DialogTitle ref={detailsHeading} tabIndex={-1} className="break-words leading-snug">{selected.client_name}</DialogTitle>
              <DialogDescription>{selected.domain === "unified" ? "MCP permissions by tool group" : `Legacy ${groupLabels[selected.domain]} MCP grant`}</DialogDescription>
            </DialogHeader>
            {selected.revoked_at && <div><Status value="revoked" label="Revoked" /></div>}
            <dl className="space-y-4 text-sm">
              <div className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-muted-foreground">Client ID</dt>
                <dd className="min-w-0 break-all font-mono text-xs leading-relaxed">{selected.client_id}</dd>
              </div>
              {grantGroups(selected).map((group) => {
                const scopes = selected.scopes.filter((scope) => selected.domain !== "unified" || groupForScope(scope) === group);
                const repositoryIds = repositoriesForGrant(selected, group);
                return (
                  <div key={group} className="grid gap-1 border-t pt-3 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
                    <dt className="font-medium">{groupLabels[group]}</dt>
                    <dd className="min-w-0 space-y-2 text-xs leading-relaxed">
                      {scopes.length > 0 && <ul aria-label="Permissions" className="space-y-1">
                        {scopes.map((scope) => (
                          <li key={scope} className="break-words">{scope}{scope === "instance:manage" && " (instance-wide)"}</li>
                        ))}
                      </ul>}
                      {repositories.isPending ? "Loading repositories…" : <ul aria-label="Repositories" className="space-y-1 text-muted-foreground">
                        {repositoryIds.length ? repositoryIds.map((id) => <li key={id} className="break-words">{names.get(id) ?? `#${id}`}</li>)
                          : <li>None selected</li>}
                      </ul>}
                    </dd>
                  </div>
                );
              })}
              {[
                ["Granted", date(selected.created_at)],
                ["Expires", date(selected.expires_at)],
                ["Last used", selected.last_used_at ? date(selected.last_used_at) : "Never used"],
                ...(selected.revoked_at ? [["Revoked", date(selected.revoked_at)]] : []),
              ].map(([label, value]) => <div key={label} className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-muted-foreground">{label}</dt><dd className="text-xs leading-relaxed">{value}</dd>
              </div>)}
            </dl>
            <ErrorNotice error={error ?? action.error ?? repositories.error} />
            {!selected.revoked_at && <DialogFooter className="border-t pt-4">
              <Button variant="outline" disabled={action.isPending} onClick={async () => {
                setError(undefined);
                try {
                  await action.mutateAsync({ path: `/mcp/grants/${encodeURIComponent(selected.id)}`, method: "DELETE" });
                  toast.success("Grant revoked.");
                  returnFocus.current = heading.current;
                  setSelectedId(null);
                  void grants.refetch();
                } catch (e) {
                  setError(e);
                }
              }}>{action.isPending ? "Revoking…" : "Revoke"}</Button>
            </DialogFooter>}
          </>}
        </DialogContent>
      </Dialog>
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
          Connect once and choose which tool groups and repositories your agent may access.
        </p>
      </div>
      <ErrorNotice error={settings.error} />
      {settings.isPending ? <Loading /> : settings.data ? (
        <>
          <ConnectionInstructions settings={settings.data} />
          {canAdminister && settings.data.can_manage ? (
            <AdminSettings settings={settings.data} onSaved={() => void settings.refetch()} />
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              {settings.data.enabled
                ? `Available tool groups are ${groups.filter((group) => settings.data?.[`${group}_enabled`]).map((group) => groupLabels[group]).join(", ") || "none"}.`
                : "MCP is off. An administrator can enable it."}
            </p>
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
  return <McpAuthorizeRequest key={requestId} requestId={requestId} />;
}

function McpAuthorizeRequest({ requestId }: { requestId: string }) {
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  const preview = useApi<McpAuthorizePreview>(`/mcp/authorize?request_id=${encodeURIComponent(requestId)}`, false, !!requestId);
  const [repos, setRepos] = useState<GroupSelections>({});
  const [scopeNames, setScopeNames] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  if (!requestId) {
    return (
      <div className="mx-auto max-w-2xl">
        <PageTitle title="Agent authorization" />
        <div className="mt-4"><ErrorNotice error={new Error("This authorization link is missing its request. Return to the agent and start again.")} /></div>
      </div>
    );
  }
  const data = preview.data;
  const allowedByGroup = data?.repositories_by_group ?? {};
  const selectedScopes = scopeNames ?? data?.scopes.filter((scope) => (
    scope.name.endsWith(":read") && allowedByGroup[scope.group]?.length
  )).map((scope) => scope.name) ?? [];
  const selectedGroups = groups.filter((group) => selectedScopes.some((scope) => groupForScope(scope) === group));
  const requiresRepositories = (group: McpGroup) => group !== "management" || selectedScopes.includes("connections:manage");
  const missingRepositories = selectedGroups.some((group) => requiresRepositories(group) && !repos[group]?.length);
  const repositoryIdsByGroup: GroupSelections = {};
  for (const group of selectedGroups) {
    if (requiresRepositories(group)) repositoryIdsByGroup[group] = repos[group] ?? [];
  }
  async function decide(approve: boolean) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<{ redirect_url: string }>("/mcp/authorize", "POST", {
        request_id: requestId,
        repository_ids_by_group: approve ? repositoryIdsByGroup : {},
        scopes: approve ? selectedScopes : [],
        approve,
      });
      if (active.current) window.location.assign(result.redirect_url);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto max-w-2xl">
      <PageTitle title="Authorize this agent?">Signing in does not approve the agent. Choose exactly what it may access.</PageTitle>
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
              <dt className="text-muted-foreground">Request expires</dt>
              <dd className="text-muted-foreground">{date(data.expires_at)}</dd>
            </div>
          </dl>
          <p className="text-sm text-muted-foreground">Choose permissions and repositories for each tool group.</p>
          {groups.map((group) => {
            const scopes = data.scopes.filter((scope) => scope.group === group);
            if (!scopes.length) return null;
            const available = allowedByGroup[group] ?? [];
            const selected = repos[group] ?? [];
            const otherSelections = selectedGroups.filter((source) => source !== group && repos[source]?.length);
            return (
              <fieldset key={group} aria-label={groupLabels[group]} disabled={busy} className="min-w-0 space-y-3 rounded-lg border bg-card p-4 sm:p-5">
                <legend className="px-1 text-base font-semibold">{groupLabels[group]}</legend>
                {group === "code" && <p className="text-xs leading-relaxed text-muted-foreground">Only repositories with code graphs enabled are offered.</p>}
                {scopes.map((scope) => (
                  <label key={scope.name} className="flex cursor-pointer items-start gap-3 py-1.5 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 shrink-0 accent-primary"
                      checked={selectedScopes.includes(scope.name)}
                      onChange={(event) => setScopeNames(event.target.checked
                        ? [...selectedScopes, scope.name]
                        : selectedScopes.filter((name) => name !== scope.name))}
                    />
                    <span className="min-w-0">
                      <span className="block break-words font-medium">{scope.label} <span className="font-mono text-xs font-normal text-muted-foreground">({scope.name})</span></span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{scope.description}{scope.name === "instance:manage" && " Applies to the entire instance."}</span>
                    </span>
                  </label>
                ))}
                {selectedGroups.includes(group) && requiresRepositories(group) && (
                  <div role="group" aria-label={`${groupLabels[group]} repositories`} className="space-y-2 border-t pt-3">
                    <p className="text-sm font-medium">Repositories</p>
                    {otherSelections.map((source) => {
                      const shared = (repos[source] ?? []).filter((id) => available.some((repo) => repo.id === id));
                      if (!shared.length) return null;
                      return <Button key={source} type="button" variant="outline" size="sm"
                        onClick={() => setRepos({ ...repos, [group]: shared })}>
                        Use repositories from {groupLabels[source]}
                      </Button>;
                    })}
                    {available.length ? available.map((repo) => (
                      <label key={repo.id} className="flex cursor-pointer items-center gap-3 py-1.5 text-sm">
                        <input
                          type="checkbox"
                          className="size-4 shrink-0 accent-primary"
                          checked={selected.includes(repo.id)}
                          onChange={(event) => setRepos({
                            ...repos,
                            [group]: event.target.checked ? [...selected, repo.id] : selected.filter((id) => id !== repo.id),
                          })}
                        />
                        <span className="min-w-0 break-words">{repo.full_name}</span>
                      </label>
                    )) : <p className="text-sm text-muted-foreground">No repositories available for this group.</p>}
                  </div>
                )}
              </fieldset>
            );
          })}
          <ErrorNotice error={error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={busy} onClick={() => void decide(false)}>Deny</Button>
            <Button disabled={busy || missingRepositories || !selectedScopes.length} onClick={() => void decide(true)}>
              {busy ? "Working…" : "Approve selected access"}
            </Button>
          </div>
          {(!selectedScopes.length || missingRepositories) && (
            <p className="text-xs text-muted-foreground">
              {selectedScopes.length ? "Select repositories for each group you want to authorize." : "Select at least one permission to approve."}
            </p>
          )}
        </div>
      ) : (
        <Button variant="outline" size="sm" className="mt-4" disabled={preview.isFetching} onClick={() => void preview.refetch()}>Retry authorization</Button>
      )}
    </div>
  );
}

export function McpApprovalPage() {
  const { id } = useParams();
  return <McpApprovalRequest key={id} id={id ?? ""} />;
}

function McpApprovalRequest({ id }: { id: string }) {
  const approval = useApi<McpApproval>(`/mcp/approvals/${encodeURIComponent(id)}`, false, !!id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [decided, setDecided] = useState<boolean | null>(null);
  async function decide(approve: boolean) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<{ approved: boolean }>(`/mcp/approvals/${encodeURIComponent(id)}`, "POST", { approve });
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
      <PageTitle title="Review this action">The agent waits until you approve the exact arguments below.</PageTitle>
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
