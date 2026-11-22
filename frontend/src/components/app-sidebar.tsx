import { Link, useLocation } from "react-router-dom";
import { ChartNoAxesCombined, House, GitPullRequest, ListChecks, LoaderCircle, Network, Settings, ShieldCheck, X } from "lucide-react";
import { useWorkspaceNavigation } from "../hooks/use-workspace-navigation";
import { getInboxReturn } from "../lib/inbox-filters";
import { getRepositoryReturn } from "../lib/navigation";
import { Button } from "./ui/button";
import { Sidebar, SidebarContent, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarTrigger, useSidebar } from "./ui/sidebar";

export function SidebarToggle() {
  const { isMobile, open, openMobile } = useSidebar();
  return <SidebarTrigger
    className={`size-9 lg:fixed lg:top-[4.75rem] lg:z-10 lg:size-7 lg:bg-background lg:transition-[left] lg:duration-200 lg:ease-linear motion-reduce:transition-none ${open ? "lg:left-[calc(var(--sidebar-width)+0.125rem)]" : "lg:left-0.5"}`}
    aria-label={isMobile ? "Open navigation" : open ? "Hide sidebar" : "Show sidebar"}
    aria-expanded={isMobile ? openMobile : open} aria-controls="workspace-navigation" />;
}

export function AppSidebar({ admin }: { admin: boolean }) {
  const { repository, section } = useWorkspaceNavigation();
  const route = useLocation();
  const { setOpenMobile } = useSidebar();
  if (!repository) return null;
  const base = `/repositories/${repository.id}`;
  const repositoryReturn = getRepositoryReturn(route.state);
  const inbox = getInboxReturn(route.state);
  const search = new URLSearchParams(route.search);
  search.delete("tab");
  const listPath = base + (search.size ? `?${search}` : "");
  const savedList = repositoryReturn?.split("?")[0] === base ? repositoryReturn : null;
  const pullPath = route.pathname.replace(/\/+$/, "") === base ? listPath
    : savedList ?? (section === "settings" ? listPath : base);
  const running = ["queued", "running", "blocked"].includes(repository.security?.latest_status ?? "");
  const items = [
    { key: "overview", label: "Overview", path: base + "/overview", icon: House },
    { key: "analytics", label: "Analytics", path: base + "/analytics", icon: ChartNoAxesCombined },
    { key: "pulls", label: "Pull requests", path: pullPath, icon: GitPullRequest },
    ...(admin ? [{ key: "custom-rules", label: "Custom rules", path: base + "/custom-rules", icon: ListChecks }] : []),
    { key: "security", label: "Security", path: `/security/repositories/${repository.id}`, icon: ShieldCheck },
    { key: "code-graph", label: "Code graph", path: base + "/code-graph", icon: Network },
    ...(admin ? [{ key: "settings", label: "Repository settings", path: base + "/settings", icon: Settings }] : []),
  ];
  return <Sidebar collapsible="offcanvas" className="top-14 h-[calc(100svh-3.5rem)]">
    <SidebarHeader className="items-end px-4 py-2 lg:hidden">
      <Button variant="ghost" size="icon" className="size-9" aria-label="Close navigation" onClick={() => setOpenMobile(false)}><X /></Button>
    </SidebarHeader>
    <SidebarContent className="p-2">
      <nav id="workspace-navigation" aria-label="Repository sections">
        <SidebarMenu>
          {items.map(({ key, label, path, icon: Icon }) => <SidebarMenuItem key={key}>
            <SidebarMenuButton asChild isActive={section === key} className="h-10">
              <Link to={path} state={{ inbox, repository: pullPath }} aria-label={key === "security" && running ? "Security, scan running" : undefined} aria-current={section === key ? "page" : undefined} onClick={() => setOpenMobile(false)}>
                <Icon aria-hidden="true" /><span className="flex-1">{label}</span>
                {((key === "security" && running) || (key === "pulls" && repository.has_running_review)) && <LoaderCircle aria-hidden="true" className="size-3 text-primary motion-safe:animate-spin" />}
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>)}
        </SidebarMenu>
      </nav>
    </SidebarContent>
  </Sidebar>;
}
