import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { Maximize, Minus, Plus } from "lucide-react";
import type { Schema } from "../api";
import { nodeRadius, type Point } from "../lib/repository-graph-layout";
import { Button } from "./ui/button";

export type GraphView = Schema["RepositoryGraphView"];
export type GraphNode = GraphView["nodes"][number];
type PositionedNode = GraphNode & Point & { radius: number };
type Size = { width: number; height: number };
type Camera = Point & { scale: number; zoom: number };

const nameOf = (node: GraphNode) => node.path.split("/").at(-1) || node.path;

function fit(nodes: PositionedNode[], size: Size, sourceOpen: boolean): Camera {
  if (!nodes.length) return { x: 0, y: 0, scale: 1, zoom: 1 };
  const left = Math.min(...nodes.map((node) => node.x - node.radius));
  const right = Math.max(...nodes.map((node) => node.x + node.radius));
  const top = Math.min(...nodes.map((node) => node.y - node.radius));
  const bottom = Math.max(...nodes.map((node) => node.y + node.radius));
  const padding = 88;
  const reserved = sourceOpen && size.width > 700 ? Math.min(400, size.width * 0.45) : 0;
  const scale = Math.max(0.01, Math.min(2.2, (size.width - reserved - padding) / (right - left), (size.height - padding) / (bottom - top)));
  return { x: (left + right) / 2 + reserved / 2 / scale, y: (top + bottom) / 2, scale, zoom: 1 };
}

function project(point: Point, camera: Camera, size: Size) {
  const scale = camera.scale * camera.zoom;
  return { x: (point.x - camera.x) * scale + size.width / 2, y: (point.y - camera.y) * scale + size.height / 2 };
}

