import { useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ExternalLink, EyeOff, MoreHorizontal, Play, RotateCcw, Settings2, Square } from "lucide-react";
import { toast } from "sonner";
import { useAction, useApi, type Run, type Schema } from "../api";
import { Button } from "./ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";

export function PullRequestActions({ id, number, title, repositoryId, admin, state }: {
  id: string;
  number: number;
  title: string;
  repositoryId: number;
  admin: boolean;
  state: { inbox?: string; repository?: string };
}) {
  const [open, setOpen] = useState(false);
  const [side, setSide] = useState<"top" | "bottom">("bottom");
  const trigger = useRef<HTMLButtonElement>(null);
  const movingFocus = useRef(false);
  const detail = useApi<Schema["PRDetail"]>(`/pulls/${id}`, false, open);
  const action = useAction();
  const navigate = useNavigate();
  const location = useLocation();
  const data = detail.error ? undefined : detail.data;
  const pr = data?.pull_request;
  const startUnavailable = !data ? "Loading review actions…"
    : pr?.ignored ? "Restore this pull request before reviewing it."
    : data.has_active_run ? "A review is already in progress."
    : pr?.state !== "open" ? "Reviews can only run on open pull requests."
    : !data.repository.enabled ? "Reviews are paused for this repository."
    : !data.repository.connection_id ? "Choose a default connection in repository settings." : null;

  function focusRemainingRow() {
    const buttons = [...document.querySelectorAll<HTMLButtonElement>("[data-pull-actions]")];
    const index = buttons.indexOf(trigger.current!);
    const target = buttons[index + 1] ?? buttons[index - 1]
      ?? document.querySelector<HTMLElement>("[data-pull-list-focus]");
    target?.focus();
  }

  async function ignore(ignored: boolean) {
    movingFocus.current = trigger.current?.getAttribute("aria-expanded") === "true";
    try {
      await action.mutateAsync({ path: `/pulls/${id}/ignore`, method: "PUT", body: { ignored } });
      focusRemainingRow();
      toast.success(ignored ? "Pull request ignored" : "Pull request restored", ignored ? {
        description: "Hidden for everyone. Reviews are stopped until restored.",
        action: { label: "Undo", onClick: () => { void ignore(false); } },
      } : undefined);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update this pull request.");
    }
  }

  async function review(stop: boolean) {
    try {
      const result = await action.mutateAsync({ path: stop ? `/pulls/${id}/cancel` : `/pulls/${id}/runs`, ...(stop ? {} : { body: {} }) });
      if (stop) toast.success("Reviews stopped");
      else toast.success("Review started", {
        action: { label: "View review", onClick: () => navigate(`/runs/${(result as Run).id}`, { state }) },
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not update the review.");
      void detail.refetch();
    }
  }

  return <DropdownMenu open={open} onOpenChange={(value) => {
    if (value) setSide((trigger.current?.getBoundingClientRect().top ?? 0) > window.innerHeight / 2 ? "top" : "bottom");
    setOpen(value);
  }}>
    <DropdownMenuTrigger asChild>
      <Button ref={trigger} data-pull-actions variant="ghost" size="icon" className="size-11 text-muted-foreground sm:size-8" disabled={action.isPending} aria-label={`Actions for #${number}: ${title}`}>
        <MoreHorizontal aria-hidden="true" className="size-4" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent side={side} align="end" collisionPadding={16} className="w-64 max-w-[calc(100vw-2rem)] [&_[role=menuitem]]:min-h-11 sm:[&_[role=menuitem]]:min-h-8" onCloseAutoFocus={(event) => {
      if (!movingFocus.current) return;
      event.preventDefault();
      movingFocus.current = false;
      focusRemainingRow();
    }}>
      <DropdownMenuItem asChild><Link to={`/pulls/${id}`} state={state}>View pull request</Link></DropdownMenuItem>
      {pr?.latest_run && <DropdownMenuItem asChild><Link to={`/runs/${pr.latest_run.id}`} state={state}>View latest review</Link></DropdownMenuItem>}
      {pr && <DropdownMenuItem asChild><a href={pr.url} target="_blank" rel="noreferrer"><ExternalLink aria-hidden="true" />Open on GitHub</a></DropdownMenuItem>}
      {admin && <DropdownMenuItem asChild><Link to={`/repositories/${repositoryId}/settings${state.repository ? location.search : ""}`} state={state}><Settings2 aria-hidden="true" />Repository settings</Link></DropdownMenuItem>}
      <DropdownMenuSeparator />
      {detail.isFetching && <p role="status" className="px-2 py-2 text-xs text-muted-foreground">Loading review actions…</p>}
      {detail.error && <>
        <p role="alert" className="px-2 py-2 text-xs text-destructive">{detail.error.message}</p>
        <DropdownMenuItem onSelect={(event) => { event.preventDefault(); void detail.refetch(); }}>Retry actions</DropdownMenuItem>
      </>}
      {data && !detail.isFetching && (data.can_write ? <>
        <DropdownMenuItem disabled={action.isPending || !!startUnavailable} onSelect={() => { void review(false); }}><Play aria-hidden="true" />Start review</DropdownMenuItem>
        {startUnavailable && <p className="px-2 pb-2 text-xs text-muted-foreground">{startUnavailable}</p>}
        {data.cancellable_run_count > 0 && <DropdownMenuItem disabled={action.isPending} onSelect={() => { void review(true); }}><Square aria-hidden="true" />Stop review</DropdownMenuItem>}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={action.isPending} onSelect={() => { void ignore(!pr?.ignored); }}>
          {pr?.ignored ? <RotateCcw aria-hidden="true" /> : <EyeOff aria-hidden="true" />}{pr?.ignored ? "Restore pull request" : "Ignore pull request"}
        </DropdownMenuItem>
      </> : <p className="px-2 py-2 text-xs text-muted-foreground">You have read-only access to this pull request.</p>)}
    </DropdownMenuContent>
  </DropdownMenu>;
}
