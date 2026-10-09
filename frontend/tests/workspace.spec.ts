import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import type { Run, Schema } from "../src/api";

const at = "2026-10-05T13:20:00Z";
const installation: Schema["InstallationOut"] = {
  id: 10, account: "acme", connected: true, active: true, status: "active",
  settings_url: "https://github.com/organizations/acme/settings/installations/10",
  repository_selection: "selected", can_connect: true, can_uninstall: true,
  unavailable_reason: null, job_id: null, operation_status: null, operation_error: null,
};
const repo = {
  id: 22,
  installation_id: 10,
  full_name: "acme/payments",
  private: true,
  active: true,
  enabled: true,
  connection_id: "connection",
  settings: {
    automatic: true,
    model: "",
    effort: "medium",
    language: "English",
    exclusions: "",
    background: "",
    timeout_seconds: 900,
    token_budget: 200000,
    max_files: 40,
    concurrency: 2,
    rules: null,
  },
  open_prs: 2,
  has_running_review: false,
};
const run = {
  id: "run",
  engine: "ocr",
  owner_id: null,
  actor_id: 7,
  progress: {},
  pr_id: "pr",
  trigger: "automatic",
  base_sha: "b".repeat(40),
  head_sha: "a".repeat(40),
  status: "partial",
  publication: "failed",
  created_at: at,
  started_at: at,
  finished_at: "2026-10-05T13:21:30Z",
  error: null,
  publication_error: "GitHub temporarily rate-limited this app.",
  check_enabled: true,
  check_pending: true,
  check_status: "in_progress",
  check_conclusion: null,
  check_url: "https://github.com/acme/payments/runs/42",
  check_error: "Enable Checks: Read and write on the GitHub App and approve the installation permission update.",
  config: {
    connection: {
      name: "Team reviews",
      model: "review-model",
      provider: "openai",
    },
  },
  report: {
    manifest: {
      coverage: {
        selected: [{ path: "src/retry.ts" }, { path: "src/config.ts" }],
        completed: [{ path: "src/retry.ts" }],
        failed: [],
        waived: [{ path: "src/config.ts", reason: "Token budget reached" }],
        reused: [],
      },
    },
  },
};
const pr = {
  id: "pr",
  repository_id: 22,
  number: 148,
  title: "Add retry handling for payment webhooks",
  author: "maria",
  url: "https://github.com/acme/payments/pull/148",
  state: "open",
  draft: false,
  fork: false,
  head_sha: run.head_sha,
  base_sha: run.base_sha,
  head_ref: "feat/webhook-retries",
  updated_at: at,
  latest_run: run,
  open_findings: 2,
};
const finding = {
  id: "finding",
  pr_id: "pr",
  status: "open",
  desired_status: null as string | null,
  sync_error: null,
  path: "src/webhooks/retry.ts",
  content:
    "The retry loop has no upper bound. A persistent failure keeps the worker busy and delays the remaining webhooks. Limit the attempts and move exhausted jobs to the failure queue.",
  severity: "high",
  category: "bug",
  start_line: 42,
  end_line: 46,
  existing_code:
    "while (!delivered) {\n  delivered = await sendWebhook(payload);\n}",
  suggestion:
    "for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {\n  if (await sendWebhook(payload)) break;\n}",
  thread_id: "thread",
  github_url: "https://github.com/acme/payments/pull/148#discussion",
  last_seen_run_id: "run",
  created_at: at,
  updated_at: at,
};
const connection = {
  id: "connection",
  name: "Team reviews",
  provider: "openai",
  protocol: "openai",
  model: "review-model",
  base_url: "https://api.openai.com/v1",
  options: {},
  has_key: true,
  header_names: [],
  last_test: "Connected. Model completed a tool-call round trip.",
  tested_at: at,
  kind: "api",
  owner_id: null,
  auth_status: "not_required",
  auth_message: null,
};

async function mock(
  page: Page,
  options: {
    configured?: boolean;
    role?: string;
    canWrite?: boolean;
    publicUrl?: string;
    personal?: boolean;
    claude?: boolean;
    run?: Partial<Run>;
    finding?: Partial<Schema["FindingOut"]>;
    verifications?: Schema["VerificationOut"][];
    background?: Schema["BackgroundJobOut"];
  } = {},
) {
  const current: Schema["FindingOut"] = { ...structuredClone(finding), sync_intent: null, ...options.finding };
  const requests: {
    path: string;
    method: string;
    body: unknown;
    csrf: string | undefined;
  }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method();
    const currentRun = { ...run, ...options.run };
    if (method !== "GET")
      requests.push({
        path,
        method,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrf-token"],
      });
    let data: unknown;
    if (path === "/setup/status")
      data = {
        configured: options.configured !== false,
        public_url: options.publicUrl ?? new URL(request.url()).origin,
      };
    else if (path === "/auth/me")
      data = {
        id: 7,
        login: "carlos",
        role: options.role ?? "admin",
        avatar_url: "",
        csrf: "csrf-from-session",
      };
    else if (path === "/repositories") data = [repo];
    else if (path === "/repositories/22/background" && options.background)
      data = { ...options.background, status: "queued", background: null, error: null };
    else if (path.startsWith("/repositories/22/background/") && options.background)
      data = options.background;
    else if (path === "/repositories/22/pulls")
      data = [
        { ...pr, latest_run: currentRun },
        {
          ...pr,
          id: "pr-other",
          number: 147,
          title: "Validate webhook signatures",
          author: "sam",
          latest_run: { ...run, status: "completed", publication: "published" },
          open_findings: 0,
        },
      ];
    else if (path === "/pulls/pr")
      data = {
        pull_request: pr,
        repository: repo,
        runs: [currentRun],
        findings: [
          current,
          {
            ...finding,
            id: "finding2",
            severity: "medium",
            category: "performance",
            path: "src/webhooks/dispatch.ts",
            start_line: 18,
            end_line: 18,
            content:
              "Sequential delivery blocks independent webhook destinations. A slow endpoint delays notifications for the other recipients.",
            last_seen_run_id: "previous",
          },
        ],
        can_write: options.canWrite !== false,
      };
    else if (path === "/findings/finding") {
      if (method === "PATCH")
        current.desired_status = request.postDataJSON().status;
      data =
        method === "PATCH"
          ? current
          : {
              finding: current,
              verifications: options.verifications ?? [],
              activities: [
                {
                  id: "event",
                  action: "detected",
                  actor: "Code Mortis",
                  source: "review",
                  reason: "",
                  created_at: at,
                },
              ],
            };
    } else if (path === "/findings/finding/sync") {
      current.sync_error = null;
      data = { job_id: "sync-job" };
    } else if (path === "/runs/run")
      data = {
        run: currentRun,
        pull_request: pr,
        repository: repo,
        occurrences: currentRun.status === "skipped" ? [] : [
          { finding_id: "finding", snapshot: finding, inline_line: 46 },
        ],
        can_write: options.canWrite !== false,
      };
    else if (path === "/connections/models" || /^\/connections\/[^/]+\/models$/.test(path))
      data = {
        source: "provider", status: "ready", message: null,
        models: [{ id: "review-model", name: "Review model" }, { id: "next-model", name: "Next model" }],
      };
    else if (path === "/connections/connection" && method === "PUT")
      data = { ...connection, ...request.postDataJSON() };
    else if (path === "/connections/personal/auth")
      data = {
        status: "disconnected",
        attempt: options.claude ? null : {
          status: "pending",
          verificationUrl: "https://auth.openai.com/codex/device",
          userCode: "ABCD-1234",
        },
      };
    else if (path === "/connections/personal/console-ticket") data = { ticket: "single-use-test-ticket" };
    else if (path === "/connections/personal/grants") data = [];
    else if (path === "/connections" && options.personal)
      data = [
        {
          ...connection,
          id: "personal",
          name: options.claude ? "My Claude Code" : "My ChatGPT",
          kind: options.claude ? "claude_code" : "chatgpt",
          owner_id: 7,
          auth_status: "disconnected",
          protocol: "native",
          has_key: false,
          last_test: null,
          tested_at: null,
        },
      ];
    else if (path === "/connections")
      data =
        method === "GET"
          ? [connection]
          : { ...connection, ...request.postDataJSON() };
    else if (path === "/providers")
      data = [
        {
          name: "openai",
          displayName: "OpenAI API",
          protocol: "openai",
          baseUrl: "https://api.openai.com/v1",
        },
      ];
    else if (path === "/settings")
      data = {
        github_app: {
          id: "123",
          slug: "code-mortis-acme",
          install_url:
            "https://github.com/apps/code-mortis-acme/installations/new",
        },
        installations: [{ id: 10, account: "acme", active: true }],
        members: [{ id: 7, login: "carlos", role: "admin" }],
        failed_jobs: [],
        public_url: "https://reviews.example.com",
      };
    else if (path === "/installations/available")
      data = [{ id: 10, account: "acme" }];
    else if (path === "/installations")
      data = [installation];
    else if (method !== "GET") data = { ok: true, job_id: "job" };
    else
      return route.fulfill({
        status: 404,
        json: { detail: "Unexpected test request: " + path },
      });
    await route.fulfill({ json: data });
  });
  return requests;
}

async function openNavigation(page: Page) {
  const toggle = page.getByRole("button", { name: "Open navigation", exact: true });
  if (page.viewportSize()!.width < 1024) await toggle.click();
}

async function closeNavigation(page: Page) {
  const close = page.getByRole("button", { name: "Close navigation", exact: true });
  if (await close.isVisible()) await close.click();
}

async function breadcrumbAncestor(page: Page, name: string) {
  if (page.viewportSize()!.width < 640) {
    await page.getByRole("button", { name: "Show breadcrumb ancestors" }).click();
    return page.getByRole("menuitem", { name, exact: true });
  }
  return page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name, exact: true, disabled: false });
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  for (const dialog of await page.getByRole("dialog").all()) {
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
}

