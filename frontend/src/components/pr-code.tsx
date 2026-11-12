import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, ExternalLink, FileCode2, MessageSquare, RefreshCw } from "lucide-react";
import { ApiError, useAction, type Schema } from "../api";
import { findingCount, usePRCode } from "../lib/use-pr-code";
import { cn } from "../lib/utils";
import { Empty, ErrorNotice, Loading, Status } from "./common";
import { GraphFileDetails, indexDescriptions, rowClass, StatusHint } from "./code-graph-explorer";
import { CodeGraphCoverage, CodeGraphWarnings } from "./code-graph-coverage";
import { FilterBar } from "./filter-bar";
import { FindingCard, FindingCategory } from "./finding-card";
import { SearchSelect } from "./search-select";
import { Pagination } from "./pagination";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

type SavedFinding = Schema["CodeFinding"];
const views = ["diff", "findings", "dependencies", "definitions", "source"] as const;
const patchMessages: Record<string, string> = {
  unavailable: "GitHub did not supply a readable patch for this file.",
  too_large: "This file's patch exceeds the display limit.",
  withheld: "This patch contains credential material and was withheld.",
  truncated: "Showing part of this diff. Open GitHub for the complete comparison.",
};

function FileReview({ view, query, occurrenceId, openFinding, renderFinding, closeFinding, restoreFocus, onRefresh }: {
  view: "diff" | "findings";
  query: { data?: Schema["CodeFile"] | null; error: ApiError | null; isPending: boolean };
  occurrenceId: string | null;
  openFinding: (item: SavedFinding, trigger: HTMLButtonElement) => void; closeFinding: () => void; restoreFocus: () => void;
  renderFinding: (item: SavedFinding) => ReactNode; onRefresh: () => void;
}) {
  const data = query.error ? undefined : query.data;
  const selectedFinding = data?.findings.find((item) => item.occurrence_id === occurrenceId);
  const groups = new Map<string, SavedFinding[]>();
  const anchored = new Map<string, SavedFinding>();
  for (const item of data?.findings ?? []) {
    groups.set(item.finding_id, [...groups.get(item.finding_id) ?? [], item]);
    if (item.placement === "inline" && (!anchored.has(item.finding_id) || item.occurrence_id === occurrenceId)) anchored.set(item.finding_id, item);
  }
  const inline = new Map<string, SavedFinding[]>();
  for (const item of anchored.values()) {
    const key = `${item.side}:${item.line}`;
    inline.set(key, [...inline.get(key) ?? [], item]);
  }
  const diffRoot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (occurrenceId) diffRoot.current?.querySelector(`[data-occurrence="${CSS.escape(occurrenceId)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [occurrenceId, data]);
  function findingButton(item: SavedFinding, history = false) {
    return <>
      <FindingCard evidence={item.snapshot} state={<Status value={item.current_status} />} onClick={(event) => openFinding(item, event.currentTarget)} />
      {history ? <p className="px-4 pb-3 text-xs text-muted-foreground sm:px-5">{item.source_sha ? `Saved at ${item.source_sha.slice(0, 7)}` : "Source revision unavailable"}</p>
        : item.placement !== "inline" && <p className="px-4 pb-3 text-xs text-muted-foreground sm:px-5">{item.placement === "historical" ? "Earlier revision" : item.placement === "unknown" ? "Unverified location" : "Outside this diff"}{item.source_sha ? ` · ${item.source_sha.slice(0, 7)}` : ""}</p>}
    </>;
  }
  function findingMarker(item: SavedFinding) {
    const description = String(item.snapshot.content ?? "Finding evidence").replaceAll(/[*`#]/g, "");
    return <Tooltip key={item.occurrence_id} delayDuration={200}>
      <TooltipTrigger asChild><Button variant="ghost" size="icon-xs" className="text-primary hover:text-primary" data-occurrence={item.occurrence_id} aria-haspopup="dialog"
        aria-label={`Open finding at ${item.side} line ${item.line}: ${description}`}
        onClick={(event) => openFinding(item, event.currentTarget)}><MessageSquare className="size-4" aria-hidden="true" /></Button></TooltipTrigger>
      <TooltipContent side="right" align="start" sideOffset={8} collisionPadding={16}
        className="w-80 max-w-[calc(100vw-2rem)] space-y-2 rounded-lg border bg-popover p-3 font-sans text-sm text-popover-foreground shadow-md [&_.rotate-45]:invisible">
        <div className="flex flex-wrap items-center gap-2"><Status value={item.current_status} />{item.snapshot.severity != null && <Status value={String(item.snapshot.severity)} />}{!!item.snapshot.category && <FindingCategory value={String(item.snapshot.category)} />}</div>
        <p className="line-clamp-4 break-words leading-5 [overflow-wrap:anywhere]">{description}</p>
        <p className="break-words font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{String(item.snapshot.path)}:{String(item.verification?.start_line ?? item.snapshot.start_line)}{(item.verification?.end_line ?? item.snapshot.end_line) !== (item.verification?.start_line ?? item.snapshot.start_line) ? `–${item.verification?.end_line ?? item.snapshot.end_line}` : ""}</p>
        {item.verification ? <p className="text-xs text-muted-foreground">Verification at {(item.verification.source_sha ?? data?.head_sha)?.slice(0, 7)}: {item.verification.verdict.replaceAll("_", " ")}</p>
          : item.source_sha && item.source_sha !== data?.head_sha && <p className="text-xs text-muted-foreground">Evidence at {item.source_sha.slice(0, 7)}</p>}
      </TooltipContent>
    </Tooltip>;
  }
  return <div ref={diffRoot} className="min-w-0">
    {query.isPending ? <div className="p-4"><Loading /></div> : query.error ? <div className="space-y-3 p-4"><ErrorNotice error={query.error} /><Button size="sm" variant="outline" onClick={() => { onRefresh(); }}>Refresh Code</Button></div> : data && <>
      {view === "diff" ? <>
      {data.file.previous_path && <p className="break-words border-b px-4 py-3 text-xs text-muted-foreground [overflow-wrap:anywhere]">Renamed from <code>{data.file.previous_path}</code></p>}
      {data.patch_state !== "available" && <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b p-4 text-sm text-muted-foreground" role="status">
        <span>{patchMessages[data.patch_state]}</span><a className="inline-flex min-h-9 items-center gap-1 underline underline-offset-4" href={data.github_url} target="_blank" rel="noreferrer">Open on GitHub<ExternalLink className="size-3" aria-hidden="true" /></a>
      </div>}
      {!!data.lines.length && <div role="region" aria-label="File diff" tabIndex={0} className="code-block max-h-[70vh] min-w-0 overflow-auto bg-card -outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
        <table className="w-full border-collapse text-xs leading-6"><caption className="sr-only">Changes with original and new line numbers</caption><thead className="sr-only"><tr>{inline.size > 0 && <th>Findings</th>}<th>Original line</th><th>New line</th><th>Change</th></tr></thead><tbody>
          {data.lines.map((line, index) =>
            <tr key={index} className={line.kind === "addition" ? "bg-[var(--code-added-background)] text-[var(--code-added)]" : line.kind === "deletion" ? "bg-[var(--code-removed-background)] text-[var(--code-removed)]" : line.kind === "hunk" ? "bg-muted text-muted-foreground" : ""}>
              {inline.size > 0 && <td className="w-8 min-w-8 px-1 align-top"><div className="flex flex-col">{[...(inline.get(`old:${line.old_line}`) ?? []), ...(inline.get(`new:${line.new_line}`) ?? [])].map(findingMarker)}</div></td>}
              <td className="w-10 select-none px-2 text-right font-mono tabular-nums text-muted-foreground">{line.old_line}</td><td className="w-10 select-none px-2 text-right font-mono tabular-nums text-muted-foreground">{line.new_line}</td>
              <td className="whitespace-pre px-3 font-mono"><span aria-hidden="true" className="mr-2 select-none">{line.kind === "addition" ? "+" : line.kind === "deletion" ? "-" : " "}</span>{line.text || " "}</td>
            </tr>
          )}
        </tbody></table>
      </div>}
      </> : groups.size ? <section aria-label="File findings">
          <p className="flex flex-wrap items-baseline gap-x-1.5 px-4 py-3 text-sm sm:px-5"><span className="font-medium">{data.findings_truncated ? "Showing " : ""}{groups.size} {groups.size === 1 ? "finding" : "findings"}</span><span className="text-xs tabular-nums text-muted-foreground">· {data.findings.length} review {data.findings.length === 1 ? "entry" : "entries"}</span></p>
          <ul aria-label="Findings for this file" className="divide-y border-t">{[...groups].map(([id, entries]) => <li key={id}>
            {findingButton(entries[0])}
            {entries.length > 1 && <details className="group/entries px-4 pb-3 sm:px-5">
              <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-1.5 rounded-sm text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden">
                <ChevronRight className="size-3.5 transition-transform group-open/entries:rotate-90" aria-hidden="true" />{entries.length} review entries
              </summary>
              <ol aria-label="Review entries" className="divide-y border-t">{entries.map((item) => <li key={item.occurrence_id}>{findingButton(item, true)}</li>)}</ol>
            </details>}
          </li>)}</ul>
      </section> : <Empty className="rounded-none border-0" title="No saved findings for this file">Findings from completed reviews appear here, including earlier revisions.</Empty>}
      {data.findings_truncated && <p className="px-4 py-3 text-xs text-muted-foreground">Showing up to 100 saved occurrences. Run history keeps the remaining evidence.</p>}
    </>}
    <Sheet open={!!occurrenceId} onOpenChange={(open) => { if (!open) closeFinding(); }}>
      <SheetContent className="w-full gap-0 overflow-hidden sm:max-w-2xl" onCloseAutoFocus={(event) => { event.preventDefault(); restoreFocus(); }}><SheetHeader className="shrink-0 px-6 pb-4 pt-5"><SheetTitle>Finding details</SheetTitle><SheetDescription className="sr-only">Saved evidence at its original revision and current resolution history.</SheetDescription></SheetHeader>
        {selectedFinding ? renderFinding(selectedFinding) : <div className="px-6">{query.isPending ? <Loading /> : <Empty title="Saved evidence unavailable">Close this panel and refresh Code.</Empty>}</div>}
      </SheetContent>
    </Sheet>
  </div>;
}

