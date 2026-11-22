import { createContext, useContext, type ReactNode } from "react";
import { useLocation, useMatch } from "react-router-dom";
import { useAction, useApi, type Repository, type Schema } from "../api";
import { getDetailPath } from "../lib/navigation";

const hasActivity = (repo: Repository) => repo.has_running_review || ["queued", "running", "blocked"].includes(repo.security?.latest_status ?? "");

function useNavigation() {
  const route = useLocation();
  const sync = useAction();
  const detailPath = getDetailPath(route.pathname);
  const detail = useApi<Pick<Schema["PRDetail"], "repository" | "pull_request" | "has_active_run">>(detailPath ?? "", false, !!detailPath);
  const repositoryRoute = useMatch("/repositories/:repositoryId");
  const settingsRoute = useMatch("/repositories/:repositoryId/settings");
  const customRulesRoute = useMatch("/repositories/:repositoryId/custom-rules");
  const overviewRoute = useMatch("/repositories/:repositoryId/overview");
  const analyticsRoute = useMatch("/repositories/:repositoryId/analytics");
  const codeGraphRoute = useMatch("/repositories/:repositoryId/code-graph");
  const securityRoute = useMatch("/security/repositories/:repositoryId");
  const scanRoute = useMatch("/security/scans/:scanId");
  const organizationRoute = useMatch("/organizations/:owner/repositories");
  const scan = useApi<Pick<Schema["SecurityRepositoryOut"], "repository">>(scanRoute ? `/security/scans/${scanRoute.params.scanId}` : "", false, !!scanRoute);
  const tab = new URLSearchParams(route.search).get("tab");
  const idleCode = detailPath?.startsWith("/pulls/") && (tab === "code" || tab === "impact")
    && !detail.error && ["closed", "merged"].includes(detail.data?.pull_request.state ?? "") && !detail.data?.has_active_run;
  const directory = useApi<Repository[]>("/repositories", (data) => idleCode && !data?.some(hasActivity) ? 60000 : 5000);
  const repositories = directory.error ? [] : directory.data ?? [];
  const repositoryId = (repositoryRoute ?? settingsRoute ?? customRulesRoute ?? overviewRoute ?? analyticsRoute ?? codeGraphRoute ?? securityRoute)?.params.repositoryId
    ?? (detailPath && !detail.error ? detail.data?.repository.id : undefined)
    ?? (scanRoute && !scan.error ? scan.data?.repository.id : undefined);
  const repository = repositories.find((repo) => String(repo.id) === String(repositoryId));
  const organizations = [...new Map(repositories.map((repo) => {
    const owner = repo.full_name.includes("/") ? repo.full_name.split("/")[0] : repo.full_name;
    return [owner.toLowerCase(), owner] as const;
  })).values()].sort((a, b) => a.localeCompare(b));
  const owner = repository?.full_name.split("/")[0] ?? organizationRoute?.params.owner;
  const organization = organizations.find((name) => name.toLowerCase() === owner?.toLowerCase());
  const section = overviewRoute ? "overview" : analyticsRoute ? "analytics" : settingsRoute ? "settings"
    : customRulesRoute ? "custom-rules" : codeGraphRoute ? "code-graph" : securityRoute || scanRoute ? "security" : "pulls";
  return { directory, repositories, organizations, organization, repository, section, sync };
}

const WorkspaceNavigation = createContext<ReturnType<typeof useNavigation> | null>(null);

export function WorkspaceNavigationProvider({ children }: { children: ReactNode }) {
  return <WorkspaceNavigation.Provider value={useNavigation()}>{children}</WorkspaceNavigation.Provider>;
}

export function useWorkspaceNavigation() {
  const value = useContext(WorkspaceNavigation);
  if (!value) throw new Error("Workspace navigation requires its provider.");
  return value;
}