test("sidebar repositories navigate, show activity, and collapse accessibly", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [
    { ...repo, has_running_review: true },
    { ...repo, id: 23, full_name: "other/payments", enabled: false },
    { ...repo, id: 24, full_name: "legacy-repository" },
  ] }));
  await page.route("**/api/v1/repositories/23/pulls", (route) => route.fulfill({ json: [] }));
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: repo.full_name, exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Repository", exact: true })).toHaveCount(0);
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link", { name: "legacy-repository", exact: true }).locator(".font-medium")).toHaveText("legacy-repository");
  const running = nav.getByRole("link", { name: "acme/payments, review running", exact: true });
  await expect(running).toHaveAttribute("aria-current", "page");
  await expect(running).not.toContainText("Running");
  await expect(running.locator(".motion-safe\\:animate-spin")).toBeVisible();
  await expect(running.locator(".animate-spin, .motion-safe\\:animate-spin")).toHaveCSS("animation-name", "none");
  await nav.getByRole("link", { name: "other/payments", exact: true }).click();
  await expect(page).toHaveURL(/\/repositories\/23$/);
  await expect(page.getByRole("heading", { name: "other/payments", exact: true })).toBeVisible();
  if (info.project.name === "mobile") {
    await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  }
  await openNavigation(page);
  await expect(nav.getByRole("link", { name: "other/payments", exact: true })).toHaveAttribute("aria-current", "page");
  for (const theme of ["light", "dark"]) {
    await closeNavigation(page);
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await openNavigation(page);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/sidebar-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
  }
  if (info.project.name === "desktop") {
    await page.setViewportSize({ width: 1400, height: 938 });
    await noOverflow(page);
    await page.screenshot({ path: "../.impeccable/review/sidebar-1400.png", fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: "Hide sidebar" }).click();
    await expect(page.locator('[data-slot="sidebar-container"]')).toHaveAttribute("inert", "");
    await expect(page.locator('[data-slot="sidebar-container"]')).not.toBeInViewport();
    await page.getByRole("button", { name: "Show sidebar" }).press("Enter");
    await expect(nav).toBeInViewport();
    await nav.getByRole("link", { name: "other/payments", exact: true }).focus();
    await page.keyboard.press("Control+b");
    await expect(page.getByRole("button", { name: "Show sidebar" })).toBeFocused();
    await expect(page.locator('[data-slot="sidebar-container"]')).toHaveAttribute("inert", "");
    await page.keyboard.press("Enter");
    await expect(nav).toBeInViewport();
  } else {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  }
  for (const path of ["/pulls/pr", "/runs/run"]) {
    await page.goto(path);
    await openNavigation(page);
    await expect(nav.getByRole("link", { name: /^acme\/payments/ })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("link", { name: "Inbox", exact: true })).not.toHaveAttribute("aria-current", "page");
  }
});

test("repository paths preserve legacy settings links and never fall back from an unknown id", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "other/payments" }] }));
  await page.route("**/api/v1/repositories/23/pulls", (route) => route.fulfill({ json: [] }));
  await page.goto("/repositories?repo=23&configure=limits#settings");
  await expect(page).toHaveURL(/\/repositories\/23\?configure=limits#settings$/);
  await expect(page.getByRole("dialog", { name: "Repository configuration" })).toContainText("other/payments");
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog", { name: "Repository configuration" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/repositories\/23$/);
  await page.goto("/repositories/999");
  await expect(page.getByRole("heading", { name: "Repository unavailable" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configure", exact: true })).toHaveCount(0);
  await openNavigation(page);
  await expect(page.getByRole("navigation", { name: "Main navigation" }).locator('[aria-current="page"]')).toHaveCount(0);
  await closeNavigation(page);
  const documentRequests: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest()) documentRequests.push(request.url());
  });
  await page.getByRole("link", { name: "View available repositories", exact: true }).click();
  await expect(page).toHaveURL(/\/repositories\/22$/);
  expect(documentRequests).toEqual([]);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [] }));
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: "Connect your first repository", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Connect repository", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configure", exact: true })).toHaveCount(0);
});

test("sidebar account shows the GitHub avatar and signs out through its keyboard menu", async ({ page }, info) => {
  await mock(page);
  let signedOut = false;
  let completeLogout!: () => void;
  const pendingLogout = new Promise<void>((resolve) => { completeLogout = resolve; });
  const avatar = `data:image/svg+xml,${encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" fill="#245c47"/><text x="16" y="22" text-anchor="middle" font-family="sans-serif" font-size="20" fill="white">C</text></svg>')}`;
  await page.route("**/api/v1/auth/me", (route) => route.fulfill(signedOut
    ? { status: 401, json: { detail: "Sign in required" } }
    : { json: { id: 7, login: "carlos", role: "admin", avatar_url: avatar, csrf: "csrf-from-session" } }));
  await page.route("**/api/v1/auth/logout", async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-csrf-token"]).toBe("csrf-from-session");
    await pendingLogout;
    signedOut = true;
    await route.fulfill({ json: {} });
  });
  await page.goto("/repositories/22");
  await openNavigation(page);
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await page.goto("/repositories/22");
  await openNavigation(page);
  const account = page.getByRole("button", { name: "Account menu for @carlos", exact: true });
  await expect(account.locator('[data-slot="avatar-image"]')).toHaveAttribute("src", avatar);
  await expect(account).toContainText("Administrator");
  await account.focus();
  await page.keyboard.press("Enter");
  const settings = page.getByRole("menuitem", { name: "Settings", exact: true });
  await expect(settings).toBeFocused();
  await expect(settings).toHaveAttribute("href", "/settings");
  await settings.press("Enter");
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
  await openNavigation(page);
  await account.press("Enter");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(account).toBeFocused();
  await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toHaveCount(0);
  for (const theme of ["light", "dark"]) {
    if (info.project.name === "mobile") {
      await page.getByRole("button", { name: "Close navigation", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: /Switch to (light|dark) mode/ })).toBeVisible();
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    if (theme === "dark") await expect(page.locator("html")).toHaveClass("dark");
    else await expect(page.locator("html")).not.toHaveClass("dark");
    await openNavigation(page);
    await account.click();
    await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toBeInViewport();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/account-${info.project.name}-${theme}.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toHaveCount(0);
  }
  await account.press("Enter");
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).press("Enter");
  await expect(account).toBeDisabled();
  await expect(account).toContainText("Signing out…");
  completeLogout();
  await expect(page.getByRole("link", { name: "Continue with GitHub", exact: true })).toBeVisible();
});

test("sidebar account keeps an initials fallback for missing or failed avatars and long names", async ({ page }) => {
  await mock(page, { role: "member" });
  await page.goto("/repositories/22");
  await openNavigation(page);
  await expect(page.getByRole("button", { name: "Account menu for @carlos", exact: true }).locator('[data-slot="avatar-fallback"]')).toHaveText("CA");
  const login = "account-with-a-long-github-username";
  let avatarFailed = false;
  await page.route("**/missing-avatar.png", (route) => { avatarFailed = true; return route.fulfill({ status: 404 }); });
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { id: 7, login, role: "member", avatar_url: "/missing-avatar.png", csrf: "csrf-from-session" } }));
  await page.reload();
  await openNavigation(page);
  await expect.poll(() => avatarFailed).toBe(true);
  const account = page.getByRole("button", { name: `Account menu for @${login}`, exact: true });
  await expect(account).toHaveAttribute("title", `@${login}`);
  await expect(account).toContainText("Member");
  await expect(account.locator('[data-slot="avatar-fallback"]')).toHaveText("AC");
  await expect(account.locator('[data-slot="avatar-image"]')).toHaveCount(0);
  await noOverflow(page);
});

test("sidebar activity refreshes and does not present failed polling as current", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let running = true;
  let failure = 0;
  await page.route("**/api/v1/repositories", (route) => route.fulfill(failure
    ? { status: failure, json: { detail: "Repositories unavailable" } }
    : { json: [{ ...repo, has_running_review: running }] }));
  await page.goto("/settings");
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  const activity = nav.getByRole("link", { name: "acme/payments, review running", exact: true }).locator(".motion-safe\\:animate-spin");
  await expect(activity).toBeVisible();
  running = false;
  await page.clock.fastForward(6000);
  await expect(activity).toHaveCount(0);
  running = true;
  await page.clock.fastForward(6000);
  await expect(activity).toBeVisible();
  failure = 503;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository activity unavailable.");
  await expect(nav.getByRole("link", { name: repo.full_name, exact: true })).toBeVisible();
  await expect(activity).toHaveCount(0);
  failure = 0;
  await nav.getByRole("button", { name: "Retry repositories" }).click();
  await expect(activity).toBeVisible();
  failure = 403;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository access denied.");
  await expect(nav.getByRole("button", { name: "Retry repositories" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: /^acme\/payments/ })).toHaveCount(0);
  failure = 401;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository access denied.");
  await expect(nav.getByRole("button", { name: "Retry repositories" })).toHaveCount(0);
});

test("sidebar handles long repository lists and empty access", async ({ page }, info) => {
  await mock(page, { role: "member" });
  const name = "organization-with-a-long-name/repository-with-a-very-long-name";
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: Array.from({ length: 40 }, (_, i) => ({ ...repo, id: i + 22, full_name: `${name}-${i}`, has_running_review: i === 0 })) }));
  await page.route("**/api/v1/repositories/*/pulls", (route) => route.fulfill({ json: [] }));
  await page.goto("/settings");
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link")).toHaveCount(41);
  await expect(page.getByRole("button", { name: "Account menu for @carlos", exact: true })).toBeInViewport();
  const last = nav.getByRole("link", { name: `${name}-39`, exact: true });
  await last.scrollIntoViewIfNeeded();
  await last.focus();
  await expect(last).toBeInViewport();
  await expect(last).toHaveAttribute("title", `${name}-39`);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await noOverflow(page);
  await expect(page.getByRole("link", { name: "Manage repositories", exact: true })).toHaveCount(0);
  await last.click();
  await expect(page.getByRole("heading", { name: `${name}-39`, exact: true })).toBeVisible();
  await noOverflow(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [] }));
  await page.reload();
  await openNavigation(page);
  await expect(nav).toContainText("No repositories available.");
  if (info.project.name === "desktop") {
    await page.setViewportSize({ width: 1023, height: 800 });
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
    await openNavigation(page);
    await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toBeVisible();
    await noOverflow(page);
    await closeNavigation(page);
    await page.setViewportSize({ width: 1024, height: 800 });
    await expect(page.getByRole("button", { name: "Hide sidebar" })).toBeVisible();
    await expect(nav).toBeVisible();
  }
});

