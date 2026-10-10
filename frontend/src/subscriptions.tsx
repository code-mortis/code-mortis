import { useEffect, useRef, useState } from "react";
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
import { Switch } from "./components/ui/switch";
import {
  ErrorNotice,
  ExternalLink,
  Field,
  NativeSelect,
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

export function PersonalConnection({ connection }: { connection: Connection }) {
  const identity = connection.id;
  const auth = useApi<AuthState>(`/connections/${identity}/auth`, true);
  const grants = useApi<Grant[]>(`/connections/${identity}/grants`);
  const repositories = useApi<Repository[]>("/repositories");
  const action = useAction();
  const modelAction = useAction<Connection>();
  const grantAction = useAction();
  const [checkingAccount, setCheckingAccount] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [repository, setRepository] = useState("");
  const [automatic, setAutomatic] = useState(false);
  const [model, setModel] = useState(connection.model);
  const [savedModel, setSavedModel] = useState(connection.model);
  const [modelError, setModelError] = useState<unknown>();
  const trimmedModel = model.trim();
  const attempt = auth.data?.attempt;
  const connected = auth.data?.status === "connected";
  const authPending = action.isPending || modelAction.isPending || grantAction.isPending;
  const modelPending = action.isPending || modelAction.isPending;
  const grantPending = action.isPending || grantAction.isPending;
  const revoking = grantAction.variables?.method === "DELETE";
  function clearGrantFeedback() {
    if (!revoking && (grantAction.isSuccess || grantAction.isError)) grantAction.reset();
  }
  return (
    <div className="min-w-0 space-y-6">
      <section className="space-y-4" aria-label="Account connection">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Account connection</h3>
          <div role="status"><Status {...accountStatus(auth.data?.status, auth.isPending || checkingAccount, auth.isError)} /></div>
        </div>
        <ErrorNotice error={auth.error || action.error || auth.data?.message} />
        {auth.data?.account?.email && <p className="break-words text-sm">Signed in as {auth.data.account.email}</p>}
        <p className="text-sm text-muted-foreground">
          Your official client runs on this server. Your computer can be off
          during reviews. No API connection is used as a fallback.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
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
          </Button>
          <Button
            variant="outline"
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
            Check status
          </Button>
          {(connected || auth.data?.message) && (
            <Button
              variant="outline"
              disabled={authPending}
              onClick={() => {
                setConsoleOpen(false);
                action.mutate({ path: `/connections/${identity}/disconnect` });
              }}
            >
              Disconnect and revoke access
            </Button>
          )}
        </div>
        {attempt?.status === "pending" && attempt.verificationUrl && (
          <div className="space-y-3 rounded-md border p-4">
            <p className="text-sm">
              Open the official authorization page and enter this code:
            </p>
            <p className="select-all font-mono text-xl tracking-wider">
              {attempt.userCode}
            </p>
            <ExternalLink href={attempt.verificationUrl}>
              Authorize with ChatGPT
            </ExternalLink>
            <p className="text-xs text-muted-foreground">
              Device code authorization must be enabled in your ChatGPT security
              settings and permitted by your workspace.
            </p>
            <Button
              variant="ghost"
              size="sm"
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
      <form aria-label="Default model" className="space-y-4 border-t pt-5" onSubmit={async (event) => {
        event.preventDefault();
        if (modelPending) return;
        setModelError(undefined);
        try {
          const saved = await modelAction.mutateAsync({
            path: `/connections/${identity}`, method: "PUT",
            body: { name: connection.name, kind: connection.kind, model: trimmedModel },
          });
          if (typeof saved?.model !== "string" || !saved.model.trim())
            throw new Error("The server did not return a valid model. Please retry.");
          setSavedModel(saved.model);
          setModel(saved.model);
        } catch (error) { setModelError(error); }
      }}>
        <h3 className="text-sm font-semibold">Default model</h3>
        <ErrorNotice error={modelError} />
        <ModelPicker
          value={model} onChange={(value) => {
            if (modelAction.isSuccess || modelAction.isError) modelAction.reset();
            setModelError(undefined);
            setModel(value);
          }} required disabled={modelPending}
          path={connected ? `/connections/${identity}/models` : undefined}
          context={`${identity}:${connected}`}
          hint="Sign in to load this connection’s models, or enter a custom model ID."
        />
        {!savedModel && <p className="text-sm text-muted-foreground">Choose a model to finish setup and enable reviews.</p>}
        <Button type="submit" disabled={modelPending || !trimmedModel || trimmedModel === savedModel.trim()}>
          {modelAction.isPending ? "Saving…" : "Save model"}
        </Button>
        {modelAction.isSuccess && !modelError && model === savedModel && <p role="status" className="text-xs text-muted-foreground">Model saved.</p>}
      </form>
      <section aria-label="Repository authorization" className="space-y-4 border-t pt-5">
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
                })
              }
            >
              {grantAction.isPending && revoking && grantAction.variables?.path === `/connections/${identity}/grants/${grant.repository_id}` ? "Revoking…" : "Revoke"}
            </Button>
          </div>
        ))}</div>}
        {revoking && <ErrorNotice error={grantAction.error} />}
        {revoking && grantAction.isSuccess && <p role="status" className="text-xs text-muted-foreground">Authorization revoked.</p>}
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (grantPending || !repository) return;
            grantAction.mutate({
              path: `/connections/${identity}/grants/${repository}`,
              method: "PUT",
              body: { automatic },
            }, {
              onSuccess: () => { setRepository(""); setAutomatic(false); },
            });
          }}
        >
          {!revoking && <ErrorNotice error={grantAction.error} />}
          {repositories.isPending && <p role="status" className="text-sm text-muted-foreground">Loading repositories…</p>}
          {repositories.isSuccess && !repositories.data?.length && <p className="text-sm text-muted-foreground">No accessible repositories.</p>}
          <Field label="Repository">
            {(id) => (
              <NativeSelect
                id={id}
                required
                disabled={grantPending || repositories.isPending || !repositories.data?.length}
                value={repository}
                onChange={(event) => {
                  clearGrantFeedback();
                  setRepository(event.target.value);
                }}
              >
                <option value="">Choose a repository</option>
                {repositories.data?.map((repo) => (
                  <option key={repo.id} value={repo.id}>
                    {repo.full_name}
                  </option>
                ))}
              </NativeSelect>
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
          <Button disabled={grantPending || !repository} type="submit">
            {grantAction.isPending && !revoking ? "Saving…" : "Save authorization"}
          </Button>
          {!revoking && grantAction.isSuccess && <p role="status" className="text-xs text-muted-foreground">Repository authorization updated.</p>}
        </form>
      </section>
    </div>
  );
}
