import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import {
  api,
  useAction,
  useApi,
  type Connection,
  type Repository,
} from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import { ModelPicker } from "./components/model-picker";
import { SearchSelect } from "./components/search-select";
import { Switch } from "./components/ui/switch";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import {
  ErrorNotice,
  ExternalLink,
  Field,
  Status,
} from "./components/common";

type AuthState = {
  status: string;
  message?: string;
  account?: { email?: string; planType?: string; subscriptionType?: string };
  limits?: unknown;
  attempt?: {
    status: string;
    verificationUrl?: string;
    userCode?: string;
    message?: string;
  };
};
type Grant = { repository_id: number; automatic: boolean };

function accountStatus(status: string | undefined, pending: boolean, failed: boolean) {
  if (pending) return { value: "running", label: "Checking account…" };
  if (failed && !status) return { value: "failed", label: "Could not check account" };
  let label = "Not connected";
  if (status === "connected") label = "Connected";
  if (status === "unavailable") label = "Runner unavailable";
  if (failed) return { value: "action_required", label: `Last known: ${label}` };
  if (status === "unavailable") return { value: "action_required", label };
  return { value: status ?? "disconnected", label };
}

function LoginConsole({
  identity,
  onClose,
}: {
  identity: string;
  onClose: () => void;
}) {
  const socket = useRef<WebSocket | null>(null);
  const [output, setOutput] = useState("");
  const [input, setInput] = useState("");
  const [status, setStatus] = useState(
    "Opening the official Claude Code login…",
  );
  const [error, setError] = useState<unknown>();
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let disposed = false;
    api<{ ticket: string }>(`/connections/${identity}/console-ticket`, "POST")
      .then(({ ticket }) => {
        if (disposed) return;
        const url = new URL(
          `/api/v1/connections/${identity}/console`,
          location.href,
        );
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const ws = new WebSocket(url);
        socket.current = ws;
        ws.onopen = () => {
          ws.send(JSON.stringify({ ticket }));
          setReady(true);
          setStatus("Complete the official login below.");
        };
        ws.onmessage = ({ data }) => {
          // Render terminal output as text, never HTML or terminal escape commands.
          const text = String(data)
            .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, "")
            .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "");
          setOutput((old) => (old + text).slice(-40000));
        };
        ws.onerror = () =>
          setError(
            new Error(
              "The login console could not connect. Reopen it to try again. If it keeps failing, ask the administrator to check the account sign-in service.",
            ),
          );
        ws.onclose = () => {
          setReady(false);
          setStatus(
            "The official login process ended. Check account status or reconnect.",
          );
        };
      })
      .catch(setError);
    return () => {
      disposed = true;
      socket.current?.close();
    };
  }, [identity]);
  const links = [
    ...new Set(
      output.match(
        /https:\/\/(?:claude\.ai|claude\.com|console\.anthropic\.com)\/[^\s\x00-\x1f]+/g,
      ) ?? [],
    ),
  ];
  return (
    <section
      className="min-w-0 space-y-3 rounded-md border p-4"
      aria-label="Official Claude Code login console"
    >
      <p role="status" className="text-sm">
        {status}
      </p>
      <ErrorNotice error={error} />
      {links.map((link) => (
        <p key={link}>
          <ExternalLink href={link}>Open Claude authorization</ExternalLink>
        </p>
      ))}
      <pre
        tabIndex={0}
        aria-label="Claude Code login output"
        className="max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-3 font-mono text-xs"
      >
        {output || "Waiting for the official client…"}
      </pre>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault();
          socket.current?.send(input + "\r");
          setInput("");
        }}
      >
        <Field
          label="Response to the official login prompt"
          hint="Paste the authorization code or answer requested by Claude Code. Input is not saved."
        >
          {(id) => (
            <Input
              id={id}
              type="password"
              autoComplete="off"
              spellCheck={false}
              disabled={!ready}
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
          )}
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!ready}>
            Send to Claude Code
          </Button>
          <Button type="button" variant="outline" onClick={onClose}>
            Close console
          </Button>
        </div>
      </form>
    </section>
  );
}

