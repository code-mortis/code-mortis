import type { Schema } from "../api";

type Diagram = Schema["SummaryDiagram"];
type NodeBox = { x: number; y: number; width: number; height: number };
const diagramPadding = 18;

let renderer: Promise<typeof import("mermaid")["default"]> | undefined;
let renderId = 0;

function loadRenderer() {
  return renderer ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      htmlLabels: false,
      layout: "dagre",
      suppressErrorRendering: true,
      fontFamily: "Inter Variable, sans-serif",
      fontSize: 14,
      flowchart: { curve: "rounded", padding: 12, nodeSpacing: 24, rankSpacing: 34, wrappingWidth: 144, minNodeWidth: 104 },
    });
    return mermaid;
  }).catch((error) => {
    renderer = undefined;
    throw error;
  });
}

export async function layoutPullDiagram(diagram: Diagram) {
  const ids = new Map(diagram.nodes.map((node, index) => [node.id, `n${index}`]));
  const definitions = diagram.nodes.map(({ id, label }) => {
    // The renderer only measures these labels. React renders the original text below.
    const measurement = label.replace(/\s+/g, " ").replace(/[^a-zA-Z0-9 .-]/gu, "M").trim() || "M";
    // Give Mermaid wrap points for long tokens, matching the button's overflow wrapping.
    const wrapping = measurement.replace(/\S{13,}/g, (word) => word.match(/.{1,8}/g)!.join(" "));
    return `${ids.get(id)}["\`${wrapping}\`"]`;
  });
  const connections = new Set(diagram.edges.map(({ source, target }) => {
    const from = ids.get(source), to = ids.get(target);
    if (!from || !to) throw new Error("Invalid diagram edge");
    return `${from} --> ${to}`;
  }));
  const mermaid = await loadRenderer();
  await document.fonts.ready;
  const id = `pr-diagram-layout-${++renderId}`;
  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  host.style.cssText = "position:fixed;inset:0;visibility:hidden;overflow:hidden;pointer-events:none";
  document.body.append(host);
  let svg: string;
  try {
    ({ svg } = await mermaid.render(id, ["flowchart TD", ...definitions, ...connections].join("\n"), host));
  } finally {
    host.remove();
  }

  // Read geometry from a detached document. The live diagram is built by React.
  const element = new DOMParser().parseFromString(svg, "image/svg+xml").documentElement;
  if (element.localName !== "svg") throw new Error("Invalid diagram markup");

  const positions = new Map<string, NodeBox>();
  const xs: number[] = [], ys: number[] = [];
  for (const [nodeId, layoutId] of ids) {
    const group = element.querySelector(`g.node[id^="${id}-flowchart-${layoutId}-"]`);
    const rect = group?.querySelector("rect.label-container");
    const translate = group?.getAttribute("transform")?.match(/^translate\(([^\s,]+)[\s,]+([^\s,]+)\)$/);
    const [x, y, nodeWidth, nodeHeight] = ["x", "y", "width", "height"].map((name) => Number(rect?.getAttribute(name) ?? NaN));
    const centerX = Number(translate?.[1]), centerY = Number(translate?.[2]);
    if (![x, y, centerX, centerY, nodeWidth, nodeHeight].every(Number.isFinite) || nodeWidth <= 0 || nodeHeight <= 0) {
      throw new Error("Invalid diagram node bounds");
    }
    const box = { x: centerX + x, y: centerY + y, width: nodeWidth, height: nodeHeight };
    positions.set(nodeId, box);
    xs.push(box.x, box.x + box.width);
    ys.push(box.y, box.y + box.height);
  }

  const edges = Array.from(element.querySelectorAll('path[data-edge="true"][d]'), (path) => path.getAttribute("d")!);
  if (!positions.size || edges.length !== connections.size) throw new Error("Incomplete diagram geometry");
  // Rounded routes use absolute coordinate pairs. Include their control points so loops fit too.
  // The SVG's own viewBox can include Mermaid decorations and inherit page styles.
  for (const path of edges) {
    const coordinates = path.match(/[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?/g)?.map(Number) ?? [];
    if (!/^M[MLCQ\d\s.,+eE-]+$/.test(path) || !coordinates.length || coordinates.length % 2 || !coordinates.every(Number.isFinite)) {
      throw new Error("Invalid diagram path");
    }
    for (let i = 0; i < coordinates.length; i += 2) {
      xs.push(coordinates[i]);
      ys.push(coordinates[i + 1]);
    }
  }
  const left = Math.min(...xs) - diagramPadding;
  const top = Math.min(...ys) - diagramPadding;
  const width = Math.ceil(Math.max(...xs) - left + diagramPadding);
  const height = Math.ceil(Math.max(...ys) - top + diagramPadding);
  for (const box of positions.values()) {
    box.x -= left;
    box.y -= top;
  }
  return { viewBox: `${left} ${top} ${width} ${height}`, width, height, positions, edges };
}