test("sidebar shortcut leaves editing controls alone", async ({ page }, info) => {
  await mock(page);
  await page.goto("/");
  const toggle = page.locator('[data-slot="sidebar-trigger"]');
  await expect(toggle).toBeVisible();
  await page.evaluate(() => {
    const fields = document.createElement("div");
    fields.id = "shortcut-fields";
    fields.innerHTML = '<input /><textarea></textarea><select><option>Option</option></select><div contenteditable=""><span tabindex="0">Editable text</span></div>';
    document.body.append(fields);
  });
  for (const selector of ["input", "textarea", "select", "[contenteditable] span"]) {
    const field = page.locator(`#shortcut-fields ${selector}`);
    for (const modifier of ["Control", "Meta"]) {
      await field.press(`${modifier}+b`);
      await expect(field).toBeFocused();
      await expect(toggle).toHaveAttribute("aria-expanded", info.project.name === "mobile" ? "false" : "true");
    }
  }
  await toggle.press("Control+b");
  await expect(toggle).toHaveAttribute("aria-expanded", info.project.name === "mobile" ? "true" : "false");
  expect((await page.context().cookies()).some((cookie) => cookie.name === "sidebar_state")).toBe(false);
});

test("sidebar and breadcrumbs agree on trailing-slash detail routes", async ({ page }) => {
  await mock(page);
  for (const [path, label] of [["/pulls/pr/", "Pull request #148"], ["/runs/run/", "Review run"]]) {
    await page.goto(path);
    const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText(label);
    await expect(await breadcrumbAncestor(page, repo.full_name)).toHaveAttribute("href", `/repositories/${repo.id}`);
    await page.keyboard.press("Escape");
    await openNavigation(page);
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: repo.full_name, exact: true })).toHaveAttribute("aria-current", "page");
  }
});

test("repository navigation, filtering, and review workspace", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mock(page);
  await page.goto("/repositories");
  await expect(
    page.getByRole("heading", { name: repo.full_name, exact: true }),
  ).toBeVisible();
  await page.getByLabel("Search pull requests").fill("148");
  await expect(page.getByText("Validate webhook signatures")).toHaveCount(0);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await expect(page.getByRole("heading", { name: pr.title })).toBeVisible();
  await expect(page.getByText("Not observed in latest review")).toBeVisible();
  await noOverflow(page);
  await page.evaluate(() => document.fonts.ready);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({
    path: `../.impeccable/review/review-detail-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "Run history" }).click();
  await expect(
    page.getByRole("link", { name: /automatic.*aaaaaaa/ }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

for (const entry of ["history", "feedback", "start"] as const) {
  test(`inbox context survives run ${entry} navigation and return`, async ({ page }) => {
    await mock(page);
    await page.route("**/api/v1/pulls?**", (route) => route.fulfill({ json: {
      groups: [{ group: "active", total: 1, page: 1, has_more: false, items: [{
        ...pr, repository_name: repo.full_name, created_at: at, review_status: run.status,
        has_running_review: false, run_id: run.id, publication: run.publication, publication_error: run.publication_error,
      }] }],
      total: 1, registered_total: 1, repository_count: 1, repositories: [repo],
      authors: [pr.author], state_counts: { open: 1 }, page_size: 10,
    } }));
    await page.route("**/api/v1/pulls/pr/runs", (route) => route.fulfill({ json: run }));
    const inbox = "/?q=retry&repo=22&ordering=title";
    await page.goto(inbox);
    await page.getByRole("link", { name: /Add retry handling/ }).click();
    if (entry === "history") {
      await page.getByRole("tab", { name: "Run history" }).click();
      await page.getByRole("link", { name: /automatic.*aaaaaaa/ }).click();
    } else if (entry === "feedback") {
      await page.getByRole("link", { name: "View run details", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "Run review", exact: true }).click();
    }
    await expect(page).toHaveURL(/\/runs\/run$/);
    await openNavigation(page);
    const inboxNav = page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Inbox", exact: true });
    await expect(inboxNav).toHaveAttribute("href", inbox);
    await expect(inboxNav).not.toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "acme/payments", exact: true })).toHaveAttribute("aria-current", "page");
    await closeNavigation(page);
    if (entry === "feedback") {
      await page.getByRole("link", { name: "Open pull request to run a new review", exact: true }).click();
    } else {
      await (await breadcrumbAncestor(page, "Pull request #148")).click();
    }
    await expect(page).toHaveURL(/\/pulls\/pr$/);
    await openNavigation(page);
    await expect(inboxNav).not.toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "acme/payments", exact: true })).toHaveAttribute("aria-current", "page");
    await closeNavigation(page);
    await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Inbox", exact: true, disabled: false }).click();
    await expect(page).toHaveURL(inbox);
    await expect(page.getByRole("textbox", { name: "Search pull requests", exact: true })).toHaveValue("retry");
  });
}

test("resolution waits for GitHub and preserves the confirmed status", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/pulls/pr");
  await page
    .getByRole("button", { name: /retry loop has no upper bound/ })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/finding-${info.project.name}.png`,
    fullPage: false,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Resolve finding", exact: true })
    .click();
  await expect(
    page.getByText("Waiting for GitHub to confirm: resolved."),
  ).toBeVisible();
  expect(requests.at(-1)).toMatchObject({
    path: "/findings/finding",
    method: "PATCH",
    body: { status: "resolved" },
    csrf: "csrf-from-session",
  });
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toBeDisabled();
});

test("finding code shows a unified diff and safe Markdown highlighting in both themes", async ({ page }, info) => {
  const source = 'def deliver():\n    return "<img src=x onerror=alert(1)>"\n';
  await mock(page, { finding: { content: `The retry loop has no upper bound. Keep \`MAX_ATTEMPTS\` bounded.\n\n\`\`\`python\n${source}\`\`\`` } });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const panel = page.getByRole("dialog");
  const patch = panel.getByRole("region", { name: "Suggested change diff" });
  await expect(patch).toContainText("--- original\n+++ suggested\n@@ -1,3 +1,3 @@");
  await expect(patch.locator(".hljs-deletion").first()).toHaveText("-while (!delivered) {");
  await expect(patch.locator(".hljs-addition").first()).toContainText("+for (let attempt = 0;");
  expect(await patch.textContent()).toContain("-  delivered = await sendWebhook(payload);\n");
  await patch.focus();
  await expect(patch).toBeFocused();
  const markdown = panel.locator(".markdown pre");
  expect(await markdown.textContent()).toBe(source);
  await expect(markdown.locator(".hljs-keyword").first()).toHaveText("def");
  await expect(markdown.locator("img, script, pre")).toHaveCount(0);
  await expect(panel.locator(".markdown p > code")).toHaveText("MAX_ATTEMPTS");
  await mkdir("../.impeccable/review", { recursive: true });
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(page.locator("html")).toHaveClass(colorScheme === "dark" ? /dark/ : /^(?!.*dark)/);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/code-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
  }
});

for (const example of [
  { name: "suggestion only", original: "", suggested: "const limit = 3;\n", path: "retry.tsx", language: "typescript", count: 1 },
  { name: "identical", original: "return True\n", suggested: "return True\n", path: "retry.py", language: "python", count: 1 },
  { name: "no suggestion", original: "FROM alpine\n", suggested: "", path: "Dockerfile", language: "dockerfile", count: 1 },
  { name: "unknown language", original: "", suggested: '<script>alert(1)</script>\n\n' + "long line ".repeat(80), path: "file.unknown", language: null, count: 1 },
  { name: "no snippets", original: "", suggested: "", path: "retry.ts", language: null, count: 0 },
  { name: "large comparison", original: "a\n".repeat(16000), suggested: "b\n".repeat(16000), path: "file.txt", language: null, count: 2 },
]) {
  test(`finding code fallback: ${example.name}`, async ({ page }) => {
    await mock(page, { finding: { existing_code: example.original, suggestion: example.suggested, path: example.path } });
    await page.goto("/pulls/pr");
    await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
    const panel = page.getByRole("dialog");
    await expect(panel.getByRole("region", { name: "Suggested change diff" })).toHaveCount(0);
    await expect(panel.locator("pre")).toHaveCount(example.count);
    if (example.count) {
      const code = panel.locator("pre").last();
      expect(await code.textContent()).toBe(example.suggested || example.original);
      await expect(code.locator(".hljs")).toHaveCount(example.language ? 1 : 0);
      if (example.language) await expect(code.locator("code")).toHaveClass(`hljs language-${example.language}`);
      await expect(code.locator("script, img")).toHaveCount(0);
    }
    await noOverflow(page);
  });
}

test("partial review and publication retry remain distinct", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await expect(page.locator("main header")).toContainText("· 90s");
  await expect(
    page.getByRole("region", { name: "Review outcome" }),
  ).toContainText("Coverage is incomplete.");
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].path).toBe("/runs/run/publish");
  await expect(page.getByRole("link", { name: "View check on GitHub" })).toHaveAttribute("href", run.check_url);
  await expect(page.getByText(run.check_error)).toBeVisible();
  await expect(page.getByText("Check update pending. Review results are saved separately.")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-${info.project.name}.png`, fullPage: true });
  await page.getByText("Configuration used", { exact: true }).click();
  const config = page.locator("pre .language-json").last();
  await expect(config.locator(".hljs-attr").first()).toHaveText('"connection"');
  expect(await config.textContent()).toBe(JSON.stringify(run.config, null, 2));
  await page.getByText("Inspect coverage report", { exact: true }).click();
  await expect(page.locator("pre .language-json .hljs-attr").first()).toHaveText('"manifest"');
});

test("GitHub check handles pending, conclusion-only, and empty states", async ({ page }, info) => {
  const checkState: Partial<Run> = {
    check_status: null,
    check_conclusion: null,
    check_url: null,
    check_error: null,
  };
  await mock(page, { run: checkState });
  await page.goto("/runs/run");
  const check = page.getByText("GitHub check", { exact: true }).locator("..");
  await expect(check.getByRole("status")).toHaveText("Updating the check on GitHub…");
  await expect(check.locator(":scope > :empty")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-pending-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = "success";
  checkState.check_pending = false;
  await page.reload();
  await expect(check.getByText("success", { exact: true })).toBeVisible();
  await expect(check.getByRole("status")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-conclusion-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = null;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await expect(page.getByText("GitHub check", { exact: true })).toHaveCount(0);
  await expect(page.getByText("GitHub comments", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-empty-${info.project.name}.png`, fullPage: true });
});

