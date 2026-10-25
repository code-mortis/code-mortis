import { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown, Copy, KeyRound, Plus } from "lucide-react";
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
const loginHref = `/api/v1/auth/login?return_to=${encodeURIComponent(RETURN_TO)}`;

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
  const [creating, setCreating] = useState(false);
  const [createPending, setCreatePending] = useState(false);
  const [revoking, setRevoking] = useState<ApiTokenItem | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const createHeading = useRef<HTMLHeadingElement>(null);
  const repoNames = new Map((repos.data ?? []).map((repo) => [repo.id, repo.full_name]));

  function restoreFocus(event: Event) {
    event.preventDefault();
    returnFocus.current?.focus();
  }

  function closeCreate() {
    if (createPending) return;
    setCreating(false);
    void list.refetch();
  }

  async function revoke(token: ApiTokenItem) {
    await revokeAction.mutateAsync({
      path: `/api-tokens/${token.id}`,
      method: "DELETE",
    });
    returnFocus.current = createHeading.current;
    setRevoking(null);
  }

  const permissions = list.data?.permissions ?? [];
  const maxDays = list.data?.max_expiration_days ?? 365;
  const activeCount = list.data?.active_count;

  const renderTokens = (data: ApiTokenList | undefined, fetching: boolean, onPage: (page: number) => void, label: string) => (
    data && (data.items.length > 0 || data.page > 1) ? <div className="divide-y rounded-lg border bg-card">
      {data.items.map((token) => {
        const known = token.repository_ids.filter((id) => repoNames.has(id));
        const unknown = token.repository_ids.filter((id) => !repoNames.has(id));
        return <article key={token.id} aria-label={`API token ${token.name}`}
          className="space-y-4 px-4 py-4 text-sm sm:px-5">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-start gap-3">
                <KeyRound aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h4 className="min-w-0 break-words font-medium">{token.name}</h4>
                  <Status value={token.status} label={token.status === "active" ? "Active" : token.status === "expired" ? "Expired" : "Revoked"} />
                </div>
              </div>
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground" title="Token prefix">
                <span className="sr-only">Prefix: </span>{token.prefix}…
              </p>
            </div>
            {token.status === "active" && <Button variant="outline" size="sm" disabled={revokeAction.isPending}
              onClick={(event) => {
                returnFocus.current = event.currentTarget;
                revokeAction.reset();
                setRevoking(token);
              }}>
              Revoke
            </Button>}
          </div>
          <dl className="space-y-2 text-xs leading-relaxed">
            <div className="grid gap-1 @min-[28rem]/settings:grid-cols-[6rem_minmax(0,1fr)] @min-[28rem]/settings:gap-4">
              <dt className="text-muted-foreground">Permissions</dt>
              <dd className="min-w-0 break-words">{token.scopes.join(", ") || "None"}</dd>
            </div>
            <div className="grid gap-1 @min-[28rem]/settings:grid-cols-[6rem_minmax(0,1fr)] @min-[28rem]/settings:gap-4">
              <dt className="text-muted-foreground">Repositories</dt>
              <dd className="min-w-0 break-words">
                {repos.isPending ? "Loading repositories…" : !repos.data ? "Repository names unavailable" : <>
                  {known.map((id) => repoNames.get(id)).join(", ") || (unknown.length ? "" : "None")}
                  {unknown.length > 0 && <span className="text-muted-foreground">{known.length > 0 ? "; " : ""}{unknown.map((id) => `#${id}`).join(", ")} unavailable</span>}
                </>}
              </dd>
            </div>
          </dl>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <p>Created {date(token.created_at)}</p>
            <p>Expires {date(token.expires_at)}</p>
            <p>{token.last_used_at ? <>Last used {date(token.last_used_at)} (approximate)</> : "Never used"}</p>
          </div>
        </article>;
      })}
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
      <Button disabled={!list.data || revokeAction.isPending} onClick={(event) => {
        returnFocus.current = event.currentTarget;
        setCreating(true);
      }}><Plus aria-hidden="true" className="size-4" />New token</Button>
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
        Store each token as a secret and send it as a bearer credential. Closing the one-time panel loses the secret permanently; revoke the unused token and create another. Revoked tokens stop new requests immediately while queued cleanup finishes in the background.
      </p>
    </section>

    <Dialog open={creating} onOpenChange={(open) => { if (!open) closeCreate(); }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg" onCloseAutoFocus={restoreFocus}>
        <CreateTokenDialog
          permissions={permissions}
          permissionsPending={list.isPending}
          maxDays={maxDays}
          defaultDays={list.data?.default_expiration_days ?? 30}
          reauthRequired={!!list.data?.reauth_required}
          repositories={repos.data ?? []}
          reposPending={repos.isPending}
          pending={createPending}
          setPending={setCreatePending}
          onDone={closeCreate}
        />
      </DialogContent>
    </Dialog>

    <Dialog open={!!revoking} onOpenChange={(open) => { if (!open && !revokeAction.isPending) { setRevoking(null); revokeAction.reset(); } }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto" onCloseAutoFocus={restoreFocus}>
        <DialogHeader>
          <DialogTitle>Revoke {revoking?.name ? `“${revoking.name}”` : "token"}?</DialogTitle>
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
      </DialogContent>
    </Dialog>
  </section>;
}

function CreateTokenDialog({ permissions, permissionsPending, maxDays, defaultDays, reauthRequired, repositories, reposPending, pending, setPending, onDone }: {
  permissions: ApiTokenPermission[];
  permissionsPending: boolean;
  maxDays: number;
  defaultDays: number;
  reauthRequired: boolean;
  repositories: Repository[];
  reposPending: boolean;
  pending: boolean;
  setPending: (pending: boolean) => void;
  onDone: () => void;
}) {
  const id = useId();
  const [name, setName] = useState("");
  const [expiresInDays, setExpiresInDays] = useState(String(defaultDays));
  const [scopes, setScopes] = useState<string[]>(() => permissions.filter((item) => item.id.endsWith(":read")).map((item) => item.id));
  const [scopedOnce, setScopedOnce] = useState(false);
  const [repositoryIds, setRepositoryIds] = useState<number[]>([]);
  const [error, setError] = useState<unknown>();
  const [lost, setLost] = useState<string | null>(null);
  // The raw secret lives only in this component state. It is never written to
  // a query cache, toast, log, or browser storage, and is cleared on unmount.
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [copyState, setCopyState] = useState<"copied" | "failed" | null>(null);
  useEffect(() => () => setCreated(null), []);
  useEffect(() => {
    if (!scopedOnce && permissions.length) {
      setScopes(permissions.filter((item) => item.id.endsWith(":read")).map((item) => item.id));
      setScopedOnce(true);
    }
  }, [permissions, scopedOnce]);

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
    <DialogHeader>
      <DialogTitle>Token created</DialogTitle>
      <DialogDescription>
        Copy “{created.item.name}” now and store it as a secret, for example with Noodle. This secret is shown once: closing this panel loses it permanently. If the secret is lost, revoke the unused token from the list and create another.
      </DialogDescription>
    </DialogHeader>
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
    <DialogFooter>
      <Button onClick={() => { setCreated(null); onDone(); }}>Done</Button>
    </DialogFooter>
  </>;

  const options = expiryOptions(maxDays, defaultDays);
  return <>
    <DialogHeader>
      <DialogTitle>New API token</DialogTitle>
      <DialogDescription>Create a named, expiring token that acts as you within its permissions and repositories.</DialogDescription>
    </DialogHeader>
    {reauthRequired && <div role="alert" className="rounded-md border border-amber-700/25 bg-amber-700/5 p-4 text-sm">
      <p className="font-medium">Sign in with GitHub again to create a token.</p>
      <p className="mt-1 leading-relaxed text-muted-foreground">
        Token creation needs a GitHub sign-in from the last 15 minutes. Your existing session keeps working elsewhere, and revoking tokens stays available.
      </p>
      <Button asChild variant="outline" size="sm" className="mt-3">
        <a href={loginHref}>Sign in with GitHub</a>
      </Button>
    </div>}
    <form className="space-y-5" onSubmit={(event) => void submit(event)}>
      <ErrorNotice error={error} />
      {lost && <div role="alert" className="rounded-md border border-amber-700/25 bg-amber-700/5 p-4 text-sm">
        <p className="font-medium">The request did not complete.</p>
        <p className="mt-1 leading-relaxed text-muted-foreground">
          A token named “{lost}” may have been created. Do not submit again right away: check the token list for “{lost}” first,
          revoke any unused duplicate, then create another if needed.
        </p>
      </div>}
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
      <fieldset className="space-y-3">
        <legend className="text-sm font-medium">Permissions</legend>
        {permissionsPending && <Loading />}
        {!permissionsPending && !permissions.length && <p className="text-sm text-muted-foreground">No permissions are available right now. Try again later.</p>}
        {groupPermissions(permissions).map(([group, items]) => <section key={group} aria-label={`${group} permissions`} className="space-y-2 rounded-lg border bg-card p-4">
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
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Repositories</legend>
        <p className="text-xs leading-relaxed text-muted-foreground">Only these repositories. New repositories are never added automatically.</p>
        {reposPending && <Loading />}
        {!reposPending && !repositories.length && <p className="text-sm text-muted-foreground">No repositories are available. Ask your administrator to connect one.</p>}
        {repositories.map((repo) => <label key={repo.id} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border px-3 text-sm sm:min-h-8">
          <Checkbox
            checked={repositoryIds.includes(repo.id)}
            onCheckedChange={() => toggleRepository(repo.id)}
          />
          <span className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">{repo.full_name}</span>
        </label>)}
      </fieldset>
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" disabled={pending} onClick={onDone}>Cancel</Button>
        <Button type="submit" disabled={pending || reauthRequired || permissionsPending}>
          {pending ? "Creating…" : "Create token"}
        </Button>
      </div>
    </form>
  </>;
}
