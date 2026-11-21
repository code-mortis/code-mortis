import { ArrowUpRight, ChevronRight, GitPullRequest, History, LockKeyhole, Moon, Play, RefreshCw, ShieldCheck, Sun, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, useSearchParams } from "react-router-dom";
import { Toaster, toast } from "sonner";
import logo from "../assets/logo.svg";
import { Status } from "../components/common";
import { repositoryHeaderLink } from "../components/repository-header";
import { Button } from "../components/ui/button";
import {
  Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "../components/ui/dialog";
import { applyAppearance } from "../lib/appearance";
import "../index.css";

const legacyOptions = [
  { id: "three", name: "Three cards", description: "Give each fact its own Overview-style card, with room for labels and links." },
  { id: "activity", name: "Status and activity", description: "Put review policy or saved grade in the lead card. Keep the last run and freshness together." },
  { id: "compact", name: "Compact summary", description: "Keep the familiar three-column summary inside one low-profile card." },
] as const;

const compactOptions = [
  { id: "aligned", name: "Aligned details", description: "Equal-width cards with a consistent title, main value, and a compact detail area." },
  { id: "value-first", name: "Value first", description: "Larger key values, muted labels, and actions alongside the card titles." },
  { id: "lead", name: "Lead card", description: "A wider, softly tinted card for the review policy or saved grade, with two quieter cards beside it." },
] as const;

type CompactOption = (typeof compactOptions)[number]["id"];
type Option = CompactOption | (typeof legacyOptions)[number]["id"];
type Action = { title: string; description: string };
type Fact = {
  label: string;
  icon: LucideIcon;
  value?: ReactNode;
  badge?: ReactNode;
  badgeInHeading?: boolean;
  details?: [string, ReactNode][];
  action?: Action & { label: string };
};

const checkedAt = <time dateTime="2026-11-21T11:58:00">Nov 21, 2026, 11:58 AM</time>;
const scanAt = <time dateTime="2026-11-21T10:42:00">Nov 21, 2026, 10:42 AM</time>;
const reportAt = <time dateTime="2026-10-19T13:06:00">Oct 19, 2026, 1:06 PM</time>;
const commit = (id: string) => <code className="text-xs">{id}</code>;
const currentCommit = commit("a02847e");

const reviewFacts: [Fact, Fact, Fact] = [
  {
    label: "Review policy", icon: GitPullRequest, value: "Automatic reviews",
    badge: <Status value="connected" label="Reviews enabled" />,
    details: [["File limit", "No limit"], ["Comment language", "English"]],
  },
  {
    label: "Last completed review", icon: History, value: "PR #54", details: [["Reviewed commit", commit("b0a185b")]],
    action: { label: "View review", title: "Completed review · PR #54", description: "The saved review is for commit b0a185b." },
  },
  { label: "Last successful GitHub PR check", icon: RefreshCw, value: checkedAt },
];

const securityFacts: [Fact, Fact, Fact] = [
  {
    label: "Saved grade", icon: ShieldCheck, value: "Coverage incomplete",
    badge: <Status value="failed" label="Security D" />,
    details: [["Report saved", reportAt], ["Graded commit", commit("b1c2c09")]],
  },
  {
    label: "Latest scan", icon: History, value: scanAt, badge: <Status value="cancelled" />,
    action: { label: "Inspect scan", title: "Latest security scan", description: "Cancelled Nov 21, 2026 at 10:42 AM. The saved grade applies to commit b1c2c09." },
  },
  {
    label: "Freshness", icon: RefreshCw,
    badge: <Status value="outdated" label="Outdated scan" />,
    details: [["Freshness checked", checkedAt], ["Current commit", currentCommit]],
  },
];

const valueFirstSecurityFacts: Fact[] = [
  securityFacts[0],
  securityFacts[1],
  {
    ...securityFacts[2],
    label: "Freshness check",
    value: checkedAt,
    badgeInHeading: true,
    details: [["Current commit", currentCommit]],
  },
];

const card = "min-w-0 rounded-xl border bg-card text-card-foreground";

function PreviewDialog({ action, trigger }: { action: Action; trigger: ReactNode }) {
  return (
    <Dialog>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{action.title}</DialogTitle>
          <DialogDescription>{action.description}</DialogDescription>
        </DialogHeader>
        <DialogFooter><DialogClose asChild><Button variant="outline">Close</Button></DialogClose></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FactLink({ action }: { action: NonNullable<Fact["action"]> }) {
  return <PreviewDialog action={action} trigger={
    <button className={`${repositoryHeaderLink} w-fit text-xs font-semibold text-primary`}>
      {action.label}<ChevronRight aria-hidden="true" className="size-3.5" />
    </button>
  } />;
}

function CardTitle({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return <h3 className="flex min-w-0 items-center gap-2 text-[15px] font-semibold leading-5">
    <Icon aria-hidden="true" strokeWidth={1.65} className="size-[18px] shrink-0 text-primary" />{children}
  </h3>;
}

function FactDetails({ details, aligned = false, compact = false }: { details: NonNullable<Fact["details"]>; aligned?: boolean; compact?: boolean }) {
  return <dl className={`text-xs tabular-nums ${compact ? "space-y-0.5 leading-4" : "space-y-1 leading-[18px]"}`}>
    {details.map(([label, value]) => <div key={label}
      className={aligned ? "grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-2" : "flex flex-wrap items-baseline gap-x-2"}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={aligned ? "text-right wrap-anywhere" : undefined}>{value}</dd>
    </div>)}
  </dl>;
}

function FactCard({ fact, lead = false }: { fact: Fact; lead?: boolean }) {
  return (
    <section className={`${card} flex flex-col gap-3 px-[17px] pt-3.5 pb-3.5`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle icon={fact.icon}>{fact.label}</CardTitle>{fact.badge}
      </div>
      {fact.value && <p className="text-sm font-medium tabular-nums">{fact.value}</p>}
      {fact.details && (lead
        ? <div className="mt-auto border-t pt-2.5"><FactDetails details={fact.details} aligned /></div>
        : <FactDetails details={fact.details} aligned />)}
      {fact.action && <div className="mt-auto border-t pt-1"><FactLink action={fact.action} /></div>}
    </section>
  );
}

function CompactCards({ option, facts }: { option: CompactOption; facts: readonly Fact[] }) {
  const valueFirst = option === "value-first";
  const aligned = option === "aligned";
  return <div className="@container">
    <div data-layout={option} className={`grid gap-3 ${option === "lead"
      ? "@min-[47rem]:grid-cols-[minmax(0,1.22fr)_repeat(2,minmax(0,1fr))]"
      : "@min-[47rem]:grid-cols-3"}`}>
      {facts.map((fact, index) => {
        const lead = option === "lead" && index === 0;
        const inlineBadge = valueFirst && !fact.badgeInHeading;
        const action = fact.action && <FactLink action={fact.action} />;
        const details = valueFirst ? fact.details?.filter(([label]) => label !== "Comment language") : fact.details;
        const Icon = fact.icon;
        return <section key={fact.label} data-slot="demo-summary-card" aria-label={fact.label}
          className={`flex min-w-0 flex-col gap-1 rounded-lg border px-3.5 py-2.5 text-card-foreground ${lead
            ? "bg-[color-mix(in_srgb,var(--card)_94%,var(--primary))]"
            : "bg-card"}`}>
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-2.5 gap-y-1">
            <h3 className={`flex min-w-0 items-center gap-1.5 leading-5 ${valueFirst ? "text-xs font-medium text-muted-foreground" : "text-sm font-semibold"}`}>
              {(aligned || lead) && <Icon aria-hidden="true" strokeWidth={1.65} className={`size-4 shrink-0 ${lead ? "text-primary" : "text-muted-foreground"}`} />}
              {fact.label}
            </h3>
            {valueFirst && action}
            {!inlineBadge && fact.badge}
          </div>
          {(fact.value || (inlineBadge && fact.badge) || (!valueFirst && action)) && <div className="flex flex-wrap items-center justify-between gap-x-2.5 gap-y-1">
            <div className={`flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 leading-5 font-semibold tabular-nums ${valueFirst || lead ? "text-base" : "text-sm"}`}>
              {fact.value && <span className="min-w-0 wrap-anywhere">{fact.value}</span>}
              {inlineBadge && fact.badge}
            </div>
            {!valueFirst && action}
          </div>}
          {details && details.length > 0 && <div className={aligned ? "border-t pt-1" : undefined}>
            <FactDetails details={details} aligned={aligned || lead} compact />
          </div>}
        </section>;
      })}
    </div>
  </div>;
}

function SummaryCards({ option, security }: { option: Option; security: boolean }) {
  if (security && option === "value-first") return <CompactCards option={option} facts={valueFirstSecurityFacts} />;
  const facts = security ? securityFacts : reviewFacts;
  if (option === "aligned" || option === "value-first" || option === "lead") return <CompactCards option={option} facts={facts} />;
  const [lead, ...activity] = facts;
  if (option === "three") return <div className="grid gap-3.5 lg:grid-cols-3">
    {facts.map((fact) => <FactCard key={fact.label} fact={fact} />)}
  </div>;
  if (option === "activity") return <div className="grid gap-3.5 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
    <FactCard fact={lead} lead />
    <section className={`${card} px-[17px] pt-3.5`}>
      <CardTitle icon={History}>{security ? "Scan activity" : "Review activity"}</CardTitle>
      <div className="mt-2 divide-y">
        {activity.map((fact) => <section key={fact.label} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2.5">
          <div className="min-w-0 space-y-1">
            <h4 className="text-xs text-muted-foreground">{fact.label}</h4>
            {(fact.badge || fact.value) && <div className="flex flex-wrap items-center gap-2 text-[13px] font-medium tabular-nums">{fact.badge}{fact.value}</div>}
            {fact.details && <FactDetails details={fact.details} />}
          </div>
          {fact.action && <FactLink action={fact.action} />}
        </section>)}
      </div>
    </section>
  </div>;
  return <section className={`${card} px-[17px] pt-3.5 pb-3`}>
    <CardTitle icon={security ? ShieldCheck : GitPullRequest}>{security ? "Security summary" : "Review summary"}</CardTitle>
    <div className="mt-2.5 grid divide-y border-t md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.05fr)] md:divide-x md:divide-y-0">
      {facts.map((fact) => {
        const dateBelow = fact.badge && fact.action;
        return <section key={fact.label} className="space-y-1 py-2 md:px-3.5 md:pb-0 md:first:pl-0 md:last:pr-0">
          <h4 className="text-xs text-muted-foreground">{fact.label}</h4>
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] font-medium tabular-nums">
            {fact.badge}{!dateBelow && fact.value}{fact.action && <FactLink action={fact.action} />}
          </div>
          {dateBelow && <p className="text-xs text-muted-foreground tabular-nums">{fact.value}</p>}
          {fact.details && <FactDetails details={fact.details} />}
        </section>;
      })}
    </div>
  </section>;
}

function HeaderActions({ security }: { security: boolean }) {
  const size = "min-h-11 sm:min-h-8";
  if (!security) return <Button size="sm" variant="outline" className={size} onClick={() => toast.success("Sync queued in preview")}>
    <RefreshCw aria-hidden="true" />Sync
  </Button>;
  return <>
    <PreviewDialog action={{ title: "Create GitHub issues", description: "In the workspace, select findings and review draft issues before publishing." }}
      trigger={<Button size="sm" variant="outline" className={size}>Create GitHub issues</Button>} />
    <PreviewDialog action={{ title: "Run security scan", description: "A scan would analyze the current default-branch commit, a02847e." }}
      trigger={<Button size="sm" className={size}><Play aria-hidden="true" />Run scan</Button>} />
  </>;
}

function RepositoryExample({ option, security = false }: { option: Option; security?: boolean }) {
  const id = security ? "security" : "pull-requests";
  return <article aria-labelledby={id}>
    <header className="mb-3.5 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h2 id={id} className="text-2xl font-semibold tracking-tight">{security ? "Security" : "Pull requests"}</h2>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5"><LockKeyhole aria-hidden="true" className="size-3.5" />Private repository</span>
          <PreviewDialog action={{ title: "View on GitHub", description: "In the workspace, this opens the connected repository on GitHub." }}
            trigger={<button className={repositoryHeaderLink}>View on GitHub<ArrowUpRight aria-hidden="true" className="size-3.5" /></button>} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2"><HeaderActions security={security} /></div>
    </header>
    <SummaryCards option={option} security={security} />
  </article>;
}

function Preview() {
  const [params] = useSearchParams();
  const [dark, setDark] = useState(true);
  const requested = params.get("option");
  const legacy = legacyOptions.find((item) => item.id === requested);
  const option = legacy ?? compactOptions.find((item) => item.id === requested) ?? compactOptions[0];
  const options = legacy ? legacyOptions : compactOptions;
  const ThemeIcon = dark ? Sun : Moon;
  return <>
    <div className="border-b">
      <div className="mx-auto flex min-h-14 max-w-6xl items-center justify-between gap-3 px-4 sm:px-8">
        <div className="flex items-center gap-2 text-sm font-semibold"><img src={logo} alt="" className="h-6 w-auto" />Code Mortis</div>
        <Button variant="ghost" size="icon" className="rounded-full bg-muted text-primary" aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() => { applyAppearance("claude-code", !dark); setDark(!dark); }}>
          <ThemeIcon aria-hidden="true" />
        </Button>
      </div>
    </div>
    <main className="mx-auto max-w-6xl px-4 pt-6 pb-10 sm:px-8 sm:pt-8">
      <h1 className="text-xl font-semibold tracking-tight">Repository summary cards</h1>
      <nav aria-label="Card options" className="mt-3 flex flex-wrap gap-x-3 gap-y-1 border-b sm:gap-x-6">
        {options.map((item) => <Link key={item.id} to={`?option=${item.id}`} aria-current={item.id === option.id ? "page" : undefined}
          className="inline-flex min-h-11 items-center border-b-2 border-transparent text-xs font-medium text-muted-foreground hover:text-foreground aria-[current=page]:border-foreground aria-[current=page]:text-foreground sm:text-sm">
          {item.name}
        </Link>)}
      </nav>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pt-3 pb-5 text-sm text-muted-foreground">
        <p>{option.description}</p><span className="text-xs">Sample data · simulated actions</span>
      </div>
      <div className="grid gap-7">
        <RepositoryExample option={option.id} />
        <RepositoryExample option={option.id} security />
      </div>
    </main>
    <Toaster theme={dark ? "dark" : "light"} position="bottom-right" />
  </>;
}

applyAppearance("claude-code", true);
createRoot(document.getElementById("root")!).render(<BrowserRouter><Preview /></BrowserRouter>);
