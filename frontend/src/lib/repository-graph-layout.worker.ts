import { positionGraph, type LayoutEdge, type LayoutNode } from "./repository-graph-layout";

self.onmessage = ({ data }: MessageEvent<{ nodes: LayoutNode[]; edges: LayoutEdge[] }>) => {
  positionGraph(data.nodes, data.edges, (frame) => self.postMessage(frame));
};
