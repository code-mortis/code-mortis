import "./sentry";
import { reactErrorHandler } from "@sentry/react";
import React, { lazy, Suspense, useEffect, useLayoutEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useLocation,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Github } from "lucide-react";
import { api, ApiError, setCsrf, useAction, useApi, type Schema } from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import {
  Empty,
  ErrorNotice,
  Field,
  Loading,
  PageTitle,
} from "./components/common";
import { Repositories, PullDetail, RunDetail } from "./reviews";
import { Inbox } from "./inbox";
import { AppSidebar } from "./components/app-sidebar";
import { Brand } from "./components/brand";
import raven from "./assets/raven.svg";
import logo from "./assets/logo.svg";
import { AppHeader } from "./components/app-header";
import { WorkspaceProgress, WorkspaceProgressFallback, WorkspaceProgressProvider } from "./components/workspace-progress";
import { AppBreadcrumb } from "./components/app-breadcrumb";
import { RepositoryDirectory, LegacyAnalytics } from "./components/repository-directory";
import { WorkspaceNavigationProvider, useWorkspaceNavigation } from "./hooks/use-workspace-navigation";
import { SidebarProvider } from "./components/ui/sidebar";
import { Toaster } from "./components/ui/sonner";
import { SettingsPage } from "./settings";
import { McpAuthorizePage, McpApprovalPage } from "./mcp";
import { applyAppearance, readAppearance, type ColorMode, type ThemeId } from "./lib/appearance";
import "./index.css";

const Analytics = lazy(() => import("./analytics"));
const SecurityRepository = lazy(() => import("./security").then((module) => ({ default: module.SecurityRepository })));
const SecurityScan = lazy(() => import("./security").then((module) => ({ default: module.SecurityScan })));

function NavigationError() {
  const { directory } = useWorkspaceNavigation();
  const path = useLocation().pathname;
  if (!directory.error || /^\/(?:repositories|organizations)(?:\/|$)/.test(path) || path === "/analytics") return null;
  return <div className="mb-4 space-y-2"><ErrorNotice error={directory.error} /><Button variant="outline" onClick={() => directory.refetch()}>Retry repositories</Button></div>;
}

function RepositoryOverview({ analytics = false }: { analytics?: boolean }) {
  const route = useLocation();
  const params = new URLSearchParams(route.search);
  const { directory, repository } = useWorkspaceNavigation();
  if (directory.isPending) return <Loading />;
  if (directory.error) return <div className="space-y-3"><ErrorNotice error={directory.error} /><Button variant="outline" onClick={() => directory.refetch()}>Retry repositories</Button></div>;
  if (!repository) return <Empty title="Repository unavailable">Choose a repository from the navigation.</Empty>;
  if (!analytics && ["tab", "period", "start", "end", "ordering", "severity", "category", "status", "repo"].some((key) => params.has(key))) {
    return <Navigate to={{ pathname: `/repositories/${repository.id}/analytics`, search: route.search, hash: route.hash }} state={route.state} replace />;
  }
  if (!analytics) return <PageTitle title="Overview" />;
  return <Suspense fallback={<WorkspaceProgressFallback />}><Analytics key={repository.id} repository={repository} /></Suspense>;
}

