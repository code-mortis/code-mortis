import { Fragment } from "react";
import { Link, useLocation, useMatch } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import { useApi, type Repository, type Schema } from "../api";
import { getInboxReturn } from "../lib/inbox-filters";
import { getDetailPath, getRepositoryReturn } from "../lib/navigation";
import { SidebarToggle } from "./app-sidebar";
import { Button } from "./ui/button";
import {
  Breadcrumb, BreadcrumbEllipsis, BreadcrumbItem, BreadcrumbLink,
  BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator,
} from "./ui/breadcrumb";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "./ui/dropdown-menu";

type Crumb = { label: string; to: string; state?: { inbox: string | null; repository: string | null } };

export function AppHeader({ admin, dark, onToggleTheme }: {
  admin: boolean;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const route = useLocation();
  const path = route.pathname.replace(/\/$/, "") || "/";
  const params = new URLSearchParams(route.search);
  const inboxReturn = getInboxReturn(route.state);
  const repositoryReturn = getRepositoryReturn(route.state);
  const detailPath = getDetailPath(route.pathname);
  const detail = useApi<Pick<Schema["PRDetail"], "repository" | "pull_request">>(detailPath ?? "", false, !!detailPath);
  const repositoryRoute = useMatch("/repositories/:repositoryId/*");
  const repos = useApi<Repository[]>("/repositories", false, !!repositoryRoute);
  const crumbs: Crumb[] = [{ label: "Inbox", to: inboxReturn ?? "/" }];

  if (path === "/repositories" || repositoryRoute) {
    const repository = repos.error ? undefined : repos.data?.find((repo) => String(repo.id) === repositoryRoute?.params.repositoryId);
    const search = new URLSearchParams(params);
    search.delete("tab");
    const repositoryPath = repositoryRoute ? `/repositories/${repositoryRoute.params.repositoryId}` : path;
    crumbs.push({ label: repository?.full_name || "Repositories", to: repositoryPath + (search.size ? `?${search}` : "") });
    if (repositoryRoute?.params["*"] === "settings") crumbs.push({ label: "Configure", to: path + route.search });
  } else if (detailPath) {
    if (detail.data && !detail.error) {
      const { repository, pull_request: pull } = detail.data;
      const repositoryPath = `/repositories/${repository.id}`;
      crumbs.push({ label: repository.full_name, to: repositoryReturn?.split("?")[0] === repositoryPath ? repositoryReturn : repositoryPath });
      crumbs.push({ label: `Pull request #${pull.number}`, to: `/pulls/${pull.id}`, state: { inbox: inboxReturn, repository: repositoryReturn } });
    } else if (path.startsWith("/pulls/")) {
      crumbs.push({ label: "Pull request", to: path });
    }
    if (path.startsWith("/runs/")) crumbs.push({ label: "Review run", to: path });
  } else if (path === "/settings") {
    const tab = admin ? params.get("tab") : null;
    crumbs.push({ label: "Settings", to: "/settings" });
    const tabLabels = new Map([["github", "GitHub"], ["team", "Team"]]);
    crumbs.push({ label: tabLabels.get(tab ?? "") ?? "AI connections", to: path + route.search });
  } else if (path !== "/") {
    crumbs.push({ label: "Page not found", to: path });
  }

  const current = crumbs.at(-1)!;
  const ancestors = crumbs.slice(0, -1);
  const themeLabel = dark ? "Switch to light mode" : "Switch to dark mode";

  return <header className="sticky top-0 z-10 flex h-14 items-center gap-2 border-b bg-background px-4 sm:gap-3 sm:px-8">
    <SidebarToggle />
    <Breadcrumb aria-label="Breadcrumb" className="min-w-0 flex-1">
      <BreadcrumbList className="flex-nowrap">
        {ancestors.map((crumb, index) => <Fragment key={crumb.to}>
          <BreadcrumbItem className={index === 0 ? "shrink-0" : "hidden min-w-0 sm:inline-flex"}>
            <BreadcrumbLink asChild className="truncate rounded-sm" title={crumb.label}>
              <Link to={crumb.to} state={crumb.state}>{crumb.label}</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className={index === 0 ? "shrink-0" : "hidden shrink-0 sm:block"} />
        </Fragment>)}
        {ancestors.length > 1 && <>
          <BreadcrumbItem className="sm:hidden">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="Show breadcrumb ancestors"><BreadcrumbEllipsis /></Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="max-w-[calc(100vw-2rem)]">
                {ancestors.slice(1).map((crumb) => <DropdownMenuItem asChild key={crumb.to}>
                  <Link to={crumb.to} state={crumb.state} className="break-all">{crumb.label}</Link>
                </DropdownMenuItem>)}
              </DropdownMenuContent>
            </DropdownMenu>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="shrink-0 sm:hidden" />
        </>}
        <BreadcrumbItem className={ancestors.length > 1 ? "min-w-0 sm:shrink-0" : "min-w-0"}>
          <BreadcrumbPage className="truncate font-medium" title={current.label}>{current.label}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
    <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label={themeLabel} title={themeLabel}
      className="relative ml-auto overflow-hidden rounded-full bg-muted text-primary hover:bg-accent motion-reduce:transition-none">
      <Sun aria-hidden="true" className="size-[18px] rotate-0 scale-100 transition-transform duration-200 ease-out dark:-rotate-90 dark:scale-0 motion-reduce:transition-none" />
      <Moon aria-hidden="true" className="absolute size-[18px] rotate-90 scale-0 transition-transform duration-200 ease-out dark:rotate-0 dark:scale-100 motion-reduce:transition-none" />
    </Button>
  </header>;
}
