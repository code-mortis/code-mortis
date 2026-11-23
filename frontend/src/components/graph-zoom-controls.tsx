import { Maximize, Minus, Plus } from "lucide-react";
import { Button } from "./ui/button";

const minZoom = 0.1;
const maxZoom = 8;

export function clampGraphZoom(zoom: number) {
  return Math.min(maxZoom, Math.max(minZoom, zoom));
}

export function GraphZoomControls({ zoom, disabled, onOut, onIn, onFit }: {
  zoom: number; disabled?: boolean; onOut: () => void; onIn: () => void; onFit: () => void;
}) {
  return <div className="flex items-center gap-1 rounded-md bg-muted px-1 text-xs tabular-nums text-muted-foreground">
    <Button size="icon-sm" variant="ghost" aria-label="Zoom out" disabled={disabled || zoom <= minZoom} onClick={onOut}><Minus /></Button>
    <output aria-label="Graph zoom" className="min-w-10 text-center">{Math.round(zoom * 100)}%</output>
    <Button size="icon-sm" variant="ghost" aria-label="Zoom in" disabled={disabled || zoom >= maxZoom} onClick={onIn}><Plus /></Button>
    <Button size="icon-sm" variant="ghost" aria-label="Fit graph" disabled={disabled} onClick={onFit}><Maximize /></Button>
  </div>;
}