export function PRCode({ prId, repositoryId, admin, canWrite, renderFinding }: {
  prId: string; repositoryId: number; admin: boolean; canWrite: boolean; renderFinding: (item: SavedFinding) => ReactNode;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const heading = useRef<HTMLHeadingElement>(null);
  const navigationId = useRef(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const action = useAction();
  const value = Number(params.get("graph_page") ?? 1), page = Number.isInteger(value) && value > 0 && value <= 100 ? value : 1;
  const search = (params.get("graph_q") ?? "").slice(0, 256), group = params.get("code_group") ?? "";
  const reviewFilters = params.has("code_reviewed") ? params.getAll("code_reviewed") : params.get("code_unreviewed") === "1" ? ["0"] : [];
  const reviewed = ["0", "1"].filter((value) => reviewFilters.includes(value));
  const findingFilters = params.getAll("code_findings").map((value) => value === "1" ? "open" : value);
  const findings = (["any", "none", "open", "resolved", "dismissed"] as const).filter((value) => findingFilters.includes(value));
  const revision = params.get("graph_revision") === "base" ? "base" : "head";
  const requested = params.get("code_view");
  const view = views.find((value) => value === requested) ?? (params.get("tab") === "impact" ? revision === "base" ? "source" : "dependencies" : "diff");
  const contentView = view === "findings" ? "diff" : view;
  const workspace = usePRCode(prId, { page, reviewed: reviewed.map((value) => value === "1"), findings, query: search, group: group || null, path: params.get("graph_file"),
    view: contentView, revision });
  const { position: positionValue, setPosition } = workspace;
  function change(values: Record<string, string | string[] | null>) {
    navigationId.current++;
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) {
      next.delete(key);
      for (const item of Array.isArray(value) ? value : value ? [value] : []) next.append(key, item);
    }
    setParams(next, { state: location.state });
  }
  const refresh = workspace.refresh;
  const data = workspace.inventory;
  if (!data && !workspace.error) return <Loading />;
  if (!data) return <div className="space-y-3"><ErrorNotice error={workspace.error} /><Button variant="outline" onClick={refresh}>Retry Code</Button></div>;
  const path = workspace.path;
  const selectedGroup = workspace.group;
  const selectedIndex = data.files.findIndex((file) => file.path === path || file.previous_path === path);
  const selectedFile = data.files[selectedIndex];
  const sequenceIndex = workspace.files.findIndex((file) => file.path === selectedFile?.path);
  const reviewedCount = data.file_states.filter((state) => state.reviewed).length;
  function selectFile(index: number) {
    const file = workspace.files[index];
    if (!file) return;
    setPosition({ offset: 0, start_byte: 0, end_byte: 1048576, previous: [] });
    change({ graph_file: contentView !== "diff" && revision === "base" ? file.previous_path ?? file.path : file.path,
      graph_revision: contentView !== "diff" && file.change === "removed" ? "base" : revision,
      graph_page: String(Math.floor(index / 10) + 1), code_occurrence: null });
    requestAnimationFrame(() => heading.current?.focus());
  }
  async function openFirstFinding(file: Schema["CodeFileInfo"], element: HTMLButtonElement) {
    trigger.current = element;
    change({ graph_file: file.path, code_view: "diff", graph_revision: "head", code_occurrence: null });
    const admitted = navigationId.current;
    try {
      const result = await workspace.loadFindings(file.path);
      const next = new URLSearchParams(window.location.search);
      if (admitted !== navigationId.current || next.get("graph_file") !== file.path || next.get("code_view") !== "diff") return;
      const finding = result.diff?.findings.find((item) => !findings.length || findings.includes("any") || findings.some((status) => status === item.current_status));
      next.set("code_occurrence", finding?.occurrence_id ?? "unavailable");
      setParams(next, { state: location.state });
    } catch { /* The workspace owns authorization and retry errors. */ }
  }
  const pair = { base_sha: data.base_sha, head_sha: data.head_sha, revision: workspace.revision } as const;
  const snapshot = workspace.data?.graph, indexed = ["ready", "partial"].includes(snapshot?.status ?? "");
  const fileQuery = { error: workspace.error, isPending: workspace.loading, isFetching: workspace.fetching, refetch: refresh };
  const comparisonDescription = `Changes from merge base ${data.merge_base_sha} to PR head ${data.head_sha}.`;
  return <section aria-label="Pull request code" aria-busy={workspace.fetching} className="min-w-0 space-y-4" onKeyDown={(event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.nativeEvent.isComposing || (event.repeat && workspace.fetching)
      || !(event.target instanceof HTMLElement) || event.target.closest("input, textarea, select, [contenteditable=true], [role=combobox]")
      || document.querySelector('[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="combobox"][aria-expanded="true"], [data-slot="popover-content"][data-state="open"]')) return;
    const next = sequenceIndex + (event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0);
    if (sequenceIndex < 0 || next === sequenceIndex || next < 0 || next >= workspace.files.length) return;
    event.preventDefault(); selectFile(next);
  }}>
    <div className="space-y-2">
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3">
    <FilterBar query={search} searchLabel="Filter changed files" searchPlaceholder="Search changed files…" filters={[
      { key: "code_reviewed", label: "Review status", values: reviewed, choices: [{ value: "0", name: "Not reviewed" }, { value: "1", name: "Reviewed" }] },
      { key: "code_findings", label: "Findings", values: findings, choices: [{ value: "any", name: "With findings" }, { value: "none", name: "Without findings" }, { value: "open", name: "With open findings" }, { value: "resolved", name: "With resolved findings" }, { value: "dismissed", name: "With dismissed findings" }], hint: "Matches any selected option. Includes saved findings from any revision. Without findings requires a complete zero count." },
    ]} onChange={(key, values) => change({ [key]: values, ...(key === "code_reviewed" ? { code_unreviewed: null } : {}), graph_page: null, graph_file: null, code_occurrence: null })} onSearch={(value) => change({ graph_q: value.trim(), graph_page: null, graph_file: null, code_occurrence: null })}>
      {!!data.groups.length && <SearchSelect aria-label="Change group" className="sm:w-72" value={selectedGroup?.id ?? "all"} options={[{ value: "all", label: "All changes" }, ...data.groups.map((item) => ({ value: item.id, label: item.title }))]} onChange={(value) => change({ code_group: value === "all" ? null : value, graph_page: null, graph_file: null, code_occurrence: null })} />}
    </FilterBar>
      <Tooltip><TooltipTrigger asChild><Button variant="outline" className="min-h-11 min-w-11 bg-card px-3 sm:min-h-9" aria-label="Refresh Code" disabled={workspace.fetching} onClick={refresh}><RefreshCw className="size-4" aria-hidden="true" /><span className="hidden sm:inline">Refresh</span></Button></TooltipTrigger><TooltipContent>Refresh Code</TooltipContent></Tooltip>
    </div>
    <p role="status" className="text-xs tabular-nums text-muted-foreground">{reviewedCount} of {data.files.length}{data.comparison_truncated || data.metadata_truncated ? " shown files" : ""} reviewed</p>
    </div>
    {selectedGroup && <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">{selectedGroup.description}</p>}
    <div className="grid min-w-0 items-start gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <section aria-label="Changed files" className="min-w-0 space-y-3">
        <div className="flex min-h-9 flex-wrap items-center gap-x-2"><h3 className="text-sm font-semibold">Changed files <span className="ml-1 font-normal tabular-nums text-muted-foreground">{workspace.files.length}</span></h3>{(data.comparison_truncated || data.metadata_truncated) && <StatusHint value="partial" label="Limited comparison">{data.metadata_truncated ? "The file inventory reached its display limit. Search covers only returned files." : "GitHub returned at most 300 files. This list and its search may be incomplete."}</StatusHint>}</div>
        <div className="divide-y overflow-hidden rounded-lg border bg-card">
          {workspace.files.slice((workspace.page - 1) * 10, workspace.page * 10).map((file) => {
            const state = data.file_states[data.files.indexOf(file)], active = file.path === path || file.previous_path === path;
            const count = findingCount(state, findings), status = findings.length === 1 && !["any", "none"].includes(findings[0]) ? findings[0] : findings.length && !findings.includes("any") ? "matching" : "saved";
            return <div key={file.path} className={`flex min-w-0 items-center transition-colors ${active ? "bg-muted" : "hover:bg-muted/50"}`}>
              <button onPointerEnter={() => workspace.prefetch({ path: file.path })} onPointerLeave={() => workspace.prefetch()} onFocus={() => workspace.prefetch({ path: file.path })} onBlur={() => workspace.prefetch()} aria-pressed={active} className={cn(rowClass, "flex-1 hover:bg-transparent")} onClick={() => selectFile(workspace.files.indexOf(file))}>
                <span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{file.path}</span><span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span className="capitalize">{file.change}</span>{file.additions != null && file.deletions != null && <span className="font-mono tabular-nums"><span className="text-[var(--code-added)]">+{file.additions}</span> <span className="text-[var(--code-removed)]">−{file.deletions}</span></span>}{state.reviewed && <Tooltip><TooltipTrigger asChild><span role="img" aria-label="Reviewed by Code Mortis"><Check className="size-3.5" aria-hidden="true" /></span></TooltipTrigger><TooltipContent>Reviewed at this revision. This does not mean there are no findings.</TooltipContent></Tooltip>}</span>
              </button>
              {(count > 0 || !state.complete) && <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="sm" className="mr-1 min-h-11 min-w-11 shrink-0 gap-1 px-2 sm:min-h-9" aria-label={`Open findings for ${file.path}`} onClick={(event) => void openFirstFinding(file, event.currentTarget)}><MessageSquare className="size-3.5" aria-hidden="true" /><span className="text-xs tabular-nums">{count || "?"}{!state.complete && count > 0 ? "+" : ""}</span></Button></TooltipTrigger><TooltipContent>{state.complete ? `${count} ${status} finding${count === 1 ? "" : "s"}` : `${count ? `At least ${count} ${status} findings. ` : ""}Finding count is incomplete.`}</TooltipContent></Tooltip>}
            </div>;
          })}
          {!workspace.files.length && <div className="space-y-2 p-4 text-sm text-muted-foreground"><p>{search || findings.length || reviewed.length || selectedGroup ? "No returned files match these filters." : "No visible changed files in this comparison."}</p>{!!(search || findings.length || reviewed.length || selectedGroup) && <Button variant="outline" size="sm" onClick={() => change({ graph_q: null, code_findings: null, code_reviewed: null, code_unreviewed: null, code_group: null, graph_page: null, graph_file: null, code_occurrence: null })}>Clear filters</Button>}</div>}
          <Pagination page={workspace.page} pageSize={10} total={workspace.files.length} loading={false} label="changed files" onPageChange={(value) => change({ graph_page: String(value), graph_file: null, code_occurrence: null })} />
        </div>
      </section>
      <section aria-label="Selected file" className="min-w-0 space-y-3">
        <div className="flex min-h-9 min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <h3 ref={heading} tabIndex={-1} aria-label="Selected file details" className="flex min-h-9 min-w-0 flex-1 items-center gap-2 font-mono text-sm font-medium"><FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="min-w-0 break-words [overflow-wrap:anywhere]">{path ?? "Select a file"}</span></h3>
          {sequenceIndex >= 0 && workspace.files.length > 1 && <nav aria-label="File navigation" className="flex shrink-0 items-center gap-1">
            <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" className="min-h-11 min-w-11 sm:min-h-9 sm:min-w-9" aria-label="Previous file" disabled={sequenceIndex === 0} onClick={() => selectFile(sequenceIndex - 1)}><ChevronLeft /></Button></TooltipTrigger><TooltipContent>Previous file · Alt+↑</TooltipContent></Tooltip>
            <span className="text-xs tabular-nums text-muted-foreground">{sequenceIndex + 1} of {workspace.files.length}</span>
            <Tooltip><TooltipTrigger asChild><Button variant="ghost" size="icon" className="min-h-11 min-w-11 sm:min-h-9 sm:min-w-9" aria-label="Next file" disabled={sequenceIndex === workspace.files.length - 1} onClick={() => selectFile(sequenceIndex + 1)}><ChevronRight /></Button></TooltipTrigger><TooltipContent>Next file · Alt+↓</TooltipContent></Tooltip>
          </nav>}
        </div>
        {path && <Tabs className="min-w-0 gap-0 overflow-hidden rounded-lg border bg-card" value={view} onValueChange={(value) => change({ code_view: value, code_occurrence: null, graph_file: (value === "diff" || value === "findings") ? data.files.find((f) => f.previous_path === path)?.path ?? path : path })}>
          <TabsList aria-label="File information" variant="line" className="w-full max-w-full flex-wrap justify-start border-b bg-muted/50 p-1 group-data-[orientation=horizontal]/tabs:h-auto">{views.map((value) => <TabsTrigger key={value} value={value} disabled={!selectedFile && (value === "diff" || value === "findings")} title={value === "diff" ? comparisonDescription : undefined} aria-description={value === "diff" ? comparisonDescription : undefined} className="h-9 flex-none group-data-[orientation=horizontal]/tabs:after:bottom-[-4px]" onPointerEnter={() => workspace.prefetch({ view: value === "findings" ? "diff" : value })} onPointerLeave={() => workspace.prefetch()} onFocus={() => workspace.prefetch({ view: value === "findings" ? "diff" : value })} onBlur={() => workspace.prefetch()}>{value[0].toUpperCase() + value.slice(1)}</TabsTrigger>)}</TabsList>
          {(["diff", "findings"] as const).map((value) => <TabsContent key={value} value={value} className="min-w-0"><FileReview view={value} key={`${path}:${data.head_sha}:${data.base_sha}:${data.policy_digest}`} query={{ ...fileQuery, data: workspace.data?.diff }} onRefresh={refresh} occurrenceId={params.get("code_occurrence")} openFinding={(item, element) => { trigger.current = element; change({ code_occurrence: item.occurrence_id, finding_tab: null }); }} closeFinding={() => change({ code_occurrence: null, finding_tab: null })} restoreFocus={() => (trigger.current?.isConnected ? trigger.current : heading.current)?.focus()} renderFinding={renderFinding} /></TabsContent>)}
          {contentView !== "diff" && <div className="min-w-0">
            {data.graph_enabled && <div role="group" aria-label="Code revision" className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-3 sm:px-5">
              <div className="flex min-w-0 flex-1 basis-48 flex-wrap items-center gap-x-3 gap-y-1">
                {!workspace.loading && !workspace.error && <><StatusHint value={snapshot?.status === "ready" ? "completed" : snapshot?.status ?? "unavailable"} label={snapshot?.status === "ready" ? "Indexed" : snapshot?.status === "partial" ? "Indexed with warnings" : undefined}><p>{indexDescriptions[snapshot?.status ?? ""] ?? "This revision has no usable index."}</p><CodeGraphCoverage coverage={snapshot?.coverage} />{data.graph_paused && <p>Indexing is paused.</p>}</StatusHint>
                {(snapshot?.truncated || (indexed && (view === "definitions" || view === "source"))) && <div className="min-w-0 flex-1 basis-48 space-y-1 text-xs text-muted-foreground">
                  {indexed && view === "definitions" && <p>Symbols defined in this file.</p>}
                  {indexed && view === "source" && <p>File contents at the selected revision.</p>}
                  {snapshot?.truncated && <p role="status">This result is bounded. More relationships or source may exist.</p>}
                </div>}</>}
              </div>
              <SearchSelect aria-label="Revision" className="ml-auto w-56 max-w-full shrink-0" value={workspace.revision} options={[{ value: "head", label: `PR head · ${data.head_sha.slice(0, 7)}` }, { value: "base", label: `Merge base · ${data.merge_base_sha.slice(0, 7)}` }]} onChange={(value) => change({ graph_revision: value, code_view: view, graph_file: value === "base" ? data.files.find((f) => f.path === path)?.previous_path ?? path : data.files.find((f) => f.previous_path === path)?.path ?? path })} />
              {!workspace.loading && !workspace.error && <CodeGraphWarnings coverage={snapshot?.coverage} />}
            </div>}
            {workspace.loading ? <div className="px-4 sm:px-5"><Loading /></div> : workspace.error ? <div className="space-y-3 p-4"><ErrorNotice error={workspace.error} /><Button size="sm" variant="outline" onClick={refresh}>Refresh Code</Button></div> : !data.graph_enabled ? <Empty className="rounded-none border-0" title="Code graph is disabled">{admin ? <Link to={`/repositories/${repositoryId}/settings?tab=code-graph`} className="underline">Enable it in Code graph settings</Link> : "Ask an administrator to enable it in Code graph settings."}</Empty> : <>
              {action.error && <div className="p-4"><ErrorNotice error={action.error} /></div>}
              {indexed ? <GraphFileDetails key={`${path}:${revision}:${snapshot?.snapshot_id}`} path={path} query={{ ...fileQuery, data: snapshot }} offset={positionValue.offset} setOffset={(offset) => setPosition({ offset })} previous={positionValue.previous} setPrevious={(previous) => setPosition({ previous })} range={{ start_byte: positionValue.start_byte, end_byte: positionValue.end_byte }} setRange={setPosition} view={view} setView={(value) => change({ code_view: value })} onSelect={(file) => { change({ graph_file: file }); heading.current?.focus(); }} onRefresh={refresh} /> : <Empty className="rounded-none border-0" title="This revision is not ready"><p>{data.graph_paused ? "Indexing is paused in Code graph settings." : "Index this revision to explore related code."}</p>{admin && canWrite && !data.graph_paused && <Button className="mt-4" disabled={action.isPending || ["queued", "running"].includes(snapshot?.status ?? "")} onClick={() => action.mutate({ path: `/pulls/${prId}/code-graph/index`, body: pair }, { onSuccess: () => void refresh() })}>Index selected revision</Button>}</Empty>}
            </>}
          </div>}
        </Tabs>}
      </section>
    </div>
  </section>;
}
