import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, useAction, type Schema } from "../api";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";
import { Empty, ErrorNotice, ExternalLink, Loading, Status } from "./common";

type Installation = Schema["InstallationOut"];
const pending = (item: Installation) => ["queued", "running"].includes(item.operation_status ?? "");

export function GitHubInstallations() {
  const query = useQuery({
    queryKey: ["/installations"],
    queryFn: () => api<Installation[]>("/installations"),
    refetchOnWindowFocus: "always",
    refetchInterval: (query) => query.state.data?.some(pending) ? 3000 : false,
    retry: false,
  });
  const action = useAction();
  const [uninstalling, setUninstalling] = useState<Installation | null>(null);
  const [message, setMessage] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const connected = query.data?.filter((item) => item.connected) ?? [];
  const available = query.data?.filter((item) => !item.connected) ?? [];

  function connect(item: Installation) {
    setMessage("");
    action.mutate({ path: `/installations/${item.id}` }, {
      onSuccess: () => setMessage(`Repository sync queued for ${item.account}. Updated repositories will appear when synchronization finishes.`),
    });
  }

  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h3 ref={heading} tabIndex={-1} className="font-semibold">Connected installations</h3>
        <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>
          {query.isFetching ? "Refreshing…" : "Refresh"}
        </Button>
      </div>
      <ErrorNotice error={query.error || action.error} />
      {message && <p role="status" className="mb-4 text-sm text-muted-foreground">{message}</p>}
      {query.isPending ? <Loading /> : connected.length ? (
        <div className="mb-8 divide-y rounded-lg border bg-card">
          {connected.map((item) => {
            const busy = pending(item);
            const removed = item.status === "uninstalled";
            const failed = item.operation_status === "failed" && !removed;
            const status = removed ? "uninstalled" : busy ? "uninstalling" : failed ? "failed" : item.status;
            return (
              <article key={item.id} aria-label={item.account} className="space-y-3 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="min-w-0 break-words text-sm font-semibold">{item.account}</h4>
                  <Status value={status} />
                </div>
                <p className="text-sm text-muted-foreground">
                  {removed ? "Uninstalled from GitHub. Saved review history is retained."
                    : busy ? "Uninstall requested. Waiting for GitHub to confirm removal."
                    : failed ? `Uninstall did not complete. Retry to check GitHub and finish removal.${item.active ? "" : " Reviews are paused."}`
                    : `${item.repository_selection === "all" ? "All repositories" : "Selected repositories"} on GitHub.${item.active ? "" : " Reviews are paused."}`}
                </p>
                {failed && item.operation_error && <p role="alert" className="break-words text-sm text-destructive">{item.operation_error}</p>}
                {!removed && (
                  <div className="flex flex-wrap items-center gap-2">
                    {item.settings_url && <ExternalLink href={item.settings_url}>Manage repositories</ExternalLink>}
                    <Button size="sm" variant="outline" disabled={action.isPending || query.isError || !item.can_connect}
                      onClick={() => connect(item)}>Resync repositories</Button>
                    <Button size="sm" variant="outline" disabled={action.isPending || query.isError || !item.can_uninstall}
                      onClick={(event) => { returnFocus.current = event.currentTarget; action.reset(); setUninstalling(item); }}>
                      {failed ? "Retry uninstall" : "Uninstall"}
                    </Button>
                  </div>
                )}
                {!removed && !busy && item.unavailable_reason && <p className="text-xs text-muted-foreground">{item.unavailable_reason}</p>}
              </article>
            );
          })}
        </div>
      ) : !query.isError && (
        <div className="mb-8"><Empty title="No connected installations">Install the app on GitHub, then connect an installation below.</Empty></div>
      )}
      {!query.isPending && !query.isError && (
        <>
          <h3 className="mb-4 font-semibold">Available installations</h3>
          {available.length ? (
            <div className="divide-y rounded-lg border bg-card">
              {available.map((item) => (
                <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
                  <span className="min-w-0 break-words">{item.account}{item.status === "suspended" && " · Suspended"}</span>
                  <Button size="sm" variant="outline" disabled={action.isPending || !item.can_connect} onClick={() => connect(item)}>Connect</Button>
                </div>
              ))}
            </div>
          ) : <p className="text-sm text-muted-foreground">No additional installations available. Install the app on another account, then refresh.</p>}
        </>
      )}
      <Dialog open={!!uninstalling} onOpenChange={(open) => { if (!open && !action.isPending) setUninstalling(null); }}>
        <DialogContent onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus.current?.focus(); }}>
          <DialogHeader>
            <DialogTitle>Uninstall from {uninstalling?.account}?</DialogTitle>
            <DialogDescription>
              This removes the GitHub App from this account and revokes its access to every repository in the installation.
              Affected reviews will stop. Saved review history and existing GitHub comments are retained.
              Reconnecting requires installing the app again on GitHub.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={action.error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={action.isPending} onClick={() => setUninstalling(null)}>Cancel</Button>
            <Button variant="destructive" disabled={action.isPending} onClick={() => {
              if (uninstalling) action.mutate({ path: `/installations/${uninstalling.id}`, method: "DELETE" }, {
                onSuccess: () => { returnFocus.current = heading.current; setUninstalling(null); setMessage(""); },
              });
            }}>{action.isPending ? "Requesting uninstall…" : "Uninstall GitHub App"}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
