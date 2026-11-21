import { ArrowLeft, Bell, ChevronDown, GitPullRequest, Moon, Shield, Sun } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, useSearchParams } from "react-router-dom";
import logo from "../assets/logo.svg";
import { Status } from "../components/common";
import { Button } from "../components/ui/button";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "../components/ui/popover";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "../components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { applyAppearance } from "../lib/appearance";
import "../index.css";

const options = [
  { id: "priority", name: "Priority popover", note: "Open to unread updates. Keep recent results and running jobs one click away." },
  { id: "grouped", name: "Grouped updates", note: "Show one entry per PR or scan target. Expand it to see each individual run." },
  { id: "drawer", name: "Activity drawer", note: "Use a wider panel to see running jobs, unread updates, and recent results together." },
  { id: "timeline", name: "Timeline", note: "See what happened in order, with dates and an unread-only filter." },
  { id: "by-type", name: "By type", note: "Switch between reviews and security scans, with running jobs and results in each view." },
  { id: "split", name: "Inbox with preview", note: "Browse a compact list and read the selected run beside it. Mark it as read when you’re done." },
] as const;
type Option = (typeof options)[number]["id"];
type Entry = { id: string; group: string; title: string; kind: "Review" | "Security scan"; state: string; time: string; detail?: string };
type FeedControls = { busy: boolean; unread: string[]; read: (id: string) => void; markAll: () => void };

const pull = { group: "pr-54", title: "#54 Add activity inbox and generation status", kind: "Review" } as const;
const scan = { group: "main", title: "main", kind: "Security scan" } as const;
const updates: Entry[] = [
  { ...scan, id: "scan-2", state: "cancelled", time: "Nov 21, 9:05 AM" },
  { ...pull, id: "review-4", state: "completed", time: "Nov 20, 2:41 PM" },
  { ...pull, id: "review-3", state: "completed", time: "Nov 20, 2:14 PM" },
  { ...pull, id: "review-2", state: "partial", time: "Nov 20, 1:46 PM" },
  { ...pull, id: "review-1", state: "completed", time: "Nov 20, 1:22 PM" },
  { ...scan, id: "scan-1", state: "completed", time: "Nov 19, 5:36 PM" },
];
const jobs: Entry[] = [
  { id: "review-running", group: "pr-55", title: "#55 Improve the repository overview", kind: "Review", state: "running", time: "Started 3 min ago", detail: "Analysis · 18 of 40 files" },
  { ...scan, id: "scan-queued", state: "queued", time: "Queued 1 min ago", detail: "Waiting for a worker" },
];
const repository = "code-mortis/code-mortis";
const groupNames = ["pr-54", "main"];
const textAction = "inline-flex min-h-11 w-fit items-center gap-1 rounded-sm text-xs font-semibold text-primary underline-offset-4 hover:underline sm:min-h-8";
const entryTitle = (entry: Entry) => entry.kind === "Security scan" ? `Security scan · ${entry.title}` : entry.title;

function KindIcon({ entry }: { entry: Entry }) {
  const Icon = entry.kind === "Review" ? GitPullRequest : Shield;
  return <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />;
}

function EntryState({ entry }: { entry: Entry }) {
  return <Status value={entry.state} label={entry.state === "partial" ? "Partial" : undefined} showSpinner={false} />;
}

function MarkAll({ unread, markAll }: Pick<FeedControls, "unread" | "markAll">) {
  return <Button variant="ghost" size="sm" className="min-h-11 text-xs text-muted-foreground sm:min-h-8"
    onClick={markAll} disabled={unread.length === 0}>Mark all as read</Button>;
}

