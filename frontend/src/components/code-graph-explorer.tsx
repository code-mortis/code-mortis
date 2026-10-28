import { useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { FileCode2, RefreshCw } from "lucide-react";
import { api, ApiError, useAction, type Schema } from "../api";
import { Code, Empty, ErrorNotice, Loading, Status } from "./common";
import { CodeGraphCoverage } from "./code-graph-coverage";
import { FilterBar } from "./filter-bar";
import { SearchSelect } from "./search-select";
import { Button } from "./ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";
import { Pagination } from "./pagination";

type Revision = Schema["ExplorerRevision"];
const usable = (state?: string) => state === "ready" || state === "partial";
const rowClass = "w-full min-w-0 px-4 py-3 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5";
const indexDescriptions: Record<string, string> = {
  ready: "Indexing finished for this revision. Supported files are ready to explore.",
  partial: "Indexing finished with warnings. Some supported files could not be fully parsed; available results remain usable.",
  queued: "This revision is waiting to be indexed. Its status updates automatically.",
  running: "This revision is being indexed. Its status updates automatically.",
  failed: "Indexing failed. An administrator can retry this revision.",
  cancelled: "Indexing was cancelled. An administrator can index this revision again.",
};

function StatusHint({ value, label, children }: { value: string; label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <Tooltip open={open} onOpenChange={setOpen}>
    <TooltipTrigger asChild><button type="button" className="inline-flex min-h-11 items-center rounded-md outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-9" onClick={(event) => { event.preventDefault(); setOpen(true); }}>
      <Status value={value} label={label} />
    </button></TooltipTrigger>
    <TooltipContent sideOffset={6} collisionPadding={16} className="max-w-xs space-y-2 leading-relaxed">{children}</TooltipContent>
  </Tooltip>;
}

function FileDetails({ prId, path, revision, commit, onSelect, onRefresh }: {
  prId: string; path: string; revision: Revision; commit: string; onSelect: (path: string) => void; onRefresh: () => void;
}) {
  const location = useLocation();
  const [params] = useSearchParams();
  const [view, setView] = useState("dependencies");
  const [offset, setOffset] = useState(0);
  const [range, setRange] = useState({ start_byte: 0, end_byte: 1048576 });
  const [previous, setPrevious] = useState<number[]>([]);
  const request = { ...revision, path, operation: view === "definitions" ? "symbols" : view, offset, ...range };
  const query = useQuery<Schema["GraphRead"], ApiError>({
    queryKey: ["graph-file", prId, request],
    queryFn: ({ signal }) => api(`/pulls/${prId}/code-graph/query`, "POST", request, undefined, signal),
    retry: false, staleTime: 0, gcTime: 0, refetchInterval: 30000,
  });
  const source = query.data?.items.find((item) => item.kind === "source");
  const relations = query.data?.items.filter((item) => item.kind === "dependency") ?? [];
  function findingsLink(tab: string) {
    const next = new URLSearchParams(params);
    next.set("tab", tab);
    next.set(tab === "findings" ? "q" : `${tab}_q`, path);
    for (const key of tab === "findings" ? ["severity", "category", "open_page"] : [`${tab}_severity`, `${tab}_category`, `${tab}_page`]) next.delete(key);
    return { pathname: location.pathname, search: next.toString() };
  }
  return <section aria-label="Selected file" className="min-w-0 space-y-3">
    <div className="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1"><h3 className="flex min-w-0 items-center gap-2 font-mono text-sm font-medium"><FileCode2 aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><span className="break-words [overflow-wrap:anywhere]">{path}</span></h3>
      <code className="text-xs text-muted-foreground" title={`Source revision ${commit}`}>{commit.slice(0, 7)}</code></div>
    <Tabs value={view} onValueChange={(value) => { setView(value); setOffset(0); }}>
      <TabsList aria-label="File information" className="max-w-full flex-wrap group-data-[orientation=horizontal]/tabs:h-auto">
        <TabsTrigger value="dependencies">Dependencies</TabsTrigger><TabsTrigger value="definitions">Definitions</TabsTrigger><TabsTrigger value="source">Source</TabsTrigger>
      </TabsList>
      <div className="mt-4 min-w-0 space-y-4" aria-live="polite">
        {query.isPending ? <Loading /> : query.error ? <><ErrorNotice error={query.error} /><Button variant="outline" size="sm" onClick={() => { onRefresh(); void query.refetch(); }}>Refresh file</Button></>
          : !query.data || !usable(query.data.status) ? <Empty title="This revision is unavailable">Refresh impact to check indexing. An older snapshot will not replace this revision.</Empty>
            : <>
              {query.data.truncated && <p role="status" className="text-xs text-muted-foreground">This result is bounded. More relationships or source may exist.</p>}
              <TabsContent value="dependencies" className="min-w-0 space-y-4">
                {relations.length ? <ul aria-label="File dependencies" className="divide-y overflow-hidden rounded-lg border bg-card">
                  {relations.map((edge) => { const target = edge.path === path ? edge.target_path : edge.path; return <li key={`${edge.path}:${edge.start_byte}`}>
                    <button className={rowClass} onClick={() => onSelect(target)}><span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{target}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">{edge.path === path ? "Imported by this file" : "Imports this file"} · Line {edge.start_line}</span></button>
                  </li>; })}
                </ul> : <Empty title="No supported dependencies returned">This does not prove the file is unused. Callers and symbol references are not supported.</Empty>}
              </TabsContent>
              <TabsContent value="definitions" className="min-w-0 space-y-3">
                {query.data.items.length ? <ul aria-label="Indexed definitions" className="divide-y overflow-hidden rounded-lg border bg-card">
                  {query.data.items.filter((item) => item.kind === "symbol").map((item) => <li key={item.key}><button className={rowClass} onClick={() => {
                    setRange({ start_byte: item.start_byte, end_byte: item.end_byte }); setPrevious([]); setOffset(0); setView("source");
                  }}><span className="break-words font-mono text-xs [overflow-wrap:anywhere]">{item.name}</span><span className="ml-2 text-xs text-muted-foreground">{item.symbol_kind} · Lines {item.start_line}–{item.end_line}</span></button></li>)}
                </ul> : <Empty title="No indexed definitions">The file may contain no definitions or use an unsupported language. Its source may still be available.</Empty>}
                {(offset > 0 || query.data.next_cursor) && <nav aria-label="Definitions pagination" className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={!offset || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous definitions</Button>
                  <Button size="sm" variant="outline" disabled={!query.data.next_cursor || query.isFetching} onClick={() => setOffset(query.data!.next_cursor!.offset)}>Next definitions</Button>
                </nav>}
              </TabsContent>
              <TabsContent value="source" className="min-w-0 space-y-3">
                {source ? <>
                  <p className="text-xs text-muted-foreground">Lines {source.start_line}–{source.end_line} · Bytes {source.start_byte}–{source.end_byte} · Blob <code title={source.blob_sha}>{source.blob_sha.slice(0, 7)}</code></p>
                  <Code path={source.path} startLine={source.start_line} label="Indexed source">{source.text}</Code>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" disabled={!previous.length || query.isFetching} onClick={() => { setRange({ ...range, start_byte: previous.at(-1)! }); setPrevious(previous.slice(0, -1)); }}>Previous source</Button>
                    <Button size="sm" variant="outline" disabled={!query.data.next_cursor || query.isFetching} onClick={() => { setPrevious([...previous, range.start_byte]); setRange({ ...range, start_byte: query.data!.next_cursor!.start_byte }); }}>Next source</Button>
                    {(range.start_byte > 0 || range.end_byte < 1048576) && <Button size="sm" variant="ghost" onClick={() => { setRange({ start_byte: 0, end_byte: 1048576 }); setPrevious([]); }}>Read from start</Button>}
                  </div>
                </> : <><Empty title="Source unavailable">The file may be excluded, absent at this revision, or its retained objects may be busy or missing. Another revision is never substituted.</Empty><Button variant="outline" size="sm" onClick={() => { onRefresh(); void query.refetch(); }}>Refresh file</Button></>}
              </TabsContent>
            </>}
      </div>
    </Tabs>
    <nav aria-label="Saved findings for this file" className="flex flex-wrap items-center gap-x-4 text-xs text-muted-foreground">
      <span>Findings</span>{["findings", "resolved", "dismissed"].map((tab) => <Link key={tab} to={findingsLink(tab)} state={location.state} aria-label={`Search ${tab === "findings" ? "open" : tab} findings`} className="inline-flex min-h-11 items-center rounded-sm capitalize underline underline-offset-4 sm:min-h-8">{tab === "findings" ? "Open" : tab}</Link>)}
    </nav>
  </section>;
}

export function CodeGraphExplorer({ prId, repositoryId, admin, canWrite }: { prId: string; repositoryId: number; admin: boolean; canWrite: boolean }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const action = useAction();
  const heading = useRef<HTMLHeadingElement>(null);
  const pageValue = Number(params.get("graph_page") ?? 1);
  const page = Number.isInteger(pageValue) && pageValue >= 1 && pageValue <= 100 ? pageValue : 1;
  const search = (params.get("graph_q") ?? "").slice(0, 512);
  const revision = params.get("graph_revision") === "base" ? "base" : "head";
  const query = useQuery<Schema["GraphExplorer"], ApiError>({
    queryKey: ["graph-overview", prId, page, search],
    queryFn: ({ signal }) => api(`/pulls/${prId}/code-graph?page=${page}&q=${encodeURIComponent(search)}`, "GET", undefined, undefined, signal),
    retry: false, staleTime: 0, gcTime: 0, refetchInterval: 30000,
  });
  function change(values: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
    setParams(next, { state: location.state });
  }
  const data = query.data;
  if (query.isPending) return <Loading />;
  if (query.error || !data) return <div className="space-y-3"><ErrorNotice error={query.error} /><Button variant="outline" onClick={() => query.refetch()}>Retry impact</Button></div>;
  if (!data.enabled) return <Empty title="Code graph is disabled">{admin ? <Link className="underline" to={`/repositories/${repositoryId}/settings?tab=context`} state={location.state}>Enable the code graph in Project context</Link> : "Ask an administrator to enable the code graph in Project context."} to explore this pull request. Indexing uses server resources, without model calls.</Empty>;
  const files = data.files ?? [];
  const snapshot = data[revision];
  const path = params.get("graph_file") ?? files[0]?.path;
  const pair: Revision = { base_sha: data.base_sha, head_sha: data.head_sha, revision };
  return <section aria-label="Pull request impact" className="min-w-0 space-y-4">
    <FilterBar query={search} searchLabel="Filter changed files" searchPlaceholder="Search changed files…" filters={[]} onChange={() => {}} onSearch={(value) => change({ graph_q: value.trim(), graph_page: null, graph_file: null })}>
      <SearchSelect aria-label="Revision" className="sm:w-64" value={revision} onChange={(value) => change({ graph_revision: value })} options={[
        { value: "head", label: `PR head · ${data.head_sha.slice(0, 7)}` }, { value: "base", label: `Merge base · ${data.merge_base_sha?.slice(0, 7) ?? "unavailable"}` },
      ]} />
      <StatusHint key={`${revision}:${snapshot?.status}:${data.paused}`} value={snapshot?.status === "ready" ? "completed" : snapshot?.status ?? "unavailable"} label={snapshot?.status === "ready" ? "Indexed" : snapshot?.status === "partial" ? "Indexed with warnings" : undefined}>
          <p>{indexDescriptions[snapshot?.status ?? ""] ?? "This revision has no usable index. It may be missing, expired, or affected by a policy change."}</p>
          <div className="[&>div]:text-inherit"><CodeGraphCoverage coverage={snapshot?.coverage} /></div>
          {data.paused && <p>Indexing is paused in repository configuration.</p>}
      </StatusHint>
      <Button variant="outline" className="ml-auto bg-card" aria-label="Refresh impact" disabled={query.isFetching} onClick={() => query.refetch()}><RefreshCw aria-hidden="true" className="size-4" />Refresh</Button>
    </FilterBar>
    <ErrorNotice error={action.error} />
    <div className="grid min-w-0 items-start gap-6 lg:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <section aria-label="Changed files" className="min-w-0 space-y-3">
        <div className="flex min-h-9 flex-wrap items-center gap-x-2"><h3 className="text-sm font-semibold">Changed files <span className="ml-1 font-normal tabular-nums text-muted-foreground">{data.total}</span></h3>
          {data.comparison_truncated && <StatusHint value="partial" label="Limited comparison">GitHub returned at most 300 changed files. This list and its search may be incomplete.</StatusHint>}
        </div>
        <div className="divide-y overflow-hidden rounded-lg border bg-card">
          {files.map((file) => <button key={file.path} aria-pressed={file.path === path} className={`${rowClass} block aria-pressed:bg-muted`} onClick={() => { change({ graph_file: file.path, graph_revision: file.change === "removed" ? "base" : "head" }); heading.current?.focus(); }}>
            <span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{file.path}</span><span className="mt-1 block text-xs capitalize text-muted-foreground">{file.change}</span>
          </button>)}
          {!files.length && <p className="p-4 text-sm text-muted-foreground">{search ? "No returned files match this filter." : "No visible changed files in this comparison."}</p>}
          <Pagination page={data.page} pageSize={10} total={data.total} loading={query.isFetching} label="changed files" onPageChange={(value) => change({ graph_page: String(value), graph_file: null })} />
        </div>
      </section>
      <div className="min-w-0 space-y-3"><h2 ref={heading} tabIndex={-1} className="sr-only">Selected file details</h2>
        {!usable(snapshot?.status) ? <div className="lg:pt-12"><Empty title="This revision is not ready">
          <p>{data.paused ? "Indexing is paused in repository configuration." : ["queued", "running"].includes(snapshot?.status ?? "") ? "Indexing is in progress. Results will appear here when it finishes." : "Index this revision to explore its dependencies, definitions and source."}</p>
          {admin && canWrite && !data.paused ? <Button className="mt-4" disabled={action.isPending || ["queued", "running"].includes(snapshot?.status ?? "")} onClick={() => action.mutate({ path: `/pulls/${prId}/code-graph/index`, body: pair })}>{action.isPending ? "Queuing index…" : "Index selected revision"}</Button>
            : !data.paused && !["queued", "running"].includes(snapshot?.status ?? "") && <p className="mt-2">An administrator can start indexing.</p>}
        </Empty></div>
          : path && snapshot ? <FileDetails key={`${snapshot.snapshot_id}:${revision}:${path}`} prId={prId} path={path} revision={pair} commit={snapshot.commit_sha} onSelect={(file) => { change({ graph_file: file }); heading.current?.focus(); }} onRefresh={() => { void query.refetch(); }} />
            : <Empty title="Select a file">Choose a changed file to inspect its dependencies and source.</Empty>}
      </div>
    </div>
  </section>;
}
