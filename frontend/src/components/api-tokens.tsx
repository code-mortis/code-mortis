import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronRight, Copy, KeyRound, Plus } from "lucide-react";
import { ApiError, api, useApi, useAction, type Repository, type Schema } from "../api";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import {
  date,
  ErrorNotice,
  Empty,
  Field,
  Loading,
  Status,
} from "./common";

export type ApiTokenStatus = "active" | "expired" | "revoked";
export type ApiTokenItem = Omit<Schema["ApiTokenOut"], "status"> & { status: ApiTokenStatus };
export type ApiTokenPermission = Schema["ApiTokenScope"];
export type ApiTokenList = Omit<Schema["ApiTokenPage"], "items"> & { items: ApiTokenItem[] };
type ApiTokenCreated = Omit<Schema["ApiTokenCreated"], "item"> & { item: ApiTokenItem };

const EXPIRY_CANDIDATES = [7, 30, 90, 180, 365];
const RETURN_TO = "/settings?tab=api-tokens";
const CREATE_PATH = "/settings/api-tokens/new";
const loginHref = `/api/v1/auth/login?return_to=${encodeURIComponent(CREATE_PATH)}`;

function expiryOptions(maxDays: number, defaultDays: number) {
  const options = EXPIRY_CANDIDATES.filter((days) => days <= maxDays);
  if (!options.includes(defaultDays) && defaultDays >= 1 && defaultDays <= maxDays)
    options.push(defaultDays);
  return options.sort((a, b) => a - b);
}

function groupPermissions(permissions: ApiTokenPermission[]) {
  const groups = new Map<string, ApiTokenPermission[]>();
  for (const permission of permissions) {
    const group = permission.id.includes(":") ? permission.id.split(":")[0] : "other";
    const items = groups.get(group) ?? [];
    items.push(permission);
    groups.set(group, items);
  }
  return [...groups];
}

function runScope(id: string) {
  return id.endsWith(":run");
}

