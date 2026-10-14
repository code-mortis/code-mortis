import { test, expect, type Page, type Locator } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import type { Run, Schema } from "../src/api";
import { contrast, themes } from "../src/lib/appearance";
import { findingSeverities } from "../src/lib/review-filters";

const at = "2026-10-05T13:20:00Z";
const installation: Schema["InstallationOut"] = {
  id: 10, account: "acme", connected: true, active: true, status: "active",
  settings_url: "https://github.com/organizations/acme/settings/installations/10",
  repository_selection: "selected", can_connect: true, can_uninstall: true, can_remove: false,
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
    delegated_context_bytes: 800000,
    concurrency: 2,
    rules: null,
  },
  open_prs: 2,
  has_running_review: false,
};
const run = {
  id: "run",
  connection_id: "connection",
  blocked_reason: null,
  failure_reason: null,
  check_synced_at: null,
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

function findingPage(items: Schema["FindingOut"][], params: URLSearchParams) {
  const statuses = ["open", "resolved", "dismissed"] as const;
  const query = (params.get("q") ?? "").toLowerCase();
  const filtered = items.filter((item) => (!params.has("status") || params.getAll("status").includes(item.status))
    && (!params.has("severity") || params.getAll("severity").includes(item.severity))
    && (!params.has("category") || params.getAll("category").includes(item.category))
    && (!query || `${item.content} ${item.path} ${item.category}`.toLowerCase().includes(query)));
  return {
    groups: statuses.filter((group) => (!params.has("status") || params.getAll("status").includes(group)) && (!params.has("group") || params.get("group") === group)).map((group) => {
      const matches = filtered.filter((item) => item.status === group);
      const page = Math.min(params.has("group") ? Number(params.get("page") ?? 1) : 1, Math.max(1, Math.ceil(matches.length / 10)));
      return { group, items: matches.slice((page - 1) * 10, page * 10), total: matches.length, page, has_more: page * 10 < matches.length };
    }),
    total: filtered.length, registered_total: items.length, page_size: 10,
    status_counts: Object.fromEntries(statuses.map((status) => [status, items.filter((item) => item.status === status).length])),
    category_counts: Object.fromEntries([...new Set(items.map((item) => item.category))].map((category) => [category, items.filter((item) => item.category === category).length])),
  };
}

type RepositoryPullFixture = Omit<typeof pr, "latest_run"> & { latest_run: typeof run | null };
function repositoryPage(items: RepositoryPullFixture[], params: URLSearchParams) {
  const rows = items.map((item) => ({ ...item, repository_name: repo.full_name, created_at: at,
    review_status: !item.latest_run ? "not_reviewed" : item.latest_run.head_sha !== item.head_sha || item.latest_run.base_sha !== item.base_sha ? "outdated" : item.latest_run.status,
    has_running_review: item.latest_run?.status === "running", run_id: item.latest_run?.id ?? null,
    publication: item.latest_run?.publication ?? null, publication_error: item.latest_run?.publication_error ?? null,
  }));
  const query = (params.get("q") ?? "").toLowerCase();
  const matches = rows.filter((item) => (!query || `${item.title} ${item.head_ref} ${item.author}`.toLowerCase().includes(query.replace(/^@/, "")) || String(item.number) === query.replace(/^#/, ""))
    && (!params.has("review") || params.getAll("review").includes(item.review_status) || (params.getAll("review").includes("running") && item.has_running_review))
    && (!params.has("author") || params.getAll("author").includes(item.author.toLowerCase()))
    && (!params.has("findings") || (params.get("findings") === "open" ? item.open_findings > 0 : item.open_findings === 0)));
  const filtered = matches.filter((item) => !params.has("state") || params.getAll("state").includes("all") || params.getAll("state").includes(item.state) || item.has_running_review);
  const ordering = params.get("ordering") ?? "-updated_at";
  const field = ordering.replace(/^-/, "") as "title" | "author" | "updated_at";
  filtered.sort((a, b) => Number(b.has_running_review) - Number(a.has_running_review) || a[field].localeCompare(b[field]) * (ordering.startsWith("-") ? -1 : 1));
  return {
    groups: (["active", "merged", "closed"] as const).filter((group) => !params.has("group") || params.get("group") === group).map((group) => {
      const matches = filtered.filter((item) => (item.has_running_review || item.state === "open" ? "active" : item.state) === group);
      const page = Math.min(params.has("group") ? Number(params.get("page") ?? 1) : 1, Math.max(1, Math.ceil(matches.length / 10)));
      return { group, items: matches.slice((page - 1) * 10, page * 10), total: matches.length, page, has_more: page * 10 < matches.length };
    }),
    total: filtered.length, registered_total: items.length, page_size: 10, repository_count: items.length ? 1 : 0,
    repositories: [{ id: repo.id, full_name: repo.full_name }], authors: [...new Set(items.map((item) => item.author))],
    state_counts: Object.fromEntries(["open", "merged", "closed"].map((state) => [state, matches.filter((item) => item.state === state).length])),
  };
}

async function mockRepositoryPulls(page: Page, repositoryId: number, items: () => RepositoryPullFixture[]) {
  await page.route(`**/api/v1/repositories/${repositoryId}/pulls?**`, (route) => route.fulfill({ json: repositoryPage(items(), new URL(route.request().url()).searchParams) }));
}

type PullDetailFixture = { pull_request: typeof pr; runs: Run[]; findings: Schema["FindingOut"][]; [key: string]: unknown };

function pullMetadata({ runs, findings: _findings, pull_request, ...metadata }: PullDetailFixture) {
  return {
    ...metadata, pull_request: { ...pull_request, latest_run: runs[0] ?? null }, run_count: runs.length,
    has_active_run: runs.some((run) => ["queued", "running"].includes(run.status)),
    latest_observed_run_id: runs.find((run) => ["completed", "partial"].includes(run.status))?.id ?? null,
  };
}

function runPage(runs: Run[], params: URLSearchParams): Schema["RunPage"] {
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(runs.length / 10)));
  return { items: runs.slice((page - 1) * 10, page * 10), total: runs.length, page, has_more: page * 10 < runs.length, page_size: 10 };
}

function candidateOutcome(candidate: Schema["CandidateOut"]) {
  return candidate.finding_id ? candidate.is_new ? "new" : "repeated" : candidate.result?.verdict === "rejected" ? "rejected" : ["queued", "running"].includes(candidate.status) ? "pending" : "withheld";
}

function candidatePage(candidates: Schema["CandidateOut"][], params: URLSearchParams): Schema["CandidatePage"] {
  const query = (params.get("q") ?? "").toLowerCase();
  const matches = candidates.filter((candidate) => (!params.has("outcome") || params.getAll("outcome").includes(candidateOutcome(candidate)))
    && (!params.has("severity") || params.getAll("severity").includes(String(candidate.snapshot.severity)))
    && (!params.has("category") || params.getAll("category").includes(String(candidate.snapshot.category)))
    && (!query || [candidate.snapshot.path, candidate.snapshot.content, candidate.snapshot.category].some((value) => String(value ?? "").toLowerCase().includes(query))));
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(matches.length / 10)));
  return { items: matches.slice((page - 1) * 10, page * 10), total: matches.length, registered_total: candidates.length,
    page, has_more: page * 10 < matches.length, page_size: 10,
    outcome_counts: Object.fromEntries(["new", "repeated", "rejected", "withheld", "pending"].map((outcome) => [outcome, candidates.filter((candidate) => candidateOutcome(candidate) === outcome).length])),
    severity_counts: Object.fromEntries(["critical", "high", "medium", "low"].map((severity) => [severity, candidates.filter((candidate) => candidate.snapshot.severity === severity).length])),
    category_counts: Object.fromEntries([...new Set(candidates.map((candidate) => String(candidate.snapshot.category ?? "")).filter(Boolean))].map((category) => [category, candidates.filter((candidate) => candidate.snapshot.category === category).length])),
  };
}

function resolvedPage(findings: Schema["FindingOut"][], params: URLSearchParams): Schema["ResolvedPage"] {
  const q = (params.get("q") ?? "").toLowerCase();
  const matches = findings.filter((finding) => (!params.has("severity") || params.getAll("severity").includes(finding.severity))
    && (!params.has("category") || params.getAll("category").includes(finding.category))
    && (!q || [finding.path, finding.content, finding.category].some((value) => value.toLowerCase().includes(q))))
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(matches.length / 10)));
  return { items: matches.slice((page - 1) * 10, page * 10), total: matches.length, registered_total: findings.length,
    page, has_more: page * 10 < matches.length, page_size: 10,
    severity_counts: Object.fromEntries(findingSeverities.map((severity) => [severity, findings.filter((finding) => finding.severity === severity).length])),
    category_counts: Object.fromEntries([...new Set(findings.map((finding) => finding.category))].map((category) => [category, findings.filter((finding) => finding.category === category).length])),
  };
}

async function mockPullDetail(page: Page, detail: () => PullDetailFixture) {
  await page.route("**/api/v1/pulls/pr", (route) => route.fulfill({ json: pullMetadata(detail()) }));
  await page.route("**/api/v1/pulls/pr/findings?**", (route) => route.fulfill({ json: findingPage(detail().findings, new URL(route.request().url()).searchParams) }));
  await page.route("**/api/v1/pulls/pr/runs?**", (route) => route.fulfill({ json: runPage(detail().runs, new URL(route.request().url()).searchParams) }));
}

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
    candidates?: Schema["CandidateOut"][];
    occurrences?: Schema["OccurrenceOut"][];
    resolved?: Schema["FindingOut"][];
    assessment?: Schema["AssessmentSummary"] | null;
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
    else if (path === "/pulls/pr/runs" && method === "GET")
      data = runPage([currentRun], new URL(request.url()).searchParams);
    else if (path === "/pulls/pr" || path === "/pulls/pr/findings") {
      const detail = {
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
      data = path.endsWith("/findings") ? findingPage(detail.findings, new URL(request.url()).searchParams) : pullMetadata(detail);
    } else if (path === "/findings/finding") {
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
        occurrences: options.occurrences ?? (currentRun.status === "skipped" ? [] : [
          { finding_id: "finding", snapshot: finding, inline_line: 46 },
        ]),
        assessment: options.assessment ?? null,
        resolved_count: options.resolved?.length ?? 0,
        can_write: options.canWrite !== false,
      };
    else if (path === "/runs/run/resolved") data = resolvedPage(options.resolved ?? [], new URL(request.url()).searchParams);
    else if (path === "/runs/run/candidates") data = candidatePage(options.candidates ?? [], new URL(request.url()).searchParams);
    else if (path.startsWith("/runs/run/candidates/")) data = options.candidates?.find((candidate) => candidate.id === path.split("/").at(-1));
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
    if (/^\/repositories\/\d+\/pulls$/.test(path) && Array.isArray(data)) data = repositoryPage(data, new URL(request.url()).searchParams);
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

async function chooseOption(page: Page, input: Locator, choice: string) {
  await input.fill(choice);
  await page.getByRole("option").filter({ hasText: choice }).click();
}

async function expectOption(page: Page, input: Locator, choice: string, count = 1) {
  await input.fill(choice);
  await expect(page.getByRole("option").filter({ hasText: choice })).toHaveCount(count);
  await input.press("Escape");
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(await page.getByRole("dialog").evaluateAll((dialogs) =>
    dialogs.every((element) => element.scrollWidth <= element.clientWidth),
  )).toBe(true);
}

async function repositorySection(page: Page, name: string) {
  const link = page.getByRole("navigation", { name: "Repository settings sections" }).getByRole("link", { name, exact: true });
  await link.click();
  await expect(link).toHaveAttribute("aria-current", "page");
}

test("sidebar repositories navigate, show activity, and collapse accessibly", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [
    { ...repo, has_running_review: true },
    { ...repo, id: 23, full_name: "other/payments", enabled: false },
    { ...repo, id: 24, full_name: "legacy-repository" },
  ] }));
  await mockRepositoryPulls(page, 23, () => []);
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: repo.full_name, exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Repository", exact: true })).toHaveCount(0);
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("link", { name: "Inbox", exact: true })).toHaveAccessibleDescription("6 open pull requests");
  await expect(nav.getByRole("link", { name: "legacy-repository", exact: true }).locator(".font-medium")).toHaveText("legacy-repository");
  const running = nav.getByRole("link", { name: "acme/payments, review running", exact: true });
  await expect(running).toHaveAttribute("aria-current", "page");
  await expect(running).not.toContainText("Running");
  await expect(running.locator(".motion-safe\\:animate-spin")).toBeVisible();
  await expect(running.locator(".animate-spin, .motion-safe\\:animate-spin")).toHaveCSS("animation-name", "none");
  await nav.getByRole("link", { name: "other/payments", exact: true }).click();
  await expect(page).toHaveURL(/\/repositories\/23\?state=open$/);
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
  await mockRepositoryPulls(page, 23, () => []);
  await page.goto("/repositories?repo=23&configure=limits#settings");
  await expect(page).toHaveURL(/\/repositories\/23\/settings\?tab=limits$/);
  await expect(page.getByRole("form", { name: "Repository configuration" })).toContainText("other/payments");
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
  await (await breadcrumbAncestor(page, "other/payments")).click();
  await expect(page).toHaveURL(/\/repositories\/23\?state=open$/);
  await page.goto("/repositories/22/settings?repo=23&configure=limits&state=all&q=retry&tab=connection");
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?state=all&q=retry&tab=limits$/);
  const repositoryCrumb = await breadcrumbAncestor(page, repo.full_name);
  await expect(repositoryCrumb).toHaveAttribute("href", "/repositories/22?state=all&q=retry");
  await repositoryCrumb.click();
  await expect(page).toHaveURL((url) => url.pathname === "/repositories/22" && url.searchParams.size === 2
    && url.searchParams.get("state") === "all" && url.searchParams.get("q") === "retry");
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
  await expect(page).toHaveURL(/\/repositories\/22\?state=open$/);
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
  const settings = page.getByRole("menuitem", { name: "AI connections", exact: true });
  await expect(settings).toBeFocused();
  await expect(settings).toHaveAttribute("href", "/settings?tab=connections");
  await settings.press("Enter");
  await expect(page).toHaveURL(/\/settings\?tab=connections$/);
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
  await openNavigation(page);
  await account.press("Enter");
  await page.keyboard.press("End");
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
  await account.click();
  const menu = page.getByRole("menu");
  await expect(menu.locator('[data-slot="avatar-fallback"]')).toHaveText("AC");
  await expect(menu).toContainText(login);
  await expect(menu).toContainText("Member");
  await noOverflow(page);
});

for (const role of ["admin", "member"]) test(`sidebar account links to the settings available to ${role}`, async ({ page }) => {
  await mock(page, { role });
  await page.goto("/repositories/22");
  const sections = role === "admin"
    ? [["connections", "AI connections"], ["appearance", "Appearance"], ["github", "GitHub"], ["team", "Team"]]
    : [["connections", "AI connections"], ["appearance", "Appearance"]];
  for (const [tab, label] of sections) {
    await openNavigation(page);
    await page.getByRole("button", { name: "Account menu for @carlos", exact: true }).click();
    const menu = page.getByRole("menu");
    await expect(menu).toContainText("carlos");
    await expect(menu).toContainText(role === "admin" ? "Administrator" : "Member");
    await expect(menu.getByRole("menuitem")).toHaveCount(sections.length + 1);
    if (role === "member") {
      await expect(menu.getByRole("menuitem", { name: "GitHub", exact: true })).toHaveCount(0);
      await expect(menu.getByRole("menuitem", { name: "Team", exact: true })).toHaveCount(0);
    }
    const link = menu.getByRole("menuitem", { name: label, exact: true });
    await expect(link).toHaveAttribute("href", `/settings?tab=${tab}`);
    await expect(link).toBeInViewport();
    await link.click();
    await expect(page).toHaveURL(`/settings?tab=${tab}`);
    await expect(page.getByRole("heading", { name: label, exact: true })).toBeVisible();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
  }
});

test("sidebar activity refreshes and does not present failed polling as current", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let running = true;
  let failure = 0;
  let openPrs = 2;
  await page.route("**/api/v1/repositories", (route) => route.fulfill(failure
    ? { status: failure, json: { detail: "Repositories unavailable" } }
    : { json: [{ ...repo, open_prs: openPrs, has_running_review: running }] }));
  await page.goto("/settings");
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  const activity = nav.getByRole("link", { name: "acme/payments, review running", exact: true }).locator(".motion-safe\\:animate-spin");
  const inbox = nav.getByRole("link", { name: "Inbox", exact: true });
  const count = nav.locator('[data-slot="sidebar-menu-badge"]');
  await expect(inbox).toHaveAccessibleDescription("2 open pull requests");
  await expect(inbox).toHaveAttribute("title", "2 open pull requests");
  await expect(activity).toBeVisible();
  running = false;
  openPrs = 0;
  await page.clock.fastForward(6000);
  await expect(activity).toHaveCount(0);
  await expect(count).toHaveText("0 open pull requests");
  await expect(inbox).toHaveAccessibleDescription("0 open pull requests");
  await expect(inbox).toHaveAttribute("title", "0 open pull requests");
  running = true;
  openPrs = 1;
  await page.clock.fastForward(6000);
  await expect(activity).toBeVisible();
  await expect(inbox).toHaveAccessibleDescription("1 open pull request");
  await expect(inbox).toHaveAttribute("title", "1 open pull request");
  failure = 503;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository activity unavailable.");
  await expect(nav.getByRole("link", { name: repo.full_name, exact: true })).toBeVisible();
  await expect(activity).toHaveCount(0);
  await expect(count).toHaveCount(0);
  await expect(inbox).not.toHaveAttribute("title");
  failure = 0;
  await nav.getByRole("button", { name: "Retry repositories" }).click();
  await expect(activity).toBeVisible();
  await expect(inbox).toHaveAccessibleDescription("1 open pull request");
  failure = 403;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository access denied.");
  await expect(nav.getByRole("button", { name: "Retry repositories" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: /^acme\/payments/ })).toHaveCount(0);
  await expect(count).toHaveCount(0);
  failure = 401;
  await page.clock.fastForward(6000);
  await expect(nav.getByRole("alert")).toHaveText("Repository access denied.");
  await expect(nav.getByRole("button", { name: "Retry repositories" })).toHaveCount(0);
});

test("sidebar handles long repository lists and empty access", async ({ page }, info) => {
  await mock(page, { role: "member" });
  const name = "organization-with-a-long-name/repository-with-a-very-long-name";
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: Array.from({ length: 40 }, (_, i) => ({ ...repo, id: i + 22, full_name: `${name}-${i}`, has_running_review: i === 0 })) }));
  await page.route("**/api/v1/repositories/*/pulls?**", (route) => route.fulfill({ json: repositoryPage([], new URL(route.request().url()).searchParams) }));
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

test("repository breadcrumbs and sidebar match only supported routes", async ({ page }) => {
  await mock(page);
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  for (const suffix of ["", "/"]) {
    await page.goto(`/repositories/22${suffix}`);
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText(repo.full_name);
    await page.goto(`/repositories/22/settings${suffix}?state=all&tab=limits`);
    await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Configure");
    await expect(await breadcrumbAncestor(page, repo.full_name)).toHaveAttribute("href", "/repositories/22?state=all");
    await page.keyboard.press("Escape");
    await openNavigation(page);
    const repository = navigation.getByRole("link", { name: repo.full_name, exact: true });
    await expect(repository).toHaveAttribute("aria-current", "page");
    await expect(repository).toHaveAttribute("href", "/repositories/22?state=all");
    await closeNavigation(page);
  }
  for (const path of ["/repositories/22/foobar", "/repositories/22/settings/foobar"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Page not found", exact: true })).toBeVisible();
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Page not found");
    await openNavigation(page);
    await expect(navigation.getByRole("link", { name: repo.full_name, exact: true })).not.toHaveAttribute("aria-current", "page");
    await closeNavigation(page);
  }
});

test("repository settings breadcrumbs omit Configure for members when access fails", async ({ page }) => {
  await mock(page, { role: "member" });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.goto("/repositories/22/settings?tab=limits");
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository access was revoked.");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Repositories");
  await expect(breadcrumb.getByText("Configure", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("form", { name: "Repository configuration" })).toHaveCount(0);
});

test("repository workspace shares inbox filters, groups, and bounded pagination", async ({ page }, info) => {
  await mock(page);
  const completed = { ...run, status: "completed", publication: "published" };
  const items = [pr, ...Array.from({ length: 12 }, (_, index) => ({
    ...pr, id: `merged-${index}`, number: 147 - index, title: `Merged change ${index}`,
    state: "merged", author: "sam", latest_run: completed, open_findings: 0,
  })), { ...pr, id: "closed", title: "Closed proposal", state: "closed", latest_run: null, draft: true, fork: true }];
  await mockRepositoryPulls(page, 22, () => items);
  await page.goto("/repositories/22");
  const count = page.getByRole("heading", { name: "Pull requests", exact: true }).locator("..").getByRole("status");
  await expect(count).toHaveText("1 of 14 pull request");
  await expect(page.getByRole("region", { name: "Review configuration" })).toContainText("Automatic reviews");
  await expect(page.getByRole("link", { name: "View on GitHub" })).toHaveAttribute("href", "https://github.com/acme/payments");
  const merged = page.getByRole("region", { name: "Merged", exact: true });
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(1);
  await expect(page.getByRole("link", { name: /Add retry handling/ }).getByText("open", { exact: true })).toBeVisible();
  await expect(merged).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Closed proposal/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Remove PR state filter" }).click();
  await expect(page).toHaveURL(/\?state=all$/);
  await expect(count).toHaveText("14 pull requests");
  await expect(merged.getByRole("link")).toHaveCount(10);
  await expect(merged.getByRole("link").first().getByText("merged", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ }).getByText("closed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ }).getByText("Draft", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ })).toContainText("Fork · manual review");
  await merged.getByRole("button", { name: "Load more merged" }).click();
  await expect(merged.getByRole("link")).toHaveCount(12);
  await expect(page).toHaveURL(/merged_pages=2/);
  await page.reload();
  await expect(merged.getByRole("link")).toHaveCount(12);
  await expect(count).toHaveText("14 pull requests");
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await expect(page.getByRole("menuitem")).toHaveText(["PR state", "Review status", "Author", "Open findings"]);
  await page.getByRole("menuitem", { name: "Review status", exact: true }).click();
  await page.getByRole("checkbox", { name: "Completed", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/merged_pages/);
  await expect(count).toHaveText("12 of 14 pull requests");
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toHaveCount(0);
  await expect(merged.getByRole("link")).toHaveCount(10);
  await page.getByRole("button", { name: "Remove Review status filter" }).click();
  await page.getByRole("button", { name: "Sort by Updated" }).click();
  await expect(page.getByRole("menuitemradio")).toHaveText(["Updated", "Title", "Author"]);
  await page.getByRole("menuitemradio", { name: "Title", exact: true }).click();
  await expect(page).toHaveURL(/ordering=title/);
  await expect(page.getByRole("button", { name: "Reset ordering", exact: true })).toHaveCount(0);
  await expect(count).toHaveText("14 pull requests");
  await page.getByLabel("Search pull requests").fill("#148");
  await expect(page.getByRole("region", { name: "Merged", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toBeVisible();
  await expect(count).toHaveText("2 of 14 pull requests");
  await page.getByLabel("Search pull requests").fill("unmatched");
  await expect(page.getByRole("heading", { name: "No matching pull requests" })).toBeVisible();
  await expect(count).toHaveText("0 of 14 pull requests");
  await page.getByRole("button", { name: "reset your filters" }).click();
  await expect(page.getByLabel("Search pull requests")).toHaveValue("");
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(count).toHaveText("1 of 14 pull request");
  await expect(merged).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toBeVisible();
  await noOverflow(page);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/repository-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
});

test("repository filters survive configuration and the pull request run round trip", async ({ page }) => {
  await mock(page);
  await page.goto("/repositories/22?q=148&author=maria&findings=open");
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toBeVisible();
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
  await noOverflow(page);
  await (await breadcrumbAncestor(page, repo.full_name)).click();
  await expect(page).toHaveURL(/q=148&state=open&author=maria&findings=open$/);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await page.getByRole("tab", { name: "Run history" }).click();
  await page.getByRole("link", { name: /automatic.*aaaaaaa/ }).click();
  await openNavigation(page);
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: repo.full_name, exact: true })).toHaveAttribute("href", "/repositories/22?q=148&state=open&author=maria&findings=open");
  await closeNavigation(page);
  const back = await breadcrumbAncestor(page, repo.full_name);
  await expect(back).toHaveAttribute("href", "/repositories/22?q=148&state=open&author=maria&findings=open");
  await back.click();
  await expect(page.getByLabel("Search pull requests")).toHaveValue("148");
  await expect(page.getByRole("button", { name: /Edit Author/ })).toContainText("Author");
});

test("repository URLs strip inbox pagination and preserve grouped pages", async ({ page }) => {
  await mock(page);
  const items = Array.from({ length: 12 }, (_, index) => ({ ...pr, id: `pr-${index}`, title: `Change ${index}` }));
  await mockRepositoryPulls(page, 22, () => items);
  await page.goto("/repositories/22?state=all&all_pages=2&active_pages=2");
  await expect(page).toHaveURL(/\?state=all&active_pages=2$/);
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(12);
});

test("repository configuration keeps drafts across sections and restores filtered navigation", async ({ page }, info) => {
  const requests = await mock(page);
  await mockRepositoryPulls(page, 22, () => Array.from({ length: 11 }, (_, index) => ({ ...pr, id: `retry-${index}` })));
  await page.goto("/repositories/22?q=retry&state=all&active_pages=2");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await expect(page).toHaveURL(/repositories\/22\/settings\?q=retry&state=all&active_pages=2$/);
  const effort = page.getByRole("combobox", { name: "Review effort", exact: true });
  await expect(effort).toHaveValue("medium");
  await chooseOption(page, effort, "high");
  await page.getByRole("switch", { name: "Automatic reviews", exact: true }).uncheck();
  await repositorySection(page, "Project context");
  await page.getByRole("textbox", { name: "Project context", exact: true }).fill("Keep this draft while changing sections.");
  await repositorySection(page, "Limits & rules");
  await page.getByLabel("Maximum files", { exact: true }).fill("75");
  await repositorySection(page, "Review behavior");
  await expect(effort).toHaveValue("high");
  await expect(page.getByRole("switch", { name: "Automatic reviews", exact: true })).not.toBeChecked();
  await repositorySection(page, "Project context");
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Keep this draft while changing sections.");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-settings-context-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/repositories\/22\?q=retry&state=all&active_pages=2$/);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository configuration saved." })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-save-toast-${info.project.name}.png` });
  expect(requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    settings: { automatic: false, effort: "high", background: "Keep this draft while changing sections.", max_files: 75 },
  });
});

test("repository configuration validates hidden fields and retains drafts after a failed save", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?tab=limits");
  await page.getByLabel("Timeout (seconds)", { exact: true }).fill("");
  await repositorySection(page, "Project context");
  await page.getByRole("textbox", { name: "Project context", exact: true }).fill("Keep this draft after validation and server errors.");
  await repositorySection(page, "Review behavior");
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).not.toBeVisible();
  expect(await page.getByLabel("Timeout (seconds)", { exact: true }).evaluate((element: HTMLInputElement) => element.willValidate)).toBe(true);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toBeFocused();
  expect(requests).toEqual([]);
  await page.getByLabel("Timeout (seconds)", { exact: true }).fill("1200");
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByRole("combobox", { name: "AI connection", exact: true }), "No connection");
  await repositorySection(page, "Review behavior");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "AI connection", exact: true })).toBeFocused();
  expect(requests).toEqual([]);
  await chooseOption(page, page.getByRole("combobox", { name: "AI connection", exact: true }), "Team reviews");
  await page.route("**/api/v1/repositories/22", (route) => route.fulfill({ status: 422, json: { detail: "Repository settings could not be saved. Try again." } }));
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository settings could not be saved");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Keep this draft after validation and server errors.");
  await repositorySection(page, "Limits & rules");
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toHaveValue("1200");
});

test("repository configuration shows connection failures across every section", async ({ page }, info) => {
  const requests = await mock(page);
  const error = "AI connections could not be loaded. Try again.";
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ status: 503, json: { detail: error } }));
  await page.goto("/repositories/22/settings");
  const form = page.getByRole("form", { name: "Repository configuration" });
  const notice = form.getByRole("alert").filter({ hasText: error });
  for (const section of ["Review behavior", "Project context", "Limits & rules", "AI connection"]) {
    await repositorySection(page, section);
    await expect(notice).toBeVisible();
    await expect(notice).toHaveCount(1);
  }
  await chooseOption(page, page.getByRole("combobox", { name: "AI connection", exact: true }), "No connection");
  await repositorySection(page, "Review behavior");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "AI connection", exact: true })).toBeFocused();
  await expect(notice).toBeVisible();
  await expect(form.getByRole("alert")).toHaveCount(2);
  expect(requests).toEqual([]);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-settings-connection-error-${info.project.name}.png`, fullPage: true, animations: "disabled" });
});

test("repository configuration sections retain navigation and fit both themes", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?q=148&state=all&tab=connection");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Configure");
  const back = await breadcrumbAncestor(page, repo.full_name);
  await expect(back).toHaveAttribute("href", "/repositories/22?q=148&state=all");
  await page.keyboard.press("Escape");
  await openNavigation(page);
  const repository = page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: repo.full_name, exact: true });
  await expect(repository).toHaveAttribute("aria-current", "page");
  await expect(repository).toHaveAttribute("href", "/repositories/22?q=148&state=all");
  await closeNavigation(page);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    for (const name of ["Review behavior", "AI connection", "Project context", "Limits & rules"]) {
      const link = page.getByRole("navigation", { name: "Repository settings sections" }).getByRole("link", { name, exact: true });
      await link.focus();
      await link.press("Enter");
      await expect(link).toHaveAttribute("aria-current", "page");
      const footer = page.getByRole("form", { name: "Repository configuration" }).locator("footer");
      await expect(footer).toHaveCSS("border-top-width", "0px");
      await expect(footer.getByRole("button")).toHaveText(["Save"]);
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/repository-settings-${name.split(" ")[0].toLowerCase()}-${info.project.name}-${theme}.png`, fullPage: true });
    }
  }
  await page.reload();
  await expect(page.getByLabel("Maximum files", { exact: true })).toBeVisible();
  await (await breadcrumbAncestor(page, repo.full_name)).click();
  await expect(page).toHaveURL(/repositories\/22\?q=148&state=all$/);
  expect(requests).toEqual([]);
});

test("selected tabs retain the light style and have a clearer neutral dark surface", async ({ page }, info) => {
  test.setTimeout(60_000);
  await mock(page);
  await page.addInitScript(() => localStorage.setItem("theme", "system"));
  await page.goto("/pulls/pr");
  const rgb = (hex: string) => `rgb(${[1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16)).join(", ")})`;
  for (const theme of themes) {
    await page.evaluate((id) => localStorage.setItem("color-theme", id), theme.id);
    await page.reload();
    for (const mode of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: mode });
      await expect(page.locator("body")).toHaveCSS("color", rgb(theme[mode]["--foreground"]));
      const findings = page.getByRole("tab", { name: /^Findings/ });
      const history = page.getByRole("tab", { name: /^Run history/ });
      await expect(findings).toHaveCSS("color", rgb(theme[mode]["--foreground"]));
      const rendered = await findings.evaluate((tab) => {
        const context = new OffscreenCanvas(1, 1).getContext("2d")!;
        function composite(base: string, color: string) {
          context.clearRect(0, 0, 1, 1);
          for (const value of [base, color]) {
            context.fillStyle = value;
            context.fillRect(0, 0, 1, 1);
          }
          return "#" + Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3).map((value) => value.toString(16).padStart(2, "0")).join("");
        }
        const list = getComputedStyle(tab.closest('[role="tablist"]')!).backgroundColor;
        const style = getComputedStyle(tab);
        const background = composite(list, style.backgroundColor);
        return { list: composite(list, list), background, count: composite(background, getComputedStyle(tab.querySelector("span")!).color) };
      });
      if (mode === "light") {
        expect(rendered.background).toBe(theme.light["--background"]);
        await expect(findings).toHaveCSS("border-color", "rgba(0, 0, 0, 0)");
      } else expect(contrast(rendered.background, rendered.list), theme.label).toBeGreaterThanOrEqual(1.25);
      expect(contrast(rendered.count, rendered.background), `${theme.label} ${mode} count`).toBeGreaterThanOrEqual(4.5);
      await findings.press("ArrowRight");
      await expect(history).toHaveAttribute("aria-selected", "true");
      await history.press("ArrowLeft");
      await expect(findings).toHaveAttribute("aria-selected", "true");
      await page.getByRole("heading", { level: 1 }).click();
      await page.mouse.move(0, 0);
      await noOverflow(page);
      if ((theme.id === "code-mortis" && mode === "light") || theme.id === "aura" || (theme.id === "cobalt2" && mode === "dark")) {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: `../.impeccable/review/neutral-tabs-${info.project.name}-${theme.id}-${mode}.png`, fullPage: true });
        if (info.project.name === "desktop" && theme.id === "aura" && mode === "dark") {
          await page.setViewportSize({ width: 1647, height: 938 });
          await noOverflow(page);
          await page.screenshot({ path: "../.impeccable/review/neutral-tabs-user-1647.png", fullPage: true });
          await page.setViewportSize({ width: 1440, height: 1000 });
        }
      }
    }
  }
});

for (const role of ["admin", "member"]) {
  test(`repository configuration handles unavailable repositories for ${role}`, async ({ page }) => {
    const requests = await mock(page, { role });
    if (role === "member") {
      await page.goto("/repositories/22/settings?tab=limits");
      await expect(page).toHaveURL(/repositories\/22\?state=open$/);
      await expect(page.getByRole("form", { name: "Repository configuration" })).toHaveCount(0);
    }
    for (const [id, repositories] of [[999, [repo]], [22, []]] as const) {
      await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: repositories }));
      await page.goto(`/repositories/${id}/settings?state=all&tab=limits`);
      await expect(page).toHaveURL(new RegExp(`/repositories/${id}\\?state=all$`));
      await expect(page.getByRole("heading", { name: "Repository unavailable" })).toBeVisible();
      await expect(page.getByRole("form", { name: "Repository configuration" })).toHaveCount(0);
    }
    await page.route("**/api/v1/repositories", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
    await page.goto("/repositories/22/settings?tab=limits");
    await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository access was revoked.");
    await expect(page.getByRole("form", { name: "Repository configuration" })).toHaveCount(0);
    expect(requests).toEqual([]);
  });
}

test("repository sync pending state stays with its repository", async ({ page }) => {
  await mock(page);
  const other = { ...repo, id: 23, full_name: "acme/another" };
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, other] }));
  await mockRepositoryPulls(page, 23, () => []);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/repositories/22/sync", async (route) => {
    await held;
    await route.fulfill({ json: { job_id: "sync-job" } });
  });
  await page.goto("/repositories/22");
  await page.getByRole("button", { name: "Sync", exact: true }).click();
  await expect(page.getByRole("button", { name: "Queuing sync…" })).toBeDisabled();
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: other.full_name, exact: true }).click();
  const sync = page.getByRole("button", { name: "Sync", exact: true });
  await expect(sync).toBeEnabled();
  await expect(sync.locator("svg")).not.toHaveClass(/animate-spin/);
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: repo.full_name, exact: true }).click();
  await expect(page.getByRole("button", { name: "Queuing sync…" })).toBeDisabled();
  release();
  await expect(page.getByRole("button", { name: "Sync", exact: true })).toBeEnabled();
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(1);
  await expect(page.locator("[data-sonner-toast]")).toContainText("Sync queued");
});

for (const theme of ["light", "dark"]) {
  test(`repository sync uses a ${theme} toast and keeps failures inline`, async ({ page }, info) => {
    await mock(page);
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    let fail = false;
    await page.route("**/api/v1/repositories/22/sync", (route) => route.fulfill(fail
      ? { status: 502, json: { detail: "GitHub unavailable. Try again shortly." } }
      : { json: { job_id: "sync-job" } }));
    await page.goto("/repositories/22");
    const sync = page.getByRole("button", { name: "Sync", exact: true });
    const toast = page.locator("[data-sonner-toast]");
    await expect(toast).toHaveCount(0);
    await sync.click();
    await expect(toast).toHaveCount(1);
    await expect(toast).toContainText("Sync queued");
    await expect(toast).toContainText("Pull requests will refresh when it finishes.");
    await expect(page.getByRole("main").getByText(/Sync queued/)).toHaveCount(0);
    await expect(toast).not.toBeFocused();
    await expect(page.locator("[data-sonner-toaster]")).toHaveAttribute("data-sonner-theme", theme);
    await expect(page.getByRole("region", { name: /^Notifications/ })).toHaveAttribute("aria-live", "polite");
    await noOverflow(page);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `../.impeccable/review/sync-toast-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await expect(toast.getByRole("button", { name: "Close toast" })).toHaveCount(0);
    await expect(toast).toHaveCount(0);
    fail = true;
    await sync.click();
    await expect(page.getByRole("main").getByRole("alert")).toHaveText("GitHub unavailable. Try again shortly.");
    await expect(toast).toHaveCount(0);
  });
}

