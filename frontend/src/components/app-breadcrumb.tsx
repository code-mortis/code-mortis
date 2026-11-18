import { Fragment } from "react";
import { Link, useLocation, useMatch } from "react-router-dom";
import { useApi, type Repository, type Schema } from "../api";
import { getInboxReturn } from "../lib/inbox-filters";
import { getDetailPath, getRepositoryReturn } from "../lib/navigation";
import { Button } from "./ui/button";
import {
  Breadcrumb, BreadcrumbEllipsis, BreadcrumbItem, BreadcrumbLink,
  BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator,
} from "./ui/breadcrumb";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "./ui/dropdown-menu";

type Crumb = { label: string; to: string; state?: { inbox: string | null; repository: string | null } };

export function AppBreadcrumb({ admin }: { admin: boolean }) {
  const route = useLocation();
  const path = route.pathname.replace(/\/$/, "") || "/";
  const params = new URLSearchParams(route.search);
  const inboxReturn = getInboxReturn(route.state);
  const repositoryReturn = getRepositoryReturn(route.state);
  const detailPath = getDetailPath(route.pathname);
  const detail = useApi<Pick<Schema["PRDetail"], "repository" | "pull_request">>(detailPath ?? "", false, !!detailPath);
  const securityScanRoute = useMatch("/security/scans/:scanId");
  const securityRepositoryRoute = useMatch("/security/repositories/:repositoryId");
  const securityPath = securityScanRoute ? `/security/scans/${securityScanRoute.params.scanId}`
    : securityRepositoryRoute ? `/security/repositories/${securityRepositoryRoute.params.repositoryId}` : null;
  const securityDetail = useApi<Pick<Schema["SecurityRepositoryOut"], "repository">>(securityPath ?? "", false, !!securityPath);
  const organizationRoute = useMatch("/organizations/:owner/repositories");
  const overviewRoute = useMatch("/repositories/:repositoryId/overview");
  const repositorySettingsRoute = useMatch("/repositories/:repositoryId/settings");
  const repositoryRoute = useMatch("/repositories/:repositoryId") ?? repositorySettingsRoute ?? overviewRoute;
  const repos = useApi<Repository[]>("/repositories", false, !!repositoryRoute);
  const crumbs: Crumb[] = [{ label: "Inbox", to: inboxReturn ?? "/" }];

  if (path === "/repositories" || repositoryRoute) {
    const repository = repos.error ? undefined : repos.data?.find((repo) => String(repo.id) === repositoryRoute?.params.repositoryId);
    const search = new URLSearchParams(params);
    search.delete("tab");
    const repositoryPath = repositoryRoute ? `/repositories/${repositoryRoute.params.repositoryId}` : path;
    const returnPath = repositorySettingsRoute && repositoryReturn?.split("?")[0] === repositoryPath
      ? repositoryReturn : repositoryPath + (search.size ? `?${search}` : "");
    crumbs.push({ label: repository?.full_name || "Repositories", to: returnPath });
    if (overviewRoute) crumbs.push({ label: "Overview", to: path + route.search });
    if (repositorySettingsRoute && admin) crumbs.push({ label: "Configure", to: path + route.search });
  } else if (organizationRoute) {
    crumbs.push({ label: organizationRoute.params.owner ?? "Repositories", to: path });
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
  } else if (path.startsWith("/security")) {
    const repository = securityDetail.error ? undefined : securityDetail.data?.repository;
    crumbs.push({ label: "Repositories", to: "/repositories" });
    if (path.startsWith("/security/repositories/")) crumbs.push({ label: repository?.full_name || "Repository security", to: path + route.search });
    if (path.startsWith("/security/scans/")) {
      if (repository) crumbs.push({ label: repository.full_name, to: `/security/repositories/${repository.id}` });
      crumbs.push({ label: "Security scan", to: path + route.search });
    }
  } else if (path === "/analytics") {
    crumbs.push({ label: "Analytics", to: path + route.search });
  } else if (path === "/mcp/authorize") {
    crumbs.push({ label: "Agent authorization", to: path + route.search });
  } else if (path.startsWith("/mcp/approvals/")) {
    crumbs.push({ label: "Review action", to: path });
  } else if (path === "/settings/api-tokens/new") {
    crumbs.push({ label: "Settings", to: "/settings" });
    crumbs.push({ label: "API tokens", to: "/settings?tab=api-tokens" });
    crumbs.push({ label: "New token", to: path });
  } else if (path === "/settings") {
    const tab = params.get("tab") === "appearance" || params.get("tab") === "mcp" || params.get("tab") === "api-tokens" || admin ? params.get("tab") : null;
    crumbs.push({ label: "Settings", to: "/settings" });
    const tabLabels = new Map([["api-tokens", "API tokens"], ["mcp", "MCP access"], ["github", "GitHub"], ["team", "Team"], ["appearance", "Appearance"]]);
    crumbs.push({ label: tabLabels.get(tab ?? "") ?? "AI connections", to: path + route.search });
  } else if (path !== "/") {
    crumbs.push({ label: "Page not found", to: path });
  }

  const current = crumbs.at(-1)!;
  const ancestors = crumbs.slice(0, -1);

  if (path === "/") return null;
  return <div className="mb-6 flex min-w-0 items-center gap-2">
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
        <BreadcrumbItem className={ancestors.length > 1 && !securityRepositoryRoute ? "min-w-0 sm:shrink-0" : "min-w-0"}>
          <BreadcrumbPage className="truncate font-medium" title={current.label}>{current.label}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  </div>;
}