export function ApiTokensSection() {
  const [page, setPage] = useState(1);
  const [inactivePage, setInactivePage] = useState(1);
  const list = useApi<ApiTokenList>(`/api-tokens?status=active&page=${page}`);
  const inactive = useApi<ApiTokenList>(`/api-tokens?status=inactive&page=${inactivePage}`);
  const repos = useApi<Repository[]>("/repositories", false, true);
  const revokeAction = useAction<{ revoked: boolean }>();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detailsTrigger = useRef<HTMLElement | null>(null);
  const detailsHeading = useRef<HTMLHeadingElement>(null);
  const detailsRevoke = useRef<HTMLButtonElement>(null);
  const [revoking, setRevoking] = useState<ApiTokenItem | null>(null);
  const location = useLocation();
  const newTokenLink = useRef<HTMLAnchorElement>(null);
  const createHeading = useRef<HTMLHeadingElement>(null);
  const selected = [...(list.data?.items ?? []), ...(inactive.data?.items ?? [])].find((token) => token.id === selectedId);
  const repoNames = new Map((repos.data ?? []).map((repo) => [repo.id, repo.full_name]));

  useEffect(() => {
    if (revoking) detailsHeading.current?.focus();
    else detailsRevoke.current?.focus();
  }, [revoking]);

  useEffect(() => {
    if (location.state?.focusNewToken) newTokenLink.current?.focus();
  }, [location.state]);

  async function revoke(token: ApiTokenItem) {
    await revokeAction.mutateAsync({
      path: `/api-tokens/${token.id}`,
      method: "DELETE",
    });
    detailsTrigger.current = createHeading.current;
    setSelectedId(null);
    setRevoking(null);
  }

  const activeCount = list.data?.active_count;

  const renderTokens = (data: ApiTokenList | undefined, fetching: boolean, onPage: (page: number) => void, label: string) => (
    data && (data.items.length > 0 || data.page > 1) ? <div className="overflow-hidden rounded-lg border bg-card">
      <div aria-hidden="true" className="settings-record settings-connection-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
        <span>Token</span><span>Access</span><span>Status</span><span />
      </div>
      {data.items.map((token) => (
        <article key={token.id} aria-label={`API token ${token.name}`}
          className="settings-record settings-connection-row group relative border-b px-4 py-4 text-sm transition-colors last:border-b-0 hover:bg-muted/50 sm:px-5">
          <div className="settings-identity flex min-w-0 items-start gap-3">
            <KeyRound aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
            <div className="min-w-0">
              <h4 className="break-words font-medium group-hover:text-primary">
                <button type="button" aria-label={`View API token ${token.name}`} aria-haspopup="dialog"
                  className="max-w-full cursor-pointer break-words text-left after:absolute after:inset-0 focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-ring"
                  onClick={(event) => { detailsTrigger.current = event.currentTarget; setSelectedId(token.id); }}>
                  {token.name}
                </button>
              </h4>
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground"><span className="sr-only">Prefix: </span>{token.prefix}…</p>
            </div>
          </div>
          <div className="min-w-0 text-xs leading-relaxed">
            <span aria-hidden="true" className="settings-cell-label mb-1 text-muted-foreground">Access</span>
            <p>{token.repository_ids.length} {token.repository_ids.length === 1 ? "repository" : "repositories"}</p>
            <p className="mt-1 text-muted-foreground">Expires {date(token.expires_at)}</p>
          </div>
          <div className="min-w-0 text-xs text-muted-foreground">
            <span aria-hidden="true" className="settings-cell-label mb-1">Status</span>
            <Status value={token.status} label={token.status === "active" ? "Active" : token.status === "expired" ? "Expired" : "Revoked"}
              icon={token.status === "active" ? <Check aria-hidden="true" className="size-3" /> : undefined} />
          </div>
          <div className="settings-actions flex items-center justify-end gap-1">
            <ChevronRight aria-hidden="true" className="m-2 size-4 text-muted-foreground" />
          </div>
        </article>
      ))}
      {(data.page > 1 || data.has_more) && <nav aria-label={`${label} tokens pagination`} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" disabled={data.page <= 1 || fetching} onClick={() => onPage(data.page - 1)}>Previous<span className="sr-only"> tokens</span></Button>
          <Button variant="ghost" size="sm" disabled={!data.has_more || fetching} onClick={() => onPage(data.page + 1)}>Next<span className="sr-only"> tokens</span></Button>
        </div>
        <span role="status" className="ml-auto text-xs tabular-nums text-muted-foreground">Page {data.page} · {data.total} total</span>
      </nav>}
    </div> : null
  );

  return <section aria-labelledby="api-tokens-heading" className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1 basis-64">
        <h2 id="api-tokens-heading" ref={createHeading} tabIndex={-1} className="text-xl font-semibold tracking-tight">API tokens</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Create personal tokens for scripts and Noodle. Tokens act as you, limited to their permissions and repositories.
        </p>
      </div>
      <Button asChild><Link ref={newTokenLink} to={CREATE_PATH}><Plus aria-hidden="true" className="size-4" />New token</Link></Button>
    </div>

    <section aria-labelledby="api-token-list-heading" className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 id="api-token-list-heading" className="text-base font-semibold">Your tokens</h3>
        {activeCount !== undefined && <span role="status" className="text-xs tabular-nums text-muted-foreground">{activeCount} active</span>}
      </div>
      <ErrorNotice error={list.error} />
      {list.isPending && <Loading />}
      {list.data && !list.data.items.length && <Empty title="No active tokens">
        Create a token to call supported API operations from scripts or Noodle without copying a browser cookie.
      </Empty>}
      {renderTokens(list.data, list.isFetching, setPage, "Active")}
      <ErrorNotice error={inactive.error} title="Inactive tokens unavailable" />
      {inactive.error && <Button variant="outline" size="sm" disabled={inactive.isFetching} onClick={() => void inactive.refetch()}>Retry inactive tokens</Button>}
      {(!!inactive.data?.total || inactivePage > 1) && <details className="group">
        <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 text-sm font-medium text-muted-foreground outline-offset-4 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
          Inactive tokens <span className="text-xs tabular-nums">{inactive.data?.total}</span>
          <ChevronDown aria-hidden="true" className="size-3 shrink-0 transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-3 space-y-3">
          <p className="text-sm text-muted-foreground">Revoked and expired tokens can no longer access the API.</p>
          {inactive.isPending && <Loading />}
          {renderTokens(inactive.data, inactive.isFetching, setInactivePage, "Inactive")}
        </div>
      </details>}
      <ErrorNotice error={repos.error} title="Repositories unavailable" />
      <p className="text-xs leading-relaxed text-muted-foreground">
        Store each token as a secret and send it as a bearer credential. Leaving the creation page loses the secret permanently; revoke the unused token and create another. Revoked tokens stop new requests immediately while queued cleanup finishes in the background.
      </p>
    </section>

    <Dialog open={!!selected || !!revoking} onOpenChange={(open) => {
      if (open || revokeAction.isPending) return;
      if (revoking) { setRevoking(null); revokeAction.reset(); }
      else setSelectedId(null);
    }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg" onOpenAutoFocus={(event) => {
        event.preventDefault();
        detailsHeading.current?.focus();
      }} onCloseAutoFocus={(event) => {
        event.preventDefault();
        (detailsTrigger.current?.isConnected ? detailsTrigger.current : createHeading.current)?.focus();
      }}>
        {revoking ? <>
          <DialogHeader>
            <DialogTitle ref={detailsHeading} tabIndex={-1}>Revoke {revoking?.name ? `“${revoking.name}”` : "token"}?</DialogTitle>
            <DialogDescription>
              Future requests with this token will fail, and unfinished work started by this token will be stopped.
              Queued cleanup continues in the background, so already running work may take a moment to settle. Completed results are kept.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={revokeAction.error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={revokeAction.isPending} onClick={() => { setRevoking(null); revokeAction.reset(); }}>Cancel</Button>
            <Button variant="destructive" disabled={revokeAction.isPending} onClick={() => { if (revoking) void revoke(revoking).catch(() => { /* Keep the dialog open and display the action error. */ }); }}>
              {revokeAction.isPending ? "Revoking…" : "Revoke token"}
            </Button>
          </div>
        </> : selected ? <>
          <DialogHeader className="min-w-0 pr-6 text-left">
            <DialogTitle ref={detailsHeading} tabIndex={-1} className="break-words leading-snug">{selected.name}</DialogTitle>
            <DialogDescription>API token details</DialogDescription>
          </DialogHeader>
          <div><Status value={selected.status} label={selected.status === "active" ? "Active" : selected.status === "expired" ? "Expired" : "Revoked"} /></div>
          <dl className="space-y-4 text-sm">
            <div className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">Token prefix</dt>
              <dd className="min-w-0 break-all font-mono text-xs leading-relaxed">{selected.prefix}…</dd>
            </div>
            <div className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">Permissions</dt>
              <dd className="min-w-0"><ul className="space-y-1 text-xs leading-relaxed">
                {selected.scopes.length ? selected.scopes.map((scope) => <li key={scope} className="break-words">{scope}</li>) : <li>None</li>}
              </ul></dd>
            </div>
            <div className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">Repositories</dt>
              <dd className="min-w-0 text-xs leading-relaxed">
                {repos.isPending ? "Loading repositories…" : !repos.data ? "Repository names unavailable" : <ul className="space-y-1">
                  {selected.repository_ids.length ? selected.repository_ids.map((id) => <li key={id} className="break-words">{repoNames.get(id) ?? `#${id} unavailable`}</li>) : <li>None</li>}
                </ul>}
              </dd>
            </div>
            {[
              ["Created", date(selected.created_at)],
              ["Expires", date(selected.expires_at)],
              ["Last used", selected.last_used_at ? `${date(selected.last_used_at)} (approximate)` : "Never used"],
              ...(selected.revoked_at ? [["Revoked", date(selected.revoked_at)]] : []),
            ].map(([label, value]) => <div key={label} className="grid gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4">
              <dt className="text-muted-foreground">{label}</dt><dd className="text-xs leading-relaxed">{value}</dd>
            </div>)}
          </dl>
          <ErrorNotice error={repos.error} title="Repositories unavailable" />
          {selected.status === "active" && <DialogFooter className="border-t pt-4">
            <Button ref={detailsRevoke} variant="outline" disabled={revokeAction.isPending} onClick={() => {
              revokeAction.reset();
              setRevoking(selected);
            }}>Revoke</Button>
          </DialogFooter>}
        </> : null}
      </DialogContent>
    </Dialog>
  </section>;
}

