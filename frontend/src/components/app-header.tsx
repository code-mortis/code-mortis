import { Link, useLocation, useNavigate } from "react-router-dom";
import { Building, FolderGit2, Moon, Settings, Sun } from "lucide-react";
import type { Schema } from "../api";
import { useWorkspaceNavigation } from "../hooks/use-workspace-navigation";
import logo from "../assets/logo.svg";
import { AccountMenu } from "./account-menu";
import { Button } from "./ui/button";
import { NotificationInbox } from "./notification-inbox";
import { WorkspaceProgress } from "./workspace-progress";
import { Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList, ComboboxTrigger } from "./ui/combobox";

function WorkspacePicker({ label, value, placeholder, searchLabel, options, onChoose, disabled }: {
  label: "Organization" | "Repository"; value?: string; placeholder: string; searchLabel: string;
  options: { value: string; label: string }[]; onChoose: (value: string) => void; disabled?: boolean;
}) {
  const current = options.find((item) => item.value === value) ?? null;
  const selected = current?.label ?? placeholder;
  const Icon = label === "Organization" ? Building : FolderGit2;
  return <Combobox items={options} value={current}
    itemToStringLabel={(item) => item.label} isItemEqualToValue={(item, current) => item.value === current.value}
    disabled={disabled} autoHighlight onValueChange={(item) => { if (item) onChoose(item.value); }}>
    <ComboboxTrigger render={<Button variant="ghost" size="sm" />} aria-label={`${label}: ${selected}`} title={selected}
      className="min-w-0 max-w-48 shrink gap-1.5 px-2 font-normal has-[>svg]:px-2 aria-expanded:bg-accent max-sm:gap-1 max-sm:has-[>svg]:px-1.5 pointer-coarse:h-11 [&>[data-slot=combobox-trigger-icon]]:size-3.5">
      <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
      <span className={label === "Repository" && value ? "truncate font-semibold" : "truncate"}>{selected}</span>
    </ComboboxTrigger>
    <ComboboxContent aria-label={searchLabel} className="w-64 min-w-0">
      <ComboboxInput aria-label={searchLabel} placeholder={searchLabel} showTrigger={false} className="[&>input]:m-0" />
      <ComboboxEmpty>No matches found.</ComboboxEmpty>
      <ComboboxList>
        {(item: { value: string; label: string }) => <ComboboxItem key={item.value} value={item} className="min-h-10 break-words [overflow-wrap:anywhere]">{item.label}</ComboboxItem>}
      </ComboboxList>
    </ComboboxContent>
  </Combobox>;
}

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
      <nav aria-label="Workspace" className="order-last flex w-full min-w-0 items-center gap-1 pb-3 sm:order-none sm:w-auto sm:flex-1 sm:pb-0">
        <WorkspacePicker label="Organization" value={organization ?? "__workspace__"} placeholder="Workspace" searchLabel="Find an organization"
          disabled={directory.isPending || !!directory.error} options={[
            { value: "__workspace__", label: "Workspace" },
            ...organizations.map((name) => ({ value: name, label: name })),
          ]} onChoose={(name) => navigate(name === "__workspace__" ? "/" : `/organizations/${encodeURIComponent(name)}/repositories`)} />
        {organization && <>
          <span aria-hidden="true" className="shrink-0 text-sm text-muted-foreground">/</span>
          <WorkspacePicker label="Repository" value={repository ? String(repository.id) : undefined} placeholder="Choose repository" searchLabel="Find a repository"
            disabled={directory.isPending || !!directory.error || !available.length}
            options={available.map((repo) => ({ value: String(repo.id), label: repo.full_name.split("/").slice(1).join("/") || repo.full_name }))}
            onChoose={(id) => navigate(`/repositories/${id}/overview`)} />
        </>}
      </nav>
      <div className="ml-auto flex shrink-0 items-center gap-1 max-sm:min-h-14">
        <NotificationInbox key={user.id} userId={user.id} />
        <Button variant="ghost" size="icon" onClick={onToggleTheme} aria-label={themeLabel} title={themeLabel}
          className="relative text-muted-foreground motion-reduce:transition-none pointer-coarse:size-11">
          <Sun aria-hidden="true" className="size-4 rotate-0 scale-100 transition-transform duration-200 ease-out dark:-rotate-90 dark:scale-0 motion-reduce:transition-none" />
          <Moon aria-hidden="true" className="absolute size-4 rotate-90 scale-0 transition-transform duration-200 ease-out dark:rotate-0 dark:scale-100 motion-reduce:transition-none" />
        </Button>
        <Button asChild variant="ghost" size="icon" className="text-muted-foreground pointer-coarse:size-11">
          <Link to="/settings" aria-label="Settings" title="Settings" aria-current={route.pathname.startsWith("/settings") ? "page" : undefined}>
            <Settings aria-hidden="true" />
          </Link>
        </Button>
        <span aria-hidden="true" className="mx-1 h-5 w-px bg-border" />
        <AccountMenu user={user} onLogout={onLogout} signingOut={signingOut} />
      </div>
    </div>
    <WorkspaceProgress />
  </header>;
}