test("read-only members cannot change findings or instance settings", async ({
  page,
}) => {
  await mock(page, { role: "member", canWrite: false });
  await page.goto("/pulls/pr");
  await expect(page.getByRole("heading", { name: pr.title })).toBeVisible();
  await openNavigation(page);
  await expect(
    page.getByRole("link", { name: "Settings", exact: true }),
  ).toHaveCount(1);
  await closeNavigation(page);
  await expect(
    page.getByRole("button", { name: "Run review", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: /retry loop has no upper bound/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toHaveCount(0);
});

test("connection editing never preloads saved credentials", async ({
  page,
}, info) => {
  await mock(page);
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/settings-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Replace API key (optional)")).toHaveValue("");
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue("review-model");
});

test("saved model selection refreshes without replacing custom IDs", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator("option", { hasText: "Next model" })).toHaveCount(1);
  await model.selectOption("next-model");
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  expect(requests.find((request) => request.path === "/connections/models")?.body).toEqual({
    kind: "api", provider: "openai", protocol: "openai", base_url: "https://api.openai.com/v1",
    options: {}, connection_id: "connection",
  });
  await expect(model).toHaveValue("next-model");
  await model.selectOption({ label: "Enter a custom model ID" });
  await page.getByLabel("Custom model ID").fill("private-model");
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  await expect(page.getByLabel("Custom model ID")).toHaveValue("private-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/model-picker-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections/connection")?.body).toMatchObject({ model: "private-model" });
});

test("API drafts load models before saving and support keyboard selection", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection name").fill("New connection");
  await page.getByLabel("API key", { exact: true }).fill("draft-key");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator("option", { hasText: "Review model" })).toHaveCount(1);
  expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
  expect(requests.find((request) => request.path === "/connections/models")?.body).toMatchObject({ api_key: "draft-key" });
  await model.focus();
  await model.press("r");
  await model.press("Tab");
  await expect(model).toHaveValue("review-model");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections")?.body).toMatchObject({ model: "review-model", api_key: "draft-key" });
});

test("changed draft discards late models and keeps manual entry on errors", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/models", async (route) => {
    const old = route.request().postDataJSON().base_url === "https://api.openai.com/v1";
    if (old) await held;
    await route.fulfill({ json: { source: "provider", status: "ready", models: [{ id: old ? "old-model" : "new-model", name: old ? "Old model" : "New model" }] } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("API key", { exact: true }).fill("key");
  const first = page.waitForRequest("**/api/v1/connections/models");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  await first;
  await page.getByLabel("API base URL").fill("https://new.example/v1");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator('option[value="new-model"]')).toHaveCount(1);
  const late = page.waitForResponse((response) => response.url().endsWith("/connections/models") && response.request().postDataJSON().base_url === "https://api.openai.com/v1");
  release();
  await late;
  await expect(model.locator('option[value="old-model"]')).toHaveCount(0);
  await expect(model.locator('option[value="new-model"]')).toHaveCount(1);
  await model.selectOption("new-model");
  await page.route("**/api/v1/connections/models", (route) => route.fulfill({ json: { source: "provider", status: "unsupported", message: "This endpoint does not support model discovery. Enter a custom model ID.", models: [] } }));
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByText("This endpoint does not support model discovery. Enter a custom model ID.")).toBeVisible();
  await expect(model).toHaveValue("new-model");
  await model.selectOption({ label: "Enter a custom model ID" });
  await page.getByLabel("Custom model ID").fill("fallback");
  await expect(page.getByLabel("Custom model ID")).toHaveValue("fallback");
});

test("repository model choices follow its selected connection", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection", model: "second-model" }] }));
  await page.route("**/api/v1/connections/second/models?repository_id=22", (route) => route.fulfill({ json: { source: "provider", status: "ready", models: [{ id: "second-model", name: "Second model" }] } }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const model = page.getByLabel("Model override (optional)");
  await expect(model.locator("option:checked")).toHaveText("review-model");
  await expect(model.locator('option[value="next-model"]')).toHaveCount(1);
  await model.selectOption("next-model");
  await page.getByLabel("AI connection", { exact: true }).selectOption("second");
  await expect(model).toHaveValue("");
  await expect(model.locator("option:checked")).toHaveText("second-model");
  await expect(model.locator('option[value="second-model"]')).toHaveCount(1);
  await expect(model.locator('option[value="next-model"]')).toHaveCount(0);
  await model.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/connection-default-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    connection_id: "second", settings: { model: "" },
  });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByLabel("AI connection", { exact: true }).selectOption("");
  await expect(model).toBeDisabled();
});

test("project background generates an editable draft with unsaved selections and Undo", async ({ page }, info) => {
  const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "running", background: null, error: null };
  const requests = await mock(page, { background });
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection" }] }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const field = page.getByLabel("Project background", { exact: true });
  await field.fill("Existing context");
  await page.getByLabel("AI connection", { exact: true }).selectOption("second");
  await page.getByLabel("Model override (optional)").selectOption("next-model");
  await page.getByRole("button", { name: "Generate background", exact: true }).click();
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  await expect(field).toHaveValue("Existing context");
  await expect(page.getByRole("status", { name: "Background generation status" })).toContainText("Changing the background, connection, model, or limits will discard this result.");
  await expect(field).toHaveAttribute("maxlength", "20000");
  expect(requests).toEqual([{ path: "/repositories/22/background", method: "POST", body: { connection_id: "second", model: "next-model", limits: { token_budget: 200000, timeout_seconds: 900 } }, csrf: "csrf-from-session" }]);
  background.background = "A payment service built with TypeScript. Workers deliver webhooks and handle retries.";
  background.status = "done";
  await expect(field).toHaveValue(background.background);
  await expect(page.getByRole("status", { name: "Background generation status" }).filter({ hasText: "Background generated." })).toBeVisible();
  await noOverflow(page);
  await field.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/project-background-${info.project.name}.png`, fullPage: true });
  expect(requests).toHaveLength(1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(field).toHaveValue("Existing context");
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Background generation status" })).toContainText("Previous background restored.");
  background.id = "second-background-job";
  await page.getByRole("button", { name: "Generate background", exact: true }).click();
  await expect(field).toHaveValue(background.background);
  await field.fill("Edited generated context");
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/repositories/22", method: "PUT", body: {
    connection_id: "second", settings: { model: "next-model", background: "Edited generated context" },
  } });
});

for (const outcome of ["done", "failed"] as const) {
  for (const change of ["background", "model", "connection", "token budget", "timeout"]) {
    test(`project background discards a late ${outcome} result after changing ${change}`, async ({ page }) => {
      const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "running", background: null, error: null };
      const requests = await mock(page, { background });
      await page.goto("/repositories");
      await page.getByRole("button", { name: "Configure", exact: true }).click();
      const field = page.getByLabel("Project background", { exact: true });
      await field.fill("Original context");
      await page.getByRole("button", { name: "Generate background", exact: true }).click();
      if (change === "background") await field.fill("Keep this edit");
      if (change === "model") await page.getByLabel("Model override (optional)").selectOption("next-model");
      if (change === "connection") await page.getByLabel("AI connection", { exact: true }).selectOption("");
      if (change === "token budget" || change === "timeout") {
        await page.getByText("Review limits and rules", { exact: true }).click();
        await page.getByLabel(change === "timeout" ? "Timeout (seconds)" : "Token budget", { exact: true })
          .fill(change === "timeout" ? "1200" : "450000");
      }
      background.background = "Stale generated context";
      background.error = "Failure from the old selections";
      background.status = outcome;
      await expect(page.getByRole("status", { name: "Background generation status" }).filter({ hasText: "Your selections or background changed." })).toBeVisible();
      await expect(field).toHaveValue(change === "background" ? "Keep this edit" : "Original context");
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(page.getByText(/Generation reference:/)).toHaveCount(0);
      expect(requests).toHaveLength(1);
      expect(requests[0].body).toEqual({ connection_id: "connection", model: "", limits: { token_budget: 200000, timeout_seconds: 900 } });
    });
  }
}

for (const outcome of ["done", "failed"] as const) {
  for (const change of ["background", "model", "connection", "limits"]) {
    test(`project background clears ${outcome} feedback after changing ${change}`, async ({ page }) => {
      const background: Schema["BackgroundJobOut"] = {
        id: "feedback-job", status: outcome, background: "A generated draft", error: "The provider denied model access.",
      };
      await mock(page, { background });
      await page.goto("/repositories");
      await page.getByRole("button", { name: "Configure", exact: true }).click();
      await page.getByRole("button", { name: "Generate background", exact: true }).click();
      if (outcome === "done") await expect(page.getByRole("status", { name: "Background generation status" })).toContainText("Background generated.");
      else {
        await expect(page.getByRole("alert")).toContainText(background.error!);
        await expect(page.getByText("Generation reference: feedback-job")).toBeVisible();
      }
      if (change === "background") await page.getByLabel("Project background", { exact: true }).fill("New manual text");
      if (change === "model") await page.getByLabel("Model override (optional)").selectOption("next-model");
      if (change === "connection") await page.getByLabel("AI connection", { exact: true }).selectOption("");
      if (change === "limits") {
        await page.getByText("Review limits and rules", { exact: true }).click();
        await page.getByLabel("Timeout (seconds)", { exact: true }).fill("1200");
      }
      await expect(page.getByRole("status", { name: "Background generation status" })).toBeEmpty();
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(page.getByText(/Generation reference:/)).toHaveCount(0);
    });
  }
}

for (const transient of ["network", "server"] as const) {
  test(`project background retries a transient ${transient} polling error without another generation`, async ({ page }) => {
    const background: Schema["BackgroundJobOut"] = { id: "retry-job", status: "done", background: "Recovered draft", error: null };
    const requests = await mock(page, { background });
    const polls: string[] = [];
    await page.route("**/api/v1/repositories/22/background/*", async (route) => {
      polls.push(new URL(route.request().url()).pathname);
      if (polls.length <= 2) {
        if (transient === "network") await route.abort("failed");
        else await route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
      } else await route.fulfill({ json: background });
    });
    await page.goto("/repositories");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    await page.getByRole("button", { name: "Generate background", exact: true }).click();
    await expect(page.getByLabel("Project background", { exact: true })).toHaveValue("Recovered draft");
    expect(polls).toEqual(Array(3).fill("/api/v1/repositories/22/background/retry-job"));
    expect(requests).toHaveLength(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByRole("status", { name: "Background generation status" })).toContainText("Background generated.");
  });
}

for (const phase of ["start", "poll"] as const) {
  test(`project background discards a late ${phase} error after an edit`, async ({ page }) => {
    const background: Schema["BackgroundJobOut"] = { id: "late-error-job", status: "running", background: null, error: null };
    await mock(page, { background });
    let release!: () => void;
    const response = new Promise<void>((resolve) => { release = resolve; });
    const path = `/api/v1/repositories/22/background${phase === "poll" ? "/late-error-job" : ""}`;
    await page.route(`**${path}`, async (route) => {
      await response;
      await route.fulfill({ status: 403, json: { detail: "Access denied for the old selections" } });
    });
    await page.goto("/repositories");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    const sent = page.waitForRequest((request) => new URL(request.url()).pathname === path);
    await page.getByRole("button", { name: "Generate background", exact: true }).click();
    await sent;
    const field = page.getByLabel("Project background", { exact: true });
    await field.fill("Preserve this edit");
    release();
    await expect(page.getByRole("status", { name: "Background generation status" })).toContainText("Your selections or background changed.");
    await expect(field).toHaveValue("Preserve this edit");
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByText(/Generation reference:/)).toHaveCount(0);
  });
}

test("project background preserves text on failure and explains missing selections", async ({ page }) => {
  const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "failed", background: null, error: "Reconnect your account before retrying." };
  await mock(page, { background });
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "no-model", name: "No model", model: "" }] }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const field = page.getByLabel("Project background", { exact: true });
  const generate = page.getByRole("button", { name: "Generate background", exact: true });
  await field.fill("Keep my context");
  await generate.click();
  await expect(page.getByRole("alert")).toContainText("Reconnect your account before retrying.");
  await expect(field).toHaveValue("Keep my context");
  await expect(generate).toBeEnabled();
  await page.getByLabel("AI connection", { exact: true }).selectOption("");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose an AI connection to generate background.")).toBeVisible();
  await page.getByLabel("AI connection", { exact: true }).selectOption("no-model");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose a model to generate background.")).toBeVisible();
  await page.getByLabel("Model override (optional)").selectOption("next-model");
  await expect(generate).toBeEnabled();
});

test("project background preserves text when starting or polling fails", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories/22/background", (route) => route.fulfill({ status: 422, json: { detail: "Authorize this repository first." } }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const field = page.getByLabel("Project background", { exact: true });
  await field.fill("Keep my context");
  await page.getByRole("button", { name: "Generate background", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Authorize this repository first.");
  await expect(field).toHaveValue("Keep my context");
  await page.route("**/api/v1/repositories/22/background", (route) => route.fulfill({ status: 202, json: { id: "job", status: "queued" } }));
  await page.route("**/api/v1/repositories/22/background/job", (route) => route.fulfill({ status: 403, json: { detail: "Repository access revoked." } }));
  await page.getByRole("button", { name: "Generate background", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access revoked.");
  await expect(field).toHaveValue("Keep my context");
});

for (const unlimited of [false, true]) {
  test(`project background uses unsaved ${unlimited ? "unlimited" : "custom"} review limits`, async ({ page }) => {
    const background: Schema["BackgroundJobOut"] = { id: "limits-job", status: "running", background: null, error: null };
    const requests = await mock(page, { background });
    await page.goto("/repositories");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    await page.getByText("Review limits and rules", { exact: true }).click();
    if (unlimited) {
      await page.getByLabel("Unlimited token budget", { exact: true }).check();
      await page.getByLabel("Unlimited timeout (seconds)", { exact: true }).check();
    } else {
      await page.getByLabel("Token budget", { exact: true }).fill("450000");
      await page.getByLabel("Timeout (seconds)", { exact: true }).fill("1200");
    }
    await expect(page.getByText(/Generation uses these review limits:/)).toContainText(
      unlimited ? "unlimited tokens, no overall timeout" : "450,000 tokens, 1,200-second timeout",
    );
    await page.getByRole("button", { name: "Generate background", exact: true }).click();
    await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({ method: "POST", body: { limits: {
      token_budget: unlimited ? null : 450000, timeout_seconds: unlimited ? null : 1200,
    } } });
  });
}

test("project background explains missing output and displays its generation reference", async ({ page }, info) => {
  const error = "The model finished without returning a usable background draft. Generate again or choose another model. Increasing the token budget will not fix a missing result. Your background was not changed.";
  const background: Schema["BackgroundJobOut"] = { id: "ab31f162-abb3-4ce5-bc85-e6af43be6ba6", status: "failed", background: null, error };
  const requests = await mock(page, { background });
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const field = page.getByLabel("Project background", { exact: true });
  await field.fill("Keep my existing project background.");
  await page.getByRole("button", { name: "Generate background", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(error);
  await expect(page.getByText(`Generation reference: ${background.id}`)).toBeVisible();
  await expect(field).toHaveValue("Keep my existing project background.");
  expect(requests).toHaveLength(1);
  await noOverflow(page);
  await page.getByRole("alert").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/project-background-error-${info.project.name}.png`, fullPage: true });
});

