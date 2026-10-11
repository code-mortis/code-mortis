import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, Github, MoreHorizontal } from "lucide-react";
import { api, useAction, type Schema } from "../api";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Empty, ErrorNotice, Loading, Status } from "./common";

type Installation = Schema["InstallationOut"];
const pending = (item: Installation) => ["queued", "running"].includes(item.operation_status ?? "");

function installationStatus(item: Installation) {
  if (item.status === "uninstalled") return "uninstalled";
  if (pending(item)) return "uninstalling";
  if (item.operation_status === "failed") return "failed";
  return item.status;
}

function installationPriority(status: string) {
  if (status === "active") return 0;
  if (status === "uninstalled") return 2;
  return 1;
}

function repositoryAccess(selection: Installation["repository_selection"]) {
  if (selection === "all") return "All repositories";
  if (selection === "selected") return "Selected repositories";
  return "Unknown repository access";
}

function installationDescription(item: Installation, status: string) {
  const paused = item.active ? "" : " Reviews are paused.";
  switch (status) {
    case "uninstalled":
      return "Uninstalled from GitHub. Saved review history is retained.";
    case "uninstalling":
      return "Uninstall requested. Waiting for GitHub to confirm removal.";
    case "failed":
      return `Uninstall did not complete. Retry to check GitHub and finish removal.${paused}`;
    default:
      return status === "active" ? `${repositoryAccess(item.repository_selection)} on GitHub.${paused}` : paused.trim();
  }
}

