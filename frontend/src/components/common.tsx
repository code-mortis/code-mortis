import { isValidElement, useId, useState, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircle, ArrowUpRight, ChevronDown, Copy, LoaderCircle, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Badge, badgeRadius } from "./ui/badge";
import { Label } from "./ui/label";
import { Button } from "./ui/button";
import { cn } from "@/lib/utils";
import { Code } from "./code";
import { engineDiagnostic, errorFeedback } from "../lib/error-feedback";

export { Code, CodeComparison } from "./code";

export const active = (status: string) =>
  ["queued", "running"].includes(status);
export const date = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
export const words = (value: string) => value.replaceAll("_", " ");
const listDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
export function ListDate({ value }: { value: string }) {
  return <time dateTime={value} title={date(value)} className="text-xs tabular-nums text-muted-foreground">{listDate.format(new Date(value))}</time>;
}
export function StatusGroup({ children }: { children: ReactNode }) {
  return <span data-slot="status-group" className={cn("inline-flex w-fit overflow-hidden border border-border align-middle [&_[data-slot=badge]]:rounded-none [&_[data-slot=badge]]:border-0 [&_a]:rounded-none [&_a:focus-visible]:-outline-offset-2", badgeRadius)}>{children}</span>;
}
export function FindingCount({ count }: { count: number }) {
  return <Badge variant={count ? "destructive" : "outline"} className={count ? undefined : "bg-muted/50 text-muted-foreground"}>{count} unresolved<span className="sr-only"> findings</span></Badge>;
}
export function Status({ value, label, icon, showSpinner = true }: { value: string; label?: string; icon?: ReactNode; showSpinner?: boolean }) {
  const good = ["completed", "published", "resolved", "connected", "success", "active"].includes(
    value,
  );
  const bad = ["failed", "critical", "high", "failure", "timed_out"].includes(value);
  const caution = ["partial", "medium", "partially_published", "action_required", "blocked", "outdated"].includes(value);
  return (
    <Badge
      variant={value === "running" ? "default" : "outline"}
      className={cn(
        "gap-1.5",
        !label && "capitalize",
        good &&
          "border-transparent bg-emerald-700/8 text-emerald-800 dark:text-emerald-300",
        bad && "border-transparent bg-red-600/8 text-red-700 dark:text-red-300",
        caution &&
          "border-transparent bg-amber-700/8 text-amber-800 dark:text-amber-300",
      )}
    >
      {label || words(value)}
      {showSpinner && ["queued", "running"].includes(value) && (
        <LoaderCircle className="size-3 motion-safe:animate-spin" />
      )}
      {icon}
    </Badge>
  );
}
export function ErrorNotice({ error, title, tone = "error" }: { error: unknown; title?: string; tone?: "error" | "warning" }) {
  if (!error) return null;
  // Titled notices already supply readable copy and their own diagnostics.
  const feedback = title ? {
    title, message: engineDiagnostic(error instanceof Error ? error.message : String(error)), details: "",
  } : errorFeedback(error);
  const heading = title || feedback.title;
  const Icon = tone === "warning" ? TriangleAlert : AlertCircle;
  return (
    <div
      role="alert"
      className={cn("flex gap-3 rounded-md border p-4 text-sm", tone === "warning"
        ? "border-amber-700/25 bg-amber-700/8 text-amber-800 dark:text-amber-300"
        : "border-destructive/25 bg-destructive/5 text-destructive")}
    >
      <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 break-words [overflow-wrap:anywhere]">
        {heading && <p className="mb-1 font-medium">{heading}</p>}
        <p className="leading-relaxed">{feedback.message}</p>
        {feedback.details && <div className="mt-2"><TechnicalDetails error={feedback.details} name={heading || "Error"} /></div>}
      </div>
    </div>
  );
}
export function TechnicalDetails({ error, name, compact = false }: { error: string; name: string; compact?: boolean }) {
  const [copying, setCopying] = useState(false);
  const message = engineDiagnostic(error);
  return <details className="group text-xs text-muted-foreground">
    <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-2 outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-8 [&::-webkit-details-marker]:hidden">Technical details<ChevronDown aria-hidden="true" className="size-3 shrink-0 transition-transform group-open:rotate-180" /></summary>
    <div className={cn("group/diagnostics relative", compact ? "mt-0.5" : "mt-3")}>
      <pre role="region" aria-label={`${name} technical details`} tabIndex={0} className="min-h-14 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 pr-14 font-mono text-xs leading-relaxed outline-offset-2 focus-visible:outline-2 focus-visible:outline-ring sm:min-h-11 sm:pr-12 [overflow-wrap:anywhere]">{message}</pre>
      <Button variant="ghost" size="icon" aria-label={`${copying ? "Copying" : "Copy"} ${name} details`} title={`${copying ? "Copying" : "Copy"} ${name} details`} className="absolute right-1 top-1 size-11 text-muted-foreground opacity-0 transition-opacity group-hover/diagnostics:opacity-100 group-focus-within/diagnostics:opacity-100 sm:size-8 [@media(hover:none)]:opacity-100" disabled={copying} onClick={async () => {
        setCopying(true);
        try {
          await navigator.clipboard.writeText(message);
          toast.success(`${name} details copied`);
        } catch {
          toast.error("Could not copy details", { description: "Select the text in Technical details and copy it manually." });
        } finally { setCopying(false); }
      }}>{copying ? <LoaderCircle aria-hidden="true" className="size-3.5 motion-safe:animate-spin" /> : <Copy aria-hidden="true" className="size-3.5" />}</Button>
    </div>
  </details>;
}

