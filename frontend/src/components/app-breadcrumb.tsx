import { Fragment } from "react";
import { Link, useLocation, useMatch } from "react-router-dom";
import { useApi, type Schema } from "../api";
import { getInboxReturn } from "../lib/inbox-filters";
import { getDetailPath, getRepositoryReturn } from "../lib/navigation";
import { useWorkspaceNavigation } from "../hooks/use-workspace-navigation";
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

export function AppBreadcrumb({ admin }: { admin: boolean }) {
  const { repository } = useWorkspaceNavigation();
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
  const securityDetail = useApi<Pick<Schema["SecurityScanDetail"], "repository"> & Partial<Pick<Schema["SecurityScanDetail"], "scan">>>(securityPath ?? "", false, !!securityPath);
  const organizationRoute = useMatch("/organizations/:owner/repositories");
  const overviewRoute = useMatch("/repositories/:repositoryId/overview");
  const analyticsRoute = useMatch("/repositories/:repositoryId/analytics");
  const repositorySettingsRoute = useMatch("/repositories/:repositoryId/settings");
  const pullListRoute = useMatch("/repositories/:repositoryId");
  const pull = detail.error ? undefined : detail.data?.pull_request;
  const security = securityDetail.error ? undefined : securityDetail.data;
  const repo = detailPath ? (pull ? detail.data?.repository : undefined)
    : securityPath ? security?.repository : repository;
  const crumbs: Crumb[] = [];
  if (!repo || inboxReturn) crumbs.push({ label: "Inbox", to: inboxReturn ?? "/" });

  if (repo) {
    const repositoryPath = `/repositories/${repo.id}`;
    const search = new URLSearchParams(params);
    search.delete("tab");
    const listPath = repositoryPath + (search.size ? `?${search}` : "");
    const savedList = repositoryReturn?.split("?")[0] === repositoryPath ? repositoryReturn : null;
    const returnPath = pullListRoute ? listPath : savedList ?? (repositorySettingsRoute ? listPath : repositoryPath);
    const returnState = { inbox: inboxReturn, repository: returnPath };
    crumbs.push({ label: repo.full_name.split("/").at(-1) || repo.full_name, to: repositoryPath + "/overview", state: returnState });
    if (overviewRoute || analyticsRoute) {
      crumbs.push({ label: analyticsRoute ? "Analytics" : "Overview", to: path });
    } else if (repositorySettingsRoute && admin) {
      crumbs.push({ label: "Repository settings", to: path });
    } else if (securityPath) {
      crumbs.push({ label: "Security", to: `/security/repositories/${repo.id}` });
      if (securityScanRoute) crumbs.push({ label: security?.scan?.head_sha ? `Scan · ${security.scan.head_sha.slice(0, 7)}` : "Security scan", to: path });
    } else {
      crumbs.push({ label: "Pull requests", to: returnPath, state: returnState });
      if (pull) crumbs.push({ label: `#${pull.number}`, to: `/pulls/${pull.id}`, state: returnState });
      if (path.startsWith("/runs/")) crumbs.push({ label: "Review run", to: path });
    }
  } else if (path === "/repositories" || pullListRoute || repositorySettingsRoute || overviewRoute || analyticsRoute) {
    crumbs.push({ label: analyticsRoute ? "Analytics" : overviewRoute ? "Overview" : "Repositories", to: path });
  } else if (organizationRoute) {
    crumbs.push({ label: organizationRoute.params.owner ?? "Repositories", to: path });
  } else if (detailPath) {
    crumbs.push({ label: path.startsWith("/runs/") ? "Review run" : "Pull request", to: path });
  } else if (path.startsWith("/security")) {
    crumbs.push({ label: "Repositories", to: "/repositories" });
    if (securityRepositoryRoute || securityScanRoute) crumbs.push({ label: securityScanRoute ? "Security scan" : "Security", to: path });
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
  return <div className={`mb-6 flex min-w-0 items-center gap-2 ${repository ? "lg:min-h-9" : ""}`}>
    {repository && <SidebarToggle />}
    <Breadcrumb aria-label="Breadcrumb" className="min-w-0 flex-1">
      <BreadcrumbList className="flex-nowrap">
        {ancestors.map((crumb, index) => <Fragment key={crumb.to}>
          <BreadcrumbItem className={index === 0 ? "min-w-0" : "hidden min-w-0 sm:inline-flex"}>
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
        <BreadcrumbItem className="min-w-0 shrink-0">
          <BreadcrumbPage className="truncate font-medium" title={current.label}>{current.label}</BreadcrumbPage>
        </BreadcrumbItem>
      </BreadcrumbList>
    </Breadcrumb>
  </div>;
}