test("repository list distinguishes failed requests, empty data, and revoked access", async ({ page }) => {
  await mock(page);
  let status = 502;
  await page.route("**/api/v1/repositories/22/pulls?**", (route) => route.fulfill(status
    ? { status, json: { detail: status === 403 ? "Repository access revoked" : "GitHub unavailable" } }
    : { json: repositoryPage([], new URL(route.request().url()).searchParams) }));
  await page.goto("/repositories/22");
  await expect(page.getByRole("alert")).toContainText("GitHub unavailable");
  await expect(page.getByRole("heading", { name: "No pull requests here yet" })).toHaveCount(0);
  await expect(page.getByText("Loading pull requests…")).toHaveCount(0);
  status = 0;
  await page.getByRole("button", { name: "Retry pull requests" }).click();
  await expect(page.getByRole("heading", { name: "No pull requests here yet" })).toBeVisible();
  status = 403;
  await page.getByRole("button", { name: "Sync", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access revoked");
  await expect(page.getByRole("button", { name: "Retry pull requests" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No pull requests here yet" })).toHaveCount(0);
});

test("repository outdated reviews remain distinct from running reviews and page limits are explicit", async ({ page }) => {
  await mock(page);
  const items = [
    { ...pr, latest_run: { ...run, status: "completed", publication: "published", head_sha: "old" } },
    { ...pr, id: "running", title: "Still running on an earlier commit", state: "merged", latest_run: { ...run, status: "running", head_sha: "old" } },
  ];
  await mockRepositoryPulls(page, 22, () => items);
  await page.goto("/repositories/22?review=outdated");
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toContainText(/outdated/i);
  await expect(page.getByRole("link", { name: /Still running/ })).toContainText("A review is still running");
  await page.goto("/repositories/22?state=closed&review=running");
  await expect(page.getByRole("link", { name: /Still running/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toHaveCount(0);
  await mockRepositoryPulls(page, 22, () => Array.from({ length: 101 }, (_, index) => ({ ...pr, id: `many-${index}`, number: index + 1, title: `Change ${index + 1}`, latest_run: null })));
  await page.goto("/repositories/22?active_pages=999999");
  const open = page.getByRole("region", { name: "Open", exact: true });
  await expect(open.getByRole("link")).toHaveCount(100);
  await expect(open.getByText("Use filters to narrow the remaining pull requests.")).toBeVisible();
  await expect(open.getByRole("button", { name: /Load more/ })).toHaveCount(0);
  await page.getByLabel("Search pull requests").fill("#101");
  await expect(open.getByRole("link")).toHaveCount(1);
  await expect(open.getByRole("link")).toContainText("Change 101");
});

test("repository state filters retain running reviews and reset when switching repository", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/other", enabled: false }] }));
  await mockRepositoryPulls(page, 22, () => [pr, { ...pr, id: "running", title: "Running review", latest_run: { ...run, status: "running" } }]);
  await mockRepositoryPulls(page, 23, () => []);
  await page.goto("/repositories/22?state=closed");
  await expect(page.getByRole("link", { name: /Running review/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toHaveCount(0);
  await openNavigation(page);
  await page.getByRole("link", { name: "acme/other", exact: true }).click();
  await expect(page).toHaveURL(/repositories\/23\?state=open$/);
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  await expect(page.getByRole("region", { name: "Review configuration" }).getByText("Paused", { exact: true })).toBeVisible();
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
  await expect(page.getByText("Not observed in latest review")).toHaveCount(0);
  await expect(page.getByText("View details", { exact: true })).toHaveCount(0);
  const findingCards = page.locator("[data-slot=finding-card]");
  await expect(findingCards.locator('[data-slot=badge]').filter({ hasText: /^Bug$/ }).locator('svg[aria-hidden=true]')).toHaveClass(/lucide-bug/);
  await expect(findingCards.locator('[data-slot=badge]').filter({ hasText: /^Performance$/ }).locator('svg[aria-hidden=true]')).toHaveClass(/lucide-gauge/);
  await noOverflow(page);
  await page.evaluate(() => document.fonts.ready);
  for (const card of await page.locator("[data-slot=finding-card]").all()) {
    const gaps = await card.evaluate((element) => {
      const [header, description, footer] = Array.from(element.children);
      const location = header.children[0].children[1];
      const h = header.getBoundingClientRect(), d = description.getBoundingClientRect(), f = footer.getBoundingClientRect();
      const l = location.getBoundingClientRect(), icon = header.children[0].children[0].getBoundingClientRect();
      return { before: d.top - h.bottom, after: f.top - d.bottom, location: d.top - l.bottom, iconCenter: icon.top + icon.height / 2, locationCenter: l.top + l.height / 2 };
    });
    expect(gaps.before).toBe(16);
    expect(gaps.after).toBe(16);
    expect(gaps.iconCenter).toBeCloseTo(gaps.locationCenter);
    if (info.project.name === "desktop") expect(gaps.location).toBe(gaps.after);
  }
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
      groups: [{ group: "all", total: 1, page: 1, has_more: false, items: [{
        ...pr, repository_name: repo.full_name, created_at: at, review_status: run.status,
        has_running_review: false, run_id: run.id, publication: run.publication, publication_error: run.publication_error,
      }] }],
      total: 1, registered_total: 1, repository_count: 1, repositories: [repo],
      authors: [pr.author], state_counts: { open: 1 }, page_size: 10,
    } }));
    await page.route("**/api/v1/pulls/pr/runs", (route) => route.fulfill({ json: run }));
    const inbox = "/?q=retry&repo=22&state=open&ordering=title";
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
    await expect(page).toHaveURL((url) => url.pathname === "/pulls/pr");
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
  await expect(patch).toContainText("@@ -1,3 +1,3 @@");
  await expect(patch).not.toContainText("--- original");
  await expect(patch).not.toContainText("No newline at end of file");
  await expect(panel.getByLabel("Changed lines")).toHaveText("+2−2");
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

test("pull filters, history tabs, and finding tabs survive reload and browser history", async ({ page }) => {
  await mock(page);
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs: [run],
    findings: [
      { ...finding, status: "resolved", severity: "high", category: "bug" },
      { ...finding, id: "open", status: "open", severity: "high", category: "bug" },
      { ...finding, id: "dismissed", status: "dismissed", severity: "high", category: "bug" },
    ],
  }));
  await page.goto("/pulls/pr?q=retry&status=resolved&severity=high&category=bug");
  const resolved = page.getByRole("region", { name: "Resolved findings", exact: true });
  await expect(resolved.getByRole("button")).toHaveCount(1);
  await expect(page.getByLabel("Search findings", { exact: true })).toHaveValue("retry");
  await expect(page.getByRole("button", { name: "Edit Status: Resolved", exact: true })).toBeVisible();
  await page.reload();
  await expect(resolved.getByRole("button")).toHaveCount(1);
  await page.getByRole("button", { name: "Remove Status filter", exact: true }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("status") === "all");
  await expect(page.getByRole("region", { name: "Dismissed findings", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("button", { name: "Edit Status: Resolved", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Dismissed findings", exact: true })).toHaveCount(0);
  await page.goForward();
  await expect(page.getByRole("region", { name: "Dismissed findings", exact: true })).toBeVisible();
  await resolved.getByRole("button").click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await sheet.getByRole("button", { name: "View activity", exact: true }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("finding") === "finding" && url.searchParams.get("finding_tab") === "activity");
  await page.reload();
  await expect(sheet.getByRole("tab", { name: "Activity 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goBack();
  await expect(sheet.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL((url) => !url.searchParams.has("finding") && !url.searchParams.has("finding_tab") && url.searchParams.get("q") === "retry");
  await page.getByRole("tab", { name: /Run history/ }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("tab") === "history");
  await page.reload();
  await expect(page.getByRole("tab", { name: /Run history/ })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: /^Findings/ }).click();
  await expect(resolved.getByRole("button")).toHaveCount(1);
});

test("run tabs and independent finding and assessment filters survive shared URLs", async ({ page }, info) => {
  const candidates: Schema["CandidateOut"][] = [{ id: "failed", snapshot: { path: "src/client.ts", content: "Timeout proposal.", severity: "low", category: "security" },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", retryable: false }];
  await mock(page, { candidates,
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1, pending: 0, incomplete: 1, published_comments: 0 },
    occurrences: [{ finding_id: "finding", inline_line: 46, snapshot: { ...finding, severity: "high", category: "bug" } }],
  });
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runs/run/candidates") requests.push(url.searchParams);
  });
  await page.goto("/runs/run?q=retry&severity=high&category=bug&tab=assessment&assessment_q=timeout&assessment_outcome=withheld&assessment_severity=low&assessment_category=security");
  const cards = page.getByRole("region", { name: "Candidate assessment", exact: true }).locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(1);
  await expect(page.getByLabel("Search assessments", { exact: true })).toHaveValue("timeout");
  expect(requests.at(-1)?.get("q")).toBe("timeout");
  expect(requests.at(-1)?.getAll("severity")).toEqual(["low"]);
  expect(requests.at(-1)?.getAll("outcome")).toEqual(["withheld"]);
  await page.reload();
  await expect(page.getByRole("tab", { name: /^Assessment/ })).toHaveAttribute("aria-selected", "true");
  await expect(cards).toHaveCount(1);
  await page.getByRole("tab", { name: /^Findings/ }).click();
  await expect(page.getByLabel("Search run findings", { exact: true })).toHaveValue("retry");
  const findingCards = page.getByRole("region", { name: "Findings at this run", exact: true }).getByRole("button");
  await expect(findingCards).toHaveCount(1);
  await page.getByRole("button", { name: "Remove Type filter", exact: true }).click();
  await expect(page).toHaveURL((url) => !url.searchParams.has("category") && url.searchParams.get("assessment_category") === "security");
  await page.goBack();
  await expect(page.getByRole("button", { name: "Edit Type: Bug", exact: true })).toBeVisible();
  await findingCards.click();
  const findingSheet = page.getByRole("dialog", { name: "Finding details" });
  await findingSheet.getByRole("tab", { name: "Current status", exact: true }).click();
  await page.reload();
  await expect(findingSheet.getByRole("tab", { name: "Current status", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  for (const name of ["Resolved", "Coverage", "Details", "Assessment"]) {
    await page.getByRole("tab", { name: new RegExp(`^${name}`) }).click();
    await expect(page).toHaveURL((url) => url.searchParams.get("tab") === name.toLowerCase());
    if (name === "Coverage") {
      await page.goBack();
      await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
      await page.goForward();
      await expect(page.getByRole("tab", { name: "Coverage", exact: true })).toHaveAttribute("aria-selected", "true");
    }
    await page.reload();
    await expect(page.getByRole("tab", { name: new RegExp(`^${name}`) })).toHaveAttribute("aria-selected", "true");
  }
  await expect(cards).toHaveCount(1);
  await cards.click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await sheet.getByRole("tab", { name: "Evidence 0", exact: true }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("candidate") === "failed" && url.searchParams.get("assessment_tab") === "evidence");
  await page.reload();
  await expect(sheet.getByRole("tab", { name: "Evidence 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL((url) => !url.searchParams.has("candidate") && !url.searchParams.has("assessment_tab") && url.searchParams.get("tab") === "assessment");
  await expect(page.getByLabel("Search assessments", { exact: true })).toHaveValue("timeout");
  await noOverflow(page);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.getByRole("region", { name: "Candidate assessment", exact: true }).screenshot({ path: `../.impeccable/review/url-filters-${info.project.name}.png`, animations: "disabled" });
});

test("run resolved tab preserves verification history and a finding's current status", async ({ page }, info) => {
  const requests = await mock(page, {
    canWrite: false, occurrences: [], finding: { status: "open" },
    resolved: [{ ...finding, status: "open", sync_intent: null }],
    verifications: [{
      id: "verification", run_id: "run", head_sha: run.head_sha, status: "completed", verdict: "fixed",
      resolution: "resolved", error: null, created_at: at, finished_at: at,
      result: { verdict: "fixed", original_issue_present: true, dismissal_recommended: false,
        explanation: "The retry loop now stops after three attempts.", change_explanation: "The loop has a fixed attempt limit.", evidence: [] },
    }],
  });
  await page.goto("/runs/run?tab=resolved&finding=finding");
  const tab = page.getByRole("tab", { name: "Resolved 1", exact: true, includeHidden: true });
  await expect(tab).toHaveAttribute("aria-selected", "true");
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByText("open", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("tab", { name: "At this run", exact: true })).toHaveCount(0);
  await sheet.getByRole("button", { name: "View verification", exact: true }).click();
  await expect(sheet).toContainText("The retry loop now stops after three attempts.");
  await expect(sheet).toContainText("Automatic resolution: resolved");
  await page.reload();
  await expect(sheet.getByRole("tab", { name: /^Activity/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(tab).toBeFocused();
  const card = page.getByRole("region", { name: "Findings resolved by this run", exact: true }).getByRole("button");
  await expect(card).toContainText("Currently open");
  await card.focus();
  await page.keyboard.press("Enter");
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/run-resolved-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  expect(requests).toEqual([]);
});

test("run resolved tab waits for GitHub confirmation and updates its count", async ({ page }) => {
  const resolved: Schema["FindingOut"][] = [];
  await mock(page, { resolved, occurrences: [], run: { status: "running", progress: { verification: { pending: 1, resolved: 0, selected: 1 } } } });
  await page.goto("/runs/run?tab=resolved");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "No confirmed resolutions yet", exact: true })).toBeVisible();
  await expect(page.getByText("Fixes appear here after verification and GitHub synchronization complete. Findings absent from a review are not automatically resolved.")).toBeVisible();
  resolved.push({ ...finding, status: "resolved", sync_intent: null });
  await page.reload();
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Findings resolved by this run", exact: true }).getByRole("button")).toContainText("Resolved");
  await noOverflow(page);
});

test("resolved findings use independent backend filters and reset pagination", async ({ page }, info) => {
  const resolved = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `resolved-${String(index).padStart(2, "0")}`,
    path: `src/handler-${index}.ts`, status: "resolved", sync_intent: null,
    severity: index % 2 ? "low" : "high", category: index === 1 ? "custom_rule" : "bug" }));
  await mock(page, { resolved });
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runs/run/resolved") requests.push(url.searchParams);
  });
  await page.goto("/runs/run?tab=resolved&q=retry&severity=high&resolved_q=handler-1&resolved_severity=low&resolved_category=custom_rule");
  const list = page.getByRole("region", { name: "Findings resolved by this run", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("handler-1.ts");
  await expect(page.getByRole("tab", { name: "Resolved 23", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(requests.at(-1)?.get("q")).toBe("handler-1");
  expect(requests.at(-1)?.getAll("severity")).toEqual(["low"]);
  expect(requests.at(-1)?.getAll("category")).toEqual(["custom_rule"]);
  await page.reload();
  await expect(page.getByLabel("Search resolved findings", { exact: true })).toHaveValue("handler-1");
  await page.getByRole("tab", { name: /^Findings/ }).click();
  await expect(page.getByLabel("Search run findings", { exact: true })).toHaveValue("retry");
  await expect(page.getByRole("button", { name: "Edit Severity: High", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Resolved 23", exact: true }).click();
  await page.getByRole("button", { name: "Remove Type filter", exact: true }).click();
  await page.getByRole("button", { name: "Remove Severity filter", exact: true }).click();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await list.getByRole("button", { name: "Load more resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(20);
  await expect(list.getByRole("status")).toHaveText("20 of 23 shown");
  expect(requests.at(-1)?.get("page")).toBe("2");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Low/ }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(10);
  await expect(list.getByRole("status")).toHaveText("10 of 11 shown");
  expect(requests.at(-1)?.get("page")).toBe("1");
  await page.getByLabel("Search resolved findings", { exact: true }).fill("not-found");
  await expect(page.getByRole("heading", { name: "No resolved findings match these filters", exact: true })).toBeVisible();
  await list.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === "retry" && url.searchParams.get("severity") === "high"
    && !url.searchParams.has("resolved_q") && !url.searchParams.has("resolved_severity"));
  await expect(page.getByLabel("Search resolved findings", { exact: true })).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/resolved-filters-${info.project.name}.png`, fullPage: true, animations: "disabled" });
});

for (const status of ["running", "completed"] as const) {
  test(`resolved findings polling respects ${status} run state`, async ({ page }) => {
    await page.clock.install();
    await mock(page, { run: { status }, resolved: [] });
    let reads = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/runs/run/resolved") reads++;
    });
    await page.goto("/runs/run?tab=resolved");
    await expect(page.getByRole("region", { name: "Findings resolved by this run", exact: true }).getByRole("heading")).toBeVisible();
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
    await page.clock.runFor(15000);
    if (status === "running") await expect.poll(() => reads).toBeGreaterThan(1);
    else expect(reads).toBe(1);
  });
}

test("resolved pages preserve loaded cards on failure and hide them on access denial", async ({ page }) => {
  const resolved = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `resolved-${index}`, status: "resolved", sync_intent: null }));
  await mock(page, { resolved });
  let failure = 500;
  await page.route("**/api/v1/runs/run/resolved?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    return failure && Number(params.get("page")) > 1 ? route.fulfill({ status: failure, json: { detail: "Resolved page unavailable" } })
      : route.fulfill({ json: resolvedPage(resolved, params) });
  });
  await page.goto("/runs/run?tab=resolved");
  const list = page.getByRole("region", { name: "Findings resolved by this run", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(10);
  await list.getByRole("button", { name: "Load more resolved findings", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Resolved page unavailable" })).toBeVisible();
  await expect(cards).toHaveCount(10);
  failure = 0;
  await list.getByRole("button", { name: "Retry resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(20);
  failure = 403;
  await list.getByRole("button", { name: "Load more resolved findings", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Resolved page unavailable" })).toBeVisible();
  await expect(cards).toHaveCount(0);
});

test("browser navigation cancels pending review search without changing the restored URL", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  await page.goto("/pulls/pr?q=retry&status=all&severity=high");
  await expect(page.getByRole("button", { name: /retry loop has no upper bound/ })).toBeVisible();
  await page.getByRole("button", { name: "Remove Severity filter", exact: true }).click();
  await expect(page).toHaveURL((url) => !url.searchParams.has("severity"));
  await expect(page.getByRole("button", { name: "Remove Severity filter", exact: true })).not.toBeVisible();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  const search = page.getByLabel("Search findings", { exact: true });
  await search.fill("unfinished draft");
  await page.clock.runFor(100);
  await page.goBack();
  await expect(page).toHaveURL((url) => url.searchParams.get("severity") === "high");
  await expect(search).toHaveValue("retry");
  await page.clock.runFor(500);
  await expect(page).toHaveURL((url) => url.searchParams.get("q") === "retry" && url.searchParams.get("severity") === "high");
});

test("invalid review tabs and URL filters recover to supported defaults", async ({ page }) => {
  await mock(page);
  await page.goto("/pulls/pr?tab=unknown&status=invalid&severity=invalid&category=&finding=finding&finding_tab=snapshot");
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL((url) => !url.searchParams.has("tab") && !url.searchParams.has("finding_tab")
    && !url.searchParams.has("severity") && !url.searchParams.has("category") && url.searchParams.getAll("status").join(",") === "open,resolved");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tab", { name: /^Findings/ })).toHaveAttribute("aria-selected", "true");
});

test("focused finding cards preserve long file locations, state, and keyboard access on narrow screens", async ({ page }) => {
  const file = `${"long-finding-filename-".repeat(4)}.tsx`;
  const directory = `src/${"deeply-nested-directory/".repeat(4)}`;
  const content = `${finding.content} ${"Additional saved evidence. ".repeat(30)}`;
  await page.setViewportSize({ width: 320, height: 844 });
  const requests = await mock(page, {
    run: { status: "completed" },
    finding: { path: directory + file, content, category: "custom_rule", desired_status: "resolved", sync_error: "GitHub unavailable", last_seen_run_id: "previous" },
    occurrences: [{ finding_id: "finding", inline_line: null, snapshot: { ...finding, path: directory + file, content } }],
  });
  await page.goto("/pulls/pr");
  const card = page.getByRole("button", { name: /retry loop has no upper bound/ });
  await expect(card.getByText(file, { exact: true })).toBeVisible();
  await expect(card.getByText(directory, { exact: true })).toBeVisible();
  await expect(card.getByText(file, { exact: true }).locator("..")).toHaveText(directory + file);
  await expect(card).toContainText("42–46");
  await expect(card.getByText("open", { exact: true })).toBeVisible();
  await expect(card).toContainText("Sync needs attention");
  await expect(card.locator("div, p")).toHaveCount(0);
  await expect(card).not.toContainText("Not observed in latest review");
  await expect(card).not.toContainText("View details");
  await expect(card.locator('[data-slot=badge]').filter({ hasText: /^Custom rule$/ }).locator('svg[aria-hidden=true]')).toHaveClass(/lucide-tag/);
  await noOverflow(page);
  await card.press("Space");
  await expect(page.getByRole("dialog", { name: "Finding details" })).toContainText(content);
  await expect(page.getByRole("dialog").locator('[data-slot=badge]').filter({ hasText: /^Custom rule$/ }).locator('svg[aria-hidden=true]')).toHaveClass(/lucide-tag/);
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  await page.goto("/runs/run");
  const saved = page.getByRole("region", { name: "Findings at this run" }).getByRole("button");
  await expect(saved).toContainText("Summary comment");
  await expect(saved.getByText(file, { exact: true })).toBeVisible();
  await expect(saved.getByText(file, { exact: true }).locator("..")).toHaveText(directory + file);
  await expect(saved).not.toContainText("View details");
  await expect(saved).not.toContainText("Sync needs attention");
  await noOverflow(page);
  await saved.press("Enter");
  await expect(page.getByRole("dialog", { name: "Finding details" }).getByRole("tab", { name: "At this run" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(saved).toBeFocused();
  expect(requests).toEqual([]);
});

test("review run workspace filters saved findings and separates original evidence from current status", async ({ page }, info) => {
  const occurrences = Array.from({ length: 6 }, (_, index) => ({
    finding_id: index === 0 ? "finding" : `finding-${index}`, inline_line: index % 2 ? null : 46,
    snapshot: { ...finding, severity: index % 2 ? "medium" : "high", category: index % 2 ? "performance" : "bug", path: `src/webhooks/handler-${index + 1}.ts`,
      content: index === 0 ? finding.content : `Validate webhook ${index + 1} before processing its payload. Invalid input can interrupt delivery.` },
  }));
  await mock(page, {
    run: { status: "completed", publication: "published", publication_error: null, check_pending: false, check_error: null, check_status: "completed", check_conclusion: "success", finished_at: "2026-10-05T13:37:36Z",
      progress: { verification: { selected: 13, completed: 7, resolved: 3, pending: 0, still_present: 2, uncertain: 8, skipped: 0, failed: 6 } },
      report: { manifest: { coverage: { selected: [{ path: "src/retry.ts" }, { path: "src/config.ts" }], completed: [{ path: "src/retry.ts" }], reused: [{ path: "src/config.ts" }], failed: [], waived: [] } } } },
    occurrences, finding: { status: "resolved", content: "Current evidence has been updated after the fix." },
  });
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run completed", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
  await expect(page.locator("main header")).toContainText("17m 36s");
  await expect(page.getByText("The review results were published on GitHub.", { exact: true })).toHaveCount(0);
  const published = page.getByRole("region", { name: "GitHub publication", exact: true }).locator("[data-slot=tooltip-trigger]").filter({ hasText: "Published" });
  await published.hover();
  await expect(page.getByRole("tooltip")).toHaveText("The review results were published on GitHub.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(page.locator("summary").filter({ hasText: "Fix verification" })).toHaveCount(0);
  await expect(page.getByText(/7 of 13 findings verified/)).not.toBeVisible();
  if (info.project.name === "desktop") expect((await page.getByRole("tab", { name: "Findings 6" }).boundingBox())!.y).toBeLessThan(350);
  await expect(page.getByRole("link", { name: `#148 · ${pr.title}` })).toHaveAttribute("href", "/pulls/pr");
  const list = page.getByRole("region", { name: "Findings at this run", exact: true });
  await expect(list.getByRole("button")).toHaveCount(6);
  await expect(page.getByRole("tabpanel").getByRole("status")).toHaveText("6 of 6 findings");
  await expect(page.getByRole("tabpanel").getByRole("status")).toHaveClass("sr-only");
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await noOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `../.impeccable/review/run-workspace-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
  }
  await page.getByRole("tab", { name: "Findings 6" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Assessment", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Coverage", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Reviewed files", exact: true }).getByText("src/config.ts", { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-coverage-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Review setup, execution details, revisions, and the configuration saved for this run.");
  await expect(page.getByRole("tabpanel")).toContainText("Shared connection");
  await expect(page.getByRole("tabpanel")).toContainText(run.head_sha);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-details-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Findings 6" }).click();
  await page.getByRole("textbox", { name: "Search run findings" }).fill("handler-6");
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear search" }).click();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Performance/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Performance/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(list.getByRole("button")).toHaveCount(3);
  await expect(page.getByRole("tabpanel").getByRole("status")).toHaveText("3 of 6 findings");
  await page.getByRole("button", { name: "Remove Type filter", exact: true }).click();
  await expect(list.getByRole("button")).toHaveCount(6);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: /^High/ }).click();
  await expect(page.getByRole("checkbox", { name: /^High/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Edit Severity: High", exact: true })).toBeFocused();
  await expect(list.getByRole("button")).toHaveCount(3);
  const row = list.getByRole("button", { name: /retry loop has no upper bound/ });
  await row.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByRole("tab", { name: "At this run" })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText(finding.content);
  await expect(sheet.getByRole("region", { name: "Suggested change diff" })).toBeVisible();
  await sheet.getByRole("tab", { name: "Current status" }).click();
  await expect(sheet).toContainText("Current evidence has been updated after the fix.");
  await expect(sheet.getByText("resolved", { exact: true })).toBeVisible();
  await sheet.getByRole("tab", { name: "At this run" }).click();
  await expect(sheet).toContainText(finding.content);
  await expect(sheet.getByText("Current evidence has been updated after the fix.")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/run-evidence-${info.project.name}.png`, fullPage: false, animations: "disabled" });
  await page.keyboard.press("Escape");
  await expect(row).toBeFocused();
  await page.getByRole("textbox", { name: "Search run findings" }).fill("no match");
  await expect(page.getByRole("heading", { name: "No findings match these filters" })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(list.getByRole("button")).toHaveCount(6);
});

test("run coverage shows compact file rows and reveals the remaining saved scope", async ({ page }, info) => {
  const completed = Array.from({ length: 11 }, (_, index) => ({ path: `src/payments/handler-${index + 1}.ts`, item_id: `file-${index}` }));
  const reused = { path: "src/payments/retry.ts", reason: "Validated result reused from an earlier run." };
  const failed = { path: "src/payments/context.ts", reason: "Context budget exceeded." };
  const waived = { path: "src/payments/config.ts", reason: "Token budget reached." };
  const missing = { path: `src/${"long-directory/".repeat(8)}unrecorded.ts` };
  await mock(page, { run: { publication: "not_applicable", publication_error: null, check_enabled: false,
    report: { manifest: { coverage: { selected: [...completed, reused, failed, waived, missing], completed, reused: [reused], failed: [failed], waived: [waived] } },
      excluded_files: [{ path: "README.md", exclude_reason: "unsupported_ext" }] } } });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  const coverage = page.getByRole("region", { name: "Coverage", exact: true });
  const reviewed = coverage.getByRole("region", { name: "Reviewed files", exact: true });
  const gaps = coverage.getByRole("region", { name: "Files not reviewed", exact: true });
  await expect(coverage.getByText("See which files were reviewed or reused from earlier runs, and why others were left unreviewed.", { exact: true })).toBeVisible();
  await expect(coverage.getByRole("heading", { name: "Coverage", exact: true })).toHaveCount(0);
  await expect(coverage.getByRole("textbox")).toHaveCount(0);
  await expect(coverage.getByRole("button", { name: "Filters", exact: true })).toHaveCount(0);
  await expect(reviewed.getByRole("listitem")).toHaveCount(10);
  await expect(gaps.getByRole("listitem")).toHaveCount(4);
  await expect(reviewed.getByText("Validated file result saved in this run.", { exact: true })).toHaveCount(0);
  await expect(gaps.getByRole("listitem").filter({ hasText: failed.path })).toContainText(failed.reason);
  await expect(gaps.getByRole("listitem").filter({ hasText: "README.md" })).toContainText("Outside scope");
  await reviewed.getByRole("button", { name: "Load more reviewed files" }).click();
  await expect(reviewed.getByRole("listitem")).toHaveCount(12);
  await expect(reviewed.getByRole("status").last()).toHaveText("12 of 12 shown");
  await expect(reviewed.getByText(completed[10].path, { exact: true })).toBeVisible();
  await expect(reviewed.getByRole("listitem").filter({ hasText: reused.path })).toContainText("Reused");
  await expect(gaps.getByRole("listitem")).toHaveCount(4);
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await noOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `../.impeccable/review/coverage-${info.project.name}-${theme}.png`, animations: "disabled" });
    await page.evaluate((top) => window.scrollTo(0, top), (await gaps.boundingBox())!.y - 76);
    await page.screenshot({ path: `../.impeccable/review/coverage-files-${info.project.name}-${theme}.png`, animations: "disabled" });
  }
  await expect(gaps.getByText(missing.path, { exact: true })).toBeVisible();
  await expect(coverage.getByRole("button", { name: /View coverage/ })).toHaveCount(0);
  await coverage.getByText("Inspect coverage report", { exact: true }).click();
  await expect(coverage.locator("pre")).toContainText(failed.reason);
  await noOverflow(page);
});

for (const example of [
  { name: "legacy report", status: "partial", report: { errors: [{ path: "src/legacy.ts", message: "No saved result" }] }, label: "Coverage unavailable", summary: "Selected review scope was not recorded.", file: "legacy.ts", state: "Not reviewed" },
  { name: "empty selected scope", status: "skipped", report: { manifest: { coverage: { selected: [] } }, excluded_files: [{ path: "README.md", exclude_reason: "unsupported_ext" }] }, label: "No selected files", summary: "0 of 0 selected files reviewed", file: "README.md", state: "Outside scope" },
  { name: "overlapping legacy outcomes", status: "partial", report: { manifest: { coverage: { selected: [{ path: "src/retry.ts" }], completed: [{ path: "src/retry.ts" }], failed: [{ path: "src/retry.ts" }] } } }, label: "Partial coverage", summary: "0 of 1 selected files reviewed", file: "retry.ts", state: "Failed" },
  { name: "pending file evidence", status: "running", report: { manifest: { coverage: { selected: [{ path: "src/pending.ts" }] } } }, label: "Coverage in progress", summary: "0 of 1 selected files reviewed", file: "pending.ts", state: "Awaiting review" },
]) {
  test(`run coverage preserves ${example.name}`, async ({ page }) => {
    await mock(page, { run: { status: example.status, report: example.report, publication: "not_applicable", publication_error: null, check_enabled: false } });
    await page.goto("/runs/run");
    await page.getByRole("tab", { name: "Coverage", exact: true }).click();
    const coverage = page.getByRole("region", { name: "Coverage", exact: true });
    await expect(coverage.getByText(example.label, { exact: true })).toHaveCount(0);
    if (example.name === "legacy report") await expect(coverage.getByText(example.summary, { exact: true })).toBeVisible();
    else await expect(coverage.getByText(example.summary, { exact: true })).toHaveCount(0);
    await expect(coverage.getByRole("listitem").filter({ hasText: example.file })).toContainText(example.state);
    await noOverflow(page);
  });
}

test("run coverage updates file rows through polling and hides them after access is revoked", async ({ page }) => {
  await page.clock.install();
  const state: Partial<Run> = { status: "running", publication: "pending", publication_error: null, check_enabled: false,
    report: { manifest: { coverage: { selected: [{ path: "src/live.ts" }], completed: [] } } } };
  await mock(page, { run: state });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  const row = page.getByRole("region", { name: "Coverage", exact: true }).getByRole("listitem").filter({ hasText: "src/live.ts" });
  await expect(row).toContainText("Awaiting review");
  state.report = { manifest: { coverage: { selected: [{ path: "src/live.ts" }], completed: [{ path: "src/live.ts" }] } } };
  await page.clock.fastForward(5000);
  await expect(row).toContainText("Reviewed");
  await page.route("**/api/v1/runs/run", (route) => route.fulfill({ status: 403, json: { detail: "Repository access revoked" } }));
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert").filter({ hasText: "Repository access revoked" })).toBeVisible();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Coverage", exact: true })).toHaveCount(0);
});

test("review run retains live progress and recovery actions while evidence is pending", async ({ page }, info) => {
  const requests = await mock(page, { run: { status: "running", publication: "pending", publication_error: null, report: null, finished_at: null, check_enabled: false, progress: { selected: 8, completed: 3 } }, occurrences: [], assessment: { candidates: 0, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0, pending: 0, incomplete: 0, published_comments: 0 } });
  await page.goto("/runs/run");
  await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
  await expect(page.getByText("The review is in progress.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("This page updates automatically as results are saved. You can cancel the review if needed.", { exact: true })).toHaveCount(0);
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(publication).toContainText("Waiting to publish");
  await expect(publication.getByText("Comments are published after review results are saved.", { exact: true })).toHaveCount(0);
  await expect(publication.locator(":scope > p")).toHaveCount(0);
  const heading = page.getByRole("heading", { name: "Review run running", exact: true });
  await expect(heading).toBeVisible();
  await expect(heading.locator("[data-slot=badge]")).toHaveText("running");
  await expect(heading.locator("svg")).toHaveClass(/lucide-loader-circle/);
  await expect(page.getByText("3 of 8 assigned files completed.", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review in progress", exact: true })).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/run-status-cleanup-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Inspect coverage", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Coverage is being recorded" })).toBeVisible();
  await noOverflow(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-progress-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Cancel review", exact: true }).click();
  await expect.poll(() => requests.map((request) => request.path)).toEqual(["/runs/run/cancel"]);
});

test("review run hides stale evidence after repository access is revoked", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  await page.goto("/runs/run");
  await expect(page.getByRole("region", { name: "Findings at this run", exact: true })).toBeVisible();
  await page.route("**/api/v1/runs/run", (route) => route.fulfill({ status: 403, json: { detail: "Repository access revoked" } }));
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert").filter({ hasText: "Repository access revoked" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Findings at this run", exact: true })).toHaveCount(0);
});

for (const publication of ["failed", "partially_published"]) {
  test(`running review preserves context when publication is ${publication}`, async ({ page }) => {
    await mock(page, { run: { status: "running", publication, progress: {}, finished_at: null } });
    await page.goto("/runs/run");
    const outcome = page.getByRole("region", { name: "Review outcome" });
    const suffix = publication === "failed" ? "Publication failed." : "Some comments published.";
    await expect(outcome.getByRole("status")).toHaveText(`The review is in progress. ${suffix}`);
    await expect(outcome).toContainText("This page updates automatically as results are saved. Retry GitHub publication");
    await expect(page.getByRole("heading", { name: "Review run running", exact: true })).toBeVisible();
  });
}

test("partial review and publication retry remain distinct", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: /^Review run / })).toBeVisible();
  await expect(page.locator("main header")).toContainText("· 1m 30s");
  await expect(
    page.getByRole("region", { name: "Review outcome" }),
  ).toContainText("Coverage is incomplete.");
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].path).toBe("/runs/run/publish");
  await expect(page.getByRole("link", { name: /^View check on GitHub:/ })).toHaveAttribute("href", run.check_url);
  await expect(page.getByText(run.check_error)).toBeVisible();
  await expect(page.getByText("Check update pending. Review results are saved separately.")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(page.getByText(run.head_sha, { exact: true })).toBeVisible();
  await expect(page.getByText(run.base_sha, { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-details-${info.project.name}.png`, fullPage: true });
  await page.locator("summary", { hasText: "Configuration used" }).press("Enter");
  const config = page.locator("pre .language-json").last();
  await expect(config.locator(".hljs-attr").first()).toHaveText('"connection"');
  expect(await config.textContent()).toBe(JSON.stringify(run.config, null, 2));
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
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
  const check = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(check.getByRole("status")).toHaveCount(0);
  await expect(check.locator(":scope > :empty")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-pending-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = "success";
  checkState.check_pending = false;
  await page.reload();
  await expect(check.getByText("success", { exact: true })).toBeVisible();
  await expect(check.getByRole("status")).toHaveCount(0);
  await check.locator("[data-slot=tooltip-trigger]").filter({ hasText: "success" }).focus();
  await expect(page.getByRole("tooltip")).toContainText("Findings are informational, not approval to merge.");
  await page.keyboard.press("Escape");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-conclusion-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = null;
  await page.reload();
  await expect(page.getByRole("heading", { name: /^Review run / })).toBeVisible();
  await expect(page.getByText("GitHub check", { exact: true })).toHaveCount(0);
  await expect(page.getByText("GitHub comments", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-empty-${info.project.name}.png`, fullPage: true });

  checkState.check_url = run.check_url;
  await page.reload();
  const checkLink = page.getByRole("link", { name: "View check on GitHub: Check", exact: true });
  await expect(checkLink).toHaveText("check");
  await expect(checkLink).toHaveAttribute("href", run.check_url);
  await checkLink.focus();
  await expect(checkLink).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText("The saved GitHub check status is unavailable.");
  await noOverflow(page);
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
  await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue("Review model · review-model");
  await page.getByRole("tab", { name: "Connection", exact: true }).click();
  await expect(page.getByLabel("Replace API key (optional)")).toHaveValue("");
});

test("searchable providers and protocols preserve presets and required model validation", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/providers", (route) => route.fulfill({ json: [
    { name: "openai", displayName: "OpenAI API", protocol: "openai", baseUrl: "https://api.openai.com/v1" },
    { name: "anthropic", displayName: "Anthropic", protocol: "anthropic", baseUrl: "https://api.anthropic.com" },
  ] }));
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection name").fill("Searchable connection");
  const provider = page.getByRole("combobox", { name: "Provider", exact: true });
  await provider.fill("anth");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(1);
  const empty = page.locator('[data-slot="combobox-empty"]');
  await expect(empty).toHaveText("");
  await expect(empty).toHaveCSS("height", "0px");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/combobox-provider-${info.project.name}.png`, animations: "disabled" });
  await provider.press("ArrowDown");
  await provider.press("Enter");
  await expect(provider).toHaveValue("Anthropic");
  await expect(page.getByLabel("API protocol")).not.toBeVisible();
  await expect(page.getByLabel("API base URL")).not.toBeVisible();
  await page.getByText("Advanced provider settings", { exact: true }).click();
  const protocol = page.getByRole("combobox", { name: "API protocol", exact: true });
  await expect(protocol).toHaveValue("anthropic");
  await expect(page.getByLabel("API base URL")).toHaveValue("https://api.anthropic.com");
  await provider.fill("unavailable-provider");
  await expect(page.getByText("No matches found.", { exact: true })).toBeVisible();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(0);
  await provider.fill("anth");
  await expect(page.getByRole("option", { name: "Anthropic", exact: true })).toBeVisible();
  await expect(empty).toHaveText("");
  await expect(empty).toHaveCSS("height", "0px");
  await provider.press("Escape");
  await expect(provider).toHaveValue("Anthropic");
  await expect(provider).toBeFocused();
  await provider.fill("");
  await expect(page.getByRole("listbox")).toBeVisible();
  await provider.press("Escape");
  await expect(provider).toHaveValue("Anthropic");
  await chooseOption(page, protocol, "anthropic-bedrock");
  await expect(page.getByLabel("API base URL")).toHaveCount(0);
  const save = page.getByRole("button", { name: "Save connection", exact: true });
  await save.click();
  expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
  await chooseOption(page, page.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await save.click();
  expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
  await page.getByLabel("Custom model ID").fill("regional-model");
  await save.click();
  await expect.poll(() => requests.find((request) => request.path === "/connections")?.body).toMatchObject({ provider: "anthropic", protocol: "anthropic-bedrock", model: "regional-model" });
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection created." })).toBeVisible();
});

test("repository language searches suggestions and preserves custom language entry", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const language = page.getByRole("combobox", { name: "Language", exact: true });
  await language.fill("span");
  await expect(page.getByRole("option", { name: "Spanish", exact: true })).toBeVisible();
  await expect(page.getByText("Use the language you entered.", { exact: true })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/combobox-language-${info.project.name}.png`, animations: "disabled" });
  await language.press("ArrowDown");
  await language.press("Enter");
  await expect(language).toHaveValue("Spanish");
  await language.fill("Eng");
  await page.getByRole("option", { name: "English", exact: true }).click();
  await expect(language).toHaveValue("English");
  await language.fill("span");
  await page.getByRole("option", { name: "Spanish", exact: true }).click();
  await expect(language).toHaveValue("Spanish");
  await language.fill("Eng");
  await language.press("Escape");
  await expect(language).toHaveValue("Spanish");
  await language.fill("");
  await expect(page.getByRole("listbox")).toBeVisible();
  await language.press("Escape");
  await expect(language).toHaveValue("Spanish");
  await language.fill("   ");
  await language.press("Tab");
  await expect(language).toHaveValue("Spanish");
  expect(requests.filter((request) => request.path === "/repositories/22")).toHaveLength(0);
  await language.fill("  Mapudungun  ");
  await expect(page.getByText("Use the language you entered.", { exact: true })).toBeVisible();
  await language.press("Tab");
  await expect(language).toHaveValue("Mapudungun");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ settings: { language: "Mapudungun" } });
});

for (const search of ["", "   "]) {
  for (const close of ["Tab", "outside", "Save"] as const) {
    test(`${search ? "whitespace" : "empty"} language search preserves the saved value on ${close}`, async ({ page }) => {
      const requests = await mock(page);
      await page.goto("/repositories/22");
      await page.getByRole("button", { name: "Configure", exact: true }).click();
      const language = page.getByRole("combobox", { name: "Language", exact: true });
      await language.fill(search);
      if (close !== "Save") {
        if (close === "Tab") await language.press("Tab");
        else await page.getByRole("heading", { name: "Repository configuration", exact: true }).click();
        await expect(language).toHaveValue("English");
      }
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ settings: { language: "English" } });
    });
  }
}

