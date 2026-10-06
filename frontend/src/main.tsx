import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Blocks, Github, LogOut, Settings } from "lucide-react";
import { api, ApiError, setCsrf, useAction, useApi, type Schema } from "./api";
import { Button } from "./components/ui/button";
import { Input } from "./components/ui/input";
import {
  Empty,
  ErrorNotice,
  Field,
  Loading,
  NativeSelect,
} from "./components/common";
import { Repositories, PullDetail, RunDetail } from "./reviews";
import { SettingsPage } from "./settings";
import "./index.css";

const cache = new QueryClient({
  defaultOptions: { queries: { staleTime: 15000, refetchOnWindowFocus: true } },
});
function Brand() {
  return (
    <span className="flex items-center gap-2.5 font-semibold tracking-tight">
      <span
        aria-hidden="true"
        className="grid size-7 place-items-center rounded border border-current/30 font-mono text-xs"
      >
        cm
      </span>
      Code Mortis
    </span>
  );
}
function Setup({ configured }: { configured: boolean }) {
  const [token, setToken] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
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
          ? "Ready for your first review."
          : "Give your reviews a home."}
      </h1>
      <p className="mt-4 leading-relaxed text-muted-foreground">
        {configured
          ? "Sign in with GitHub, connect a repository, and choose the model that reviews your code."
          : "Register a GitHub App for this instance. It publishes comments under its own name and keeps review threads in sync."}
      </p>
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
            hint="Use CM_BOOTSTRAP_TOKEN from your instance’s .env file."
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
  const setup = useApi<{ configured: boolean; public_url: string }>(
    "/setup/status",
  );
  const session = useApi<Schema["UserOut"]>(
    "/auth/me",
    false,
    setup.data?.configured === true,
  );
  const action = useAction();
  const navigate = useNavigate();
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem("theme");
    return saved === "light" || saved === "dark" ? saved : "system";
  });
  useEffect(() => {
    const system = matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.classList.toggle(
        "dark",
        theme === "dark" || (theme === "system" && system.matches),
      );
    apply();
    localStorage.setItem("theme", theme);
    system.addEventListener("change", apply);
    return () => system.removeEventListener("change", apply);
  }, [theme]);
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
    <div className="min-h-dvh lg:grid lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside className="bg-[var(--rail)] text-[var(--rail-foreground)] lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col">
        <div className="flex items-center justify-between px-5 py-5 lg:px-6 lg:py-8">
          <NavLink to="/" aria-label="Code Mortis home">
            <Brand />
          </NavLink>
          <NativeSelect
            className="w-28 border-white/20 bg-[var(--rail)] text-xs lg:hidden"
            aria-label="Appearance"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          >
            <option value="system">System</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </NativeSelect>
        </div>
        <nav
          aria-label="Main navigation"
          className="flex gap-1 px-3 pb-3 lg:flex-col lg:py-3"
        >
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `nav-link ${isActive || location.pathname.startsWith("/pulls") || location.pathname.startsWith("/runs") ? "nav-active" : ""}`
            }
          >
            <Blocks className="size-4" />
            Repositories
          </NavLink>
          {session.data.role === "admin" && (
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `nav-link ${isActive ? "nav-active" : ""}`
              }
            >
              <Settings className="size-4" />
              Settings
            </NavLink>
          )}
        </nav>
        <div className="hidden px-6 pt-9 text-xs leading-relaxed text-white/50 lg:block">
          Every finding.
          <br />A traceable resolution.
        </div>
        <div className="flex items-center gap-2 border-t border-white/10 px-4 py-3 lg:mt-auto lg:flex-wrap lg:px-5 lg:py-5">
          <span className="mr-auto truncate text-xs">
            @{session.data.login}
          </span>
          <NativeSelect
            className="order-3 mt-3 hidden w-full border-white/20 bg-[var(--rail)] text-xs lg:block"
            aria-label="Appearance"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          >
            <option value="system">System appearance</option>
            <option value="light">Light appearance</option>
            <option value="dark">Dark appearance</option>
          </NativeSelect>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="Sign out"
            onClick={logout}
            disabled={action.isPending}
          >
            <LogOut className="size-4" />
          </Button>
        </div>
      </aside>
      <main className="min-w-0 px-5 py-8 sm:px-9 lg:px-12 lg:py-12">
        <div className="mx-auto max-w-6xl">
          <ErrorNotice error={action.error} />
          <Routes>
            <Route
              path="/"
              element={<Repositories admin={session.data.role === "admin"} />}
            />
            <Route path="/pulls/:id" element={<PullDetail />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route
              path="/settings"
              element={
                session.data.role === "admin" ? (
                  <SettingsPage />
                ) : (
                  <Navigate to="/" replace />
                )
              }
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
                    Return to repositories
                  </NavLink>
                </Empty>
              }
            />
          </Routes>
        </div>
      </main>
    </div>
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
createRoot(document.getElementById("root")!).render(
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