test("review limits save and reopen independent unlimited choices", async ({ page }, info) => {
  await mock(page);
  let current = structuredClone(repo) as Schema["RepositoryOut"];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/repositories/22", async (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    await route.fulfill({ json: current });
  });
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByText("Review limits and rules", { exact: true }).click();
  const time = page.getByLabel("Timeout (seconds)", { exact: true });
  const tokens = page.getByLabel("Token budget", { exact: true });
  const files = page.getByLabel("Maximum files", { exact: true });
  const unlimitedTime = page.getByRole("checkbox", { name: "Unlimited timeout (seconds)", exact: true });
  const unlimitedTokens = page.getByRole("checkbox", { name: "Unlimited token budget", exact: true });
  const unlimitedFiles = page.getByRole("checkbox", { name: "Unlimited maximum files", exact: true });
  await expect(time).toHaveValue("900");
  await expect(tokens).toHaveValue("200000");
  await expect(files).toHaveValue("40");
  for (const [field, unlimited, value] of [
    [time, unlimitedTime, "900"],
    [tokens, unlimitedTokens, "200000"],
    [files, unlimitedFiles, "40"],
  ] as const) {
    for (const invalid of ["", "1e309"]) {
      await field.fill(invalid);
      await expect(field).toHaveValue("");
      expect(await field.evaluate((element: HTMLInputElement) => element.validity.valid)).toBe(false);
      await page.getByRole("button", { name: "Save configuration", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      expect(current.settings).toMatchObject({ timeout_seconds: 900, token_budget: 200000, max_files: 40 });
      await unlimited.check();
      await unlimited.uncheck();
      await expect(field).toHaveValue(value);
    }
  }
  await time.clear();
  await time.pressSequentially("1200");
  await expect(time).toHaveValue("1200");
  await unlimitedTime.check();
  await expect(time).toBeDisabled();
  await expect(tokens).toBeEnabled();
  await expect(files).toBeEnabled();
  await unlimitedTime.uncheck();
  await expect(time).toHaveValue("1200");
  await files.fill("0");
  expect(await files.evaluate((element: HTMLInputElement) => element.validity.valid)).toBe(false);
  await unlimitedFiles.check();
  await unlimitedFiles.uncheck();
  await expect(files).toHaveValue("40");
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: 1200, token_budget: 200000, max_files: 40 });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByText("Review limits and rules", { exact: true }).click();
  await expect(time).toHaveValue("1200");
  await unlimitedTokens.check();
  await unlimitedFiles.check();
  await unlimitedTime.check();
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: null });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByText("Review limits and rules", { exact: true }).click();
  await expect(unlimitedTime).toBeChecked();
  await expect(unlimitedTokens).toBeChecked();
  await expect(unlimitedFiles).toBeChecked();
  await unlimitedFiles.uncheck();
  await expect(files).toHaveValue("40");
  await noOverflow(page);
  await time.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: 40 });
});