for (const commit of ["Enter", "Save"] as const) {
  test(`custom language commits a trimmed value with ${commit}`, async ({ page }) => {
    const requests = await mock(page);
    await page.goto("/repositories/22");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
    const language = page.getByRole("combobox", { name: "Language", exact: true });
    await language.fill("  Mapudungun  ");
    await expect(page.getByText("Use the language you entered.", { exact: true })).toBeVisible();
    if (commit === "Enter") {
      await language.press("Enter");
      await expect(language).toHaveValue("Mapudungun");
      expect(requests.filter((request) => request.path === "/repositories/22")).toHaveLength(0);
    }
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ settings: { language: "Mapudungun" } });
  });
}

test("closed custom language input submits the configuration with Enter", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const language = page.getByRole("combobox", { name: "Language", exact: true });
  await language.fill("Mapudungun");
  await language.press("Enter");
  await expect(language).toHaveAttribute("aria-expanded", "false");
  expect(requests.filter((request) => request.path === "/repositories/22")).toHaveLength(0);
  await language.press("Enter");
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ settings: { language: "Mapudungun" } });
});

for (const available of [true, false]) {
  test(`repository connection preserves its saved value while loading and ${available ? "restores the name" : "handles missing connections"}`, async ({ page }) => {
    const requests = await mock(page);
    const modelRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname.endsWith("/connections/connection/models")) modelRequests.push(request.url());
    });
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    await page.route("**/api/v1/connections?repository_id=22", async (route) => {
      await held;
      await route.fulfill({ json: available ? [connection] : [] });
    });
    await page.goto("/repositories/22");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "AI connection");
    const picker = page.getByRole("combobox", { name: "AI connection", exact: true });
    await expect(picker).toBeDisabled();
    await expect(picker).toHaveValue("Loading connections…");
    await expect(picker).toHaveAttribute("placeholder", "Loading connections…");
    expect(modelRequests).toHaveLength(0);
    release();
    await expect(picker).toBeEnabled();
    if (available) {
      await expect(picker).toHaveValue("Team reviews");
    } else {
      await expect(picker).toHaveValue("Connection unavailable");
      expect(modelRequests).toHaveLength(0);
      await repositorySection(page, "Review behavior");
    await page.getByRole("switch", { name: "Enable reviews", exact: true }).click();
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ enabled: false, connection_id: "connection" });
      await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "AI connection");
    }
    await repositorySection(page, "Review behavior");
    await page.getByRole("switch", { name: "Enable reviews", exact: true }).click();
    await repositorySection(page, "AI connection");
    await chooseOption(page, picker, "No connection");
    await expect(picker).toHaveValue("");
    await picker.fill("No connection");
    await expect(page.getByRole("option", { name: "No connection", exact: true })).toHaveAttribute("aria-selected", "true");
    await picker.press("Escape");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => requests.filter((request) => request.path === "/repositories/22").at(-1)?.body).toMatchObject({ enabled: false, connection_id: null });
  });
}

test("review connection stays closed after a running review disables it", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let status: Run["status"] = "completed";
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, runs: [{ ...run, status }], findings: [], can_write: true }));
  await page.goto("/pulls/pr");
  const picker = page.getByRole("combobox", { name: "Review connection", exact: true });
  await picker.fill("Team");
  await expect(page.getByRole("listbox")).toBeVisible();
  status = "running";
  await page.clock.fastForward(6000);
  await expect(picker).toBeDisabled();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  status = "completed";
  await page.clock.fastForward(6000);
  await expect(picker).toBeEnabled();
  await expect(picker).toHaveValue("");
  await expect(picker).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await picker.fill("Team");
  await expect(page.getByRole("option", { name: "Team reviews", exact: true })).toBeVisible();
});

test("pull request connection search preserves the default and submits the selected connection", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [connection, { ...connection, id: "research", name: "Research reviews" }] }));
  await page.goto("/pulls/pr");
  const picker = page.getByRole("combobox", { name: "Review connection", exact: true });
  await expect(picker).toHaveAttribute("placeholder", "Repository default");
  await picker.fill("Research");
  await expect(page.getByRole("option")).toHaveCount(1);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/combobox-connection-${info.project.name}.png`, animations: "disabled" });
  await page.getByRole("option", { name: "Research reviews", exact: true }).click();
  await expect(picker).toHaveValue("Research reviews");
  await chooseOption(page, picker, "Repository default");
  await expect(picker).toHaveValue("");
  await picker.fill("Repository default");
  await expect(page.getByRole("option", { name: "Repository default", exact: true })).toHaveAttribute("aria-selected", "true");
  await picker.press("Escape");
  await chooseOption(page, picker, "Research reviews");
  await page.getByRole("button", { name: "Run review", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/pulls/pr/runs")?.body).toEqual({ connection_id: "research" });
});

test("saved model selection refreshes without replacing custom IDs", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expectOption(page, model, "Next model");
  await chooseOption(page, model, "next-model");
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  expect(requests.find((request) => request.path === "/connections/models")?.body).toEqual({
    kind: "api", provider: "openai", protocol: "openai", base_url: "https://api.openai.com/v1",
    options: { prompt_token_ceiling: 58888 }, connection_id: "connection",
  });
  await expect(model).toHaveValue("Next model · next-model");
  await chooseOption(page, model, "Enter a custom model ID");
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

test("API drafts load models before saving and support keyboard selection", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/connection-api-${info.project.name}.png` });
  await page.getByLabel("Connection name").fill("New connection");
  await page.getByLabel("API key", { exact: true }).fill("draft-key");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expectOption(page, model, "Review model");
  expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
  expect(requests.find((request) => request.path === "/connections/models")?.body).toMatchObject({ api_key: "draft-key" });
  await model.fill("review-model");
  await model.press("ArrowDown");
  await model.press("Enter");
  await expect(model).toHaveValue("Review model · review-model");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections")?.body).toMatchObject({ model: "review-model", api_key: "draft-key" });
});

for (const editing of [false, true]) {
  test(`API ${editing ? "edits" : "drafts"} clear credentials and the previous model on provider changes`, async ({ page }, info) => {
    const requests = await mock(page);
    await page.route("**/api/v1/providers", (route) => route.fulfill({ json: [
      { name: "openai", displayName: "OpenAI API", protocol: "openai", baseUrl: "https://api.openai.com/v1" },
      { name: "custom", displayName: "Custom endpoint", protocol: "openai", baseUrl: "" },
    ] }));
    await page.goto("/settings");
    if (editing) {
      await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
      await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "Add connection", exact: true }).click();
    }
    await page.getByLabel("Connection name").fill("Custom reviews");
    const key = page.getByLabel(editing ? "Replace API key (optional)" : "API key", { exact: true });
    if (editing) await page.getByRole("tab", { name: "Connection", exact: true }).click();
    await key.fill("draft-key");
    if (editing) await page.getByRole("tab", { name: "Advanced", exact: true }).click();
    else await page.getByText("Advanced provider settings", { exact: true }).click();
    const headers = page.getByLabel("Extra headers JSON");
    await headers.fill('{"Authorization":"draft-header"}');
    if (editing) await page.getByRole("tab", { name: "Connection", exact: true }).click();
    await page.getByLabel("Provider", { exact: true }).click();
    await page.getByRole("option", { name: "OpenAI API", exact: true }).click();
    await expect(key).toHaveValue("draft-key");
    await expect(headers).toHaveValue('{"Authorization":"draft-header"}');
    if (editing) await page.getByRole("tab", { name: "General", exact: true }).click();
    await page.getByRole("button", { name: /^(Load|Refresh) models$/, exact: true }).click();
    const model = page.getByLabel("Model", { exact: true });
    await chooseOption(page, model, "next-model");
    if (editing) await page.getByRole("tab", { name: "Connection", exact: true }).click();
    await chooseOption(page, page.getByLabel("Provider", { exact: true }), "Custom endpoint");
    await expect(model).toHaveValue("");
    await expect(key).toHaveValue("");
    await expect(headers).toHaveValue("");
    await expect(page.getByLabel("API protocol", { exact: true })).toBeVisible();
    await expect(page.getByLabel("API base URL")).toBeVisible();
    await page.getByLabel("API base URL").fill("https://custom.example/v1");
    await page.getByRole("button", { name: "Save connection", exact: true }).click();
    const path = editing ? "/connections/connection" : "/connections";
    expect(requests.filter((request) => request.path === path)).toHaveLength(0);
    await page.screenshot({ path: info.outputPath("cleared-provider-credentials.png"), fullPage: true });
    if (editing) await page.getByRole("tab", { name: "Connection", exact: true }).click();
    await key.fill("custom-key");
    if (editing) await page.getByRole("tab", { name: "General", exact: true }).click();
    await chooseOption(page, model, "Enter a custom model ID");
    await page.getByLabel("Custom model ID").fill("custom-model");
    await page.getByRole("button", { name: "Save connection", exact: true }).click();
    await expect.poll(() => requests.find((request) => request.path === path)?.body).toMatchObject({
      provider: "custom", protocol: "openai", base_url: "https://custom.example/v1", model: "custom-model",
      api_key: "custom-key",
    });
    expect(requests.find((request) => request.path === path)?.body).not.toHaveProperty("extra_headers");
  });
}

test("large model catalogs search labels and retain the selected model ID", async ({ page }) => {
  const requests = await mock(page);
  await page.route("**/api/v1/connections/models", (route) => route.fulfill({ json: {
    source: "provider", status: "ready",
    models: Array.from({ length: 1000 }, (_, index) => ({ id: `catalog-${index}`, name: `Model ${index}` })),
  } }));
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection name").fill("Catalog connection");
  await page.getByLabel("API key", { exact: true }).fill("draft-key");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await model.fill("Model 987");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(1);
  await page.getByRole("option", { name: "Model 987 · catalog-987", exact: true }).click();
  await expect(model).toHaveValue("Model 987 · catalog-987");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/connections")?.body).toMatchObject({ model: "catalog-987" });
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
  await page.getByText("Advanced provider settings", { exact: true }).click();
  await page.getByLabel("API base URL").fill("https://new.example/v1");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expectOption(page, model, "new-model", 1);
  const late = page.waitForResponse((response) => response.url().endsWith("/connections/models") && response.request().postDataJSON().base_url === "https://api.openai.com/v1");
  release();
  await late;
  await expectOption(page, model, "old-model", 0);
  await expectOption(page, model, "new-model", 1);
  await chooseOption(page, model, "new-model");
  await page.route("**/api/v1/connections/models", (route) => route.fulfill({ json: { source: "provider", status: "unsupported", message: "This endpoint does not support model discovery. Enter a custom model ID.", models: [] } }));
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByText("This endpoint does not support model discovery. Enter a custom model ID.")).toBeVisible();
  await expect(model).toHaveValue("new-model");
  await chooseOption(page, model, "Enter a custom model ID");
  await page.getByLabel("Custom model ID").fill("fallback");
  await expect(page.getByLabel("Custom model ID")).toHaveValue("fallback");
});

test("repository model choices follow its selected connection", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection", model: "second-model" }] }));
  await page.route("**/api/v1/connections/second/models?repository_id=22", (route) => route.fulfill({ json: { source: "provider", status: "ready", models: [{ id: "second-model", name: "Second model" }] } }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "AI connection");
  const model = page.getByLabel("Model override (optional)");
  await expect(model).toHaveAttribute("placeholder", "review-model");
  await expectOption(page, model, "next-model", 1);
  await chooseOption(page, model, "next-model");
  const aiConnection = page.getByRole("combobox", { name: "AI connection", exact: true });
  await aiConnection.fill("second");
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(1);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-connection-search-${info.project.name}.png`, fullPage: true });
  await aiConnection.press("ArrowDown");
  await aiConnection.press("Enter");
  await expect(aiConnection).toHaveValue("Second connection");
  await expect(model).toHaveValue("");
  await expect(model).toHaveAttribute("placeholder", "second-model");
  await expectOption(page, model, "second-model", 2);
  await expectOption(page, model, "next-model", 0);
  await model.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/connection-default-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    connection_id: "second", settings: { model: "" },
  });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "No connection");
  await expect(model).toBeDisabled();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect(requests.filter((request) => request.path === "/repositories/22")).toHaveLength(1);
  await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
});

test("project background generates an editable draft with unsaved selections and Undo", async ({ page }, info) => {
  const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "running", background: null, error: null };
  const requests = await mock(page, { background });
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection" }] }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Project context");
  const field = page.getByRole("textbox", { name: "Project context", exact: true });
  await field.fill("Existing context");
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "Second connection");
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Existing context");
  await repositorySection(page, "Project context");
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Changing the context, connection, model, or limits will discard this result.");
  await repositorySection(page, "Project context");
  await expect(field).toHaveAttribute("maxlength", "20000");
  expect(requests).toEqual([{ path: "/repositories/22/background", method: "POST", body: { connection_id: "second", model: "next-model", limits: { token_budget: 200000, timeout_seconds: 900 } }, csrf: "csrf-from-session" }]);
  background.background = Array.from({ length: 40 }, (_, index) => `Module ${index + 1}: A payment service built with TypeScript. Workers deliver webhooks and handle retries.`).join("\n\n");
  background.status = "done";
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue(background.background);
  await expect(page.getByRole("status", { name: "Context generation status" }).filter({ hasText: "Context generated." })).toBeVisible();
  const editor = await field.evaluate((element: HTMLTextAreaElement) => ({ height: element.clientHeight, contentHeight: element.scrollHeight }));
  expect(editor.height).toBeLessThanOrEqual(384);
  expect(editor.contentHeight).toBeGreaterThan(editor.height);
  await field.evaluate((element: HTMLTextAreaElement) => { element.scrollTop = element.scrollHeight; });
  expect(await field.evaluate((element: HTMLTextAreaElement) => element.scrollTop)).toBeGreaterThan(0);
  await noOverflow(page);
  await field.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/project-background-${info.project.name}.png`, fullPage: true });
  expect(requests).toHaveLength(1);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Existing context");
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toHaveCount(0);
  await repositorySection(page, "Project context");
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Previous context restored.");
  background.id = "second-background-job";
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue(background.background);
  await field.fill("Edited generated context");
  await page.getByRole("button", { name: "Save", exact: true }).click();
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
  await repositorySection(page, "Project context");
      const field = page.getByRole("textbox", { name: "Project context", exact: true });
      await field.fill("Original context");
      await repositorySection(page, "Project context");
      await page.getByRole("button", { name: "Generate context", exact: true }).click();
      if (change === "background") await field.fill("Keep this edit");
      if (change === "model") await repositorySection(page, "AI connection");
      if (change === "model") await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
      if (change === "connection") await repositorySection(page, "AI connection");
      if (change === "connection") await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "No connection");
      if (change === "token budget" || change === "timeout") {
        await repositorySection(page, "Limits & rules");
        await page.getByLabel(change === "timeout" ? "Timeout (seconds)" : "Token budget", { exact: true })
          .fill(change === "timeout" ? "1200" : "450000");
      }
      background.background = "Stale generated context";
      background.error = "Failure from the old selections";
      background.status = outcome;
      await repositorySection(page, "Project context");
      await expect(page.getByRole("status", { name: "Context generation status" }).filter({ hasText: "Your selections or context changed." })).toBeVisible();
      await repositorySection(page, "Project context");
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
  await repositorySection(page, "Project context");
      await repositorySection(page, "Project context");
      await page.getByRole("button", { name: "Generate context", exact: true }).click();
      if (outcome === "done") await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Context generated.");
      else {
        await expect(page.getByRole("alert")).toContainText(background.error!);
        await expect(page.getByText("Generation reference: feedback-job")).toBeVisible();
      }
      if (change === "background") await page.getByRole("textbox", { name: "Project context", exact: true }).fill("New manual text");
      if (change === "model") await repositorySection(page, "AI connection");
      if (change === "model") await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
      if (change === "connection") await repositorySection(page, "AI connection");
      if (change === "connection") await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "No connection");
      if (change === "limits") {
        await repositorySection(page, "Limits & rules");
        await page.getByLabel("Timeout (seconds)", { exact: true }).fill("1200");
      }
      await repositorySection(page, "Project context");
      await expect(page.getByRole("status", { name: "Context generation status" })).toBeEmpty();
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
  await repositorySection(page, "Project context");
    await repositorySection(page, "Project context");
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Recovered draft");
    expect(polls).toEqual(Array(3).fill("/api/v1/repositories/22/background/retry-job"));
    expect(requests).toHaveLength(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await repositorySection(page, "Project context");
    await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Context generated.");
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
  await repositorySection(page, "Project context");
    const sent = page.waitForRequest((request) => new URL(request.url()).pathname === path);
    await repositorySection(page, "Project context");
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
    await sent;
    const field = page.getByRole("textbox", { name: "Project context", exact: true });
    await field.fill("Preserve this edit");
    release();
    await repositorySection(page, "Project context");
    await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Your selections or context changed.");
    await repositorySection(page, "Project context");
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
  await repositorySection(page, "Project context");
  const field = page.getByRole("textbox", { name: "Project context", exact: true });
  const generate = page.getByRole("button", { name: "Generate context", exact: true });
  await field.fill("Keep my context");
  await generate.click();
  await expect(page.getByRole("alert")).toContainText("Reconnect your account before retrying.");
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Keep my context");
  await repositorySection(page, "Project context");
  await expect(generate).toBeEnabled();
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "No connection");
  await repositorySection(page, "Project context");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose an AI connection to generate context.")).toBeVisible();
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "No model");
  await repositorySection(page, "Project context");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose a model to generate context.")).toBeVisible();
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
  await repositorySection(page, "Project context");
  await expect(generate).toBeEnabled();
});

test("project background preserves text when starting or polling fails", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories/22/background", (route) => route.fulfill({ status: 422, json: { detail: "Authorize this repository first." } }));
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Project context");
  const field = page.getByRole("textbox", { name: "Project context", exact: true });
  await field.fill("Keep my context");
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Authorize this repository first.");
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Keep my context");
  await page.route("**/api/v1/repositories/22/background", (route) => route.fulfill({ status: 202, json: { id: "job", status: "queued" } }));
  await page.route("**/api/v1/repositories/22/background/job", (route) => route.fulfill({ status: 403, json: { detail: "Repository access revoked." } }));
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access revoked.");
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Keep my context");
});

for (const unlimited of [false, true]) {
  test(`project background uses unsaved ${unlimited ? "unlimited" : "custom"} review limits`, async ({ page }) => {
    const background: Schema["BackgroundJobOut"] = { id: "limits-job", status: "running", background: null, error: null };
    const requests = await mock(page, { background });
    await page.goto("/repositories");
    await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Project context");
    await repositorySection(page, "Limits & rules");
    if (unlimited) {
      await page.getByLabel("Unlimited token budget", { exact: true }).check();
      await page.getByLabel("Unlimited timeout (seconds)", { exact: true }).check();
    } else {
      await page.getByLabel("Token budget", { exact: true }).fill("450000");
      await page.getByLabel("Timeout (seconds)", { exact: true }).fill("1200");
    }
    await repositorySection(page, "Project context");
    await expect(page.getByText(/Generation uses these review limits:/)).toContainText(
      unlimited ? "unlimited tokens, no overall timeout" : "450,000 tokens, 1,200-second timeout",
    );
    await repositorySection(page, "Project context");
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
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
  await repositorySection(page, "Project context");
  const field = page.getByRole("textbox", { name: "Project context", exact: true });
  await field.fill("Keep my existing project background.");
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(error);
  await expect(page.getByText(`Generation reference: ${background.id}`)).toBeVisible();
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("Keep my existing project background.");
  expect(requests).toHaveLength(1);
  await noOverflow(page);
  await page.getByRole("alert").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/project-background-error-${info.project.name}.png`, fullPage: true });
});

test("review limits save and reopen independent unlimited choices", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("color-theme", "code-mortis"));
  await mock(page);
  let current = structuredClone(repo) as Schema["RepositoryOut"];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/repositories/22", async (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    await route.fulfill({ json: current });
  });
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Limits & rules");
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
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
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
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: 1200, token_budget: 200000, max_files: 40 });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Limits & rules");
  await expect(time).toHaveValue("1200");
  await unlimitedTokens.check();
  await unlimitedFiles.check();
  await unlimitedTime.check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: null });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Limits & rules");
  await expect(unlimitedTime).toBeChecked();
  await expect(unlimitedTokens).toBeChecked();
  await expect(unlimitedFiles).toBeChecked();
  await unlimitedFiles.uncheck();
  await expect(files).toHaveValue("40");
  await noOverflow(page);
  await time.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("body")).toHaveCSS("color", "rgb(232, 238, 233)");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: 40 });
});

test("personal review limits save and reopen the shared unlimited token budget", async ({ page }, info) => {
  await mock(page, { personal: true });
  let current = structuredClone(repo) as Schema["RepositoryOut"];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/repositories/22", async (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    await route.fulfill({ json: current });
  });
  await page.goto("/repositories");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "AI connection");
  await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "My ChatGPT");
  await repositorySection(page, "Limits & rules");
  await expect(page.getByLabel("Native context budget (bytes)", { exact: true })).toHaveCount(0);
  const budget = page.getByLabel("Token budget", { exact: true });
  const unlimited = page.getByRole("checkbox", { name: "Unlimited token budget", exact: true });
  await expect(budget).toHaveValue("200000");
  await page.getByRole("checkbox", { name: "Unlimited timeout (seconds)" }).check();
  await page.getByRole("checkbox", { name: "Unlimited maximum files" }).check();
  await expect(budget).toBeEnabled();
  await budget.fill("300000");
  await unlimited.check();
  await expect(budget).toBeDisabled();
  await expect(budget).toHaveValue("");
  await unlimited.uncheck();
  await expect(budget).toHaveValue("300000");
  await unlimited.check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings.token_budget).toBeNull();
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await repositorySection(page, "Limits & rules");
  await expect(unlimited).toBeChecked();
  await expect(budget).toBeDisabled();
  await expect(budget).toHaveValue("");
  await noOverflow(page);
  await budget.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/subscription-token-budget-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await unlimited.uncheck();
  await expect(budget).toHaveValue("200000");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings.token_budget).toBe(200000);
});

test("finding sheet keeps actions visible and preserves its active tab through polling", async ({ page }) => {
  const requests = await mock(page, { finding: { content: `${finding.content}\n\n` + "Long saved evidence.\n\n".repeat(50) } });
  await page.clock.install();
  await page.goto("/pulls/pr");
  const trigger = page.getByRole("button", { name: /retry loop has no upper bound/ });
  await trigger.click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await sheet.locator('[data-slot="finding-scroll"]').evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(sheet.getByRole("button", { name: "Resolve finding", exact: true })).toBeInViewport();
  await expect(sheet.getByRole("link", { name: "GitHub thread", exact: true })).toBeInViewport();
  await sheet.getByRole("button", { name: "View activity", exact: true }).click();
  const activity = sheet.getByRole("tab", { name: "Activity 1", exact: true });
  await expect(activity).toBeFocused();
  await expect(activity).toHaveAttribute("aria-selected", "true");
  await page.clock.fastForward(5000);
  await expect(activity).toHaveAttribute("aria-selected", "true");
  await activity.press("ArrowLeft");
  await expect(sheet.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await sheet.getByRole("button", { name: "Dismiss", exact: true }).click();
  const reason = sheet.getByRole("textbox", { name: "Why dismiss this finding?" });
  await expect(reason).toBeFocused();
  await expect(sheet.getByRole("button", { name: "Confirm dismissal", exact: true })).toBeDisabled();
  await reason.fill("This behavior is intentional and covered by tests.");
  await sheet.getByRole("button", { name: "Confirm dismissal", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/findings/finding", method: "PATCH", body: { status: "dismissed", reason: "This behavior is intentional and covered by tests." } });
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
});

test("saved finding evidence remains readable when current status cannot be loaded", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/findings/finding", (route) => route.fulfill({ status: 503, json: { detail: "Current finding unavailable" } }));
  await page.goto("/runs/run");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByRole("tab", { name: "At this run", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText(finding.content);
  await expect(sheet.getByRole("region", { name: "Suggested change diff" })).toBeVisible();
  await expect(sheet.getByRole("alert")).toContainText("Current finding unavailable");
  await expect(sheet.getByRole("button", { name: "Resolve finding", exact: true })).toHaveCount(0);
  await sheet.getByRole("tab", { name: "Current status", exact: true }).click();
  await expect(sheet).toContainText("Current evidence could not be loaded.");
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("Bug", { exact: true })).toHaveCount(0);
  await sheet.getByRole("tab", { name: "At this run", exact: true }).click();
  await expect(sheet).toContainText(finding.content);
  await noOverflow(page);
});

test("current finding header waits for current evidence instead of showing the snapshot", async ({ page }) => {
  await mock(page, { finding: { path: "src/current.ts", severity: "low", category: "performance" } });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/findings/finding", async (route) => { await pending; await route.fallback(); });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  await sheet.getByRole("tab", { name: "Current status", exact: true }).click();
  await expect(sheet.getByRole("status")).toContainText("Loading workspace");
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("Bug", { exact: true })).toHaveCount(0);
  release();
  await expect(sheet.getByText("low", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Performance", { exact: true })).toBeVisible();
  await expect(sheet).toContainText("current.ts");
  await sheet.getByRole("tab", { name: "At this run", exact: true }).click();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
});

for (const field of [
  { label: "Token budget", key: "token_budget", personal: true, valid: "6000", excessive: "5000001" },
  { label: "Concurrency", key: "concurrency", personal: false, valid: "4", excessive: "9" },
] as const) {
  test(`repository ${field.label} keeps invalid drafts out of saved settings`, async ({ page }) => {
    const requests = await mock(page);
    await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, {
      ...connection, id: "personal", name: "My ChatGPT", kind: "chatgpt", owner_id: 7, protocol: "native",
    }] }));
    await page.goto("/repositories/22/settings?tab=connection");
    if (field.personal) await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "My ChatGPT");
    await repositorySection(page, "Limits & rules");
    const input = page.getByLabel(field.label, { exact: true });
    for (const invalid of ["", "0", field.excessive, "1e309"]) {
      await input.fill(invalid);
      await expect(input).toHaveValue(invalid === "1e309" ? "" : invalid);
      expect(await input.evaluate((element: HTMLInputElement) => element.validity.valid)).toBe(false);
      await repositorySection(page, "Review behavior");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(input).toBeFocused();
      expect(requests).toEqual([]);
    }
    await input.clear();
    await input.pressSequentially(field.valid);
    await expect(input).toHaveValue(field.valid);
    await input.fill("0");
    await repositorySection(page, "AI connection");
    await chooseOption(page, page.getByLabel("AI connection", { exact: true }), field.personal ? "Team reviews" : "My ChatGPT");
    if (field.personal) {
      await repositorySection(page, "Limits & rules");
      await expect(input).toHaveValue("0");
      await input.fill(field.valid);
    } else {
      await repositorySection(page, "Limits & rules");
      await expect(input).toBeDisabled();
      await expect(input).toHaveValue("1");
      await repositorySection(page, "AI connection");
      await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "Team reviews");
      await repositorySection(page, "Limits & rules");
      await expect(input).toHaveValue(field.valid);
      await repositorySection(page, "AI connection");
      await chooseOption(page, page.getByLabel("AI connection", { exact: true }), "My ChatGPT");
    }
    await repositorySection(page, "Review behavior");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
      settings: { [field.key]: field.personal ? Number(field.valid) : 1 },
    });
  });
}

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
  const settings = page.getByRole("form", { name: "Repository configuration" });
  await expect(settings).toBeVisible();
  await expect(settings.getByLabel("Maximum files", { exact: true })).toBeVisible();
  await expect(settings.getByRole("checkbox", { name: "Unlimited maximum files" })).toBeVisible();
  await repositorySection(page, "Project context");
  await settings.getByRole("textbox", { name: "Project context", exact: true }).fill("A settings change must preserve the selected section.");
  await expect(page.getByRole("navigation", { name: "Repository settings sections" }).getByRole("link", { name: "Project context" })).toHaveAttribute("aria-current", "page");
  expect(requests).toEqual([]);
  await noOverflow(page);
});

test("candidate assessment separates withheld claims from repeated findings", async ({ page }, info) => {
  const requests = await mock(page, {
    run: { status: "completed", publication: "no_new_comments", publication_error: null,
      check_status: "completed", check_conclusion: "neutral", check_pending: false, check_error: null },
    assessment: { candidates: 2, new_findings: 0, repeated_observations: 1, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, published_comments: 0 },
    candidates: [
      { id: "repeat", snapshot: { ...finding, content: "Same defect with different wording." },
        status: "completed", result: null, finding_id: "finding", is_new: false, error: null, retryable: false },
      { id: "uncertain", snapshot: { path: "frontend/src/index.css", start_line: 315,
          content: "Tailwind might fail to process this rule." }, status: "completed", finding_id: null, retryable: false,
        is_new: false, error: null, result: {
          verdict: "uncertain", relationship: "new", finding_id: null,
          explanation: "No evidence establishes a build failure at this revision.",
          cause: "", trigger: "", consequence: "", history_explanation: "", contradicts_prior: false,
          changed_conditions: "", suggestion_supported: false, evidence: [],
        } },
    ],
  });
  await page.goto("/runs/run");
  await expect(page.getByText("No new comments", { exact: true })).toBeVisible();
  await expect(page.getByText("No additional GitHub comments were needed. Inspect assessment results for repeated or withheld candidates.", { exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: /Assessment/ }).click();
  const totals = page.getByRole("region", { name: "Candidate assessment", exact: true }).locator("dl");
  await expect(totals.locator("dt")).toHaveText(["validated findings", "repeated", "not supported", "withheld", "comments published"]);
  await expect(totals.locator("dd")).toHaveText(["0", "1", "0", "1", "0"]);
  await expect(page.getByRole("region", { name: "GitHub publication" }).getByText("Partial", { exact: true })).toBeVisible();
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  await publication.getByRole("status").focus();
  await expect(page.getByRole("tooltip")).toHaveText("Some candidates could not be fully assessed.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  const checkLink = publication.getByRole("link", { name: "View check on GitHub: Neutral", exact: true });
  await checkLink.focus();
  await expect(page.getByRole("tooltip")).toHaveText("The review completed, but assessment or fix verification is incomplete.");
  await expect(checkLink).toHaveAttribute("href", run.check_url);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(page.getByText(/Assessment is incomplete/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Inspect assessment", exact: true })).toHaveCount(0);
  const withheld = page.getByRole("button", { name: /index.css.*315.*Withheld/ });
  await withheld.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText("No evidence establishes a build failure at this revision.");
  await expect(sheet.getByText("frontend/src/index.css", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Open finding" })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: /Resolve finding|Reopen finding|Dismiss/ })).toHaveCount(0);
  await sheet.locator("summary", { hasText: "Original proposal" }).click();
  await expect(sheet).toContainText("Tailwind might fail to process this rule.");
  await sheet.getByRole("tab", { name: "Evidence 0" }).click();
  await expect(sheet).toContainText("No supporting evidence was recorded.");
  await page.keyboard.press("Escape");
  await expect(withheld).toBeFocused();
  const repeated = page.getByRole("button", { name: /Same defect with different wording/ });
  await repeated.click();
  await sheet.getByRole("button", { name: "Open finding" }).click();
  const linked = page.getByRole("dialog", { name: "Finding details" });
  await expect(linked.getByRole("tab", { name: "At this run" })).toHaveAttribute("data-state", "active");
  await expect(linked.getByRole("button", { name: "Back to assessment" })).toBeFocused();
  await linked.getByRole("button", { name: "Back to assessment" }).click();
  await expect(sheet.getByRole("tab", { name: "Assessment", exact: true })).toBeFocused();
  await expect(sheet).toContainText("Repeated observation");
  await page.keyboard.press("Escape");
  await expect(repeated).toBeFocused();
  await expect(page.getByRole("button", { name: "Retry GitHub publication" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/candidate-assessment-${info.project.name}.png`, fullPage: true });
  expect(requests).toEqual([]);
});

