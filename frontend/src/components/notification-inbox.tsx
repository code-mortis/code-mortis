import { useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, ChevronRight, FileText, GitPullRequest, LoaderCircle, Shield } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { api, ApiError, type Schema } from "../api";
import { activityFeedQueryKey, activityQueryKey } from "../hooks/use-activity-read";
import { reviewStatusLabel } from "../lib/inbox-filters";
import { cn } from "../lib/utils";
import { ListDate } from "./common";
import { Button } from "./ui/button";
import { Popover, PopoverAnchor, PopoverContent, PopoverTitle, PopoverTrigger } from "./ui/popover";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

type Activity = Schema["ActivityItem"];
type Feed = Schema["ActivityFeed"];
type ActivityTab = "running" | "updates";

const items = {
  review: ["Review", GitPullRequest],
  scan: ["Security scan", Shield],
  context: ["Context generation", FileText],
  repository_summary: ["Repository summary", FileText],
  pr_summary: ["PR summary", FileText],
} as const;

const statusLabels: Record<string, string> = {
  blocked: "Paused", waiting: "Waiting", pending: "Queued", queued: "Queued", running: "Running",
  done: "Completed", completed: "Completed", partial: "Completed with warnings",
  failed: "Failed", cancelled: "Cancelled", skipped: "Skipped", superseded: "Superseded",
};

function ActivityRow({ item, onOpen }: { item: Activity; onOpen: () => void }) {
  const location = useLocation();
  const [kind, Icon] = items[item.kind];
  const [pathname, search] = item.href.split("?");
  // A same-page generation link must retain the settings handoff and unsaved draft.
  const preserveState = pathname === location.pathname
    && new URLSearchParams(search).get("tab") === new URLSearchParams(location.search).get("tab");
  const running = item.status === "running";
  const status = item.kind === "review" ? reviewStatusLabel(item.status) : statusLabels[item.status] ?? item.status;
  const stages = running ? item.stages.join(" · ") : "";
  const subtitle = item.title === kind ? item.repository : `${kind} · ${item.repository}`;
  return <li>
    <Link to={item.href} state={preserveState ? location.state : undefined} onClick={(event) => {
      if (event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) onOpen();
    }} className="grid min-h-14 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-5.5 py-2.5 hover:bg-accent focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring">
      <span className="flex size-8 items-center justify-center rounded-md bg-muted text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span>
      <span className="grid min-w-0 gap-0.5">
        <span className="truncate text-sm font-medium leading-snug" title={item.title}>{item.title}</span>
        <span className="truncate text-xs text-muted-foreground" title={subtitle}>{subtitle}</span>
        <span className="flex flex-wrap items-center gap-x-1 text-xs">
          <span className={cn(running ? "activity-stage" : ["done", "completed"].includes(item.status)
            ? "text-primary" : item.status === "failed" ? "text-destructive" : "text-muted-foreground")}>
            {stages && item.kind === "review" ? `${status} · ${stages}` : stages || status}
          </span>
          {item.finished_at && <><span aria-hidden="true" className="text-muted-foreground">·</span><ListDate value={item.finished_at} /></>}
        </span>
      </span>
      <span className="flex items-center gap-2">
        {item.unread && <><span aria-hidden="true" className="size-1.5 rounded-full bg-primary" /><span className="sr-only">Unread</span></>}
        <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
      </span>
    </Link>
  </li>;
}

const denied = (error: Error | null) => error instanceof ApiError && [401, 403].includes(error.status);