const initialAppearance = readAppearance();
const initialDark = initialAppearance.mode === "dark" || (initialAppearance.mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
applyAppearance(initialAppearance.theme, initialDark);

const cache = new QueryClient({
  defaultOptions: { queries: { staleTime: 15000, refetchOnWindowFocus: true } },
});
function Setup({ configured }: { configured: boolean }) {
  const route = useLocation();
  const [token, setToken] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const params = new URLSearchParams(route.search);
  const authErrors: Record<string, string> = {
    expired:
      "This attempt expired or was already used. Start again in this browser.",
    cancelled: "GitHub sign-in was cancelled. You can try again.",
    provider: "GitHub could not complete authorization. Start a new attempt.",
    membership: "Ask the instance administrator to allow your GitHub account.",
    installation_cancelled:
      "Installation was cancelled. You can start again from Settings.",
    installation_admin:
      "An instance administrator must connect this installation.",
    installation_denied:
      "GitHub did not confirm access to this installation. Check your permissions and retry.",
  };
  const authError = authErrors[params.get("auth_error") ?? ""];
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<{ action: string; manifest: object }>(
        "/setup/manifest",
        "POST",
        { organization },
        { "x-setup-token": token },
      );
      const form = document.createElement("form");
      form.method = "POST";
      form.action = result.action;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "manifest";
      input.value = JSON.stringify(result.manifest);
      form.append(input);
      document.body.append(form);
      form.submit();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }
  return (
    <div className="mx-auto grid min-h-svh max-w-lg content-center items-center gap-12 px-6 py-16 md:max-w-6xl md:grid-cols-2 lg:gap-20 lg:py-28">
      <section className="min-w-0">
        <div className="flex items-end gap-3">
          <img src={logo} alt="Code Mortis" width="232" height="192" className="h-10 w-auto shrink-0 -translate-y-1" />
          <h1 className="text-3xl font-semibold tracking-tight">
            {configured
              ? "Connect your GitHub account."
              : "Give your reviews a home."}
          </h1>
        </div>
        <p className="mt-4 leading-relaxed text-muted-foreground">
          {configured
            ? "Your GitHub App is registered. Next, authorize your GitHub account, install the app on selected repositories, and choose a model connection."
            : "Register a GitHub App for this instance. GitHub requires Contents: Read and write to resolve or reopen review threads. You choose which repositories it can access when installing it."}
        </p>
        {authError && (
          <div
            role="alert"
            className="mt-6 rounded border border-destructive/30 p-4 text-sm"
          >
            <p>{authError}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              Diagnostic: {params.get("diagnostic")}
            </p>
          </div>
        )}
        {configured ? (
          <Button asChild className="mt-8">
            <a href="/api/v1/auth/login">
              <Github />
              Continue with GitHub
            </a>
          </Button>
        ) : (
          <form onSubmit={submit} className="mt-8 space-y-5">
            <ErrorNotice error={error} />
            <Field
              label="Bootstrap token"
              hint="Copy only the CM_BOOTSTRAP_TOKEN value from .env.dev for development, or .env for the server. Each instance has its own token."
            >
              {(id) => (
                <Input
                  id={id}
                  required
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              )}
            </Field>
            <Field
              label="GitHub organization (optional)"
              hint="Leave blank to register the app under your personal account."
            >
              {(id) => (
                <Input
                  id={id}
                  value={organization}
                  onChange={(e) => setOrganization(e.target.value)}
                  placeholder="your-team"
                />
              )}
            </Field>
            <Button disabled={busy} type="submit">
              {busy ? "Opening GitHub…" : "Register GitHub App"}
              <Github />
            </Button>
          </form>
        )}
        <p className="mt-10 text-xs leading-relaxed text-muted-foreground">
          Self-hosted. Your repositories, your model connections.
          <br />
          Access is limited to people allowed by your administrator.
        </p>
      </section>
      <img src={raven} alt="" width="490" height="389" className="h-auto w-full max-w-xs -scale-x-100 justify-self-center md:max-w-lg" />
    </div>
  );
}
function App() {
  const setup = useApi<{
    configured: boolean;
    public_url: string;
    configuration_errors?: string[];
  }>("/setup/status");
  const session = useApi<Schema["UserOut"]>(
    "/auth/me",
    false,
    setup.data?.configured === true,
  );
  const action = useAction();
  const navigate = useNavigate();
  const route = useLocation();
  const [mode, setMode] = useState<ColorMode>(initialAppearance.mode);
  const [colorTheme, setColorTheme] = useState<ThemeId>(initialAppearance.theme);
  const [dark, setDark] = useState(initialDark);
  useLayoutEffect(() => {
    const system = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolvedDark = mode === "dark" || (mode === "system" && system.matches);
      applyAppearance(colorTheme, resolvedDark);
      setDark(resolvedDark);
    };
    apply();
    try {
      localStorage.setItem("theme", mode);
      localStorage.setItem("color-theme", colorTheme);
    } catch { /* Keep the current selection usable without persistent storage. */ }
    system.addEventListener("change", apply);
    return () => system.removeEventListener("change", apply);
  }, [mode, colorTheme]);
  useEffect(() => {
    setCsrf(session.data?.csrf ?? "");
  }, [session.data]);
  if (setup.isPending || (setup.data?.configured && session.isPending))
    return (
      <header className="relative flex min-h-14 items-center border-b bg-background px-4 sm:px-8">
        <img src={logo} alt="Code Mortis" className="h-7 w-auto" width="232" height="192" />
        <WorkspaceProgress />
      </header>
    );
  if (setup.error)
    return (
      <div className="mx-auto max-w-lg p-8">
        <ErrorNotice error={setup.error} />
        <Button className="mt-4" onClick={() => setup.refetch()}>
          Retry connection
        </Button>
      </div>
    );
  if (setup.data?.configuration_errors?.length)
    return (
      <div className="mx-auto max-w-lg space-y-4 p-8">
        <Brand />
        <h1 className="text-xl font-semibold">Check instance configuration</h1>
        {setup.data.configuration_errors.map((message) => (
          <p key={message} role="alert" className="text-sm">
            {message}
          </p>
        ))}
        <Button onClick={() => setup.refetch()}>Check again</Button>
      </div>
    );
  if (new URLSearchParams(route.search).has("auth_error"))
    return <Setup configured={setup.data?.configured ?? false} />;
  if (!setup.data?.configured || session.error?.status === 401)
    return <Setup configured={setup.data?.configured ?? false} />;
  if (!session.data)
    return (
      <div className="mx-auto max-w-lg p-8">
        <Brand />
        <div className="mt-8">
          <ErrorNotice error={session.error} />
        </div>
        <Button asChild className="mt-6">
          <a href="/api/v1/auth/login">Try another GitHub account</a>
        </Button>
      </div>
    );
  async function logout() {
    await action.mutateAsync({ path: "/auth/logout" });
    setCsrf("");
    cache.clear();
    navigate("/");
    window.location.reload();
  }
  return (
    <WorkspaceNavigationProvider>
    <SidebarProvider className="flex-col">
      <a href="#main-content" onClick={(event) => {
        event.preventDefault();
        document.getElementById("main-content")?.focus();
      }} className="sr-only z-50 rounded-md bg-background px-4 py-3 text-sm font-medium focus:not-sr-only focus:fixed focus:left-4 focus:top-3">Skip to content</a>
      <AppHeader user={session.data} onLogout={logout} signingOut={action.isPending} dark={dark} onToggleTheme={() => setMode(dark ? "light" : "dark")} />
      <div className="flex min-w-0 flex-1">
      <AppSidebar admin={session.data.role === "admin"} />
      <div className="min-w-0 flex-1">
        <main id="main-content" tabIndex={-1} className="min-w-0 px-5 py-6 sm:px-8 lg:py-8 has-[[data-slot=breadcrumb]]:pt-4">
          <div className="mx-auto max-w-6xl">
            <NavigationError />
            <AppBreadcrumb admin={session.data.role === "admin"} />
            <ErrorNotice error={action.error} />
            <Routes>
              <Route path="/" element={<Inbox admin={session.data.role === "admin"} />} />
              <Route path="/analytics" element={<LegacyAnalytics />} />
              <Route path="/repositories/:repositoryId/overview" element={<RepositoryOverview />} />
              <Route path="/repositories/:repositoryId/analytics" element={<RepositoryOverview analytics />} />
              <Route path="/organizations/:owner/repositories" element={<RepositoryDirectory admin={session.data.role === "admin"} />} />
              <Route path="/repositories" element={<RepositoryDirectory admin={session.data.role === "admin"} />} />
              <Route path="/security" element={<Navigate to="/repositories" replace />} />
              <Route path="/security/repositories/:id" element={<Suspense fallback={<WorkspaceProgressFallback />}><SecurityRepository user={session.data} /></Suspense>} />
              <Route path="/security/scans/:id" element={<Suspense fallback={<WorkspaceProgressFallback />}><SecurityScan user={session.data} /></Suspense>} />
              <Route
                path="/repositories/:repositoryId"
                element={<Repositories admin={session.data.role === "admin"} />}
              />
              <Route
                path="/repositories/:repositoryId/settings"
                element={<Repositories admin={session.data.role === "admin"} configure />}
              />
              <Route path="/pulls/:id" element={<PullDetail key={`${session.data.id}:${session.data.csrf}`} admin={session.data.role === "admin"} />} />
              <Route path="/runs/:id" element={<RunDetail user={session.data} />} />
              <Route
                path="/settings"
                element={<SettingsPage user={session.data} mode={mode} theme={colorTheme} dark={dark} onModeChange={setMode} onThemeChange={setColorTheme} />}
              />
              <Route
                path="/settings/api-tokens/new"
                element={<SettingsPage creatingToken user={session.data} mode={mode} theme={colorTheme} dark={dark} onModeChange={setMode} onThemeChange={setColorTheme} />}
              />
              <Route path="/mcp/authorize" element={<McpAuthorizePage />} />
              <Route path="/mcp/approvals/:id" element={<McpApprovalPage />} />
              <Route
                path="/setup"
                element={<Navigate to="/settings?tab=github" replace />}
              />
              <Route
                path="*"
                element={
                  <Empty title="Page not found">
                    <NavLink className="underline" to="/">
                      Return to inbox
                    </NavLink>
                  </Empty>
                }
              />
            </Routes>
          </div>
        </main>
      </div>
      </div>
      <Toaster theme={dark ? "dark" : "light"} />
    </SidebarProvider>
    </WorkspaceNavigationProvider>
  );
}
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <div className="p-8">
        <ErrorNotice
          error={
            new ApiError(
              500,
              "This page could not be displayed. Refresh to try again.",
            )
          }
        />
        <Button className="mt-4" onClick={() => window.location.reload()}>
          Refresh page
        </Button>
      </div>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("root")!, {
  onUncaughtError: reactErrorHandler(),
  onCaughtError: reactErrorHandler(),
  onRecoverableError: reactErrorHandler(),
}).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={cache}>
        <WorkspaceProgressProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </WorkspaceProgressProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
