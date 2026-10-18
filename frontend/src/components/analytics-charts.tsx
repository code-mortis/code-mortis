import type { ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Line, LineChart, XAxis, YAxis } from "recharts";
import type { Schema } from "../api";
import { analyticsLabel, analyticsNumber } from "../lib/analytics";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "./ui/chart";

const series = {
  runs: { label: "Review runs", color: "var(--chart-1)" },
  findings: { label: "Findings detected", color: "var(--chart-2)" },
  tokens: { label: "Reported tokens", color: "var(--chart-1)" },
} satisfies ChartConfig;
const axisNumber = (value: number) => new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
const dateLabel = (value: string) => new Date(`${value}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

export function AnalyticsPanel({ title, description, children, className = "" }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return <section aria-label={title} className={`min-w-0 rounded-lg border bg-card p-4 sm:p-5 ${className}`}>
    <h2 className="text-sm font-semibold">{title}</h2>
    {description && <p className="mt-1 max-w-[75ch] text-xs leading-relaxed text-muted-foreground">{description}</p>}
    <div className="mt-5">{children}</div>
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

export function TrendChart({ title, description, data, keys }: {
  title: string; description: string; data: Schema["AnalyticsPoint"][]; keys: (keyof typeof series)[];
}) {
  return <AnalyticsPanel title={title} description={description}>
    <ChartContainer config={series} className="h-64 w-full aspect-auto sm:h-72">
      <LineChart accessibilityLayer data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="date" tickFormatter={dateLabel} tickLine={false} axisLine={false} minTickGap={36} tickMargin={10} />
        <YAxis tickFormatter={axisNumber} tickLine={false} axisLine={false} width={42} allowDecimals={false} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(label) => String(label)} />} />
        <ChartLegend content={<ChartLegendContent />} />
        {keys.map((key) => <Line key={key} dataKey={key} type="linear" stroke={`var(--color-${key})`} strokeWidth={2} dot={data.length === 1} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false} />)}
      </LineChart>
    </ChartContainer>
    <details className="mt-4 border-t pt-3 text-xs">
      <summary className="cursor-pointer rounded-sm font-medium">View {title.toLowerCase()} data</summary>
      <div className="mt-3"><DataTable label={`${title} data`} headings={["Period starting", ...keys.map((key) => series[key].label), ...(keys.includes("tokens") ? ["Missing reports"] : [])]}
        rows={data.map((row) => [row.date, ...keys.map((key) => analyticsNumber(row[key])), ...(keys.includes("tokens") ? [analyticsNumber(row.missing_reports)] : [])])} /></div>
    </details>
  </AnalyticsPanel>;
}

export function BreakdownChart({ title, description, data, onSelect, severity = false }: {
  title: string; description?: string; data: Schema["AnalyticsCount"][]; onSelect?: (key: string) => void; severity?: boolean;
}) {
  const visible = data.slice(0, 10).map((row) => ({ ...row, label: analyticsLabel(row.key) }));
  const colors: Record<string, string> = { critical: "var(--chart-critical)", high: "var(--chart-high)", medium: "var(--chart-medium)", low: "var(--chart-low)" };
  return <AnalyticsPanel title={title} description={description}>
    {data.some((row) => row.count > 0) ? <ChartContainer config={{ count: { label: title, color: "var(--chart-1)" } }} className="w-full aspect-auto" style={{ height: Math.max(170, visible.length * 36) }}>
      <BarChart accessibilityLayer data={visible} layout="vertical" margin={{ left: 0, right: 12 }}>
        <CartesianGrid horizontal={false} />
        <XAxis type="number" tickLine={false} axisLine={false} tickFormatter={axisNumber} allowDecimals={false} />
        <YAxis type="category" dataKey="label" tickLine={false} axisLine={false} width={116} tick={{ width: 106, maxLines: 2, style: { fontSize: 11 } }} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(_label, payload) => payload[0]?.payload.label} />} />
        <Bar dataKey="count" fill="var(--color-count)" radius={3} maxBarSize={18} isAnimationActive={false}
          onClick={onSelect ? (row) => { if (typeof row.key === "string") onSelect(row.key); } : undefined} cursor={onSelect ? "pointer" : undefined}>
          {visible.map((row) => <Cell key={row.key} fill={severity ? colors[row.key] ?? "var(--chart-1)" : "var(--chart-1)"} />)}
        </Bar>
      </BarChart>
    </ChartContainer> : <p className="py-7 text-sm text-muted-foreground">No recorded results in this period.</p>}
    {data.length > 10 && <p className="mt-2 text-xs text-muted-foreground">Showing the first 10. All {data.length} results are available below.</p>}
    {onSelect && <div className="mt-3 flex flex-wrap gap-2" aria-label={`Filter by ${title.toLowerCase()}`}>{visible.map((row) => <button key={row.key} aria-label={`${row.label}: ${analyticsNumber(row.count)} findings`} onClick={() => onSelect(row.key)} className="min-h-11 max-w-full rounded-md border px-2.5 py-1.5 text-xs hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring sm:min-h-8"><span className="break-words">{row.label}</span><span className="ml-2 tabular-nums text-muted-foreground">{analyticsNumber(row.count)}</span></button>)}</div>}
    <details className="mt-4 border-t pt-3 text-xs"><summary className="cursor-pointer rounded-sm font-medium">View {title.toLowerCase()} data</summary>
      <div className="mt-3"><DataTable label={`${title} data`} headings={[title, "Count"]} rows={data.map((row) => [onSelect ? <button onClick={() => onSelect(row.key)} className="rounded-sm text-left underline underline-offset-4">{analyticsLabel(row.key)}</button> : analyticsLabel(row.key), analyticsNumber(row.count)])} /></div>
    </details>
  </AnalyticsPanel>;
}
