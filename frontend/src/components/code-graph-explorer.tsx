import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ArrowRight, RefreshCw } from "lucide-react";
import { api, ApiError, useAction, type Schema } from "../api";
import { Code, Empty, ErrorNotice, Loading, Status } from "./common";
import { CodeGraphCoverage } from "./code-graph-coverage";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { Pagination } from "./pagination";

type Revision = Schema["ExplorerRevision"];
type Relation = Schema["GraphRelation"];
const usable = (state?: string) => state === "ready" || state === "partial";
const rowClass = "w-full min-w-0 px-4 py-3 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5";

function DependencyMap({ path, relations }: { path: string; relations: Relation[] }) {
  const incoming = [...new Set(relations.filter((edge) => edge.target_path === path).map((edge) => edge.path))];
  const outgoing = [...new Set(relations.filter((edge) => edge.path === path).map((edge) => edge.target_path))];
  function nodes(paths: string[], name: string) {
    return <div className="min-w-0 space-y-2"><p className="text-xs text-muted-foreground">{name}</p>
      {paths.slice(0, 3).map((file) => <div key={file} className="truncate rounded-md border bg-card p-3 font-mono text-xs" title={file}>{file}</div>)}
      {!paths.length && <p className="text-xs text-muted-foreground">None returned</p>}
      {paths.length > 3 && <p className="text-xs text-muted-foreground">{paths.length - 3} more in the list</p>}
    </div>;
  }
  return <div aria-hidden="true" className="grid min-w-0 items-center gap-3 rounded-lg bg-muted/30 p-4 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)]">
    {nodes(incoming, "Imports this file")}<ArrowRight className="size-4 rotate-90 justify-self-center text-muted-foreground sm:rotate-0" />
    <div className="min-w-0 rounded-md border border-primary bg-card p-3"><p className="mb-1 text-xs text-muted-foreground">Selected file</p><p className="break-words font-mono text-xs [overflow-wrap:anywhere]">{path}</p></div>
    <ArrowRight className="size-4 rotate-90 justify-self-center text-muted-foreground sm:rotate-0" />{nodes(outgoing, "This file imports")}
  </div>;
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
  return <section aria-label="Selected file" className="min-w-0 space-y-4">
    <div><h3 className="break-words font-mono text-sm font-medium [overflow-wrap:anywhere]">{path}</h3>
      <p className="mt-1 text-xs text-muted-foreground">Source revision <code title={commit}>{commit.slice(0, 7)}</code></p></div>
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
                <DependencyMap path={path} relations={relations} />
                {relations.length ? <ul aria-label="File dependencies" className="divide-y overflow-hidden rounded-lg border bg-card">
                  {relations.map((edge) => { const target = edge.path === path ? edge.target_path : edge.path; return <li key={`${edge.path}:${edge.start_byte}`}>
                    <button className={rowClass} onClick={() => onSelect(target)}><span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{target}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">{edge.path === path ? "Imported by selected file" : "Imports selected file"} · {edge.path}:{edge.start_line} · Relative import</span></button>
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
    <div className="space-y-2 border-t pt-4 text-xs text-muted-foreground"><p>Saved findings keep their original evidence and current resolution. They may describe a different commit.</p>
      <div className="flex flex-wrap gap-4">{["findings", "resolved", "dismissed"].map((tab) => <Link key={tab} to={findingsLink(tab)} state={location.state} className="inline-flex min-h-8 items-center rounded-sm underline underline-offset-4">Search {tab === "findings" ? "open findings" : `${tab} findings`}</Link>)}</div>
    </div>
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
  return <section aria-label="Pull request impact" className="min-w-0 space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h2 className="text-base font-semibold">Explore change impact</h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">Trace file imports from this PR to exact source. Dependencies suggest what to inspect; they do not prove a defect.</p></div>
      <Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => query.refetch()}><RefreshCw aria-hidden="true" className="size-4" />Refresh impact</Button></div>
    <div className="flex flex-wrap items-end gap-4 border-y py-4">
      <label className="space-y-1.5 text-sm"><span className="block font-medium">Revision</span><select className="h-9 rounded-md border bg-card px-3 text-sm" value={revision} onChange={(event) => change({ graph_revision: event.target.value })}>
        <option value="head">PR head · {data.head_sha.slice(0, 7)}</option><option value="base">Merge base · {data.merge_base_sha?.slice(0, 7)}</option>
      </select></label>
      <div className="flex min-h-9 items-center gap-2 text-xs"><span>Index</span><Status value={snapshot?.status === "ready" ? "completed" : snapshot?.status ?? "unavailable"} label={snapshot?.status === "ready" ? "Indexed" : snapshot?.status === "partial" ? "Indexed with warnings" : undefined} /></div>
      {!usable(snapshot?.status) && admin && canWrite && !data.paused && <Button variant="outline" size="sm" disabled={action.isPending || ["queued", "running"].includes(snapshot?.status ?? "")} onClick={() => action.mutate({ path: `/pulls/${prId}/code-graph/index`, body: pair })}>{action.isPending ? "Queuing index…" : "Index selected revision"}</Button>}
      <p className="w-full text-xs text-muted-foreground">Last synced PR revisions. {data.paused ? "Indexing is paused in repository configuration. " : ""}The latest default-branch revision and up to 10 recent revisions are retained; recent revisions expire after 7 days. Selecting a revision does not index it.</p>
    </div>
    <ErrorNotice error={action.error} />
    {data.comparison_truncated && <p role="status" className="text-sm text-muted-foreground">The comparison reached GitHub's 300-file limit. This list may be incomplete; filtering searches only the returned files.</p>}
    {snapshot?.status === "partial" && <p role="status" className="text-xs text-muted-foreground">Indexing finished with warnings. Available results remain usable.</p>}
    <CodeGraphCoverage coverage={snapshot?.coverage} />
    <p className="text-xs text-muted-foreground">Relative file imports are supported for Python and JavaScript/TypeScript. Callers and symbol references are not supported. Excluded paths are hidden.</p>
    <div className="grid min-w-0 items-start gap-6 xl:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
      <section aria-label="Changed files" className="min-w-0 space-y-3">
        <form onSubmit={(event) => { event.preventDefault(); const value = new FormData(event.currentTarget).get("path") as string; change({ graph_q: value.trim(), graph_page: null, graph_file: null }); }} className="flex gap-2">
          <Input key={search} name="path" defaultValue={search} maxLength={512} aria-label="Filter changed files" placeholder="Filter paths…" className="min-w-0" /><Button type="submit" variant="outline" size="sm" className="h-9">Filter</Button>
        </form>
        <div className="divide-y overflow-hidden rounded-lg border bg-card">
          {files.map((file) => <button key={file.path} aria-pressed={file.path === path} className={`${rowClass} block aria-pressed:bg-muted`} onClick={() => { change({ graph_file: file.path, graph_revision: file.change === "removed" ? "base" : "head" }); heading.current?.focus(); }}>
            <span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{file.path}</span><span className="mt-1 block text-xs capitalize text-muted-foreground">{file.change}</span>
          </button>)}
          {!files.length && <p className="p-4 text-sm text-muted-foreground">{search ? "No returned files match this filter." : "No visible changed files in this comparison."}</p>}
          <Pagination page={data.page} pageSize={10} total={data.total} loading={query.isFetching} label="changed files" onPageChange={(value) => change({ graph_page: String(value), graph_file: null })} />
        </div>
      </section>
      <div className="min-w-0 space-y-3"><h2 ref={heading} tabIndex={-1} className="sr-only">Selected file details</h2>
        {!usable(snapshot?.status) ? <Empty title="This revision is not ready">{["queued", "running"].includes(snapshot?.status ?? "") ? "Indexing is in progress. Refresh impact to check its status." : "Its index is missing, expired, failed, or does not match the current policy. An administrator can index this revision."}</Empty>
          : path && snapshot ? <FileDetails key={`${snapshot.snapshot_id}:${revision}:${path}`} prId={prId} path={path} revision={pair} commit={snapshot.commit_sha} onSelect={(file) => { change({ graph_file: file }); heading.current?.focus(); }} onRefresh={() => { void query.refetch(); }} />
            : <Empty title="Select a file">Choose a changed file to inspect its dependencies and source.</Empty>}
      </div>
    </div>
  </section>;
}
