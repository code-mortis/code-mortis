import { isValidElement, useId, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { AlertCircle, ArrowUpRight, LoaderCircle } from "lucide-react";
import { Badge } from "./ui/badge";
import { Label } from "./ui/label";
import { Button } from "./ui/button";
import { cn } from "@/lib/utils";
import { Code } from "./code";

export { Code, CodeComparison } from "./code";

export const active = (status: string) =>
  ["queued", "running"].includes(status);
export const date = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
export const words = (value: string) => value.replaceAll("_", " ");
export function Status({ value, label, icon }: { value: string; label?: string; icon?: ReactNode }) {
  const good = ["completed", "published", "resolved", "connected", "success", "active"].includes(
    value,
  );
  const bad = ["failed", "critical", "high", "failure", "timed_out"].includes(value);
  const caution = ["partial", "medium", "partially_published", "action_required", "blocked", "outdated"].includes(value);
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1.5 font-medium whitespace-nowrap",
        !label && "capitalize",
        good &&
          "border-emerald-700/20 bg-emerald-700/8 text-emerald-800 dark:text-emerald-300",
        bad && "border-red-600/20 bg-red-600/8 text-red-700 dark:text-red-300",
        // Foreground blend keeps primary text readable over its tint across themes.
        value === "running" && "border-primary/20 bg-primary/8 text-[color:color-mix(in_srgb,var(--primary)_75%,var(--foreground))]",
        caution &&
          "border-amber-700/20 bg-amber-700/8 text-amber-800 dark:text-amber-300",
      )}
    >
      {label || words(value)}
      {["queued", "running", "in_progress"].includes(value) && (
        <LoaderCircle className="size-3 motion-safe:animate-spin" />
      )}
      {icon}
    </Badge>
  );
}
export function ErrorNotice({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : "";
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-md border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 break-words">
        {title && <span className="mb-1 block font-medium">{title}</span>}
        {message.trim() || "This action could not be completed. Refresh the page and try again."}
      </span>
    </div>
  );
}
export function Loading() {
  return (
    <div
      role="status"
      className="flex items-center gap-2 py-14 text-sm text-muted-foreground"
    >
      <LoaderCircle className="size-4 motion-safe:animate-spin" /> Loading
      workspace…
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed px-6 py-14 text-center">
      <h2 className="text-lg font-medium">{title}</h2>
      <div className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
        {children}
      </div>
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-8">
      {eyebrow && (
        <div className="mb-3 text-xs font-medium text-muted-foreground">
          {eyebrow}
        </div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl text-balance">
            {title}
          </h1>
          {children && (
            <div className="mt-3 text-sm leading-relaxed text-muted-foreground">
              {children}
            </div>
          )}
        </div>
        <div className="flex flex-wrap gap-2">{actions}</div>
      </div>
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
  hint?: string;
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
export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown text-sm">
      <ReactMarkdown
        skipHtml
        components={{
          img: () => null,
          pre: ({ children }) => {
            if (!isValidElement<{ children?: ReactNode; className?: string }>(children)) return <pre>{children}</pre>;
            const language = children.props.className?.match(/\blanguage-([\w+-]+)/)?.[1];
            return <Code language={language}>{String(children.props.children ?? "")}</Code>;
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
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