export function NotificationInbox({ userId }: { userId: number }) {
  const title = useId();
  const runningTab = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<ActivityTab>("running");
  const [pages, setPages] = useState<string[]>([]);
  const cache = useQueryClient();
  const feed = useQuery({
    queryKey: [...activityFeedQueryKey, userId],
    queryFn: ({ signal }) => api<Feed>("/activity", "GET", undefined, undefined, signal),
    refetchInterval: ({ state }) => state.data?.running?.length ? 5000 : 30000,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  const cursor = pages.at(-1);
  const more = useQuery({
    queryKey: [...activityQueryKey, userId, tab, cursor],
    queryFn: ({ signal }) => api<Feed>(`/activity?${tab}_cursor=${encodeURIComponent(cursor ?? "")}`, "GET", undefined, undefined, signal),
    enabled: open && !!cursor,
    retry: false,
  });
  const markAll = useMutation({
    mutationFn: (result: Pick<Feed, "as_of" | "snapshot">) => api("/activity/read", "POST", { mode: "all", ...result }),
    onSuccess: async () => {
      setPages([]);
      await cache.invalidateQueries({ queryKey: activityQueryKey });
    },
  });
  const snapshot = denied(feed.error) ? undefined : feed.data;
  const running = snapshot?.running_count ?? 0;
  const queued = snapshot?.queued_count ?? 0;
  const inProgress = running + queued;
  const unread = snapshot?.unread_count ?? 0;
  const pageQuery = cursor ? more : feed;
  const current = snapshot && !denied(pageQuery.error) ? pageQuery.data : undefined;
  const rows = current?.[tab] ?? [];
  const next = current?.[`next_${tab}_cursor`];

  function setExpanded(expanded: boolean) {
    setOpen(expanded);
    if (expanded) {
      setTab("running");
      setPages([]);
      markAll.reset();
    }
  }

  return <Popover open={open} onOpenChange={setExpanded}>
    <PopoverAnchor asChild>
      <span className="flex h-14 items-center">
        <PopoverTrigger asChild>
          <Button variant="ghost" size="icon" className="w-auto min-w-9 gap-1.5 px-2 text-muted-foreground aria-expanded:bg-muted pointer-coarse:min-h-11 pointer-coarse:min-w-11"
            aria-label={snapshot ? `Notifications, ${running} running${queued ? `, ${queued} queued` : ""}, ${unread} unread` : "Notifications"}>
            {running ? <LoaderCircle aria-hidden="true" className="text-primary motion-safe:animate-spin" /> : <Bell aria-hidden="true" />}
            {inProgress + unread > 0 && <span aria-hidden="true" className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary/10 px-1 text-xs font-semibold tabular-nums text-primary">{inProgress + unread}</span>}
          </Button>
        </PopoverTrigger>
      </span>
    </PopoverAnchor>
    <PopoverContent align="end" sideOffset={8} collisionPadding={8} aria-labelledby={title}
      onOpenAutoFocus={(event) => { event.preventDefault(); runningTab.current?.focus(); }}
      className="flex max-h-[min(62dvh,490px,var(--radix-popover-content-available-height))] w-100 max-w-[calc(100vw-16px)] flex-col overflow-hidden rounded-lg p-0 sm:max-h-[min(74dvh,var(--radix-popover-content-available-height))] motion-reduce:animate-none">
      <div className="flex shrink-0 items-center justify-between gap-3 border-b p-4">
        <PopoverTitle id={title} className="text-base font-semibold">Notifications</PopoverTitle>
        <Button variant="ghost" size="sm" className="shrink-0 text-xs text-muted-foreground pointer-coarse:min-h-11 aria-disabled:opacity-50 aria-disabled:hover:bg-transparent aria-disabled:hover:text-muted-foreground"
          aria-disabled={!unread || markAll.isPending}
          onClick={() => { if (snapshot && unread && !markAll.isPending) markAll.mutate({ as_of: snapshot.as_of, snapshot: snapshot.snapshot }); }}>
          Mark all as read
        </Button>
      </div>
      <Tabs value={tab} className="min-h-0 gap-0" onValueChange={(value) => { setTab(value === "updates" ? "updates" : "running"); setPages([]); }}>
        <TabsList variant="page" aria-label="Notification views" className="shrink-0 px-4">
          <TabsTrigger ref={runningTab} value="running">In progress <span className="tabular-nums">{inProgress}</span></TabsTrigger>
          <TabsTrigger value="updates">Updates <span className="tabular-nums">{unread}</span></TabsTrigger>
        </TabsList>
        {(["running", "updates"] as const).map((view) => <TabsContent value={view} key={view} className="min-h-0 overflow-y-auto overscroll-contain">
          {view === tab && <>
            {(feed.error || pageQuery.error || markAll.error) && <div role="status" className="flex items-center justify-between gap-2 px-4 py-2 text-xs text-muted-foreground">
              <span>{markAll.error ? "Couldn’t mark results as read." : "Couldn’t refresh notifications."}</span>
              <Button variant="ghost" size="sm" disabled={markAll.isPending || pageQuery.isFetching} onClick={() => {
                if (markAll.error && markAll.variables) markAll.mutate(markAll.variables);
                else if (feed.error) void feed.refetch();
                else void pageQuery.refetch();
              }}>Retry</Button>
            </div>}
            {pageQuery.isPending ? <p role="status" className="px-5 py-5 text-sm text-muted-foreground">Loading notifications…</p>
              : rows.length ? <ul className="divide-y">{rows.map((row) => <ActivityRow key={`${row.kind}:${row.id}`} item={row} onOpen={() => setOpen(false)} />)}</ul>
                : !pageQuery.error && <p className="px-5 py-5 text-sm text-muted-foreground">{view === "running" ? "No jobs in progress." : "No updates yet."}</p>}
            {(cursor || next) && <div className="flex justify-between border-t px-3 py-2">
              {cursor && <Button size="sm" variant="ghost" onClick={() => setPages((old) => old.slice(0, -1))}>Previous</Button>}
              {next && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setPages((old) => [...old, next])}>Next</Button>}
            </div>}
          </>}
        </TabsContent>)}
      </Tabs>
    </PopoverContent>
  </Popover>;
}