function NotificationPanel({ option, busy, unread, read, markAll }: FeedControls & { option: Option }) {
  const [tab, setTab] = useState(unread.length || !busy ? "updates" : "running");
  const [selected, setSelected] = useState<Entry | null>(null);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [kind, setKind] = useState("All");
  const previous = useRef("");
  const list = useRef<HTMLDivElement>(null);
  const detail = useRef<HTMLHeadingElement>(null);
  const timelineFilter = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (selected) detail.current?.focus();
    else if (previous.current) {
      const row = list.current?.querySelector<HTMLButtonElement>(`[data-entry="${CSS.escape(previous.current)}"]`);
      (row ?? timelineFilter.current)?.focus();
    }
  }, [selected]);
  const running = busy ? jobs : [];
  const openEntry = (entry: Entry) => {
    previous.current = entry.id;
    read(entry.id);
    setSelected(entry);
  };
  const row = (entry: Entry, nested = false) => <li key={entry.id}>
    <button type="button" data-entry={entry.id}
      className="grid min-h-11 w-full grid-cols-[minmax(0,1fr)_auto] items-start gap-2 rounded-md px-2 py-2.5 text-left hover:bg-accent focus-visible:-outline-offset-2"
      onClick={() => openEntry(entry)}>
      <span className="min-w-0 space-y-1">
        {!nested && <span className="flex items-start gap-2 text-sm leading-snug font-medium">
          <KindIcon entry={entry} /><span className="wrap-anywhere">{entryTitle(entry)}</span>
        </span>}
        <span className={nested ? "flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground" : "flex flex-wrap items-center gap-x-2 gap-y-1 pl-6 text-xs text-muted-foreground"}>
          <EntryState entry={entry} /><span>{entry.time}</span>
        </span>
        {entry.detail && <span className="block pl-6 text-xs text-muted-foreground">{entry.detail}</span>}
      </span>
      {unread.includes(entry.id) && <span className="mt-1.5 size-2 rounded-full bg-primary"><span className="sr-only">Unread</span></span>}
    </button>
  </li>;
  const section = (title: string, entries: Entry[]) => entries.length > 0 && <section aria-label={title} className="px-3 pt-3 pb-1">
    <h3 className="px-2 pb-1 text-xs font-semibold text-muted-foreground">{title}</h3>
    <ul>{entries.map((entry) => row(entry))}</ul>
  </section>;
  const recent = updates.filter(({ id }) => !unread.includes(id));
  const unreadEntries = updates.filter(({ id }) => unread.includes(id));
  const timeline = unreadOnly ? unreadEntries : updates;
  const days = [...new Set(timeline.map(({ time }) => time.split(", ")[0]))];
  const heading = option === "drawer"
    ? <><SheetTitle className="text-base">Activity</SheetTitle><SheetDescription className="sr-only">Running jobs and recent notifications</SheetDescription></>
    : <PopoverTitle id="notifications-title" className="text-base font-semibold">Notifications</PopoverTitle>;
  return <>
    <div className={`flex shrink-0 items-center justify-between gap-3 border-b px-4 py-2 ${option === "drawer" ? "pr-14" : ""}`}>
      {heading}
      <MarkAll unread={unread} markAll={markAll} />
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pb-2">
      {selected ? <div className="space-y-2 p-4">
        <button className={textAction} onClick={() => setSelected(null)}><ArrowLeft aria-hidden="true" className="size-3.5" />Back to notifications</button>
        <h3 className="rounded-sm text-sm font-semibold" ref={detail} tabIndex={-1}>{entryTitle(selected)}</h3>
        <p className="text-xs text-muted-foreground">{repository}</p>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><EntryState entry={selected} />{selected.time}</div>
        {selected.detail && <p className="text-sm">{selected.detail}</p>}
      </div> : null}
      <div ref={list} hidden={!!selected}>
      {option === "timeline" ? <>
        <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
          <span className="text-xs text-muted-foreground">Newest first</span>
          <Button ref={timelineFilter} variant={unreadOnly ? "secondary" : "outline"} size="sm" className="min-h-11 sm:min-h-8"
            aria-pressed={unreadOnly} onClick={() => setUnreadOnly(!unreadOnly)}>Unread only</Button>
        </div>
        {!unreadOnly && section("Now", running)}
        {days.length ? days.map((day) => <section aria-label={day} className="px-3 py-2" key={day}>
          <h3 className="px-2 pb-1 text-xs font-semibold text-muted-foreground">{day}</h3>
          <ol className="divide-y">
            {timeline.filter(({ time }) => time.startsWith(`${day},`)).map((entry) => <li key={entry.id}>
              <button type="button" data-entry={entry.id} className="grid min-h-11 w-full grid-cols-[3.75rem_minmax(0,1fr)_0.5rem] items-start gap-2 rounded-sm px-2 py-2.5 text-left hover:bg-accent focus-visible:-outline-offset-2"
                onClick={() => openEntry(entry)}>
                <span className="pt-0.5 text-xs text-muted-foreground tabular-nums">{entry.time.split(", ")[1]}</span>
                <span className="space-y-1">
                  <span className="block text-sm leading-snug font-medium wrap-anywhere">{entryTitle(entry)}</span>
                  <EntryState entry={entry} />
                </span>
                {unread.includes(entry.id) && <span className="mt-1.5 size-2 rounded-full bg-primary"><span className="sr-only">Unread</span></span>}
              </button>
            </li>)}
          </ol>
        </section>) : <p className="px-5 py-4 text-sm text-muted-foreground">No unread updates.</p>}
      </> : option === "by-type" ? <Tabs value={kind} onValueChange={setKind} className="gap-0">
        <TabsList aria-label="Notification type" className="mx-4 mt-3 w-auto max-w-full justify-start">
          <TabsTrigger value="All">All</TabsTrigger>
          <TabsTrigger value="Review"><GitPullRequest aria-hidden="true" />Reviews</TabsTrigger>
          <TabsTrigger value="Security scan"><Shield aria-hidden="true" />Security</TabsTrigger>
        </TabsList>
        <TabsContent value={kind}>
          {section("In progress", running.filter((entry) => kind === "All" || entry.kind === kind))}
          {section("Updates", [...unreadEntries, ...recent].filter((entry) => kind === "All" || entry.kind === kind))}
        </TabsContent>
      </Tabs> : option === "drawer" ? <>
        <div className="border-b">
          {running.length ? section("In progress", running) : <p className="px-5 py-3 text-xs text-muted-foreground">No jobs in progress</p>}
        </div>
        {section("Unread", unreadEntries)}{section("Recent", recent)}
      </> : <Tabs value={tab} onValueChange={setTab} className="gap-0">
        <TabsList variant="page" aria-label="Notification views" className="gap-x-4 px-5">
          <TabsTrigger value="updates">Updates <span className="tabular-nums text-muted-foreground">{unread.length}</span></TabsTrigger>
          <TabsTrigger value="running">In progress <span className="tabular-nums text-muted-foreground">{running.length}</span></TabsTrigger>
        </TabsList>
        <TabsContent value="updates">
          {option === "priority" ? <>{section("Unread", unreadEntries)}{section("Recent", recent)}</> :
            <div className="divide-y px-4">
              {groupNames.map((group) => {
                const entries = updates.filter((entry) => entry.group === group);
                const latest = entries[0];
                const newCount = entries.filter((entry) => unread.includes(entry.id)).length;
                return <details className="group py-2" key={group}>
                  <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 rounded-md px-1 py-2 hover:bg-accent [&::-webkit-details-marker]:hidden">
                    <KindIcon entry={latest} />
                    <span className="min-w-0 flex-1 space-y-1">
                      <span className="block text-sm leading-snug font-medium">{entryTitle(latest)}</span>
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <EntryState entry={latest} /><span>{entries.length} runs</span>
                        {newCount > 0 && <span className="font-semibold text-primary">{newCount} unread</span>}
                      </span>
                    </span>
                    <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground group-open:rotate-180" />
                  </summary>
                  <ul className="mt-1 ml-3 border-l pl-2">{entries.map((entry) => row(entry, true))}</ul>
                </details>;
              })}
            </div>}
        </TabsContent>
        <TabsContent value="running">
          {running.length ? <ul className="px-3 py-2">{running.map((entry) => row(entry))}</ul>
            : <p className="px-5 py-4 text-sm text-muted-foreground">No jobs in progress.</p>}
        </TabsContent>
      </Tabs>}
      </div>
    </div>
  </>;
}

