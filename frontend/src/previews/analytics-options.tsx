import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ArrowLeft, ChartNoAxesCombined, ChevronDown, ChevronRight, GitPullRequest, House, Moon, Settings, ShieldCheck, Sun } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, useSearchParams } from "react-router-dom";
import Analytics from "../analytics";
import logo from "../assets/logo.svg";
import { Button } from "../components/ui/button";
import { TooltipProvider } from "../components/ui/tooltip";
import { analyticsTabs } from "../lib/analytics";
import { applyAppearance, readAppearance } from "../lib/appearance";
import "../index.css";
import "./analytics-options.css";

const layouts = [
  { id: "signal", name: "Signal first", description: "A lead visualization with its most useful breakdown beside it." },
  { id: "grid", name: "Comparison grid", description: "Compact metrics and aligned panels make the numbers easier to compare." },
  { id: "report", name: "Guided report", description: "Read each section in order or jump straight to the one you need." },
] as const;

const repository = { id: 1407742623, full_name: "code-mortis/code-mortis" };
const repositoryPath = `/repositories/${repository.id}`;
const appearance = readAppearance();
const initialDark = appearance.mode === "dark" || (appearance.mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);

function ReportIndex({ content }: { content: RefObject<HTMLDivElement | null> }) {
  const [headings, setHeadings] = useState<HTMLHeadingElement[]>([]);
  useEffect(() => {
    const root = content.current;
    if (!root) return;
    const readHeadings = () => {
      const next = [...root.querySelectorAll<HTMLHeadingElement>("#analytics-panel section > h2")];
      setHeadings((previous) => previous.length === next.length && previous.every((item, index) => item === next[index]) ? previous : next);
    };
    readHeadings();
    const observer = new MutationObserver(readHeadings);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [content]);

  if (!headings.length) return null;
  const links = <ul className="grid gap-1">
    {headings.map((heading) => <li key={heading.textContent}>
      <button type="button" className="min-h-11 w-full rounded-md px-2 text-left text-xs hover:bg-accent hover:text-accent-foreground focus-visible:outline-2 focus-visible:outline-ring lg:min-h-8"
        onClick={(event) => {
          const disclosure = event.currentTarget.closest("details");
          if (disclosure) disclosure.open = false;
          heading.tabIndex = -1;
          heading.focus({ preventScroll: true });
          heading.scrollIntoView({ block: "start" });
        }}>{heading.textContent}</button>
    </li>)}
  </ul>;

  return <nav aria-label="Report sections" className="analytics-report-index self-start rounded-lg border bg-card">
    <div className="hidden p-4 lg:block">
      <p className="mb-1.5 px-2 text-xs font-semibold text-muted-foreground">On this tab</p>
      {links}
    </div>
    <details className="group px-3 lg:hidden">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-sm text-xs font-medium [&::-webkit-details-marker]:hidden">
        Jump to a section<ChevronDown aria-hidden="true" className="size-4 text-muted-foreground group-open:rotate-180" />
      </summary>
      <div className="border-t py-1">{links}</div>
    </details>
  </nav>;
}

function Preview() {
  const [params] = useSearchParams();
  const [dark, setDark] = useState(initialDark);
  const content = useRef<HTMLDivElement>(null);
  const layout = layouts.find((item) => item.id === params.get("option")) ?? layouts[0];
  const tab = analyticsTabs.find((item) => item === params.get("tab")) ?? "overview";
  const ThemeIcon = dark ? Sun : Moon;

  return <>
    <header className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 border-b bg-card px-4 py-2 sm:px-7">
      <a href={`${repositoryPath}/overview`} className="flex shrink-0 items-center gap-2.5 text-sm font-semibold">
        <img src={logo} alt="" width="29" height="24" />Code Mortis
      </a>
      <span className="hidden truncate border-l pl-4 text-xs text-muted-foreground sm:block">{repository.full_name}</span>
      <div className="ml-auto flex items-center gap-1">
        <Button variant="ghost" size="sm" asChild><a href={`${repositoryPath}/analytics`}><ArrowLeft aria-hidden="true" /><span className="hidden sm:inline">Back to Analytics</span><span className="sm:hidden">Analytics</span></a></Button>
        <Button variant="ghost" size="icon" aria-label={`Switch to ${dark ? "light" : "dark"} mode`}
          onClick={() => { applyAppearance(appearance.theme, !dark); setDark(!dark); }}><ThemeIcon aria-hidden="true" /></Button>
      </div>
    </header>
    <div className="mx-auto grid max-w-[100rem] min-w-0 lg:min-h-[calc(100dvh-3.5rem)] lg:grid-cols-[12rem_minmax(0,1fr)]">
      <nav aria-label="Repository sections" className="hidden border-r px-2 py-4 text-sm lg:block">
        <a href={`${repositoryPath}/overview`} className="analytics-sidebar-link"><House aria-hidden="true" />Overview</a>
        <a href={`${repositoryPath}/analytics`} aria-current="page" className="analytics-sidebar-link bg-accent text-accent-foreground"><ChartNoAxesCombined aria-hidden="true" />Analytics</a>
        <a href={repositoryPath} className="analytics-sidebar-link"><GitPullRequest aria-hidden="true" />Pull requests</a>
        <a href={`/security${repositoryPath}`} className="analytics-sidebar-link"><ShieldCheck aria-hidden="true" />Security</a>
        <a href={`${repositoryPath}/settings`} className="analytics-sidebar-link"><Settings aria-hidden="true" />Repository settings</a>
      </nav>
      <main className="mx-auto w-full min-w-0 max-w-[82rem] px-4 pt-4 pb-10 sm:px-7 lg:px-10">
        <nav aria-label="Analytics layout previews" className="mb-5 rounded-lg border bg-card px-3 py-2.5 sm:px-4">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-xs font-semibold text-muted-foreground">Layout preview</span>
            <div className="flex flex-wrap gap-1">
              {layouts.map((item) => {
                const next = new URLSearchParams(params);
                next.set("option", item.id);
                return <Link key={item.id} to={`?${next}`} aria-current={item.id === layout.id ? "page" : undefined}
                  className="inline-flex min-h-9 items-center rounded-md px-2.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring aria-[current=page]:bg-accent aria-[current=page]:text-accent-foreground sm:min-h-8 sm:text-sm">{item.name}</Link>;
              })}
            </div>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{layout.description}</p>
        </nav>
        <nav aria-label="Breadcrumb" className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          <a href={`${repositoryPath}/overview`} className="hover:text-foreground hover:underline">code-mortis</a>
          <ChevronRight aria-hidden="true" className="size-3" />
          <span aria-current="page">Analytics</span>
        </nav>
        <div className="analytics-preview" data-layout={layout.id} data-view={tab}>
          {layout.id === "report" && <ReportIndex content={content} />}
          <div ref={content} className="analytics-preview-content min-w-0"><Analytics repository={repository} /></div>
        </div>
      </main>
    </div>
  </>;
}

applyAppearance(appearance.theme, initialDark);
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient()}>
    <TooltipProvider><BrowserRouter><Preview /></BrowserRouter></TooltipProvider>
  </QueryClientProvider>,
);
