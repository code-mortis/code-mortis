import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { FolderGit2 } from "lucide-react";
import { useWorkspaceNavigation } from "../hooks/use-workspace-navigation";
import { Empty, ErrorNotice, Loading, PageTitle } from "./common";
import { Button } from "./ui/button";
import { Repositories } from "../reviews";

export function RepositoryDirectory({ admin }: { admin: boolean }) {
  const { owner } = useParams();
  const [params] = useSearchParams();
  const { directory, repositories, organization } = useWorkspaceNavigation();
  if (!owner && (params.has("repo") || params.has("configure"))) return <Repositories admin={admin} />;
  if (directory.isPending) return <Loading />;
  if (directory.error) return <div className="space-y-3"><ErrorNotice error={directory.error} /><Button variant="outline" onClick={() => directory.refetch()}>Retry directory</Button></div>;
  const items = owner ? repositories.filter((repo) => repo.full_name.split("/")[0].toLowerCase() === owner.toLowerCase()) : repositories;
  return <>
    <PageTitle title={owner ? `${organization ?? owner} repositories` : "Repositories"}>Choose a repository to view its overview, pull requests, and security findings.</PageTitle>
    {items.length ? <nav aria-label={owner ? `Repositories in ${organization ?? owner}` : "Repositories"} className="divide-y rounded-lg border bg-card px-4">
      {items.map((repo) => <Link key={repo.id} to={`/repositories/${repo.id}/overview`} className="flex min-h-14 items-center gap-3 py-3 text-sm hover:text-primary">
        <FolderGit2 aria-hidden="true" className="size-4 shrink-0" /><span className="min-w-0 break-words [overflow-wrap:anywhere]">{repo.full_name}</span>
      </Link>)}
    </nav> : <Empty title={owner ? "No repositories available" : "Connect your first repository"}>
      {admin ? <>Connect repositories in <Link className="underline" to="/settings?tab=github">GitHub settings</Link>.</> : "Ask your administrator to connect a repository you can access on GitHub."}
    </Empty>}
  </>;
}

export function LegacyAnalytics() {
  const location = useLocation();
  const { directory, repositories } = useWorkspaceNavigation();
  const params = new URLSearchParams(location.search);
  const ids = [...new Set(params.getAll("repo"))];
  const repository = ids.length === 1 ? repositories.find((repo) => String(repo.id) === ids[0]) : undefined;
  if (directory.isPending) return <Loading />;
  params.delete("repo");
  return <Navigate to={repository ? `/repositories/${repository.id}/analytics${params.size ? `?${params}` : ""}${location.hash}` : "/repositories"} state={location.state} replace />;
}
