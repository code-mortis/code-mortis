import { useId, useRef, type ReactNode, type Ref } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileCode2 } from "lucide-react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, type ApiError, type Schema } from "../api";
import { useIsMobile } from "../hooks/use-mobile";
import { cn } from "../lib/utils";
import { ErrorNotice, Loading, Markdown } from "./common";
import { repositoryActivityLink } from "./repository-header";
import { Button } from "./ui/button";

type Diagram = Schema["SummaryDiagram"];
type Group = Schema["CodeGroup"];

function OverviewSection({ title, children, defaultOpen = true, meta, detailsRef }: {
  title: string; children: ReactNode; defaultOpen?: boolean; meta?: ReactNode; detailsRef?: Ref<HTMLDetailsElement>;
}) {
  return <section aria-label={title} className="min-w-0">
    <details ref={detailsRef} open={defaultOpen} className="group/overview">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-sm outline-offset-2 hover:text-primary focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden">
        <h2 className="font-semibold">{title}</h2>
        <span className="flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
          {meta}
          <ChevronDown aria-hidden="true" className="size-4 shrink-0 transition-transform group-open/overview:rotate-180 motion-reduce:transition-none" />
        </span>
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  </section>;
}

function layoutDiagram(diagram: Diagram, compact: boolean) {
  // Draw sources before targets. If the remaining graph cycles, start the next layer at its first node.
  const pending = new Set(diagram.nodes.map((node) => node.id));
  const layers: string[][] = [];
  const limit = compact ? 2 : 4;
  while (pending.size) {
    const ids = [...pending];
    const roots = ids.filter((id) => !diagram.edges.some(({ source, target }) => target === id && source !== id && pending.has(source)));
    const layer = roots.length ? roots.slice(0, limit) : ids.slice(0, 1);
    layers.push(layer);
    layer.forEach((id) => pending.delete(id));
  }
  const nodeWidth = compact ? 140 : 188;
  const nodeHeight = 44;
  const stepX = nodeWidth + 24;
  const stepY = nodeHeight + 34;
  const widestLayer = Math.max(...layers.map((layer) => layer.length));
  const width = (widestLayer - 1) * stepX + nodeWidth + 40;
  const height = (layers.length - 1) * stepY + nodeHeight + 36;
  const positions = new Map(layers.flatMap((layer, rank) => layer.map((id, index) => [
    id, {
      x: (width - (layer.length - 1) * stepX - nodeWidth) / 2 + index * stepX,
      y: 18 + rank * stepY,
    },
  ] as const)));
  const edges = diagram.edges.flatMap(({ source, target }) => {
    const from = positions.get(source);
    const to = positions.get(target);
    if (!from || !to) return [];
    if (source === target) {
      const x = from.x + nodeWidth - 10;
      const y = from.y + 10;
      return [`M ${x} ${y} C ${x + 28} ${y - 32} ${x + 32} ${y + 30} ${x + 8} ${y + 22}`];
    }
    const forwards = to.y > from.y;
    const start = from.y + (forwards ? nodeHeight : 0);
    const end = to.y + (forwards ? 0 : nodeHeight);
    const bend = (start + end) / 2;
    const offset = nodeWidth / 2 + (forwards ? 0 : 10);
    return [`M ${from.x + offset} ${start} C ${from.x + offset} ${bend} ${to.x + offset} ${bend} ${to.x + offset} ${end}`];
  });
  return { width, height, nodeWidth, nodeHeight, positions, edges };
}

function ChangeDiagram({ diagram, selected, onSelect, controls }: {
  diagram: Diagram; selected?: string; onSelect: (id: string) => void; controls: string;
}) {
  const compact = useIsMobile();
  const arrow = useId();
  const { width, height, nodeWidth, nodeHeight, positions, edges } = layoutDiagram(diagram, compact);
  const labels = new Map(diagram.nodes.map((node) => [node.id, node.label]));
  return <OverviewSection title="Change diagram"
    meta={<span className="hidden text-right group-open/overview:block">Select a node to see its files</span>}>
    <div className="overflow-x-auto rounded-lg border bg-muted/30">
      <div className="relative mx-auto" style={{ width, height }}>
        <svg className="pointer-events-none absolute inset-0 size-full text-muted-foreground" aria-hidden="true" viewBox={`0 0 ${width} ${height}`}>
          <defs><marker id={arrow} markerWidth={7} markerHeight={7} refX={6} refY={3.5} orient="auto">
            <path d="M 0 0 L 7 3.5 L 0 7 Z" fill="currentColor" />
          </marker></defs>
          {edges.map((path, index) => <path key={index} d={path} fill="none" stroke="currentColor" strokeWidth={1.25} vectorEffect="non-scaling-stroke" markerEnd={`url(#${arrow})`} />)}
        </svg>
        {diagram.nodes.map((node) => {
          const point = positions.get(node.id)!;
          return <Button key={node.id} type="button" variant="outline" size="sm" aria-controls={controls} aria-pressed={node.id === selected}
            title={node.label} onClick={() => onSelect(node.id)}
            className="absolute whitespace-normal bg-card px-2 aria-pressed:border-primary aria-pressed:bg-accent aria-pressed:text-accent-foreground dark:aria-pressed:border-primary dark:aria-pressed:bg-accent"
            style={{ left: point.x, top: point.y, width: nodeWidth, height: nodeHeight }}>
            <span className="line-clamp-2 break-words [overflow-wrap:anywhere]">{node.label}</span>
          </Button>;
        })}
      </div>
    </div>
    {diagram.edges.length > 0 && <ul className="sr-only" aria-label="Diagram connections">
      {diagram.edges.map(({ source, target }, index) => <li key={index}>{labels.get(source)} leads to {labels.get(target)}</li>)}
    </ul>}
  </OverviewSection>;
}