export function CreateApiTokenPage() {
  const metadata = useApi<ApiTokenList>("/api-tokens?status=active&page=1");
  const repositories = useApi<Repository[]>("/repositories");
  const [pending, setPending] = useState(false);
  const navigate = useNavigate();
  function backToTokens() {
    if (!pending) navigate(RETURN_TO, { replace: true, state: { focusNewToken: true } });
  }
  return <section aria-label="Create API token" className="max-w-3xl space-y-6">
    <ErrorNotice error={metadata.error ?? repositories.error} />
    {(metadata.error || repositories.error) && <Button variant="outline" disabled={metadata.isFetching || repositories.isFetching}
      onClick={() => { void metadata.refetch(); void repositories.refetch(); }}>Retry token settings</Button>}
    {metadata.data && repositories.data ? <CreateTokenForm
      permissions={metadata.data.permissions}
      maxDays={metadata.data.max_expiration_days}
      defaultDays={metadata.data.default_expiration_days}
      reauthRequired={metadata.data.reauth_required}
      repositories={repositories.data}
      pending={pending}
      setPending={setPending}
      onDone={backToTokens}
    /> : <>
      <h2 className="text-xl font-semibold tracking-tight">New API token</h2>
      {(metadata.isPending || repositories.isPending) && <Loading />}
    </>}
  </section>;
}