test("personal review limits retain their separate context budget", async ({ page }) => {
  await mock(page, { personal: true });
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByLabel("AI connection", { exact: true }).selectOption("personal");
  await page.getByText("Review limits and rules", { exact: true }).click();
  await expect(page.getByLabel("Token budget", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Native context budget (bytes)")).toHaveValue("800000");
  await page.getByRole("checkbox", { name: "Unlimited timeout (seconds)" }).check();
  await page.getByRole("checkbox", { name: "Unlimited maximum files" }).check();
  await expect(page.getByLabel("Native context budget (bytes)")).toBeEnabled();
  await noOverflow(page);
});

const limitedRun: Partial<Run> = {
  status: "skipped", publication: "not_applicable", publication_error: null,
  check_status: "completed", check_conclusion: "skipped", check_pending: false, check_error: null,
  config: { ...run.config, settings: { max_files: 40 } },
  report: {
    message: "41 reviewable files exceeds the maximum of 40.",
    manifest: { schema_version: "code-mortis.file-limit/v1", coverage: {
      selected: Array.from({ length: 41 }, (_, index) => ({ path: `src/file-${index}.py` })),
      completed: [], reused: [], failed: [],
      waived: Array.from({ length: 41 }, (_, index) => ({ path: `src/file-${index}.py`, reason: "41 reviewable files exceeds the maximum of 40." })),
    } },
  },
};

for (const [publication, label] of [
  ["pending", "Waiting to publish skip notice"],
  ["publishing", "Publishing skip notice"],
  ["published", "Skip notice published"],
  ["failed", "Skip notice publication failed"],
  ["partially_published", "Skip notice publication failed"],
] as const) {
  test(`file-limit skip notice ${publication} preserves the review outcome`, async ({ page }, info) => {
    const requests = await mock(page, { run: { ...limitedRun, publication } });
    await page.goto("/pulls/pr");
    await page.getByRole("tab", { name: /Run history/ }).click();
    await expect(page.getByRole("link", { name: new RegExp(`GitHub: ${label}`) })).toBeVisible();
    await page.getByRole("link", { name: "View run details", exact: true }).click();
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText("41 reviewable files exceed the limit of 40. No files were reviewed.");
    await expect(outcome).toContainText("post the command shown in the GitHub skip notice");
    await expect(outcome).not.toContainText("@codemortis");
    await expect(outcome).toContainText("Personal connections remain owner-only.");
    await expect(page.getByText(label, { exact: true })).toBeVisible();
    await expect(page.getByText("The review results were published on GitHub.", { exact: true })).toHaveCount(0);
    const retry = page.getByRole("button", { name: "Retry GitHub publication" });
    if (publication === "failed" || publication === "partially_published") {
      await expect(outcome).toContainText("send the saved file-limit notice without running AI analysis");
      await expect(outcome).not.toContainText("send saved results");
      await expect(page.getByText("Retry publication to send the saved file-limit notice without running AI analysis.", { exact: true })).toBeVisible();
      await expect(page.getByText("Some comments published", { exact: true })).toHaveCount(0);
      await retry.click();
      expect(requests).toEqual([{ path: "/runs/run/publish", method: "POST", body: null, csrf: "csrf-from-session" }]);
    } else {
      await expect(retry).toHaveCount(0);
      expect(requests).toEqual([]);
    }
    await noOverflow(page);
    if (publication === "published" || publication === "failed") {
      await page.screenshot({ path: `../.impeccable/review/file-limit-notice-${publication}-${info.project.name}.png`, fullPage: true });
    }
  });
}

for (const [name, coverage, settings] of [
  ["missing selected files", {}, { max_files: 40 }],
  ["missing file limit", { selected: [{ path: "source.py" }] }, {}],
  ["invalid file limit", { selected: [{ path: "source.py" }] }, { max_files: "40" }],
] as const) {
  test(`malformed file-limit report with ${name} uses consistent fallback copy`, async ({ page }) => {
    await mock(page, { run: {
      ...limitedRun, publication: "failed", config: { ...run.config, settings },
      report: { manifest: { schema_version: "code-mortis.file-limit/v1", coverage } },
    } });
    await page.goto("/runs/run");
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText("This review was skipped. No specific reason was saved. Publication failed.");
    await expect(outcome).toContainText("Inspect the coverage report and repository settings before starting a new review.");
    await expect(outcome).not.toContainText("skip notice");
    await expect(page.getByText("Publication failed", { exact: true })).toBeVisible();
    await expect(page.getByText("Skip notice publication failed", { exact: true })).toHaveCount(0);
    await noOverflow(page);
  });
}

test("oversized review explains its outcome and links to editable limits throughout the app", async ({ page }, info) => {
  const requests = await mock(page, { run: limitedRun });
  const reason = "41 reviewable files exceed the limit of 40. No files were reviewed.";
  await page.goto("/repositories");
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toContainText(reason);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText(reason);
  await page.getByRole("tab", { name: /Run history/ }).click();
  await expect(page.getByRole("link", { name: /GitHub: No comments published/ })).toContainText(reason);
  await page.getByRole("link", { name: "View run details", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: reason })).toBeVisible();
  await expect(page.getByText("No comments published", { exact: true })).toBeVisible();
  await expect(page.getByText("not applicable", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry GitHub publication" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/file-limit-skipped-${info.project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: "Adjust review settings" }).click();
  const settings = page.getByRole("dialog");
  await expect(settings).toBeVisible();
  await expect(settings.getByLabel("Maximum files", { exact: true })).toBeVisible();
  await expect(settings.getByRole("checkbox", { name: "Unlimited maximum files" })).toBeVisible();
  const limits = settings.locator("details").filter({ hasText: "Review limits and rules" });
  await limits.locator("summary").click();
  await expect(limits).not.toHaveAttribute("open");
  await settings.getByLabel("Project background", { exact: true }).fill("A settings change must preserve the collapsed disclosure.");
  await expect(limits).not.toHaveAttribute("open");
  expect(requests).toEqual([]);
  await noOverflow(page);
});

test("viewers receive an administrator next step without settings or retry actions", async ({ page }) => {
  await mock(page, { run: limitedRun, role: "member", canWrite: false });
  await page.goto("/runs/run");
  const outcome = page.getByRole("region", { name: "Review outcome" });
  await expect(outcome).toContainText("Ask your administrator to adjust the repository settings.");
  await expect(outcome.getByRole("link", { name: "Adjust review settings" })).toHaveCount(0);
  await expect(outcome.getByRole("link", { name: "View pull request", exact: true })).toHaveAttribute("href", "/pulls/pr");
  await expect(page.getByRole("button", { name: /Resume review|Retry GitHub publication/ })).toHaveCount(0);
});

const feedbackCases: { name: string; state: Partial<Run>; summary: string; nextStep: string; fileReason?: string }[] = [
  {
    name: "unsupported files",
    state: { status: "skipped", report: { manifest: { coverage: { selected: [] } }, excluded_files: [{ path: "README.md", exclude_reason: "unsupported_ext" }] } },
    summary: "The changed file types are not supported by the review engine.",
    nextStep: "These changes need a manual review.",
    fileReason: "File type is not supported by the review engine.",
  },
  {
    name: "empty scope",
    state: { status: "skipped", report: { manifest: { coverage: { selected: [] } }, excluded_files: [{ path: "src/vendor.ts", exclude_reason: "user_exclude" }] } },
    summary: "No changed files were eligible for review.",
    nextStep: "Check file exclusions and review rules.",
    fileReason: "Excluded by the review rules or repository settings.",
  },
  {
    name: "time limit",
    state: { status: "failed", failure_reason: "timeout", report: null },
    summary: "The review reached its time limit.",
    nextStep: "Increase Timeout, choose Unlimited, or reduce the review scope",
  },
  {
    name: "token budget",
    state: { report: { manifest: { coverage: { ...run.report.manifest.coverage, waived: [{ path: "src/config.ts", classification: "budget", reason: "Token budget reached" }] } } } },
    summary: "Coverage is incomplete. The token budget was reached.",
    nextStep: "Increase Token budget, choose Unlimited",
    fileReason: "Token budget reached",
  },
  {
    name: "native context limit",
    state: { report: { manifest: { coverage: { selected: [{ path: "source.py" }], failed: [{ path: "source.py" }] } }, errors: [{ path: "source.py", message: "Delegated context budget exceeded; reduce the supplied context." }] } },
    summary: "The native context budget was reached.",
    nextStep: "Increase Native context budget or reduce the review scope",
    fileReason: "Delegated context budget exceeded; reduce the supplied context.",
  },
  {
    name: "authentication",
    state: { status: "blocked", blocked_reason: "authentication", owner_id: 7 },
    summary: "The AI connection needs to sign in again.",
    nextStep: "The connection owner must reconnect the account, then resume this review.",
  },
  {
    name: "quota",
    state: { status: "blocked", blocked_reason: "quota", owner_id: 8 },
    summary: "The AI connection reached its usage limit.",
    nextStep: "The connection owner can resume this review when usage becomes available.",
  },
  {
    name: "busy connection",
    state: { status: "queued", blocked_reason: "busy" },
    summary: "The selected personal connection is reviewing another pull request.",
    nextStep: "This review will retry automatically when the connection is available.",
  },
  {
    name: "interrupted worker",
    state: { status: "failed", failure_reason: "worker_stopped", report: null },
    summary: "The review worker stopped before the run could finish.",
    nextStep: "Interrupted AI usage may already have been charged.",
  },
  {
    name: "cancelled review",
    state: { status: "cancelled" },
    summary: "This review was cancelled before it finished.",
    nextStep: "Start a new review from the pull request",
  },
  {
    name: "superseded review",
    state: { status: "superseded" },
    summary: "The pull request changed or closed",
    nextStep: "Open the pull request to inspect the latest commit",
  },
  {
    name: "older saved reason",
    state: { status: "skipped", report: { message: "A saved reason from an older run." } },
    summary: "A saved reason from an older run.",
    nextStep: "Inspect the coverage report and repository settings",
  },
  {
    name: "older partial run without coverage",
    state: { status: "partial", report: null },
    summary: "The review finished with incomplete coverage.",
    nextStep: "Inspect the error and file results in the run details",
  },
];

for (const example of feedbackCases) {
  test(`review feedback: ${example.name}`, async ({ page }) => {
    await mock(page, { run: { publication: "not_applicable", publication_error: null, ...example.state } });
    await page.goto("/runs/run");
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText(example.summary);
    await expect(outcome).toContainText(example.nextStep);
    if (example.fileReason) {
      const files = page.locator("details").filter({ has: page.getByText("Files not reviewed (1)", { exact: true }) });
      if (await files.getAttribute("open") === null) await files.locator("summary").click();
      await expect(files.getByText(example.fileReason, { exact: true })).toBeVisible();
    }
    if (example.state.status === "blocked") {
      await expect(page.getByRole("button", { name: "Resume review" })).toHaveCount(example.state.owner_id === 7 ? 1 : 0);
      await expect(outcome.getByRole("link", { name: "Manage AI connections" })).toHaveAttribute("href", "/settings?tab=connections");
    }
    await noOverflow(page);
  });
}

test("publication failure after a completed review offers a publication retry", async ({ page }) => {
  const requests = await mock(page, { run: { status: "completed" } });
  await page.goto("/pulls/pr");
  const outcome = page.getByRole("region", { name: "Review outcome" });
  await expect(outcome).toContainText("The selected review scope completed. Publication failed.");
  await expect(outcome).toContainText("Retry GitHub publication from the run details to send saved results without rerunning the review.");
  await outcome.getByRole("link", { name: "View run details" }).click();
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.map((request) => request.path)).toEqual(["/runs/run/publish"]);
});

test("completed reviews without comments need no attention in lists or previews", async ({ page }) => {
  await mock(page, { run: { status: "completed", publication: "not_applicable", publication_error: null } });
  await page.goto("/repositories");
  const row = page.getByRole("link", { name: /Add retry handling/ });
  await expect(row).not.toContainText("The selected review scope completed.");
  await row.click();
  await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Run history" }).click();
  const history = page.getByRole("link", { name: /GitHub: No comments published/ });
  await expect(history).not.toContainText("The selected review scope completed.");
  await history.click();
  await expect(page.getByText("No comments published", { exact: true })).toHaveCSS("text-transform", "none");
  await expect(page.locator('[data-slot="badge"]').filter({ hasText: /^completed$/ })).toHaveCSS("text-transform", "capitalize");
});

for (const status of ["partial", "skipped"]) {
  test(`file disclosure preserves the user's choice during ${status} run polling`, async ({ page }) => {
    await page.clock.install();
    let reads = 0;
    const currentRun: Partial<Run> = { status };
    await mock(page, { run: currentRun });
    await page.route("**/api/v1/runs/run", (route) => {
      reads += 1;
      currentRun.progress = { selected: 10, completed: reads };
      return route.fallback();
    });
    await page.goto("/runs/run");
    const files = page.locator("details").filter({ hasText: "Files not reviewed (1)" });
    await expect(files).toBeVisible();
    await files.locator("summary").click();
    const open = await files.getAttribute("open");
    const before = reads;
    await page.clock.fastForward(10_000);
    await expect.poll(() => reads).toBeGreaterThan(before);
    await expect(page.getByText(`${reads} of 10 assigned files completed.`, { exact: true })).toBeVisible();
    if (open === null) await expect(files).not.toHaveAttribute("open");
    else await expect(files).toHaveAttribute("open", "");
  });
}

test("personal setup saves without a model then signs in and selects one", async ({ page }, info) => {
  await mock(page);
  let connected = false;
  let savedModel = "";
  await page.route("**/api/v1/connections", (route) => route.request().method() === "POST"
    ? route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", model: "", owner_id: 7, auth_status: "disconnected" } })
    : route.fallback());
  await page.route("**/api/v1/connections/personal/auth", (route) => {
    if (route.request().method() === "POST") connected = true;
    return route.fulfill({ json: { status: connected ? "connected" : "disconnected" } });
  });
  await page.route("**/api/v1/connections/personal/models", (route) => route.fulfill({ json: { source: "client", status: "ready", models: [{ id: "personal-model", name: "Personal model" }] } }));
  await page.route("**/api/v1/connections/personal", (route) => {
    savedModel = route.request().postDataJSON().model;
    return route.fulfill({ json: { ...connection, kind: "chatgpt", id: "personal", model: savedModel } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection type").selectOption("chatgpt");
  await page.getByLabel("Connection name").fill("Personal setup");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Personal setup" })).toBeVisible();
  await expect(page.getByText("Choose a model to finish setup and enable reviews.")).toBeVisible();
  await page.getByRole("button", { name: "Connect ChatGPT", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator('option[value="personal-model"]')).toHaveCount(1);
  await model.selectOption("personal-model");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.getByText("Choose a model to finish setup and enable reviews.")).toHaveCount(0);
  expect(savedModel).toBe("personal-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/personal-model-picker-${info.project.name}.png`, fullPage: true });
});

test("new instance sends the bootstrap token in the request header", async ({
  page,
}, info) => {
  await mock(page, { configured: false });
  await page.route("**/api/v1/setup/manifest", (route) => route.fulfill({
    status: 403,
    json: { detail: "This test stops before GitHub registration." },
  }));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Give your reviews a home." }),
  ).toBeVisible();
  await expect(page.getByLabel("Bootstrap token")).toHaveAttribute(
    "type",
    "password",
  );
  await page.getByLabel("Bootstrap token").fill("test-bootstrap-token");
  const sent = page.waitForRequest("**/api/v1/setup/manifest");
  await page.getByRole("button", { name: "Register GitHub App" }).click();
  const request = await sent;
  expect(request.headers()["x-setup-token"]).toBe("test-bootstrap-token");
  expect(request.postDataJSON()).toEqual({ organization: "" });
  await expect(page.getByText("This test stops before GitHub registration.")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/bootstrap-${info.project.name}.png`,
    fullPage: true,
  });
});

for (const configured of [false, true]) {
  test(`keeps the development browser origin before ${configured ? "sign-in" : "registration"}`, async ({
    page,
  }) => {
    const publicUrl = "http://localhost:4178";
    const requests = await mock(page, { configured, publicUrl });
    const sessionOrigins: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/auth/me") sessionOrigins.push(url.origin);
    });
    const entry = configured ? "/repositories?from=local#setup" : "/?from=local#setup";
    await page.goto(entry);
    await expect(page).toHaveURL(`http://127.0.0.1:4178${entry}`);
    await expect(
      page.getByRole("heading", {
        name: configured ? repo.full_name : "Give your reviews a home.",
        exact: true,
      }),
    ).toBeVisible();
    expect(sessionOrigins).toEqual(configured ? ["http://127.0.0.1:4178"] : []);
    expect(requests).toEqual([]);
  });
}

test("breadcrumbs navigate every page hierarchy and settings tab", async ({ page }, info) => {
  await mock(page);
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1283, height: 938 });
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  const current = breadcrumb.locator('[aria-current="page"]');
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await expect(current).toHaveText("Review run");
  await expect(breadcrumb.getByRole("link", { name: "Inbox", exact: true })).toHaveAttribute("href", "/");
  await noOverflow(page);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await page.screenshot({ path: `../.impeccable/review/header-${info.project.name}-${theme}.png`, animations: "disabled" });
  }
  const parent = await breadcrumbAncestor(page, "Pull request #148");
  await expect(parent).toHaveAttribute("href", "/pulls/pr");
  await parent.press("Enter");
  await expect(current).toHaveText("Pull request #148");
  await (await breadcrumbAncestor(page, "acme/payments")).click();
  await expect(page).toHaveURL(/\/repositories\/22$/);
  await expect(current).toHaveText("acme/payments");
  await breadcrumb.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(current).toHaveText("Inbox");
  await page.goto("/settings");
  await expect(current).toHaveText("AI connections");
  for (const label of ["GitHub", "Team"]) {
    await page.getByRole("tab", { name: label, exact: true }).click();
    await expect(current).toHaveText(label);
  }
  await (await breadcrumbAncestor(page, "Settings")).click();
  await expect(current).toHaveText("AI connections");
  for (const tab of ["", "unknown", "constructor"]) {
    await page.goto(`/settings?tab=${tab}`);
    await expect(current).toHaveText("AI connections");
  }
  await page.goto("/setup");
  await expect(current).toHaveText("GitHub");
  await page.goto("/missing-page");
  await expect(current).toHaveText("Page not found");
  await breadcrumb.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(current).toHaveText("Inbox");
});

test("breadcrumbs keep long names accessible without pushing the theme button offscreen", async ({ page }, info) => {
  await mock(page);
  const fullName = "organization-with-a-very-long-name/repository-with-a-very-long-name";
  await page.route("**/api/v1/pulls/pr", (route) => route.fulfill({ json: {
    repository: { ...repo, full_name: fullName }, pull_request: pr, runs: [run], findings: [], can_write: true,
  } }));
  await page.goto("/pulls/pr");
  await expect(page.getByRole("heading", { name: pr.title, exact: true })).toBeVisible();
  for (const width of info.project.name === "desktop" ? [1283, 1024, 768, 640] : [390, 320]) {
    await page.setViewportSize({ width, height: 938 });
    await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeInViewport();
    await expect(page.getByRole("navigation", { name: "Breadcrumb", exact: true }).locator('[aria-current="page"]')).toBeInViewport();
    await noOverflow(page);
  }
  await expect(await breadcrumbAncestor(page, fullName)).toHaveAttribute("href", "/repositories/22");
  await page.screenshot({ path: `../.impeccable/review/header-long-${info.project.name}.png`, animations: "disabled" });
});

test("breadcrumbs handle unavailable data and member settings without inventing links", async ({ page }) => {
  await mock(page, { role: "member" });
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  const current = breadcrumb.locator('[aria-current="page"]');
  await page.goto("/settings?tab=team");
  await expect(current).toHaveText("AI connections");
  await page.route("**/api/v1/pulls/pr", (route) => route.fulfill({ status: 403, json: { detail: "Permission denied" } }));
  await page.goto("/pulls/pr");
  await expect(current).toHaveText("Pull request");
  await expect(breadcrumb).not.toContainText("acme/payments");
  await expect(breadcrumb.getByRole("link", { name: "Inbox", exact: true })).toHaveAttribute("href", "/");
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [] }));
  await page.goto("/repositories");
  await expect(current).toHaveText("Repositories");
  await expect(page.getByRole("heading", { name: "Connect your first repository", exact: true })).toBeVisible();
  await expect(page.getByText("Ask your administrator to connect a repository you can access on GitHub.", { exact: true })).toBeVisible();
  await noOverflow(page);
});