function Files({ paths }: { paths: string[] }) {
  return <ul className="mt-3 max-h-64 space-y-1.5 overflow-auto">
    {[...new Set(paths)].map((path) => <li key={path} className="flex items-start gap-2 text-xs text-muted-foreground">
      <FileCode2 aria-hidden="true" className="size-3.5 shrink-0" />
      <code className="min-w-0 [overflow-wrap:anywhere]">{path}</code>
    </li>)}
  </ul>;
}

function codeLink(search: string, group: string) {
  const next = new URLSearchParams(search);
  for (const key of new Set(next.keys())) {
    if (key.startsWith("code_") || key.startsWith("graph_") || key === "finding" || key === "finding_tab" || key === "overview_change") next.delete(key);
  }
  next.set("tab", "code");
  next.set("code_group", group);
  return { search: next.toString() };
}

function SavedOverview({ summary }: { summary: Schema["PullOverviewSummary"] }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const details = useId();
  const changes = useRef<HTMLDetailsElement>(null);
  const { diagram, groups, narrative } = summary;
  const selection = params.get("overview_change");
  function select(value: string) {
    const next = new URLSearchParams(params);
    next.set("overview_change", value);
    setParams(next, { replace: true, state: location.state });
  }
  const defaultNode = diagram?.nodes.find((node) => node.paths.some((path) => groups[0]?.paths.includes(path)));
  const selectedGroup = groups.find((group) => selection === `group:${group.id}`);
  const nodePrefix = `node:${summary.run_id}:`;
  const node = selectedGroup ? undefined : diagram?.nodes.find(({ id }) => selection === nodePrefix + id) ?? defaultNode ?? diagram?.nodes[0];
  const visible = node ? groups.filter((group) => group.paths.some((path) => node.paths.includes(path)))
    : [selectedGroup ?? groups[0]].filter(Boolean);
  const count = new Set(groups.flatMap((group) => group.paths)).size;
  const hasChanges = groups.length > 0 || !!diagram;

  return <>
    <OverviewSection title="Mortis summary">
      <Markdown gfm>{narrative}</Markdown>
    </OverviewSection>
    {diagram && <ChangeDiagram diagram={diagram} selected={node?.id} controls={details} onSelect={(node) => {
      if (changes.current) changes.current.open = true;
      select(nodePrefix + node);
    }} />}
    {hasChanges && <OverviewSection title="Grouped changes" detailsRef={changes}
      meta={groups.length > 0 && <span>{count} {count === 1 ? "file" : "files"} in {groups.length} {groups.length === 1 ? "group" : "groups"}</span>}>
      <div className={cn("grid min-w-0 overflow-hidden rounded-lg border bg-card", groups.length > 0 && "md:grid-cols-[minmax(12rem,1fr)_minmax(0,2fr)]")}>
        {groups.length > 0 && <ul className="divide-y border-b md:border-r md:border-b-0">
          {groups.map((group) => {
            const files = new Set(group.paths).size;
            return <li key={group.id}><button type="button" aria-controls={details} aria-pressed={visible.includes(group)} onClick={() => select(`group:${group.id}`)}
              className="flex min-h-11 w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring aria-pressed:bg-accent aria-pressed:text-accent-foreground sm:px-4">
              <span className="min-w-0 [overflow-wrap:anywhere]">{group.title}</span>
              <span className="shrink-0 text-xs font-normal tabular-nums text-muted-foreground">{files} {files === 1 ? "file" : "files"}</span>
            </button></li>;
          })}
        </ul>}
        <div id={details} className="min-w-0 divide-y">
          {visible.map((group: Group) => <article key={group.id} className="px-3 py-3 sm:px-4">
            <div className="flex items-center justify-between gap-2">
              <h3 className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">{group.title}</h3>
              <Link to={codeLink(location.search, group.id)} state={location.state}
                className={cn(repositoryActivityLink, "shrink-0 gap-0.5 font-[650] sm:min-h-9")}>
                Open in Code<ChevronRight aria-hidden="true" className="size-3.5" />
              </Link>
            </div>
            <p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">{group.description}</p>
            <Files paths={group.paths} />
          </article>)}
          {node && !visible.length && <article className="px-3 py-3 sm:px-4">
            <h3 className="text-sm font-semibold [overflow-wrap:anywhere]">{node.label}</h3>
            <Files paths={node.paths} />
          </article>}
        </div>
      </div>
    </OverviewSection>}
  </>;
}

export function PROverview({ pr }: { pr: Schema["PullRequestOut"] }) {
  const path = `/pulls/${pr.id}/overview`;
  const query = useQuery<Schema["PullOverview"], ApiError>({
    queryKey: [path, pr.base_sha, pr.head_sha, pr.latest_run?.id, pr.latest_run?.summary?.status],
    queryFn: ({ signal }) => api<Schema["PullOverview"]>(path, "GET", undefined, undefined, signal),
    refetchInterval: 30000,
    gcTime: 0,
    retry: false,
  });
  if (query.isPending) return <Loading />;
  if (query.error) return <div className="space-y-2"><ErrorNotice error={query.error} /><Button size="sm" variant="outline" onClick={() => query.refetch()}>Retry Overview</Button></div>;
  const { data } = query;
  if (!data || data.base_sha !== pr.base_sha || data.head_sha !== pr.head_sha) return <Loading />;
  return <div className="space-y-6">
    {data.description?.trim() && <OverviewSection title="PR description" defaultOpen={false}>
      <Markdown gfm>{data.description}</Markdown>
    </OverviewSection>}
    {data.summary ? <SavedOverview key={data.summary.run_id} summary={data.summary} />
      : <p className="text-sm text-muted-foreground">No Mortis summary available for this revision.</p>}
  </div>;
}