function SplitInbox({ busy, unread, read, markAll }: FeedControls) {
  const [selection, setSelection] = useState(unread[0] ?? updates[0].id);
  const [showDetail, setShowDetail] = useState(false);
  const list = useRef<HTMLDivElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const returnFocus = useRef(false);
  const entries = [...(busy ? jobs : []), ...updates];
  const selected = entries.find(({ id }) => id === selection) ?? updates[0];
  const isUnread = unread.includes(selected.id);
  useLayoutEffect(() => {
    if (showDetail) title.current?.focus();
    else if (returnFocus.current) {
      list.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus();
      returnFocus.current = false;
    }
  }, [showDetail, selection]);
  return <>
    <div className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-2">
      <PopoverTitle id="notifications-title" className="text-base font-semibold">Notifications</PopoverTitle>
      <MarkAll unread={unread} markAll={markAll} />
    </div>
    <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <div ref={list} className={`min-h-0 overflow-y-auto overscroll-contain md:border-r ${showDetail ? "hidden md:block" : ""}`}>
        <ul className="space-y-0.5 p-2">
          {entries.map((entry) => <li key={entry.id}>
            <button type="button" aria-pressed={entry.id === selected.id}
              className="grid min-h-11 w-full grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-2 rounded-lg px-2 py-2.5 text-left hover:bg-muted aria-pressed:bg-accent focus-visible:-outline-offset-2"
              onClick={() => { setSelection(entry.id); setShowDetail(true); returnFocus.current = true; }}>
              <KindIcon entry={entry} />
              <span className="grid min-w-0 gap-1">
                <span className="truncate text-sm font-medium" title={entryTitle(entry)}>{entryTitle(entry)}</span>
                <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                  <span className="capitalize">{entry.state}</span><span>{entry.time}</span>
                </span>
              </span>
              {unread.includes(entry.id) && <span className="mt-1.5 size-2 rounded-full bg-primary"><span className="sr-only">Unread</span></span>}
            </button>
          </li>)}
        </ul>
      </div>
      <section aria-label="Selected notification" className={`min-h-0 min-w-0 space-y-3 overflow-y-auto overscroll-contain p-4 md:block md:p-5 ${showDetail ? "" : "hidden"}`}>
        <button type="button" className={`${textAction} md:hidden`} onClick={() => setShowDetail(false)}><ArrowLeft aria-hidden="true" className="size-3.5" />Back to notifications</button>
        <h3 ref={title} tabIndex={-1} className="text-base leading-snug font-semibold wrap-anywhere">{entryTitle(selected)}</h3>
        <EntryState entry={selected} />
        <dl className="space-y-2 border-t pt-3 text-xs">
          <div className="flex flex-wrap justify-between gap-1"><dt className="text-muted-foreground">Repository</dt><dd>{repository}</dd></div>
          <div className="flex flex-wrap justify-between gap-1"><dt className="text-muted-foreground">Time</dt><dd>{selected.time}</dd></div>
        </dl>
        {selected.detail && <p className="text-sm">{selected.detail}</p>}
        <div aria-live="polite" className="min-h-11">
          {isUnread ? <Button size="sm" variant="outline" className="min-h-11 sm:min-h-8" onClick={() => { read(selected.id); title.current?.focus(); }}>Mark as read</Button>
            : <span className="text-xs text-muted-foreground">No unread update for this run</span>}
        </div>
      </section>
    </div>
  </>;
}