test("assessment summary separates not supported and withheld to match the cards", async ({ page }, info) => {
  const result: Schema["CandidateOut"]["result"] = {
    verdict: "rejected", relationship: "new", finding_id: null, explanation: "The evidence does not establish a defect.",
    cause: "", trigger: "", consequence: "", history_explanation: "", contradicts_prior: false,
    changed_conditions: "", suggestion_supported: false, evidence: [],
  };
  const candidates: Schema["CandidateOut"][] = Array.from({ length: 10 }, (_, index) => ({
    id: `candidate-${index}`, snapshot: { path: `src/proposal-${index}.ts`, content: `Proposal ${index}` },
    status: index === 9 ? "failed" : "completed",
    result: index < 8 ? { ...result, verdict: index < 3 ? "confirmed" : "rejected" }
      : index === 8 ? { ...result, verdict: "uncertain" } : null,
    finding_id: index < 3 ? `finding-${index}` : null, is_new: index < 3, error: null, retryable: index === 9,
  }));
  const writes = await mock(page, { candidates, assessment: {
    candidates: 10, new_findings: 3, repeated_observations: 0, rejected_candidates: 5,
    withheld_candidates: 2, pending: 0, incomplete: 2, published_comments: 3,
  } });
  await page.goto("/runs/run?tab=assessment");
  const assessment = page.getByRole("region", { name: "Candidate assessment", exact: true });
  await expect(assessment.locator("dl dt")).toHaveText([
    "validated findings", "repeated", "not supported", "withheld", "comments published",
  ]);
  await expect(assessment.locator("dl dd")).toHaveText(["3", "0", "5", "2", "3"]);
  await expect(assessment.getByRole("button", { name: /New validated finding/ })).toHaveCount(3);
  await expect(assessment.getByRole("button", { name: /Not supported/ })).toHaveCount(5);
  await expect(assessment.getByRole("button", { name: /Withheld/ })).toHaveCount(2);
  await noOverflow(page);
  await assessment.locator("dl").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/assessment-summary-${info.project.name}.png` });
  expect(writes).toEqual([]);
});

test("assessment result tooltips preserve card activation and keyboard access", async ({ page }, info) => {
  const result: Schema["CandidateOut"]["result"] = {
    verdict: "rejected", relationship: "new", finding_id: null, explanation: "The evidence does not establish a defect.",
    cause: "", trigger: "", consequence: "", history_explanation: "", contradicts_prior: false,
    changed_conditions: "", suggestion_supported: false, evidence: [],
  };
  const outcomes = [
    { label: "New validated finding", description: "Confirmed by evidence and saved as a new finding.", finding_id: "finding", is_new: true },
    { label: "Repeated observation", description: "Matches an existing finding from review history.", finding_id: "finding" },
    { label: "Not supported", description: "The evidence did not support this proposed issue.", result },
    { label: "Withheld", description: "Not accepted as a finding because assessment was uncertain or incomplete." },
    { label: "Awaiting assessment", description: "Queued or being checked against evidence.", status: "queued" },
  ];
  const writes = await mock(page, { candidates: outcomes.map((outcome, index) => ({
    id: `result-${index}`, snapshot: { path: `src/result-${index}.ts`, content: `Proposal ${index}` },
    status: "completed", result: null, finding_id: null, is_new: false, error: null, retryable: false, ...outcome,
  })), assessment: { candidates: 5, new_findings: 1, repeated_observations: 1, rejected_candidates: 1, withheld_candidates: 1,
    pending: 1, incomplete: 0, published_comments: 0 } });
  await page.goto("/runs/run?tab=assessment");
  for (const { label, description } of outcomes) {
    const card = page.getByRole("button", { name: new RegExp(label) });
    const badge = card.getByText(label, { exact: true });
    await expect(badge).toHaveCSS("cursor", "pointer");
    await expect(card.locator("button, a, input, [tabindex]")).toHaveCount(0);
    await expect(card).toHaveAccessibleName(new RegExp(description));
    if (info.project.name === "desktop") {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toHaveText(description);
    }
    await badge.click();
    const sheet = page.getByRole("dialog", { name: "Assessment details" });
    await sheet.getByText(label, { exact: true }).locator("..").focus();
    await expect(page.getByRole("tooltip")).toHaveText(description);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeVisible();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(card).toBeFocused();
  }
  expect(writes).toEqual([]);
  await noOverflow(page);
});

test("legacy review findings are not labeled as validated", async ({ page }) => {
  await mock(page);
  await page.goto("/runs/run");
  await expect(page.getByText("This run predates candidate assessment. Its findings have not been assessed by this policy.")).toBeVisible();
});

test("technical assessment retry preserves its previous attempt and polls only the candidate", async ({ page }, info) => {
  await page.clock.install();
  const candidate: Schema["CandidateOut"] = {
    id: "failed", snapshot: { path: "src/retry.ts", content: "Proposed retry defect." },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", retryable: true,
  };
  const writes = await mock(page, { candidates: [candidate],
    run: { status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, published_comments: 0 },
  });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let retries = 0;
  await page.route("**/api/v1/runs/run/candidates/failed/retry", async (route) => {
    expect(route.request().method()).toBe("POST");
    retries++;
    await pending;
    candidate.attempts = [{ status: candidate.status, error: candidate.error, result: null,
      usage: { tokens: 25 }, finished_at: at }];
    candidate.status = "queued";
    candidate.error = null;
    candidate.retryable = false;
    await route.fulfill({ status: 202, json: candidate });
  });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await page.getByRole("button", { name: /Proposed retry defect/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText("Retry uses the same connection and AI quota.");
  await sheet.screenshot({ path: `../.impeccable/review/retry-assessment-${info.project.name}.png` });
  await sheet.getByRole("button", { name: "Retry assessment", exact: true }).click();
  await expect(sheet.getByRole("button", { name: "Retrying assessment…" })).toBeDisabled();
  release();
  await expect(sheet).toContainText("Awaiting assessment");
  await sheet.locator("summary", { hasText: "Previous attempts (1)" }).click();
  await expect(sheet).toContainText("25 tokens used or reserved.");
  await expect(sheet.getByRole("alert")).toContainText("Assessment timed out");
  candidate.status = "completed";
  candidate.result = { verdict: "uncertain", relationship: "uncertain", finding_id: null,
    explanation: "The supplied evidence is insufficient.", cause: "", trigger: "", consequence: "",
    history_explanation: "", contradicts_prior: false, changed_conditions: "", suggestion_supported: false, evidence: [] };
  await page.clock.fastForward(5000);
  await expect(sheet).toContainText("The supplied evidence is insufficient.");
  await expect(sheet.getByRole("button", { name: "Retry assessment", exact: true })).toHaveCount(0);
  expect(retries).toBe(1);
  expect(writes).toEqual([]);
  await noOverflow(page);
});

test("assessment retry shows server errors without discarding the failed attempt", async ({ page }) => {
  await mock(page, { candidates: [{ id: "failed", snapshot: { content: "Failed proposal." },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", retryable: true }],
    run: { status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, published_comments: 0 },
  });
  await page.route("**/api/v1/runs/run/candidates/failed/retry", (route) => route.fulfill({
    status: 409, json: { detail: "The saved run's remaining AI budget cannot support another assessment." },
  }));
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await page.getByRole("button", { name: /Failed proposal/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await sheet.getByRole("button", { name: "Retry assessment", exact: true }).click();
  await expect(sheet.getByRole("alert").filter({ hasText: "remaining AI budget" })).toBeVisible();
  await expect(sheet.getByRole("alert").filter({ hasText: "Assessment timed out" })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Retry assessment", exact: true })).toBeEnabled();
});

test("read-only users cannot retry technical assessment failures", async ({ page }) => {
  await mock(page, { canWrite: false, candidates: [{ id: "failed", snapshot: { content: "Failed proposal." },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", retryable: true }],
    run: { status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, published_comments: 0 },
  });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await page.getByRole("button", { name: /Failed proposal/ }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Retry assessment", exact: true })).toHaveCount(0);
});

test("original proposal Markdown filters unsafe link protocols before rendering anchors", async ({ page }) => {
  const unsafe = [
    ["javascript", "javascript:alert%281%29"], ["mixed case", "JaVaScRiPt:alert%281%29"],
    ["entity", "javascript&#58;alert%281%29"], ["data", "data:text/html,unsafe"], ["vbscript", "vbscript:msgbox%281%29"],
  ];
  const safe = [["https", "https://example.com"], ["http", "http://example.com"], ["email", "mailto:review@example.com"],
    ["relative", "/repositories/22"], ["fragment", "#evidence"]];
  await mock(page, { assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
    pending: 0, incomplete: 1, published_comments: 0 }, candidates: [{ id: "proposal", snapshot: { content: "Proposed links.\n\n"
    + [...unsafe, ...safe].map(([name, href]) => `[${name}](${href})`).join("\n\n")
    + '\n\n<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>',
  }, status: "failed", result: null, finding_id: null, is_new: false, error: null, retryable: false }] });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await page.getByRole("button", { name: /Proposed links/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await sheet.locator("summary", { hasText: "Original proposal" }).click();
  const markdown = sheet.locator(".markdown");
  for (const [name] of unsafe) await expect(markdown.getByRole("link", { name, exact: true })).toHaveAttribute("href", "");
  for (const [name, href] of safe) await expect(markdown.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
  await expect(markdown.locator("img, script")).toHaveCount(0);
});

test("assessment search keeps its debounce deadline when polling updates the list", async ({ page }) => {
  await page.clock.install();
  const candidates: Schema["CandidateOut"][] = [{ id: "pending", snapshot: finding,
    status: "running", result: null, finding_id: null, is_new: false, error: null, retryable: false }];
  await mock(page, { candidates, run: { status: "running", finished_at: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
      pending: 1, incomplete: 0, published_comments: 0 } });
  let releasePoll!: () => void;
  const poll = new Promise<void>((resolve) => { releasePoll = resolve; });
  const searches: string[] = [];
  let reads = 0;
  await page.route("**/api/v1/runs/run/candidates?**", async (route) => {
    searches.push(new URL(route.request().url()).searchParams.get("q") ?? "");
    if (++reads === 2) await poll;
    await route.fallback();
  });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await expect(page.getByRole("region", { name: "Candidate assessment", exact: true }).getByRole("button", { name: /retry loop has no upper bound/ })).toBeVisible();
  await expect(page).toHaveURL((url) => url.searchParams.get("tab") === "assessment");
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.clock.runFor(6000);
  await expect.poll(() => reads).toBe(2);
  await page.getByLabel("Search assessments", { exact: true }).fill("Refreshed");
  await page.clock.runFor(150);
  candidates[0] = { ...candidates[0], snapshot: { ...finding, content: "Refreshed proposal." } };
  const refreshed = page.waitForResponse((response) => response.url().includes("/runs/run/candidates?") && !response.url().includes("q="));
  releasePoll();
  await (await refreshed).finished();
  await page.clock.runFor(50);
  await expect(page.getByRole("button", { name: /Refreshed proposal/ })).toBeVisible();
  expect(searches).toEqual(["", ""]);
  await page.clock.runFor(100);
  await expect.poll(() => searches.at(-1)).toBe("Refreshed");
});

test("candidate assessment exposes errors and complete highlighted evidence", async ({ page }, info) => {
  const source = "while True:\n    retry()";
  await mock(page, {
    run: { ...run, status: "completed", publication: "published", publication_error: null },
    assessment: { candidates: 2, new_findings: 1, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, published_comments: 0 },
    candidates: [
      { id: "failed", snapshot: { path: "backend/client.py", start_line: 12, content: "Connection failure." },
        status: "failed", result: null, finding_id: null, is_new: false, retryable: true,
        error: "Assessment timed out; this candidate was withheld." },
      { id: "confirmed", snapshot: { path: "backend/retry.py", start_line: 7, content: "Retry has no limit." },
        status: "completed", finding_id: "finding", is_new: true, error: null, retryable: false, result: {
          verdict: "confirmed", relationship: "new", finding_id: null, explanation: "Retries never terminate.",
          cause: "Unbounded loop", trigger: "Repeated delivery failure", consequence: "Worker remains occupied",
          history_explanation: "", contradicts_prior: false, changed_conditions: "", suggestion_supported: false,
          evidence: [{ revision: "current", path: "backend/retry.py", start_line: 7, end_line: 8, quote: source }],
        } },
    ],
  });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  const assessment = page.getByRole("region", { name: "Candidate assessment", exact: true });
  await expect(page.getByRole("region", { name: "GitHub publication" }).getByText("Partial", { exact: true })).toBeVisible();
  await expect(assessment).not.toContainText("No new comments");
  await expect(assessment).not.toContainText("Assessment needs attention");
  await mkdir("../.impeccable/review", { recursive: true });
  await assessment.screenshot({ path: `../.impeccable/review/assessment-list-${info.project.name}.png` });
  await assessment.getByRole("button", { name: /client.py.*12.*Withheld/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet.getByRole("alert")).toContainText("Assessment timed out");
  await expect(sheet.getByRole("alert")).toContainText("The time limit was reached before this assessment finished.");
  await expect(sheet.getByRole("region", { name: "Assessment technical details" })).toBeHidden();
  await expect(sheet.getByRole("button", { name: "Open finding" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  const confirmed = assessment.getByRole("button", { name: /retry.py.*7.*New validated finding/ });
  await confirmed.focus();
  await page.keyboard.press("Enter");
  await expect(sheet).toContainText("Retries never terminate.");
  await sheet.getByRole("tab", { name: "Evidence 1" }).click();
  await expect(sheet.getByText("Current evidence: backend/retry.py:7–8", { exact: true })).toBeVisible();
  const code = sheet.getByRole("region", { name: "Code snippet" });
  await expect(code).toHaveText(source);
  await expect(code.locator("code")).toHaveClass(/language-python/);
  await expect(code.locator(".hljs-keyword").first()).toHaveText("while");
  await expect(sheet.getByRole("alert")).toHaveCount(0);
  await expect(sheet.locator("[data-slot=sheet-footer]").getByRole("button", { name: "Open finding" })).toBeInViewport();
  await noOverflow(page);
  await mkdir("../.impeccable/review", { recursive: true });
  await sheet.screenshot({ path: `../.impeccable/review/candidate-evidence-${info.project.name}.png` });
});

const citationError = "OCR returned an invalid verification verdict: Assessment evidence does not match the supplied source. "
  + JSON.stringify({ field: "evidence[2]", reason: "quote is not verbatim text within the cited source lines",
    revision: "current", path: "frontend/src/settings.tsx", start_line: 682, end_line: 687 })
  + "; response=" + JSON.stringify({ verdict: "confirmed", explanation: "A long saved response. ".repeat(260) });

const assessmentErrors = [
  { name: "citation mismatch", error: citationError, title: "Evidence could not be verified",
    message: "The AI's evidence quote did not exactly match the cited source.",
    citation: "Citation 3 · Current source: frontend/src/settings.tsx:682–687" },
  { name: "citation with trailing diagnostics", error: citationError.replace("; response=", " Technical details: response="),
    title: "Evidence could not be verified", message: "The AI's evidence quote did not exactly match the cited source.",
    citation: "Citation 3 · Current source: frontend/src/settings.tsx:682–687" },
  { name: "finding reference", error: 'OCR returned an invalid verification verdict: Assessment references an unavailable finding. {"field":"finding_id","reason":"ID is unavailable"}; response={"finding_id":"candidate-id"}',
    title: "Finding reference could not be verified", message: "Only findings supplied in the review history can be linked." },
  { name: "missing final assessment", error: 'The AI did not finish checking this finding. Technical details: {"exit_code":0,"status":"success","result_error":"The AI finished without returning a final assessment. Start a new review or check the selected model."}',
    title: "No usable assessment returned", message: "The AI did not return a complete assessment in the required format." },
  { name: "exhausted budget", error: "Token budget exhausted: 200,000 / 200,000 tokens used or reserved. Assessment stopped.",
    title: "Assessment limit reached", message: "200,000 / 200,000 tokens used or reserved" },
  { name: "prompt ceiling", error: "OCR evidence prompt exceeds its 47,110-token limit (80% of max_tokens=58,888): prompt tokens exceed the configured limit.",
    title: "Assessment limit reached", message: "47,110-token limit" },
  { name: "long limit diagnostics", error: "OCR evidence prompt exceeds its 47,110-token limit: " + JSON.stringify({ detail: "saved diagnostics ".repeat(400) }),
    title: "Assessment limit reached", message: "The assessment could not continue within the configured limits." },
  { name: "unknown usage", error: "The review engine did not report valid token usage. Further AI checks were stopped to avoid exceeding the run budget. Technical details: {}",
    title: "Token usage could not be verified", message: "The AI's token usage was unavailable, so this assessment could not continue." },
  { name: "provider failure", error: 'OCR verification process failed; token consumption is unknown. {"exit_code":1,"stderr":"Provider returned HTTP 401"}',
    title: "AI request could not complete", message: "The selected AI service could not complete this assessment." },
  { name: "cancelled review", error: "The assessment stopped because the review was cancelled, replaced by a newer run, or reached its time limit. Check the review outcome.",
    title: "Assessment stopped", message: "Check the run outcome and current access." },
  { name: "malformed historical diagnostics", error: 'OCR returned an invalid verification verdict: Assessment evidence does not match the supplied source. {"field":"evidence[2]"',
    title: "Evidence could not be verified", message: "The AI returned a source citation that could not be validated." },
  { name: "unknown long diagnostics", error: "Unknown assessment failure: " + JSON.stringify({ response: "saved response ".repeat(400) }),
    title: "Assessment could not be completed", message: "No usable assessment was saved." },
];

for (const failure of assessmentErrors) {
  test(`assessment sheet explains ${failure.name} and preserves copyable diagnostics`, async ({ page }, info) => {
    await mock(page, {
      run: { ...run, status: "completed", publication: "no_new_comments", publication_error: null },
      assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
        pending: 0, incomplete: 1, published_comments: 0 },
      candidates: [{ id: "failed", snapshot: { path: "frontend/src/settings.tsx", start_line: 525, content: "Proposed finding." },
        status: "failed", result: null, finding_id: null, is_new: false, error: failure.error, retryable: true }],
    });
    await page.goto("/runs/run");
    await page.getByRole("tab", { name: /Assessment/ }).click();
    const card = page.getByRole("button", { name: /settings.tsx.*525.*Withheld/ });
    await card.click();
    const sheet = page.getByRole("dialog", { name: "Assessment details" });
    const alert = sheet.getByRole("alert");
    await expect(alert).toContainText(failure.title);
    await expect(alert).toContainText(failure.message);
    await expect(alert).toContainText("This candidate was withheld from publication.");
    await expect(alert).not.toContainText("response=");
    await expect(sheet.getByText("No assessment result was recorded.", { exact: true })).toHaveCount(0);
    const raw = sheet.locator('pre[aria-label="Assessment technical details"]');
    await expect(raw).toBeHidden();
    if ("citation" in failure) await expect(sheet.getByText(failure.citation!, { exact: true })).toBeVisible();
    await noOverflow(page);
    if (failure.name === "citation mismatch") {
      await mkdir("../.impeccable/review", { recursive: true });
      await sheet.screenshot({ path: `../.impeccable/review/assessment-error-${info.project.name}.png` });
    }
    const summary = sheet.locator("summary", { hasText: "Technical details" });
    await summary.focus();
    await summary.press("Enter");
    await expect(raw).toBeVisible();
    expect(await raw.textContent()).toBe(failure.error);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async (value: string) => { document.body.dataset.copiedAssessment = value; },
      } });
    });
    await sheet.getByRole("button", { name: "Copy details", exact: true }).click();
    await expect(page.locator("body")).toHaveAttribute("data-copied-assessment", failure.error);
    await expect(page.getByText("Assessment details copied", { exact: true })).toBeVisible();
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async () => { throw new Error("Clipboard denied"); },
      } });
    });
    await sheet.getByRole("button", { name: "Copy details", exact: true }).click();
    await expect(page.getByText("Select the text in Technical details and copy it manually.", { exact: true })).toBeVisible();
    expect(await raw.textContent()).toBe(failure.error);
    await raw.focus();
    await expect(raw).toBeFocused();
    await expect(sheet.locator("[data-slot=sheet-footer]").getByRole("button", { name: "Close", exact: true })).toBeInViewport();
    await noOverflow(page);
    await page.keyboard.press("Escape");
    await expect(card).toBeFocused();
  });
}

test("assessment sheet preserves polling, full evidence, and focus on narrow screens", async ({ page }) => {
  await page.clock.install();
  await page.setViewportSize({ width: 320, height: 844 });
  const path = `frontend/${"nested/".repeat(6)}${"long-filename-".repeat(7)}.tsx`;
  const source = `const value = "${"saved-evidence-".repeat(30)}";\nexport { value };`;
  const candidate: Schema["CandidateOut"] = {
    id: "pending", snapshot: { path, start_line: 10, end_line: 11, content: "Full original proposal remains available after assessment finishes." },
    status: "running", result: null, finding_id: null, is_new: false, error: null, retryable: false,
  };
  const currentRun: Partial<Run> = { status: "running", publication: "pending", publication_error: null };
  const requests = await mock(page, {
    run: currentRun, canWrite: false, candidates: [candidate],
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0, pending: 1, incomplete: 0, published_comments: 0 },
  });
  let listReads = 0;
  let detailReads = 0;
  await page.route("**/api/v1/runs/run/candidates?**", (route) => { listReads++; return route.fallback(); });
  await page.route("**/api/v1/runs/run/candidates/pending", (route) => { detailReads++; return route.fallback(); });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  const card = page.getByRole("button", { name: /long-filename-/ });
  await card.focus();
  await page.keyboard.press("Space");
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText("Assessment is waiting for the review worker.");
  await expect(sheet.getByText(path, { exact: true })).toBeVisible();
  await sheet.getByRole("tab", { name: "Evidence 0" }).click();
  await expect(sheet).toContainText("Evidence will appear when assessment completes.");
  candidate.status = "completed";
  candidate.result = {
    verdict: "rejected", relationship: "new", finding_id: null,
    explanation: "The supplied evidence does not establish the alleged defect.",
    cause: "", trigger: "", consequence: "", history_explanation: "Earlier evidence is preserved.",
    contradicts_prior: false, changed_conditions: "The reviewed conditions did not change.", suggestion_supported: false,
    evidence: [
      { revision: "original", path, start_line: 10, end_line: 11, quote: source },
      { revision: "current", path, start_line: 10, end_line: 11, quote: "export const unchanged = true;" },
    ],
  };
  await page.clock.fastForward(5000);
  await expect(sheet.getByRole("tab", { name: "Evidence 2" })).toHaveAttribute("data-state", "active", { timeout: 10000 });
  expect(detailReads).toBeGreaterThan(0);
  expect(listReads).toBeGreaterThan(1);
  currentRun.status = "completed";
  await page.clock.fastForward(5000);
  await expect(page.locator("main h1")).toHaveText("Review run completed");
  const settledReads = { list: listReads, detail: detailReads };
  await page.clock.runFor(10_000);
  expect({ list: listReads, detail: detailReads }).toEqual(settledReads);
  await expect(sheet).toContainText("Not supported");
  const code = sheet.getByRole("region", { name: "Code snippet" });
  await expect(code.nth(0)).toHaveText(source);
  await expect(code.nth(1)).toHaveText("export const unchanged = true;");
  await code.nth(0).focus();
  await expect(code.nth(0)).toBeFocused();
  await expect(sheet.locator("[data-slot=sheet-footer]").getByRole("button", { name: "Close", exact: true })).toBeInViewport();
  await sheet.getByRole("tab", { name: "Assessment", exact: true }).click();
  await expect(sheet).toContainText("Earlier evidence is preserved.");
  await expect(sheet).toContainText("The reviewed conditions did not change.");
  await sheet.locator("summary", { hasText: "Original proposal" }).click();
  await expect(sheet).toContainText("Full original proposal remains available after assessment finishes.");
  await expect(sheet.getByRole("button", { name: /Open finding|Resolve finding|Reopen finding|Dismiss/ })).toHaveCount(0);
  await noOverflow(page);
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  expect(requests).toEqual([]);
});

for (const status of [401, 403, 404]) {
  test(`candidate detail stops polling after ${status} and leaves linked findings accessible`, async ({ page }) => {
    await page.clock.install();
    const candidate: Schema["CandidateOut"] = {
      id: "linked", snapshot: finding, status: "running", result: null, retryable: false,
      finding_id: "finding", is_new: true, error: null,
    };
    await mock(page, { candidates: [candidate], assessment: {
      candidates: 1, new_findings: 1, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
      pending: 1, incomplete: 0, published_comments: 0,
    } });
    let denied = false;
    let reads = 0;
    await page.route("**/api/v1/runs/run/candidates/linked", (route) => {
      reads++;
      return denied ? route.fulfill({ status, json: { detail: "Candidate unavailable" } }) : route.fallback();
    });
    await page.goto("/runs/run");
    await page.getByRole("tab", { name: /Assessment/ }).click();
    await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
    await page.getByRole("button", { name: "Open finding", exact: true }).click();
    const sheet = page.getByRole("dialog", { name: "Finding details" });
    await expect(sheet).toContainText(finding.content);
    denied = true;
    await page.clock.fastForward(5000);
    await expect(sheet.getByRole("alert")).toContainText("Candidate unavailable");
    await expect(sheet).toContainText(finding.content);
    const deniedReads = reads;
    await page.clock.runFor(10_000);
    expect(reads).toBe(deniedReads);
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: /^Findings/ }).click();
    await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
    await expect(sheet).toContainText(finding.content);
    await expect(sheet.getByRole("alert")).toHaveCount(0);
  });
}

test("assessments paginate on the backend and reset pages when filters change", async ({ page }, info) => {
  const candidates: Schema["CandidateOut"][] = Array.from({ length: 23 }, (_, index) => ({
    id: `candidate-${index}`, snapshot: { path: `src/handler-${index}.ts`, content: `Saved proposal ${index}`, severity: index % 2 ? "low" : "high", category: index === 22 ? "custom_rule" : index === 21 ? "performance" : "bug" },
    status: "completed", result: null, finding_id: index === 22 ? "finding" : null, is_new: index === 22, error: null, retryable: false,
  }));
  const writes = await mock(page, { candidates,
    assessment: { candidates: 23, new_findings: 1, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 22, pending: 0, incomplete: 0, published_comments: 1 },
  });
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runs/run/candidates") requests.push(url.searchParams);
  });
  await page.goto("/runs/run");
  expect(requests).toEqual([]);
  await page.getByRole("tab", { name: /Assessment/ }).click();
  const list = page.getByRole("region", { name: "Candidate assessment", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(10);
  await expect(list.getByRole("heading", { name: "Candidate assessment", exact: true })).toHaveCount(0);
  await expect(list).toContainText("Proposed findings are checked against evidence and earlier reviews before becoming new comments.");
  await expect(list.getByText("23 of 23 assessments", { exact: true })).toHaveClass("sr-only");
  await list.getByRole("button", { name: "Load more assessments" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/assessment-pagination-${info.project.name}.png`, animations: "disabled" });
  await list.getByRole("button", { name: "Load more assessments" }).click();
  await expect(cards).toHaveCount(20);
  await expect(list.getByText("20 of 23 shown", { exact: true })).toBeVisible();
  expect(requests.some((params) => params.get("page") === "2")).toBe(true);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await page.getByLabel("Search type options", { exact: true }).fill("custom");
  await page.getByRole("checkbox", { name: /^Custom rule/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Custom rule/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("handler-22.ts");
  expect(requests.at(-1)?.getAll("category")).toEqual(["custom_rule"]);
  expect(requests.at(-1)?.get("page")).toBe("1");
  await page.getByRole("button", { name: "Remove Type filter", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await page.getByLabel("Search assessments", { exact: true }).fill("handler-22.ts");
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("New validated finding");
  await expect(list.getByText("1 of 23 assessments", { exact: true })).toHaveClass("sr-only");
  expect(requests.at(-1)?.get("q")).toBe("handler-22.ts");
  expect(requests.at(-1)?.get("page")).toBe("1");
  await cards.press("Enter");
  await expect(page.getByRole("dialog", { name: "Assessment details" })).toContainText("handler-22.ts");
  await page.keyboard.press("Escape");
  await expect(cards).toBeFocused();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await page.getByRole("checkbox", { name: /^New validated finding/ }).click();
  await expect(page.getByRole("checkbox", { name: /^New validated finding/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(1);
  expect(requests.at(-1)?.getAll("outcome")).toEqual(["new"]);
  await page.getByRole("button", { name: "Remove Result filter", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: /^High/ }).click();
  await expect(page.getByRole("checkbox", { name: /^High/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(list.getByText("12 of 23 assessments", { exact: true })).toHaveClass("sr-only");
  await list.getByRole("button", { name: "Load more assessments" }).click();
  await expect(cards).toHaveCount(12);
  expect(requests.at(-1)?.getAll("severity")).toEqual(["high"]);
  await page.getByRole("button", { name: "Remove Severity filter", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await page.getByLabel("Search assessments", { exact: true }).fill("no-match");
  await expect(list.getByRole("heading", { name: "No assessments match these filters" })).toBeVisible();
  await page.getByRole("button", { name: "View all assessments", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await expect(page.getByLabel("Search assessments", { exact: true })).toBeFocused();
  await noOverflow(page);
  expect(writes).toEqual([]);
});

test("assessment pages preserve loaded cards on failure and hide them on access denial", async ({ page }) => {
  const candidates: Schema["CandidateOut"][] = Array.from({ length: 21 }, (_, index) => ({
    id: `candidate-${index}`, snapshot: { path: `src/file-${index}.ts`, content: `Saved proposal ${index}` },
    status: "completed", result: null, finding_id: null, is_new: false, error: null, retryable: false,
  }));
  await mock(page, { candidates, assessment: { candidates: 21, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 21, pending: 0, incomplete: 0, published_comments: 0 } });
  let failure = 502;
  await page.route("**/api/v1/runs/run/candidates?**", (route) => failure
    ? route.fulfill({ status: failure, json: { detail: failure === 403 ? "Repository access revoked" : "Assessments temporarily unavailable" } })
    : route.fallback());
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  const list = page.getByRole("region", { name: "Candidate assessment", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(list.getByRole("alert")).toHaveText("Assessments temporarily unavailable");
  await expect(list.getByText("No candidates to assess")).toHaveCount(0);
  failure = 0;
  await list.getByRole("button", { name: "Retry assessments", exact: true }).click();
  await expect(cards).toHaveCount(10);
  failure = 502;
  await list.getByRole("button", { name: "Load more assessments" }).click();
  await expect(list.getByRole("button", { name: "Retry assessments", exact: true })).toBeVisible();
  await expect(cards).toHaveCount(10);
  failure = 0;
  await list.getByRole("button", { name: "Retry assessments", exact: true }).click();
  await expect(cards).toHaveCount(20);
  failure = 403;
  await list.getByRole("button", { name: "Load more assessments" }).click();
  await expect(list.getByRole("alert")).toHaveText("Repository access revoked");
  await expect(cards).toHaveCount(0);
});

for (const status of ["running", "completed"]) {
  test(`empty candidate assessment explains ${status} review`, async ({ page }) => {
    await mock(page, {
      run: { ...run, status, publication: status === "running" ? "pending" : "no_new_comments", publication_error: null },
      assessment: { candidates: 0, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
        pending: 0, incomplete: 0, published_comments: 0 },
      candidates: [],
    });
    await page.goto("/runs/run");
    await page.getByRole("tab", { name: /Assessment/ }).click();
    await expect(page.getByText(status === "running" ? "Candidates appear after the initial review finishes." : "No candidates were produced for assessment.")).toBeVisible();
    await noOverflow(page);
  });
}

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
    nextStep: "Subscription reviews now use the Token budget",
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
      await page.getByRole("tab", { name: "Coverage", exact: true }).click();
      const files = page.getByRole("region", { name: "Files not reviewed", exact: true });
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
  test(`coverage report disclosure preserves the user's choice during ${status} run polling`, async ({ page }) => {
    await page.clock.install();
    let reads = 0;
    const currentRun: Partial<Run> = { status };
    await mock(page, { run: currentRun });
    await page.route("**/api/v1/runs/run", (route) => {
      reads += 1;
      currentRun.progress = { selected: 10, completed: reads };
      currentRun.check_conclusion = reads > 1 ? "success" : null;
      return route.fallback();
    });
    await page.goto("/runs/run");
    await page.getByRole("tab", { name: "Coverage", exact: true }).click();
    const files = page.locator("details").filter({ has: page.getByText("Inspect coverage report", { exact: true }) });
    await expect(files).toBeVisible();
    await files.locator("summary").click();
    const open = await files.getAttribute("open");
    const before = reads;
    await page.clock.fastForward(10_000);
    await expect.poll(() => reads).toBeGreaterThan(before);
    await expect(page.getByRole("region", { name: "GitHub publication", exact: true }).getByText("success", { exact: true })).toBeVisible();
    await expect(page.getByText(/of 10 assigned files completed/)).toHaveCount(0);
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
  const type = page.getByRole("combobox", { name: "Connection type", exact: true });
  await type.fill("Claude");
  await expect(page.getByRole("option", { name: "Personal Claude Code subscription", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "Personal ChatGPT subscription", exact: true })).toHaveCount(0);
  await type.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(type).toHaveValue("Shared API connection");
  await type.fill("ChatGPT");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/connection-type-combobox-${info.project.name}.png` });
  await type.press("Enter");
  await expect(type).toHaveValue("Personal ChatGPT subscription");
  await page.getByLabel("Connection name").fill("Personal setup");
  await expect(page.getByLabel("Model", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Load models", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Continue to sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Edit subscription", exact: true })).toBeFocused();
  await expect(page.getByText("Personal setup · ChatGPT subscription", { exact: true })).toBeVisible();
  await expect(page.getByRole("list", { name: "Subscription setup" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Check status", exact: true })).toHaveCount(0);
  await expect(page.getByText("After signing in, choose a default model.", { exact: false })).toBeVisible();
  await expect(page.getByRole("form", { name: "Subscription settings" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Repository authorization" })).toHaveCount(0);
  await page.getByRole("button", { name: "Connect ChatGPT", exact: true }).click();
  await expect(page.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "General", exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  const model = page.getByLabel("Model", { exact: true });
  await expectOption(page, model, "personal-model", 1);
  await chooseOption(page, model, "personal-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/personal-model-selection-${info.project.name}.png` });
  await page.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Edit subscription", exact: true })).toBeVisible();
  await expect(page.getByRole("form", { name: "Subscription settings" })).toBeVisible();
  await expect(page.getByText("You can start manual reviews in enabled repositories.", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Repository authorization" })).toHaveCount(0);
  expect(savedModel).toBe("personal-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/personal-model-picker-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Set up automatic reviews", exact: true }).click();
  await expect(page.getByRole("region", { name: "Repository authorization" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "I authorize automatic reviews with my subscription" })).not.toBeChecked();
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add connection", exact: true })).toBeFocused();
});

for (const [kind, label] of [["chatgpt", "Personal ChatGPT subscription"], ["claude_code", "Personal Claude Code subscription"]] as const) {
  test(`${kind} subscription setup resumes after closing without creating another connection`, async ({ page }, info) => {
    await mock(page);
    const requests: string[] = [];
    page.on("request", (request) => requests.push(`${request.method()} ${new URL(request.url()).pathname}`));
    let created: typeof connection | undefined;
    await page.route("**/api/v1/connections", (route) => {
      if (route.request().method() === "POST") {
        expect(route.request().postDataJSON()).toEqual({
          name: "Subscription setup", kind, model: "", options: { prompt_token_ceiling: 58888 },
        });
        created = { ...connection, ...route.request().postDataJSON(), id: "personal", owner_id: 7, auth_status: "disconnected" };
        return route.fulfill({ json: created });
      }
      return route.fulfill({ json: created ? [created] : [connection] });
    });
    await page.route("**/api/v1/connections/personal/auth", (route) => route.fulfill({ json: { status: created?.auth_status ?? "disconnected" } }));
    await page.goto("/settings");
    await page.getByRole("button", { name: "Add connection", exact: true }).click();
    const dialog = page.getByRole("dialog");
    const dialogId = await dialog.getAttribute("id");
    await dialog.getByLabel("API key", { exact: true }).fill("previous-api-key");
    await chooseOption(page, dialog.getByLabel("Model", { exact: true }), "Enter a custom model ID");
    await dialog.getByLabel("Custom model ID").fill("previous-api-model");
    await chooseOption(page, dialog.getByLabel("Connection type"), label);
    await dialog.getByLabel("Connection name").fill("Subscription setup");
    await expect(dialog.getByLabel("Model", { exact: true })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Continue to sign in", exact: true }).click();
    await expect(dialog.getByRole("heading", { name: "Edit subscription", exact: true })).toBeFocused();
    await expect(dialog.getByText(`Subscription setup · ${kind === "chatgpt" ? "ChatGPT" : "Claude Code"} subscription`, { exact: true })).toBeVisible();
    await expect(dialog).toHaveAttribute("id", dialogId!);
    await expect(dialog.getByRole("heading", { name: "Account connection", exact: true })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Check status", exact: true })).toHaveCount(0);
    await expect(dialog.getByText("How this connection works", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText("Your official client runs on this server.", { exact: false })).toBeVisible();
    await expect(dialog.getByRole("form", { name: "Subscription settings" })).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/${kind}-setup-${info.project.name}.png` });
    await page.keyboard.press("Escape");
    const row = page.getByRole("article", { name: "Subscription setup", exact: true });
    const signIn = row.getByRole("button", { name: "Complete sign in", exact: true });
    await signIn.click();
    await expect(dialog.getByRole("heading", { name: "Edit subscription", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(signIn).toBeFocused();
    expect(requests.filter((request) => request === "POST /api/v1/connections")).toHaveLength(1);
    expect(created).toMatchObject({ kind, model: "" });
    expect(requests).not.toContain("POST /api/v1/connections/personal/auth");
    expect(requests).not.toContain("GET /api/v1/connections/personal/grants");
    created!.auth_status = "connected";
    await page.reload();
    await row.getByRole("button", { name: "Choose model", exact: true }).click();
    await expect(dialog.getByRole("form", { name: "Subscription settings" })).toBeVisible();
    await expect(dialog.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true");
  });
}

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
  await expect(page.getByRole("heading", { name: /^Review run / })).toBeVisible();
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
  await expect(page).toHaveURL(/\/repositories\/22\?state=open$/);
  await expect(current).toHaveText("acme/payments");
  await breadcrumb.getByRole("link", { name: "Inbox", exact: true }).click();
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(current).toHaveText("Inbox");
  await page.goto("/settings");
  await expect(current).toHaveText("AI connections");
  for (const label of ["GitHub", "Team"]) {
    await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: label, exact: true }).click();
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
  await mockPullDetail(page, () => ({
    repository: { ...repo, full_name: fullName }, pull_request: pr, runs: [run], findings: [], can_write: true,
  }));
  await page.goto("/pulls/pr");
  await expect(page.getByRole("heading", { name: `#${pr.number} ${pr.title}`, exact: true })).toBeVisible();
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

test("appearance palettes keep text, actions, syntax and focus readable", () => {
  expect(contrast("#000000", "#ffffff")).toBe(21);
  for (const theme of themes) {
    for (const mode of ["light", "dark"] as const) {
      const colors = theme[mode];
      const pairs = [
        ["--foreground", "--background"], ["--card-foreground", "--card"],
        ["--popover-foreground", "--popover"], ["--primary-foreground", "--primary"],
        ["--secondary-foreground", "--secondary"], ["--accent-foreground", "--accent"],
        ["--destructive-foreground", "--destructive"], ["--code-added", "--code-added-background"],
        ["--code-removed", "--code-removed-background"],
      ] as const;
      for (const [foreground, background] of pairs) {
        expect(contrast(colors[foreground], colors[background]), `${theme.id} ${mode} ${foreground}`).toBeGreaterThanOrEqual(4.5);
      }
      for (const surface of ["--background", "--card", "--muted"] as const) {
        for (const foreground of ["--primary", "--muted-foreground", "--code-keyword", "--code-string", "--code-number", "--code-function"] as const) {
          expect(contrast(colors[foreground], colors[surface]), `${theme.id} ${mode} ${foreground} on ${surface}`).toBeGreaterThanOrEqual(4.5);
        }
        expect(contrast(colors["--ring"], colors[surface]), `${theme.id} ${mode} focus`).toBeGreaterThanOrEqual(3);
      }
    }
  }
});

for (const role of ["admin", "member"]) {
  test(`appearance navigation works for ${role}s without depending on operational settings`, async ({ page }) => {
    await mock(page, { role });
    await page.route("**/api/v1/settings", (route) => route.fulfill({ status: 503, json: { detail: "Settings unavailable" } }));
    await page.route("**/api/v1/connections", (route) => route.fulfill({ status: 503, json: { detail: "Connections unavailable" } }));
    await page.route("**/api/v1/providers", (route) => route.fulfill({ status: 503, json: { detail: "Providers unavailable" } }));
    await page.goto("/settings");
    const navigation = page.getByRole("navigation", { name: "Settings sections" });
    await expect(navigation.getByRole("link")).toHaveText(role === "admin"
      ? ["AI connections", "GitHub", "Team", "Appearance"] : ["AI connections", "Appearance"]);
    await navigation.getByRole("link", { name: "Appearance", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\?tab=appearance$/);
    await expect(page.getByRole("region", { name: "Appearance", exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByText("Appearance", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.goBack();
    await expect(navigation.getByRole("link", { name: "AI connections", exact: true })).toHaveAttribute("aria-current", "page");
    await page.goForward();
    await page.reload();
    await expect(page.getByRole("radio")).toHaveCount(9);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await openNavigation(page);
    await page.getByRole("button", { name: "Account menu for @carlos", exact: true }).click();
    const shortcut = page.getByRole("menuitem", { name: "Appearance", exact: true });
    await expect(shortcut).toHaveAttribute("href", "/settings?tab=appearance");
    await shortcut.click();
    await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
  });
}

test("appearance applies and persists all nine palettes in both color modes", async ({ page }, info) => {
  await mock(page);
  const operationalReads: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/(settings|connections|providers)$/.test(new URL(request.url()).pathname)) operationalReads.push(request.url());
  });
  await page.goto("/settings?tab=appearance");
  const appearance = page.getByRole("region", { name: "Appearance", exact: true });
  await expect(appearance.getByRole("radio", { name: "Claude Code", exact: true })).toBeChecked();
  await expect(appearance.getByLabel("Color mode", { exact: true })).toHaveValue("system");
  for (const mode of ["light", "dark"] as const) {
    await appearance.getByLabel("Color mode", { exact: true }).selectOption(mode);
    for (const theme of themes) {
      await appearance.getByText(theme.label, { exact: true }).click();
      await expect(appearance.getByRole("radio", { name: theme.label, exact: true })).toBeChecked();
      await expect(page.locator("html")).toHaveAttribute("data-color-theme", theme.id);
      await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe(mode);
      const actual = await page.evaluate(() => {
        const root = getComputedStyle(document.documentElement);
        return Object.fromEntries(["--background", "--primary", "--code-keyword", "--code-added-background"].map((key) => [key, root.getPropertyValue(key).trim()]));
      });
      expect(actual).toMatchObject(Object.fromEntries(Object.keys(actual).map((key) => [key, theme[mode][key as keyof typeof theme.light]])));
      await page.reload();
      await expect(appearance.getByRole("radio", { name: theme.label, exact: true })).toBeChecked();
      await expect(appearance.getByLabel("Color mode", { exact: true })).toHaveValue(mode);
      expect(await page.evaluate(() => [localStorage.getItem("color-theme"), localStorage.getItem("theme")])).toEqual([theme.id, mode]);
      await noOverflow(page);
      if (theme.id === "code-mortis" || theme.id === "noodle" || theme.id === "dracula") {
        await page.screenshot({ path: `../.impeccable/review/appearance-${info.project.name}-${theme.id}-${mode}.png`, fullPage: true });
      }
    }
  }
  expect(operationalReads).toEqual([]);
  await appearance.getByText("Code Mortis", { exact: true }).click();
  await page.getByRole("radio", { name: "Code Mortis", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(appearance.getByRole("radio", { name: "Noodle", exact: true })).toBeChecked();
  await page.getByRole("radio", { name: "Aura", exact: true }).focus();
  await page.keyboard.press("Space");
  await expect(page.locator("html")).toHaveAttribute("data-color-theme", "aura");
  await appearance.getByText("Dracula", { exact: true }).click();
  for (const width of info.project.name === "desktop" ? [1636, 1024, 1023] : [320]) {
    await page.setViewportSize({ width, height: 900 });
    await noOverflow(page);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `../.impeccable/review/appearance-${info.project.name}-${width}.png`, fullPage: true });
  }
});

test("appearance system mode and the header toggle keep the selected palette", async ({ page }) => {
  await mock(page);
  await page.goto("/settings?tab=appearance");
  const mode = page.getByLabel("Color mode", { exact: true });
  await page.getByRole("region", { name: "Appearance", exact: true }).getByText("Dracula", { exact: true }).click();
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(mode).toHaveValue("system");
  await page.getByRole("button", { name: "Switch to light mode", exact: true }).click();
  await expect(mode).toHaveValue("light");
  await expect(page.locator("html")).toHaveAttribute("data-color-theme", "dracula");
  await page.emulateMedia({ colorScheme: "light" });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.reload();
  await expect(mode).toHaveValue("light");
  await expect(page.locator("html")).toHaveAttribute("data-color-theme", "dracula");
  await mode.selectOption("system");
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.goto("/repositories/22");
  await page.getByRole("button", { name: "Switch to dark mode", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-color-theme", "dracula");
  await page.route("**/api/v1/repositories/22/sync", (route) => route.fulfill({ json: { job_id: "sync-job" } }));
  await page.getByRole("button", { name: "Sync", exact: true }).click();
  await expect(page.locator("[data-sonner-toast]")).toContainText("Sync queued");
  await expect(page.locator("[data-sonner-toaster]")).toHaveAttribute("data-sonner-theme", "dark");
});

test("appearance themes reach finding dialogs and rendered code diffs", async ({ page }, info) => {
  await mock(page, { finding: { content: "Keep retries bounded.\n\n```python\ndef deliver():\n    return True\n```" } });
  await page.addInitScript(() => localStorage.setItem("color-theme", "dracula"));
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /Keep retries bounded/ }).click();
  const dialog = page.getByRole("dialog");
  const patch = dialog.getByRole("region", { name: "Suggested change diff" });
  const theme = themes.find((item) => item.id === "dracula")!;
  const rgb = (hex: string) => `rgb(${[1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16)).join(", ")})`;
  for (const mode of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: mode });
    await expect(dialog).toHaveCSS("background-color", rgb(theme[mode]["--background"]));
    await expect(patch.locator(".hljs-addition").first()).toHaveCSS("color", rgb(theme[mode]["--code-added"]));
    await expect(patch.locator(".hljs-deletion").first()).toHaveCSS("background-color", rgb(theme[mode]["--code-removed-background"]));
    await expect(dialog.locator(".markdown .hljs-keyword").first()).toHaveCSS("color", rgb(theme[mode]["--code-keyword"]));
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/appearance-code-${info.project.name}-${mode}.png`, animations: "disabled" });
  }
});

test("appearance defaults recover invalid preferences", async ({ page }) => {
  await mock(page);
  await page.addInitScript(() => {
    localStorage.setItem("theme", "invalid");
    localStorage.setItem("color-theme", "unknown-theme");
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/settings?tab=appearance");
  await expect(page.getByRole("radio", { name: "Claude Code", exact: true })).toBeChecked();
  await expect(page.getByLabel("Color mode", { exact: true })).toHaveValue("system");
  await expect(page.locator("html")).toHaveClass("dark");
});

test("appearance retains the legacy saved dark mode", async ({ page }) => {
  await mock(page);
  await page.addInitScript(() => {
    localStorage.setItem("theme", "dark");
    localStorage.removeItem("color-theme");
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/settings?tab=appearance");
  await expect(page.getByLabel("Color mode", { exact: true })).toHaveValue("dark");
  await expect(page.getByRole("radio", { name: "Claude Code", exact: true })).toBeChecked();
  await expect(page.locator("html")).toHaveClass("dark");
});

test("appearance remains usable when local storage is unavailable", async ({ page }) => {
  await mock(page);
  await page.addInitScript(() => {
    for (const method of ["getItem", "setItem"]) {
      Object.defineProperty(Storage.prototype, method, { value: () => { throw new DOMException("Storage disabled", "SecurityError"); } });
    }
  });
  await page.goto("/settings?tab=appearance");
  const appearance = page.getByRole("region", { name: "Appearance", exact: true });
  await appearance.getByText("Noodle", { exact: true }).click();
  await appearance.getByLabel("Color mode", { exact: true }).selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-color-theme", "noodle");
  await expect(page.locator("html")).toHaveClass("dark");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.reload();
  await expect(appearance.getByRole("radio", { name: "Claude Code", exact: true })).toBeChecked();
});

test("members manage personal subscriptions without administrative controls", async ({
  page,
}, info) => {
  await mock(page, { role: "member", personal: true });
  await page.goto("/settings");
  await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link")).toHaveText(["AI connections", "Appearance"]);
  await expect(
    page.getByRole("link", { name: "GitHub", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await expect(page.getByText("ABCD-1234")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Authorize with ChatGPT" }),
  ).toHaveAttribute("href", "https://auth.openai.com/codex/device");
  await expect(page.getByRole("link", { name: "Authorize with ChatGPT" })).toHaveAttribute("data-variant", "default");
  await expect(page.getByText("My ChatGPT · ChatGPT subscription", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveAccessibleDescription("My ChatGPT · ChatGPT subscription");
  await expect(page.getByRole("status").filter({ hasText: "Waiting for sign-in" })).toBeVisible();
  const check = page.getByRole("button", { name: "Check status", exact: true });
  await expect(check).toHaveAttribute("data-variant", "ghost");
  const authorizeBounds = await page.getByRole("link", { name: "Authorize with ChatGPT" }).boundingBox();
  const checkBounds = await check.boundingBox();
  expect(checkBounds!.y).toBeGreaterThan(authorizeBounds!.y + authorizeBounds!.height);
  await expect(page.getByRole("button", { name: "Connect ChatGPT", exact: true })).toHaveCount(0);
  await expect(page.getByRole("form", { name: "Subscription settings" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Repository authorization" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/subscription-${info.project.name}.png`,
    fullPage: true,
  });
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const automatic = page.getByRole("switch", { name: "I authorize automatic reviews with my subscription" });
  await expect(automatic).not.toBeChecked();
  await automatic.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/subscription-consent-${info.project.name}.png` });
});

test("pending subscription sign-in checks retain the code and recover from status errors", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
    { ...connection, id: "personal", kind: "chatgpt", owner_id: 7, model: "", auth_status: "connecting" },
  ] }));
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let reads = 0;
  let fail = true;
  await page.route("**/api/v1/connections/personal/auth", async (route) => {
    expect(route.request().method()).toBe("GET");
    if (++reads === 1) return route.fulfill({ json: {
      status: "disconnected", attempt: { status: "pending", verificationUrl: "https://auth.openai.com/codex/device", userCode: "ABCD-1234" },
    } });
    await held;
    await route.fulfill(fail ? { status: 503, json: { detail: "Account refresh failed." } } : { json: { status: "connected" } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
  const check = account.getByRole("button", { name: "Check status", exact: true });
  await expect(account.getByRole("status").filter({ hasText: "Waiting for sign-in" })).toHaveText("Waiting for sign-in");
  await check.click();
  await expect(check).toBeDisabled();
  await expect(check).toHaveAttribute("aria-busy", "true");
  await expect(account.getByRole("status")).toHaveText("Checking account…");
  release();
  await expect(account.getByRole("alert")).toHaveText("Account refresh failed.");
  await expect(account.getByRole("status")).toHaveText("Could not check sign-in");
  await expect(account.getByText("ABCD-1234", { exact: true })).toBeVisible();
  await expect(check).toBeEnabled();
  fail = false;
  await check.click();
  await expect(page.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "General", exact: true })).toBeFocused();
  await expect(page.getByRole("form", { name: "Subscription settings" })).toBeVisible();
  await expect(account.getByText("ABCD-1234", { exact: true })).toHaveCount(0);
  expect(reads).toBe(3);
});

test("inactive subscription authorization offers reconnect without a status check", async ({ page }, info) => {
  await mock(page, { role: "member", personal: true });
  let attemptStatus = "";
  await page.route("**/api/v1/connections/personal/auth", (route) => {
    if (route.request().method() === "POST") attemptStatus = "pending";
    return route.fulfill({ json: {
      status: "disconnected",
      attempt: attemptStatus ? {
        status: attemptStatus,
        ...(attemptStatus === "pending"
          ? { verificationUrl: "https://auth.openai.com/codex/device", userCode: "ABCD-1234" }
          : { message: "Enable device code authorization in ChatGPT security settings and reconnect." }),
      } : null,
    } });
  });
  for (const status of ["", "failed", "expired"]) {
    attemptStatus = status;
    await page.goto("/settings");
    await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
    const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
    await expect(account.getByRole("button", { name: "Connect ChatGPT", exact: true })).toBeEnabled();
    await expect(account.getByRole("button", { name: "Check status", exact: true })).toHaveCount(0);
    if (status) await expect(account.getByRole("alert").filter({ hasText: "Enable device code authorization" })).toBeVisible();
    if (status === "failed") {
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/subscription-reconnect-${info.project.name}.png` });
    }
  }
  await page.getByRole("button", { name: "Connect ChatGPT", exact: true }).click();
  await expect(page.getByText("ABCD-1234", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Check status", exact: true })).toBeEnabled();
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
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
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
  closed = false;
  await page.getByRole("button", { name: "Connect Claude Code", exact: true }).click();
  await expect.poll(() => messages.length).toBe(3);
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await expect.poll(() => closed).toBe(true);
  await expect(page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true })).toBeFocused();
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
  await page.getByRole("button", { name: "View verification", exact: true }).click();
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

for (const value of ["__proto__", "constructor", "toString", "unknown-check"] as const) {
  test(`GitHub check tooltip handles unknown status ${value}`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await mock(page, { run: { check_conclusion: value, check_status: "completed", check_pending: false } });
    await page.goto("/runs/run");
    await page.getByRole("region", { name: "GitHub publication", exact: true })
      .getByRole("link", { name: /^View check on GitHub:/ }).focus();
    await expect(page.getByRole("tooltip")).toHaveText("The saved GitHub check status is unavailable.");
    expect(errors).toEqual([]);
  });
}

test("run shows failed fix verification beside assessment with a keyboard tooltip", async ({ page }, info) => {
  const writes = await mock(page, { run: {
    status: "completed", publication: "published", publication_error: null,
    check_pending: false, check_error: null, check_status: "completed", check_conclusion: "neutral",
    progress: { verification: { failed: 3, sync_failed: 8, resolved: 2 } },
  }, assessment: { candidates: 10, new_findings: 0, repeated_observations: 1, rejected_candidates: 0, withheld_candidates: 9,
    pending: 0, incomplete: 9, published_comments: 0 } });
  await page.goto("/runs/run");
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  const failed = publication.getByRole("status", { name: "Failed fix verifications: 3", exact: true });
  await expect(failed).toHaveText("Failed 3");
  await expect(failed).toHaveCSS("cursor", "pointer");
  await expect(publication.locator("div > span.text-muted-foreground")).toHaveText(["GitHub comments", "Assessment", "Fix verification", "GitHub check"]);
  if (info.project.name === "desktop") {
    const assessment = await publication.getByText("Assessment", { exact: true }).boundingBox();
    const verification = await publication.getByText("Fix verification", { exact: true }).boundingBox();
    expect(verification!.y).toBe(assessment!.y);
  }
  await failed.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Automatic checks could not verify whether earlier findings were fixed. See Activity → Fix verification for the saved error.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await expect(failed).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/failed-fix-verification-${info.project.name}.png` });
  expect(writes).toEqual([]);
});

for (const [name, verification] of [
  ["missing summary", undefined], ["missing count", { sync_failed: 4 }],
  ["zero failures", { failed: 0, sync_failed: 4 }], ["negative count", { failed: -1 }],
  ["fractional count", { failed: 1.5 }], ["string count", { failed: "3" }],
  ["null summary", null], ["invalid summary", "failed"],
] as const) {
  test(`run hides fix verification badge with ${name}`, async ({ page }) => {
    await mock(page, { run: { progress: { verification } }, assessment: {
      candidates: 5, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 5,
      pending: 0, incomplete: 5, published_comments: 0,
    } });
    await page.goto("/runs/run");
    const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
    await expect(publication.getByText("Partial", { exact: true })).toBeVisible();
    await expect(publication.getByText("Fix verification", { exact: true })).toHaveCount(0);
  });
}

test("run omits verification summary and orders assessment before coverage", async ({ page }) => {
  await mock(page, { run: { progress: { verification: {
    selected: 3, completed: 3, fixed: 1, still_present: 1, uncertain: 1, resolved: 0, pending: 1,
  } } } });
  await page.goto("/runs/run");
  await expect(page.locator("summary").filter({ hasText: "Fix verification" })).toHaveCount(0);
  await expect(page.getByRole("tablist").getByRole("tab")).toHaveText(["Findings 1", "Resolved 0", "Assessment", "Coverage", "Details"]);
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
  await page.getByRole("button", { name: "Save", exact: true }).click();
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
  await page.getByRole("button", { name: "View verification", exact: true }).click();
  const panel = page.getByRole("dialog");
  await expect(panel.getByText("queued", { exact: true })).toHaveCount(1);
  await expect(panel.getByRole("alert")).toContainText("Verification context is unavailable");
  await expect(panel.getByText("Automatic resolution: not applicable")).toHaveCount(2);
  await noOverflow(page);
});

for (const webhook of [undefined, "https://hooks.example.com/a-long-webhook-channel"]) {
  test(`GitHub App icons open its profile and copy the ${webhook ? "configured" : "default"} webhook`, async ({ page }, info) => {
    await mock(page);
    if (webhook) await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
      github_app: { id: "123", slug: "code-mortis-acme", install_url: "/api/v1/installations/start" },
      members: [], failed_jobs: [], public_url: "https://reviews.example.com", webhook_url: webhook,
    } }));
    await page.goto("/settings?tab=github");
    const app = page.getByRole("region", { name: "GitHub App", exact: true });
    await expect(page.getByRole("article", { name: "acme", exact: true }).locator('[data-slot="badge"]')).toHaveText("Installed");
    await expect(page.getByRole("region", { name: "Accounts to connect" })).toHaveCount(0);
    const link = app.getByRole("link", { name: "Open GitHub App on GitHub", exact: true });
    await expect(link).toHaveAttribute("href", "https://github.com/apps/code-mortis-acme");
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noreferrer");
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { value: {
        writeText: async (value: string) => { document.body.dataset.copiedEndpoint = value; },
      } });
    });
    const card = app.locator("dl");
    const height = (await card.boundingBox())!.height;
    await app.getByRole("button", { name: "Copy webhook endpoint", exact: true }).click();
    await expect(page.locator("body")).toHaveAttribute("data-copied-endpoint", webhook ?? "https://reviews.example.com/api/v1/webhooks/github");
    await expect(app.getByRole("status")).toHaveText("Webhook endpoint copied.");
    await expect(app.getByRole("button", { name: "Copy webhook endpoint", exact: true })).toHaveAttribute("title", "Copied");
    expect((await card.boundingBox())!.height).toBe(height);
    await noOverflow(page);
    if (webhook) await page.screenshot({ path: `../.impeccable/review/github-app-icons-${info.project.name}.png`, fullPage: true });
  });
}

test("webhook copy failures retain the endpoint and explain manual recovery", async ({ page }) => {
  await mock(page);
  await page.goto("/settings?tab=github");
  const app = page.getByRole("region", { name: "GitHub App", exact: true });
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { value: {
      writeText: async () => { throw new Error("Clipboard denied"); },
    } });
  });
  await app.getByRole("button", { name: "Copy webhook endpoint", exact: true }).click();
  await expect(app.getByRole("alert")).toHaveText("Could not copy. Select the endpoint and copy it manually.");
  await expect(app).toContainText("https://reviews.example.com/api/v1/webhooks/github");
  await expect(app.getByRole("status")).toHaveCount(0);
  await noOverflow(page);
});

test("GitHub installations manage repositories and confirm uninstall", async ({ page }, info) => {
  const requests = await mock(page);
  let current = { ...installation };
  let deletes = 0;
  await page.route("**/api/v1/installations", (route) => route.fulfill({ json: [
    current,
    { ...installation, id: 12, account: "zeta", status: "suspended" },
    { ...installation, id: 13, account: "beta" },
    { ...installation, id: 14, account: "suspended-account", connected: false,
      status: "suspended", can_connect: false },
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
  const connectedAccounts = page.getByRole("region", { name: "Connected installations" }).getByRole("heading", { level: 4 });
  await expect(connectedAccounts).toHaveText(["acme", "beta", "zeta"]);
  await expect(page.getByRole("region", { name: "Accounts to connect" }).locator(".settings-identity"))
    .toHaveText(["personal-account", "suspended-account"]);
  await expect(page.getByRole("heading", { name: "acme", exact: true })).toHaveCount(1);
  await expect(account.getByRole("link", { name: "Manage repositories" })).toHaveCount(0);
  await expect(page.getByText("personal-account", { exact: true })).toBeVisible();
  await account.getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Manage repositories" })).toHaveAttribute("href", installation.settings_url!);
  await page.getByRole("menuitem", { name: "Resync repositories" }).click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository sync queued for acme" })).toBeVisible();
  await expect(page.getByRole("main").getByText(/Repository sync queued/)).toHaveCount(0);
  expect(requests.some((request) => request.path === "/installations/10" && request.method === "POST")).toBe(true);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/github-installations-${info.project.name}.png`, fullPage: true });

  await account.getByRole("button", { name: "Actions for acme" }).focus();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "Uninstall", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Uninstall from acme?" })).toBeVisible();
  await expect(dialog).toContainText("every repository in the installation");
  await expect(dialog).toContainText("Saved review history");
  await page.screenshot({ path: `../.impeccable/review/github-uninstall-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(deletes).toBe(0);
  await expect(account.getByRole("button", { name: "Actions for acme" })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "Uninstall", exact: true }).press("Enter");
  await dialog.getByRole("button", { name: "Uninstall GitHub App" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(account).toContainText("Waiting for GitHub to confirm removal");
  await expect(connectedAccounts).toHaveText(["beta", "acme", "zeta"]);
  await account.getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Resync repositories" })).toBeDisabled();
  await page.keyboard.press("Escape");
  expect(deletes).toBe(1);
  current = { ...current, active: false, status: "uninstalled", operation_status: "done", settings_url: null, can_remove: true };
  await expect(account.locator('[data-slot="badge"]')).toHaveText("uninstalled");
  await expect(account).toContainText("No repository access.");
  await expect(account.locator(".settings-row-note")).toHaveCount(0);
  await account.getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Remove from list" })).toBeEnabled();
  await expect(page.getByRole("menuitem", { name: /^Uninstall$|^Manage repositories$/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(connectedAccounts).toHaveText(["beta", "zeta", "acme"]);
});

test("GitHub removed accounts require confirmation and retain review history", async ({ page }, info) => {
  await mock(page);
  let removed = false;
  let reject = true;
  let deletes = 0;
  let finishRemoval!: () => void;
  const removalResponse = new Promise<void>((resolve) => { finishRemoval = resolve; });
  const item = { ...installation, active: false, status: "uninstalled", settings_url: null,
    can_connect: false, can_uninstall: false, can_remove: true };
  await page.route("**/api/v1/installations", (route) => route.fulfill({ json: removed ? [] : [item] }));
  await page.route("**/api/v1/installations/10/local", async (route) => {
    expect(route.request().method()).toBe("DELETE");
    expect(route.request().headers()["x-csrf-token"]).toBe("csrf-from-session");
    deletes++;
    if (reject) return route.fulfill({ status: 409, json: { detail: "This account still has an installed GitHub App. Refresh and retry." } });
    await removalResponse;
    removed = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto("/settings?tab=github");
  const account = page.getByRole("article", { name: "acme", exact: true, includeHidden: true });
  const trigger = account.getByRole("button", { name: "Actions for acme" });
  await trigger.click();
  await expect(page.getByRole("menuitem")).toHaveText(["Remove from list"]);
  await page.getByRole("menuitem", { name: "Remove from list" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Remove acme from the list?" })).toBeVisible();
  await expect(dialog).toContainText("Saved repositories and review history are retained.");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/github-remove-confirmation-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toHaveCount(0);
  expect(deletes).toBe(0);
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "Remove from list" }).press("Enter");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(deletes).toBe(0);
  await expect(trigger).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("menuitem", { name: "Remove from list" }).press("Enter");
  await dialog.getByRole("button", { name: "Remove from list" }).click();
  await expect(dialog.getByRole("alert")).toContainText("still has an installed GitHub App");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expect(account).toBeVisible();
  reject = false;
  await dialog.getByRole("button", { name: "Remove from list" }).focus();
  await page.keyboard.press("Enter");
  await expect(dialog.getByRole("button", { name: "Removing…" })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  expect(deletes).toBe(2);
  finishRemoval();
  await expect(dialog).toHaveCount(0);
  await expect(account).toHaveCount(0);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "acme removed from the list." })).toContainText("Saved repositories and review history are retained.");
  await expect(page.getByRole("heading", { name: /^Connected installations/, level: 3 })).toBeFocused();
  await expect(page.getByRole("heading", { name: "No connected installations" })).toBeVisible();
  await noOverflow(page);
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
  await expect(page.getByRole("region", { name: "Connected installations" }).getByRole("heading", { level: 4 }))
    .toHaveText(["permission-update", "acme", "restricted-team"]);
  await expect(account.getByRole("alert")).toContainText("Reviews remain paused");
  const restricted = page.getByRole("article", { name: "restricted-team" });
  await restricted.getByRole("button", { name: "Actions for restricted-team" }).click();
  await expect(page.getByRole("menuitem", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Resync repositories" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(restricted).toContainText("requires an organization owner");
  const permissionUpdate = page.getByRole("article", { name: "permission-update" });
  await expect(permissionUpdate).toContainText("Members: Read-only");
  await expect(permissionUpdate).toContainText("approve the permission update");
  await permissionUpdate.getByRole("button", { name: "Actions for permission-update" }).click();
  await expect(page.getByRole("menuitem", { name: "Uninstall", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Resync repositories" })).toBeEnabled();
  await expect(page.getByRole("menuitem", { name: "Manage repositories" })).toBeVisible();
  await page.keyboard.press("Escape");
  await account.getByRole("button", { name: "Actions for acme" }).click();
  await page.getByRole("menuitem", { name: "Retry uninstall" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Uninstall GitHub App" }).click();
  await expect(page.getByRole("dialog")).toContainText("GitHub ownership changed");
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  refreshError = true;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "GitHub could not be reached" })).toBeVisible();
  await account.getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Retry uninstall" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(account.locator('[data-slot="badge"]')).not.toHaveText("uninstalled");
  refreshError = false;
  current = { ...current, operation_status: "done", status: "uninstalled", can_uninstall: false };
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(account.locator('[data-slot="badge"]')).toHaveText("uninstalled");
  await noOverflow(page);
});

test("GitHub installation lists order and label uninstall operations consistently", async ({ page }) => {
  await mock(page);
  const items: Schema["InstallationOut"][] = [
    { ...installation, id: 11, account: "Removed account", status: "uninstalled", operation_status: "failed", can_connect: false, repository_selection: null },
    { ...installation, id: 12, account: "Failed uninstall", operation_status: "failed", can_connect: false },
    { ...installation, id: 13, account: "Queued uninstall", operation_status: "queued", can_connect: false },
    { ...installation, id: 14, account: "Active account" },
    { ...installation, id: 15, account: "Running uninstall", operation_status: "running", can_connect: false },
    { ...installation, id: 16, account: "Suspended account", status: "suspended", can_connect: false },
  ];
  await page.route("**/api/v1/installations", (route) => route.fulfill({ json: [
    ...items, ...items.map((item) => ({ ...item, id: item.id + 100, connected: false })),
  ] }));
  await page.goto("/settings?tab=github");
  for (const name of ["Connected installations", "Accounts to connect"]) {
    const section = page.getByRole("region", { name, exact: true });
    await expect(section.locator(".settings-identity")).toHaveText([
      "Active account", "Failed uninstall", "Queued uninstall", "Running uninstall", "Suspended account", "Removed account",
    ]);
    await expect(section.locator('[data-slot="badge"]')).toHaveText([
      "Installed", "failed", "uninstalling", "uninstalling", "suspended", "uninstalled",
    ]);
    const rows = section.locator(".settings-record:not(.settings-list-heading)");
    await expect(rows.first()).toContainText("Selected repositories");
    await expect(rows.first().locator('[data-slot="badge"]')).toHaveClass(/bg-emerald/);
    if (name === "Connected installations") await expect(rows.last()).toContainText("No repository access.");
    for (const row of await rows.all()) {
      if (await row.locator('[data-slot="badge"]').textContent() === "Installed") continue;
      await expect(row).not.toContainText("Unknown repository access");
      await expect(row).not.toContainText("Selected repositories");
      await expect(row.locator('[data-slot="badge"]')).not.toHaveClass(/bg-emerald/);
    }
    await expect(section.getByRole("button", { name: /^Connect$|^Retry uninstall$/ })).toHaveCount(0);
  }
});

test("GitHub installation rows adapt without the settings page container", async ({ page }) => {
  await mock(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/settings?tab=github");
  await page.locator(".settings-content").evaluate((element) => { (element as HTMLElement).style.containerType = "normal"; });
  const section = page.getByRole("region", { name: "Connected installations", exact: true });
  for (const width of [360, 640]) {
    await section.evaluate((element, value) => { (element as HTMLElement).style.width = `${value}px`; }, width);
    await expect(section.locator(".settings-list-heading")).toBeVisible({ visible: width >= 640 });
    await expect(section.getByRole("article")).toBeVisible();
    await noOverflow(page);
  }
});

test("GitHub installations refresh on return and connect available accounts", async ({ page }, info) => {
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
  const available = page.getByRole("region", { name: "Accounts to connect" });
  await expect(available).toContainText("Connect a GitHub account to sync its repositories.");
  await expect(available.locator('[data-slot="badge"]')).toHaveText("Installed");
  connected = true;
  await page.getByRole("button", { name: "Actions for acme" }).click();
  await page.getByRole("menuitem", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("article", { name: "acme" })).toContainText("All repositories on GitHub");
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository sync queued for acme" })).toBeVisible();
  await expect(available).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("installation-empty-state.png"), fullPage: true });
  expect(requests.some((request) => request.path === "/installations/10" && request.method === "POST")).toBe(true);
  const before = reads;
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    window.dispatchEvent(new Event("visibilitychange"));
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    window.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => reads).toBeGreaterThan(before);
  await page.getByRole("article", { name: "acme" }).getByRole("button", { name: "Actions for acme" }).click();
  await expect(page.getByRole("menuitem", { name: "Manage repositories" }))
    .toHaveAttribute("href", "https://github.com/settings/installations/10");
});

test("settings sections retain URLs and adapt to the workspace width in both themes", async ({ page }, info) => {
  await mock(page);
  await page.goto("/settings?tab=github");
  const navigation = page.getByRole("navigation", { name: "Settings sections" });
  await expect(navigation.getByRole("link", { name: "GitHub", exact: true })).toHaveAttribute("aria-current", "page");
  await navigation.getByRole("link", { name: "Team", exact: true }).click();
  await expect(page).toHaveURL(/tab=team$/);
  await page.goBack();
  await expect(navigation.getByRole("link", { name: "GitHub", exact: true })).toHaveAttribute("aria-current", "page");
  await page.goForward();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Team", exact: true })).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: theme as "light" | "dark" });
    for (const section of ["AI connections", "GitHub", "Team", "Appearance"]) {
      const link = navigation.getByRole("link", { name: section, exact: true });
      await link.click();
      await expect(link).toHaveAttribute("aria-current", "page");
      await expect(page.getByRole("heading", { name: section, exact: true })).toBeVisible();
      await page.mouse.move(0, 0);
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/settings-${section.replaceAll(" ", "-")}-${info.project.name}-${theme}.png`, fullPage: true });
    }
  }
  await navigation.getByRole("link", { name: "Team", exact: true }).click();
  for (const width of info.project.name === "desktop" ? [1024, 1023] : [320]) {
    await page.setViewportSize({ width, height: 900 });
    await noOverflow(page);
    const nav = await navigation.boundingBox();
    const content = await page.getByRole("region", { name: "Team", exact: true }).boundingBox();
    if (width >= 1024) {
      expect(nav!.width).toBe(176);
      expect(content!.x - nav!.x - nav!.width).toBe(24);
    } else expect(content!.y).toBeGreaterThan(nav!.y + nav!.height);
    await page.screenshot({ path: `../.impeccable/review/settings-breakpoint-${width}.png`, fullPage: true });
  }
});

test("settings rows retain labels and fit enlarged text at container breakpoints", async ({ page }, info) => {
  await mock(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/settings?tab=github");
  await expect(page.getByRole("article", { name: "acme", exact: true })).toBeVisible();
  await page.addStyleTag({ content: ".settings-content { width: 640px; max-width: 100%; }" });
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const fontSize of [16, 32]) {
      await page.evaluate((size) => { document.documentElement.style.fontSize = `${size}px`; }, fontSize);
      const row = page.getByRole("article", { name: "acme", exact: true });
      await expect(row.locator('[data-slot="badge"]')).toHaveCSS("font-size", `${fontSize * 0.75}px`);
      await expect(row.getByRole("button", { name: "Actions for acme" })).toHaveCSS("font-size", `${fontSize * 0.875}px`);
      const clipped = await row.evaluate((element) => [...element.querySelectorAll('[data-slot="button"], [data-slot="badge"]')]
        .filter((control) => {
          const bounds = control.getBoundingClientRect();
          const parent = control.parentElement!.getBoundingClientRect();
          return bounds.left < parent.left - 1 || bounds.right > parent.right + 1 || control.scrollWidth > control.clientWidth + 1;
        }).map((control) => control.textContent));
      expect.soft(clipped).toEqual([]);
      const labels = await row.ariaSnapshot();
      expect.soft(labels).toContain("Repository access");
      expect.soft(labels).toContain("Status");
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/settings-text-${info.project.name}-${theme}-${fontSize}.png`, fullPage: true });
    }
  }
});

test("settings rows stack in narrow containers and keep icon targets and accessible labels", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/installations", (route) => route.fulfill({ json: [
    installation, { ...installation, id: 11, account: "Removed", status: "uninstalled" },
  ] }));
  await page.goto("/settings?tab=github");
  await expect(page.getByRole("article", { name: "acme", exact: true })).toBeVisible();
  await page.addStyleTag({ content: ".settings-content { width: 280px; max-width: 100%; }" });
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    const row = page.getByRole("article", { name: "acme", exact: true });
    const access = await row.getByText("Repository access", { exact: true }).boundingBox();
    const status = await row.getByText("Status", { exact: true }).boundingBox();
    expect(status!.y).toBeGreaterThan(access!.y + access!.height);
    const icon = row.getByRole("button", { name: "Actions for acme" });
    await expect(icon).toHaveCSS("width", "32px");
    await expect(icon).toHaveCSS("height", "32px");
    await expect(page.getByRole("article", { name: "Removed", exact: true }).getByRole("button", { name: "Actions for Removed" })).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/settings-narrow-${info.project.name}-${theme}.png`, fullPage: true });
  }
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "AI connections" }).click();
  const connectionRow = page.getByRole("article", { name: "Team reviews", exact: true });
  await expect(connectionRow).toBeVisible();
  for (const width of [280, 640]) {
    await page.locator(".settings-content").evaluate((element, value) => { (element as HTMLElement).style.width = `${value}px`; }, width);
    const accessible = await connectionRow.ariaSnapshot();
    expect(accessible.match(/Default model/g)).toHaveLength(1);
    expect(accessible.match(/Account status/g)).toHaveLength(1);
  }
});

test("settings group visible connections and distinguish tests from authentication", async ({ page }, info) => {
  await mock(page);
  const longName = "A connection with a long name " + "x".repeat(100);
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
    { ...connection, name: longName, model: "model-" + "x".repeat(160) },
    { ...connection, id: "untested", name: "Untested API", last_test: null, tested_at: null },
    { ...connection, id: "personal", name: "My subscription", kind: "chatgpt", owner_id: 7, auth_status: "connected", last_test: null },
    { ...connection, id: "claude", name: "My Claude subscription", kind: "claude_code", owner_id: 7, auth_status: "disconnected", last_test: null },
    { ...connection, id: "future", name: "Another subscription", kind: "future_provider", owner_id: 7, auth_status: "disconnected", last_test: null },
  ] }));
  await page.goto("/settings");
  const shared = page.getByRole("region", { name: "Shared API connections" });
  const personal = page.getByRole("region", { name: "Your subscriptions" });
  await expect(shared.getByRole("article")).toHaveCount(2);
  await expect(personal.getByRole("article")).toHaveCount(3);
  await expect(shared.getByRole("article").first()).toHaveAccessibleName(longName);
  await expect(shared.getByRole("article").first().locator('[data-slot="badge"]')).toHaveText("connected");
  await expect(shared.getByRole("article").first().locator(".lucide-check")).toBeVisible();
  await expect(shared).not.toContainText("Model completed a tool-call round trip.");
  await expect(shared.locator("time")).toHaveCount(0);
  await expect(shared).toContainText("Not tested");
  await expect(personal.getByText("connected", { exact: true })).toBeVisible();
  await expect(personal.getByRole("article", { name: "My subscription", exact: true }).locator(".lucide-check")).toBeVisible();
  await expect(shared).toContainText("openai · openai");
  await expect(personal.getByRole("article", { name: "My subscription", exact: true })).toContainText("ChatGPT subscription");
  await expect(personal.getByRole("article", { name: "My Claude subscription", exact: true })).toContainText("Claude Code subscription");
  await expect(personal.getByRole("article", { name: "Another subscription", exact: true })).toContainText("Personal subscription");
  for (const name of [longName, "My subscription", "My Claude subscription"]) {
    const logo = page.getByRole("article", { name, exact: true }).locator(".settings-identity img");
    await expect(logo).toHaveAttribute("alt", "");
    await expect.poll(() => logo.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
  }
  await expect(personal.getByRole("article", { name: "Another subscription", exact: true }).locator(".lucide-user-round")).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/subscription-logos-light-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/subscription-logos-dark-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
  await expect(personal).not.toContainText("Last test");
  await expect(shared.getByRole("button", { name: /Test connection|Test again|Testing…/ })).toHaveCount(0);
  await expect(personal.getByRole("button", { name: "Manage", exact: true })).toHaveCount(0);
  const claudeRow = personal.getByRole("article", { name: "My Claude subscription", exact: true });
  await expect(claudeRow.getByRole("status").getByRole("button", { name: "Complete sign in", exact: true })).toHaveAttribute("data-variant", "link");
  await expect(claudeRow.locator('[data-slot="badge"]')).toHaveCount(0);
  await expect(claudeRow.locator(".settings-actions").getByRole("button", { name: "Complete sign in", exact: true })).toHaveCount(0);
  const apiActions = shared.getByRole("button", { name: "Actions for Untested API", exact: true });
  await apiActions.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveText(["Edit", "Test connection", "Delete"]);
  await page.keyboard.press("Escape");
  await expect(apiActions).toBeFocused();
  const personalActions = personal.getByRole("button", { name: "Actions for My subscription", exact: true });
  await personalActions.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveText(["Edit", "Delete"]);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/settings-groups-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(personalActions).toBeFocused();
  await personalActions.press("Enter");
  await page.getByRole("menuitem", { name: "Edit", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog", { name: "Edit subscription", exact: true });
  await expect(dialog).toBeVisible();
  await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(personalActions).toBeFocused();
});

test("shared API logos cover the provider catalog with custom and unknown fallbacks", async ({ page }, info) => {
  await mock(page);
  const catalog: { name: string; displayName: string; protocol: string }[] = JSON.parse(
    await readFile(new URL("../../backend/code_mortis/providers.json", import.meta.url), "utf8"),
  );
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
    ...catalog.map((provider) => ({
      ...connection, id: provider.name, name: provider.displayName, provider: provider.name, protocol: provider.protocol,
    })),
    { ...connection, id: "unknown", name: "Unknown provider", provider: "constructor" },
  ] }));
  await page.goto("/settings?tab=connections");
  const shared = page.getByRole("region", { name: "Shared API connections", exact: true });
  await expect(shared.getByRole("article")).toHaveCount(catalog.length + 1);
  for (const provider of catalog) {
    const row = shared.getByRole("article", { name: provider.displayName, exact: true });
    const logo = row.locator(".settings-identity img");
    if (provider.name === "custom") {
      await expect(logo).toHaveCount(0);
      await expect(row.locator(".lucide-plug")).toBeVisible();
    } else {
      await expect(logo, provider.name).toHaveAttribute("src", /^\/brands\/.+\.svg$/);
      await expect(logo).toHaveAttribute("alt", "");
      await expect.poll(() => logo.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0), { message: provider.name }).toBe(true);
    }
  }
  const unknown = shared.getByRole("article", { name: "Unknown provider", exact: true });
  await expect(unknown.locator(".settings-identity img")).toHaveCount(0);
  await expect(unknown.locator(".lucide-plug")).toBeVisible();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(page.locator("html")).toHaveClass(colorScheme === "dark" ? "dark" : "");
    await noOverflow(page);
    await shared.screenshot({ path: `../.impeccable/review/api-provider-logos-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
  }
});

test("connection selectors show logos in options and selected values, but hide them during search", async ({ page }, info) => {
  await mock(page);
  const catalog: { name: string; displayName: string }[] = JSON.parse(
    await readFile(new URL("../../backend/code_mortis/providers.json", import.meta.url), "utf8"),
  );
  await page.route("**/api/v1/providers", (route) => route.fulfill({ json: catalog }));
  await page.goto("/settings?tab=connections");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const type = dialog.getByRole("combobox", { name: "Connection type", exact: true });
  const provider = dialog.getByRole("combobox", { name: "Provider", exact: true });
  const typeIcon = type.locator("..").locator('[data-align="inline-start"]');
  const providerIcon = provider.locator("..").locator('[data-align="inline-start"]');
  await dialog.getByLabel("Connection name", { exact: true }).focus();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(page.locator("html")).toHaveClass(colorScheme === "dark" ? "dark" : "");
    await expect(typeIcon.locator(".lucide-plug")).toBeVisible();
    await expect(providerIcon.locator("img")).toHaveAttribute("src", "/brands/openai.svg");
    await expect(typeIcon).toHaveAttribute("role", "group");
    await expect(typeIcon).not.toHaveAttribute("aria-hidden");
    await expect(typeIcon.locator("span")).toHaveAttribute("aria-hidden", "true");
    await typeIcon.click();
    await expect(type).toBeFocused();
    await dialog.getByLabel("Connection name", { exact: true }).focus();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/selected-connection-logos-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
    await type.fill("");
    const shared = page.getByRole("option", { name: "Shared API connection", exact: true });
    await expect(shared.locator(".lucide-plug")).toBeVisible();
    for (const [label, logo] of [["Personal ChatGPT subscription", "openai"], ["Personal Claude Code subscription", "claude"]]) {
      await expect(page.getByRole("option", { name: label, exact: true }).locator("img")).toHaveAttribute("src", `/brands/${logo}.svg`);
    }
    await expect(typeIcon).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/connection-type-logos-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
    await type.fill("claude");
    await expect(typeIcon).toHaveCount(0);
    await type.press("Escape");
    await expect(type).toHaveValue("Shared API connection");
    await expect(typeIcon.locator(".lucide-plug")).toBeVisible();
    await provider.fill("");
    await expect(page.getByRole("option")).toHaveCount(catalog.length);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/provider-option-logos-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
    await provider.fill("no such provider");
    await expect(providerIcon).toHaveCount(0);
    await provider.press("Escape");
    await expect(provider).toHaveValue("OpenAI API");
    await expect(providerIcon.locator("img")).toHaveAttribute("src", "/brands/openai.svg");
  }
  for (const preset of catalog) {
    await provider.fill(preset.displayName);
    const option = page.getByRole("option", { name: preset.displayName, exact: true });
    const logo = option.locator("img");
    if (preset.name === "custom") {
      await expect(logo).toHaveCount(0);
      await expect(option.locator(".lucide-plug")).toBeVisible();
    } else {
      await expect(logo).toHaveAttribute("alt", "");
      await expect(logo).toHaveAttribute("aria-hidden", "true");
      await expect.poll(() => logo.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0), { message: preset.name }).toBe(true);
    }
    await expect(provider.locator("..").locator("img")).toHaveCount(0);
  }
  await provider.fill("anth");
  await provider.press("Enter");
  await expect(provider).toHaveValue("Anthropic Claude API");
  await expect(providerIcon.locator("img")).toHaveAttribute("src", "/brands/claude.svg");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await type.fill("claude");
  await type.press("Enter");
  await expect(type).toHaveValue("Personal Claude Code subscription");
  await expect(typeIcon.locator("img")).toHaveAttribute("src", "/brands/claude.svg");
  await expect(dialog.getByRole("combobox", { name: "Provider", exact: true })).toHaveCount(0);
});

for (const kind of ["chatgpt", "claude_code"] as const) {
  test(`${kind} subscription sign-in links replace pending badges and resume setup`, async ({ page }, info) => {
    const requests = await mock(page, { role: "member", personal: true, claude: kind === "claude_code" });
    await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
      ...["disconnected", "connecting", "revoked", "connected", "unavailable"].map((status) => ({
        ...connection, id: status === "disconnected" ? "personal" : status, name: status, kind, owner_id: 7, auth_status: status,
      })),
      { ...connection, id: "another-owner", name: "Another account", kind, owner_id: 8, auth_status: "disconnected" },
    ] }));
    await page.goto("/settings");
    for (const status of ["disconnected", "connecting", "revoked"]) {
      const row = page.getByRole("article", { name: status, exact: true });
      const signIn = row.getByRole("status").getByRole("button", { name: "Complete sign in", exact: true });
      await expect(signIn).toHaveAttribute("data-variant", "link");
      await expect(signIn).toHaveClass(/underline/);
      await expect(row.locator('[data-slot="badge"]')).toHaveCount(0);
      await expect(row.locator(".settings-actions").getByRole("button", { name: /Complete sign in|Continue setup/ })).toHaveCount(0);
    }
    for (const status of ["connected", "unavailable"]) {
      const row = page.getByRole("article", { name: status, exact: true });
      await expect(row.locator('[data-slot="badge"]')).toHaveText(status);
      await expect(row.getByRole("button", { name: "Complete sign in", exact: true })).toHaveCount(0);
    }
    const other = page.getByRole("article", { name: "Another account", exact: true });
    await expect(other.locator('[data-slot="badge"]')).toHaveText("disconnected");
    await expect(other.getByRole("button")).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/${kind}-sign-in-status-${info.project.name}.png`, fullPage: true });
    const signIn = page.getByRole("article", { name: "disconnected", exact: true }).getByRole("button", { name: "Complete sign in", exact: true });
    await signIn.press("Enter");
    const dialog = page.getByRole("dialog", { name: "Edit subscription", exact: true });
    await expect(dialog.getByRole("heading", { name: "Edit subscription", exact: true })).toBeVisible();
    await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    await page.keyboard.press("Escape");
    await expect(signIn).toBeFocused();
    expect(requests.filter((request) => request.path.endsWith("/auth") && request.method === "POST")).toHaveLength(0);
  });
}

