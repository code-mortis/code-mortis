import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, FileCode2, GitBranch, Search, SlidersHorizontal } from "lucide-react";
import { api, type ApiError, type Schema } from "./api";
import { Code, Empty, ErrorNotice, Loading, PageTitle } from "./components/common";
import { RepositoryGraphCanvas, type GraphDirection, type GraphNode, type GraphView } from "./components/repository-graph-canvas";
import { RepositorySettingsShortcut } from "./components/repository-header";
import { SearchSelect } from "./components/search-select";
import { Button } from "./components/ui/button";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList } from "./components/ui/combobox";
import { InputGroupAddon } from "./components/ui/input-group";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { useSidebar } from "./components/ui/sidebar";

type Filters = { folder: string; language: string; direction: GraphDirection; hideUnlinked: boolean };
type FilterOption = { value: string; label: string };
type FilterOptions = { folders: FilterOption[]; languages: FilterOption[] };
const defaultFilters: Filters = { folder: "", language: "", direction: "both", hideUnlinked: false };
const languageLabels: Record<string, string> = {
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

function CodeGraphHeader({ actions }: { actions: ReactNode }) {
  return <header>
    <div className="flex items-start justify-between gap-2">
      <h1 id="repository-code-graph-title" className="text-2xl font-semibold tracking-tight sm:text-3xl">Code graph</h1>
      <div className="flex shrink-0 items-center gap-2 empty:hidden">{actions}</div>
    </div>
    <p className="mt-1 text-sm text-muted-foreground">Explore imports across the indexed repository.</p>
  </header>;
}

export default function RepositoryCodeGraph({ admin }: { admin: boolean }) {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const base = `/repositories/${repositoryId}/code-graph`;
  const scope = useId();
  const overview = useGraphRead<GraphView>(scope, `${base}/view`);
  const [attempt, setAttempt] = useState(0);
  const snapshot = overview.error ? undefined : overview.data;
  const settings = admin && repositoryId ? <RepositorySettingsShortcut repositoryId={repositoryId} section="code-graph" /> : null;
  function refresh() {
    setAttempt((previous) => previous + 1);
    void overview.refetch();
  }
  return <section className="pb-24" aria-labelledby="repository-code-graph-title">
    {snapshot?.commit_sha && snapshot.snapshot_id && (snapshot.status === "ready" || snapshot.status === "partial")
      ? <GraphWorkspace key={`${repositoryId}:${snapshot.snapshot_id}:${attempt}`} base={base} scope={scope} snapshot={snapshot} commit={snapshot.commit_sha} refresh={refresh} settings={settings} />
      : <>
        <CodeGraphHeader actions={settings} />
        {overview.error ? <div className="mt-6 space-y-3"><ErrorNotice error={overview.error} /><Button onClick={refresh} variant="outline">Refresh graph</Button></div>
          : overview.isPending ? <Loading />
            : <div className="mt-6"><Empty title={snapshot?.status === "disabled" ? "Code graph is off" : "Code graph index unavailable"}>
              <p>{snapshot?.status === "disabled" ? "Enable code graph to explore imports." : "Wait for a usable default branch index, then refresh."}</p>
              {admin && snapshot?.status === "disabled"
                ? <Button asChild variant="outline" className="mt-3"><Link to={`/repositories/${repositoryId}/settings?tab=code-graph`}>Repository settings</Link></Button>
                : <Button variant="outline" className="mt-3" onClick={refresh} disabled={overview.isFetching}>Refresh graph</Button>}
            </Empty></div>}
      </>}
  </section>;
}

export function RepositoryGraphFile() {
  const { repositoryId } = useParams<{ repositoryId: string }>();
  const [params] = useSearchParams();
  const path = params.get("path");
  const commit = params.get("commit_sha");
  const validRepository = !!repositoryId && /^[1-9][0-9]*$/.test(repositoryId);
  const valid = !!(validRepository && path && commit && /^[a-f0-9]{40}$/.test(commit));
  return <>
    <PageTitle title={valid ? filename(path) : "Indexed file"}
      titleMeta={valid && <code className="text-xs text-muted-foreground" title={commit}>{commit.slice(0, 7)}</code>}
      actions={<Button asChild variant="outline" size="sm">
        <Link to={validRepository ? "/repositories/" + repositoryId + "/code-graph" : "/repositories"}>
          <ArrowLeft aria-hidden="true" />{validRepository ? "Repository graph" : "Repositories"}
        </Link>
      </Button>}>
      {valid && <p className="wrap-anywhere">{path}</p>}
    </PageTitle>
    {valid
      ? <RepositoryGraphSource key={JSON.stringify([repositoryId, commit, path])} repositoryId={repositoryId} commit={commit} path={path} />
      : <Empty title="File link is incomplete">Choose an indexed file from the repository graph.</Empty>}
  </>;
}

function RepositoryGraphSource({ repositoryId, commit, path }: { repositoryId: string; commit: string; path: string }) {
  const scope = useId();
  const [page, setPage] = useState({ starts: [0], index: 0 });
  const start = page.starts[page.index];
  const params = new URLSearchParams({ commit_sha: commit, path, start_byte: String(start) });
  const query = useGraphRead<Schema["GraphRead"]>(scope, "/repositories/" + repositoryId + "/code-graph/source?" + params);
  const data = query.data;
  const source = data && String(data.repository_id) === repositoryId && data.commit_sha === commit && data.snapshot_id
    && (data.status === "ready" || data.status === "partial") && (data.source_state === "available" || data.source_state === "partial")
    ? data.items.find((item) => item.kind === "source" && item.path === path && item.start_byte === start) : undefined;
  const loading = query.isFetching || query.isPending;
  const retryable = query.error && [0, 408, 429, 500, 502, 503, 504].includes(query.error.status);
  const cursor = data?.next_cursor;
  const next = !loading && !query.error && source?.kind === "source" && cursor?.offset === 0
    && cursor.start_byte === source.end_byte && source.end_byte > start ? cursor.start_byte : null;

  return <>
    {loading ? <p role="status" className="text-sm text-muted-foreground">Loading source…</p>
      : query.error ? <div className="space-y-3">
        <ErrorNotice error={query.error} />
        {retryable && <Button size="sm" variant="outline" onClick={() => void query.refetch()}>Retry source</Button>}
      </div>
        : source?.kind !== "source" ? <Empty title="Source unavailable">This file could not be read from the requested index.</Empty>
          : source.text ? <Code path={path} startLine={source.start_line} label="Indexed source">{source.text}</Code>
            : <Empty title="Empty file">This file has no content.</Empty>}
    {(page.starts.length > 1 || next !== null) && <nav aria-label="Source pages" className="mt-4 flex flex-wrap gap-2">
      <Button size="sm" variant="outline" aria-disabled={page.index === 0} className="aria-disabled:opacity-50"
        onClick={() => { if (page.index > 0) setPage({ ...page, index: page.index - 1 }); }}>Previous page</Button>
      <Button size="sm" variant="outline" aria-disabled={next === null} className="aria-disabled:opacity-50"
        onClick={() => { if (next !== null) setPage({ starts: [...page.starts.slice(0, page.index + 1), next], index: page.index + 1 }); }}>Next page</Button>
    </nav>}
  </>;
}

function GraphWorkspace({ base, scope, snapshot, commit, refresh, settings }: {
  base: string; scope: string; snapshot: GraphView; commit: string; refresh: () => void; settings: ReactNode;
}) {
  const [filters, setFilters] = useState(defaultFilters);
  const [focus, setFocus] = useState<GraphNode | null>(null);
  const [depth, setDepth] = useState<1 | 2>(1);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [zoomControls, setZoomControls] = useState<HTMLDivElement | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const canvasView = useRef<{ graph: GraphView; key: string; focus: string | null }>({
    graph: snapshot, key: "folder:", focus: null,
  });
  const { open: sidebarOpen } = useSidebar();
  const filterOptions = useMemo<FilterOptions>(() => {
    const folders = new Set<string>();
    const languages = new Set<string>();
    for (const { path, kind, language } of snapshot.nodes) {
      if (language) languages.add(language);
      if (kind === "folder") folders.add(path);
      for (let slash = path.indexOf("/"); slash !== -1; slash = path.indexOf("/", slash + 1)) {
        folders.add(path.slice(0, slash));
      }
    }
    return {
      folders: [{ value: "", label: "All folders" }, ...Array.from(folders).sort().map((path) => ({ value: path, label: path }))],
      languages: Array.from(languages, (value) => ({ value, label: languageLabels[value] ?? value }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    };
  }, [snapshot.nodes]);
  useEffect(() => {
    const timeout = setTimeout(() => setQuery(search.trim()), 200);
    return () => clearTimeout(timeout);
  }, [search]);

  const hasFilters = !!(filters.folder || filters.language || filters.hideUnlinked);
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
  const accessError = [view.error, searchMatches.error].find(needsRefresh);
  const graph = hasFilters || focus ? view.error ? undefined : view.data : snapshot;
  const revisionChanged = graph && (graph.commit_sha !== commit || graph.snapshot_id !== snapshot.snapshot_id);
  const found = searchMatches.error ? [] : searchMatches.data?.files ?? [];
  const filesByPath = new Map(found.map((node) => [node.path, node]));
  const graphVisible = !!graph?.nodes.length && !view.error && !(view.isFetching && (hasFilters || focus));
  if (graphVisible && graph) canvasView.current = {
    graph, key: focus ? `file:${focus.path}:${depth}` : `folder:${filters.folder}`,
    focus: focus?.path ?? null,
  };

  function selectNode(node: GraphNode) {
    if (node.kind === "folder") {
      setFilters((previous) => ({ ...previous, folder: node.path }));
      setFocus(null);
    } else {
      setFocus(node);
      setDepth(1);
    }
    setSearch("");
    setQuery("");
  }

  function leaveFocus() {
    if (focus) setFocus(null);
    else setFilters((previous) => ({ ...previous, folder: "" }));
    searchInput.current?.focus();
  }

  if (accessError || revisionChanged) return <>
    <CodeGraphHeader actions={settings} />
    <div className="mt-6 space-y-3">
      {accessError ? <ErrorNotice error={accessError} /> : <Empty title="Indexed revision changed">Refresh to open the current index.</Empty>}
      <Button variant="outline" onClick={refresh}>Refresh graph</Button>
    </div>
  </>;

  return <>
    <CodeGraphHeader actions={<><GraphFilters filters={filters} options={filterOptions} focused={!!focus} onChange={setFilters} />{settings}</>} />
    <div className="mt-6 flex flex-wrap items-center justify-between gap-2">
      <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto">
        <span className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border bg-card px-2.5 text-sm" title={commit}>
          <GitBranch className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          {snapshot.branch && <span className="max-w-20 truncate font-medium sm:max-w-40">{snapshot.branch}</span>}
          <code className="border-l pl-2 text-xs text-muted-foreground">{commit.slice(0, 7)}</code>
        </span>
        <Combobox items={found.map((node) => node.path)} value={null} inputValue={search} filter={null} autoHighlight
          onInputValueChange={(value, event) => { if (event.reason === "input-change") setSearch(value); }}
          onValueChange={(path) => { const node = path ? filesByPath.get(path) : undefined; if (node) selectNode(node); }}>
          <ComboboxInput ref={searchInput} aria-label="Find a file" placeholder="Find a file…" maxLength={512}
            className="h-9 flex-1 bg-card sm:w-60 sm:flex-none" showTrigger={false}>
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
      </div>
      <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
        {(focus || filters.folder) && <Button variant="ghost" size="sm" onClick={leaveFocus}><ArrowLeft />Repository graph</Button>}
        {focus && <div role="group" aria-label="Connection depth" className="flex gap-0.5 rounded-md bg-muted p-0.5">
          {([1, 2] as const).map((value) => <Button key={value} size="xs" variant={depth === value ? "outline" : "ghost"}
            aria-pressed={depth === value} onClick={() => setDepth(value)}>{value} {value === 1 ? "hop" : "hops"}</Button>)}
        </div>}
        <span className="text-right text-xs text-muted-foreground" aria-live="polite">
          {graph ? `${graph.total_files} files · ${graph.total_links} resolved imports` : "Loading graph…"}
        </span>
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
      <div className={`absolute inset-0 ${graphVisible ? "" : "invisible pointer-events-none"}`}>
        <RepositoryGraphCanvas
          graph={canvasView.current.graph} viewKey={canvasView.current.key}
          focus={canvasView.current.focus} direction={canvasView.current.focus ? "both" : filters.direction}
          controls={graphVisible ? zoomControls : null} onSelect={selectNode}
        />
      </div>
    </div>
    <footer className={`fixed inset-x-0 bottom-0 z-10 bg-background px-5 pt-2 pb-[max(0.625rem,env(safe-area-inset-bottom))] sm:px-8 ${sidebarOpen ? "lg:left-(--sidebar-width)" : ""}`}>
      <div className="mx-auto flex min-h-9 max-w-6xl flex-wrap items-center justify-between gap-2">
        {focus && <Link relative="path" to={"../code/file?" + new URLSearchParams({ path: focus.path, commit_sha: commit })}
          className="min-w-0 rounded-sm hover:text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          aria-label={"Open " + focus.path}>
          <span className="flex items-start gap-2 text-sm font-semibold">
            <FileCode2 aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
            <span className="wrap-anywhere">{filename(focus.path)}</span>
          </span>
          <span className="block text-xs text-muted-foreground">{focus.outgoing} imports · {focus.incoming} imported by</span>
        </Link>}
        <div ref={setZoomControls} className="ml-auto" />
      </div>
    </footer>
  </>;
}

function GraphFilters({ filters, options, focused, onChange }: {
  filters: Filters; options: FilterOptions; focused: boolean; onChange: (filters: Filters) => void;
}) {
  const folderId = useId();
  const directionId = useId();
  const directionHintId = directionId + "-hint";
  const [folderDraft, setFolderDraft] = useState<string | null>(null);
  return <Popover onOpenChange={() => setFolderDraft(null)}>
    <PopoverTrigger asChild><Button variant="outline" size="sm" className="h-11 sm:h-8 pointer-coarse:h-11"><SlidersHorizontal />Filters</Button></PopoverTrigger>
    <PopoverContent aria-label="Filters" align="end" collisionPadding={16} className="grid w-80 max-w-[calc(100vw-2rem)] gap-3">
      <h2 className="text-sm font-semibold">Filters</h2>
      <form key={JSON.stringify(filters)} className="grid gap-3 text-sm" onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        onChange({
          folder: folderDraft ?? filters.folder,
          language: String(data.get("language") ?? filters.language),
          direction: (data.get("direction") as GraphDirection | null) ?? filters.direction,
          hideUnlinked: data.get("hideUnlinked") === "on",
        });
        setFolderDraft(null);
      }}>
        <div className="grid gap-1">
          <label htmlFor={folderId}>Folder</label>
          <SearchSelect id={folderId} options={options.folders} value={folderDraft ?? filters.folder} onChange={setFolderDraft} placeholder="All folders" />
        </div>
        <label className="grid gap-1">Language
          <select name="language" className="h-9 rounded-md border bg-card px-2.5" defaultValue={filters.language}>
            <option value="">All languages</option>
            {options.languages.map(({ value, label }) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <div className="grid gap-1">
          <label htmlFor={directionId}>Import direction</label>
          <select id={directionId} name="direction" className="h-9 rounded-md border bg-card px-2.5" defaultValue={filters.direction}
            aria-describedby={focused ? undefined : directionHintId}>
            <option value="both">Both</option><option value="incoming">Incoming</option><option value="outgoing">Outgoing</option>
          </select>
          {!focused && <span id={directionHintId} className="text-xs text-muted-foreground">Applies when you highlight a file.</span>}
        </div>
        <label className="flex items-center gap-2"><input name="hideUnlinked" type="checkbox" className="size-4 accent-primary" defaultChecked={filters.hideUnlinked} />Hide unlinked files</label>
        <div className="flex flex-wrap gap-2"><Button size="sm" type="submit">Apply</Button><Button size="sm" type="button" variant="ghost"
          onClick={(event) => { event.currentTarget.closest("form")?.reset(); setFolderDraft(null); onChange(defaultFilters); }}>Clear filters</Button></div>
      </form>
    </PopoverContent>
  </Popover>;
}
