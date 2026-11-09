import type { ComponentProps } from "react";
import { Link } from "react-router-dom";
import type { LucideIcon } from "lucide-react";

export function SettingsNavigation({ label, items, value }: {
  label: string;
  items: { value: string; label: string; icon?: LucideIcon; to: string; state?: ComponentProps<typeof Link>["state"] }[];
  value: string;
}) {
  return <nav aria-label={label} className="flex flex-wrap self-start gap-1 rounded-lg bg-muted/40 p-1 lg:flex-col">
    {items.map(({ value: key, label: name, icon: Icon, to, state }) => <Link key={key} to={to} state={state}
      aria-current={value === key ? "page" : undefined}
      className={`flex min-h-10 items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${value === key ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"}`}>
      {Icon && <Icon aria-hidden="true" className="size-4 shrink-0" />}{name}
    </Link>)}
  </nav>;
}