test("header theme toggle follows the OS until an explicit choice, then persists", async ({
  page,
}, info) => {
  await mock(page);
  await page.goto("/pulls/pr");
  await expect(page.getByRole("combobox", { name: "Appearance", exact: true })).toHaveCount(0);
  await expect(page.getByRole("banner").getByRole("button", { name: "Switch to dark mode" })).toBeInViewport();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
  await page.screenshot({
    path: `../.impeccable/review/dark-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Switch to light mode" }).press("Enter");
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.reload();
  await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeVisible();
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.getByRole("button", { name: "Switch to dark mode" }).press("Space");
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveClass("dark");
  await page.reload();
  await expect(page.getByRole("button", { name: "Switch to light mode" })).toBeVisible();
  await expect(page.locator("html")).toHaveClass("dark");
});

test("members manage personal subscriptions without administrative controls", async ({
  page,
}, info) => {
  await mock(page, { role: "member", personal: true });
  await page.goto("/settings");
  await expect(
    page.getByRole("tab", { name: "Team", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "GitHub", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Manage account" }).click();
  await expect(page.getByText("ABCD-1234")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Authorize with ChatGPT" }),
  ).toHaveAttribute("href", "https://auth.openai.com/codex/device");
  await expect(
    page.getByRole("switch", {
      name: "I authorize automatic reviews with my subscription",
    }),
  ).not.toBeChecked();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/subscription-${info.project.name}.png`,
    fullPage: true,
  });
  const automatic = page.getByRole("switch", { name: "I authorize automatic reviews with my subscription" });
  await automatic.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/subscription-consent-${info.project.name}.png` });
});

test("Claude login uses a private console and closes with its dialog", async ({ page }, info) => {
  await mock(page, { role: "member", personal: true, claude: true });
  const messages: string[] = [];
  let closed = false;
  await page.routeWebSocket("**/connections/personal/console", (socket) => {
    socket.onMessage((message) => {
      messages.push(String(message));
      if (messages.length === 1) socket.send("Open \x1b]8;;https://claude.com/cai/oauth/authorize?test=fixture\x1b\\https://claude.com/cai/oauth/authorize?test=fixture\x1b]8;;\x1b\\\nPaste the authorization code:");
    });
    socket.onClose(() => { closed = true; });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Manage account" }).click();
  await page.getByRole("button", { name: "Connect Claude Code", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open Claude authorization" })).toBeVisible();
  const response = page.getByLabel("Response to the official login prompt");
  await expect(response).toHaveAttribute("type", "password");
  await response.fill("test-authorization-code");
  await page.getByRole("button", { name: "Send to Claude Code" }).click();
  await expect(response).toHaveValue("");
  expect(messages).toEqual([JSON.stringify({ ticket: "single-use-test-ticket" }), "test-authorization-code\r"]);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/claude-console-${info.project.name}.png` });
  await page.getByRole("button", { name: "Close console" }).click();
  await expect(page.getByLabel("Official Claude Code login console")).toHaveCount(0);
  await expect.poll(() => closed).toBe(true);
});