function Demo({ option, busy }: { option: Option; busy: boolean }) {
  const [open, setOpen] = useState(true);
  const [unread, setUnread] = useState(["review-4"]);
  const bell = <Button variant="ghost" size="icon" className="w-auto min-w-10 gap-1.5 px-2 aria-expanded:bg-muted"
    aria-label={`Notifications, ${unread.length} unread${busy ? ", 2 jobs in progress" : ""}`}>
    <Bell aria-hidden="true" />
    {unread.length > 0 && <span className="rounded-full bg-primary/10 px-1.5 text-xs font-semibold text-primary">{unread.length}</span>}
  </Button>;
  const controls: FeedControls = { busy, unread, read: (id) => setUnread((items) => items.filter((entry) => entry !== id)), markAll: () => setUnread([]) };
  const panel = option === "split" ? <SplitInbox {...controls} /> : <NotificationPanel option={option} {...controls} />;
  return <>
    <div className="flex min-h-14 items-center justify-between gap-3 border-b px-4 sm:px-8">
      <span className="inline-flex items-center gap-2 text-sm font-semibold"><img src={logo} alt="" className="h-6 w-auto" />code-mortis</span>
      {option === "drawer" ? <Sheet open={open} onOpenChange={setOpen}>
        <SheetTrigger asChild>{bell}</SheetTrigger>
        <SheetContent className="w-full max-w-[31rem] gap-0 sm:max-w-[31rem] motion-reduce:animate-none">{panel}</SheetContent>
      </Sheet> : <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>{bell}</PopoverTrigger>
        <PopoverContent align="end" side="bottom" sideOffset={12} collisionPadding={8} aria-labelledby="notifications-title"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (event.target instanceof Element && event.target.closest("[data-preview-controls]")) event.preventDefault();
          }}
          className={`flex max-h-[var(--radix-popover-content-available-height)] max-w-[calc(100vw-16px)] flex-col overflow-hidden rounded-xl p-0 motion-reduce:animate-none ${option === "split" ? "w-184" : "w-105"}`}>
          {panel}
        </PopoverContent>
      </Popover>}
    </div>
    <section aria-label="Repository overview" className="mx-auto max-w-6xl px-4 py-6 sm:px-8">
      <h2 className="text-2xl font-semibold tracking-tight">Overview</h2>
      <div className="mt-4 grid max-w-3xl gap-3.5 sm:grid-cols-2">
        <div className="rounded-xl border bg-card p-4"><h3 className="flex items-center gap-2 text-sm font-semibold"><GitPullRequest aria-hidden="true" className="size-4 text-primary" />Reviews</h3>
          <p className="mt-3 text-sm text-muted-foreground"><strong className="mr-1.5 text-2xl font-semibold text-foreground">0</strong> open findings</p></div>
        <div className="rounded-xl border bg-card p-4"><h3 className="flex items-center gap-2 text-sm font-semibold"><Shield aria-hidden="true" className="size-4 text-primary" />Security</h3>
          <p className="mt-3 text-sm text-muted-foreground"><strong className="mr-1.5 text-2xl font-semibold text-foreground">53</strong> open findings</p></div>
      </div>
    </section>
  </>;
}