test("API test badges show a spinner while queued and a check after success", async ({ page }, info) => {
  await mock(page);
  await page.clock.install();
  let result: string | null = null;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
    { ...connection, last_test: result },
    { ...connection, id: "other", name: "Other API", last_test: null },
  ] }));
  await page.route("**/api/v1/connections/connection/test", async (route) => {
    result = "Testing connection…";
    await held;
    await route.fulfill({ status: 202, json: { job_id: "test-job" } });
  });
  await page.goto("/settings");
  const row = page.getByRole("article", { name: "Team reviews", exact: true });
  const badge = row.locator('[data-slot="badge"]');
  const actions = row.getByRole("button", { name: "Actions for Team reviews" });
  await expect(badge).toHaveText("Not tested");
  await expect(row.getByRole("button", { name: /Test connection|Test again|Testing…/ })).toHaveCount(0);
  await actions.click();
  await page.getByRole("menuitem", { name: "Test connection", exact: true }).click();
  await expect(actions).toBeDisabled();
  await expect(badge).toHaveText("Testing");
  await expect(badge.locator(".lucide-loader-circle")).toHaveClass(/motion-safe:animate-spin/);
  await expect(page.getByRole("article", { name: "Other API" }).locator('[data-slot="badge"]')).toHaveText("Not tested");
  await page.screenshot({ path: `../.impeccable/review/api-testing-${info.project.name}.png`, fullPage: true });
  release();
  await expect(actions).toBeEnabled();
  await expect(row.getByRole("status")).toHaveAttribute("title", "Testing connection…");
  await actions.click();
  await expect(page.getByRole("menuitem", { name: "Testing…", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  result = connection.last_test;
  await page.clock.runFor(5000);
  await expect(badge).toHaveText("connected");
  await expect(badge.locator(".lucide-loader-circle")).toHaveCount(0);
  await expect(badge.locator(".lucide-check")).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/api-connected-${info.project.name}.png`, fullPage: true });
  await actions.click();
  await expect(page.getByRole("menuitem", { name: "Test again", exact: true })).toBeEnabled();
  await page.screenshot({ path: `../.impeccable/review/api-test-menu-${info.project.name}.png`, fullPage: true });
  await page.getByRole("menuitem", { name: "Test again", exact: true }).click();
  await expect(row.getByRole("status")).toHaveAttribute("title", "Testing connection…");
  result = "OCR connection test failed. Check credentials, protocol, and model.";
  await page.clock.runFor(5000);
  await expect(badge).toHaveText("failed");
  await expect(badge.locator("svg")).toHaveCount(0);
  await expect(row.getByRole("status")).toHaveAccessibleDescription(result);
  await actions.click();
  await expect(page.getByRole("menuitem", { name: "Test connection", exact: true })).toBeEnabled();
  await noOverflow(page);
});

test("API status badges retain failure details without expanding the row", async ({ page }) => {
  await mock(page);
  const longResult = "Provider response: " + "request-context/".repeat(80);
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [
    { ...connection, name: "Short result", last_test: "Connected." },
    { ...connection, id: "long", name: "Long result", last_test: longResult },
  ] }));
  await page.goto("/settings");
  for (const [name, result] of [["Short result", "Connected."], ["Long result", longResult]]) {
    const row = page.getByRole("article", { name, exact: true });
    await expect(row.locator('[data-slot="badge"]')).toHaveText(name === "Short result" ? "connected" : "failed");
    await expect(row.getByRole("status")).toHaveAttribute("title", result);
    await expect(row.getByRole("status")).toHaveAccessibleDescription(result);
    await expect(row).not.toContainText(result);
    await expect(row.locator("time")).toHaveCount(0);
    await expect(row.locator("summary")).toHaveCount(0);
  }
  await noOverflow(page);
});

test("background errors show saved metadata and omit unavailable dates and attempts", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], members: [], public_url: "https://reviews.example.com",
    failed_jobs: [
      { id: "failed-job", kind: "generate_background", error: "Check the connection and retry.", available_at: at, attempts: 2 },
      { id: "legacy-job", kind: "review", error: "The operation exceeded its configured time limit." },
      { id: "undispatched-job", kind: "sync_check", error: "GitHub unavailable.", available_at: null, attempts: 0 },
    ],
  } }));
  await page.goto("/settings");
  await page.getByText("Recent background errors (3)", { exact: true }).click();
  const dated = page.getByRole("listitem").filter({ hasText: "Job failed-job" });
  await expect(dated).toContainText("generate_background");
  await expect(dated).toContainText("Check the connection and retry.");
  await expect(dated).toContainText("2 attempts");
  const formatted = await page.evaluate((value) => new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(new Date(value)), at);
  await expect(dated.locator("time")).toHaveAttribute("datetime", at);
  await expect(dated.locator("time")).toHaveText(`Scheduled ${formatted}`);
  const legacy = page.getByRole("listitem").filter({ hasText: "Job legacy-job" });
  await expect(legacy.locator("time")).toHaveCount(0);
  await expect(legacy).not.toContainText("Scheduled");
  await expect(legacy).not.toContainText("attempt");
  const undispatched = page.getByRole("listitem").filter({ hasText: "Job undispatched-job" });
  await expect(undispatched.locator("time")).toHaveCount(0);
  await expect(undispatched).toContainText("0 attempts");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/background-errors-${info.project.name}.png`, fullPage: true });
});