export function RepositoryGraphCanvas({
  graph, viewKey, focus, sourceOpen, controls, onSelect,
}: {
  graph: GraphView;
  viewKey: string;
  focus: string | null;
  sourceOpen: boolean;
  controls: HTMLDivElement | null;
  onSelect: (node: GraphNode) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const cache = useRef(new Map<string, Point>());
  const cameras = useRef(new Map<string, Camera>());
  const drag = useRef<{ id: number; point: Point; moved: boolean } | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [coordinates, setCoordinates] = useState<Record<string, Point>>({});
  const [hover, setHover] = useState<string | null>(null);
  const [keyboard, setKeyboard] = useState<string | null>(null);
  const [cameraVersion, setCameraVersion] = useState(0);
  const [layoutError, setLayoutError] = useState(false);

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
    const nodes = graph.nodes.map((node) => ({
      id: node.id, incoming: node.incoming, outgoing: node.outgoing, position: cache.current.get(node.id),
    }));
    setLayoutError(false);
    if (nodes.every((node) => node.position)) {
      setCoordinates(Object.fromEntries(nodes.map((node) => [node.id, node.position!])));
      return;
    }
    const worker = new Worker(new URL("../lib/repository-graph-layout.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<Record<string, Point>>) => {
      for (const [id, position] of Object.entries(event.data)) cache.current.set(id, position);
      setCoordinates(event.data);
    };
    worker.onerror = () => setLayoutError(true);
    const ids = new Set(nodes.map((node) => node.id));
    worker.postMessage({ nodes, edges: graph.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) });
    return () => worker.terminate();
  }, [graph.nodes, graph.edges]);

  const nodes = useMemo(() => graph.nodes.flatMap((node) => {
    const point = coordinates[node.id];
    return point ? [{ ...node, ...point, radius: nodeRadius(node) }] : [];
  }), [graph.nodes, coordinates]);
  const ready = nodes.length === graph.nodes.length;
  const camera = useMemo(() => {
    const previous = cameras.current.get(viewKey);
    const computed = previous ?? fit(nodes, size, !!focus && sourceOpen);
    if (!previous && ready && nodes.length && size.width && size.height) cameras.current.set(viewKey, computed);
    return computed;
    // State changes caused by pan and zoom update the stored camera.
  }, [cameraVersion, nodes, size, viewKey, focus, sourceOpen, ready]);
  const byId = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  useEffect(() => {
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
      const caution = style.getPropertyValue("--code-number").trim() || "#946020";
      const success = style.getPropertyValue("--code-added").trim() || "#58703b";
      const highlighted = hover ?? keyboard;
      const focused = nodes.find((node) => node.path === focus)?.id;
      const emphasis = highlighted ?? focused;
      const neighbors = new Set<string>();
      const scale = camera.scale * camera.zoom;
      const points = new Map(nodes.map((node) => [node.id, project(node, camera, size)]));
      for (const edge of graph.edges) {
        const from = points.get(edge.source);
        const to = points.get(edge.target);
        const target = byId.get(edge.target);
        if (!from || !to || !target) continue;
        const outgoing = edge.source === emphasis;
        const incoming = edge.target === emphasis;
        if (outgoing) neighbors.add(edge.target);
        if (incoming) neighbors.add(edge.source);
        ctx.lineWidth = 1;
        ctx.globalAlpha = outgoing || incoming ? 0.9 : highlighted ? 0.1 : 0.32;
        ctx.strokeStyle = outgoing ? primary : muted;
        ctx.setLineDash(incoming ? [4, 4] : []);
        const angle = Math.atan2(to.y - from.y, to.x - from.x);
        const endX = to.x - Math.cos(angle) * (target.radius * scale + 2);
        const endY = to.y - Math.sin(angle) * (target.radius * scale + 2);
        ctx.beginPath();
        ctx.moveTo(from.x, from.y);
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
      }
      for (const node of nodes) {
        const point = points.get(node.id)!;
        const count = node.incoming + node.outgoing;
        const share = node.kind === "file" && graph.max_connections > 0 ? count / graph.max_connections : 0;
        const emphasized = node.id === emphasis;
        ctx.globalAlpha = highlighted && !emphasized && !neighbors.has(node.id) ? 0.32 : emphasized ? 1 : 0.82;
        ctx.fillStyle = share <= 1 / 3 ? muted : share <= 2 / 3 ? caution : success;
        ctx.beginPath();
        ctx.arc(point.x, point.y, node.radius * scale, 0, Math.PI * 2);
        ctx.fill();
        if (node.id === keyboard) {
          ctx.strokeStyle = primary;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
      }
      if (camera.zoom > 0.55) {
        const boxes: { x: number; y: number; width: number; height: number }[] = [];
        const order = (node: PositionedNode) => node.id === emphasis ? 0 : neighbors.has(node.id) ? 1 : 2;
        const ordered = [...nodes].sort((a, b) => order(a) - order(b) || (b.incoming + b.outgoing) - (a.incoming + a.outgoing));
        ctx.font = "500 12px Inter, sans-serif";
        ctx.textBaseline = "top";
        ctx.lineWidth = 3;
        ctx.strokeStyle = background;
        for (const node of ordered) {
          if (boxes.length >= Math.min(nodes.length, Math.floor(8 + camera.zoom * 21))) break;
          const point = points.get(node.id)!;
          const label = nameOf(node);
          const width = ctx.measureText(label).width + 4;
          const x = point.x - width / 2;
          const y = point.y + node.radius * scale + 4;
          if (x < 0 || x + width > size.width || y < 0 || y + 14 > size.height
            || boxes.some((box) => x < box.x + box.width && x + width > box.x && y < box.y + box.height && y + 14 > box.y)
            || nodes.some((other) => {
              const position = points.get(other.id)!;
              const radius = other.radius * scale;
              return other.id !== node.id && x < position.x + radius && x + width > position.x - radius
                && y < position.y + radius && y + 14 > position.y - radius;
            })) continue;
          boxes.push({ x, y, width, height: 14 });
          ctx.globalAlpha = highlighted && order(node) === 2 ? 0.36 : 1;
          ctx.fillStyle = node.id === emphasis ? primary : foreground;
          ctx.strokeText(label, x + 2, y);
          ctx.fillText(label, x + 2, y);
        }
      }
      ctx.globalAlpha = 1;
    };
    const frame = requestAnimationFrame(draw);
    const observer = new MutationObserver(draw);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [byId, camera, focus, graph.edges, graph.max_connections, hover, keyboard, nodes, size]);

  function moveCamera(next: Camera) {
    cameras.current.set(viewKey, next);
    setCameraVersion((value) => value + 1);
  }

  function zoomBy(factor: number, origin?: Point) {
    const zoom = Math.min(8, Math.max(0.1, camera.zoom * factor));
    if (zoom === camera.zoom) return;
    const dx = (origin?.x ?? size.width / 2) - size.width / 2;
    const dy = (origin?.y ?? size.height / 2) - size.height / 2;
    const change = 1 / (camera.scale * camera.zoom) - 1 / (camera.scale * zoom);
    moveCamera({ ...camera, zoom, x: camera.x + dx * change, y: camera.y + dy * change });
  }

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    function wheel(event: WheelEvent) {
      event.preventDefault();
      if (!element) return;
      const rect = element.getBoundingClientRect();
      zoomBy(event.deltaY < 0 ? 1.15 : 1 / 1.15, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    }
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
      const center = project(node, camera, size);
      const delta = Math.hypot(center.x - point.x, center.y - point.y);
      if (delta < distance && delta <= Math.max(7, node.radius * camera.scale * camera.zoom + 3)) {
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
    <canvas ref={canvas} data-testid="repository-code-graph-canvas" data-ready={ready && !layoutError} tabIndex={0}
      aria-label="File dependency graph. Use arrow keys to select a node and Enter to open it."
      className="absolute inset-0 size-full cursor-grab touch-none focus-visible:outline-2 focus-visible:outline-ring active:cursor-grabbing"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { id: event.pointerId, point: pointerPoint(event), moved: false };
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
        const selected = !drag.current.moved && hit(pointerPoint(event));
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
          if (!nodes.length) return;
          event.preventDefault();
          const index = nodes.findIndex((node) => node.id === keyboard);
          const next = index < 0 ? 0 : (index + (event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1) + nodes.length) % nodes.length;
          setKeyboard(nodes[next].id);
        } else if ((event.key === "Enter" || event.key === " ") && activeNode) {
          event.preventDefault();
          setKeyboard(null);
          onSelect(activeNode);
        }
      }} />
    {layoutError && <p role="alert" className="pointer-events-none absolute inset-x-0 top-4 text-center text-sm text-muted-foreground">Graph layout is unavailable. Search for an indexed file.</p>}
    {!ready && !layoutError && <p role="status" className="pointer-events-none absolute inset-x-0 top-4 text-center text-sm text-muted-foreground">Arranging files…</p>}
    {activeNode && <span aria-live="polite" className="sr-only">{activeNode.path}</span>}
    {hoverNode && tooltip && <div role="tooltip" className="pointer-events-none absolute z-10 max-w-52 rounded-md border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-sm"
      style={{ left: Math.max(0, Math.min(tooltip.x + 10, size.width - 210)), top: Math.max(0, tooltip.y - 68) }}>
      <p className="wrap-anywhere font-medium">{nameOf(hoverNode)}</p>
      <p className="text-muted-foreground">{hoverNode.kind === "folder" ? `${hoverNode.file_count} files` : `${hoverNode.incoming} imported by · ${hoverNode.outgoing} imports`}</p>
    </div>}
    {controls && createPortal(<GraphZoomControls zoom={camera.zoom} disabled={!ready || !nodes.length} onOut={() => zoomBy(1 / 1.2)} onIn={() => zoomBy(1.2)} onFit={() => moveCamera(fit(nodes, size, !!focus && sourceOpen))} />, controls)}
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