export function GitHubInstallations() {
  const query = useQuery({
    queryKey: ["/installations"],
    queryFn: () => api<Installation[]>("/installations"),
    refetchOnWindowFocus: "always",
    refetchInterval: (query) => query.state.data?.some(pending) ? 3000 : false,
    retry: false,
  });
  const action = useAction();
  const uninstallAction = useAction();
  const removeAction = useAction();
  const mutationPending = action.isPending || uninstallAction.isPending || removeAction.isPending;
  const [uninstalling, setUninstalling] = useState<Installation | null>(null);
  const [removing, setRemoving] = useState<Installation | null>(null);
  const [message, setMessage] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const connected = query.data?.filter((item) => item.connected)
    .sort((a, b) => installationPriority(installationStatus(a)) - installationPriority(installationStatus(b))) ?? [];
  const available = query.data?.filter((item) => !item.connected)
    .sort((a, b) => installationPriority(installationStatus(a)) - installationPriority(installationStatus(b))) ?? [];

  function connect(item: Installation) {
    setMessage("");
    action.mutate({ path: `/installations/${item.id}` }, {
      onSuccess: () => setMessage(`Repository sync queued for ${item.account}. Updated repositories will appear when synchronization finishes.`),
    });
  }

  function remove(item: Installation) {
    setMessage("");
    removeAction.mutate({ path: `/installations/${item.id}/local`, method: "DELETE" }, {
      onSuccess: () => {
        returnFocus.current = heading.current;
        setRemoving(null);
        setMessage(`${item.account} removed from the list. Saved repositories and review history are retained.`);
      },
    });
  }

  return (
    <>
      <section aria-label="Connected installations" className="settings-container">
        <div className="mb-2.5 flex items-center justify-between gap-3">
          <h3 ref={heading} tabIndex={-1} className="flex items-baseline gap-2 text-sm font-semibold">
            Connected installations{query.data && <span className="text-xs font-normal tabular-nums text-muted-foreground">{connected.length}</span>}
          </h3>
          <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>
            {query.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
        {(query.error || action.error) && <div className="mb-4"><ErrorNotice error={query.error || action.error} /></div>}
        {message && <p role="status" className="mb-4 text-sm text-muted-foreground">{message}</p>}
        {query.isPending ? <Loading /> : connected.length ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="settings-record settings-installation-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
              <span>Account</span><span>Repository access</span><span>Status</span><span />
            </div>
            {connected.map((item) => {
              const status = installationStatus(item);
              const busy = status === "uninstalling";
              const removed = status === "uninstalled";
              const failed = status === "failed";
              return (
                <article key={item.id} aria-label={item.account} className="settings-record settings-installation-row border-b px-4 py-4 last:border-b-0 sm:px-5">
                  <div className="settings-identity flex min-w-0 items-start gap-3">
                    <Github aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                    <h4 className="min-w-0 break-words text-sm font-medium">{item.account}</h4>
                  </div>
                  <div className="min-w-0 text-xs">
                    {status === "active" && <>
                      <span className="settings-cell-label mb-1 text-muted-foreground">Repository access</span>
                      {repositoryAccess(item.repository_selection)} on GitHub.
                    </>}
                  </div>
                  <div>
                    <span className="settings-cell-label mb-1 text-xs text-muted-foreground">Status</span>
                    <Status value={status} label={status === "active" ? "Installed" : undefined}
                      icon={status === "active" ? <Check aria-hidden="true" className="size-3" /> : undefined} />
                  </div>
                  <div className="settings-actions flex items-center justify-end gap-1">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${item.account}`}
                          onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                          onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" onCloseAutoFocus={(event) => {
                        if (uninstalling || removing || removed) event.preventDefault();
                        if (removed && !removing) returnFocus.current?.focus();
                      }}>
                        {removed ? <DropdownMenuItem disabled={mutationPending || query.isError || !item.can_remove}
                          onSelect={() => { removeAction.reset(); setRemoving(item); }}>Remove from list</DropdownMenuItem> : <>
                          {item.settings_url && <DropdownMenuItem asChild>
                            <a href={item.settings_url} target="_blank" rel="noreferrer">Manage repositories<ArrowUpRight aria-hidden="true" className="size-4" /></a>
                          </DropdownMenuItem>}
                          <DropdownMenuItem disabled={mutationPending || query.isError || !item.can_connect}
                            onSelect={() => connect(item)}>Resync repositories</DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem variant="destructive" disabled={mutationPending || query.isError || !item.can_uninstall}
                            onSelect={() => { uninstallAction.reset(); setUninstalling(item); }}>{failed ? "Retry uninstall" : "Uninstall"}</DropdownMenuItem>
                        </>}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {(removed || busy || failed || !item.active) && <p className="settings-row-note text-xs leading-relaxed text-muted-foreground">{installationDescription(item, status)}</p>}
                  {failed && item.operation_error && <p role="alert" className="settings-row-note break-words text-sm text-destructive">{item.operation_error}</p>}
                  {!removed && !busy && item.unavailable_reason && <p className="settings-row-note break-words text-xs text-muted-foreground">{item.unavailable_reason}</p>}
                </article>
              );
            })}
          </div>
        ) : !query.isError && <Empty title="No connected installations">Install the app on GitHub and connect an account to sync its repositories.</Empty>}
      </section>
      {!query.isPending && !query.isError && <section aria-label="Accounts to connect" className="settings-container">
        <h3 className="mb-2.5 flex items-baseline gap-2 text-sm font-semibold">Accounts to connect<span className="text-xs font-normal tabular-nums text-muted-foreground">{available.length}</span></h3>
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">Connect a GitHub account to sync its repositories.</p>
        {available.length ? <div className="overflow-hidden rounded-lg border bg-card">
          <div aria-hidden="true" className="settings-record settings-installation-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
            <span>Account</span><span>Repository access</span><span>Status</span><span />
          </div>
          {available.map((item) => <div key={item.id} className="settings-record settings-installation-row border-b px-4 py-4 last:border-b-0 sm:px-5">
            <div className="settings-identity flex min-w-0 items-start gap-3">
              <Github aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" /><span className="min-w-0 break-words text-sm font-medium">{item.account}</span>
            </div>
            <div className="text-xs">{installationStatus(item) === "active" && <><span className="settings-cell-label mb-1 text-muted-foreground">Repository access</span>{repositoryAccess(item.repository_selection)}</>}</div>
            <div><span className="settings-cell-label mb-1 text-xs text-muted-foreground">Status</span><Status value={installationStatus(item)} label={installationStatus(item) === "active" ? "Installed" : undefined}
              icon={installationStatus(item) === "active" ? <Check aria-hidden="true" className="size-3" /> : undefined} /></div>
            <div className="settings-actions flex justify-end">
              <DropdownMenu>
                <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${item.account}`}><MoreHorizontal className="size-4" /></Button></DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem disabled={mutationPending || !item.can_connect} onSelect={() => connect(item)}>Connect</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            {item.unavailable_reason && <p className="settings-row-note break-words text-xs text-muted-foreground">{item.unavailable_reason}</p>}
          </div>)}
        </div> : <p className="text-xs leading-relaxed text-muted-foreground">No additional installations available. Install the app on another account, then refresh.</p>}
      </section>}
      <Dialog open={!!uninstalling} onOpenChange={(open) => { if (!open && !uninstallAction.isPending) setUninstalling(null); }}>
        <DialogContent onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus.current?.focus(); }}>
          <DialogHeader>
            <DialogTitle>Uninstall from {uninstalling?.account}?</DialogTitle>
            <DialogDescription>
              This removes the GitHub App from this account and revokes its access to every repository in the installation.
              Affected reviews will stop. Saved review history and existing GitHub comments are retained.
              Reconnecting requires installing the app again on GitHub.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={uninstallAction.error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={uninstallAction.isPending} onClick={() => setUninstalling(null)}>Cancel</Button>
            <Button variant="destructive" disabled={uninstallAction.isPending} onClick={() => {
              if (uninstalling) uninstallAction.mutate({ path: `/installations/${uninstalling.id}`, method: "DELETE" }, {
                onSuccess: () => { returnFocus.current = heading.current; setUninstalling(null); setMessage(""); },
              });
            }}>{uninstallAction.isPending ? "Requesting uninstall…" : "Uninstall GitHub App"}</Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={!!removing} onOpenChange={(open) => { if (!open && !removeAction.isPending) setRemoving(null); }}>
        <DialogContent showCloseButton={!removeAction.isPending}
          onCloseAutoFocus={(event) => { event.preventDefault(); returnFocus.current?.focus(); }}>
          <DialogHeader>
            <DialogTitle>Remove {removing?.account} from the list?</DialogTitle>
            <DialogDescription>
              This hides the uninstalled account from Connected installations. Saved repositories and review history are retained.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={removeAction.error} />
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" disabled={removeAction.isPending} onClick={() => setRemoving(null)}>Cancel</Button>
            <Button variant="destructive" disabled={removeAction.isPending} onClick={() => { if (removing) remove(removing); }}>
              {removeAction.isPending ? "Removing…" : "Remove from list"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
