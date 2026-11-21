import { useState, type ReactNode } from "react";
import { cn } from "cn";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, XAxis, YAxis } from "recharts";
import type { Schema } from "../api";
import { analyticsCompactNumber, analyticsLabel, analyticsNumber } from "../lib/analytics";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "./ui/chart";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const series = {
  runs: { label: "Review runs", color: "var(--chart-1)" },
  findings: { label: "Findings detected", color: "var(--chart-2)" },
  tokens: { label: "Reported tokens", color: "var(--chart-1)" },
} satisfies ChartConfig;
const dateLabel = (value: string) => new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function AnalyticsNumber({ value }: { value: number | null | undefined }) {
  const [open, setOpen] = useState(false);
  const full = analyticsNumber(value);
  if (value == null || Math.abs(value) < 1000) return full;
  return <Tooltip open={open} onOpenChange={setOpen}>
    <TooltipTrigger asChild><button type="button" className="cursor-help rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring" aria-label={full} onClick={(event) => { event.preventDefault(); setOpen(true); }}>{analyticsCompactNumber(value)}</button></TooltipTrigger>
    <TooltipContent sideOffset={6}>{full}</TooltipContent>
  </Tooltip>;
}

export function AnalyticsPanel({ title, description, children, className }: { title: string; description?: ReactNode; children: ReactNode; className?: string }) {
  return <section aria-label={title} className={cn("min-w-0 rounded-lg border bg-card p-4 sm:p-5", className)}>
    <h2 className="text-base leading-snug font-semibold">{title}</h2>
    {description && <p className="mt-1 max-w-[75ch] text-xs leading-relaxed text-muted-foreground">{description}</p>}
    <div className="mt-3.5">{children}</div>
  </section>;
}

export function DataTable({ label, headings, rows }: { label: string; headings: string[]; rows: ReactNode[][] }) {
  return <div className="max-w-full overflow-x-auto" tabIndex={0} role="region" aria-label={label}>
    <table className="w-full text-left text-sm">
      <caption className="sr-only">{label}</caption>
      <thead><tr className="border-b">{headings.map((heading, index) => <th key={heading} scope="col" className={`px-3 pb-3 text-xs font-medium text-muted-foreground first:pl-0 last:pr-0 ${index ? "text-right" : ""}`}>{heading}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index} className="border-b last:border-0">{row.map((cell, column) => <td key={column} className={`px-3 py-3 align-top first:pl-0 last:pr-0 ${column ? "text-right tabular-nums" : "max-w-64 break-words [overflow-wrap:anywhere]"}`}>{cell}</td>)}</tr>)}</tbody>
    </table>
    {!rows.length && <p className="py-5 text-sm text-muted-foreground">No recorded activity in this period.</p>}
  </div>;
}

export function TrendChart({ title, description, data, keys, runLabel = "Review runs", className }: {
  title: string; description: string; data: Schema["AnalyticsPoint"][]; keys: (keyof typeof series)[]; runLabel?: string; className?: string;
}) {
  const config = { ...series, runs: { ...series.runs, label: runLabel } };
  const showMissingReports = keys.includes("tokens");
  return <AnalyticsPanel title={title} description={description} className={className}>
    <ChartContainer config={config} className="h-[clamp(13rem,25cqi,18rem)] w-full aspect-auto">
      <LineChart accessibilityLayer data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="date" tickFormatter={dateLabel} tickLine={false} axisLine={false} minTickGap={36} tickMargin={10} />
        <YAxis tickFormatter={analyticsCompactNumber} tickLine={false} axisLine={false} width={42} allowDecimals={false} />
        <ChartTooltip filterNull={!showMissingReports} content={<ChartTooltipContent labelFormatter={(label, payload) => {
          const missingReports = showMissingReports ? payload[0]?.payload?.missing_reports : undefined;
          return <>{String(label)}{typeof missingReports === "number" && <span className="mt-1 block font-normal text-muted-foreground">Missing reports: {analyticsNumber(missingReports)}</span>}</>;
        }} />} />
        <ChartLegend content={<ChartLegendContent />} />
        {keys.map((key) => <Line key={key} dataKey={key} type="linear" stroke={`var(--color-${key})`} strokeWidth={2} dot={data.length === 1} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />)}
      </LineChart>
    </ChartContainer>
  </AnalyticsPanel>;
}

export function BreakdownChart({ title, description, data, onSelect, severity = false, className }: {
  title: string; description?: ReactNode; data: Schema["AnalyticsCount"][]; onSelect?: (key: string) => void; severity?: boolean; className?: string;
}) {
  const visible = data.slice(0, 10).map((row) => ({ ...row, label: analyticsLabel(row.key) }));
  const colors: Record<string, string> = { critical: "var(--chart-critical)", high: "var(--chart-high)", medium: "var(--chart-medium)", low: "var(--chart-low)" };
  return <AnalyticsPanel title={title} description={description} className={className}>
    {data.some((row) => row.count > 0) ? <ChartContainer config={{ count: { label: title, color: "var(--chart-1)" } }} className="w-full aspect-auto" style={{ height: Math.max(170, visible.length * 36) }}>
      <BarChart accessibilityLayer data={visible} layout="vertical" margin={{ left: 0, right: 12 }}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={analyticsCompactNumber} allowDecimals={false} />
        <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={116} tick={{ width: 106, maxLines: 2, style: { fontSize: 11 } }} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(_label, payload) => payload[0]?.payload.label} />} />
        <Bar dataKey="count" fill="var(--color-count)" radius={3} maxBarSize={18} isAnimationActive={false}
          onClick={onSelect ? (row) => { if (typeof row.key === "string") onSelect(row.key); } : undefined} cursor={onSelect ? "pointer" : undefined}>
          {visible.map((row) => <Cell key={row.key} fill={severity ? colors[row.key] ?? "var(--chart-1)" : "var(--chart-1)"} />)}
        </Bar>
      </BarChart>
    </ChartContainer> : <p className="py-7 text-sm text-muted-foreground">No recorded results in this period.</p>}
    {data.length > visible.length && <p className="mt-2 text-xs text-muted-foreground">Showing {visible.length} of {analyticsNumber(data.length)} results.</p>}
    {onSelect && <div className="mt-3 flex flex-wrap gap-2" aria-label={`Filter by ${title.toLowerCase()}`}>{visible.map((row) => <Tooltip key={row.key}>
      <TooltipTrigger asChild><button aria-label={`${row.label}: ${analyticsNumber(row.count)} findings`} onClick={() => onSelect(row.key)} className="min-h-11 max-w-full rounded-md border px-2.5 py-1.5 text-xs hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring sm:min-h-8"><span className="break-words">{row.label}</span><span className="ml-2 tabular-nums text-muted-foreground">{analyticsCompactNumber(row.count)}</span></button></TooltipTrigger>
      {row.count >= 1000 && <TooltipContent sideOffset={6}>{analyticsNumber(row.count)}</TooltipContent>}
    </Tooltip>)}</div>}
  </AnalyticsPanel>;
}
