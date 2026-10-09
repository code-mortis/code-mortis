import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Github, MoreHorizontal } from "lucide-react";
import { api, useAction, type Schema } from "../api";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "./ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import { Empty, ErrorNotice, ExternalLink, Loading, Status } from "./common";

type Installation = Schema["InstallationOut"];
const pending = (item: Installation) => ["queued", "running"].includes(item.operation_status ?? "");

function installationStatus(item: Installation) {
  if (item.status === "uninstalled") return "uninstalled";
  if (pending(item)) return "uninstalling";
  if (item.operation_status === "failed") return "failed";
  return item.status;
}

function installationPriority(status: string) {
  return status === "active" ? 0 : status === "uninstalled" ? 2 : 1;
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
      return `${item.repository_selection === "all" ? "All repositories" : "Selected repositories"} on GitHub.${paused}`;
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
  const [uninstalling, setUninstalling] = useState<Installation | null>(null);
  const [message, setMessage] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const connected = query.data?.filter((item) => item.connected)
    .sort((a, b) => installationPriority(installationStatus(a)) - installationPriority(installationStatus(b))) ?? [];
  const available = query.data?.filter((item) => !item.connected)
    .sort((a, b) => installationPriority(a.status) - installationPriority(b.status)) ?? [];

  function connect(item: Installation) {
    setMessage("");
    action.mutate({ path: `/installations/${item.id}` }, {
      onSuccess: () => setMessage(`Repository sync queued for ${item.account}. Updated repositories will appear when synchronization finishes.`),
    });
  }

  return (
    <>
      <section aria-label="Connected installations">
        <div className="mb-2.5 flex items-center justify-between gap-3">
          <h3 ref={heading} tabIndex={-1} className="flex items-baseline gap-2 text-sm font-semibold">
            Connected installations{query.data && <span className="text-xs font-normal tabular-nums text-muted-foreground">{connected.length}</span>}
          </h3>
          <Button size="sm" variant="ghost" disabled={query.isFetching} onClick={() => { void query.refetch(); }}>
            {query.isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
        {(query.error || (!uninstalling && action.error)) && <div className="mb-4"><ErrorNotice error={query.error || action.error} /></div>}
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
                    <span className="settings-cell-label mb-1 text-muted-foreground">Repository access</span>
                    {item.repository_selection === "all" ? "All repositories" : "Selected repositories"} on GitHub.
                  </div>
                  <div>
                    <span className="settings-cell-label mb-1 text-xs text-muted-foreground">Status</span>
                    <Status value={status} />
                  </div>
                  <div className="settings-actions flex items-center justify-end gap-1">
                    {!removed && <>
                      {item.settings_url && <ExternalLink href={item.settings_url}>Manage<span className="sr-only"> repositories</span></ExternalLink>}
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${item.account}`}
                            onPointerDown={(event) => { returnFocus.current = event.currentTarget; }}
                            onFocus={(event) => { returnFocus.current = event.currentTarget; }}>
                            <MoreHorizontal className="size-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" onCloseAutoFocus={(event) => { if (uninstalling) event.preventDefault(); }}>
                          <DropdownMenuItem disabled={action.isPending || query.isError || !item.can_connect}
                            onSelect={() => connect(item)}>Resync repositories</DropdownMenuItem>
                          {!failed && <>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem variant="destructive" disabled={action.isPending || query.isError || !item.can_uninstall}
                              onSelect={() => { action.reset(); setUninstalling(item); }}>Uninstall</DropdownMenuItem>
                          </>}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </>}
                  </div>
                  {(removed || busy || failed || !item.active) && <p className="settings-row-note text-xs leading-relaxed text-muted-foreground">{installationDescription(item, status)}</p>}
                  {failed && <div className="settings-row-note flex flex-wrap items-center justify-between gap-3">
                    {item.operation_error && <p role="alert" className="min-w-0 flex-1 basis-48 break-words text-sm text-destructive">{item.operation_error}</p>}
                    <Button size="sm" variant="outline" disabled={action.isPending || query.isError || !item.can_uninstall}
                      onClick={(event) => { returnFocus.current = event.currentTarget; action.reset(); setUninstalling(item); }}>Retry uninstall</Button>
                  </div>}
                  {!removed && !busy && item.unavailable_reason && <p className="settings-row-note break-words text-xs text-muted-foreground">{item.unavailable_reason}</p>}
                </article>
              );
            })}
          </div>
        ) : !query.isError && <Empty title="No connected installations">Install the app on GitHub, then connect an installation below.</Empty>}
      </section>
      {!query.isPending && !query.isError && <section aria-label="Available installations">
        <h3 className="mb-2.5 flex items-baseline gap-2 text-sm font-semibold">Available installations<span className="text-xs font-normal tabular-nums text-muted-foreground">{available.length}</span></h3>
        {available.length ? (
          <div className="overflow-hidden rounded-lg border bg-card">
            <div aria-hidden="true" className="settings-record settings-installation-row settings-list-heading border-b bg-muted/30 px-5 py-3 text-xs font-medium text-muted-foreground">
              <span>Account</span><span>Repository access</span><span>Status</span><span />
            </div>
            {available.map((item) => <div key={item.id} className="settings-record settings-installation-row border-b px-4 py-4 last:border-b-0 sm:px-5">
              <div className="settings-identity flex min-w-0 items-start gap-3">
                <Github aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" /><span className="min-w-0 break-words text-sm font-medium">{item.account}</span>
              </div>
              <div className="text-xs"><span className="settings-cell-label mb-1 text-muted-foreground">Repository access</span>{item.repository_selection === "all" ? "All repositories" : "Selected repositories"}</div>
              <div><span className="settings-cell-label mb-1 text-xs text-muted-foreground">Status</span><Status value={item.status} /></div>
              <div className="settings-actions flex justify-end"><Button size="sm" variant="outline" disabled={action.isPending || !item.can_connect} onClick={() => connect(item)}>Connect</Button></div>
              {item.unavailable_reason && <p className="settings-row-note break-words text-xs text-muted-foreground">{item.unavailable_reason}</p>}
            </div>)}
          </div>
        ) : <p className="text-xs leading-relaxed text-muted-foreground">No additional installations available. Install the app on another account, then refresh.</p>}
      </section>}
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