test("settings keep loading and failed requests distinct from empty connections", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections", async (route) => {
    await held;
    await route.fulfill({ status: 503, json: { detail: "Connections could not be loaded." } });
  });
  await page.goto("/settings");
  await expect(page.getByText("Loading workspace…")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toContainText("Connections could not be loaded");
  await expect(page.getByRole("region", { name: "Shared API connections" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Your subscriptions" })).toHaveCount(0);
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [] }));
  await page.reload();
  await expect(page.getByRole("region", { name: "Shared API connections" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Your subscriptions" })).toBeVisible();
});

for (const role of ["admin", "member"] as const) {
  test(`empty connection groups show the next actions allowed for ${role}s`, async ({ page }, info) => {
    await mock(page, { role });
    await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [] }));
    await page.goto("/settings?tab=connections");
    const shared = page.getByRole("region", { name: "Shared API connections", exact: true });
    const subscriptions = page.getByRole("region", { name: "Your subscriptions", exact: true });
    await expect(shared.getByRole("heading")).toHaveText("Shared API connections0");
    await expect(subscriptions.getByRole("heading")).toHaveText("Your subscriptions0");
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/empty-connections-${role}-${info.project.name}.png`, fullPage: true });

    const connect = subscriptions.getByRole("button", { name: "Connect subscription", exact: true });
    await connect.click();
    const dialog = page.getByRole("dialog");
    const type = dialog.getByRole("combobox", { name: "Connection type", exact: true });
    await expect(type).toHaveValue("Personal ChatGPT subscription");
    await expect(dialog.getByLabel("API key", { exact: true })).toHaveCount(0);
    await expectOption(page, type, "Shared API connection", role === "admin" ? 1 : 0);
    await chooseOption(page, type, "Personal Claude Code subscription");
    await expect(page.locator('[data-slot="popover-content"]')).toHaveCount(0);
    await expect(dialog.getByRole("button", { name: "Continue to sign in", exact: true })).toBeEnabled();
    await dialog.getByLabel("Connection name").focus();
    await page.keyboard.press("Escape");
    await expect(connect).toBeFocused();

    const addApi = shared.getByRole("button", { name: "Add API connection", exact: true });
    if (role === "admin") {
      await addApi.click();
      await expect(type).toHaveValue("Shared API connection");
      await expect(dialog.getByLabel("API key", { exact: true })).toBeVisible();
      await dialog.getByLabel("API key", { exact: true }).focus();
      await page.keyboard.press("Escape");
      await expect(addApi).toBeFocused();
    } else {
      await expect(addApi).toHaveCount(0);
      await expect(shared).toContainText("An administrator can add an API connection for your team.");
    }
    await page.getByRole("button", { name: "Add connection", exact: true }).click();
    await expect(type).toHaveValue(role === "admin" ? "Shared API connection" : "Personal ChatGPT subscription");
  });
}

test("connection menus restore focus and deletion preserves server conflicts", async ({ page }) => {
  await mock(page);
  let removed = false;
  let conflict = true;
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: removed ? [] : [connection] }));
  await page.route("**/api/v1/connections/connection", (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    if (conflict) return route.fulfill({ status: 409, json: { detail: "Change repositories using this connection before deleting it." } });
    removed = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto("/settings");
  const trigger = page.getByRole("button", { name: "Actions for Team reviews" });
  await trigger.focus();
  await trigger.press("Enter");
  await page.getByRole("menuitem", { name: "Edit", exact: true }).press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("tab")).toHaveText(["General", "Connection", "Advanced"]);
  await expect(page.getByRole("combobox", { name: "Connection type", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await trigger.press("Enter");
  await page.getByRole("menuitem", { name: "Delete", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Delete connection" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Change repositories");
  await expect(page.getByRole("alert")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("alert")).toHaveCount(0);
  conflict = false;
  await trigger.click();
  await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
  await dialog.getByRole("button", { name: "Delete connection" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "AI connections", exact: true })).toBeFocused();
  await expect(page.getByRole("region", { name: "Shared API connections" }).getByRole("button", { name: "Add API connection", exact: true })).toBeVisible();
});

for (const scenario of [
  { tab: "connections", trigger: "Actions for Team reviews", item: "Edit" },
  { tab: "connections", trigger: "Actions for Team reviews", item: "Delete" },
  { tab: "github", trigger: "Actions for acme", item: "Uninstall" },
  { tab: "team", trigger: "Actions for @sam", item: "Edit role" },
  { tab: "team", trigger: "Actions for @sam", item: "Remove" },
]) test(`settings menu hands focus directly to its dialog: ${scenario.item}`, async ({ page }) => {
  await mock(page);
  if (scenario.tab === "team") await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], failed_jobs: [], public_url: "https://reviews.example.com",
    members: [{ id: 7, login: "carlos", role: "admin" }, { id: 8, login: "sam", role: "member" }],
  } }));
  await page.goto(`/settings?tab=${scenario.tab}`);
  const trigger = page.getByRole("button", { name: scenario.trigger, exact: true, includeHidden: true });
  const menu = page.getByRole("menu", { includeHidden: true });
  const dialog = page.getByRole("dialog");
  await trigger.focus();
  await trigger.press("Enter");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();

  for (const activation of ["keyboard", "pointer", "no-animation"]) {
    if (activation === "no-animation") {
      await page.addStyleTag({ content: '[data-slot="dropdown-menu-content"] { animation: none !important; }' });
    }
    if (activation !== "pointer") await trigger.press("Enter");
    else await trigger.click();
    const item = page.getByRole("menuitem", { name: scenario.item, exact: true });
    await item.focus();
    await trigger.evaluate((element) => {
      element.dataset.focusReturns = "0";
      const focus = element.focus.bind(element);
      element.focus = (options) => {
        element.dataset.focusReturns = String(Number(element.dataset.focusReturns) + 1);
        focus(options);
      };
    });
    if (activation !== "pointer") await item.press("Enter");
    else await item.click();
    await expect(dialog).toBeVisible();
    await expect(menu).toHaveCount(0);
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    for (const key of ["Shift+Tab", "Tab"]) {
      await page.keyboard.press(key);
      await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    }
    await expect(trigger).toHaveAttribute("data-focus-returns", "0");
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
  }
});

test("team rows preserve unknown roles and align labels with resized avatars in both directions", async ({ page }, info) => {
  await mock(page);
  const role = "repository-observer-with-limited-access";
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], failed_jobs: [], public_url: "https://reviews.example.com",
    members: [{ id: 8, login: "observer", role }],
  } }));
  await page.goto("/settings?tab=team");
  await expect(page.getByText(role, { exact: true })).toBeVisible();
  await page.locator(".settings-content").evaluate((element) => { (element as HTMLElement).style.width = "280px"; });
  const row = page.locator(".settings-member-row").filter({ hasText: "@observer" });
  for (const direction of ["ltr", "rtl"]) {
    await page.evaluate((value) => { document.documentElement.dir = value; }, direction);
    for (const avatarSize of ["2rem", "3rem"]) {
      await row.evaluate((element, value) => {
        (element as HTMLElement).style.setProperty("--settings-avatar-size", value);
        (element as HTMLElement).style.setProperty("--settings-member-gap", "1rem");
      }, avatarSize);
      await expect.poll(() => row.evaluate((element) => {
        const name = element.querySelector(".settings-member-identity p")!.getBoundingClientRect();
        const badge = element.querySelector('[data-slot="badge"]')!.getBoundingClientRect();
        return getComputedStyle(element).direction === "ltr" ? badge.left - name.left : badge.right - name.right;
      })).toBeCloseTo(0, 0);
      expect(await row.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    }
    await page.screenshot({ path: `../.impeccable/review/settings-member-${info.project.name}-${direction}.png`, fullPage: true });
  }
});

test("member dialog preserves drafts, saves roles, and reports removal failures", async ({ page }, info) => {
  await mock(page);
  let members = [{ id: 7, login: "carlos", role: "admin" }];
  let fail = true;
  let release: (() => void) | undefined;
  let releaseRemoval!: () => void;
  const removal = new Promise<void>((resolve) => { releaseRemoval = resolve; });
  const bodies: unknown[] = [];
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: { id: "123", slug: "code-mortis-acme", install_url: "https://github.com/apps/code-mortis-acme" },
    installations: [], members, failed_jobs: [], public_url: "https://reviews.example.com",
  } }));
  await page.route("**/api/v1/members", async (route) => {
    const body = route.request().postDataJSON();
    bodies.push(body);
    if (fail) {
      await new Promise<void>((resolve) => { release = resolve; });
      return route.fulfill({ status: 422, json: { detail: "GitHub account could not be verified." } });
    }
    members = members.some((item) => item.login === body.login)
      ? members.map((item) => item.login === body.login ? { ...item, role: body.role } : item)
      : [...members, { id: 8, ...body }];
    await route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/v1/members/8", async (route) => {
    await removal;
    await route.fulfill({ status: 409, json: { detail: "Membership changed. Refresh and retry." } });
  });
  await page.goto("/settings?tab=team");
  const add = page.getByRole("button", { name: "Add member", exact: true });
  await add.click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("GitHub username").fill("sam");
  const role = dialog.getByRole("combobox", { name: "Role", exact: true });
  await role.fill("admin");
  await expect(page.getByRole("option", { name: "Administrator", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "Member", exact: true })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/member-role-combobox-${info.project.name}.png` });
  await role.press("Enter");
  await dialog.getByRole("button", { name: "Add or update member" }).click();
  await expect(dialog.getByRole("button", { name: "Saving…" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Actions for @carlos", includeHidden: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add member", exact: true, includeHidden: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await expect.poll(() => !!release).toBe(true);
  release!();
  await expect(dialog.getByRole("alert")).toContainText("could not be verified");
  await expect(dialog.getByLabel("GitHub username")).toHaveValue("sam");
  await expect(dialog.getByLabel("Role", { exact: true })).toHaveValue("Administrator");
  await page.screenshot({ path: `../.impeccable/review/settings-member-${info.project.name}.png` });
  fail = false;
  await dialog.getByRole("button", { name: "Add or update member" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(add).toBeFocused();
  await expect(page.getByText("@sam", { exact: true })).toBeVisible();
  const actions = page.getByRole("button", { name: "Actions for @sam" });
  await actions.click();
  await page.getByRole("menuitem", { name: "Edit role" }).click();
  await expect(dialog.getByLabel("GitHub username")).toHaveAttribute("readonly", "");
  await expect(dialog.getByLabel("GitHub username")).toHaveValue("sam");
  await chooseOption(page, dialog.getByLabel("Role", { exact: true }), "Member");
  await dialog.getByRole("button", { name: "Save role" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(actions).toBeFocused();
  expect(bodies.at(-1)).toEqual({ login: "sam", role: "member" });
  await actions.click();
  await page.getByRole("menuitem", { name: "Remove", exact: true }).click();
  await dialog.getByRole("button", { name: "Remove member", exact: true }).click();
  await expect(page.getByRole("button", { name: "Actions for @sam", includeHidden: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Add member", exact: true, includeHidden: true })).toBeDisabled();
  releaseRemoval();
  await expect(dialog.getByRole("alert")).toContainText("Membership changed");
  await expect(dialog.getByRole("button", { name: "Remove member", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(actions).toBeFocused();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("@sam", { exact: true })).toBeVisible();
});

test("team retains the last administrator and protects existing roles in the add form", async ({ page }, info) => {
  const requests = await mock(page);
  const bodies: unknown[] = [];
  await page.route("**/api/v1/members", async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto("/settings?tab=team");
  await page.getByRole("button", { name: "Actions for @carlos" }).click();
  await expect(page.getByRole("menuitem", { name: "Remove", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Edit role", exact: true })).toBeDisabled();
  await expect(page.getByRole("menu")).toHaveText("Edit roleRemove");
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/team-last-administrator-${info.project.name}-${theme}.png` });
  }
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Add member", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("GitHub username").fill("CARLOS");
  await expect(dialog.getByRole("combobox", { name: "Role", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("combobox", { name: "Role", exact: true })).toHaveValue("Administrator");
  await expect(dialog).toContainText("Administrator access must be retained.");
  await dialog.getByRole("button", { name: "Add or update member" }).click();
  await expect(dialog).toHaveCount(0);
  expect(bodies).toEqual([{ login: "CARLOS", role: "admin" }]);
  expect(requests.some((request) => request.path.startsWith("/members/") && request.method === "DELETE")).toBe(false);
});

test("member removal requires confirmation and restores focus after cancellation or success", async ({ page }, info) => {
  await mock(page);
  let members = [{ id: 7, login: "carlos", role: "admin" }, { id: 8, login: "sam", role: "member" }];
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], members, failed_jobs: [], public_url: "https://reviews.example.com",
  } }));
  let deletes = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/members/8", async (route) => {
    expect(route.request().method()).toBe("DELETE");
    deletes++;
    await held;
    members = members.filter((member) => member.id !== 8);
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto("/settings?tab=team");
  const actions = page.getByRole("button", { name: "Actions for @sam", exact: true });
  const dialog = page.getByRole("dialog");
  for (const dismissal of ["Escape", "Cancel"]) {
    await actions.click();
    await page.getByRole("menuitem", { name: "Remove", exact: true }).click();
    await expect(dialog.getByRole("heading", { name: "Remove @sam?", exact: true })).toBeVisible();
    await expect(dialog).toContainText("ends their sessions");
    await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    expect(deletes).toBe(0);
    if (dismissal === "Escape") await page.keyboard.press("Escape");
    else await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect(actions).toBeFocused();
  }
  await actions.click();
  await page.getByRole("menuitem", { name: "Remove", exact: true }).click();
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/member-removal-${info.project.name}-${theme}.png` });
  }
  await dialog.getByRole("button", { name: "Remove member", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Removing…", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.locator('[data-slot="dialog-overlay"]').click({ position: { x: 4, y: 4 } });
  await expect(dialog).toBeVisible();
  expect(deletes).toBe(1);
  release();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Team", exact: true })).toBeFocused();
  await expect(actions).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Actions for @carlos" })).toBeEnabled();
});

test("testing a connection cannot open a trapped deletion dialog", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/connection/test", async (route) => {
    await held;
    await route.fulfill({ status: 503, json: { detail: "Connection test failed." } });
  });
  await page.goto("/settings");
  const actions = page.getByRole("button", { name: "Actions for Team reviews" });
  await actions.click();
  await page.getByRole("menuitem", { name: "Test again", exact: true }).click();
  await expect(actions).toBeDisabled();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  release();
  await expect(page.getByRole("alert")).toContainText("Connection test failed");
  await expect(actions).toBeEnabled();
  await actions.click();
  await page.getByRole("menuitem", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Cancel" })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(actions).toBeFocused();
});

for (const [status, message] of [
  [401, "Your session has expired. Sign in with GitHub again."],
  [403, "You do not have access to this action."],
  [422, "Some values could not be accepted."],
  [429, "Too many requests were sent."],
  [500, "Code Mortis could not complete this request."],
  [503, "Code Mortis is temporarily unavailable."],
  [504, "The server took too long to respond."],
] as const) {
  test(`error messages explain HTTP ${status} and allow retry`, async ({ page }, info) => {
    await mock(page);
    let failed = true;
    await page.route("**/api/v1/setup/status", (route) => failed
      ? route.fulfill({ status, contentType: "text/html", body: "<h1>Upstream failure</h1>" })
      : route.fallback());
    await page.goto("/");
    await expect(page.getByRole("alert")).toContainText(message);
    await expect(page.getByRole("alert")).not.toContainText("Upstream failure");
    await noOverflow(page);
    if (status === 500) {
      await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
      await noOverflow(page);
      await page.screenshot({ path: info.outputPath("request-error.png"), fullPage: true });
      await page.evaluate(() => { document.documentElement.style.zoom = ""; });
    }
    failed = false;
    await page.getByRole("button", { name: "Retry connection", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  });
}

test("error messages explain network failures", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/setup/status", (route) => route.abort("connectionrefused"));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Could not reach Code Mortis. Check your connection and try again.");
  await noOverflow(page);
});

for (const [field, invalid, message] of [
  ["Extra headers JSON", '{"Authorization":"private-provider-key",', "Headers must contain valid JSON. Check quotes, commas, and braces, then try again."],
  ["Provider options JSON", "[]", "Options must be a JSON object."],
] as const) {
  test(`model refresh keeps form errors and the current tab with invalid ${field}`, async ({ page }) => {
    const requests = await mock(page);
    const saveError = "Provider configuration could not be saved.";
    await page.route("**/api/v1/connections/connection", (route) => route.request().method() === "PUT"
      ? route.fulfill({ status: 422, json: { detail: saveError } }) : route.fallback());
    await page.goto("/settings?tab=connections");
    await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
    await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
    await expect(dialog.getByRole("alert")).toHaveText(saveError);
    await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
    await dialog.getByLabel(field, { exact: true }).fill(invalid);
    await dialog.getByRole("tab", { name: "General", exact: true }).click();
    await dialog.getByRole("button", { name: /^(Load|Refresh) models$/ }).click();
    await expect(dialog.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("data-state", "active");
    await expect(dialog.getByRole("alert").filter({ hasText: saveError })).toBeVisible();
    await expect(dialog.getByRole("region", { name: "Default model", exact: true }).getByRole("alert")).toHaveText(message);
    expect(requests.filter((request) => request.path === "/connections/models")).toHaveLength(0);
  });

  test(`connection save reveals invalid ${field} from another tab`, async ({ page }, info) => {
    const requests = await mock(page);
    await page.goto("/settings?tab=connections");
    await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
    await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
    await dialog.getByLabel(field, { exact: true }).fill(invalid);
    for (const tab of ["General", "Connection"]) {
      await dialog.getByRole("tab", { name: tab, exact: true }).click();
      await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
      await expect(dialog.getByRole("tab", { name: "Advanced", exact: true })).toHaveAttribute("data-state", "active");
      await expect(dialog.getByRole("alert")).toHaveText(message);
      await expect(dialog.getByLabel(field, { exact: true })).toHaveValue(invalid);
    }
    expect(requests.filter((request) => request.method === "PUT")).toHaveLength(0);
    await noOverflow(page);
    await page.screenshot({ path: info.outputPath("advanced-json-validation.png") });
    await dialog.getByLabel(field, { exact: true }).fill("{}");
    await dialog.getByRole("tab", { name: "General", exact: true }).click();
    await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(requests.filter((request) => request.method === "PUT")).toHaveLength(1);
  });
}

test("error messages identify invalid JSON without exposing draft secrets", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Actions for Team reviews" }).click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
  await dialog.getByLabel("Extra headers JSON").fill('{"Authorization":"private-provider-key",');
  await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Headers must contain valid JSON. Check quotes, commas, and braces, then try again.");
  await expect(dialog.getByRole("alert")).not.toContainText("private-provider-key");
  expect(requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  await noOverflow(page);
  await dialog.getByRole("alert").scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("json-error.png"), fullPage: true });
});

test("review feedback shows recorded usage and the configured token limit", async ({ page }) => {
  await mock(page, { run: { status: "partial", config: { settings: { token_budget: 5000000 } },
    report: { summary: { budget_exceeded: true, total_tokens: 5232697 } } } });
  await page.goto("/runs/run");
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText("5,232,697 / 5,000,000 tokens used.");
});

test("connection form errors preserve edits and omit unchanged secrets", async ({ page }, info) => {
  const requests = await mock(page);
  let fail = true;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/connection", async (route) => {
    if (!fail || route.request().method() !== "PUT") return route.fallback();
    await held;
    await route.fulfill({ status: 422, json: { detail: "Provider configuration could not be saved." } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Actions for Team reviews" }).click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Connection name").fill("Updated connection");
  await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
  await expect(dialog.getByLabel("Extra headers JSON")).toHaveValue("");
  await expect(dialog.getByLabel("Replace API key (optional)")).toHaveValue("");
  await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Saving…", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(dialog.getByRole("heading", { name: "Edit AI connection" })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Edit AI connection" })).toBeVisible();
  await page.locator('[data-slot="dialog-overlay"]').click({ position: { x: 4, y: 4 } });
  await expect(dialog.getByLabel("Connection name")).toHaveValue("Updated connection");
  release();
  await expect(dialog.getByRole("alert")).toContainText("could not be saved");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Save connection", exact: true })).toBeEnabled();
  await expect(dialog.getByLabel("Connection name")).toHaveValue("Updated connection");
  await noOverflow(page);
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await dialog.getByRole("tab", { name: "General", exact: true }).click();
    await page.screenshot({ path: `../.impeccable/review/settings-api-dialog-${info.project.name}-${theme}.png` });
    await dialog.getByRole("button", { name: "Save connection", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `../.impeccable/review/settings-api-dialog-bottom-${info.project.name}-${theme}.png` });
  }
  fail = false;
  await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/connection-save-toast-${info.project.name}-dark.png` });
  const body = requests.find((request) => request.path === "/connections/connection")!.body;
  expect(body).toMatchObject({ name: "Updated connection", model: "review-model" });
  expect(body).not.toHaveProperty("api_key");
  expect(body).not.toHaveProperty("extra_headers");
});

test("API connection prompt ceiling defaults, saves, and reloads independently of provider options", async ({ page }, info) => {
  const requests = await mock(page);
  let current = { ...connection, options: { timeout_sec: 30 } as Record<string, unknown> };
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/connections/connection", (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    return route.fulfill({ json: current });
  });
  await page.goto("/settings?tab=connections");
  async function edit() {
    await page.getByRole("button", { name: "Actions for Team reviews" }).click();
    await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  }
  await edit();
  const dialog = page.getByRole("dialog");
  const ceiling = dialog.getByLabel("Evidence prompt ceiling (tokens)", { exact: true });
  await expect(ceiling).toHaveValue("58888");
  await expect(dialog.getByLabel("Provider options JSON")).toHaveValue('{\n  "timeout_sec": 30\n}');
  await ceiling.fill("200000");
  await noOverflow(page);
  await ceiling.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await page.screenshot({ path: `../.impeccable/review/api-prompt-ceiling-${info.project.name}.png` });
  await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(current.options).toEqual({ timeout_sec: 30, prompt_token_ceiling: 200000 });
  await edit();
  await expect(ceiling).toHaveValue("200000");
  await expect(dialog.getByLabel("Provider options JSON")).toHaveValue('{\n  "timeout_sec": 30\n}');
  for (const value of ["", "4095", "2000001", "4096.5"]) {
    await ceiling.fill(value);
    await expect.poll(() => ceiling.evaluate((element: HTMLInputElement) => element.checkValidity())).toBe(false);
    await dialog.getByRole("button", { name: "Refresh models", exact: true }).click();
    await expect(dialog.getByRole("alert")).toHaveText("Evidence prompt ceiling must be an integer between 4096 and 2000000.");
    expect(requests.filter((request) => request.path === "/connections/models")).toHaveLength(0);
  }
  await ceiling.fill("58888");
  await dialog.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections/models")?.body).toMatchObject({
    options: { timeout_sec: 30, prompt_token_ceiling: 58888 },
  });
  await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(current.options).toEqual({ timeout_sec: 30, prompt_token_ceiling: 58888 });
});

test("shared connection tabs preserve drafts and reveal invalid fields without moving the modal", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings?tab=connections");
  await page.getByRole("button", { name: "Actions for Team reviews", exact: true }).click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("tab")).toHaveText(["General", "Connection", "Advanced"]);
  const general = dialog.getByRole("tabpanel", { name: "General", exact: true });
  const authentication = dialog.getByRole("tabpanel", { name: "Connection", exact: true });
  const advanced = dialog.getByRole("tabpanel", { name: "Advanced", exact: true });
  const save = dialog.getByRole("button", { name: "Save connection", exact: true });
  await general.getByLabel("Connection name").fill("Shared review draft");
  await chooseOption(page, general.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await general.getByLabel("Custom model ID").fill("custom-model");
  await general.getByLabel("Evidence prompt ceiling (tokens)").fill("200000");
  await dialog.getByRole("tab", { name: "Connection", exact: true }).click();
  await expect(authentication.getByLabel("Model", { exact: true })).toHaveCount(0);
  await authentication.getByLabel("Replace API key (optional)").fill("replacement-key");
  const url = authentication.getByLabel("API base URL");
  await url.fill("invalid-url");
  await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
  await expect(advanced.getByLabel("Model", { exact: true })).toHaveCount(0);
  await advanced.getByLabel("Extra headers JSON").fill('{"X-Review":"draft"}');
  await advanced.getByLabel("Provider options JSON").fill('{"timeout_sec":45}');
  const originalBounds = await dialog.boundingBox();
  const originalTabs = await dialog.getByRole("tablist").boundingBox();
  const originalSave = await save.boundingBox();
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const tab of ["General", "Connection", "Advanced"]) {
      await dialog.getByRole("tab", { name: tab, exact: true }).click();
      await noOverflow(page);
      const bounds = await dialog.boundingBox();
      const tabs = await dialog.getByRole("tablist").boundingBox();
      const footer = await save.boundingBox();
      expect(bounds!.height).toBeCloseTo(originalBounds!.height, 1);
      expect(bounds!.y).toBeCloseTo(originalBounds!.y, 1);
      expect(tabs!.y).toBeCloseTo(originalTabs!.y, 1);
      expect(footer!.y).toBeCloseTo(originalSave!.y, 1);
      await expect(save).toBeInViewport();
      await page.screenshot({ path: `../.impeccable/review/shared-connection-${tab.toLowerCase()}-${theme}-${info.project.name}.png` });
    }
  }
  await save.click();
  await expect(dialog.getByRole("tab", { name: "Connection", exact: true })).toHaveAttribute("data-state", "active");
  await expect(url).toBeFocused();
  expect(requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  await url.fill("https://custom.example/v1");
  await dialog.getByRole("tab", { name: "General", exact: true }).click();
  await expect(general.getByLabel("Connection name")).toHaveValue("Shared review draft");
  await expect(general.getByLabel("Custom model ID")).toHaveValue("custom-model");
  await expect(general.getByLabel("Evidence prompt ceiling (tokens)")).toHaveValue("200000");
  const name = general.getByLabel("Connection name");
  await name.fill("");
  await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
  await save.click();
  await expect(dialog.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("data-state", "active");
  await expect(name).toBeFocused();
  expect(requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  await name.fill("Shared review draft");
  await page.setViewportSize({ width: page.viewportSize()!.width, height: 500 });
  await dialog.getByRole("tab", { name: "Connection", exact: true }).click();
  expect(await authentication.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await expect(save).toBeInViewport();
  await noOverflow(page);
  await save.click();
  await expect(dialog).toHaveCount(0);
  expect(requests.find((request) => request.method === "PUT" && request.path === "/connections/connection")?.body).toMatchObject({
    name: "Shared review draft", model: "custom-model", api_key: "replacement-key",
    base_url: "https://custom.example/v1", extra_headers: { "X-Review": "draft" },
    options: { timeout_sec: 45, prompt_token_ceiling: 200000 },
  });
});

test("connection editing recovers when providers fail to load", async ({ page }) => {
  await mock(page);
  let fail = true;
  await page.route("**/api/v1/providers", (route) => fail
    ? route.fulfill({ status: 503, json: { detail: "Providers could not be loaded." } }) : route.fallback());
  await page.goto("/settings");
  await expect(page.getByRole("alert")).toContainText("Providers could not be loaded");
  const actions = page.getByRole("button", { name: "Actions for Team reviews" });
  await actions.click();
  await expect(page.getByRole("menuitem", { name: "Edit", exact: true })).toBeDisabled();
  await expect(page.getByRole("menuitem", { name: "Delete", exact: true })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "Retry providers" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await actions.click();
  await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
  await expect(page.getByRole("dialog").getByLabel("Connection name")).toHaveValue("Team reviews");
});

test("installation sync errors survive opening and cancelling uninstall", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/installations/10", (route) => route.fulfill({ status: 503, json: {
    detail: route.request().method() === "DELETE" ? "Uninstall request failed." : "Repository sync failed.",
  } }));
  await page.goto("/settings?tab=github");
  const actions = page.getByRole("button", { name: "Actions for acme" });
  await actions.click();
  await page.getByRole("menuitem", { name: "Resync repositories" }).click();
  const syncError = page.getByRole("region", { name: "Connected installations", includeHidden: true }).getByRole("alert", { includeHidden: true });
  await expect(syncError).toHaveText("Repository sync failed.");
  await actions.click();
  await page.getByRole("menuitem", { name: "Uninstall", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await expect(syncError).toHaveText("Repository sync failed.");
  await dialog.getByRole("button", { name: "Uninstall GitHub App" }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Uninstall request failed.");
  await expect(syncError).toHaveText("Repository sync failed.");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(actions).toBeFocused();
  await expect(syncError).toHaveText("Repository sync failed.");
});

test("subscription editor tabs preserve drafts and save General without changing account access", async ({ page }, info) => {
  const requests = await mock(page, { role: "member", personal: true });
  const authorizedRepositories = Array.from({ length: 24 }, (_, index) => index
    ? { ...repo, id: repo.id + index, full_name: `acme/service-${index}` } : repo);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [...authorizedRepositories, { ...repo, id: 99, full_name: "acme/new-service" }] }));
  await page.route("**/api/v1/connections/personal/grants", (route) => route.fulfill({ json:
    authorizedRepositories.map((repository) => ({ repository_id: repository.id, automatic: true })),
  }));
  let personal = { ...connection, id: "personal", name: "My ChatGPT", kind: "chatgpt", owner_id: 7,
    auth_status: "connected", options: { prompt_token_ceiling: 200000, existing_option: true } };
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [personal] }));
  await page.route("**/api/v1/connections/personal", (route) => {
    personal = { ...personal, ...route.request().postDataJSON() };
    return route.fulfill({ json: personal });
  });
  await page.route("**/api/v1/connections/personal/auth", (route) => route.fulfill({ json: {
    status: "connected", account: { email: "carlos@example.com" }, limits: { remaining: 80 },
  } }));
  await page.goto("/settings");
  const actions = page.getByRole("button", { name: "Actions for My ChatGPT", exact: true });
  await actions.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveText(["Edit", "Delete"]);
  await page.getByRole("menuitem", { name: "Edit", exact: true }).press("Enter");
  const dialog = page.getByRole("dialog", { name: "Edit subscription", exact: true });
  await expect(dialog.getByRole("tab")).toHaveText(["General", "Account", "Repositories"]);
  const originalBounds = await dialog.boundingBox();
  const originalTabs = await dialog.getByRole("tablist").boundingBox();
  const originalDone = await dialog.getByRole("button", { name: "Done", exact: true }).boundingBox();
  const general = dialog.getByRole("tabpanel", { name: "General", exact: true });
  await general.getByLabel("Connection name").fill("Renamed subscription");
  await chooseOption(page, general.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await general.getByLabel("Custom model ID").fill(" custom-model ");
  await general.getByLabel("Prompt ceiling (tokens)").fill("300000");
  await dialog.getByRole("tab", { name: "Account", exact: true }).click();
  const account = dialog.getByRole("tabpanel", { name: "Account", exact: true });
  await expect(account.getByText("Signed in as carlos@example.com")).toBeVisible();
  const explanation = account.getByText("Your official client runs on this server.", { exact: false });
  await expect(explanation).toBeVisible();
  const explanationBounds = await explanation.boundingBox();
  const limitsBounds = await account.getByText("Subscription usage limits", { exact: true }).boundingBox();
  expect(explanationBounds!.y + explanationBounds!.height).toBeLessThan(limitsBounds!.y);
  const checkBounds = await account.getByRole("button", { name: "Check status", exact: true }).boundingBox();
  const revokeBounds = await account.getByRole("button", { name: "Revoke access", exact: true }).boundingBox();
  expect(revokeBounds!.y).toBeCloseTo(checkBounds!.y, 1);
  expect(revokeBounds!.x).toBeGreaterThan(checkBounds!.x + checkBounds!.width);
  await expect(account.getByLabel("Model", { exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Save changes", exact: true })).toHaveCount(0);
  await dialog.getByRole("tab", { name: "Repositories", exact: true }).click();
  const repositories = dialog.getByRole("tabpanel", { name: "Repositories", exact: true });
  await expect(repositories.getByLabel("Model", { exact: true })).toHaveCount(0);
  await expect(repositories.getByRole("button", { name: "Revoke", exact: true })).toHaveCount(24);
  expect(await repositories.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await chooseOption(page, repositories.getByLabel("Repository", { exact: true }), "acme/new-service");
  await repositories.getByRole("switch").check();
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme });
    for (const tab of ["General", "Account", "Repositories"]) {
      await dialog.getByRole("tab", { name: tab, exact: true }).click();
      await noOverflow(page);
      const bounds = await dialog.boundingBox();
      const tabs = await dialog.getByRole("tablist").boundingBox();
      const done = await dialog.getByRole("button", { name: "Done", exact: true }).boundingBox();
      expect(bounds!.height).toBeCloseTo(originalBounds!.height, 1);
      expect(bounds!.y).toBeCloseTo(originalBounds!.y, 1);
      expect(tabs!.y).toBeCloseTo(originalTabs!.y, 1);
      expect(done!.y).toBeCloseTo(originalDone!.y, 1);
      await expect(dialog.getByRole("button", { name: "Done", exact: true })).toBeInViewport();
      await page.screenshot({ path: info.outputPath(`subscription-${tab.toLowerCase()}-${theme}.png`) });
    }
  }
  await expect(repositories.getByLabel("Repository", { exact: true })).toHaveValue("acme/new-service");
  await expect(repositories.getByRole("switch")).toBeChecked();
  const generalTab = dialog.getByRole("tab", { name: "General", exact: true });
  await generalTab.click();
  await generalTab.press("ArrowRight");
  await expect(dialog.getByRole("tab", { name: "Account", exact: true })).toBeFocused();
  await generalTab.click();
  await expect(general.getByLabel("Connection name")).toHaveValue("Renamed subscription");
  await expect(general.getByLabel("Custom model ID")).toHaveValue(" custom-model ");
  await expect(general.getByLabel("Prompt ceiling (tokens)")).toHaveValue("300000");
  await general.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect.poll(() => personal).toMatchObject({ name: "Renamed subscription", model: "custom-model",
    options: { prompt_token_ceiling: 300000, existing_option: true } });
  await expect(dialog).toHaveAccessibleDescription("Renamed subscription · ChatGPT subscription");
  await expect(general.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  expect(requests.filter((request) => request.method !== "GET" && /\/auth|\/disconnect|\/grants/.test(request.path))).toHaveLength(0);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.getByRole("button", { name: "Actions for Renamed subscription", exact: true })).toBeFocused();
});

test("personal model saves compare and send trimmed model IDs", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let body: { model: string } | undefined;
  await page.route("**/api/v1/connections/personal", (route) => {
    body = route.request().postDataJSON();
    return route.fulfill({ json: { ...connection, ...body } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const form = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await chooseOption(page, form.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  const model = form.getByLabel("Custom model ID");
  await model.fill(" review-model ");
  await expect(form.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await model.fill(" custom-model ");
  await form.getByRole("button", { name: "Save changes" }).click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  expect(body?.model).toBe("custom-model");
  await expect(model).toHaveValue("custom-model");
});

for (const kind of ["chatgpt", "claude_code"] as const) {
  for (const model of ["review-model", "", " "]) {
    test(`${kind} subscription General saves name and ceiling with model ${JSON.stringify(model)}`, async ({ page }, info) => {
      await mock(page, { role: "member", personal: true, claude: kind === "claude_code" });
      let personal = { ...connection, id: "personal", name: "My subscription", kind, owner_id: 7, auth_status: "connected", model };
      await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [personal] }));
      await page.route("**/api/v1/connections/personal", (route) => {
        personal = { ...personal, ...route.request().postDataJSON() };
        return route.fulfill({ json: personal });
      });
      await page.goto("/settings");
      await page.getByRole("button", { name: "Actions for My subscription", exact: true }).click();
      await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog.getByLabel("Evidence prompt ceiling (tokens)")).toHaveCount(0);
      const ceiling = dialog.getByLabel("Prompt ceiling (tokens)", { exact: true });
      await expect(ceiling).toHaveValue("58888");
      for (const value of ["4095", "2000001", "4096.5"]) {
        await ceiling.fill(value);
        await expect.poll(() => ceiling.evaluate((element: HTMLInputElement) => element.checkValidity())).toBe(false);
      }
      await ceiling.fill("200000");
      await noOverflow(page);
      if (model.trim()) await page.screenshot({ path: info.outputPath("subscription-prompt-ceiling.png"), fullPage: true });
      await dialog.getByLabel("Connection name").fill("Updated subscription");
      await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
      await expect.poll(() => personal).toMatchObject({ name: "Updated subscription", kind, model: model.trim(),
        options: { prompt_token_ceiling: 200000 } });
      await expect(dialog.getByRole("heading", { name: "Edit subscription", exact: true })).toBeVisible();
      await expect(dialog.getByRole("tab", { name: "General", exact: true })).toHaveAttribute("aria-selected", "true");
      await dialog.getByRole("button", { name: "Done", exact: true }).click();
      await expect(dialog).toHaveCount(0);
      await expect(page.getByRole("article", { name: "Updated subscription", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "Actions for Updated subscription", exact: true })).toBeFocused();
      await page.getByRole("button", { name: "Actions for Updated subscription", exact: true }).click();
      await page.getByRole("menuitem", { name: "Edit", exact: true }).click();
      await expect(dialog.getByLabel("Prompt ceiling (tokens)", { exact: true })).toHaveValue("200000");
    });
  }
}

test("pending personal connection creation keeps its dialog until setup opens", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await held;
    await route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", model: "", owner_id: 7 } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await chooseOption(page, dialog.getByLabel("Connection type"), "Personal ChatGPT subscription");
  await dialog.getByLabel("Connection name").fill("Personal setup");
  const dialogId = await dialog.getAttribute("id");
  await dialog.getByRole("button", { name: "Continue to sign in", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Saving…", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.locator('[data-slot="dialog-overlay"]').click({ position: { x: 4, y: 4 } });
  await expect(dialog.getByRole("heading", { name: "Add AI connection" })).toBeVisible();
  await expect(dialog.getByLabel("Connection name")).toHaveValue("Personal setup");
  release();
  await expect(dialog.getByRole("heading", { name: "Edit subscription", exact: true })).toBeVisible();
  await expect(dialog).toHaveCount(1);
  await expect(dialog).toHaveAttribute("id", dialogId!);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add connection", exact: true })).toBeFocused();
});

test("personal model and repository errors stay with their operation", async ({ page }) => {
  const requests = await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] }));
  let fail = true;
  await page.route("**/api/v1/connections/personal", (route) => route.fulfill({ status: 422, json: { detail: "Model could not be saved." } }));
  await page.route("**/api/v1/connections/personal/grants/22", (route) => fail
    ? route.fulfill({ status: 403, json: { detail: "Repository authorization denied." } }) : route.fallback());
  await page.goto("/settings?tab=team");
  const sections = page.getByRole("navigation", { name: "Settings sections" });
  await expect(sections.getByRole("link")).toHaveText(["AI connections", "Appearance"]);
  await expect(sections.getByRole("link", { name: "AI connections", exact: true })).toHaveAttribute("aria-current", "page");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const model = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await chooseOption(page, model.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await model.getByLabel("Custom model ID").fill("custom-model");
  await model.getByRole("button", { name: "Save changes" }).click();
  await expect(model.getByRole("alert", { includeHidden: true })).toContainText("Model could not be saved");
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  await grants.getByRole("button", { name: "Save authorization" }).click();
  await expect(grants.getByRole("alert")).toContainText("Repository authorization denied");
  await expect(grants.getByLabel("Repository", { exact: true })).toHaveValue("acme/payments");
  await expect(page.getByRole("region", { name: "Account connection" }).getByRole("alert")).toHaveCount(0);
  await expect(model.getByLabel("Custom model ID")).toHaveValue("custom-model");
  await grants.getByRole("switch").check();
  await expect(grants.getByRole("alert")).toHaveCount(0);
  await expect(model.getByRole("alert", { includeHidden: true })).toContainText("Model could not be saved");
  await grants.getByRole("button", { name: "Save authorization" }).click();
  await expect(grants.getByRole("alert")).toContainText("Repository authorization denied");
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/orders");
  await expect(grants.getByRole("alert")).toHaveCount(0);
  await expect(grants.getByRole("switch")).toBeChecked();
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  await grants.getByRole("switch").uncheck();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await model.getByLabel("Custom model ID").fill("revised-model");
  await expect(model.getByRole("alert", { includeHidden: true })).toHaveCount(0);
  await model.getByRole("button", { name: "Save changes" }).click();
  await expect(model.getByRole("alert", { includeHidden: true })).toContainText("Model could not be saved");
  await expect(model.getByLabel("Custom model ID")).toHaveValue("revised-model");
  fail = false;
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await grants.getByRole("button", { name: "Save authorization" }).click();
  expect(requests.filter((request) => request.path === "/connections/personal/grants/22").at(-1)?.body).toEqual({ automatic: false });
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository authorization updated." })).toBeVisible();
  await grants.getByRole("switch").check();
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  await grants.getByRole("button", { name: "Save authorization" }).click();
  await expect.poll(() => requests.filter((request) => request.path === "/connections/personal/grants/22").at(-1)?.body).toEqual({ automatic: true });
});

test("personal model saves use toasts while edits retain saved state", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let saves = 0;
  await page.route("**/api/v1/connections/personal", (route) => {
    saves++;
    return route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", kind: "chatgpt" } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const model = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await chooseOption(page, model.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  const input = model.getByLabel("Custom model ID");
  await input.fill("saved-custom");
  await model.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  await expect(model.getByText("Connection saved.", { exact: true })).toHaveCount(0);
  await input.fill("edited-custom");
  await input.fill("saved-custom");
  await expect(model.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  expect(saves).toBe(1);
  await input.fill("next-custom");
  await model.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." }).last()).toBeVisible();
  expect(saves).toBe(2);
});

test("personal model saves report invalid success responses and retain an editable draft", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  const invalid = [{ status: 204 }, { body: "{", contentType: "application/json" }, { json: {} }, { json: { model: 42 } }, { json: { model: " " } }];
  let saves = 0;
  await page.route("**/api/v1/connections/personal", (route) => {
    const response = invalid[saves++];
    return route.fulfill(response ?? { json: { ...connection, ...route.request().postDataJSON(), id: "personal" } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const form = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await chooseOption(page, form.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  const input = form.getByLabel("Custom model ID");
  for (let attempt = 0; attempt < invalid.length; attempt++) {
    await input.fill(`custom-${attempt}`);
    await expect(form.getByRole("alert")).toHaveCount(0);
    await form.getByRole("button", { name: "Save changes", exact: true }).click();
    await expect(form.getByRole("alert")).toHaveText("The server did not return a valid model. Please retry.");
    await expect(input).toHaveValue(`custom-${attempt}`);
    await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toHaveCount(0);
    await expect(form.getByRole("button", { name: "Save changes", exact: true })).toBeEnabled();
  }
  await form.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(form.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  await expect(input).toHaveValue(`custom-${invalid.length - 1}`);
  await expect(form.getByRole("button", { name: "Save changes", exact: true })).toBeDisabled();
  expect(saves).toBe(invalid.length + 1);
});

test("personal account status distinguishes loading, errors, and the signed-in account", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let release!: () => void;
  let held = new Promise<void>((resolve) => { release = resolve; });
  let status = "error";
  await page.route("**/api/v1/connections/personal/auth", async (route) => {
    await held;
    await route.fulfill(status === "error"
      ? { status: 503, json: { detail: "Account status could not be loaded." } }
      : { json: { status, account: status === "connected" ? { email: "carlos@example.com" } : null } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
  await expect(account.getByRole("status")).toHaveText("Checking account…");
  await expect(account.getByRole("status").locator("svg")).toBeVisible();
  release();
  await expect(account.getByRole("status")).toHaveText("Could not check account");
  await expect(account.getByRole("alert")).toContainText("Account status could not be loaded");
  for (const next of ["unavailable", "connected", "error", "disconnected"]) {
    status = next;
    held = new Promise<void>((resolve) => { release = resolve; });
    await account.getByRole("button", { name: "Check status" }).click();
    await expect(account.getByRole("status")).toHaveText("Checking account…");
    await expect(account.getByRole("status").locator("svg")).toBeVisible();
    release();
    const labels: Record<string, string> = {
      disconnected: "Not connected", unavailable: "Runner unavailable", connected: "Connected", error: "Last known: Connected",
    };
    await expect(account.getByRole("status")).toHaveText(labels[next]);
    if (next === "unavailable" || next === "error") {
      await expect(account.locator('[data-slot="badge"]')).toHaveClass(/text-amber-800/);
      await expect(account.locator('[data-slot="badge"]')).not.toHaveClass(/text-emerald-800/);
    }
    if (next === "connected") await expect(account.getByText("Signed in as carlos@example.com")).toBeVisible();
    if (next === "error") await expect(account.getByRole("alert")).toContainText("Account status could not be loaded");
    if (next === "disconnected") await expect(account.getByRole("button", { name: "Check status", exact: true })).toHaveCount(0);
  }
});

test("personal account polling stays quiet while manual checks show progress and reuse the fetch", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let reads = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/personal/auth", async (route) => {
    if (++reads === 1) return route.fulfill({ json: { status: "connected" } });
    await held;
    await route.fulfill({ status: 503, json: { detail: "Account refresh failed." } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
  const check = account.getByRole("button", { name: "Check status", exact: true });
  await expect(account.getByRole("status")).toHaveText("Connected");
  await expect.poll(() => reads, { timeout: 8000 }).toBe(2);
  await expect(account.getByRole("status")).toHaveText("Connected");
  await expect(check).toBeEnabled();
  await check.click();
  await expect(account.getByRole("status")).toHaveText("Checking account…");
  await expect(check).toBeDisabled();
  release();
  await expect(account.getByRole("status")).toHaveText("Last known: Connected");
  await expect(account.getByRole("alert")).toContainText("Account refresh failed");
  await expect(account.locator('[data-slot="badge"]')).toHaveClass(/text-amber-800/);
  await expect(check).toBeEnabled();
  expect(reads).toBe(2);
});

test("personal authorizations tolerate an empty response body", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/connections/personal/grants", (route) => route.fulfill({ status: 204 }));
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await expect(page.getByRole("region", { name: "Repository authorization" }).getByText("No repositories authorized yet.")).toBeVisible();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await expect(page.getByRole("form", { name: "Subscription settings" })).toBeVisible();
});

test("personal repository choices distinguish loading, empty results, and fetch errors", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let failed = false;
  await page.route("**/api/v1/repositories", async (route) => {
    await held;
    await route.fulfill(failed ? { status: 503, json: { detail: "Repositories could not be loaded." } } : { json: [] });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  await expect(grants.getByText("Loading repositories…")).toBeVisible();
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  await expect(grants.getByText("No accessible repositories.")).toHaveCount(0);
  release();
  await expect(grants.getByText("No accessible repositories.")).toBeVisible();
  await expect(grants.getByText("Loading repositories…")).toHaveCount(0);
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  failed = true;
  await page.reload();
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await expect(grants.getByRole("alert")).toContainText("Repositories could not be loaded");
  await expect(grants.getByText("No accessible repositories.")).toHaveCount(0);
  await expect(grants.getByText("Loading repositories…")).toHaveCount(0);
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
});

test("personal repository and authorization fetches retry independently without losing drafts", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  let retry = false;
  let releaseRepositories!: () => void;
  let releaseGrants!: () => void;
  const repositories = new Promise<void>((resolve) => { releaseRepositories = resolve; });
  const authorizations = new Promise<void>((resolve) => { releaseGrants = resolve; });
  await page.route("**/api/v1/repositories", async (route) => {
    if (!retry) return route.fulfill({ status: 503, json: { detail: "Repositories unavailable." } });
    await repositories;
    await route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] });
  });
  await page.route("**/api/v1/connections/personal/grants", async (route) => {
    if (!retry) return route.fulfill({ status: 503, json: { detail: "Authorizations unavailable." } });
    await authorizations;
    await route.fulfill({ json: [{ repository_id: 22, automatic: false }] });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const model = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await chooseOption(page, model.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await model.getByLabel("Custom model ID").fill("unsaved-model");
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await grants.getByRole("switch").check();
  await expect(grants.getByRole("alert")).toHaveText(["Authorizations unavailable.", "Repositories unavailable."]);
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  retry = true;
  await grants.getByRole("button", { name: "Retry repositories", exact: true }).click();
  await expect(grants.getByText("Loading repositories…")).toBeVisible();
  await expect(grants.getByRole("button", { name: "Retry authorizations", exact: true })).toBeEnabled();
  releaseRepositories();
  await expect(grants.getByText("Loading repositories…")).toHaveCount(0);
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  await expect(grants.getByRole("button", { name: "Save authorization", exact: true })).toBeDisabled();
  await expect(grants.getByRole("alert")).toHaveText("Authorizations unavailable.");
  await grants.getByRole("button", { name: "Retry authorizations", exact: true }).click();
  await expect(grants.getByText("Loading authorizations…")).toBeVisible();
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  releaseGrants();
  await expect(grants.getByText("Manual use only")).toBeVisible();
  await expect(grants.getByLabel("Repository", { exact: true })).toBeEnabled();
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/orders");
  await expect(grants.getByRole("alert")).toHaveCount(0);
  await expect(grants.getByRole("button", { name: /^Retry / })).toHaveCount(0);
  await expect(grants.getByLabel("Repository", { exact: true })).toHaveValue("acme/orders");
  await expect(grants.getByRole("switch")).toBeChecked();
  await expect(model.getByLabel("Custom model ID")).toHaveValue("unsaved-model");
});

test("personal model saves disable controls while retaining the loaded catalog", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/connections/personal/auth", (route) => route.fulfill({ json: { status: "connected" } }));
  let reads = 0;
  let releaseRefresh!: () => void;
  const refresh = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  await page.route("**/api/v1/connections/personal/models", async (route) => {
    if (++reads > 1) await refresh;
    await route.fulfill({ json: { source: "client", status: "ready", message: "Official client catalog.", models: [
      { id: "review-model", name: "Review model" }, { id: "next-model", name: "Next model" },
    ] } });
  });
  let releaseSave!: () => void;
  const save = new Promise<void>((resolve) => { releaseSave = resolve; });
  await page.route("**/api/v1/connections/personal", async (route) => {
    await save;
    await route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", kind: "chatgpt" } });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const form = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  const select = form.getByLabel("Model", { exact: true });
  await chooseOption(page, select, "next-model");
  await form.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(select).toBeDisabled();
  await expect(form.getByRole("button", { name: "Refresh models" })).toBeDisabled();
  await expect(select).toHaveValue("Next model · next-model");
  await expect(form.getByText("Official client catalog.")).toBeVisible();
  expect(reads).toBe(1);
  releaseSave();
  await expect.poll(() => reads).toBe(2);
  await expect(select).toHaveValue("Next model · next-model");
  releaseRefresh();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  await expect(form.getByText("Official client catalog.")).toBeVisible();
  await expect(select).toBeEnabled();
  expect(reads).toBe(2);
});

test("personal authorizations distinguish loading, fetch failure, mutation failure, and empty results", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] }));
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let failLoad = true;
  let failSave = true;
  await page.route("**/api/v1/connections/personal/grants", async (route) => {
    await held;
    await route.fulfill(failLoad
      ? { status: 503, json: { detail: "Authorizations could not be loaded." } }
      : { json: [] });
  });
  await page.route("**/api/v1/connections/personal/grants/22", (route) => failSave
    ? route.fulfill({ status: 403, json: { detail: "Repository authorization denied." } }) : route.fallback());
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  await expect(grants.getByRole("status")).toHaveText("Loading authorizations…");
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  await expect(grants.getByRole("button", { name: "Save authorization", exact: true })).toBeDisabled();
  await expect(grants.getByText("No repositories authorized yet.")).toHaveCount(0);
  release();
  await expect(grants.getByRole("alert")).toContainText("Authorizations could not be loaded");
  await expect(grants.getByText("Loading authorizations…")).toHaveCount(0);
  await expect(grants.getByText("No repositories authorized yet.")).toHaveCount(0);
  await expect(grants.getByLabel("Repository", { exact: true })).toBeDisabled();
  failLoad = false;
  await grants.getByRole("button", { name: "Retry authorizations", exact: true }).click();
  await expect(grants.getByText("No repositories authorized yet.")).toBeVisible();
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  await grants.getByRole("button", { name: "Save authorization" }).click();
  await expect(grants.getByRole("alert")).toHaveText("Repository authorization denied.");
  await expect(grants.getByLabel("Repository", { exact: true })).toHaveValue("acme/payments");
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/orders");
  await expect(grants.getByRole("alert")).toHaveCount(0);
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  failSave = false;
  await grants.getByRole("button", { name: "Save authorization" }).click();
  await expect(grants.getByText("No repositories authorized yet.")).toBeVisible();
  await expect(grants.getByRole("alert")).toHaveCount(0);
});

test("subscription repository choices exclude authorizations and return after revocation", async ({ page }, info) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] }));
  let saved = [{ repository_id: 22, automatic: false }];
  let firstRead = true;
  let release!: () => void;
  const initial = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/personal/grants", async (route) => {
    if (firstRead) { firstRead = false; await initial; }
    await route.fulfill({ json: saved });
  });
  await page.route("**/api/v1/connections/personal/grants/*", (route) => {
    const id = Number(route.request().url().split("/").at(-1));
    saved = saved.filter((grant) => grant.repository_id !== id);
    if (route.request().method() === "PUT") saved.push({ repository_id: id, automatic: route.request().postDataJSON().automatic });
    return route.fulfill({ status: 204 });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization" });
  const repository = grants.getByRole("combobox", { name: "Repository", exact: true });
  const automatic = grants.getByRole("switch");
  await expect(grants.getByText("Loading authorizations…")).toBeVisible();
  await expect(repository).toBeDisabled();
  await expect(grants.getByRole("button", { name: "Save authorization", exact: true })).toBeDisabled();
  release();
  await expect(grants.getByText("Manual use only", { exact: true })).toBeVisible();
  await expect(repository).toHaveValue("");
  await expect(automatic).not.toBeChecked();
  await repository.click();
  await expect(page.getByRole("option", { name: "acme/payments", exact: true })).toHaveCount(0);
  await expect(page.getByRole("option", { name: "acme/orders", exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/subscription-available-repositories-${info.project.name}.png` });
  await page.getByRole("option", { name: "acme/orders", exact: true }).click();
  await automatic.check();
  await grants.getByRole("button", { name: "Save authorization", exact: true }).click();
  await expect(grants.getByText("Automatic reviews authorized", { exact: true })).toBeVisible();
  await expect(repository).toBeDisabled();
  await expect(grants.getByText("All accessible repositories are authorized.")).toBeVisible();
  await grants.getByRole("button", { name: "Revoke", exact: true }).first().click();
  await expect(repository).toBeEnabled();
  await repository.click();
  await expect(page.getByRole("option", { name: "acme/payments", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "acme/orders", exact: true })).toHaveCount(0);
});

test("personal authorization confirmations match saved changes and revocations", async ({ page }, info) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] }));
  let saved: { repository_id: number; automatic: boolean }[] = [];
  let failRevoke = true;
  let releaseRevoke!: () => void;
  const revoke = new Promise<void>((resolve) => { releaseRevoke = resolve; });
  await page.route("**/api/v1/connections/personal/grants", (route) => route.fulfill({ json: saved }));
  await page.route("**/api/v1/connections/personal/grants/22", async (route) => {
    if (route.request().method() === "DELETE") {
      await revoke;
      if (failRevoke) return route.fulfill({ status: 403, json: { detail: "Authorization could not be revoked." } });
      saved = [];
      return route.fulfill({ status: 204 });
    }
    saved = [{ repository_id: 22, automatic: route.request().postDataJSON().automatic }];
    return route.fulfill({ json: saved[0] });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  const repository = grants.getByRole("combobox", { name: "Repository", exact: true });
  const automatic = grants.getByRole("switch");
  const save = grants.getByRole("button", { name: "Save authorization" });
  await expect(repository).toHaveAttribute("placeholder", "Choose a repository");
  await expect(save).toBeDisabled();
  await repository.click();
  await expect(page.getByRole("option", { name: "acme/payments", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "Choose a repository", exact: true })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/subscription-repository-combobox-${info.project.name}.png` });
  await repository.fill("payments");
  await expect(page.getByRole("option", { name: "acme/payments", exact: true })).toBeVisible();
  await repository.press("Enter");
  await automatic.check();
  await save.click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository authorization updated." })).toBeVisible();
  await expect(grants.getByRole("status")).toHaveCount(0);
  await expect(grants.getByText("Automatic reviews authorized", { exact: true })).toBeVisible();
  await expect(repository).toHaveValue("");
  await expect(automatic).not.toBeChecked();
  await expect(save).toBeDisabled();
  await expectOption(page, repository, "acme/payments", 0);
  await expectOption(page, repository, "acme/orders");
  await grants.getByRole("button", { name: "Revoke" }).click();
  await expect(grants.getByRole("button", { name: "Revoking…", exact: true })).toBeDisabled();
  await expect(save).toBeDisabled();
  await expect(grants.getByRole("button", { name: "Saving…", exact: true })).toHaveCount(0);
  releaseRevoke();
  await expect(grants.getByRole("alert")).toContainText("Authorization could not be revoked");
  await expect(grants.locator("form").getByRole("alert")).toHaveCount(0);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Authorization revoked." })).toHaveCount(0);
  await expect(grants.getByRole("status")).toHaveCount(0);
  await expect(grants.getByText("Automatic reviews authorized", { exact: true })).toBeVisible();
  await chooseOption(page, repository, "acme/orders");
  await automatic.check();
  await expect(grants.getByRole("alert")).toContainText("Authorization could not be revoked");
  failRevoke = false;
  await grants.getByRole("button", { name: "Revoke" }).click();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Authorization revoked." })).toBeVisible();
  await expect(grants.locator("form").getByRole("status")).toHaveCount(0);
  await expect(grants.getByText("No repositories authorized yet.")).toBeVisible();
  await expectOption(page, repository, "acme/payments");
  await automatic.uncheck();
  await expect(repository).toHaveValue("acme/orders");
  await expect(grants.getByRole("status")).toHaveCount(0);
  await expect(save).toBeEnabled();
});

for (const operation of ["auth", "model", "grant", "revoke"] as const) {
  test(`personal connection scopes mutation guards during ${operation}`, async ({ page }) => {
    await mock(page, { role: "member", personal: true });
    await page.route("**/api/v1/connections/personal/auth", (route) => route.fulfill({ json: {
      status: "connected", attempt: { status: "pending", verificationUrl: "https://auth.openai.com/codex/device", userCode: "ABCD-1234" },
    } }));
    await page.route("**/api/v1/connections/personal/grants", (route) => route.fulfill({ json: [{ repository_id: 22, automatic: false }] }));
    await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "acme/orders" }] }));
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const paths = { auth: "/auth", model: "", grant: "/grants/23", revoke: "/grants/22" };
    await page.route(`**/api/v1/connections/personal${paths[operation]}`, async (route) => {
      if (route.request().method() === "GET") return route.fallback();
      await held;
      await route.fulfill({ status: 422, json: { detail: "Operation could not be completed." } });
    });
    await page.goto("/settings");
    await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
    const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
    await page.getByRole("tab", { name: "General", exact: true }).click();
    const model = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
    await page.getByRole("tab", { name: "Repositories", exact: true }).click();
    const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
    await page.getByRole("tab", { name: "General", exact: true }).click();
    await chooseOption(page, model.getByLabel("Model", { exact: true }), "Enter a custom model ID");
    await model.getByLabel("Custom model ID").fill("custom-model");
    await page.getByRole("tab", { name: "Repositories", exact: true }).click();
    await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/orders");
    const triggers = {
      auth: account.getByRole("button", { name: "Reconnect", exact: true, includeHidden: true }),
      model: model.getByRole("button", { name: "Save changes", includeHidden: true }),
      grant: grants.getByRole("button", { name: "Save authorization", includeHidden: true }),
      revoke: grants.getByRole("button", { name: "Revoke", exact: true, includeHidden: true }),
    };
    await page.getByRole("tab", { name: operation === "auth" ? "Account" : operation === "model" ? "General" : "Repositories", exact: true }).click();
    await triggers[operation].click();
    await expect(account.getByRole("button", { name: "Reconnect", exact: true, includeHidden: true })).toBeDisabled();
    await expect(account.getByRole("button", { name: "Revoke access", includeHidden: true })).toBeDisabled();
    await expect(account.getByRole("button", { name: "Cancel authorization", includeHidden: true })).toBeDisabled();
    const modelBlocked = operation === "auth" || operation === "model";
    await expect(model.getByRole("button", { name: /Save changes|Saving/, includeHidden: true })).toBeEnabled({ enabled: !modelBlocked });
    await expect(model.getByLabel("Custom model ID")).toBeEnabled({ enabled: !modelBlocked });
    const grantsBlocked = operation !== "model";
    await expect(grants.getByRole("button", { name: /Save authorization|Saving/, includeHidden: true })).toBeEnabled({ enabled: !grantsBlocked });
    await expect(grants.getByRole("button", { name: /^Revoke$|^Revoking…$/, includeHidden: true })).toBeEnabled({ enabled: !grantsBlocked });
    await expect(grants.getByLabel("Repository", { exact: true })).toBeEnabled({ enabled: !grantsBlocked });
    await expect(grants.getByRole("switch", { includeHidden: true })).toBeEnabled({ enabled: !grantsBlocked });
    const checkStatus = account.getByRole("button", { name: "Check status", exact: true, includeHidden: true });
    if (operation === "auth") await expect(checkStatus).toBeDisabled();
    else {
      await expect(checkStatus).toBeEnabled();
      await page.getByRole("tab", { name: "Account", exact: true }).click();
      await checkStatus.click();
      await expect(account.getByRole("status", { includeHidden: true })).toHaveText("Connected");
      await expect(grants.getByRole("button", { name: /^Revoke$|^Revoking…$/, includeHidden: true })).toBeEnabled({ enabled: !grantsBlocked });
    }
    release();
    await page.getByRole("tab", { name: operation === "auth" ? "Account" : operation === "model" ? "General" : "Repositories", exact: true }).click();
    await expect(page.getByRole("dialog").getByRole("alert").filter({ hasText: "Operation could not be completed" })).toBeVisible();
    await expect(triggers.auth).toBeEnabled();
    await expect(triggers.model).toBeEnabled();
    await expect(triggers.grant).toBeEnabled();
    await expect(triggers.revoke).toBeEnabled();
    await expect(model.getByLabel("Custom model ID")).toHaveValue("custom-model");
    await expect(grants.getByLabel("Repository", { exact: true })).toHaveValue("acme/orders");
  });
}

test("personal model and authorization saves run independently while auth stays guarded", async ({ page }) => {
  await mock(page, { role: "member", personal: true });
  await page.route("**/api/v1/connections/personal/auth", (route) => route.fulfill({ json: { status: "connected" } }));
  let saved: { repository_id: number; automatic: boolean }[] = [];
  await page.route("**/api/v1/connections/personal/grants", (route) => route.fulfill({ json: saved }));
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const started: string[] = [];
  await page.route("**/api/v1/connections/personal", async (route) => {
    started.push("model");
    await held;
    await route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", kind: "chatgpt" } });
  });
  await page.route("**/api/v1/connections/personal/grants/22", async (route) => {
    started.push("grant");
    await held;
    saved = [{ repository_id: 22, automatic: route.request().postDataJSON().automatic }];
    await route.fulfill({ json: saved[0] });
  });
  await page.goto("/settings");
  await page.getByRole("region", { name: "Your subscriptions" }).getByRole("button", { name: "Complete sign in", exact: true }).click();
  const account = page.getByRole("region", { name: "Account connection", includeHidden: true });
  await page.getByRole("tab", { name: "General", exact: true }).click();
  const model = page.getByRole("form", { name: "Subscription settings", includeHidden: true });
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  const grants = page.getByRole("region", { name: "Repository authorization", includeHidden: true });
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await chooseOption(page, model.getByLabel("Model", { exact: true }), "Enter a custom model ID");
  await model.getByLabel("Custom model ID").fill("saved-custom");
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await chooseOption(page, grants.getByLabel("Repository", { exact: true }), "acme/payments");
  await grants.getByRole("switch").check();
  await page.getByRole("tab", { name: "General", exact: true }).click();
  await model.getByRole("button", { name: "Save changes", exact: true, includeHidden: true }).click();
  await page.getByRole("tab", { name: "Repositories", exact: true }).click();
  await grants.getByRole("button", { name: "Save authorization", includeHidden: true }).click();
  await expect.poll(() => started).toEqual(["model", "grant"]);
  await expect(account.getByRole("button", { name: "Reconnect", exact: true, includeHidden: true })).toBeDisabled();
  await expect(account.getByRole("button", { name: "Revoke access", includeHidden: true })).toBeDisabled();
  release();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Connection saved." })).toBeVisible();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository authorization updated." })).toBeVisible();
  await expect(model.getByLabel("Custom model ID")).toHaveValue("saved-custom");
  await expect(grants.getByText("Automatic reviews authorized", { exact: true })).toBeVisible();
  await expect(account.getByRole("button", { name: "Reconnect", exact: true, includeHidden: true })).toBeEnabled();
});

test("pull detail searches evidence and separates severity, status, and run history", async ({ page }, info) => {
  await mock(page);
  const latest = { ...run, status: "completed", publication: "published", head_sha: "c".repeat(40) };
  await mockPullDetail(page, () => ({
    pull_request: { ...pr, draft: true }, repository: repo, can_write: true,
    runs: [latest, { ...run, id: "older" }],
    findings: [
      { ...finding, sync_intent: null },
      { ...finding, id: "resolved", status: "resolved", severity: "medium", content: "Validate the response before acknowledging a webhook.", path: "src/webhooks/validate.ts", last_seen_run_id: "older" },
      { ...finding, id: "dismissed", status: "dismissed", severity: "low", category: "performance", content: "Batch dispatches to reduce unnecessary database queries.", path: "src/webhooks/dispatch.ts", desired_status: "open", sync_error: "GitHub unavailable" },
    ],
  }));
  await page.goto("/pulls/pr");
  const summary = page.getByRole("region", { name: "Latest review" });
  await expect(summary).toContainText("Earlier commit");
  await expect(summary).toContainText("ccccccc");
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();
  const list = page.getByRole("region", { name: "Findings", exact: true });
  await expect(list.getByRole("region")).toHaveCount(2);
  await expect(list.getByRole("button")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: `#${pr.number} ${pr.title}`, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit Status: Open, Resolved", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await expect(page.getByRole("tabpanel").getByRole("status")).toHaveCount(0);
  await expect(list.getByRole("region", { name: "Dismissed findings", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Remove Status filter", exact: true }).click();
  await expect(list.getByRole("region")).toHaveCount(3);
  await expect(list.getByRole("button")).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await noOverflow(page);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `../.impeccable/review/finding-groups-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
  }
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Status", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Open/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByRole("checkbox", { name: /^Resolved/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Resolved/ })).toBeChecked();
  await expect(list.getByRole("button")).toHaveCount(2);
  await page.getByRole("checkbox", { name: /^Dismissed/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Dismissed/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Edit Status: Open, Resolved, Dismissed", exact: true })).toBeFocused();
  await expect(list.getByRole("button")).toHaveCount(3);
  await page.getByRole("button", { name: "Remove Status filter", exact: true }).click();
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
  await expect(list.getByRole("button", { name: /Validate the response/ })).toContainText("resolved");
  await expect(list.getByRole("button", { name: /Batch dispatches/ })).toContainText("dismissed");
  await expect(list.getByRole("button", { name: /Batch dispatches/ })).toContainText("Sync needs attention");
  await page.getByLabel("Search findings", { exact: true }).fill("VALIDATE.TS");
  await expect(list.getByRole("button")).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "High", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "High", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "No findings match these filters" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Performance/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Performance/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "View all findings", exact: true }).click();
  await expect(page.getByLabel("Search findings", { exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: /Remove (Status|Severity|Type) filter/ })).toHaveCount(0);
  await page.getByLabel("Search findings", { exact: true }).fill("performance");
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page.getByLabel("Search findings", { exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Status", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Open/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  await page.getByRole("checkbox", { name: /^Resolved/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Resolved/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "High", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "High", exact: true })).toBeChecked();
  await page.getByRole("checkbox", { name: "Medium", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Medium", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(list.getByRole("button")).toHaveCount(2);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await page.getByRole("tab", { name: /Findings/ }).click();
    await noOverflow(page);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `../.impeccable/review/pull-detail-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: "Edit Status: Open, Resolved", exact: true }).click();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/pull-filters-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "Run history" }).click();
    await expect(page.getByRole("link", { name: /automatic.*ccccccc/ })).toContainText("GitHub: Published");
    await expect(page.getByRole("tabpanel").getByRole("status")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Load more run history" })).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/pull-history-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
  }
  await page.getByRole("tab", { name: /Findings/ }).click();
  await list.getByRole("button", { name: /retry loop has no upper bound/ }).press("Enter");
  await expect(page.getByRole("dialog", { name: "Finding details" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(list.getByRole("button", { name: /retry loop has no upper bound/ })).toBeFocused();
});

test("pull detail distinguishes resolved history from unreviewed and incomplete results", async ({ page }, info) => {
  await mock(page);
  let findings = [{ ...finding, status: "resolved" }];
  let runs: typeof run[] = [run];
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings }));
  await page.goto("/pulls/pr");
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toContainText("resolved");
  await page.getByRole("button", { name: "Edit Status: Open, Resolved", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Resolved/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Resolved/ })).not.toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "No open findings", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "View all findings", exact: true }).click();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toContainText("resolved");
  await expect(page.getByRole("region", { name: "Open findings", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Status", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Open/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "No open findings", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText("Coverage is incomplete");
  await page.clock.install();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByLabel("Search findings", { exact: true }).fill("pending search with no matches");
  await page.getByRole("button", { name: "View all findings", exact: true }).click();
  await expect(page.getByLabel("Search findings", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Search findings", { exact: true })).toBeFocused();
  await page.clock.fastForward(500);
  await page.clock.resume();
  await expect(page.getByRole("region", { name: "Findings", exact: true })).toContainText("resolved");
  findings = [];
  await page.reload();
  await expect(page.getByRole("heading", { name: "No findings recorded", exact: true })).toBeVisible();
  await expect(page.getByText("Check run history for completion and coverage. An incomplete review is not a clean result.")).toBeVisible();
  runs = [];
  await page.reload();
  await expect(page.getByRole("region", { name: "Latest review" })).toContainText("No reviews yet");
  await expect(page.getByRole("heading", { name: "No review findings yet", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run review", exact: true })).toBeEnabled();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/pull-empty-${info.project.name}.png`, fullPage: true });
});

test("run history shows recorded durations and omits incomplete timing", async ({ page }, info) => {
  await mock(page);
  const runs = [
    { ...run, id: "run", status: "completed" },
    { ...run, id: "short", status: "completed", finished_at: "2026-10-05T13:20:42Z" },
    { ...run, id: "zero", status: "completed", finished_at: at },
    { ...run, id: "failed", status: "failed", finished_at: "2026-10-05T13:37:36Z" },
    { ...run, id: "queued", status: "queued", started_at: null, finished_at: null },
    { ...run, id: "running", status: "running", finished_at: null },
    { ...run, id: "unknown", status: "cancelled", started_at: null },
  ];
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [] }));
  await page.goto("/pulls/pr");
  await page.getByRole("tab", { name: /Run history/ }).click();
  const history = page.getByRole("tabpanel");
  for (const [id, duration] of [["run", "1m 30s"], ["short", "42s"], ["zero", "0s"], ["failed", "17m 36s"]]) {
    await expect(history.locator(`a[href="/runs/${id}"]`)).toContainText(`Duration: ${duration}`);
  }
  for (const id of ["queued", "running", "unknown"]) {
    await expect(history.locator(`a[href="/runs/${id}"]`)).not.toContainText("Duration:");
  }
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/run-history-duration-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
  }
  await history.locator('a[href="/runs/run"]').click();
  await expect(page.locator("main header")).toContainText("1m 30s");
});

test("run history fetches ten runs per page and keeps summary independent of loaded pages", async ({ page }, info) => {
  await mock(page);
  await page.clock.install();
  const runs = Array.from({ length: 23 }, (_, index) => ({
    ...run, id: `history-${index}`, status: index === 22 ? "queued" : index === 20 ? "completed" : "cancelled",
    publication: "published", publication_error: null, check_enabled: false,
  }));
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [{ ...finding, sync_intent: null }] }));
  const requests: number[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/pulls/pr/runs")) requests.push(Number(url.searchParams.get("page")));
  });
  await page.goto("/pulls/pr");
  await expect(page.getByRole("tab", { name: "Run history 23" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review in progress" })).toBeDisabled();
  await expect(page.getByText("Not observed in latest review", { exact: true })).toHaveCount(0);
  expect(requests).toEqual([]);
  await page.getByRole("tab", { name: "Run history 23" }).click();
  const history = page.getByRole("tabpanel");
  await expect(history.getByRole("link")).toHaveCount(10);
  await expect(history.getByRole("status")).toHaveText("10 of 23");
  expect(requests).toEqual([1]);
  await noOverflow(page);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/run-history-pagination-${info.project.name}.png`, fullPage: true });
  await page.clock.fastForward(5000);
  await expect.poll(() => requests.length).toBe(2);
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(20);
  await expect(history.getByRole("status")).toHaveText("20 of 23");
  const expandedRequests = requests.length;
  await page.clock.fastForward(15000);
  expect(requests.length).toBe(expandedRequests);
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(23);
  await expect(history.getByRole("status")).toHaveText("23 of 23");
  await expect(history.getByRole("button", { name: /Load more/ })).toHaveCount(0);
  expect(requests).toEqual([1, 1, 2, 3]);
  await page.getByRole("tab", { name: "Findings 1", exact: true }).click();
  await expect(page.getByText("Not observed in latest review", { exact: true })).toHaveCount(0);
});

test("run history load more stays available during polling and waits for the next page", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  const runs = Array.from({ length: 23 }, (_, index) => ({ ...run, id: `history-${index}` }));
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [] }));
  let hold = false;
  let waiting = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/pulls/pr/runs?**", async (route) => {
    if (hold) {
      waiting++;
      await held;
    }
    await route.fulfill({ json: runPage(runs, new URL(route.request().url()).searchParams) });
  });
  await page.goto("/pulls/pr");
  await page.getByRole("tab", { name: /Run history/ }).click();
  const history = page.getByRole("tabpanel");
  const more = history.getByRole("button", { name: "Load more run history" });
  await expect(history.getByRole("link")).toHaveCount(10);
  hold = true;
  await page.clock.fastForward(5000);
  await expect.poll(() => waiting).toBe(1);
  await expect(more).toBeEnabled();
  await more.click();
  await expect(history.getByRole("button", { name: "Loading… run history" })).toBeDisabled();
  hold = false;
  release();
  await expect(history.getByRole("link")).toHaveCount(20);
  await expect(more).toBeEnabled();
});

test("run history retries failed pages and hides loaded runs when access is revoked", async ({ page }) => {
  await mock(page);
  const runs = Array.from({ length: 23 }, (_, index) => ({ ...run, id: `history-${index}` }));
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [] }));
  let failure = 502;
  await page.route("**/api/v1/pulls/pr/runs?**", (route) => failure
    ? route.fulfill({ status: failure, json: { detail: failure === 403 ? "Repository access revoked" : "Run history temporarily unavailable" } })
    : route.fallback());
  await page.goto("/pulls/pr");
  await page.getByRole("tab", { name: /Run history/ }).click();
  const history = page.getByRole("tabpanel");
  await expect(history.getByText("Run history temporarily unavailable")).toBeVisible();
  await expect(history.getByText("No reviews yet")).toHaveCount(0);
  failure = 0;
  await history.getByRole("button", { name: "Retry run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(10);
  failure = 502;
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByRole("button", { name: "Retry run history" })).toBeVisible();
  await expect(history.getByRole("link")).toHaveCount(10);
  await expect(history.getByRole("status")).toHaveText("10 of 23");
  failure = 0;
  await history.getByRole("button", { name: "Retry run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(20);
  failure = 403;
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByText("Repository access revoked")).toBeVisible();
  await expect(history.getByRole("link")).toHaveCount(0);
});

test("run history deduplicates shifted pages without changing the server total or cursor", async ({ page }) => {
  await mock(page);
  const runs = Array.from({ length: 23 }, (_, index) => ({ ...run, id: `history-${index}` }));
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [] }));
  await page.goto("/pulls/pr");
  await page.getByRole("tab", { name: /Run history/ }).click();
  const history = page.getByRole("tabpanel");
  await expect(history.getByRole("link")).toHaveCount(10);
  runs.unshift({ ...run, id: "new-run" });
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(19);
  await expect(history.getByRole("status")).toHaveText("19 of 24");
  await history.getByRole("button", { name: "Load more run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(23);
  await expect(history.getByRole("status")).toHaveText("23 of 24");
  await expect(history.getByRole("button", { name: /Load more/ })).toHaveCount(0);
});

test("pull detail paginates each finding status independently and resets pages with filters", async ({ page }, info) => {
  await mock(page);
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.endsWith("/pulls/pr/findings")) requests.push(url.searchParams);
  });
  const findings = ["open", "resolved", "dismissed"].flatMap((status) => Array.from({ length: 21 }, (_, index) => ({
    ...finding, id: `${status}-${index}`, status, sync_intent: null,
    content: `Test finding ${index + 1}: validate incoming webhook data before processing.`,
    path: `src/${status}/handler-${index + 1}.ts`, severity: index % 2 ? "medium" : "high",
    category: index === 20 ? "custom_rule" : index === 19 ? "performance" : "bug",
  })));
  await mockPullDetail(page, () => ({
    pull_request: pr, repository: repo, can_write: true, runs: [], findings,
  }));
  await page.goto("/pulls/pr");
  const open = page.getByRole("region", { name: "Open findings", exact: true });
  const resolved = page.getByRole("region", { name: "Resolved findings", exact: true });
  const dismissed = page.getByRole("region", { name: "Dismissed findings", exact: true });
  const rows = (section: typeof open) => section.getByRole("button", { name: /Test finding/ });
  await expect(rows(open)).toHaveCount(10);
  await expect(rows(resolved)).toHaveCount(10);
  await expect(dismissed).toHaveCount(0);
  expect(requests[0].getAll("status")).toEqual(["open", "resolved"]);
  await open.getByRole("button", { name: "Load more open findings", exact: true }).click();
  await expect(rows(open)).toHaveCount(20);
  expect(requests.some((params) => params.get("status") === "open" && params.get("group") === "open" && params.get("page") === "2")).toBe(true);
  await page.getByRole("button", { name: "Remove Status filter", exact: true }).click();
  for (const section of [open, resolved, dismissed]) {
    await expect(rows(section)).toHaveCount(10);
    await expect(section.getByRole("heading")).toContainText("21");
    await expect(section.getByText("10 of 21", { exact: true })).toBeVisible();
  }
  await noOverflow(page);
  await open.getByRole("button", { name: "Load more open findings", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/finding-pagination-${info.project.name}.png`, animations: "disabled" });
  await open.getByRole("button", { name: "Load more open findings", exact: true }).press("Enter");
  await expect(rows(open)).toHaveCount(20);
  await expect(rows(resolved)).toHaveCount(10);
  await expect(rows(dismissed)).toHaveCount(10);
  await resolved.getByRole("button", { name: "Load more resolved findings", exact: true }).click();
  await expect(rows(resolved)).toHaveCount(20);
  await dismissed.getByRole("button", { name: "Load more dismissed findings", exact: true }).click();
  await expect(rows(dismissed)).toHaveCount(20);
  await open.getByRole("button", { name: "Load more open findings", exact: true }).click();
  await expect(rows(open)).toHaveCount(21);
  for (const status of ["open", "resolved", "dismissed"]) expect(requests.some((params) => params.get("group") === status && params.get("page") === "2")).toBe(true);
  expect(requests.some((params) => params.get("group") === "open" && params.get("page") === "3")).toBe(true);
  await expect(open.getByRole("button", { name: /Load more/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Custom rule/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Custom rule/ })).toBeChecked();
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(1);
  await page.getByRole("checkbox", { name: /^Performance/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Performance/ })).toBeChecked();
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(2);
  await page.keyboard.press("Escape");
  expect(requests.at(-1)?.getAll("category")).toEqual(["custom_rule", "performance"]);
  expect(requests.at(-1)?.has("group")).toBe(false);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/finding-type-filter-${info.project.name}.png`, animations: "disabled" });
  await page.getByRole("button", { name: "Remove Type filter", exact: true }).click();
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(10);

  await page.getByLabel("Search findings", { exact: true }).fill("handler-21.ts");
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(1);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(10);
  await open.getByRole("button", { name: "Load more open findings", exact: true }).click();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "High", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "High", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  for (const section of [open, resolved, dismissed]) {
    await expect(rows(section)).toHaveCount(10);
    await expect(section.getByRole("heading")).toContainText("11");
  }
  await resolved.getByRole("button", { name: "Load more resolved findings", exact: true }).click();
  await expect(rows(resolved)).toHaveCount(11);
  await page.getByRole("button", { name: "Remove Severity filter", exact: true }).click();
  await expect(rows(resolved)).toHaveCount(10);
  await open.getByRole("button", { name: "Load more open findings", exact: true }).click();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Status", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Open/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(rows(open)).toHaveCount(10);
  await expect(resolved).toHaveCount(0);
  await expect(dismissed).toHaveCount(0);
  await page.getByRole("button", { name: "Remove Status filter", exact: true }).click();
  for (const section of [open, resolved, dismissed]) await expect(rows(section)).toHaveCount(10);
});

for (const state of ["merged", "closed", "paused", "running", "viewer", "fork"] as const) {
  test(`pull detail handles review availability for ${state}`, async ({ page }) => {
    await mock(page);
    await mockPullDetail(page, () => ({
      pull_request: { ...pr, state: state === "merged" || state === "closed" ? state : "open", fork: state === "fork" },
      repository: { ...repo, enabled: state !== "paused" }, can_write: state !== "viewer", findings: [],
      runs: state === "running" ? [{ ...run, status: "running", publication: "pending" }] : [],
    }));
    await page.goto("/pulls/pr");
    const summary = page.getByRole("region", { name: "Latest review" });
    const messages = {
      merged: "Reviews can only run on open pull requests.", closed: "Reviews can only run on open pull requests.", paused: "Reviews are paused.",
      running: "A review is already queued or running.", viewer: "You have read-only access",
      fork: "Fork reviews require a manual start.",
    };
    if (state === "running") await expect(page.getByText(messages[state], { exact: true })).toHaveCount(0);
    else await expect(summary).toContainText(messages[state]);
    if (state === "viewer") {
      await expect(summary.getByRole("button")).toHaveCount(0);
      await expect(page.getByLabel("Review connection")).toHaveCount(0);
    } else if (state === "fork") await expect(summary.getByRole("button", { name: "Run review" })).toBeEnabled();
    else {
      await expect(summary.getByRole("button")).toBeDisabled();
      await expect(page.getByLabel("Review connection")).toBeDisabled();
    }
    await noOverflow(page);
  });
}

test("pull detail retains evidence on transient errors and hides it after access is denied", async ({ page }) => {
  await mock(page);
  await page.goto("/pulls/pr");
  await expect(page.getByRole("region", { name: "Findings", exact: true })).toBeVisible();
  let status = 503;
  await page.route("**/api/v1/pulls/pr", (route) => route.fulfill({ status, json: { detail: status === 403 ? "Access denied" : "Service unavailable" } }));
  await expect(page.getByRole("alert")).toContainText("Service unavailable", { timeout: 10_000 });
  await expect(page.getByRole("region", { name: "Findings", exact: true })).toBeVisible();
  status = 403;
  await expect(page.getByRole("alert")).toContainText("Access denied", { timeout: 10_000 });
  await expect(page.getByRole("region", { name: "Findings", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run review" })).toHaveCount(0);
});

for (const kind of ["repository", "findings"] as const) {
  test(`${kind} backend pages keep rows on failure and clear them on access denial`, async ({ page }) => {
    await mock(page);
    const findings = Array.from({ length: 21 }, (_, index) => ({ ...finding, id: `finding-${index}`, content: `Test finding ${index}` }));
    const pulls = Array.from({ length: 21 }, (_, index) => ({ ...pr, id: `pr-${index}`, title: `Test pull request ${index}` }));
    if (kind === "findings") await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, runs: [], findings, can_write: true }));
    let failure = 502;
    const requests: URLSearchParams[] = [];
    const endpoint = kind === "repository" ? "/repositories/22/pulls" : "/pulls/pr/findings";
    await page.route(`**/api/v1${endpoint}?**`, (route) => {
      const params = new URL(route.request().url()).searchParams;
      requests.push(params);
      if (failure && params.has("group")) return route.fulfill({ status: failure, json: { detail: failure === 403 ? "Access denied" : "Page unavailable" } });
      return route.fulfill({ json: kind === "repository" ? repositoryPage(pulls, params) : findingPage(findings, params) });
    });
    await page.goto(kind === "repository" ? "/repositories/22" : "/pulls/pr");
    const group = page.getByRole("region", { name: kind === "repository" ? "Open" : "Open findings", exact: true });
    const rows = group.getByRole(kind === "repository" ? "link" : "button", { name: /Test (finding|pull request)/ });
    await expect(rows).toHaveCount(10);
    await group.getByRole("button", { name: /Load more/ }).click();
    await expect(group.getByRole("alert")).toContainText("Page unavailable");
    await expect(rows).toHaveCount(10);
    expect(requests.some((params) => params.get("page") === "2")).toBe(true);
    failure = 0;
    await group.getByRole("button", { name: /Retry/ }).click();
    await expect(rows).toHaveCount(20);
    failure = 403;
    await group.getByRole("button", { name: /Load more/ }).click();
    await expect(page.getByRole("alert")).toContainText("Access denied");
    await expect(rows).toHaveCount(0);
  });
}
