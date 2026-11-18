import { Link, useLocation, useNavigate } from "react-router-dom";
import { Building2, FolderGit2, Moon, Settings, Sun } from "lucide-react";
import type { Schema } from "../api";
import { useWorkspaceNavigation } from "../hooks/use-workspace-navigation";
import logo from "../assets/logo.svg";
import { AccountMenu } from "./account-menu";
import { SearchSelect } from "./search-select";
import { Button } from "./ui/button";

export function AppHeader({ user, onLogout, signingOut, dark, onToggleTheme }: {
  user: Schema["UserOut"]; onLogout: () => void; signingOut: boolean; dark: boolean; onToggleTheme: () => void;
}) {
  const route = useLocation();
  const navigate = useNavigate();
  const { directory, organizations, repositories, organization, repository } = useWorkspaceNavigation();
  const themeLabel = dark ? "Switch to light mode" : "Switch to dark mode";
  const available = repositories.filter((repo) => (repo.full_name.split("/")[0]).toLowerCase() === organization?.toLowerCase());
  return <header className="sticky top-0 z-20 border-b bg-background px-4 sm:px-8">
    <div className="flex min-h-14 min-w-0 flex-wrap items-center gap-2 sm:gap-3">
      <Link to="/" aria-label="Code Mortis home" className="shrink-0 rounded-md">
        <img src={logo} alt="" className="h-7 w-auto" width="232" height="192" />
      </Link>
      <nav aria-label="Workspace" className="order-last flex w-full min-w-0 gap-2 pb-3 sm:order-none sm:w-auto sm:flex-1 sm:pb-0">
        <SearchSelect aria-label="Organization" value={organization ?? "__workspace__"} placeholder="Workspace" disabled={directory.isPending || !!directory.error}
          className="w-40 flex-1 sm:flex-none lg:w-44" options={[
            { value: "__workspace__", label: "Workspace", icon: <Building2 aria-hidden="true" className="size-4" /> },
            ...organizations.map((name) => ({ value: name, label: name, icon: <Building2 aria-hidden="true" className="size-4" /> })),
          ]} onChange={(name) => { if (name) navigate(name === "__workspace__" ? "/" : `/organizations/${encodeURIComponent(name)}/repositories`); }} />
        {organization && <SearchSelect aria-label="Repository" value={repository ? String(repository.id) : ""} placeholder="Choose repository"
          disabled={directory.isPending || !!directory.error || !available.length} className="w-40 flex-1 sm:flex-none lg:w-44"
          options={available.map((repo) => ({ value: String(repo.id), label: repo.full_name.split("/").slice(1).join("/") || repo.full_name, icon: <FolderGit2 aria-hidden="true" className="size-4" /> }))}
          onChange={(id) => { if (id) navigate(`/repositories/${id}/overview`); }} />}
      </nav>
      <div className="ml-auto flex shrink-0 items-center gap-1">
        <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label={themeLabel} title={themeLabel}
          className="relative overflow-hidden rounded-full bg-muted text-primary hover:bg-accent motion-reduce:transition-none">
          <Sun aria-hidden="true" className="size-[18px] rotate-0 scale-100 transition-transform duration-200 ease-out dark:-rotate-90 dark:scale-0 motion-reduce:transition-none" />
          <Moon aria-hidden="true" className="absolute size-[18px] rotate-90 scale-0 transition-transform duration-200 ease-out dark:rotate-0 dark:scale-100 motion-reduce:transition-none" />
        </Button>
        <Button asChild variant="ghost" size="icon"><Link to="/settings" aria-label="Settings" title="Settings" aria-current={route.pathname.startsWith("/settings") ? "page" : undefined}><Settings aria-hidden="true" /></Link></Button>
        <AccountMenu user={user} onLogout={onLogout} signingOut={signingOut} />
      </div>
    </div>
  </header>;
}
