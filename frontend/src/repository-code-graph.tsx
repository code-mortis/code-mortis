import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, FileCode2, GitBranch, Search, SlidersHorizontal, X } from "lucide-react";
import { api, type ApiError, type Schema } from "./api";
import { Code, Empty, ErrorNotice, Loading } from "./components/common";
import { RepositoryGraphCanvas, type GraphNode, type GraphView } from "./components/repository-graph-canvas";
import { Button } from "./components/ui/button";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "./components/ui/combobox";
import { Input } from "./components/ui/input";
import { InputGroupAddon } from "./components/ui/input-group";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { useSidebar } from "./components/ui/sidebar";

type Direction = "both" | "incoming" | "outgoing";
type Filters = { folder: string; language: string; direction: Direction; hideUnlinked: boolean };
const defaultFilters: Filters = { folder: "", language: "", direction: "both", hideUnlinked: false };
const languages: Record<string, string> = {
  bash: "Bash", c: "C", cpp: "C++", c_sharp: "C#", go: "Go", java: "Java", javascript: "JavaScript",
  php: "PHP", python: "Python", ruby: "Ruby", rust: "Rust", tsx: "TSX", typescript: "TypeScript",
};

function useGraphRead<T>(scope: string, path: string | null) {
  return useQuery<T, ApiError>({
    queryKey: ["repository-code-graph", scope, path],
    queryFn: ({ signal }) => api<T>(path!, "GET", undefined, undefined, signal),
    enabled: path !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
  });
}

const needsRefresh = (error: ApiError | null) => !!error && [401, 403, 404, 409].includes(error.status);
const filename = (path: string) => path.split("/").at(-1) || path;

export default function RepositoryCodeGraph({ admin }: { admin: boolean }) {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const base = `/repositories/${repositoryId}/code-graph`;
  const scope = useId();
  const overview = useGraphRead<GraphView>(scope, `${base}/view`);
  const [attempt, setAttempt] = useState(0);
  const snapshot = overview.error ? undefined : overview.data;
  function refresh() {
    setAttempt((previous) => previous + 1);
    void overview.refetch();
  }
  return <section className="pb-24" aria-labelledby="repository-code-graph-title">
    <h1 id="repository-code-graph-title" className="text-2xl font-semibold tracking-tight sm:text-3xl">Code graph</h1>
    <p className="mt-1 text-sm text-muted-foreground">Explore imports across the indexed repository.</p>
    {overview.error ? <div className="mt-6 space-y-3"><ErrorNotice error={overview.error} /><Button onClick={refresh} variant="outline">Refresh graph</Button></div>
      : overview.isPending ? <Loading />
        : snapshot?.commit_sha && snapshot.snapshot_id && (snapshot.status === "ready" || snapshot.status === "partial")
          ? <GraphWorkspace key={`${repositoryId}:${snapshot.snapshot_id}:${attempt}`} base={base} scope={scope} snapshot={snapshot} commit={snapshot.commit_sha} refresh={refresh} />
          : <div className="mt-6"><Empty title={snapshot?.status === "disabled" ? "Code graph is off" : "Code graph index unavailable"}>
            <p>{snapshot?.status === "disabled" ? "Enable code graph indexing in repository settings to explore imports." : "Wait for a usable default branch index, then refresh."}</p>
            {admin && snapshot?.status === "disabled"
              ? <Button asChild variant="outline" className="mt-3"><Link to={`/repositories/${repositoryId}/settings?tab=code-graph`}>Repository settings</Link></Button>
              : <Button variant="outline" className="mt-3" onClick={refresh} disabled={overview.isFetching}>Refresh graph</Button>}
          </Empty></div>}
  </section>;
}