function CreateTokenForm({ permissions, maxDays, defaultDays, reauthRequired, repositories, pending, setPending, onDone }: {
  permissions: ApiTokenPermission[];
  maxDays: number;
  defaultDays: number;
  reauthRequired: boolean;
  repositories: Repository[];
  pending: boolean;
  setPending: (pending: boolean) => void;
  onDone: () => void;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const [expiresInDays, setExpiresInDays] = useState(String(defaultDays));
  const [scopes, setScopes] = useState<string[]>(() => permissions.filter((item) => item.id.endsWith(":read")).map((item) => item.id));
  const [repositoryIds, setRepositoryIds] = useState<number[]>([]);
  const [error, setError] = useState<unknown>();
  const [lost, setLost] = useState<string | null>(null);
  // The raw secret lives only in this component state. It is never written to
  // a query cache, toast, log, or browser storage, and is cleared on unmount.
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [copyState, setCopyState] = useState<"copied" | "failed" | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorSummary = useRef<HTMLDivElement>(null);
  const cache = useQueryClient();
  useEffect(() => {
    heading.current?.focus();
    window.scrollTo(0, 0);
  }, [created]);
  useEffect(() => {
    if (error) {
      errorSummary.current?.focus();
      errorSummary.current?.scrollIntoView({ block: "center" });
    }
  }, [error]);

  function toggleScope(value: string) {
    setScopes((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  }
  function toggleRepository(value: number) {
    setRepositoryIds((current) => current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending || created) return;
    setError(undefined);
    setLost(null);
    const trimmed = name.trim();
    if (!trimmed) {
      setError(new Error("Enter a token name."));
      return;
    }
    if (!scopes.length) {
      setError(new Error("Select at least one permission."));
      return;
    }
    if (!repositoryIds.length) {
      setError(new Error("Select at least one repository. New repositories are never added automatically."));
      return;
    }
    const days = Number(expiresInDays);
    if (!Number.isInteger(days) || days < 1 || days > maxDays) {
      setError(new Error(`Choose an expiration between 1 and ${maxDays} days.`));
      return;
    }
    setPending(true);
    try {
      // Direct call on purpose: useAction would retain the secret in mutation state.
      const result = await api<ApiTokenCreated>("/api-tokens", "POST", {
        name: trimmed,
        scopes,
        repository_ids: repositoryIds,
        expires_in_days: days,
      });
      setCreated(result);
      void cache.invalidateQueries();
    } catch (requestError) {
      if (requestError instanceof ApiError && (requestError.status === 0 || requestError.status === 504)) {
        setLost(trimmed);
      }
      setError(requestError);
    } finally {
      setPending(false);
    }
  }

  async function copy() {
    if (!created) return;
    setCopyState(null);
    try {
      await navigator.clipboard.writeText(created.token);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  if (created) return <>
    <header>
      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold tracking-tight">Token created</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Copy “{created.item.name}” now and store it as a secret, for example with Noodle. This secret is shown once: leaving this page loses it permanently. If the secret is lost, revoke the unused token from the list and create another.
      </p>
    </header>
    <div className="space-y-2">
      <Label htmlFor={`${id}-secret`}>One-time token</Label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          id={`${id}-secret`}
          readOnly
          autoComplete="off"
          spellCheck={false}
          value={created.token}
          onFocus={(event) => event.currentTarget.select()}
          className="min-w-0 flex-1 font-mono text-xs"
        />
        <Button type="button" variant="outline" size="sm" className="shrink-0" onClick={() => void copy()}>
          {copyState === "copied" ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />}
          {copyState === "copied" ? "Copied" : "Copy"}
        </Button>
      </div>
      {copyState === "copied" && <span role="status" className="sr-only">Token copied.</span>}
      {copyState === "failed" && <p role="alert" className="text-xs text-destructive">Could not copy. Select the token and copy it manually.</p>}
      <p className="text-xs leading-relaxed text-muted-foreground">
        Send it as an Authorization bearer header over HTTPS. It never appears in this list again; only its name and prefix remain.
      </p>
    </div>
    <div className="flex justify-end border-t pt-4">
      <Button onClick={() => { setCreated(null); onDone(); }}>Done</Button>
    </div>
  </>;

  const options = expiryOptions(maxDays, defaultDays);
  return <>
    <header>
      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold tracking-tight">New API token</h2>
      <p className="mt-1 text-sm text-muted-foreground">Create a named, expiring token that acts as you within its permissions and repositories.</p>
    </header>
    {reauthRequired && <div role="alert" className="rounded-md border border-amber-700/25 bg-amber-700/5 p-4 text-sm">
      <p className="font-medium">Sign in with GitHub again to create a token.</p>
      <p className="mt-1 leading-relaxed text-muted-foreground">
        Token creation needs a GitHub sign-in from the last 15 minutes. Your existing session keeps working elsewhere, and revoking tokens stays available.
      </p>
      <Button asChild variant="outline" size="sm" className="mt-3">
        <a href={loginHref}>Sign in with GitHub</a>
      </Button>
    </div>}
    <form aria-label="New API token" className="space-y-6" onSubmit={(event) => void submit(event)}>
      {!!error && <div ref={errorSummary} tabIndex={-1}><ErrorNotice error={error} /></div>}
      {lost && <div role="alert" className="rounded-md border border-amber-700/25 bg-amber-700/5 p-4 text-sm">
        <p className="font-medium">The request did not complete.</p>
        <p className="mt-1 leading-relaxed text-muted-foreground">
          A token named “{lost}” may have been created. Do not submit again right away: check the token list for “{lost}” first,
          revoke any unused duplicate, then create another if needed.
        </p>
      </div>}
      <section aria-labelledby="token-basics-heading" className="space-y-4">
        <h3 id="token-basics-heading" className="text-base font-semibold">Name and expiration</h3>
        <div className="grid gap-4 @min-[28rem]/settings:grid-cols-2">
          <Field
            label="Token name"
            hint="A name you will recognize in the token list, such as Noodle on laptop."
          >
            {(fieldId) => <Input id={fieldId} required maxLength={100} value={name}
              onChange={(event) => setName(event.target.value)} placeholder="noodle-laptop" />}
          </Field>
          <Field label="Expires in">
            {(fieldId) => <select id={fieldId} value={expiresInDays} onChange={(event) => setExpiresInDays(event.target.value)}
              className="h-9 w-full rounded-md border border-input bg-card px-3 text-base shadow-xs md:text-sm">
              {options.map((days) => <option key={days} value={days}>{days} days{days === defaultDays ? " (default)" : ""}</option>)}
            </select>}
          </Field>
        </div>
      </section>
      <fieldset className="space-y-4">
        <legend className="text-base font-semibold">Permissions</legend>
        {!permissions.length && <p className="text-sm text-muted-foreground">No permissions are available right now. Try again later.</p>}
        <div className="divide-y rounded-lg border bg-card">
          {groupPermissions(permissions).map(([group, items]) => <section key={group} aria-label={`${group} permissions`} className="space-y-2 p-4 sm:px-5">
            <h3 className="text-sm font-semibold capitalize">{group}</h3>
            {items.some((item) => runScope(item.id)) && <p className="text-xs leading-relaxed text-muted-foreground">
              Run permissions can start model-assisted work and publish GitHub comments. Provider usage applies.
            </p>}
            {items.some((item) => item.id.endsWith(":write")) && <p className="text-xs leading-relaxed text-muted-foreground">
              Write permissions change saved state and can update GitHub threads.
            </p>}
            {items.map((item) => <label key={item.id} className="flex min-h-11 cursor-pointer items-start gap-3 text-sm sm:min-h-8">
              <Checkbox
                className="mt-0.5"
                checked={scopes.includes(item.id)}
                onCheckedChange={() => toggleScope(item.id)}
                aria-describedby={`${id}-${item.id}-hint`}
              />
              <span className="min-w-0">
                <span className="block font-medium">{item.label} <span className="font-mono text-xs font-normal text-muted-foreground">{item.id}</span></span>
                <span id={`${id}-${item.id}-hint`} className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{item.description}</span>
              </span>
            </label>)}
          </section>)}
        </div>
      </fieldset>
      <fieldset className="space-y-4">
        <legend className="text-base font-semibold">Repositories</legend>
        <p className="text-xs leading-relaxed text-muted-foreground">Only these repositories. New repositories are never added automatically.</p>
        {!repositories.length && <p className="text-sm text-muted-foreground">No repositories are available. Ask your administrator to connect one.</p>}
        <div className="divide-y rounded-lg border bg-card">
          {repositories.map((repo) => <label key={repo.id} className="flex min-h-11 cursor-pointer items-center gap-3 px-4 py-3 text-sm sm:px-5">
            <Checkbox
              checked={repositoryIds.includes(repo.id)}
              onCheckedChange={() => toggleRepository(repo.id)}
            />
            <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{repo.full_name}</span>
          </label>)}
        </div>
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2 pt-4">
        <Button type="button" variant="outline" disabled={pending} onClick={onDone}>Cancel</Button>
        <Button type="submit" disabled={pending || reauthRequired}>
          {pending ? "Creating…" : "Create token"}
        </Button>
      </div>
    </form>
  </>;
}
