import { Fragment, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ExternalLink, FileCode2, RefreshCw } from "lucide-react";
import { ApiError, useAction, type Schema } from "../api";
import { usePRCode } from "../lib/use-pr-code";
import { Empty, ErrorNotice, Loading, Status } from "./common";
import { GraphFileDetails, indexDescriptions, rowClass, StatusHint } from "./code-graph-explorer";
import { CodeGraphCoverage } from "./code-graph-coverage";
import { FilterBar } from "./filter-bar";
import { SearchSelect } from "./search-select";
import { Pagination } from "./pagination";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

type SavedFinding = Schema["CodeFinding"];
const views = ["diff", "dependencies", "definitions", "source"];
const patchMessages: Record<string, string> = {
  unavailable: "GitHub did not supply a readable patch for this file.",
  too_large: "This file's patch exceeds the display limit.",
  withheld: "This patch contains credential material and was withheld.",
  truncated: "Showing part of this diff. Open GitHub for the complete comparison.",
};

function FileDiff({ query, occurrenceId, openFinding, renderFinding, closeFinding, onRefresh }: {
  query: { data?: Schema["CodeFile"] | null; error: ApiError | null; isPending: boolean };
  occurrenceId: string | null;
  openFinding: (item: SavedFinding, trigger: HTMLButtonElement) => void; closeFinding: () => void;
  renderFinding: (item: SavedFinding) => ReactNode; onRefresh: () => void;
}) {
  const data = query.error ? undefined : query.data;
  const selectedFinding = data?.findings.find((item) => item.occurrence_id === occurrenceId);
  const unanchored = data?.findings.filter((item) => item.placement !== "inline") ?? [];
  const inline = new Map<string, SavedFinding[]>();
  for (const item of data?.findings ?? []) if (item.placement === "inline") {
    const key = `${item.side}:${item.line}`;
    inline.set(key, [...inline.get(key) ?? [], item]);
  }
  function findingButton(item: SavedFinding) {
    return <button key={item.occurrence_id} className={`${rowClass} flex flex-wrap items-start gap-2`} onClick={(event) => openFinding(item, event.currentTarget)}>
      <Status value={item.current_status} /><span className="line-clamp-2 min-w-0 flex-1 break-words text-left text-sm [overflow-wrap:anywhere]">{String(item.snapshot.content)}</span>
      {item.placement !== "inline" && <span className="w-full text-xs text-muted-foreground">{item.placement === "historical" ? "Earlier revision" : item.placement === "unknown" ? "Unverified location" : "Outside this diff"}{item.source_sha ? ` · ${item.source_sha.slice(0, 7)}` : ""}</span>}
    </button>;
  }
  return <div className="min-w-0 space-y-3">
    {query.isPending ? <Loading /> : query.error ? <><ErrorNotice error={query.error} /><Button size="sm" variant="outline" onClick={() => { onRefresh(); }}>Refresh Code</Button></> : data && <>
      {data.file.previous_path && <p className="break-words text-xs text-muted-foreground [overflow-wrap:anywhere]">Renamed from <code>{data.file.previous_path}</code></p>}
      {data.patch_state !== "available" && <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground" role="status">
        <span>{patchMessages[data.patch_state]}</span><a className="inline-flex min-h-9 items-center gap-1 underline underline-offset-4" href={data.github_url} target="_blank" rel="noreferrer">Open on GitHub<ExternalLink className="size-3" aria-hidden="true" /></a>
      </div>}
      {!!data.lines.length && <div role="region" aria-label="File diff" tabIndex={0} className="code-block max-h-[70vh] min-w-0 overflow-auto rounded-lg border bg-card outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring">
        <table className="w-full border-collapse text-xs leading-6"><caption className="sr-only">Changes with original and new line numbers</caption><thead className="sr-only"><tr><th>Original line</th><th>New line</th><th>Change</th></tr></thead><tbody>
          {data.lines.map((line, index) => <Fragment key={index}>
            <tr className={line.kind === "addition" ? "bg-[var(--code-added-background)] text-[var(--code-added)]" : line.kind === "deletion" ? "bg-[var(--code-removed-background)] text-[var(--code-removed)]" : line.kind === "hunk" ? "bg-muted text-muted-foreground" : ""}>
              <td className="w-10 select-none px-2 text-right font-mono tabular-nums text-muted-foreground">{line.old_line}</td><td className="w-10 select-none px-2 text-right font-mono tabular-nums text-muted-foreground">{line.new_line}</td>
              <td className="whitespace-pre px-3 font-mono"><span aria-hidden="true" className="mr-2 select-none">{line.kind === "addition" ? "+" : line.kind === "deletion" ? "-" : " "}</span>{line.text || " "}</td>
            </tr>
            {[...(inline.get(`old:${line.old_line}`) ?? []), ...(inline.get(`new:${line.new_line}`) ?? [])].map((item) => <tr key={item.occurrence_id}><td colSpan={3} className="border-y bg-background font-sans">{findingButton(item)}</td></tr>)}
          </Fragment>)}
        </tbody></table>
      </div>}
      {!!unanchored.length && <section aria-label="Other saved findings" className="space-y-2"><h4 className="text-sm font-medium">Other saved findings</h4><div className="divide-y overflow-hidden rounded-lg border bg-card">{unanchored.map(findingButton)}</div></section>}
      {data.findings_truncated && <p className="text-xs text-muted-foreground">Showing up to 100 saved occurrences. Run history keeps the remaining evidence.</p>}
    </>}
    <Sheet open={!!occurrenceId} onOpenChange={(open) => { if (!open) closeFinding(); }}>
      <SheetContent className="w-full gap-0 overflow-hidden sm:max-w-2xl"><SheetHeader className="shrink-0 px-6 pb-4 pt-5"><SheetTitle>Finding details</SheetTitle><SheetDescription className="sr-only">Saved evidence at its original revision and current resolution history.</SheetDescription></SheetHeader>
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
  const trigger = useRef<HTMLButtonElement>(null);
  const action = useAction();
  const value = Number(params.get("graph_page") ?? 1), page = Number.isInteger(value) && value > 0 && value <= 100 ? value : 1;
  const search = (params.get("graph_q") ?? "").slice(0, 256), group = params.get("code_group") ?? "";
  const revision = params.get("graph_revision") === "base" ? "base" : "head";
  const requested = params.get("code_view");
  const view = requested && views.includes(requested) ? requested : params.get("tab") === "impact" ? revision === "base" ? "source" : "dependencies" : "diff";
  const positionKey = `${params.get("graph_file")}:${revision}`;
  const [position, setPosition] = useState({ key: positionKey, offset: 0, start_byte: 0, end_byte: 1048576, previous: [] as number[] });
  const positionValue = position.key === positionKey ? position : { offset: 0, start_byte: 0, end_byte: 1048576, previous: [] as number[] };
  const workspace = usePRCode(prId, { page, query: search, group: group || null, path: params.get("graph_file"),
    view: view as Schema["CodeNavigation"]["view"], revision,
    offset: view === "definitions" ? positionValue.offset : 0,
    start_byte: view === "source" ? positionValue.start_byte : 0, end_byte: view === "source" ? positionValue.end_byte : 1048576 });
  function change(values: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    setParams(next, { state: location.state });
  }
  const refresh = workspace.refresh;
  const data = workspace.inventory;
  if (!data && !workspace.error) return <Loading />;
  if (!data) return <div className="space-y-3"><ErrorNotice error={workspace.error} /><Button variant="outline" onClick={refresh}>Retry Code</Button></div>;
  const path = workspace.path;
  const selectedGroup = workspace.group;
  const pair = { base_sha: data.base_sha, head_sha: data.head_sha, revision: workspace.revision } as const;
  const snapshot = workspace.data?.graph, indexed = ["ready", "partial"].includes(snapshot?.status ?? "");
  const fileQuery = { error: workspace.error, isPending: workspace.loading, isFetching: workspace.fetching, refetch: refresh };
  return <section aria-label="Pull request code" aria-busy={workspace.fetching} className="min-w-0 space-y-4">
    <FilterBar query={search} searchLabel="Filter changed files" searchPlaceholder="Search changed files…" filters={[]} onChange={() => {}} onSearch={(value) => change({ graph_q: value.trim(), graph_page: null, graph_file: null, code_occurrence: null })}>
      {!!data.groups.length && <SearchSelect aria-label="Change group" className="sm:w-72" value={selectedGroup?.id ?? "all"} options={[{ value: "all", label: "All changes" }, ...data.groups.map((item) => ({ value: item.id, label: item.title }))]} onChange={(value) => change({ code_group: value === "all" ? null : value, graph_page: null, graph_file: null, code_occurrence: null })} />}
      <code className="text-xs text-muted-foreground" title={`Merge base ${data.merge_base_sha} to PR head ${data.head_sha}`}>{data.merge_base_sha.slice(0, 7)}…{data.head_sha.slice(0, 7)}</code>
      <Button variant="outline" className="ml-auto bg-card" aria-label="Refresh Code" disabled={workspace.fetching} onClick={refresh}><RefreshCw className="size-4" aria-hidden="true" />Refresh</Button>
    </FilterBar>
    {selectedGroup && <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">{selectedGroup.description}</p>}
    <div className="grid min-w-0 items-start gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <section aria-label="Changed files" className="min-w-0 space-y-3">
        <div className="flex min-h-9 flex-wrap items-center gap-x-2"><h3 className="text-sm font-semibold">Changed files <span className="ml-1 font-normal tabular-nums text-muted-foreground">{workspace.files.length}</span></h3>{(data.comparison_truncated || data.metadata_truncated) && <StatusHint value="partial" label="Limited comparison">{data.metadata_truncated ? "The file inventory reached its display limit. Search covers only returned files." : "GitHub returned at most 300 files. This list and its search may be incomplete."}</StatusHint>}</div>
        <div className="divide-y overflow-hidden rounded-lg border bg-card">
          {workspace.files.slice((workspace.page - 1) * 10, workspace.page * 10).map((file) => <button key={file.path} onPointerEnter={() => workspace.prefetch({ path: file.path })} onPointerLeave={() => workspace.prefetch()} onFocus={() => workspace.prefetch({ path: file.path })} onBlur={() => workspace.prefetch()} aria-pressed={file.path === path || file.previous_path === path} className={`${rowClass} block aria-pressed:bg-muted disabled:opacity-50`} onClick={() => { change({ graph_file: view !== "diff" && revision === "base" ? file.previous_path ?? file.path : file.path, graph_revision: view !== "diff" && file.change === "removed" ? "base" : revision, code_occurrence: null }); heading.current?.focus(); }}>
            <span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{file.path}</span><span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><span className="capitalize">{file.change}</span>{file.additions != null && file.deletions != null && <span className="font-mono tabular-nums"><span className="text-[var(--code-added)]">+{file.additions}</span> <span className="text-[var(--code-removed)]">−{file.deletions}</span></span>}</span>
          </button>)}
          {!workspace.files.length && <p className="p-4 text-sm text-muted-foreground">{search ? "No returned files match this filter." : "No visible changed files in this comparison."}</p>}
          <Pagination page={workspace.page} pageSize={10} total={workspace.files.length} loading={false} label="changed files" onPageChange={(value) => change({ graph_page: String(value), graph_file: null, code_occurrence: null })} />
        </div>
      </section>
      <section aria-label="Selected file" className="min-w-0 space-y-3">
        <h3 ref={heading} tabIndex={-1} aria-label="Selected file details" className="flex min-h-9 min-w-0 items-center gap-2 font-mono text-sm font-medium"><FileCode2 className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="break-words [overflow-wrap:anywhere]">{path ?? "Select a file"}</span></h3>
        {path && <Tabs value={view} onValueChange={(value) => change({ code_view: value, code_occurrence: null, graph_file: value === "diff" ? data.files.find((f) => f.previous_path === path)?.path ?? path : path })}>
          <TabsList aria-label="File information" className="max-w-full flex-wrap group-data-[orientation=horizontal]/tabs:h-auto">{views.map((value) => <TabsTrigger key={value} value={value} onPointerEnter={() => workspace.prefetch({ view: value as Schema["CodeNavigation"]["view"] })} onPointerLeave={() => workspace.prefetch()} onFocus={() => workspace.prefetch({ view: value as Schema["CodeNavigation"]["view"] })} onBlur={() => workspace.prefetch()}>{value[0].toUpperCase() + value.slice(1)}</TabsTrigger>)}</TabsList>
          <TabsContent value="diff" className="mt-4 min-w-0"><FileDiff key={`${path}:${data.head_sha}:${data.base_sha}:${data.policy_digest}`} query={{ ...fileQuery, data: workspace.data?.diff }} onRefresh={refresh} occurrenceId={params.get("code_occurrence")} openFinding={(item, element) => { trigger.current = element; change({ code_occurrence: item.occurrence_id, finding_tab: null }); }} closeFinding={() => { change({ code_occurrence: null, finding_tab: null }); requestAnimationFrame(() => (trigger.current?.isConnected ? trigger.current : heading.current)?.focus()); }} renderFinding={renderFinding} /></TabsContent>
          {view !== "diff" && <div className="mt-4 min-w-0 space-y-3">
            {workspace.loading ? <Loading /> : workspace.error ? <><ErrorNotice error={workspace.error} /><Button size="sm" variant="outline" onClick={refresh}>Refresh Code</Button></> : !data.graph_enabled ? <Empty title="Code graph is disabled">{admin ? <Link to={`/repositories/${repositoryId}/settings?tab=context`} className="underline">Enable it in Project context</Link> : "Ask an administrator to enable it in Project context."}</Empty> : <>
              <div className="flex flex-wrap items-center gap-2"><SearchSelect aria-label="Revision" className="sm:w-64" value={workspace.revision} options={[{ value: "head", label: `PR head · ${data.head_sha.slice(0, 7)}` }, { value: "base", label: `Merge base · ${data.merge_base_sha.slice(0, 7)}` }]} onChange={(value) => change({ graph_revision: value, code_view: view, graph_file: value === "base" ? data.files.find((f) => f.path === path)?.previous_path ?? path : data.files.find((f) => f.previous_path === path)?.path ?? path })} />
                <StatusHint value={snapshot?.status === "ready" ? "completed" : snapshot?.status ?? "unavailable"} label={snapshot?.status === "ready" ? "Indexed" : snapshot?.status === "partial" ? "Indexed with warnings" : undefined}><p>{indexDescriptions[snapshot?.status ?? ""] ?? "This revision has no usable index."}</p><CodeGraphCoverage coverage={snapshot?.coverage} />{data.graph_paused && <p>Indexing is paused.</p>}</StatusHint>
              </div>
              <ErrorNotice error={action.error} />
              {indexed ? <GraphFileDetails key={`${path}:${revision}:${snapshot?.snapshot_id}`} path={path} query={{ ...fileQuery, data: snapshot }} offset={positionValue.offset} setOffset={(offset) => setPosition((current) => ({ ...(current.key === positionKey ? current : positionValue), key: positionKey, offset }))} previous={positionValue.previous} setPrevious={(previous) => setPosition((current) => ({ ...(current.key === positionKey ? current : positionValue), key: positionKey, previous }))} range={{ start_byte: positionValue.start_byte, end_byte: positionValue.end_byte }} setRange={(range) => setPosition((current) => ({ ...(current.key === positionKey ? current : positionValue), key: positionKey, ...range }))} view={view} setView={(value) => change({ code_view: value })} onSelect={(file) => { change({ graph_file: file }); heading.current?.focus(); }} onRefresh={refresh} /> : <Empty title="This revision is not ready"><p>{data.graph_paused ? "Indexing is paused in Project context." : "Index this revision to explore related code."}</p>{admin && canWrite && !data.graph_paused && <Button className="mt-4" disabled={action.isPending || ["queued", "running"].includes(snapshot?.status ?? "")} onClick={() => action.mutate({ path: `/pulls/${prId}/code-graph/index`, body: pair }, { onSuccess: () => void refresh() })}>Index selected revision</Button>}</Empty>}
            </>}
          </div>}
        </Tabs>}
      </section>
    </div>
  </section>;
}