function GraphWorkspace({ base, scope, snapshot, commit, refresh }: { base: string; scope: string; snapshot: GraphView; commit: string; refresh: () => void }) {
  const [filters, setFilters] = useState(defaultFilters);
  const [focus, setFocus] = useState<GraphNode | null>(null);
  const [depth, setDepth] = useState<1 | 2>(1);
  const [selected, setSelected] = useState<{ node: GraphNode; start: number; previous: number[] } | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [zoomControls, setZoomControls] = useState<HTMLDivElement | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const graphSurface = useRef<HTMLDivElement>(null);
  const sourcePanel = useRef<HTMLElement>(null);
  const canvasView = useRef<{ graph: GraphView; key: string; focus: string | null; sourceOpen: boolean }>({
    graph: snapshot, key: "folder:", focus: null, sourceOpen: false,
  });
  const { open: sidebarOpen } = useSidebar();
  useEffect(() => {
    const timeout = setTimeout(() => setQuery(search.trim()), 200);
    return () => clearTimeout(timeout);
  }, [search]);

  const hasFilters = !!(filters.folder || filters.language || filters.direction !== "both" || filters.hideUnlinked);
  const params = new URLSearchParams({ commit_sha: commit });
  if (filters.folder) params.set("folder", filters.folder);
  if (filters.language) params.set("language", filters.language);
  if (filters.direction !== "both" && focus) params.set("direction", filters.direction);
  if (filters.hideUnlinked) params.set("hide_unlinked", "true");
  if (focus) {
    params.set("focus", focus.path);
    params.set("depth", String(depth));
  }
  const view = useGraphRead<GraphView>(scope, hasFilters || focus ? `${base}/view?${params}` : null);
  const searchMatches = useGraphRead<Schema["RepositoryGraphFiles"]>(scope, query && query === search.trim()
    ? `${base}/files?${new URLSearchParams({ commit_sha: commit, query })}`
    : null);
  const source = useGraphRead<Schema["GraphRead"]>(scope, selected
    ? `${base}/source?${new URLSearchParams({ commit_sha: commit, path: selected.node.path, start_byte: String(selected.start) })}`
    : null);
  const accessError = [view.error, searchMatches.error, source.error].find(needsRefresh);
  const graph = hasFilters || focus ? view.error ? undefined : view.data : snapshot;
  const revisionChanged = (graph && (graph.commit_sha !== commit || graph.snapshot_id !== snapshot.snapshot_id))
    || (source.data && (source.data.commit_sha !== commit || source.data.snapshot_id !== snapshot.snapshot_id));
  const found = searchMatches.error ? [] : searchMatches.data?.files ?? [];
  const filesByPath = new Map(found.map((node) => [node.path, node]));
  const sourceItem = !source.error && source.data?.items.find((item) => item.kind === "source");
  const graphVisible = !!graph?.nodes.length && !view.error && !(view.isFetching && (hasFilters || focus));
  if (graphVisible && graph) canvasView.current = {
    graph, key: focus ? `file:${focus.path}:${depth}` : `folder:${filters.folder}`,
    focus: focus?.path ?? null, sourceOpen: !!selected,
  };

  function selectNode(node: GraphNode) {
    if (node.kind === "folder") {
      setFilters((previous) => ({ ...previous, folder: node.path }));
      setFocus(null);
      setSelected(null);
    } else {
      setFocus(node);
      setDepth(1);
      setSelected({ node, start: 0, previous: [] });
    }
    setSearch("");
    setQuery("");
  }

  function leaveFocus() {
    if (focus) {
      setFocus(null);
      setSelected(null);
    } else setFilters((previous) => ({ ...previous, folder: "" }));
    searchInput.current?.focus();
  }

  function closeSource(restoreFocus = sourcePanel.current?.contains(document.activeElement)) {
    if (restoreFocus) {
      const target = graphVisible ? graphSurface.current?.querySelector("canvas") : searchInput.current;
      target?.focus();
    }
    setSelected(null);
  }

  function showSourcePage(start: number, previous: number[]) {
    if (selected) setSelected({ ...selected, start, previous });
  }

  if (accessError || revisionChanged) return <div className="mt-6 space-y-3">
    {accessError ? <ErrorNotice error={accessError} /> : <Empty title="Indexed revision changed">Refresh to open the current index.</Empty>}
    <Button variant="outline" onClick={refresh}>Refresh graph</Button>
  </div>;

  return <div className="mt-6" onKeyDown={(event) => {
    if (event.key === "Escape" && selected && !event.defaultPrevented) {
      event.stopPropagation();
      closeSource();
    }
  }}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
        <Combobox items={found.map((node) => node.path)} value={null} inputValue={search} filter={null} autoHighlight
          onInputValueChange={(value, event) => { if (event.reason === "input-change") setSearch(value); }}
          onValueChange={(path) => { const node = path ? filesByPath.get(path) : undefined; if (node) selectNode(node); }}>
          <ComboboxInput ref={searchInput} aria-label="Find a file" placeholder="Find a file…" maxLength={512}
            className="h-9 w-52 bg-card sm:w-60" showTrigger={false}>
            <InputGroupAddon align="inline-start"><Search aria-hidden="true" /></InputGroupAddon>
          </ComboboxInput>
          <ComboboxContent className="w-80">
            <ComboboxEmpty className="px-3 py-2">
              {searchMatches.error ? "Search unavailable. Try a new search." : searchMatches.isFetching || search.trim() !== query ? "Searching files…" : search ? "No matching indexed files." : "Search by filename or path."}
            </ComboboxEmpty>
            <ComboboxList>
              {(path: string) => <ComboboxItem key={path} value={path} className="min-h-11 wrap-anywhere">
                <span className="min-w-0"><span className="block font-medium">{filename(path)}</span><span className="block text-xs text-muted-foreground">{path}</span></span>
              </ComboboxItem>}
            </ComboboxList>
            {searchMatches.data?.truncated && <p className="px-3 py-2 text-xs text-muted-foreground">Refine your search to see more matches.</p>}
          </ComboboxContent>
        </Combobox>
        <span className="text-xs text-muted-foreground" aria-live="polite">
          {graph ? `${graph.total_files} files · ${graph.total_links} resolved imports` : "Loading graph…"}
        </span>
      </div>
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {(focus || filters.folder) && <Button variant="ghost" size="sm" onClick={leaveFocus}><ArrowLeft />Repository graph</Button>}
        {focus && <div role="group" aria-label="Connection depth" className="flex gap-0.5 rounded-md bg-muted p-0.5">
          {([1, 2] as const).map((value) => <Button key={value} size="xs" variant={depth === value ? "outline" : "ghost"}
            aria-pressed={depth === value} onClick={() => setDepth(value)}>{value} {value === 1 ? "hop" : "hops"}</Button>)}
        </div>}
        <div className="flex items-center gap-2">
          <span className="inline-flex h-9 items-center gap-2 rounded-md border bg-card px-2.5 text-sm" title={commit}>
            <GitBranch className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            {snapshot.branch && <span className="max-w-20 truncate font-medium sm:max-w-40">{snapshot.branch}</span>}
            <code className="border-l pl-2 text-xs text-muted-foreground">{commit.slice(0, 7)}</code>
          </span>
          <GraphFilters filters={filters} focused={!!focus} onChange={setFilters} />
        </div>
      </div>
    </div>
    {(snapshot.status === "partial" || (snapshot.observed_sha && snapshot.observed_sha !== commit) || graph?.mode === "folders" || graph?.truncated) && (
      <p className="mt-2 text-xs text-muted-foreground">
        {[
          snapshot.status === "partial" && "Some imports could not be resolved.",
          snapshot.observed_sha && snapshot.observed_sha !== commit && "The default branch has changed since this index was created.",
          graph?.mode === "folders" && "Folders summarize the index. Select a folder or search for a file.",
          graph?.truncated && "This view is limited. Narrow the graph or search for a file.",
        ].filter(Boolean).join(" ")}
      </p>
    )}
    <div className="relative mt-1 h-[max(22rem,calc(100dvh-20rem))] min-w-0">
      {view.error ? <ErrorNotice error={view.error} />
        : graph?.status === "unavailable" || graph?.status === "disabled" ? <Empty title="Graph unavailable"><Button variant="outline" onClick={refresh}>Refresh graph</Button></Empty>
          : !graph || view.isFetching && (hasFilters || focus) ? <div className="absolute inset-x-0 top-4"><Loading /></div>
            : !graph.nodes.length ? <Empty title="No matching files"><Button variant="outline" onClick={() => setFilters(defaultFilters)}>Clear filters</Button></Empty>
              : null}
      <div ref={graphSurface} className={`absolute inset-0 ${graphVisible ? "" : "invisible pointer-events-none"}`}>
        <RepositoryGraphCanvas
          graph={canvasView.current.graph} viewKey={canvasView.current.key}
          focus={canvasView.current.focus} sourceOpen={canvasView.current.sourceOpen}
          controls={graphVisible ? zoomControls : null} onSelect={selectNode}
        />
      </div>
      {selected && <aside ref={sourcePanel} className="absolute right-0 top-1 z-10 flex max-h-full w-full max-w-[400px] flex-col overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-md" aria-labelledby="repository-source-title">
        <div className="flex items-start gap-2 border-b p-3">
          <FileCode2 className="mt-1 size-4 shrink-0 text-primary" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h2 className="wrap-anywhere font-semibold" id="repository-source-title">{filename(selected.node.path)}</h2>
            <p className="wrap-anywhere text-xs text-muted-foreground">{selected.node.path}</p>
          </div>
          <Button variant="ghost" size="icon-sm" aria-label="Close file" onClick={() => closeSource(true)}><X /></Button>
        </div>
        <div className="overflow-auto">
          {source.error ? <div className="p-3"><ErrorNotice error={source.error} /></div>
            : source.isPending || source.isFetching ? <Loading />
              : sourceItem && sourceItem.path === selected.node.path ? <>
                <Code path={sourceItem.path} startLine={sourceItem.start_line} label="Indexed source" className="rounded-none border-0">{sourceItem.text}</Code>
                {(selected.previous.length > 0 || source.data?.next_cursor) && <div className="flex flex-wrap gap-2 border-t p-2">
                  {selected.previous.length > 0 && <Button variant="outline" size="sm" onClick={() => showSourcePage(selected.previous.at(-1)!, selected.previous.slice(0, -1))}>Previous lines</Button>}
                  {source.data?.next_cursor && <Button variant="outline" size="sm" onClick={() => showSourcePage(source.data!.next_cursor!.start_byte, [...selected.previous, selected.start])}>Next lines</Button>}
                </div>}
              </>
                : <Empty title="Source unavailable" className="rounded-none border-0">The indexed file’s retained source could not be read.</Empty>}
        </div>
      </aside>}
    </div>
    <div className={`fixed inset-x-0 bottom-0 z-10 bg-background px-5 pt-2 pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:px-8 ${sidebarOpen ? "lg:left-(--sidebar-width)" : ""}`}>
      <div className="mx-auto flex min-h-9 max-w-6xl flex-wrap items-center justify-between gap-2">
        {focus ? <div className="flex min-w-0 items-start gap-2">
          <FileCode2 aria-hidden="true" className="mt-1 size-4 shrink-0 text-primary" />
          <div className="min-w-0"><p className="wrap-anywhere text-sm font-semibold">{filename(focus.path)}</p>
            <p className="text-xs text-muted-foreground">{focus.outgoing} imports · {focus.incoming} imported by</p></div>
        </div> : <span />}
        <div ref={setZoomControls} className="ml-auto" />
      </div>
    </div>
  </div>;
}