export function PersonalConnection({ connection, initialTab, onDone, onSavingChange }: {
  connection: Connection;
  initialTab: "general" | "account";
  onDone: () => void;
  onSavingChange: (saving: boolean) => void;
}) {
  const identity = connection.id;
  const settingUp = !connection.model.trim();
  const provider = connection.kind === "chatgpt" ? "ChatGPT" : "Claude Code";
  const [tab, setTab] = useState<string>(initialTab);
  const auth = useApi<AuthState>(`/connections/${identity}/auth`, true);
  const grants = useApi<Grant[]>(`/connections/${identity}/grants`, false, tab === "repositories");
  const repositories = useApi<Repository[]>("/repositories", false, tab === "repositories");
  const action = useAction();
  const connectionAction = useAction<Connection>();
  const grantAction = useAction();
  const [checkingAccount, setCheckingAccount] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [repository, setRepository] = useState("");
  const [automatic, setAutomatic] = useState(false);
  const [model, setModel] = useState(connection.model);
  const [savedModel, setSavedModel] = useState(connection.model);
  const [name, setName] = useState(connection.name);
  const [savedName, setSavedName] = useState(connection.name);
  const [promptCeiling, setPromptCeiling] = useState(String(connection.options?.prompt_token_ceiling ?? 58888));
  const [savedCeiling, setSavedCeiling] = useState(Number(promptCeiling));
  const [connectionError, setConnectionError] = useState<unknown>();
  const trimmedModel = model.trim();
  const attempt = auth.data?.attempt;
  const awaitingAuthorization = attempt?.status === "pending" && !!attempt.verificationUrl;
  const connected = auth.data?.status === "connected";
  const setupComplete = connected && !auth.isError && !!savedModel.trim();
  const heading = useRef<HTMLHeadingElement>(null);
  const generalTab = useRef<HTMLButtonElement>(null);
  const repositoriesTab = useRef<HTMLButtonElement>(null);
  const wasConnected = useRef(false);
  const availableRepositories = grants.isSuccess ? (repositories.data ?? []).filter((repo) =>
    !grants.data?.some((grant) => grant.repository_id === repo.id),
  ) : [];
  useEffect(() => {
    if (grants.data?.some((grant) => String(grant.repository_id) === repository)) {
      setRepository("");
      setAutomatic(false);
    }
  }, [grants.data, repository]);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    if (!savedModel.trim() && connected && !wasConnected.current && !auth.isError) {
      setTab("general");
      generalTab.current?.focus();
    }
    wasConnected.current = connected;
  }, [savedModel, connected, auth.isError]);
  const authPending = action.isPending || connectionAction.isPending || grantAction.isPending;
  const generalPending = action.isPending || connectionAction.isPending;
  const grantPending = action.isPending || grantAction.isPending;
  const revoking = grantAction.variables?.method === "DELETE";
  function clearGrantFeedback() {
    if (!revoking && (grantAction.isSuccess || grantAction.isError)) grantAction.reset();
  }
  function clearConnectionFeedback() {
    if (connectionAction.isSuccess || connectionAction.isError) connectionAction.reset();
    setConnectionError(undefined);
  }
  const checkStatus = (connected || attempt?.status === "pending" || consoleOpen || auth.isError || auth.data?.status === "unavailable") && <Button
    variant={awaitingAuthorization && !connected ? "ghost" : !connected ? "link" : "outline"}
    size={awaitingAuthorization && !connected ? "sm" : "default"}
    className={!connected && !awaitingAuthorization ? "px-0" : undefined}
    aria-label="Check status"
    aria-busy={checkingAccount}
    disabled={action.isPending || auth.isPending || checkingAccount}
    onClick={async () => {
      setCheckingAccount(true);
      try {
        await auth.refetch({ cancelRefetch: false });
      } finally {
        setCheckingAccount(false);
      }
    }}
  >
    {awaitingAuthorization && !connected && <RefreshCw aria-hidden="true" className={`size-3.5 ${checkingAccount ? "motion-safe:animate-spin" : ""}`} />}
    {checkingAccount ? "Checking…" : "Check status"}
  </Button>;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-5">
      <DialogHeader className="shrink-0">
        <DialogTitle ref={heading} tabIndex={-1} className="break-words">
          Edit subscription
        </DialogTitle>
        <DialogDescription className="break-words">
          {savedName} · {provider} subscription
        </DialogDescription>
      </DialogHeader>
      <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-5">
        <TabsList className="w-full shrink-0" aria-label="Subscription sections">
          <TabsTrigger ref={generalTab} value="general">General</TabsTrigger>
          <TabsTrigger value="account">Account</TabsTrigger>
          <TabsTrigger ref={repositoriesTab} value="repositories">Repositories</TabsTrigger>
        </TabsList>
        <TabsContent value="account" forceMount className="-m-1 min-h-0 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <section className="space-y-4" aria-label="Account connection">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">Account connection</h3>
              {(connected || !awaitingAuthorization) && <div role="status"><Status {...accountStatus(auth.data?.status, auth.isPending || checkingAccount, auth.isError)} /></div>}
            </div>
            <ErrorNotice error={auth.error || action.error || auth.data?.message} />
            {auth.data?.account?.email && <p className="break-words text-sm">Signed in as {auth.data.account.email}</p>}
            {!connected && !awaitingAuthorization && <p className="text-sm leading-relaxed text-muted-foreground">
              Use your {provider} account for reviews.{settingUp && " After signing in, choose a default model."}
            </p>}
            {connected && !savedModel.trim() && <Button onClick={() => { setTab("general"); generalTab.current?.focus(); }}>Choose model</Button>}
            {(connected || !awaitingAuthorization || auth.data?.message) && <div className="flex flex-wrap gap-2">
              {(connected || !awaitingAuthorization) &&
                <Button
                  variant={connected ? "outline" : "default"}
                  className={!connected ? "w-full" : undefined}
                  disabled={authPending}
                  onClick={async () => {
                    const result = await action
                      .mutateAsync({ path: `/connections/${identity}/auth` })
                      .then(() => true)
                      .catch(() => false);
                    if (result && connection.kind === "claude_code")
                      setConsoleOpen(true);
                  }}
                >
                  {connected
                    ? "Reconnect"
                    : connection.kind === "chatgpt"
                      ? "Connect ChatGPT"
                      : "Connect Claude Code"}
                </Button>}
              <div className="flex flex-wrap gap-2">
                {(connected || !awaitingAuthorization) && checkStatus}
                {(connected || auth.data?.message) && (
                  <Button
                    variant="destructive"
                    disabled={authPending}
                    onClick={() => {
                      setConsoleOpen(false);
                      action.mutate({ path: `/connections/${identity}/disconnect` });
                    }}
                  >
                    Revoke access
                  </Button>
                )}
              </div>
            </div>}
            {attempt?.status === "pending" && attempt.verificationUrl && (
              <div className="space-y-4">
                <p className="text-sm">
                  Enter this code on the ChatGPT authorization page.
                </p>
                <p className="select-all rounded-md bg-muted p-4 text-center font-mono text-2xl tracking-wider">
                  {attempt.userCode}
                </p>
                <Button asChild className="w-full">
                  <a href={attempt.verificationUrl} target="_blank" rel="noreferrer">
                    Authorize with ChatGPT <ArrowUpRight aria-hidden="true" className="size-4" />
                  </a>
                </Button>
                <p className="text-xs text-muted-foreground">
                  Device code authorization must be enabled in your ChatGPT security
                  settings and permitted by your workspace.
                </p>
                {!connected && <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                  <p role="status" className="text-xs text-muted-foreground">
                    {checkingAccount ? "Checking account…" : auth.isError ? "Could not check sign-in" : "Waiting for sign-in"}
                  </p>
                  {checkStatus}
                </div>}
                <Button
                  variant="outline"
                  disabled={authPending}
                  onClick={() =>
                    action.mutate({
                      path: `/connections/${identity}/auth`,
                      method: "DELETE",
                    })
                  }
                >
                  Cancel authorization
                </Button>
              </div>
            )}
            {attempt?.message && (
              <p role="alert" className="text-sm">
                {attempt.message}
              </p>
            )}
            {attempt?.status === "expired" && (
              <p role="alert" className="text-sm">
                Authorization expired. Start a new connection attempt.
              </p>
            )}
            {consoleOpen && (
              <LoginConsole
                identity={identity}
                onClose={() => {
                  setConsoleOpen(false);
                  void auth.refetch();
                }}
              />
            )}
            <p className="text-xs leading-relaxed text-muted-foreground">
              Your official client runs on this server. Your computer can be off
              during reviews. No API connection is used as a fallback.
            </p>
            {!!auth.data?.limits && (
              <details className="text-sm">
                <summary className="cursor-pointer">
                  Subscription usage limits
                </summary>
                <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all rounded bg-muted p-3 text-xs">
                  {JSON.stringify(auth.data.limits, null, 2)}
                </pre>
              </details>
            )}
          </section>
        </TabsContent>
        <TabsContent value="general" forceMount className="-m-1 min-h-0 space-y-4 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <form aria-label="Subscription settings" className="space-y-4" onSubmit={async (event) => {
            event.preventDefault();
            if (generalPending) return;
            onSavingChange(true);
            setConnectionError(undefined);
            try {
              const ceiling = Number(promptCeiling);
              if (!Number.isInteger(ceiling) || ceiling < 4096 || ceiling > 2000000)
                throw new Error("Prompt ceiling must be an integer between 4096 and 2000000.");
              const saved = await connectionAction.mutateAsync({
                path: `/connections/${identity}`, method: "PUT",
                body: { name, kind: connection.kind, model: trimmedModel,
                  options: { ...connection.options, prompt_token_ceiling: ceiling } },
              });
              if (typeof saved?.model !== "string" || (trimmedModel && !saved.model.trim()))
                throw new Error("The server did not return a valid model. Please retry.");
              setSavedModel(saved.model);
              setModel(saved.model);
              setSavedName(saved.name);
              setName(saved.name);
              setSavedCeiling(ceiling);
              toast.success("Connection saved.");
            } catch (error) { setConnectionError(error); }
            finally { onSavingChange(false); }
          }}>
            <ErrorNotice error={connectionError} />
            <Field label="Connection name">
              {(id) => <Input id={id} required maxLength={100} disabled={generalPending} value={name}
                onChange={(event) => { clearConnectionFeedback(); setName(event.target.value); }} />}
            </Field>
            <h3 className="text-sm font-semibold">Default model</h3>
            <ModelPicker
              value={model} onChange={(value) => {
                clearConnectionFeedback();
                setModel(value);
              }} enabled={tab === "general"} disabled={generalPending}
              path={connected ? `/connections/${identity}/models` : undefined}
              context={`${identity}:${connected}`}
              hint={connected ? "Choose a model or enter a custom model ID." : "Sign in to load this connection’s models, or enter a custom model ID."}
            />
            <Field label="Prompt ceiling (tokens)"
              hint="Each prompt, including instructions and output schema, is checked conservatively against 80% of this value. The total review Token budget stays separate.">
              {(id) => <Input id={id} required type="number" min={4096} max={2000000} step={1} disabled={generalPending}
                value={promptCeiling} onChange={(event) => { clearConnectionFeedback(); setPromptCeiling(event.target.value); }} />}
            </Field>
            <Button type="submit" disabled={generalPending || !name.trim() || (trimmedModel === savedModel.trim() && name.trim() === savedName.trim() && Number(promptCeiling) === savedCeiling)}>
              {connectionAction.isPending ? "Saving…" : "Save changes"}
            </Button>
          </form>
          {settingUp && setupComplete && <div className="space-y-3 border-t pt-5">
            <p role="status" className="text-sm text-muted-foreground">You can start manual reviews in enabled repositories.</p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => { setTab("repositories"); repositoriesTab.current?.focus(); }}>Set up automatic reviews</Button>
            </div>
          </div>}
        </TabsContent>
        <TabsContent value="repositories" forceMount className="-m-1 min-h-0 overflow-x-hidden overflow-y-auto p-1 data-[state=inactive]:hidden">
          <section aria-label="Repository authorization" className="space-y-4">
            <h3 className="text-sm font-semibold">Repository authorization</h3>
            <ErrorNotice error={grants.error} />
            {grants.isError && <Button type="button" variant="outline" size="sm" disabled={grants.isFetching} onClick={() => { void grants.refetch(); }}>
              Retry authorizations
            </Button>}
            <ErrorNotice error={repositories.error} />
            {repositories.isError && <Button type="button" variant="outline" size="sm" disabled={repositories.isFetching} onClick={() => { void repositories.refetch(); }}>
              Retry repositories
            </Button>}
            <p className="text-sm text-muted-foreground">
              Only you can grant use of this subscription. An administrator must
              also enable the repository and select this connection. One review runs
              at a time.
            </p>
            {grants.isPending && <p role="status" className="text-sm text-muted-foreground">Loading authorizations…</p>}
            {grants.isSuccess && !grants.data?.length && <p className="text-sm text-muted-foreground">No repositories authorized yet.</p>}
            {!!grants.data?.length && <div className="divide-y rounded-lg border bg-card">{grants.data.map((grant) => (
              <div
                key={grant.repository_id}
                className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm"
              >
                <span className="break-all">
                  {repositories.data?.find(
                    (repo) => repo.id === grant.repository_id,
                  )?.full_name ?? `Repository ${grant.repository_id}`}
                  <span className="block text-xs text-muted-foreground">
                    {grant.automatic
                      ? "Automatic reviews authorized"
                      : "Manual use only"}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={grantPending}
                  onClick={() =>
                    grantAction.mutate({
                      path: `/connections/${identity}/grants/${grant.repository_id}`,
                      method: "DELETE",
                    }, { onSuccess: () => toast.success("Authorization revoked.") })
                  }
                >
                  {grantAction.isPending && revoking && grantAction.variables?.path === `/connections/${identity}/grants/${grant.repository_id}` ? "Revoking…" : "Revoke"}
                </Button>
              </div>
            ))}</div>}
            {revoking && <ErrorNotice error={grantAction.error} />}
            <form
              className="space-y-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (grantPending || !availableRepositories.some((repo) => String(repo.id) === repository)) return;
                grantAction.mutate({
                  path: `/connections/${identity}/grants/${repository}`,
                  method: "PUT",
                  body: { automatic },
                }, {
                  onSuccess: () => { setRepository(""); setAutomatic(false); toast.success("Repository authorization updated."); },
                });
              }}
            >
              {!revoking && <ErrorNotice error={grantAction.error} />}
              {repositories.isPending && <p role="status" className="text-sm text-muted-foreground">Loading repositories…</p>}
              {repositories.isSuccess && !repositories.data?.length && <p className="text-sm text-muted-foreground">No accessible repositories.</p>}
              {repositories.isSuccess && !!repositories.data?.length && grants.isSuccess && !availableRepositories.length && <p className="text-sm text-muted-foreground">All accessible repositories are authorized.</p>}
              <Field label="Repository">
                {(id) => (
                  <SearchSelect
                    id={id}
                    required
                    disabled={grantPending || repositories.isPending || !availableRepositories.length}
                    value={repository}
                    placeholder="Choose a repository"
                    onChange={(value) => {
                      clearGrantFeedback();
                      setRepository(value);
                    }}
                    options={availableRepositories.map((repo) => ({ value: String(repo.id), label: repo.full_name }))}
                  />
                )}
              </Field>
              <Field
                label="I authorize automatic reviews with my subscription"
                hint="New commits in this repository may consume your plan's usage until you revoke this authorization."
              >
                {(id) => (
                  <Switch
                    id={id}
                    disabled={grantPending}
                    checked={automatic}
                    onCheckedChange={(checked) => {
                      clearGrantFeedback();
                      setAutomatic(checked);
                    }}
                  />
                )}
              </Field>
              <Button disabled={grantPending || !availableRepositories.some((repo) => String(repo.id) === repository)} type="submit">
                {grantAction.isPending && !revoking ? "Saving…" : "Save authorization"}
              </Button>
            </form>
          </section>
        </TabsContent>
      </Tabs>
      <DialogFooter className="shrink-0 border-t pt-4">
        <Button variant="outline" disabled={connectionAction.isPending} onClick={onDone}>Done</Button>
      </DialogFooter>
    </div>
  );
}