test("OAuth errors remain recoverable inside the app", async ({ page }) => {
  await mock(page);
  await page.goto("/?auth_error=expired&diagnostic=request-123");
  await expect(page.getByRole("alert")).toContainText("This attempt expired");
  await expect(page.getByText("Diagnostic: request-123")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Continue with GitHub" }),
  ).toHaveAttribute("href", "/api/v1/auth/login");
});

test("verified fixes show evidence, pending sync, retry and manual override", async ({ page }, info) => {
  const requests = await mock(page, {
    finding: {
      desired_status: "resolved", sync_error: "GitHub unavailable",
      sync_intent: { verification_id: "verification", finding_id: "finding", version: 0 },
    },
    verifications: [{
      id: "verification", run_id: "run", head_sha: "c".repeat(40), status: "completed", verdict: "fixed",
      resolution: "failed", error: "GitHub unavailable", created_at: at, finished_at: at,
      result: {
        verdict: "fixed", original_issue_present: true, dismissal_recommended: false,
        explanation: "The retry loop now stops after three attempts.",
        change_explanation: "A bounded loop replaces the unbounded retry.",
        evidence: [{ revision: "current", path: "src/retry.ts", start_line: 1, end_line: 1, quote: "for (let attempt = 0; attempt < 3; attempt++) {" }],
      },
    }],
  });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  await expect(page.getByText("The retry loop now stops after three attempts.")).toBeVisible();
  await expect(page.getByText("Automatic resolution: failed")).toBeVisible();
  await page.getByText("Current evidence: src/retry.ts:1–1").click();
  await expect(page.getByText("for (let attempt = 0; attempt < 3; attempt++) {", { exact: true })).toBeVisible();
  await expect(page.locator("pre .language-typescript .hljs-keyword").first()).toHaveText("for");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/verification-${info.project.name}.png`, fullPage: false });
  await page.getByRole("button", { name: "Retry sync", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/findings/finding/sync", method: "POST" });
  await page.getByRole("button", { name: "Keep finding open", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/findings/finding", method: "PATCH", body: { status: "open" } });
});

test("run shows verification separately from confirmed resolutions", async ({ page }) => {
  await mock(page, { run: { progress: { verification: {
    selected: 3, completed: 3, fixed: 1, still_present: 1, uncertain: 1, resolved: 0, pending: 1,
  } } } });
  await page.goto("/runs/run");
  await expect(page.getByText("3 of 3 findings verified. 0 automatically resolved, 1 pending synchronization.")).toBeVisible();
  await noOverflow(page);
});


test("automatic resolution is opt-in and requires automatic reviews", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const automatic = page.getByRole("switch", { name: "Automatic reviews", exact: true });
  const resolution = page.getByRole("switch", { name: "Automatically resolve verified fixes", exact: true });
  await expect(resolution).not.toBeChecked();
  await automatic.uncheck();
  await expect(resolution).toBeDisabled();
  await automatic.check();
  await resolution.check();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/auto-resolution-setting-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect.poll(() => requests.some((request) => request.path === "/repositories/22" &&
    (request.body as { settings?: { auto_resolve?: boolean } }).settings?.auto_resolve === true)).toBe(true);
});


test("pending verification has one status and failed verification has an alert", async ({ page }) => {
  await mock(page, { verifications: [
    { id: "pending", run_id: "run", head_sha: "c".repeat(40), status: "queued", verdict: null,
      resolution: "not_applicable", error: null, result: null, created_at: at, finished_at: null },
    { id: "failed", run_id: "run", head_sha: "d".repeat(40), status: "failed", verdict: "uncertain",
      resolution: "not_applicable", error: "Verification context is unavailable", result: null,
      created_at: at, finished_at: at },
  ] });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const panel = page.getByRole("dialog");
  await expect(panel.getByText("queued", { exact: true })).toHaveCount(1);
  await expect(panel.getByRole("alert")).toContainText("Verification context is unavailable");
  await expect(panel.getByText("Automatic resolution: not applicable")).toHaveCount(2);
  await noOverflow(page);
});

for (const verification of [null, [1, 2], { selected: "3", completed: 3 }, { selected: 3, pending: -1 }]) {
  test(`invalid verification counts are not presented: ${JSON.stringify(verification)}`, async ({ page }) => {
    await mock(page, { run: { progress: { verification } } });
    await page.goto("/runs/run");
    await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
    await expect(page.getByText(/findings verified/)).toHaveCount(0);
  });
}

test("GitHub installations manage repositories and confirm uninstall", async ({ page }, info) => {
  const requests = await mock(page);
  let current = { ...installation };
  let deletes = 0;
  await page.route("**/api/v1/installations", (route) => route.fulfill({ json: [
    current,
    { ...installation, id: 11, account: "personal-account", connected: false,
      settings_url: "https://github.com/settings/installations/11", can_uninstall: false },
  ] }));
  await page.route("**/api/v1/installations/10", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    expect(route.request().headers()["x-csrf-token"]).toBe("csrf-from-session");
    deletes++;
    current = { ...current, operation_status: "queued", job_id: "uninstall", can_connect: false, can_uninstall: false };
    await route.fulfill({ status: 202, json: { job_id: "uninstall" } });
  });
  await page.goto("/repositories");
  await page.getByRole("main").getByRole("link", { name: "Manage repositories", exact: true }).click();
  const account = page.getByRole("article", { name: "acme", exact: true });
  await expect(page.getByRole("heading", { name: "acme", exact: true })).toHaveCount(1);
  await expect(account.getByRole("link", { name: "Manage repositories" })).toHaveAttribute("href", installation.settings_url!);
  await expect(page.getByText("personal-account", { exact: true })).toBeVisible();
  await account.getByRole("button", { name: "Resync repositories" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Repository sync queued" })).toBeVisible();
  expect(requests.some((request) => request.path === "/installations/10" && request.method === "POST")).toBe(true);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/github-installations-${info.project.name}.png`, fullPage: true });

  await account.getByRole("button", { name: "Uninstall", exact: true }).focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Uninstall from acme?" })).toBeVisible();
  await expect(dialog).toContainText("every repository in the installation");
  await expect(dialog).toContainText("Saved review history");
  await page.screenshot({ path: `../.impeccable/review/github-uninstall-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(deletes).toBe(0);
  await expect(account.getByRole("button", { name: "Uninstall", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await dialog.getByRole("button", { name: "Uninstall GitHub App" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(account).toContainText("Waiting for GitHub to confirm removal");
  await expect(account.getByRole("button", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(account.getByRole("button", { name: "Resync repositories" })).toBeDisabled();
  expect(deletes).toBe(1);
  current = { ...current, active: false, status: "uninstalled", operation_status: "done", settings_url: null };
  await expect(account).toContainText("Uninstalled from GitHub. Saved review history is retained.");
  await expect(account.getByRole("button", { name: "Uninstall", exact: true })).toHaveCount(0);
});

test("GitHub installations show recoverable failures and unavailable actions", async ({ page }) => {
  await mock(page);
  let current = { ...installation, active: false, can_connect: false, operation_status: "failed",
    operation_error: "GitHub has not confirmed uninstall. Reviews remain paused." };
  let refreshError = false;
  await page.route("**/api/v1/installations", (route) => route.fulfill(refreshError
    ? { status: 502, json: { detail: "GitHub could not be reached. Refresh to retry." } }
    : { json: [current, { ...installation, id: 12, account: "restricted-team", can_uninstall: false,
      status: "suspended", can_connect: false, unavailable_reason: "Uninstall requires an organization owner." },
      { ...installation, id: 13, account: "permission-update", can_uninstall: false,
        unavailable_reason: "GitHub organization ownership could not be verified. To verify organization ownership, enable Organization permissions → Members: Read-only in the GitHub App settings, approve the permission update for this installation, then refresh." }] }));
  await page.route("**/api/v1/installations/10", (route) => route.fulfill({ status: 403, json: { detail: "GitHub ownership changed. Refresh and retry." } }));
  await page.goto("/settings?tab=github");
  const account = page.getByRole("article", { name: "acme", exact: true });
  await expect(account).toContainText("Uninstall did not complete");
  await expect(account.getByRole("alert")).toContainText("Reviews remain paused");
  const restricted = page.getByRole("article", { name: "restricted-team" });
  await expect(restricted.getByRole("button", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(restricted.getByRole("button", { name: "Resync repositories" })).toBeDisabled();
  await expect(restricted).toContainText("requires an organization owner");
  const permissionUpdate = page.getByRole("article", { name: "permission-update" });
  await expect(permissionUpdate).toContainText("Members: Read-only");
  await expect(permissionUpdate).toContainText("approve the permission update");
  await expect(permissionUpdate.getByRole("button", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(permissionUpdate.getByRole("button", { name: "Resync repositories" })).toBeEnabled();
  await expect(permissionUpdate.getByRole("link", { name: "Manage repositories" })).toBeVisible();
  await account.getByRole("button", { name: "Retry uninstall" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Uninstall GitHub App" }).click();
  await expect(page.getByRole("dialog")).toContainText("GitHub ownership changed");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  refreshError = true;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "GitHub could not be reached" })).toBeVisible();
  await expect(account.getByRole("button", { name: "Retry uninstall" })).toBeDisabled();
  await expect(account.getByText("Uninstalled from GitHub", { exact: false })).toHaveCount(0);
  refreshError = false;
  current = { ...current, operation_status: "done", status: "uninstalled", can_uninstall: false };
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(account).toContainText("Uninstalled from GitHub");
  await noOverflow(page);
});

test("GitHub installations refresh on return and connect available accounts", async ({ page }) => {
  const requests = await mock(page);
  let connected = false;
  let reads = 0;
  await page.route("**/api/v1/installations", (route) => {
    reads++;
    return route.fulfill({ json: [{ ...installation, connected,
      settings_url: "https://github.com/settings/installations/10", repository_selection: reads > 1 ? "all" : "selected" }] });
  });
  await page.goto("/settings?tab=github");
  await expect(page.getByRole("heading", { name: "No connected installations" })).toBeVisible();
  connected = true;
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("article", { name: "acme" })).toContainText("All repositories on GitHub");
  expect(requests.some((request) => request.path === "/installations/10" && request.method === "POST")).toBe(true);
  const before = reads;
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    window.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    window.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => reads).toBeGreaterThan(before);
  await expect(page.getByRole("article", { name: "acme" }).getByRole("link", { name: "Manage repositories" }))
    .toHaveAttribute("href", "https://github.com/settings/installations/10");
});
