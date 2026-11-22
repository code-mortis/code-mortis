import { positionGraph, type LayoutEdge, type LayoutNode } from "./repository-graph-layout";

self.onmessage = ({ data }: MessageEvent<{ nodes: LayoutNode[]; edges: LayoutEdge[] }>) => {
  self.postMessage(positionGraph(data.nodes, data.edges));
};