function Preview() {
  const [params] = useSearchParams();
  const [busy, setBusy] = useState(true);
  const [dark, setDark] = useState(true);
  const option = options.find((item) => item.id === params.get("option")) ?? options[0];
  const ThemeIcon = dark ? Sun : Moon;
  return <main>
    <div data-preview-controls className="border-b bg-card">
      <div className="mx-auto max-w-6xl px-4 py-4 sm:px-8">
        <div className="flex items-center justify-between gap-4">
          <h1 className="text-xl font-semibold tracking-tight">Notification options</h1>
          <Button variant="ghost" size="icon" aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => { applyAppearance("claude-code", !dark); setDark(!dark); }}><ThemeIcon aria-hidden="true" /></Button>
        </div>
        <nav aria-label="Design options" className="mt-2 flex flex-wrap gap-x-4 border-b sm:gap-x-6">
          {options.map(({ id, name }) => <Link key={id} to={`?option=${id}`} aria-current={id === option.id ? "page" : undefined}
            className="inline-flex min-h-11 items-center border-b-2 border-transparent text-xs font-medium text-muted-foreground hover:text-foreground aria-[current=page]:border-foreground aria-[current=page]:text-foreground sm:text-sm">{name}</Link>)}
        </nav>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-5 gap-y-2 text-xs text-muted-foreground">
          <p>{option.note}</p>
          <label className="inline-flex min-h-8 items-center gap-2"><input type="checkbox" className="accent-primary" checked={busy} onChange={(event) => setBusy(event.target.checked)} />Show running jobs</label>
          <span>Sample data</span>
        </div>
      </div>
    </div>
    <Demo key={option.id} option={option.id} busy={busy} />
  </main>;
}

applyAppearance("claude-code", true);
createRoot(document.getElementById("root")!).render(<BrowserRouter><Preview /></BrowserRouter>);
