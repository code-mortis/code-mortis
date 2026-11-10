import { Link, useLocation, useMatch } from "react-router-dom";
import { Bot, ChartNoAxesCombined, FolderGit2, Github, Inbox, KeyRound, LoaderCircle, LogOut, MoreVertical, Palette, Plug, Settings, ShieldCheck, Users, X } from "lucide-react";
import { useApi, type Repository, type Schema } from "../api";
import { getInboxReturn } from "../lib/inbox-filters";
import { getDetailPath, getRepositoryReturn } from "../lib/navigation";
import { Button } from "./ui/button";
import { Avatar, AvatarFallback, AvatarImage } from "./ui/avatar";
import { DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "./ui/dropdown-menu";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupAction,
  SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuBadge, SidebarMenuButton,
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
  const repos = useApi<Repository[]>("/repositories", (data) => data?.some((repo) => repo.has_running_review || ["queued", "running", "blocked"].includes(repo.security?.latest_status ?? "")) ? 5000 : 60000);
  const inboxReturn = getInboxReturn(route.state);
  const repositoryReturn = getRepositoryReturn(route.state);
  const detailPath = getDetailPath(route.pathname);
  const detail = useApi<Pick<Schema["PRDetail"], "repository">>(detailPath ?? "", false, !!detailPath);
  const accessDenied = !!repos.error && [401, 403].includes(repos.error.status);
  const repositories = accessDenied ? [] : repos.data ?? [];
  const openPrs = repos.data && !repos.error ? repositories.reduce((total, repo) => total + (repo.open_prs ?? 0), 0) : undefined;
  const securityRunning = !repos.error && repositories.some((repo) => ["queued", "running", "blocked"].includes(repo.security?.latest_status ?? ""));
  const repositorySettingsRoute = useMatch("/repositories/:repositoryId/settings");
  const repositoryRoute = useMatch("/repositories/:repositoryId") ?? repositorySettingsRoute;
  const settingsSearch = new URLSearchParams(route.search);
  settingsSearch.delete("tab");
  const selected = repositoryRoute
    ? repositories.find((repo) => String(repo.id) === repositoryRoute.params.repositoryId)?.id
    : detailPath && !detail.error ? detail.data?.repository.id : undefined;
  const inboxActive = route.pathname === "/";
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
                <Link to={inboxReturn ?? "/"} aria-current={inboxActive ? "page" : undefined} aria-describedby={openPrs !== undefined ? "inbox-open-count" : undefined} title={openPrs !== undefined ? `${openPrs} open pull ${openPrs === 1 ? "request" : "requests"}` : undefined} onClick={closeMobile}><Inbox aria-hidden="true" /><span>Inbox</span></Link>
              </SidebarMenuButton>
              {openPrs !== undefined && <SidebarMenuBadge id="inbox-open-count" className="right-2 h-6 min-w-6 rounded-md border border-sidebar-border bg-sidebar px-1.5 text-xs font-normal peer-data-[size=default]/menu-button:top-2">{openPrs}<span className="sr-only"> open pull {openPrs === 1 ? "request" : "requests"}</span></SidebarMenuBadge>}
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={route.pathname.startsWith("/security")} className="h-10">
                <Link to="/security" aria-label={securityRunning ? "Security, scan running" : undefined} aria-current={route.pathname.startsWith("/security") ? "page" : undefined} onClick={closeMobile}>
                  <ShieldCheck aria-hidden="true" /><span className="flex-1">Security</span>
                  {securityRunning && <span className="shrink-0 text-primary"><LoaderCircle aria-hidden="true" className="size-3 motion-safe:animate-spin" /></span>}
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={route.pathname === "/analytics"} className="h-10">
                <Link to="/analytics" aria-current={route.pathname === "/analytics" ? "page" : undefined} onClick={closeMobile}><ChartNoAxesCombined aria-hidden="true" /><span>Analytics</span></Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroup>
        <SidebarGroup className="mt-3">
          <SidebarGroupLabel className="text-muted-foreground">Repositories</SidebarGroupLabel>
          {user.role === "admin" && <SidebarGroupAction asChild title="Manage repositories" className="top-2 right-2 h-8 w-auto aspect-auto px-2 text-xs">
            <Link to="/settings?tab=github" aria-label="Manage repositories" onClick={closeMobile}>Manage</Link>
          </SidebarGroupAction>}
          {repos.isPending && <p className="px-2 py-3 text-xs text-muted-foreground">Loading repositories…</p>}
          {repos.error && <div className="space-y-1 px-2 py-3 text-xs">
            <p role="alert">{accessDenied ? "Repository access denied." : "Repository activity unavailable."}</p>
            {!accessDenied && <button className="underline underline-offset-4" onClick={() => repos.refetch()}>Retry repositories</button>}
          </div>}
          {!repos.isPending && !repos.error && !repositories.length && <p className="px-2 py-3 text-xs text-muted-foreground">
            {user.role === "admin" ? <Link className="underline underline-offset-4" to="/settings?tab=github" onClick={closeMobile}>Connect your first repository</Link> : "No repositories available. Ask your administrator to connect one."}
          </p>}
          <SidebarMenu>
            {repositories.map((repo) => {
              const [owner, name = owner] = repo.full_name.split("/");
              const running = !repos.error && repo.has_running_review;
              const path = `/repositories/${repo.id}`;
              const returnPath = repositorySettingsRoute && selected === repo.id
                ? path + (settingsSearch.size ? `?${settingsSearch}` : "")
                : repositoryReturn?.split("?")[0] === path ? repositoryReturn : path;
              return <SidebarMenuItem key={repo.id}>
                <SidebarMenuButton asChild size="lg" isActive={selected === repo.id}>
                  <Link to={returnPath} title={repo.full_name} aria-label={`${repo.full_name}${running ? ", review running" : ""}`} aria-current={selected === repo.id ? "page" : undefined} onClick={closeMobile}>
                    <FolderGit2 aria-hidden="true" className="text-muted-foreground" />
                    <span className="min-w-0 flex-1"><span className="block truncate font-medium">{name}</span><span className="block truncate text-xs text-muted-foreground">{owner}</span></span>
                    {running && <span className="shrink-0 text-primary"><LoaderCircle aria-hidden="true" className="size-3 motion-safe:animate-spin" /></span>}
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>;
            })}
          </SidebarMenu>
        </SidebarGroup>
      </nav>
    </SidebarContent>
    <SidebarFooter className="gap-3 p-4">
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton asChild isActive={route.pathname === "/settings" || route.pathname.startsWith("/settings/")} className="h-10">
            <Link to="/settings" aria-current={route.pathname === "/settings" || route.pathname.startsWith("/settings/") ? "page" : undefined} onClick={closeMobile}><Settings aria-hidden="true" /><span>Settings</span></Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton size="lg" aria-label={`Account menu for @${user.login}`} title={`@${user.login}`} disabled={signingOut}>
                <Avatar className="rounded-lg">
                  <AvatarImage src={user.avatar_url || undefined} alt="" className="object-cover" />
                  <AvatarFallback className="rounded-lg bg-sidebar-accent text-xs font-medium text-sidebar-accent-foreground">{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                </Avatar>
                <span className="min-w-0 flex-1 text-left">
                  <span className="block truncate font-medium">{user.login}</span>
                  <span className="block truncate text-xs text-muted-foreground">{signingOut ? "Signing out…" : user.role === "admin" ? "Administrator" : "Member"}</span>
                </span>
                {signingOut ? <LoaderCircle aria-hidden="true" className="size-4 motion-safe:animate-spin" /> : <MoreVertical aria-hidden="true" className="size-4 text-muted-foreground" />}
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="end" sideOffset={8} collisionPadding={16} className="w-60 max-w-[calc(100vw-2rem)] rounded-lg">
              <DropdownMenuLabel className="flex items-center gap-2 px-2 py-2 font-normal">
                <Avatar className="shrink-0 rounded-lg">
                  <AvatarImage src={user.avatar_url || undefined} alt="" className="object-cover" />
                  <AvatarFallback className="rounded-lg bg-sidebar-accent text-xs font-medium text-sidebar-accent-foreground">{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                </Avatar>
                <span className="min-w-0">
                  <span className="block truncate font-medium" title={user.login}>{user.login}</span>
                  <span className="block text-xs text-muted-foreground">{user.role === "admin" ? "Administrator" : "Member"}</span>
                </span>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=connections" onClick={closeMobile}><Plug aria-hidden="true" />AI connections</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=api-tokens" onClick={closeMobile}><KeyRound aria-hidden="true" />API tokens</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=mcp" onClick={closeMobile}><Bot aria-hidden="true" />MCP access</Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                  <Link to="/settings?tab=appearance" onClick={closeMobile}><Palette aria-hidden="true" />Appearance</Link>
                </DropdownMenuItem>
                {user.role === "admin" && <>
                  <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                    <Link to="/settings?tab=github" onClick={closeMobile}><Github aria-hidden="true" />GitHub</Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild className="min-h-10 lg:min-h-8">
                    <Link to="/settings?tab=team" onClick={closeMobile}><Users aria-hidden="true" />Team</Link>
                  </DropdownMenuItem>
                </>}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onLogout} disabled={signingOut} className="min-h-10 lg:min-h-8"><LogOut aria-hidden="true" />Sign out</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
  </Sidebar>;
}