export function Loading() {
  return null;
}
export function Empty({
  title,
  children,
  className,
}: {
  title: string;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("rounded-lg border bg-card px-6 py-12 text-center", className)}>
      <h2 className="text-base font-semibold">{title}</h2>
      <div className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        {children}
      </div>
    </div>
  );
}
export function PageTitle({
  title,
  titleMeta,
  headingProps,
  children,
  actions,
  footer,
  className,
}: {
  title: ReactNode;
  titleMeta?: ReactNode;
  headingProps?: ComponentProps<"h1">;
  children?: ReactNode;
  actions?: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <header data-slot="page-title" className={cn("mb-6 min-w-0", className)}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1 basis-72">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h1 {...headingProps} className={cn("min-w-0 break-words text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]", headingProps?.className)}>{title}</h1>
            {titleMeta}
          </div>
          {children && (
            <div className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {children}
            </div>
          )}
        </div>
        {actions && <div className="flex max-w-full shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {footer}
    </header>
  );
}
export function Field({
  label,
  children,
  hint,
  action,
}: {
  label: string;
  children: (id: string) => ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="space-y-2">
      {action ? (
        <div className="flex flex-wrap items-end justify-between gap-2">
          <Label htmlFor={id}>{label}</Label>
          {action}
        </div>
      ) : <Label htmlFor={id}>{label}</Label>}
      {children(id)}
      {hint && (
        <p className="text-xs leading-relaxed text-muted-foreground">{hint}</p>
      )}
    </div>
  );
}
export function Markdown({ children, gfm = false }: { children: string; gfm?: boolean }) {
  return (
    <div className="markdown text-sm">
      <ReactMarkdown
        skipHtml
        remarkPlugins={gfm ? [remarkGfm] : []}
        components={{
          img: () => null,
          table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="w-max max-w-full [&_td]:border [&_td]:px-3 [&_td]:py-1 [&_th]:border [&_th]:bg-muted [&_th]:px-3 [&_th]:py-1">{children}</table></div>,
          pre: ({ children }) => {
            if (!isValidElement<{ children?: ReactNode; className?: string }>(children)) return <pre>{children}</pre>;
            const language = children.props.className?.match(/\blanguage-([\w+-]+)/)?.[1];
            return <Code language={language}>{String(children.props.children ?? "")}</Code>;
          },
          a: ({ href, children }) => (
            href ? <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a> : <>{children}</>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
export function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <Button asChild variant="outline" size="sm">
      <a href={href} target="_blank" rel="noreferrer">
        {children}
        <ArrowUpRight className="size-3.5" />
      </a>
    </Button>
  );
}
