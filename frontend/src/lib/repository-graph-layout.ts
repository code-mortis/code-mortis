import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

export type Point = { x: number; y: number };
export type LayoutNode = { id: string; incoming: number; outgoing: number; position?: Point };
export type LayoutEdge = { source: string; target: string };

export function nodeRadius(node: Pick<LayoutNode, "incoming" | "outgoing">): number {
  return Math.min(11, 4.5 + (node.incoming + node.outgoing) * 0.65);
}

export function positionGraph(nodes: LayoutNode[], edges: LayoutEdge[]): Record<string, Point> {
  const particles = nodes.map((node) => ({
    id: node.id,
    radius: nodeRadius(node),
    fx: node.position?.x,
    fy: node.position?.y,
    x: node.position?.x ?? NaN,
    y: node.position?.y ?? NaN,
  }));
  const boundary = Math.max(46, Math.sqrt(nodes.length) * 16);
  forceSimulation(particles)
    .stop()
    .force("links", forceLink<(typeof particles)[number], LayoutEdge>(edges.map((edge) => ({ ...edge }))).id((node) => node.id).distance(43))
    .force("charge", forceManyBody().strength(-34))
    .force("collision", forceCollide<(typeof particles)[number]>().radius((node) => node.radius + 5).iterations(2))
    .force("x", forceX(0).strength(0.065))
    .force("y", forceY(0).strength(0.065))
    .force("round", (alpha) => {
      for (const node of particles) {
        if (node.fx !== undefined || node.fy !== undefined) continue;
        const distance = Math.hypot(node.x, node.y);
        const excess = distance + node.radius - boundary;
        if (excess <= 0 || !distance) continue;
        const spring = alpha * 0.75 * excess / distance;
        if ("vx" in node && typeof node.vx === "number") node.vx -= node.x * spring;
        if ("vy" in node && typeof node.vy === "number") node.vy -= node.y * spring;
      }
    })
    .tick(160);
  return Object.fromEntries(particles.map(({ id, x, y }) => [id, { x, y }]));
}
