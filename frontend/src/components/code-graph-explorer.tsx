import { useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { ApiError, type Schema } from "../api";
import { Code, Empty, ErrorNotice, Loading, Status } from "./common";
import { Button } from "./ui/button";
import { TabsContent } from "./ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const usable = (state?: string) => state === "ready" || state === "partial";
export const rowClass = "w-full min-w-0 px-4 py-3 text-left text-sm transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-5";
export const indexDescriptions: Record<string, string> = {
  ready: "Indexing finished for this revision. Supported files are ready to explore.",
  partial: "Indexing finished with warnings. Some supported files could not be fully parsed; available results remain usable.",
  queued: "This revision is waiting to be indexed. Its status updates automatically.",
  running: "This revision is being indexed. Its status updates automatically.",
  failed: "Indexing failed. An administrator can retry this revision.",
  cancelled: "Indexing was cancelled. An administrator can index this revision again.",
};

export function StatusHint({ value, label, children }: { value: string; label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <Tooltip open={open} onOpenChange={setOpen}>
    <TooltipTrigger asChild><button type="button" className="inline-flex min-h-11 items-center rounded-md outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-9" onClick={(event) => { event.preventDefault(); setOpen(true); }}>
      <Status value={value} label={label} />
    </button></TooltipTrigger>
    <TooltipContent sideOffset={6} collisionPadding={16} className="max-w-xs space-y-2 leading-relaxed">{children}</TooltipContent>
  </Tooltip>;
}

export function GraphFileDetails({ path, view, setView, onSelect, onRefresh, query, offset, setOffset, range, setRange, previous, setPrevious }: {
  path: string; view: string; setView: (view: string) => void; onSelect: (path: string) => void; onRefresh: () => void;
  query: { data?: Schema["GraphRead"] | null; error: ApiError | null; isPending: boolean; isFetching: boolean; refetch: () => void };
  previous: number[]; setPrevious: (previous: number[]) => void;
  offset: number; setOffset: (offset: number) => void;
  range: { start_byte: number; end_byte: number }; setRange: (range: { start_byte: number; end_byte: number }) => void;
}) {
  const location = useLocation();
  const [params] = useSearchParams();
  const source = query.data?.items.find((item) => item.kind === "source");
  const hasSourcePages = previous.length > 0 || !!query.data?.next_cursor;
  const hasSourceRange = range.start_byte > 0 || range.end_byte < 1048576;
  const relations = query.data?.items.filter((item) => item.kind === "dependency") ?? [];
  function findingsLink(tab: string) {
    const next = new URLSearchParams(params);
    next.set("tab", tab);
    next.set(tab === "findings" ? "q" : `${tab}_q`, path);
    for (const key of tab === "findings" ? ["severity", "category", "open_page"] : [`${tab}_severity`, `${tab}_category`, `${tab}_page`]) next.delete(key);
    return { pathname: location.pathname, search: next.toString() };
  }
  return <div className="min-w-0">
      <div className="min-w-0" aria-live="polite">
        {query.isPending ? <div className="px-4 sm:px-5"><Loading /></div> : query.error ? <div className="space-y-3 p-4"><ErrorNotice error={query.error} /><Button variant="outline" size="sm" onClick={() => { onRefresh(); }}>Refresh file</Button></div>
          : !query.data || !usable(query.data.status) ? <Empty className="rounded-none border-0" title="This revision is unavailable">Refresh Code to check indexing. An older snapshot will not replace this revision.</Empty>
            : <>
              <TabsContent value="dependencies" className="min-w-0">
                {relations.length ? <ul aria-label="File dependencies" className="divide-y">
                  {relations.map((edge) => { const target = edge.path === path ? edge.target_path : edge.path; return <li key={`${edge.path}:${edge.start_byte}`}>
                    <button className={rowClass} onClick={() => onSelect(target)}><span className="block break-words font-mono text-xs [overflow-wrap:anywhere]">{target}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">{edge.path === path ? "Imported by this file" : "Imports this file"} · Line {edge.start_line}</span></button>
                  </li>; })}
                </ul> : <Empty className="rounded-none border-0" title="No supported dependencies returned">This does not prove the file is unused. Callers and symbol references are not supported.</Empty>}
              </TabsContent>
              <TabsContent value="definitions" className="min-w-0">
                {query.data.items.length ? <ul aria-label="Indexed definitions" className="divide-y">
                  {query.data.items.filter((item) => item.kind === "symbol").map((item) => <li key={item.key}><button className={rowClass} onClick={() => {
                    setRange({ start_byte: item.start_byte, end_byte: item.end_byte }); setPrevious([]); setOffset(0); setView("source");
                  }}><span className="break-words font-mono text-xs [overflow-wrap:anywhere]">{item.name}</span><span className="ml-2 text-xs text-muted-foreground">{item.symbol_kind} · Lines {item.start_line}–{item.end_line}</span></button></li>)}
                </ul> : <Empty className="rounded-none border-0" title="No indexed definitions">The file may contain no definitions or use an unsupported language. Its source may still be available.</Empty>}
                {(offset > 0 || query.data.next_cursor) && <nav aria-label="Definitions pagination" className="flex flex-wrap gap-2 border-t px-4 py-3 sm:px-5">
                  <Button size="sm" variant="outline" disabled={!offset || query.isFetching} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous definitions</Button>
                  <Button size="sm" variant="outline" disabled={!query.data.next_cursor || query.isFetching} onClick={() => setOffset(query.data!.next_cursor!.offset)}>Next definitions</Button>
                </nav>}
              </TabsContent>
              <TabsContent value="source" className="min-w-0">
                {source ? <>
                  <p className="border-b px-4 py-3 text-xs text-muted-foreground sm:px-5">Lines {source.start_line}–{source.end_line} · Bytes {source.start_byte}–{source.end_byte} · Blob <code title={source.blob_sha}>{source.blob_sha.slice(0, 7)}</code></p>
                  <Code className="rounded-none border-0 bg-card sm:px-5 focus-visible:-outline-offset-2" path={source.path} startLine={source.start_line} label="Indexed source">{source.text}</Code>
                  {(hasSourcePages || hasSourceRange) && <div className="flex flex-wrap gap-2 border-t px-4 py-3 sm:px-5">
                    {hasSourcePages && <>
                    <Button size="sm" variant="outline" disabled={!previous.length || query.isFetching} onClick={() => { setRange({ ...range, start_byte: previous.at(-1)! }); setPrevious(previous.slice(0, -1)); }}>Previous source</Button>
                    <Button size="sm" variant="outline" disabled={!query.data.next_cursor || query.isFetching} onClick={() => { setPrevious([...previous, range.start_byte]); setRange({ ...range, start_byte: query.data!.next_cursor!.start_byte }); }}>Next source</Button>
                    </>}
                    {hasSourceRange && <Button size="sm" variant="ghost" onClick={() => { setRange({ start_byte: 0, end_byte: 1048576 }); setPrevious([]); }}>Read from start</Button>}
                  </div>}
                </> : <Empty className="rounded-none border-0" title="Source unavailable"><p>The file may be excluded, absent at this revision, or its retained objects may be busy or missing. Another revision is never substituted.</p><Button className="mt-4" variant="outline" size="sm" onClick={() => { onRefresh(); }}>Refresh file</Button></Empty>}
              </TabsContent>
            </>}
      </div>
    <nav aria-label="Saved findings for this file" className="flex flex-wrap items-center gap-x-4 border-t px-4 py-3 text-xs text-muted-foreground sm:px-5">
      <span>Findings</span>{["findings", "resolved", "dismissed"].map((tab) => <Link key={tab} to={findingsLink(tab)} state={location.state} aria-label={`Search ${tab === "findings" ? "open" : tab} findings`} className="inline-flex min-h-11 items-center rounded-sm capitalize underline underline-offset-4 sm:min-h-8">{tab === "findings" ? "Open" : tab}</Link>)}
    </nav>
  </div>;
}
