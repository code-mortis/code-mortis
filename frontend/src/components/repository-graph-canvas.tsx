import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { Maximize, Minus, Plus } from "lucide-react";
import type { Schema } from "../api";
import type { LayoutFrame, Point } from "../lib/repository-graph-layout";
import { Button } from "./ui/button";

export type GraphView = Schema["RepositoryGraphView"];
export type GraphNode = GraphView["nodes"][number];
export type GraphDirection = "both" | "incoming" | "outgoing";
type GraphEdge = GraphView["edges"][number];
type PositionedNode = GraphNode & Point & { band: 0 | 1 | 2; radius: number };
type Size = { width: number; height: number };
type Camera = Point & { scale: number; zoom: number };

const nameOf = (node: GraphNode) => node.path.split("/").at(-1) || node.path;
const bandRadii = [4.5, 7.5, 11];
const bandDelay = 160;
const bandFadeDuration = 180;

function fit(nodes: PositionedNode[], size: Size): Camera {
  if (!nodes.length) return { x: 0, y: 0, scale: 1, zoom: 1 };
  const left = Math.min(...nodes.map((node) => node.x - node.radius));
  const right = Math.max(...nodes.map((node) => node.x + node.radius));
  const top = Math.min(...nodes.map((node) => node.y - node.radius));
  const bottom = Math.max(...nodes.map((node) => node.y + node.radius));
  const padding = 88;
  const scale = Math.max(0.01, Math.min(2.2, (size.width - padding) / (right - left), (size.height - padding) / (bottom - top)));
  return { x: (left + right) / 2, y: (top + bottom) / 2, scale, zoom: 1 };
}

function project(point: Point, camera: Camera, size: Size) {
  const scale = camera.scale * camera.zoom;
  return { x: (point.x - camera.x) * scale + size.width / 2, y: (point.y - camera.y) * scale + size.height / 2 };
}