function GraphFilters({ filters, focused, onChange }: { filters: Filters; focused: boolean; onChange: (filters: Filters) => void }) {
  return <Popover>
    <PopoverTrigger asChild><Button variant="outline"><SlidersHorizontal />Filters</Button></PopoverTrigger>
    <PopoverContent align="end" className="grid w-60 gap-3">
      <h2 className="text-sm font-semibold">Filters</h2>
      <form key={JSON.stringify(filters)} className="grid gap-3 text-sm" onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        onChange({
          folder: String(data.get("folder") ?? filters.folder).trim().replace(/\/+$/, ""),
          language: String(data.get("language") ?? filters.language),
          direction: (data.get("direction") as Direction | null) ?? filters.direction,
          hideUnlinked: data.get("hideUnlinked") === "on",
        });
      }}>
        <label className="grid gap-1">Folder<Input name="folder" defaultValue={filters.folder} placeholder="All folders" maxLength={2000} /></label>
        <label className="grid gap-1">Language
          <select name="language" className="h-9 rounded-md border bg-card px-2.5" defaultValue={filters.language}>
            <option value="">All languages</option>
            {Object.entries(languages).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="grid gap-1">Import direction
          <select name="direction" className="h-9 rounded-md border bg-card px-2.5" defaultValue={filters.direction} disabled={!focused}>
            <option value="both">Both</option><option value="incoming">Incoming</option><option value="outgoing">Outgoing</option>
          </select>
        </label>
        <label className="flex items-center gap-2"><input name="hideUnlinked" type="checkbox" className="size-4 accent-primary" defaultChecked={filters.hideUnlinked} />Hide unlinked files</label>
        <div className="flex flex-wrap gap-2"><Button size="sm" type="submit">Apply</Button><Button size="sm" type="button" variant="ghost"
          onClick={(event) => { event.currentTarget.closest("form")?.reset(); onChange(defaultFilters); }}>Clear filters</Button></div>
      </form>
    </PopoverContent>
  </Popover>;
}
