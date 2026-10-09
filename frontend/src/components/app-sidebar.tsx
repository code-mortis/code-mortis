import { Link, useLocation } from "react-router-dom";
import { FolderGit2, Inbox, LoaderCircle, LogOut, Plus, Settings, X } from "lucide-react";
import { useApi, type Repository, type Schema } from "../api";
import { getInboxReturn } from "../lib/inbox-filters";
import { Button } from "./ui/button";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupAction,
  SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton,
  SidebarMenuItem, SidebarTrigger, useSidebar,
} from "./ui/sidebar";

export function Brand() {
  return (
    <span className="flex items-center gap-2.5 font-semibold tracking-tight">
      <span aria-hidden="true" className="grid size-7 place-items-center rounded border border-current/30 font-mono text-xs">cm</span>
      Code Mortis
    </span>
  );
}

export function SidebarToggle() {
  const { isMobile, open, openMobile } = useSidebar();
  return <SidebarTrigger
    id="sidebar-toggle"
    className="size-9"
    aria-label={isMobile ? "Open navigation" : open ? "Hide sidebar" : "Show sidebar"}
    aria-expanded={isMobile ? openMobile : open}
    aria-controls="workspace-navigation"
  />;
}

export function AppSidebar({ user, onLogout, signingOut }: {
  user: Schema["UserOut"];
  onLogout: () => void;
  signingOut: boolean;
}) {
  const route = useLocation();
  const { setOpenMobile } = useSidebar();
  const repos = useApi<Repository[]>("/repositories", true);
  const inboxReturn = getInboxReturn(route.state);
  const detailPath = /^\/(pulls|runs)\/[^/]+$/.test(route.pathname) ? route.pathname : null;
  const detail = useApi<Pick<Schema["PRDetail"], "repository">>(detailPath ?? "", false, !!detailPath);
  const repositories = repos.error && [401, 403].includes(repos.error.status) ? [] : repos.data ?? [];
  const requested = Number(new URLSearchParams(route.search).get("repo"));
  const selected = route.pathname === "/repositories"
    ? repositories.find((repo) => repo.id === requested)?.id ?? repositories[0]?.id
    : detailPath && !inboxReturn && !detail.error ? detail.data?.repository.id : undefined;
  const inboxActive = route.pathname === "/" || (!!detailPath && !!inboxReturn);
  const closeMobile = () => setOpenMobile(false);

  return <Sidebar collapsible="offcanvas">
    <SidebarHeader className="px-4 py-5">
      <div className="flex items-center justify-between gap-2">
        <Link to="/" aria-label="Code Mortis home" onClick={closeMobile} className="rounded-md text-primary"><Brand /></Link>
        <Button variant="ghost" size="icon" className="size-9 lg:hidden" aria-label="Close navigation" onClick={closeMobile}><X /></Button>
      </div>
    </SidebarHeader>
    <SidebarContent>
      <nav id="workspace-navigation" aria-label="Main navigation" className="px-2">
        <SidebarGroup>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={inboxActive} className="h-10">
                <Link to={inboxReturn ?? "/"} aria-current={inboxActive ? "page" : undefined} onClick={closeMobile}><Inbox aria-hidden="true" /><span>Inbox</span></Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup className="mt-3">
          <SidebarGroupLabel className="text-muted-foreground">Repositories</SidebarGroupLabel>
          {user.role === "admin" && <SidebarGroupAction asChild title="Manage repositories">
            <Link to="/settings?tab=github" aria-label="Manage repositories" onClick={closeMobile}><Plus aria-hidden="true" /></Link>
          </SidebarGroupAction>}
          {repos.isPending && <p className="px-2 py-3 text-xs text-muted-foreground">Loading repositories…</p>}
          {repos.error && <div className="space-y-1 px-2 py-3 text-xs">
            <p role="alert">Repository activity unavailable.</p>
            <button className="underline underline-offset-4" onClick={() => repos.refetch()}>Retry repositories</button>
          </div>}
          {!repos.isPending && !repos.error && !repositories.length && <p className="px-2 py-3 text-xs text-muted-foreground">
            {user.role === "admin" ? <Link className="underline underline-offset-4" to="/settings?tab=github" onClick={closeMobile}>Connect your first repository</Link> : "No repositories available. Ask your administrator to connect one."}
          </p>}
          <SidebarMenu>
            {repositories.map((repo) => {
              const [owner, name] = repo.full_name.split("/");
              const running = !repos.error && repo.has_running_review;
              return <SidebarMenuItem key={repo.id}>
                <SidebarMenuButton asChild size="lg" isActive={selected === repo.id} tooltip={{ children: repo.full_name, hidden: false }}>
                  <Link to={`/repositories?repo=${repo.id}`} aria-label={`${repo.full_name}${running ? ", review running" : ""}`} aria-current={selected === repo.id ? "page" : undefined} onClick={closeMobile}>
                    <FolderGit2 aria-hidden="true" className="text-muted-foreground" />
                    <span className="min-w-0 flex-1"><span className="block truncate font-medium">{name}</span><span className="block truncate text-xs text-muted-foreground">{owner}</span></span>
                    {running && <span className="flex shrink-0 items-center gap-1 text-xs text-primary"><LoaderCircle aria-hidden="true" className="size-3 motion-safe:animate-spin" />Running</span>}
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>;
            })}
          </SidebarMenu>
        </SidebarGroup>
      </nav>
    </SidebarContent>
    <SidebarFooter className="gap-3 border-t border-sidebar-border p-4">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton asChild isActive={route.pathname === "/settings"} className="h-10">
            <Link to="/settings" aria-current={route.pathname === "/settings" ? "page" : undefined} onClick={closeMobile}><Settings aria-hidden="true" /><span>Settings</span></Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
      <div className="flex min-w-0 items-center gap-2 px-2">
        <span className="mr-auto truncate text-xs" title={`@${user.login}`}>@{user.login}</span>
        <Button variant="ghost" size="icon" className="size-8" aria-label="Sign out" onClick={onLogout} disabled={signingOut}><LogOut aria-hidden="true" className="size-4" /></Button>
      </div>
    </SidebarFooter>
  </Sidebar>;
}