export function RepositoryGraphCanvas({
  graph, viewKey, focus, direction, controls, onSelect,
}: {
  graph: GraphView;
  viewKey: string;
  focus: string | null;
  direction: GraphDirection;
  controls: HTMLDivElement | null;
  onSelect: (node: GraphNode) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const cache = useRef(new Map<string, Point>());
  const presentedFocus = useRef<string | null | undefined>(undefined);
  const cameras = useRef(new Map<string, Camera>());
  const drag = useRef<{ id: number; point: Point; moved: boolean; nodeId?: string } | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [layout, setLayout] = useState<(LayoutFrame & { graph: GraphView; failed?: boolean; revealTime?: number }) | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [keyboard, setKeyboard] = useState<string | null>(null);
  const [cameraVersion, setCameraVersion] = useState(0);
  const sizedNodes = useMemo(() => graph.nodes.map((node) => {
    const share = node.kind === "file" && graph.max_connections > 0
      ? (node.incoming + node.outgoing) / graph.max_connections : 0;
    const band: PositionedNode["band"] = share <= 1 / 3 ? 0 : share <= 2 / 3 ? 1 : 2;
    return { ...node, band, radius: bandRadii[band] };
  }), [graph.nodes, graph.max_connections]);
  const { bandStarts, dotRevealDuration } = useMemo(() => {
    const present = new Set<number>(sizedNodes.map((node) => node.band));
    let offset = 0;
    const bandStarts = [0, 1, 2].map((band) => {
      const start = offset;
      if (present.has(band)) offset += bandDelay;
      return start;
    });
    return { bandStarts, dotRevealDuration: Math.max(0, present.size - 1) * bandDelay + bandFadeDuration };
  }, [sizedNodes]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setHover(null);
    const nodes = sizedNodes.map(({ id, radius }) => ({ id, radius, position: cache.current.get(id) }));
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const frameInterval = nodes.length > 1000 || graph.edges.length > 4000 ? 32 : 0;
    let animation = 0;
    const showLayout = (data: LayoutFrame) => {
      const previousFocus = presentedFocus.current;
      presentedFocus.current = focus;
      const shouldReveal = previousFocus === undefined || (focus !== null && previousFocus !== focus);
      if (reducedMotion.matches || !shouldReveal) {
        setLayout({ graph, ...data });
        return;
      }
      const start = performance.now();
      let lastFrame = -Infinity;
      const paintFrame = (time: number) => {
        const revealTime = reducedMotion.matches ? dotRevealDuration : Math.max(0, Math.min(dotRevealDuration, time - start));
        const finished = revealTime === dotRevealDuration;
        if (finished || time - lastFrame >= frameInterval) {
          lastFrame = time;
          setLayout({ graph, ...data, revealTime });
        }
        animation = finished ? 0 : requestAnimationFrame(paintFrame);
      };
      paintFrame(start);
    };
    if (nodes.every((node) => node.position)) {
      showLayout({ positions: Object.fromEntries(nodes.map((node) => [node.id, node.position!])), settled: true });
      return () => cancelAnimationFrame(animation);
    }
    const worker = new Worker(new URL("../lib/repository-graph-layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = ({ data }: MessageEvent<LayoutFrame>) => {
      if (!data.settled || nodes.some(({ id }) => !data.positions[id])) return;
      for (const [id, position] of Object.entries(data.positions)) cache.current.set(id, position);
      worker.terminate();
      showLayout(data);
    };
    worker.onerror = () => {
      cancelAnimationFrame(animation);
      setLayout((previous) => previous?.graph === graph
        ? { ...previous, revealTime: dotRevealDuration, failed: true } : { graph, positions: {}, settled: false, failed: true });
      worker.terminate();
    };
    const ids = new Set(nodes.map((node) => node.id));
    worker.postMessage({ nodes, edges: graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) });
    return () => {
      cancelAnimationFrame(animation);
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    };
  }, [graph, sizedNodes, dotRevealDuration, focus]);

  const currentLayout = layout?.graph === graph ? layout : null;
  const positions = currentLayout?.positions;
  const revealTime = currentLayout?.revealTime ?? dotRevealDuration;
  const bandMotion = useMemo(() => bandStarts.map((start) => {
    const progress = Math.max(0, Math.min(1, (revealTime - start) / bandFadeDuration));
    const remaining = progress - 1;
    const overshoot = 2.3;
    return {
      opacity: progress * (2 - progress),
      radiusScale: 1 + 0.28 * remaining ** 2 * ((overshoot + 1) * remaining + overshoot),
    };
  }), [bandStarts, revealTime]);
  const nodes = useMemo(() => sizedNodes.flatMap((node) => {
    const point = positions?.[node.id];
    return point ? [{ ...node, ...point }] : [];
  }), [sizedNodes, positions]);
  const ready = nodes.length === graph.nodes.length;
  const settled = ready && !!currentLayout?.settled;
  const camera = useMemo(() => {
    const previous = cameras.current.get(viewKey);
    const computed = previous ?? fit(nodes, size);
    if (!previous && settled && nodes.length && size.width && size.height) cameras.current.set(viewKey, computed);
    return computed;
    // State changes caused by pan and zoom update the stored camera.
  }, [cameraVersion, nodes, size, viewKey, settled]);
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  useLayoutEffect(() => {
    const element = canvas.current;
    if (!element || !size.width || !size.height) return;
    const ctx = element.getContext("2d");
    if (!ctx) return;
    const draw = () => {
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.round(size.width * pixelRatio);
      const height = Math.round(size.height * pixelRatio);
      if (element.width !== width) element.width = width;
      if (element.height !== height) element.height = height;
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.clearRect(0, 0, size.width, size.height);
      const style = getComputedStyle(element);
      const primary = style.getPropertyValue("--primary").trim();
      const foreground = style.getPropertyValue("--foreground").trim();
      const background = style.getPropertyValue("--background").trim();
      const muted = style.getPropertyValue("--muted-foreground").trim();
      const border = style.getPropertyValue("--border").trim();
      const caution = style.getPropertyValue("--code-number").trim() || "#946020";
      const success = style.getPropertyValue("--code-added").trim() || "#58703b";
      const bandColors = [muted, caution, success];
      const highlighted = hover ?? keyboard;
      const focused = nodes.find((node) => node.path === focus)?.id;
      const emphasis = highlighted ?? focused;
      const neighbors = new Set<string>();
      const scale = camera.scale * camera.zoom;
      const points = new Map(nodes.map((node) => [node.id, project(node, camera, size)]));
      const raisedEdges: { edge: GraphEdge; outgoing: boolean; incoming: boolean }[] = [];
      const drawEdge = (edge: GraphEdge, outgoing: boolean, incoming: boolean, raised = false) => {
        const from = points.get(edge.source);
        const to = points.get(edge.target);
        const source = byId.get(edge.source);
        const target = byId.get(edge.target);
        if (!from || !to || !source || !target) return;
        const sourceMotion = bandMotion[source.band];
        const targetMotion = bandMotion[target.band];
        const visibility = Math.min(sourceMotion.opacity, targetMotion.opacity);
        if (!visibility) return;
        const angle = Math.atan2(to.y - from.y, to.x - from.x);
        const tangentX = Math.cos(angle);
        const tangentY = Math.sin(angle);
        const startOffset = raised ? source.radius * sourceMotion.radiusScale * scale : 0;
        const endOffset = target.radius * targetMotion.radiusScale * scale + 2;
        if (raised && Math.hypot(to.x - from.x, to.y - from.y) <= startOffset + endOffset) return;
        const endX = to.x - tangentX * endOffset;
        const endY = to.y - tangentY * endOffset;
        ctx.lineWidth = 1;
        ctx.globalAlpha = (raised ? 0.9 : highlighted ? 0.24 : 0.72) * visibility;
        ctx.strokeStyle = raised ? (outgoing ? primary : muted) : border;
        ctx.setLineDash(incoming ? [4, 4] : []);
        ctx.beginPath();
        ctx.moveTo(from.x + tangentX * startOffset, from.y + tangentY * startOffset);
        ctx.lineTo(endX, endY);
        ctx.stroke();
        if (outgoing || incoming) {
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(endX - 6 * Math.cos(angle - Math.PI / 6), endY - 6 * Math.sin(angle - Math.PI / 6));
          ctx.lineTo(endX, endY);
          ctx.lineTo(endX - 6 * Math.cos(angle + Math.PI / 6), endY - 6 * Math.sin(angle + Math.PI / 6));
          ctx.stroke();
        }
      };
      for (const edge of graph.edges) {
        const outgoing = direction !== "incoming" && edge.source === emphasis;
        const incoming = direction !== "outgoing" && edge.target === emphasis;
        if (outgoing) neighbors.add(edge.target);
        if (incoming) neighbors.add(edge.source);
        if (highlighted && (outgoing || incoming)) raisedEdges.push({ edge, outgoing, incoming });
        else drawEdge(edge, outgoing, incoming);
      }
      for (const node of nodes) {
        const { opacity: visibility, radiusScale } = bandMotion[node.band];
        if (!visibility) continue;
        const point = points.get(node.id)!;
        const emphasized = node.id === emphasis;
        ctx.beginPath();
        ctx.arc(point.x, point.y, node.radius * radiusScale * scale, 0, Math.PI * 2);
        ctx.globalAlpha = visibility;
        ctx.fillStyle = background;
        ctx.fill();
        const opacity = highlighted && !emphasized && !neighbors.has(node.id) ? 0.32 : emphasized ? 1 : 0.82;
        ctx.globalAlpha = opacity * visibility;
        ctx.fillStyle = bandColors[node.band];
        ctx.fill();
        if (node.id === keyboard) {
          ctx.strokeStyle = primary;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      }
      for (const { edge, outgoing, incoming } of raisedEdges) drawEdge(edge, outgoing, incoming, true);
      if (camera.zoom > 0.55 && revealTime === dotRevealDuration) {
        const boxes: { x: number; y: number; width: number; height: number }[] = [];
        const order = (node: PositionedNode) => node.id === emphasis ? 0 : node.id === focused ? 1 : neighbors.has(node.id) ? 2 : 3;
        const ordered = [...nodes].sort((a, b) => order(a) - order(b) || (b.incoming + b.outgoing) - (a.incoming + a.outgoing));
        const dense = nodes.length > 300;
        const labelLimit = Math.min(Math.floor(8 + camera.zoom * 21), dense ? 16 : nodes.length);
        let checked = 0;
        ctx.font = "500 12px Inter, sans-serif";
        ctx.textBaseline = "top";
        ctx.lineWidth = 3;
        ctx.strokeStyle = background;
        for (const node of ordered) {
          if (boxes.length >= labelLimit) break;
          const point = points.get(node.id)!;
          if (point.x < 0 || point.x > size.width || point.y < 0 || point.y > size.height) continue;
          if (dense && ++checked > 24) break;
          const priority = node.id === emphasis || node.id === focused;
          const label = nameOf(node);
          const width = ctx.measureText(label).width + 4;
          const x = point.x - width / 2;
          const y = point.y + node.radius * scale + 4;
          if (x < 0 || x + width > size.width || y < 0 || y + 14 > size.height
            || boxes.some((box) => x < box.x + box.width && x + width > box.x && y < box.y + box.height && y + 14 > box.y)
            || !priority && nodes.some((other) => {
              const position = points.get(other.id)!;
              const radius = other.radius * scale;
              return other.id !== node.id && x < position.x + radius && x + width > position.x - radius
                && y < position.y + radius && y + 14 > position.y - radius;
            })) continue;
          boxes.push({ x, y, width, height: 14 });
          ctx.globalAlpha = highlighted && order(node) === 3 ? 0.36 : 1;
          ctx.fillStyle = node.id === emphasis ? primary : foreground;
          ctx.strokeText(label, x + 2, y);
          ctx.fillText(label, x + 2, y);
        }
      }
      ctx.globalAlpha = 1;
    };
    draw();
    const observer = new MutationObserver(draw);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => observer.disconnect();
  }, [bandMotion, byId, camera, direction, dotRevealDuration, focus, graph.edges, hover, keyboard, nodes, revealTime, size]);

  function moveCamera(next: Camera) {
    cameras.current.set(viewKey, next);
    setCameraVersion((value) => value + 1);
  }

  function zoomBy(factor: number, origin?: Point) {
    const latest = cameras.current.get(viewKey) ?? camera;
    const zoom = Math.min(8, Math.max(0.1, latest.zoom * factor));
    if (zoom === latest.zoom) return;
    const dx = (origin?.x ?? size.width / 2) - size.width / 2;
    const dy = (origin?.y ?? size.height / 2) - size.height / 2;
    const change = 1 / (latest.scale * latest.zoom) - 1 / (latest.scale * zoom);
    moveCamera({ ...latest, zoom, x: latest.x + dx * change, y: latest.y + dy * change });
  }

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!ready) return;
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      zoomBy(event.deltaY < 0 ? 1.15 : 1 / 1.15, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  });

  function pointerPoint(event: ReactPointerEvent<HTMLCanvasElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function hit(point: Point) {
    let closest: PositionedNode | undefined;
    let distance = Infinity;
    for (const node of nodes) {
      const { opacity, radiusScale } = bandMotion[node.band];
      if (!opacity) continue;
      const center = project(node, camera, size);
      const delta = Math.hypot(center.x - point.x, center.y - point.y);
      if (delta < distance && delta <= Math.max(7, node.radius * radiusScale * camera.scale * camera.zoom + 3)) {
        closest = node;
        distance = delta;
      }
    }
    return closest;
  }

  const hoverNode = hover ? byId.get(hover) : undefined;
  const tooltip = hoverNode ? project(hoverNode, camera, size) : null;
  const activeNode = keyboard ? byId.get(keyboard) : undefined;
  return <>
    <canvas ref={canvas} data-testid="repository-code-graph-canvas" data-ready={ready} data-settled={settled} tabIndex={0}
      aria-label="File dependency graph. Use arrow keys to select a node and Enter to focus its connections."
      className="absolute inset-0 size-full cursor-grab touch-none focus-visible:outline-2 focus-visible:outline-ring active:cursor-grabbing"
      onPointerDown={(event) => {
        if (!ready || event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const point = pointerPoint(event);
        drag.current = { id: event.pointerId, point, moved: false, nodeId: hit(point)?.id };
      }}
      onPointerMove={(event) => {
        const point = pointerPoint(event);
        const current = drag.current;
        if (current?.id === event.pointerId) {
          const dx = point.x - current.point.x;
          const dy = point.y - current.point.y;
          if (!current.moved && Math.hypot(dx, dy) < 4) return;
          current.moved = true;
          current.point = point;
          setHover(null);
          const latest = cameras.current.get(viewKey) ?? camera;
          const scale = latest.scale * latest.zoom;
          moveCamera({ ...latest, x: latest.x - dx / scale, y: latest.y - dy / scale });
        } else setHover(hit(point)?.id ?? null);
      }}
      onPointerUp={(event) => {
        if (drag.current?.id !== event.pointerId) return;
        const { moved, nodeId } = drag.current;
        const selected = !moved && nodeId ? byId.get(nodeId) : undefined;
        drag.current = null;
        if (selected) {
          setHover(null);
          onSelect(selected);
        }
      }}
      onPointerCancel={() => { drag.current = null; }}
      onPointerLeave={() => setHover(null)}
      onBlur={() => setKeyboard(null)}
      onKeyDown={(event) => {
        if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) {
          const visible = revealTime === dotRevealDuration ? nodes : nodes.filter((node) => bandMotion[node.band].opacity);
          if (!visible.length) return;
          event.preventDefault();
          const index = visible.findIndex((node) => node.id === keyboard);
          const next = index < 0 ? 0 : (index + (event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1) + visible.length) % visible.length;
          setKeyboard(visible[next].id);
        } else if ((event.key === "Enter" || event.key === " ") && activeNode) {
          event.preventDefault();
          setKeyboard(null);
          onSelect(activeNode);
        }
      }} />
    {!ready && (currentLayout?.failed
      ? <p role="alert" className="pointer-events-none absolute inset-x-0 top-4 text-center text-sm text-muted-foreground">Graph layout is unavailable. Search for an indexed file.</p>
      : <p role="status" className="pointer-events-none absolute inset-x-0 top-4 text-center text-sm text-muted-foreground">Arranging files…</p>)}
    {activeNode && <span aria-live="polite" className="sr-only">{activeNode.path}</span>}
    {hoverNode && tooltip && <div role="tooltip" className="pointer-events-none absolute z-10 max-w-52 rounded-md border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-sm"
      style={{ left: Math.max(0, Math.min(tooltip.x + 10, size.width - 210)), top: Math.max(0, tooltip.y - 68) }}>
      <p className="wrap-anywhere font-medium">{nameOf(hoverNode)}</p>
      <p className="text-muted-foreground">{hoverNode.kind === "folder" ? `${hoverNode.file_count} files` : `${hoverNode.incoming} imported by · ${hoverNode.outgoing} imports`}</p>
    </div>}
    {controls && createPortal(<GraphZoomControls zoom={camera.zoom} disabled={!ready || !nodes.length} onOut={() => zoomBy(1 / 1.2)} onIn={() => zoomBy(1.2)} onFit={() => moveCamera(fit(nodes, size))} />, controls)}
  </>;
}

function GraphZoomControls({ zoom, disabled, onOut, onIn, onFit }: { zoom: number; disabled: boolean; onOut: () => void; onIn: () => void; onFit: () => void }) {
  return <div className="flex items-center gap-1 rounded-md bg-muted px-1 text-xs tabular-nums text-muted-foreground">
    <Button size="icon-sm" variant="ghost" aria-label="Zoom out" disabled={disabled || zoom <= 0.1} onClick={onOut}><Minus /></Button>
    <output aria-label="Graph zoom" className="min-w-10 text-center">{Math.round(zoom * 100)}%</output>
    <Button size="icon-sm" variant="ghost" aria-label="Zoom in" disabled={disabled || zoom >= 8} onClick={onIn}><Plus /></Button>
    <Button size="icon-sm" variant="ghost" aria-label="Fit graph" disabled={disabled} onClick={onFit}><Maximize /></Button>
  </div>;
}
