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
} from "./components/common";
import { Repositories, PullDetail, RunDetail } from "./reviews";
import { Inbox } from "./inbox";
import { AppSidebar, Brand } from "./components/app-sidebar";
import { AppHeader } from "./components/app-header";
import { SidebarProvider } from "./components/ui/sidebar";
import { Toaster } from "./components/ui/sonner";
import { SettingsPage } from "./settings";
import { applyAppearance, readAppearance, type ColorMode, type ThemeId } from "./lib/appearance";
import "./index.css";

const Analytics = lazy(() => import("./analytics"));

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
    <div className="mx-auto max-w-lg px-6 py-16 sm:py-28">
      <Brand />
      <h1 className="mt-14 text-3xl font-semibold tracking-tight">
        {configured
          ? "Connect your GitHub account."
          : "Give your reviews a home."}
      </h1>
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
      <div className="px-8">
        <Loading />
      </div>
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
    <SidebarProvider>
      <AppSidebar user={session.data} onLogout={logout} signingOut={action.isPending} />
      <div className="min-w-0 flex-1">
        <AppHeader admin={session.data.role === "admin"} dark={dark} onToggleTheme={() => setMode(dark ? "light" : "dark")} />
        <main className="min-w-0 px-5 py-8 sm:px-9 lg:px-12 lg:py-10">
          <div className="mx-auto max-w-6xl">
            <ErrorNotice error={action.error} />
            <Routes>
              <Route path="/" element={<Inbox admin={session.data.role === "admin"} />} />
              <Route path="/analytics" element={<Suspense fallback={<Loading />}><Analytics /></Suspense>} />
              <Route
                path="/repositories/:repositoryId?"
                element={<Repositories admin={session.data.role === "admin"} />}
              />
              <Route
                path="/repositories/:repositoryId/settings"
                element={<Repositories admin={session.data.role === "admin"} configure />}
              />
              <Route path="/pulls/:id" element={<PullDetail admin={session.data.role === "admin"} />} />
              <Route path="/runs/:id" element={<RunDetail user={session.data} />} />
              <Route
                path="/settings"
                element={<SettingsPage user={session.data} mode={mode} theme={colorTheme} dark={dark} onModeChange={setMode} onThemeChange={setColorTheme} />}
              />
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
      <Toaster theme={dark ? "dark" : "light"} />
    </SidebarProvider>
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
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
