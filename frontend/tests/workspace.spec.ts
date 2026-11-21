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
  code_graph_enabled: false,
  id: 22,
  installation_id: 10,
  full_name: "acme/payments",
  private: true,
  active: true,
  enabled: true,
  connection_id: "connection",
  settings: {
    analysis_engine: "mortis" as const,
    automatic: true,
    model: "",
    effort: "medium",
    review_sensitivity: "balanced" as const,
    language: "English",
    exclusions: "",
    background: "",
    timeout_seconds: 900,
    token_budget: 200000,
    assessment_token_budget: 200000,
    verification_token_budget: 200000,
    summary_diagram_enabled: false,
    ai_fix_prompts_enabled: false,
    summary_token_budget: 60000,
    summary_timeout_seconds: 180,
    max_files: 40,
    delegated_context_bytes: 800000,
    concurrency: 2,
    rules: null,
  },
  open_prs: 2,
  has_running_review: false,
};

test("code graph stays opt-in and saves with the existing repository draft", async ({ page }) => {
  const requests = await mock(page);
  let statusReads = 0;
  await page.route("**/api/v1/repositories/22/code-graph", async (route) => { statusReads++; await route.fulfill({ json: { state: "disabled" } }); });
  await page.goto("/repositories/22/settings?tab=code-graph");
  const toggle = page.getByRole("switch", { name: "Code graph", exact: true });
  await expect(toggle).not.toBeChecked();
  await expect(toggle).toHaveAccessibleDescription(/eligible Mortis PR reviews can use related source/);
  await expect(toggle).toHaveAccessibleDescription(/Security scans do not use graph enrichment/);
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(page.getByText("Save graph settings before indexing.", { exact: true })).toBeVisible();
  await repositorySection(page, "Project context");
  await page.getByRole("textbox", { name: "Project context", exact: true }).fill("Existing project context");
  await repositorySection(page, "Pull request reviews");
  await repositorySection(page, "Code graph");
  await expect(toggle).toBeChecked();
  expect(statusReads).toBe(0);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?tab=code-graph$/);
  expect(requests.find((r) => r.path === "/repositories/22")?.body).toMatchObject({
    code_graph_enabled: true, enabled: true, connection_id: "connection", settings: { background: "Existing project context" },
  });
});

for (const mode of ["light", "dark"] as const) {
  test(`code graph readiness and retry cancel actions ${mode}`, async ({ page }, info) => {
    await page.addInitScript((value) => localStorage.setItem("theme", value), mode);
    const requests = await mock(page);
    await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, code_graph_enabled: true }] }));
    const status: Schema["CodeGraphStatus"] = { enabled: true, paused: false, state: "partial", refreshing: false,
      commit_sha: "b".repeat(40), indexed_sha: "b".repeat(40), snapshot_id: "snapshot", checked_at: at, finished_at: at, error: null,
      coverage: { parsed: 239, partial: 1, unsupported: 72, failed: 0, too_large: 0, visible_files: 312, complete: false, reasons: { syntax_error: 1, unsupported_language: 72 },
        warning_files: [{ path: "src/MainView.tsx", reason: "syntax_error", diagnostic: { start_byte: 50, line: 51, column: 20, context: "type_annotation", token: "import", missing_token: null } }] } };
    const actions: string[] = [];
    await page.route("**/api/v1/repositories/22/code-graph**", async (route) => {
      if (route.request().method() === "POST") {
        const action = new URL(route.request().url()).pathname.split("/").at(-1)!;
        actions.push(action);
        status.state = action === "retry" ? "queued" : "cancelled";
        status.paused = action === "cancel";
        status.error = null;
      }
      await route.fulfill({ json: status });
    });
    await page.goto("/repositories/22/settings?tab=code-graph");
    await expect(page.getByText("Indexed with warnings", { exact: true })).toBeVisible();
    await expect(page.getByText("Fully parsed", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Unsupported formats", { exact: true })).toHaveCount(0);
    const statusRow = page.getByRole("status").filter({ hasText: "Revision bbbbbbbb" });
    const warningButton = statusRow.getByRole("button", { name: "View 1 warning", exact: true });
    await expect(warningButton).toHaveCSS("font-size", await statusRow.locator("code").evaluate(el => getComputedStyle(el).fontSize));
    await expect(warningButton).toHaveCSS("text-decoration-line", "none");
    await expect(warningButton).toHaveCSS("padding-left", "0px");
    await expect(warningButton).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await warningButton.focus();
    await page.keyboard.press("Enter");
    const warnings = page.getByRole("dialog", { name: "Indexing warnings" });
    await expect(warnings.getByText("src/MainView.tsx", { exact: true })).toBeVisible();
    await expect(warnings).toContainText("Line 51, column 20");
    await expect(warnings).toContainText('The parser could not read "import" within type annotation.');
    const dialogHeight = await warnings.evaluate(el => el.clientHeight);
    await warnings.getByText("Technical details", { exact: true }).click();
    expect(await warnings.evaluate(el => el.clientHeight)).toBe(dialogHeight);
    const details = warnings.getByRole("region", { name: "src/MainView.tsx technical details" });
    await expect(details).toContainText("Valid syntax can also be unsupported");
    const row = warnings.getByRole("listitem").filter({ hasText: "src/MainView.tsx" });
    const rightInset = await row.evaluate(el => el.getBoundingClientRect().right - parseFloat(getComputedStyle(el).paddingRight));
    expect(Math.abs(rightInset - await details.evaluate(el => el.getBoundingClientRect().right))).toBeLessThanOrEqual(1);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/code-graph-${info.project.name}-${mode}.png`, fullPage: true });
    await page.keyboard.press("Escape");
    await expect(warnings).toHaveCount(0);
    await expect(warningButton).toBeFocused();
    status.state = "failed";
    status.error = "Could not update the code index. Check repository access and retry.";
    await page.reload();
    await expect(page.getByRole("alert").filter({ hasText: status.error })).toBeVisible();
    const retry = page.getByRole("button", { name: "Retry indexing", exact: true });
    await retry.focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Cancel indexing", exact: true }).click();
    await expect(page.getByText("Indexing is paused. Retry to resume automatic updates.")).toBeVisible();
    expect(actions).toEqual(["retry", "cancel"]);
    expect(requests.filter((r) => r.path === "/repositories/22")).toHaveLength(0);
    await page.getByRole("switch", { name: "Code graph", exact: true }).uncheck();
    await expect(page.getByText("Save to disable indexing and code access.")).toBeVisible();
    await expect(retry).toBeDisabled();
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
    await expect(page).toHaveURL(/\/repositories\/22\/settings\?tab=code-graph$/);
    expect(requests.find((r) => r.path === "/repositories/22")?.body).toMatchObject({ code_graph_enabled: false });
  });
}

test("previous index warnings show saved file reasons without a current coverage claim", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, code_graph_enabled: true }] }));
  await page.route("**/api/v1/repositories/22/code-graph", (route) => route.fulfill({ json: {
    enabled: true, state: "stale", commit_sha: "b".repeat(40), indexed_sha: "b".repeat(40), coverage: null,
    previous_coverage: { parsed: 649, partial: 1, unsupported: 245, failed: 1, too_large: 1, visible_files: 897, complete: false,
      reasons: { syntax_error: 1, invalid_encoding: 1, file_too_large: 1 },
      warning_files: [{ path: "src/old-parser.ts", reason: "syntax_error", diagnostic: null }, { path: "src/unreadable.py", reason: "invalid_encoding", diagnostic: null }],
      oversized_files: [{ path: "src/huge.ts", size_bytes: 2097152, limit_bytes: 1048576 }] },
  } }));
  await page.goto("/repositories/22/settings?tab=code-graph");
  const warningButton = page.getByRole("button", { name: "View 3 previous warnings", exact: true });
  await expect(warningButton).toHaveCSS("text-decoration-line", "none");
  await warningButton.click();
  const warnings = page.getByRole("dialog", { name: "Previous index warnings" });
  await expect(warnings.getByText("src/old-parser.ts", { exact: true })).toBeVisible();
  await expect(warnings).toContainText("The parser could not read some syntax");
  await expect(warnings).toContainText("The file could not be read as UTF-8");
  await expect(warnings).toContainText("2.00 MiB exceeds the indexing limit of 1 MiB");
  await expect(page.getByText("Indexed with warnings", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Index coverage", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/updated index is pending/)).toHaveCount(0);
  await noOverflow(page);
  await warnings.getByRole("button", { name: "Close", exact: true }).last().click();
  await expect(warnings).toHaveCount(0);
  await expect(warningButton).toBeFocused();
});

test("a previous clean index does not show historical counts under the current status", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, code_graph_enabled: true }] }));
  await page.route("**/api/v1/repositories/22/code-graph", (route) => route.fulfill({ json: {
    enabled: true, state: "stale", commit_sha: "b".repeat(40), coverage: null,
    previous_coverage: { parsed: 649, partial: 0, unsupported: 245, failed: 0, too_large: 0, visible_files: 894, complete: false },
  } }));
  await page.goto("/repositories/22/settings?tab=code-graph");
  await expect(page.getByLabel("Index coverage", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /View .*previous warnings?/ })).toHaveCount(0);
  await expect(page.getByText("Fully parsed", { exact: true })).toHaveCount(0);
});

test("code graph is unavailable to workspace members", async ({ page }) => {
  await mock(page, { role: "member" });
  await page.goto("/repositories/22/settings?tab=code-graph");
  await expect(page.getByRole("switch", { name: "Code graph", exact: true })).toBeHidden();
});
test("code graph limits retain drafts across tabs and focus invalid settings", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, code_graph_enabled: true }] }));
  await page.route("**/api/v1/repositories/22/code-graph", (route) => route.fulfill({ json: {
    enabled: true, state: "failed", indexed_sha: "b".repeat(40), commit_sha: "a".repeat(40),
    error: "1,207 eligible files exceed the 1,000-file limit. Increase Maximum indexed files or add exclusions.",
    failure: { code: "file_count", stage: "admission", attempt_reference: "00000000-0000-0000-0000-000000000001:1", measurements: { actual: 1207, limit: 1000 } },
  } }));
  await page.goto("/repositories/22/settings?tab=code-graph");
  const files = page.getByRole("spinbutton", { name: "Maximum indexed files", exact: true });
  const individual = page.getByRole("spinbutton", { name: "Maximum individual file size (MiB)", exact: true });
  const total = page.getByRole("spinbutton", { name: "Maximum total source size (MiB)", exact: true });
  const retry = page.getByRole("button", { name: "Retry indexing", exact: true });
  await expect(files).toHaveValue("1000");
  await expect(individual).toHaveValue("1");
  await expect(total).toHaveValue("8");
  await expect(retry).toBeEnabled();
  await page.getByText("Indexing error details", { exact: true }).click();
  await expect(page.getByText("file_count", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/graph-errors-${info.project.name}.png`, fullPage: true });
  await files.fill("5001");
  await expect(retry).toBeDisabled();
  await repositorySection(page, "Project context");
  await page.getByText("Mandatory file exclusions", { exact: true }).click();
  await expect(page.getByText(/Markdown, MDX, text documentation/)).toBeVisible();
  await page.getByRole("textbox", { name: "Additional file exclusions", exact: true }).fill("fixtures/**");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/tab=code-graph/);
  await expect(files).toBeFocused();
  await files.fill("2000");
  await individual.fill("4");
  await total.fill("2");
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(individual).toBeFocused();
  await total.fill("32");
  await noOverflow(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/graph-limits-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?tab=code-graph$/);
  expect(requests.find((r) => r.path === "/repositories/22")?.body).toMatchObject({
    code_graph_enabled: true, code_graph_limits: { max_files: 2000, max_file_bytes: 4194304, max_source_bytes: 33554432 },
    settings: { analysis_engine: "mortis", exclusions: "fixtures/**" },
  });
});

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
      const ordering = params.get("ordering") ?? "-severity";
      const field = ordering.replace(/^-/, "") as "severity" | "path" | "created_at" | "updated_at";
      const ranks: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };
      matches.sort((a, b) => (field === "severity" ? ranks[a.severity] - ranks[b.severity] : a[field].localeCompare(b[field])) * (ordering.startsWith("-") ? -1 : 1));
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

function pullMetadata({ runs, findings, pull_request, ...metadata }: PullDetailFixture) {
  return {
    ...metadata, pull_request: { ...pull_request, latest_run: runs[0] ?? null }, run_count: runs.length,
    finding_counts: Object.fromEntries(["open", "resolved", "dismissed"].map((state) => [state, findings.filter((finding) => finding.status === state).length])),
    has_active_run: runs.some((run) => ["queued", "running"].includes(run.status)),
    latest_observed_run_id: runs.find((run) => ["completed", "partial"].includes(run.status))?.id ?? null,
  };
}

function runPage(runs: Run[], params: URLSearchParams): Schema["RunPage"] {
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(runs.length / 10)));
  return { items: runs.slice((page - 1) * 10, page * 10), total: runs.length, page, has_more: page * 10 < runs.length, page_size: 10 };
}

function fixtureRevision(value: unknown) {
  return Array.from(JSON.stringify(value)).reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) >>> 0, 0);
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
  const size = Number(params.get("page_size") ?? 10);
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(matches.length / size)));
  return { revision: fixtureRevision(candidates), items: matches.slice((page - 1) * size, page * size), total: matches.length, registered_total: candidates.length,
    page, has_more: page * size < matches.length, page_size: size,
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
  const size = Number(params.get("page_size") ?? 10);
  const page = Math.min(Number(params.get("page") ?? 1), Math.max(1, Math.ceil(matches.length / size)));
  return { revision: String(fixtureRevision(findings)), items: matches.slice((page - 1) * size, page * size), total: matches.length, registered_total: findings.length,
    page, has_more: page * size < matches.length, page_size: size,
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
    totalTokens?: number | null;
    overview?: Partial<Schema["PullRequestOut"]>;
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
  const fixedSnapshots = new Map<string, Schema["OccurrenceOut"]>();
  let savedContext: Pick<Schema["RepositoryOut"]["settings"], "background" | "project_summary"> | undefined;
  let savedScanSettings: Schema["RepositoryOut"]["scan_settings"] | undefined;
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
    if (path === "/activity/read") return route.fulfill({ status: 204 });
    const currentRun = { ...run, ...options.run };
    const observations: Schema["OccurrenceOut"][] = options.occurrences ?? (currentRun.status === "skipped" ? [] : [
      { finding_id: "finding", snapshot: finding, inline_line: 46 },
    ]);
    const fixed = (options.resolved ?? []).map((item) => {
      if (!fixedSnapshots.has(item.id)) fixedSnapshots.set(item.id, { finding_id: item.id, snapshot: structuredClone(item), inline_line: null });
      return fixedSnapshots.get(item.id)!;
    });
    const savedRevision = String(fixtureRevision([options.candidates ?? [], observations.map(({ finding_id, snapshot, is_new }) => ({ finding_id, snapshot, is_new })), fixed]));
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
    else if (path === "/repositories") data = [{ ...repo, scan_settings: savedScanSettings, settings: { ...repo.settings, ...savedContext } }];
    else if (path === "/repositories/22" && method === "PUT") {
      const nextScanSettings = request.postDataJSON().scan_settings;
      if (nextScanSettings) savedScanSettings = { ...savedScanSettings, ...nextScanSettings };
      data = { ...repo, scan_settings: savedScanSettings };
    }
    else if (/^\/repositories\/\d+\/activity$/.test(path)) data = {
      days: Array.from({ length: 365 }, (_, index) => ({
        date: new Date(Date.UTC(2025, 10, 21 + index)).toISOString().slice(0, 10), reviews: 0, scans: 0,
      })),
    };
    else if (path === "/repositories/22/background" && options.background) {
      options.background.mode = request.postDataJSON().mode;
      data = { ...options.background, status: "queued", background: null, error: null };
    } else if (path.startsWith("/repositories/22/background/") && options.background) {
      const { mode, status, background, project_summary } = options.background;
      if (mode === "context_refresh" && status === "done" && background && project_summary) {
        savedContext = { background, project_summary };
      }
      data = options.background;
    } else if (path === "/repositories/22/pulls")
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
        total_tokens: options.totalTokens ?? null,
        pull_request: { ...pr, ...options.overview },
        repository: repo,
        occurrences: observations,
        finding_state_counts: { open: observations.length, resolved: fixed.length },
        findings_revision: savedRevision,
        assessment: options.assessment ?? null,
        resolved_count: options.resolved?.length ?? 0,
        resolved_revision: String(fixtureRevision(options.resolved ?? [])),
        assessment_revision: fixtureRevision(options.candidates ?? []),
        can_write: options.canWrite !== false,
      };
    else if (path === "/runs/run/findings") {
      const params = new URL(request.url()).searchParams;
      const rows = params.get("status") === "resolved" ? fixed : observations;
      const result = (item: Schema["OccurrenceOut"]) => item.is_new === true ? "new" : item.is_new === false ? "repeated" : "recorded";
      const selected = rows.filter((item) => !params.has("outcome") || params.getAll("outcome").includes(result(item)));
      const paged = resolvedPage(selected.map((item) => ({ ...finding, ...item.snapshot, id: item.finding_id })), params);
      data = { ...paged, revision: savedRevision, registered_total: rows.length,
        items: paged.items.map((item) => rows.find((row) => row.finding_id === item.id)),
        outcome_counts: Object.fromEntries(["new", "repeated", "recorded"].map((value) => [value, rows.filter((item) => result(item) === value).length])),
      };
    }
    else if (path.startsWith("/runs/run/findings/")) {
      const rows = new URL(request.url()).searchParams.get("status") === "resolved" ? fixed : observations;
      data = rows.find((item) => item.finding_id === path.split("/").at(-1));
      if (!data) return route.fulfill({ status: 404, json: { detail: "Finding not recorded in this run." } });
    }
    else if (path === "/runs/run/resolved") data = resolvedPage(options.resolved ?? [], new URL(request.url()).searchParams);
    else if (path === "/runs/run/candidates") data = candidatePage(options.candidates ?? [], new URL(request.url()).searchParams);
    else if (path.startsWith("/runs/run/candidates/")) data = { ...options.candidates?.find((candidate) => candidate.id === path.split("/").at(-1)), revision: fixtureRevision(options.candidates ?? []) };
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
  if (page.viewportSize()!.width < 1024 && /^\/(?:repositories\/[^/]+(?:\/(?:settings|overview|analytics))?|pulls\/[^/]+|runs\/[^/]+|security\/(?:repositories|scans)\/[^/]+)\/?$/.test(new URL(page.url()).pathname)) {
    await expect(toggle).toBeVisible();
    await toggle.click();
  }
}

async function closeNavigation(page: Page) {
  const close = page.getByRole("button", { name: "Close navigation", exact: true });
  if (await close.isVisible()) await close.click();
}

async function chooseRepository(page: Page, name: string) {
  await closeNavigation(page);
  await page.getByRole("combobox", { name: /^Repository:/ }).click();
  await page.getByRole("combobox", { name: "Find a repository", exact: true }).fill(name.split("/").at(-1)!);
  await page.getByRole("option", { name: name.split("/").at(-1)!, exact: true }).click();
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true }).click();
}

async function breadcrumbAncestor(page: Page, name: string) {
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.getByRole("link", { name, exact: true, disabled: false, includeHidden: true })).toBeAttached();
  const link = breadcrumb.getByRole("link", { name, exact: true, disabled: false });
  if (await link.isVisible()) return link;
  await breadcrumb.getByRole("button", { name: "Show breadcrumb ancestors" }).click();
  return page.getByRole("menuitem", { name, exact: true });
}

async function repositoryPullsLink(page: Page) {
  await openNavigation(page);
  return page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true });
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

test("review activity shows saved milestones in order and follows stage retries", async ({ page }, info) => {
  await page.clock.install();
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const saved: Partial<Run> = { status: "running", finished_at: null, publication: "pending", publication_error: null,
    stages: {
      ocr: { tokens: 0, status: "completed", started_at: at, finished_at: "2026-10-05T13:20:30Z" },
      assessment: { tokens: 0, status: "running", started_at: "2026-10-05T13:21:00Z", finished_at: "2026-10-05T13:20:45Z" },
      verification: { tokens: 0, status: "skipped", error: "No earlier findings." },
    } };
  await mock(page, { run: saved });
  await page.goto("/runs/run?tab=activity");
  await expect(page.getByRole("tab", { name: "Activity 5", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 5");
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(timeline.locator("li p.font-medium")).toHaveText([
    "Assessment started", "Code review completed", "Code review started", "Review started", "Review queued",
  ]);
  await expect(timeline.getByText("Assessment completed", { exact: true })).toHaveCount(0);
  await expect(timeline.getByText("Fix verification skipped", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/earlier retry attempts are not retained/)).toBeVisible();
  const failure = "AI result delivery failed. Technical details: saved provider response " + "detail".repeat(120);
  Object.assign(saved, { status: "partial", finished_at: "2026-10-05T13:22:00Z",
    stages: { ...saved.stages, assessment: { tokens: 0, status: "failed", started_at: "2026-10-05T13:21:00Z", finished_at: "2026-10-05T13:21:50Z", error: failure } } });
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tab", { name: "Activity 7", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(timeline.getByRole("listitem")).toHaveCount(7);
  await expect(timeline.locator("li p.font-medium").first()).toHaveText("Review finished with incomplete work");
  const failed = timeline.getByRole("listitem").filter({ hasText: "Assessment failed" });
  await failed.getByText("Technical details", { exact: true }).click();
  await expect(failed.getByRole("region", { name: "Assessment failed technical details" })).toHaveText(failure);
  await expect(timeline.getByRole("alert")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("review-activity.png"), fullPage: true });
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  saved.stages!.verification!.finished_at = "2026-10-05T13:22:01Z";
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 8");
  await page.getByRole("tab", { name: "Activity 8", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(8);
  await expect(page).toHaveURL(/tab=activity/);
});

test("review activity preserves cancellation and does not invent history for legacy runs", async ({ page }) => {
  await page.clock.install();
  await mock(page, { run: { status: "cancelled", stages: {}, error: "Stopped by the repository administrator." } });
  await page.goto("/runs/run?tab=activity");
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(timeline.locator("li p.font-medium")).toHaveText(["Review cancelled", "Review started", "Review queued"]);
  await expect(timeline).toContainText("Stopped by the repository administrator.");
  await expect(timeline.locator("time")).toHaveCount(3);
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 3");
  await page.route("**/api/v1/runs/run", (route) => route.fulfill({ status: 403, json: { detail: "Access denied" } }));
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert").filter({ hasText: "Access denied" })).toBeVisible();
  await expect(timeline).toHaveCount(0);
});

for (const status of ["running", "cancelled", "completed"] as const) {
test(`historical assessment ${status} preserves the original review milestone`, async ({ page }) => {
  await mock(page, { overview: { state: "merged" }, run: { status, publication: "no_new_comments", publication_error: null,
    finished_at: "2026-10-05T13:20:00Z", error: status === "cancelled" ? "Retry cancelled." : null,
    progress: { analysis_status: "completed", assessment_retry: { assessment_only: true } },
    stages: { assessment: { status, tokens: 25, started_at: "2026-10-06T13:00:00Z",
      finished_at: status === "running" ? null : "2026-10-06T13:01:00Z" } },
  } });
  await page.goto("/runs/run?tab=activity");
  const timeline = page.getByRole("list", { name: "Activity events" });
  const original = timeline.getByRole("listitem").filter({ has: page.getByText("Review completed", { exact: true }) });
  await expect(original).toHaveCount(1);
  await expect(original.locator("time")).toHaveAttribute("datetime", "2026-10-05T13:20:00Z");
  await expect(original).not.toContainText("Retry cancelled.");
  await expect(timeline.getByText("Review cancelled", { exact: true })).toHaveCount(0);
  const latest = timeline.getByRole("listitem").first();
  await expect(latest).toContainText(`Assessment ${status === "running" ? "started" : status}`);
  await expect(latest.locator("time")).toHaveAttribute("datetime", `2026-10-06T13:0${status === "running" ? "0" : "1"}:00Z`);
});
}

test("ignored run status keeps restoration navigation outside the live region", async ({ page }, info) => {
  await mock(page, { overview: { ignored: true } });
  await mockPullDetail(page, () => ({ pull_request: { ...pr, ignored: true }, repository: repo, runs: [run], findings: [], can_write: true }));
  await page.goto("/runs/run");
  const status = page.getByRole("status").filter({ hasText: "This pull request is ignored." });
  await expect(status).toHaveText("This pull request is ignored.");
  await expect(status.getByRole("link")).toHaveCount(0);
  const restore = page.getByRole("link", { name: "Open the pull request", exact: true });
  await expect(restore).toHaveAttribute("href", "/pulls/pr");
  await restore.focus();
  await expect(restore).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/pr-ignored-run-${info.project.name}.png` });
  await restore.press("Enter");
  await expect(page).toHaveURL(/\/pulls\/pr(?:\?.*)?$/);
  await expect(page.getByRole("region", { name: "Ignored pull request" })).toBeVisible();
});

test("workspace pages keep one visible heading and let keyboard users skip navigation", async ({ page }, info) => {
  await mock(page);
  for (const [name, path, heading] of [
    ["repository", "/repositories/22", "Pull requests"],
    ["pull", "/pulls/pr", `#${pr.number} ${pr.title}`],
    ["run", "/runs/run", "Review run"],
    ["settings", "/settings", "Settings"],
    ["repository-settings", "/repositories/22/settings", "Repository settings"],
  ]) {
    await page.goto(path);
    await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toHaveText(heading);
    await expect(page.getByRole("main").getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText("Loading workspace…", { exact: true })).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/cohesive-${name}-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  }
  await page.getByRole("link", { name: "Skip to content", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});

test("skip navigation preserves the filtered repository return context", async ({ page }) => {
  await mock(page);
  await page.goto("/repositories/22?q=148&author=maria&findings=open");
  const pull = page.getByRole("link", { name: /Add retry handling/ });
  await expect(pull).toBeVisible();
  const repositoryUrl = page.url();
  const repositoryPath = new URL(repositoryUrl).pathname + new URL(repositoryUrl).search;
  await pull.click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(`#${pr.number} ${pr.title}`);
  const detailUrl = page.url();
  const state = await page.evaluate(() => history.state);
  expect(state.usr.repository).toBe(repositoryPath);
  await page.getByRole("link", { name: "Skip to content", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  expect(page.url()).toBe(detailUrl);
  expect(await page.evaluate(() => history.state)).toEqual(state);
  const back = await breadcrumbAncestor(page, "Pull requests");
  await expect(back).toHaveAttribute("href", repositoryPath);
  await back.click();
  await expect(page).toHaveURL(repositoryUrl);
  await expect(page.getByLabel("Search pull requests")).toHaveValue("148");
  await pull.click();
  await (await breadcrumbAncestor(page, "payments")).click();
  await expect(page).toHaveURL("/repositories/22/overview");
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Analytics", exact: true }).click();
  await expect(page).toHaveURL("/repositories/22/analytics");
  await page.getByRole("combobox", { name: "Date range", exact: true }).fill("7");
  await page.getByRole("option", { name: "Last 7 days", exact: true }).click();
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true }).click();
  await expect(page).toHaveURL(repositoryUrl);
});

test("primary page tabs stay keyboard accessible when they wrap", async ({ page }, info) => {
  await mock(page);
  for (const [path, label] of [["/pulls/pr", "Pull request views"], ["/runs/run", "Review run views"]]) {
    await page.goto(path);
    const tabs = page.getByRole("tablist", { name: label });
    const findings = tabs.getByRole("tab", { name: /^Findings/ });
    await findings.focus();
    await page.keyboard.press("ArrowRight");
    await expect(tabs.getByRole("tab", { name: /^Resolved/ })).toBeFocused();
    await expect(page).toHaveURL(/tab=resolved/);
    await page.reload();
    await expect(tabs.getByRole("tab", { name: /^Resolved/ })).toHaveAttribute("aria-selected", "true");
    await tabs.getByRole("tab", { name: /^Resolved/ }).focus();
    await page.keyboard.press("Home");
    await expect(findings).toBeFocused();
    await expect(findings).toHaveAttribute("aria-selected", "true");
    const targets = await tabs.getByRole("tab").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
    expect(targets.every((height) => height >= 44)).toBe(true);
    await noOverflow(page);
  }
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await page.screenshot({ path: `../.impeccable/review/cohesive-run-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
});

test("home has only Inbox while organization and repository pickers open scoped navigation", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "other/service" }] }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Repository sections" })).toHaveCount(0);
  await expect(page.locator('[data-slot="sidebar-trigger"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Account menu for @carlos" })).toBeVisible();
  await page.getByRole("combobox", { name: "Organization: Workspace" }).press("Enter");
  const organization = page.getByRole("combobox", { name: "Find an organization", exact: true });
  await expect(organization).toBeFocused();
  await organization.fill("other");
  await page.getByRole("option", { name: "other", exact: true }).click();
  await expect(page).toHaveURL("/organizations/other/repositories");
  await expect(page.getByRole("navigation", { name: "Repositories in other" }).getByRole("link")).toHaveText(["other/service"]);
  await expect(page.getByRole("navigation", { name: "Repository sections" })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Repository: Choose repository", exact: true }).click();
  await page.getByRole("combobox", { name: "Find a repository", exact: true }).fill("service");
  await page.getByRole("option", { name: "service", exact: true }).click();
  await expect(page).toHaveURL("/repositories/23/overview");
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Repository sections" });
  await expect(nav.getByRole("link")).toHaveText(["Overview", "Analytics", "Pull requests", "Security", "Repository settings"]);
  await expect(nav.getByRole("link", { name: "Overview", exact: true })).toHaveAttribute("aria-current", "page");
  await nav.getByRole("link", { name: "Pull requests", exact: true }).click();
  await expect(page).toHaveURL(/\/repositories\/23\?state=open$/);
  await expect(page.getByRole("dialog", { name: "Navigation" })).toHaveCount(0);
  await page.goto("/pulls/pr");
  await expect(page.getByRole("combobox", { name: "Organization: acme", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Repository: payments", exact: true })).toBeVisible();
  await openNavigation(page);
  await expect(nav.getByRole("link", { name: "Pull requests", exact: true })).toHaveAttribute("aria-current", "page");
  if (info.project.name === "desktop") {
    const sidebar = page.locator('[data-slot="sidebar-container"]');
    const hide = page.getByRole("button", { name: "Hide sidebar" });
    const show = page.getByRole("button", { name: "Show sidebar" });
    await expect(hide).toHaveAttribute("aria-expanded", "true");
    const panel = (await sidebar.boundingBox())!;
    const trigger = (await hide.boundingBox())!;
    const breadcrumb = (await page.getByRole("navigation", { name: "Breadcrumb" }).boundingBox())!;
    expect(trigger.x - panel.x - panel.width).toBeGreaterThanOrEqual(0);
    expect(trigger.x - panel.x - panel.width).toBeLessThan(8);
    expect(trigger.x + trigger.width).toBeLessThan(breadcrumb.x);
    await hide.click();
    await expect(sidebar).toHaveAttribute("inert", "");
    await expect(show).toHaveAttribute("aria-expanded", "false");
    await expect.poll(async () => (await show.boundingBox())?.x).toBeLessThan(8);
    await show.press("Enter");
    await nav.getByRole("link", { name: "Pull requests", exact: true }).focus();
    await page.keyboard.press("Control+b");
    await expect(page.getByRole("button", { name: "Show sidebar" })).toBeFocused();
    await page.keyboard.press("Enter");
  } else {
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  }
  await noOverflow(page);
});

test("repository paths preserve legacy settings links and never fall back from an unknown id", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repo, { ...repo, id: 23, full_name: "other/payments" }] }));
  await mockRepositoryPulls(page, 23, () => []);
  await page.goto("/repositories?repo=23&configure=limits#settings");
  await expect(page).toHaveURL(/\/repositories\/23\/settings\?tab=limits$/);
  await expect(page.getByRole("heading", { name: "Run limits", exact: true })).toBeInViewport();
  await expect(page.getByRole("combobox", { name: "Organization: other", exact: true })).toBeVisible();
  await expect(page.getByRole("form", { name: "Repository settings" })).not.toContainText("other/payments");
  await expect(page.getByLabel("Time limit (seconds)", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("form", { name: "Repository settings" })).toBeVisible();
  await (await repositoryPullsLink(page)).click();
  await expect(page).toHaveURL(/\/repositories\/23\?state=open$/);
  await page.goto("/repositories/22/settings?repo=23&configure=limits&state=all&q=retry&tab=connection");
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?state=all&q=retry&tab=limits$/);
  const repositoryLink = await repositoryPullsLink(page);
  await expect(repositoryLink).toHaveAttribute("href", "/repositories/22?state=all&q=retry");
  await repositoryLink.click();
  await expect(page).toHaveURL((url) => url.pathname === "/repositories/22" && url.searchParams.size === 2
    && url.searchParams.get("state") === "all" && url.searchParams.get("q") === "retry");
  await page.goto("/repositories/999");
  await expect(page.getByRole("heading", { name: "Repository unavailable" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configure", exact: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Repository sections" })).toHaveCount(0);
  await closeNavigation(page);
  const documentRequests: string[] = [];
  page.on("request", (request) => {
    if (request.isNavigationRequest()) documentRequests.push(request.url());
  });
  await page.getByRole("link", { name: "View available repositories", exact: true }).click();
  await expect(page).toHaveURL("/repositories");
  expect(documentRequests).toEqual([]);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [] }));
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: "Connect your first repository", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "GitHub settings", exact: true })).toBeVisible();
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
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await page.goto("/repositories/22");
  const account = page.getByRole("button", { name: "Account menu for @carlos", exact: true });
  await expect(account.locator('[data-slot="avatar-image"]')).toHaveAttribute("src", avatar);
  await account.focus();
  await page.keyboard.press("Enter");
  const settings = page.getByRole("menuitem", { name: "AI connections", exact: true });
  await expect(settings).toBeFocused();
  await expect(settings).toHaveAttribute("href", "/settings?tab=connections");
  await settings.press("Enter");
  await expect(page).toHaveURL(/\/settings\?tab=connections$/);
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Navigation", exact: true })).toHaveCount(0);
  await account.press("Enter");
  await page.keyboard.press("End");
  await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(account).toBeFocused();
  await expect(page.getByRole("menuitem", { name: "Sign out", exact: true })).toHaveCount(0);
  for (const theme of ["light", "dark"]) {
    await expect(page.getByRole("button", { name: /Switch to (light|dark) mode/ })).toBeVisible();
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    if (theme === "dark") await expect(page.locator("html")).toHaveClass("dark");
    else await expect(page.locator("html")).not.toHaveClass("dark");
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
  await expect(account.locator(".motion-safe\\:animate-spin")).toHaveCount(1);
  completeLogout();
  await expect(page.getByRole("link", { name: "Continue with GitHub", exact: true })).toBeVisible();
});

test("sidebar account keeps an initials fallback for missing or failed avatars and long names", async ({ page }) => {
  await mock(page, { role: "member" });
  await page.goto("/repositories/22");
  await expect(page.getByRole("button", { name: "Account menu for @carlos", exact: true }).locator('[data-slot="avatar-fallback"]')).toHaveText("CA");
  const login = "account-with-a-long-github-username";
  let avatarFailed = false;
  await page.route("**/missing-avatar.png", (route) => { avatarFailed = true; return route.fulfill({ status: 404 }); });
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { id: 7, login, role: "member", avatar_url: "/missing-avatar.png", csrf: "csrf-from-session" } }));
  await page.reload();
  await expect.poll(() => avatarFailed).toBe(true);
  const account = page.getByRole("button", { name: `Account menu for @${login}`, exact: true });
  await expect(account).toHaveAttribute("title", `@${login}`);
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
    ? [["connections", "AI connections"], ["api-tokens", "API tokens"], ["mcp", "MCP access"], ["appearance", "Appearance"], ["github", "GitHub"], ["team", "Team"]]
    : [["connections", "AI connections"], ["api-tokens", "API tokens"], ["mcp", "MCP access"], ["appearance", "Appearance"]];
  for (const [tab, label] of sections) {
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

test("repository activity and directory failures refresh without stale navigation", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let running = true;
  let failure = 0;
  await page.route("**/api/v1/repositories", (route) => route.fulfill(failure
    ? { status: failure, json: { detail: "Repositories unavailable" } }
    : { json: [{ ...repo, has_running_review: running }] }));
  await page.goto("/repositories/22");
  const activityText = page.locator('[data-slot="page-title"]').getByText(/Review in progress/);
  await expect(activityText).toBeVisible();
  await openNavigation(page);
  const nav = page.getByRole("navigation", { name: "Repository sections" });
  const activity = nav.getByRole("link", { name: "Pull requests", exact: true }).locator(".motion-safe\\:animate-spin");
  await expect(activity).toBeVisible();
  running = false;
  await page.clock.fastForward(6000);
  await expect(activity).toHaveCount(0);
  await closeNavigation(page);
  await expect(activityText).toHaveCount(0);
  for (const status of [503, 403, 401]) {
    failure = status;
    await page.clock.fastForward(6000);
    await expect(page.getByRole("combobox", { name: /^Organization:/ })).toBeDisabled();
    await expect(page.getByRole("navigation", { name: "Repository sections" })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: /^Repository:/ })).toHaveCount(0);
  }
  failure = 0;
  await page.getByRole("button", { name: "Retry repositories", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Repository: payments", exact: true })).toBeVisible();
});

test("repository header shows successful GitHub checks and the reviewed PR commit", async ({ page }, info) => {
  await mock(page);
  const checked = "2026-11-18T10:30:00Z";
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{
    ...repo, last_pr_sync_check_at: checked,
    latest_completed_review: {
      run_id: "run", pull_request_id: "pr", number: 148, head_sha: "c".repeat(40),
      finished_at: "2026-11-18T10:00:00Z",
    },
  }] }));
  await page.goto("/repositories/22?q=148&state=open&ordering=title");
  const header = page.locator('[data-slot="page-title"]');
  const summary = header.locator('[data-slot="repository-header-summary"]');
  const policy = summary.getByRole("region", { name: "Review configuration" });
  const latest = summary.getByRole("region", { name: "Last completed review", exact: true });
  const check = summary.getByRole("region", { name: "Last successful GitHub PR check", exact: true });
  await expect(summary.getByRole("region")).toHaveCount(3);
  const enabled = policy.getByRole("heading", { name: "Review policy" }).locator("..").getByText("Reviews enabled", { exact: true });
  await expect(enabled).toHaveAttribute("data-variant", "default");
  await expect(policy).toContainText("Automatic reviews");
  await expect(policy.getByText("File limit", { exact: true })).toBeVisible();
  await expect(policy.getByText("Comment language", { exact: true })).toHaveCount(0);
  await expect(check.locator("time")).toHaveAttribute("datetime", checked);
  await expect(check.getByRole("term")).toHaveText("Open PRs");
  await expect(check.getByRole("definition")).toHaveText(String(repo.open_prs));
  await expect(check.getByRole("definition")).toHaveCSS("font-size", "12px");
  await expect(latest.locator("code")).toHaveText("ccccccc");
  const review = latest.getByRole("link", { name: "View review for PR #148", exact: true });
  await expect(review).toHaveAttribute("href", "/runs/run");
  await expect(review).toHaveAttribute("title", `Reviewed commit ${"c".repeat(40)}`);
  await expect(latest.getByRole("heading").locator("..").getByRole("link")).toHaveCount(1);
  await expect(latest.getByText("PR #148", { exact: true })).toHaveCSS("font-size", "16px");
  await expect(header.getByRole("link", { name: "View on GitHub" })).toHaveAttribute("href", `https://github.com/${repo.full_name}`);
  const [policyBox, checkBox, reviewBox] = await Promise.all([policy.boundingBox(), check.boundingBox(), latest.boundingBox()]);
  if (info.project.name === "mobile") {
    expect(checkBox!.y).toBeGreaterThanOrEqual(policyBox!.y + policyBox!.height);
    expect(reviewBox!.y).toBeGreaterThanOrEqual(checkBox!.y + checkBox!.height);
  } else {
    expect([checkBox!.y, reviewBox!.y]).toEqual([policyBox!.y, policyBox!.y]);
    expect(checkBox!.x).toBeGreaterThan(policyBox!.x);
    expect(reviewBox!.x).toBeGreaterThan(checkBox!.x);
    expect(policyBox!.height).toBeLessThan(140);
  }
  await header.screenshot({ path: info.outputPath("pull-requests-summary-cards.png") });
  const returnUrl = page.url();
  await review.click();
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await (await breadcrumbAncestor(page, "Pull requests")).click();
  await expect(page).toHaveURL(returnUrl);
  const pulls = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(pulls.getByLabel("Search pull requests")).toHaveValue("148");
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, open_prs: 0 }] }));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Pull requests", exact: true })).toBeVisible();
  await expect(summary.getByRole("region")).toHaveCount(3);
  await expect(latest).toContainText("None recorded");
  await expect(latest.getByRole("link")).toHaveCount(0);
  await expect(check).toContainText("None recorded");
  await expect(check.getByRole("definition")).toHaveText("0");
});

test("repository settings retain drafts while directory polling fails and recovers", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let failure = false;
  await page.route("**/api/v1/repositories", (route) => route.fulfill(failure
    ? { status: 503, json: { detail: "Repository directory temporarily unavailable" } }
    : { json: [repo] }));
  await page.goto("/repositories/22/settings?tab=background");
  await repositorySection(page, "Project context");
  const background = page.getByRole("textbox", { name: "Project context", exact: true });
  await background.fill("Unsaved project context");
  failure = true;
  await page.clock.fastForward(6000);
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository directory temporarily unavailable");
  await expect(background).toHaveValue("Unsaved project context");
  failure = false;
  await page.getByRole("button", { name: "Retry repositories", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveCount(0);
  await expect(background).toHaveValue("Unsaved project context");
});

test("directory picks up repository changes after the GitHub installation callback", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let repositories: (typeof repo)[] = [];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: repositories }));
  await page.goto("/settings?tab=github&installed=1");
  const picker = page.getByRole("combobox", { name: /^Organization:/ });
  await picker.click();
  await expect(page.getByRole("option", { name: "acme", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Find an organization" }).press("Escape");
  repositories = [repo];
  await page.clock.fastForward(6000);
  await picker.click();
  await expect(page.getByRole("option", { name: "acme", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "Find an organization" }).press("Escape");
  repositories = [];
  await page.clock.fastForward(6000);
  await picker.click();
  await expect(page.getByRole("option", { name: "acme", exact: true })).toHaveCount(0);
});

test("organization directory handles long names, empty access and member navigation", async ({ page }) => {
  await mock(page, { role: "member" });
  const owner = "organization-with-a-long-name";
  const name = owner + "/repository-with-a-very-long-name";
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: Array.from({ length: 40 }, (_, i) => ({ ...repo, id: i + 22, full_name: `${name}-${i}` })) }));
  await page.goto(`/organizations/${owner}/repositories`);
  const directory = page.getByRole("navigation", { name: `Repositories in ${owner}` });
  await expect(directory.getByRole("link")).toHaveCount(40);
  await noOverflow(page);
  await directory.getByRole("link", { name: `${name}-39`, exact: true }).click();
  await expect(page).toHaveURL("/repositories/61/overview");
  await openNavigation(page);
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Repository settings" })).toHaveCount(0);
  await closeNavigation(page);
  await noOverflow(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [] }));
  await page.goto(`/organizations/${owner}/repositories`);
  await expect(page.getByRole("heading", { name: "No repositories available", exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Repository sections" })).toHaveCount(0);
});

test("sidebar shortcut leaves editing controls alone", async ({ page }, info) => {
  await mock(page);
  await page.goto("/repositories/22");
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
  for (const [path, label] of [["/pulls/pr/", "#148"], ["/runs/run/", "Review run"]]) {
    await page.goto(path);
    const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText(label);
    await expect(await breadcrumbAncestor(page, "payments")).toHaveAttribute("href", `/repositories/${repo.id}/overview`);
    await page.keyboard.press("Escape");
    await openNavigation(page);
    await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true })).toHaveAttribute("aria-current", "page");
  }
});

test("repository breadcrumbs and sidebar match only supported routes", async ({ page }) => {
  await mock(page);
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  const navigation = page.getByRole("navigation", { name: "Repository sections" });
  for (const suffix of ["", "/"]) {
    await page.goto(`/repositories/22${suffix}`);
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Pull requests");
    await page.goto(`/repositories/22/settings${suffix}?state=all&tab=limits`);
    await expect(page.getByRole("form", { name: "Repository settings" })).toBeVisible();
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Repository settings");
    await expect(breadcrumb.getByRole("link")).toHaveText(["payments", "Repository settings"]);
    await page.keyboard.press("Escape");
    await openNavigation(page);
    const repository = navigation.getByRole("link", { name: "Pull requests", exact: true });
    await expect(navigation.getByRole("link", { name: "Repository settings", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(repository).toHaveAttribute("href", "/repositories/22?state=all");
    await closeNavigation(page);
  }
  for (const path of ["/repositories/22/foobar", "/repositories/22/settings/foobar"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Page not found", exact: true })).toBeVisible();
    await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Page not found");
    await openNavigation(page);
    await expect(navigation).toHaveCount(0);
    await closeNavigation(page);
  }
});

test("repository settings breadcrumbs omit Repository settings for members when access fails", async ({ page }) => {
  await mock(page, { role: "member" });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.goto("/repositories/22/settings?tab=limits");
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository access was revoked.");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Repositories");
  await expect(breadcrumb.getByText("Repository settings", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("form", { name: "Repository settings" })).toHaveCount(0);
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
  const count = page.getByRole("region", { name: "Pull requests", exact: true }).locator(':scope > p[role="status"]');
  await expect(count).toHaveText("1 of 14 pull request");
  await expect(page.locator('[data-slot="page-title"]').getByText("2 open pull requests", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Review configuration" })).toContainText("Automatic reviews");
  await expect(page.getByRole("link", { name: "View on GitHub" })).toHaveAttribute("href", "https://github.com/acme/payments");
  const merged = page.getByRole("region", { name: "Merged", exact: true });
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(1);
  await expect(page.getByRole("link", { name: /Add retry handling/ }).locator("xpath=ancestor::*[@data-pull-row]").getByText("open", { exact: true })).toBeVisible();
  await expect(merged).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Closed proposal/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Remove PR state filter" }).click();
  await expect(page).toHaveURL(/\?state=all$/);
  await expect(count).toHaveText("14 pull requests");
  await expect(merged.getByRole("link")).toHaveCount(10);
  await expect(merged.getByRole("link").first().locator("xpath=ancestor::*[@data-pull-row]").getByText("merged", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ }).locator("xpath=ancestor::*[@data-pull-row]").getByText("closed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ }).locator("xpath=ancestor::*[@data-pull-row]").getByText("Draft", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /Closed proposal/ }).locator("xpath=ancestor::*[@data-pull-row]")).toContainText("Fork · manual review");
  await merged.getByRole("button", { name: "Next merged" }).click();
  await expect(merged.getByRole("link")).toHaveCount(2);
  await expect(page).toHaveURL(/merged_pages=2/);
  await page.reload();
  await expect(merged.getByRole("link")).toHaveCount(2);
  await expect(count).toHaveText("14 pull requests");
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await expect(page.getByRole("menuitem")).toHaveText(["PR state", "Review status", "Author", "Open findings", "Visibility"]);
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
  await expect(page.getByRole("button", { name: "Sync", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Configure", exact: true })).toHaveCount(0);
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Repository settings", exact: true }).click();
  await expect(page.getByRole("form", { name: "Repository settings" })).toBeVisible();
  await noOverflow(page);
  await (await repositoryPullsLink(page)).click();
  await expect(page).toHaveURL(/q=148&state=open&author=maria&findings=open$/);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await page.getByRole("tab", { name: "Run history" }).click();
  await page.getByRole("link", { name: /automatic.*aaaaaaa/ }).click();
  await openNavigation(page);
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true })).toHaveAttribute("href", "/repositories/22?q=148&state=open&author=maria&findings=open");
  await closeNavigation(page);
  const back = await breadcrumbAncestor(page, "Pull requests");
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
  await expect(page.getByRole("region", { name: "Open", exact: true }).getByRole("link")).toHaveCount(2);
});

test("repository sensitivity preserves custom rules and saves all three policies", async ({ page }, info) => {
  const requests = await mock(page);
  const rules = { rules: [{ path: "**/*.py", rule: "Keep project checks", merge_system_rule: true }], exclude: ["generated/**"] };
  let savedSettings: Partial<Schema["RepositoryOut"]["settings"]> = { ...repo.settings, effort: "medium", rules, background: "Saved project context" };
  delete savedSettings.review_sensitivity;
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, settings: savedSettings }] }));
  const saves: Schema["RepositoryOut"]["settings"][] = [];
  await page.route("**/api/v1/repositories/22", async (route) => {
    const body = route.request().postDataJSON();
    savedSettings = { ...savedSettings, ...body.settings };
    saves.push(body.settings);
    await route.fulfill({ json: { ...repo, settings: savedSettings } });
  });
  await page.goto("/repositories/22/settings");
  const review = page.getByRole("region", { name: "Pull request reviews", exact: true });
  await expect(review.getByRole("heading", { level: 3 })).toHaveText([
    "When reviews run", "Review behavior", "GitHub output", "Run limits",
  ]);
  const slider = review.getByRole("slider", { name: "Finding sensitivity", exact: true });
  await expect(slider).toHaveValue("1");
  await expect(slider).toHaveAttribute("aria-valuetext", "Balanced");
  await expect(slider).toHaveAccessibleDescription("Actionable bugs and edge cases.");
  await slider.focus();
  await slider.press("ArrowLeft");
  await expect(slider).toHaveValue("0");
  await expect(slider).toHaveAttribute("aria-valuetext", "Low noise");
  await expect(slider).toHaveAccessibleDescription("Material bugs with likely triggers.");
  await expect(page.getByRole("textbox", { name: "Custom review rules (JSON)" })).toHaveValue(JSON.stringify(rules, null, 2));
  await expect(slider).toHaveValue("0");
  expect(saves).toHaveLength(0);
  expect(requests).toHaveLength(0);
  for (const [index, value, label] of [[0, "low_noise", "Low noise"], [1, "balanced", "Balanced"], [2, "broad", "Broad"]] as const) {
    await slider.focus();
    await slider.press("Home");
    for (let step = 0; step < index; step++) await slider.press("ArrowRight");
    await expect(slider).toHaveAttribute("aria-valuetext", label);
    const hint = slider.locator("..").locator("p");
    const layout = await hint.evaluate((element) => ({
      height: element.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(getComputedStyle(element).lineHeight),
      contentWidth: element.scrollWidth,
      availableWidth: element.clientWidth,
    }));
    expect(layout.height).toBeLessThanOrEqual(layout.lineHeight + 1);
    expect(layout.contentWidth).toBeLessThanOrEqual(layout.availableWidth);
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
    expect(saves.at(-1)).toMatchObject({ review_sensitivity: value, rules });
    expect(saves.at(-1)).not.toHaveProperty("background");
    await page.goto("/repositories/22/settings");
    await expect(slider).toHaveValue(String(index));
    await expect(slider).toHaveAttribute("aria-valuetext", label);
  }
  await slider.focus();
  await noOverflow(page);
  await slider.locator("..").screenshot({ path: `../.impeccable/review/repository-sensitivity-${info.project.name}.png` });
  expect(requests.filter((request) => request.path.includes("/reviews"))).toHaveLength(0);
});

test("repository configuration keeps drafts and stays on the selected section after saving", async ({ page }, info) => {
  const requests = await mock(page);
  await mockRepositoryPulls(page, 22, () => Array.from({ length: 11 }, (_, index) => ({ ...pr, id: `retry-${index}` })));
  await page.goto("/repositories/22/settings?q=retry&state=all&active_pages=2");
  const effort = page.getByRole("combobox", { name: "Review effort", exact: true });
  await expect(effort).toHaveValue("medium");
  await chooseOption(page, effort, "high");
  await page.getByRole("switch", { name: "Automatically review new commits", exact: true }).uncheck();
  await repositorySection(page, "Project context");
  await page.getByRole("textbox", { name: "Project context", exact: true }).fill("Keep this draft while changing sections.");
  await repositorySection(page, "Pull request reviews");
  await page.getByLabel("Maximum reviewable files", { exact: true }).fill("75");
  await expect(effort).toHaveValue("high");
  await expect(page.getByRole("switch", { name: "Automatically review new commits", exact: true })).not.toBeChecked();
  await repositorySection(page, "Project context");
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Keep this draft while changing sections.");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-settings-context-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/repositories\/22\/settings\?q=retry&state=all&active_pages=2&tab=context$/);
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Repository settings saved." })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-save-toast-${info.project.name}.png` });
  expect(requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    settings: { automatic: false, effort: "high", background: "Keep this draft while changing sections.", max_files: 75 },
  });
});

for (const reason of ["The native runner is temporarily unavailable. Try again shortly.", "The selected connection is busy."]) {
  test(`summary waiting preserves its reason: ${reason}`, async ({ page }, info) => {
    await mock(page, { run: {
      status: "running", publication: "pending", publication_error: null,
      config: { ...run.config, pipeline_version: 2 },
      summary: { status: "pending", tokens: 0, limited_context: false },
      stages: { summary: { status: "waiting", tokens: 0, error: reason } },
    } });
    await page.goto("/runs/run");
    const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
    await expect(trigger.getByText("PR summary waiting", { exact: true })).toBeVisible();
    await expect(trigger.getByLabel("Stage progress unavailable")).toHaveText("—");
    await trigger.click();
    const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
    await expect(details.getByText("PR summary", { exact: true }).locator("..")).toContainText("waiting");
    await expect(details).toContainText("Waiting to generate the summary.");
    await expect(details.getByRole("alert")).toHaveCount(0);
    await details.locator("summary", { hasText: "Technical details" }).click();
    await expect(details).toContainText(reason);
    await expect(details).not.toContainText("Waiting for the personal connection.");
    await noOverflow(page);
    await page.screenshot({ path: `/tmp/pr-summary-waiting-${reason.includes("busy") ? "busy" : "unavailable"}-${info.project.name}.png` });
  });
}

test("summary allowance is separate and keeps unlimited preferences", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?tab=limits");
  for (const [group, labels] of [
    ["Execution", ["Maximum reviewable files", "Time limit (seconds)", "Parallel review tasks"]],
    ["Token budgets", ["Analysis token budget", "Assessment token budget", "Verification token budget"]],
    ["PR summary", ["Summary time limit (seconds)", "Summary token budget"]],
  ] as const) {
    const section = page.getByRole("region", { name: group, exact: true });
    await expect(section.getByRole("heading", { name: group, level: 4, exact: true })).toBeVisible();
    for (const label of labels) await expect(section.getByLabel(label, { exact: true })).toBeVisible();
  }
  await expect(page.getByLabel("Summary token budget", { exact: true })).toHaveValue("60000");
  await expect(page.getByLabel("Summary time limit (seconds)", { exact: true })).toHaveValue("180");
  await expect(page.getByText(/Adds input and output token usage beyond the review budget/)).toBeVisible();
  await page.getByLabel("Summary token budget", { exact: true }).fill("45000");
  await page.getByLabel("Unlimited summary token budget", { exact: true }).check();
  await expect(page.getByLabel("Summary token budget", { exact: true })).toBeDisabled();
  await page.getByLabel("Unlimited summary token budget", { exact: true }).uncheck();
  await expect(page.getByLabel("Summary token budget", { exact: true })).toHaveValue("45000");
  await page.getByLabel("Summary time limit (seconds)", { exact: true }).fill("240");
  await noOverflow(page);
  await page.screenshot({ path: `/tmp/pr-summary-settings-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const body = requests.find((request) => request.path === "/repositories/22")?.body;
  expect(body).not.toHaveProperty("scan_settings");
  expect(body).toMatchObject({
    settings: { summary_token_budget: 45000, summary_timeout_seconds: 240, token_budget: 200000, timeout_seconds: 900 },
  });
});

test("repository scan limits validate across sections and save independently of review limits", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?tab=scan-limits");
  const form = page.getByRole("form", { name: "Repository settings" });
  const budget = form.getByLabel("Total scan token budget", { exact: true });
  const unlimitedBudget = form.getByLabel("Unlimited total scan token budget", { exact: true });
  const unlimitedTimeout = form.getByLabel("Unlimited scan time limit (seconds)", { exact: true });
  const concurrency = form.getByLabel("Parallel scan tasks", { exact: true });
  await expect(unlimitedBudget).toBeChecked();
  await expect(unlimitedTimeout).toBeChecked();
  await expect(concurrency).toHaveValue("4");
  await unlimitedBudget.uncheck();
  await budget.fill("250000");
  await unlimitedBudget.check();
  await expect(budget).toBeDisabled();
  await unlimitedBudget.uncheck();
  await expect(budget).toHaveValue("250000");
  await unlimitedTimeout.uncheck();
  await form.getByLabel("Scan time limit (seconds)", { exact: true }).fill("1200");
  await concurrency.fill("9");
  await repositorySection(page, "Pull request reviews");
  await expect(form.getByLabel("Analysis token budget", { exact: true })).toHaveValue("200000");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page).toHaveURL(/tab=scan-limits$/);
  await expect(concurrency).toBeFocused();
  expect(requests).toHaveLength(0);
  await concurrency.fill("6");
  await noOverflow(page);
  const effort = form.getByRole("combobox", { name: "Scan reasoning effort" });
  const exclusions = form.getByLabel("AI path exclusions");
  const cap = form.getByLabel("Maximum output tokens per response", { exact: true });
  const assessment = form.getByLabel("Scan assessment token budget", { exact: true });
  const verification = form.getByLabel("Scan verification token budget", { exact: true });
  await expect(effort).toHaveValue("High");
  await expect(exclusions).toHaveValue("");
  await expect(cap).toBeDisabled();
  await expect(assessment).toBeDisabled();
  await expect(verification).toBeDisabled();
  await chooseOption(page, effort, "Medium");
  await exclusions.fill("fixtures/**\nexamples/sample.py");
  await form.getByLabel(/^Automatic maximum output tokens/).uncheck();
  await cap.fill("4096");
  await form.getByLabel(/^No separate cap scan assessment/).uncheck();
  await assessment.fill("50000");
  await form.getByLabel(/^No separate cap scan verification/).uncheck();
  await verification.fill("55000");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    scan_settings: { token_budget: 250000, timeout_seconds: 1200, concurrency: 6, effort: "medium",
      exclusions: "fixtures/**\nexamples/sample.py", max_output_tokens: 4096,
      assessment_token_budget: 50000, verification_token_budget: 55000 },
    settings: { token_budget: 200000, timeout_seconds: 900, concurrency: 2 },
  });
  await page.reload();
  await expect(effort).toHaveValue("Medium");
  await expect(exclusions).toHaveValue("fixtures/**\nexamples/sample.py");
  await expect(cap).toHaveValue("4096");
  await expect(assessment).toHaveValue("50000");
  await expect(verification).toHaveValue("55000");
  await concurrency.fill("5");
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.filter((request) => request.path === "/repositories/22").at(-1)?.body)
    .toMatchObject({ scan_settings: { token_budget: 250000, timeout_seconds: 1200, concurrency: 5 } });
  expect(requests.filter((request) => request.path === "/repositories/22").at(-1)?.body).not.toHaveProperty("scan_settings.exclusions");
  expect(requests.filter((request) => request.path === "/repositories/22").at(-1)?.body).not.toHaveProperty("scan_settings.assessment_token_budget");
  await exclusions.clear();
  await form.getByLabel(/^No separate cap scan assessment/).check();
  await form.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.filter((request) => request.path === "/repositories/22").at(-1)?.body)
    .toMatchObject({ scan_settings: { exclusions: "", assessment_token_budget: null } });
});

test("stage budgets and optional overview content save independently", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?tab=reviews");
  for (const name of ["Include a diagram in the PR overview", "Include AI fix prompts"]) {
    const toggle = page.getByRole("switch", { name, exact: true });
    await expect(toggle).not.toBeChecked();
    await toggle.check();
  }
  await page.getByLabel("Analysis token budget", { exact: true }).fill("11000");
  await page.getByLabel("Assessment token budget", { exact: true }).fill("22000");
  await page.getByLabel("Unlimited verification token budget", { exact: true }).check();
  await page.getByLabel("Summary token budget", { exact: true }).fill("33000");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect(requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    settings: { token_budget: 11000, assessment_token_budget: 22000, verification_token_budget: null,
      summary_token_budget: 33000, summary_diagram_enabled: true, ai_fix_prompts_enabled: true },
  });
});

test("review stage animation follows execution, visibility and reduced motion", async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const state: Partial<Run> = { status: "running", publication: "pending", publication_error: null,
    config: { ...run.config, pipeline_version: 2 }, check_enabled: false,
    stages: { ocr: { status: "running", tokens: null } } };
  await mock(page, { run: state });
  await page.goto("/runs/run");
  const card = page.getByRole("region", { name: "Review stages", exact: true });
  const divider = card.locator(".stage-divider");
  const animation = () => divider.evaluate((element) => {
    const style = getComputedStyle(element, "::before");
    return { name: style.animationName, state: style.animationPlayState, transform: style.transform };
  });
  await expect(card).toHaveAttribute("data-motion", "active");
  expect(await animation()).toMatchObject({ name: "stage-sweep", state: "running" });
  const start = await animation();
  await expect.poll(async () => (await animation()).transform).not.toBe(start.transform);
  const height = (await card.boundingBox())!.height;
  await page.screenshot({ path: info.outputPath("review-stage-animation.png") });
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect((await animation()).name).toBe("none");
  await expect(card.getByRole("button", { name: "Review stage details" })).toContainText("Code review");
  expect((await card.boundingBox())!.height).toBe(height);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const viewport = page.viewportSize()!;
  await page.setViewportSize({ ...viewport, height: 300 });
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await expect(card).toHaveAttribute("data-motion", "paused");
  expect((await animation()).state).toBe("paused");
  await page.setViewportSize(viewport);
  await card.scrollIntoViewIfNeeded();
  await expect(card).toHaveAttribute("data-motion", "active");
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(card).toHaveAttribute("data-motion", "paused");
  await page.evaluate(() => {
    delete (document as { hidden?: boolean }).hidden;
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(card).toHaveAttribute("data-motion", "active");
  state.stages = { ocr: { status: "waiting", tokens: 0 } };
  await page.reload();
  await expect(card).toHaveAttribute("data-status", "waiting");
  expect((await animation()).name).toBe("none");
  state.status = "completed";
  state.stages = { ocr: { status: "completed", tokens: 100 } };
  await page.reload();
  await expect(card).toHaveAttribute("data-status", "completed");
  expect((await animation()).name).toBe("none");
  expect((await card.boundingBox())!.height).toBe(height);
  await noOverflow(page);
});

test("review stage title follows parallel work and summary publication without changing layout", async ({ page }, info) => {
  await page.clock.install();
  const state: Partial<Run> = { status: "running", report: null, progress: { selected: 8, completed: 3 },
    publication: "pending", publication_error: null, check_enabled: false,
    config: { ...run.config, pipeline_version: 2 },
    summary: { status: "pending", tokens: 0, limited_context: false }, stages: {
      ocr: { status: "running", tokens: 0 }, summary: { status: "running", tokens: 0 },
      assessment: { status: "pending", tokens: 0 }, verification: { status: "pending", tokens: 0 },
    } };
  const overview: Partial<Schema["PullRequestOut"]> = { summary_pending: false, summary_error: null };
  await mock(page, { run: state, overview });
  await page.goto("/runs/run");
  const card = page.getByRole("region", { name: "Review stages", exact: true });
  const title = card.getByRole("button", { name: "Review stage details", exact: true });
  const count = card.getByLabel(/^(Review progress count|Stage progress unavailable)$/);
  const geometry = async () => {
    const box = (await card.boundingBox())!;
    return { height: box.height, count: (await count.boundingBox())!.y - box.y,
      explanation: (await card.locator(".stage-divider").boundingBox())!.y - box.y,
      tabs: (await page.getByRole("tablist").boundingBox())!.y - box.y };
  };
  await expect(title).toContainText("Code review · PR summary");
  const original = await geometry();
  await page.screenshot({ path: info.outputPath("parallel-stage-title.png") });
  const check = async (text: string) => {
    await expect(title.locator(".line-clamp-2")).toHaveText(text);
    expect(await geometry()).toEqual(original);
    await expect(card.locator('[data-slot="badge"], .lucide-loader-circle')).toHaveCount(0);
    await expect(title.locator(".lucide-chevron-down")).toBeVisible();
    await noOverflow(page);
  };
  state.stages!.assessment!.status = "running";
  state.stages!.verification!.status = "running";
  await page.clock.fastForward(5000);
  await check("Code review · Assessment · Fix verification · PR summary");
  await page.screenshot({ path: info.outputPath("all-active-stage-title.png") });
  state.stages!.ocr!.status = "completed";
  state.stages!.summary!.status = "completed";
  overview.summary_pending = true;
  await page.clock.fastForward(5000);
  await check("Assessment · Fix verification");
  state.stages!.verification!.status = "completed";
  await page.clock.fastForward(5000);
  await check("Assessment");
  await page.screenshot({ path: info.outputPath("assessment-with-summary-sync-queued.png") });
  for (const pending of [false, true]) {
    overview.summary_pending = pending;
    await page.clock.fastForward(5000);
    await check("Assessment");
  }
  overview.summary_error = "GitHub sync failed";
  await page.clock.fastForward(5000);
  await check("Assessment · Summary sync failed");
  overview.summary_error = null;
  state.status = "partial";
  await page.reload();
  await check("Assessment");
  state.stages!.assessment!.status = "completed";
  await page.reload();
  await check("Review complete with warnings");
  await expect(card).toHaveAttribute("data-status", "partial");
  await expect(card.locator(".lucide-pause")).toHaveCount(0);
  await expect(card.locator(".stage-divider")).toHaveText("Waiting to publish the summary to GitHub.");
  state.status = "running";
  await page.clock.fastForward(5000);
  await check("Finalizing review");
  await expect(card).toHaveAttribute("data-status", "running");
  await expect(card.locator(".lucide-pause")).toHaveCount(0);
  state.status = "completed";
  overview.summary_url = "https://github.com/acme/payments/pull/148#issuecomment-10";
  for (const pending of [true, false, true]) {
    overview.summary_pending = pending;
    await page.clock.fastForward(5000);
    await check("Review complete");
    await expect(card).toHaveAttribute("data-status", "completed");
    await expect(card.locator(".lucide-circle-check")).toBeVisible();
    await expect(card.locator(".lucide-pause")).toHaveCount(0);
    await expect(count).toHaveText("3 / 8 files");
    await expect(card.locator(".stage-divider")).toHaveText("Review findings and coverage below.");
  }
  await page.screenshot({ path: info.outputPath("completed-review-summary-sync-pending.png") });
  state.status = "partial";
  await page.reload();
  await check("Review complete with warnings");
  await expect(card.locator(".stage-divider")).toHaveText("Some review work could not be completed.");
  overview.summary_pending = false;
  overview.summary_error = "GitHub sync failed";
  await page.reload();
  await check("Summary sync failed");
  await title.click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("link", { name: "PR summary", exact: true })).toHaveAttribute("href", overview.summary_url);
  await expect(details.getByRole("button", { name: "Retry GitHub sync" })).toBeVisible();
  await page.keyboard.press("Escape");
  overview.latest_run = { ...run, id: "newer-run" };
  await page.reload();
  await check("Review complete with warnings");
  overview.latest_run = { ...run, id: "run" };
  overview.summary_error = null;
  overview.summary_pending = true;
  state.status = "completed";
  await page.reload();
  await check("Review complete");
  await expect(card.locator(".stage-divider")).toHaveText("Review findings and coverage below.");
});

test("review stage card keeps a fixed layout through every stage and terminal state", async ({ page }, info) => {
  const state: Partial<Run> = { status: "queued", report: null, progress: {}, publication: "pending",
    publication_error: null, check_enabled: false, config: { ...run.config, pipeline_version: 2 },
    summary: { status: "pending", tokens: 0, limited_context: false }, stages: {} };
  await mock(page, { run: state, assessment: { candidates: 7, pending: 4, incomplete: 0,
    new_findings: 2, repeated_observations: 1, rejected_candidates: 0, withheld_candidates: 0, failed: 0, skipped: 0, published_comments: 0 } });
  let height = 0;
  const steps: [string, string | null, string, string][] = [
    ["queued", null, "Waiting to start", "—"],
    ["running", "ocr", "Code review", "3 / 8 files"],
    ["running", "assessment", "Assessment", "3 / 7 candidates"],
    ["running", "verification", "Fix verification", "1 / 3 findings"],
    ["running", "summary", "PR summary", "—"],
    ["running", "waiting", "PR summary waiting", "—"],
    ["blocked", null, "Review paused", "3 / 8 files"],
    ["completed", null, "Review complete", "2 / 2 files"],
    ["partial", null, "Review complete with warnings", "3 / 8 files"],
    ["failed", null, "Review failed", "3 / 8 files"],
    ["cancelled", null, "Review cancelled", "3 / 8 files"],
    ["superseded", null, "Review superseded", "3 / 8 files"],
    ["skipped", null, "Review skipped", "3 / 8 files"],
  ];
  for (const [status, stage, title, count] of steps) {
    state.status = status;
    state.progress = status === "queued" ? {} : { selected: 8, completed: 3, verification: { selected: 3, completed: 1 } };
    state.stages = Object.fromEntries(["ocr", "assessment", "verification", "summary"].map((key) => [key,
      { status: key === stage ? "running" : stage === "waiting" && key === "summary" ? "waiting" : "completed", tokens: 0 }]));
    state.report = status === "completed" ? { manifest: { coverage: { selected: [{ path: "one.ts" }, { path: "two.ts" }],
      completed: [{ path: "one.ts" }], reused: [{ path: "two.ts" }], failed: [], waived: [] } } } : null;
    await page.goto("/runs/run");
    const card = page.getByRole("region", { name: "Review stages", exact: true });
    await expect(card.getByRole("button", { name: "Review stage details", exact: true })).toContainText(title);
    await expect(card.getByLabel(count === "—" ? "Stage progress unavailable" : "Review progress count", { exact: true })).toHaveText(count);
    await expect(card.locator('[data-slot="badge"], .lucide-loader-circle')).toHaveCount(0);
    await expect(card.locator(".lucide-chevron-down")).toBeVisible();
    const box = (await card.boundingBox())!;
    if (!height) height = box.height;
    expect(box.height).toBe(height);
    await noOverflow(page);
    if (["running", "completed", "partial"].includes(status) && (!stage || stage === "ocr")) {
      await page.screenshot({ path: `/private/tmp/review-stage-card-${status}-${info.project.name}.png` });
    }
  }
  state.status = "running";
  state.stages = {};
  state.report = null;
  state.progress = { selected: -1, completed: 5, phase: "__proto__" };
  await page.goto("/runs/run");
  await expect(page.getByRole("region", { name: "Review stages", exact: true }).getByLabel("Stage progress unavailable")).toHaveText("—");
});

test("assessment progress counts candidates while polling instead of completed review files", async ({ page }, info) => {
  if (info.project.name === "mobile") await page.setViewportSize({ width: 320, height: 844 });
  await page.clock.install();
  const state: Partial<Run> = { status: "running", report: null, progress: { selected: 45, completed: 45 },
    stages: { ocr: { status: "completed", tokens: 100 }, assessment: { status: "running", tokens: 0 },
      verification: { status: "completed", tokens: 20 }, summary: { status: "completed", tokens: 10 } } };
  const assessment: Schema["AssessmentSummary"] = { candidates: 7, pending: 7, incomplete: 0, new_findings: 0, repeated_observations: 0,
    rejected_candidates: 0, withheld_candidates: 0, failed: 0, skipped: 0, published_comments: 0 };
  const options = { run: state, assessment: assessment as Schema["AssessmentSummary"] | null };
  await mock(page, options);
  await page.goto("/runs/run?tab=assessment");
  const card = page.getByRole("region", { name: "Review stages", exact: true });
  const count = card.getByLabel(/^(Review progress count|Stage progress unavailable)$/);
  await expect(count).toHaveText("0 / 7 candidates");
  const geometry = () => card.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    const tabs = document.querySelector('[role="tablist"][aria-label="Review run views"]')!.getBoundingClientRect();
    return { height: bounds.height, tabs: tabs.top - bounds.top };
  });
  const original = await geometry();
  assessment.progress = { completed: 1, failed: 0, skipped: 0 };
  await page.clock.fastForward(5000);
  await expect(count).toHaveText("1 / 7 candidates");
  assessment.progress = { completed: 1, failed: 1, skipped: 1 };
  await page.clock.fastForward(5000);
  await expect(count).toHaveText("1 / 7 candidates");
  await expect(card.getByRole("status")).toContainText("1 failed · 1 skipped");
  const caption = card.locator("p");
  await expect(caption).toHaveText("Assessment: 1 failed · 1 skipped.");
  expect(await caption.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
  await card.getByRole("button", { name: "Review stage details" }).click();
  await expect(page.getByRole("dialog", { name: "Review stage details" }).getByText("1 failed · 1 skipped", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  assessment.progress = null;
  assessment.failed = assessment.skipped = 1;
  for (const pending of [4, 0]) {
    assessment.pending = pending;
    assessment.rejected_candidates = 7 - pending - assessment.failed - assessment.skipped;
    await page.clock.fastForward(5000);
    await expect(count).toHaveText(`${7 - pending - assessment.failed - assessment.skipped} / 7 candidates`);
    await expect(page.getByRole("tab", { name: "Assessment 7", exact: true })).toBeVisible();
    expect(await geometry()).toEqual(original);
    if (pending === 4) await page.screenshot({ path: info.outputPath("assessment-candidate-progress.png") });
  }
  for (const pending of [8, -1, 0.5]) {
    assessment.pending = pending;
    await page.clock.fastForward(5000);
    await expect(count).toHaveText("—");
    expect(await geometry()).toEqual(original);
  }
  options.assessment = null;
  await page.clock.fastForward(5000);
  await expect(count).toHaveText("—");
  await expect(card).not.toContainText("45 / 45 files");
  await noOverflow(page);
});

for (const [ocr, assessment, verification, expected] of [
  ["completed", "completed", "completed", "Completed"],
  ["completed", "completed", "failed", "Partial"],
  ["completed", "partial", "skipped", "Partial"],
  ["failed", "failed", "failed", "Failed"],
  ["failed", "skipped", "skipped", "Failed"],
  ["failed", "cancelled", "skipped", "Failed"],
  ["completed", "completed", "skipped", "Completed"],
  ["skipped", "skipped", "skipped", "Skipped"],
  ["completed", "running", "failed", "Running"],
  ["completed", "pending", "failed", "Pending"],
  ["completed", "waiting", "failed", "Waiting"],
  ["cancelled", "cancelled", "skipped", "Cancelled"],
  ["completed", "cancelled", "skipped", "Partial"],
] as const) {
  test(`review stage summary is ${expected} for ${ocr}/${assessment}/${verification}`, async ({ page }, info) => {
    await mock(page, { run: {
      status: "completed", stages: {
        ocr: { status: ocr, tokens: 100 }, assessment: { status: assessment, tokens: 20 },
        verification: { status: verification, tokens: 10 }, summary: { status: "skipped", tokens: 0 },
      },
      summary: { status: "skipped", tokens: 0, limited_context: false },
    } });
    await page.goto("/runs/run");
    const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
    await expect(trigger).toHaveAttribute("data-status", expected.toLowerCase());
    await trigger.click();
    const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
    await expect(details.getByText("Analysis", { exact: true }).locator("..")).toContainText(ocr);
    await expect(details.getByText("Fix verification", { exact: true }).locator("..")).toContainText(verification);
    await noOverflow(page);
    if (expected === "Partial" && verification === "failed" || expected === "Failed" && assessment === "failed") {
      await page.screenshot({ path: `/private/tmp/review-stages-${expected.toLowerCase()}-${info.project.name}.png` });
    }
  });
}

for (const [summary, expected] of [["completed", "Completed"], ["running", "Running"], ["waiting", "Waiting"],
  ["pending", "Pending"], ["failed", "Partial"]] as const) {
  test(`review stages include PR summary ${summary} in the combined status`, async ({ page }) => {
    await mock(page, { run: { status: "completed", stages: {
      summary: { status: summary, tokens: 0 }, ocr: { status: "completed", tokens: 100 },
      assessment: { status: "completed", tokens: 20 }, verification: { status: "skipped", tokens: 0 },
    } } });
    await page.goto("/runs/run");
    const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
    await expect(trigger).toHaveAttribute("data-status", expected.toLowerCase());
    await trigger.click();
    const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
    await expect(details.locator(".font-medium").first()).toHaveText("PR summary");
    await expect(details.getByText("PR summary", { exact: true }).locator("..")).toContainText(summary);
  });
}

for (const [summary, expected] of [["completed", "Completed"], ["running", "Running"],
  ["failed", "Partial"], ["skipped", "Completed"]] as const) {
  test(`legacy summary status overrides its pending orchestration stage: ${summary}`, async ({ page }) => {
    await mock(page, { run: {
      status: summary === "running" ? "running" : "completed",
      summary: { status: summary, tokens: 0, limited_context: false },
      stages: {
        summary: { status: "pending", tokens: 0 }, ocr: { status: "completed", tokens: 100 },
        assessment: { status: "completed", tokens: 20 }, verification: { status: "skipped", tokens: 0 },
      },
    } });
    await page.goto("/runs/run");
    const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
    await expect(trigger).toHaveAttribute("data-status", expected.toLowerCase());
    await trigger.click();
    const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
    await expect(details.getByText("PR summary", { exact: true }).locator("..")).toContainText(summary);
  });
}

for (const [summary, expected] of [["waiting", "Waiting"], ["running", "Running"],
  ["completed", "Running"], ["failed", "Partial"], ["skipped", "Running"]] as const) {
  test(`pipeline summary stage overrides its pending generation status: ${summary}`, async ({ page }) => {
    await mock(page, { run: {
      status: "running", config: { ...run.config, pipeline_version: 2 },
      summary: { status: "pending", tokens: 0, limited_context: false },
      stages: {
        summary: { status: summary, tokens: 0 }, ocr: { status: "completed", tokens: 100 },
        assessment: { status: "completed", tokens: 20 }, verification: { status: "skipped", tokens: 0 },
      },
    } });
    await page.goto("/runs/run");
    const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
    await expect(trigger).toHaveAttribute("data-status", expected.toLowerCase());
    await trigger.click();
    const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
    await expect(details.getByText("PR summary", { exact: true }).locator("..")).toContainText(summary);
  });
}

test("skipped fix verification shows its saved reason in stage details", async ({ page }, info) => {
  const reason = "Fix verification was skipped because the pull request or finding changed, or automatic resolution was disabled. Check the latest finding status and repository settings.";
  await mock(page, { run: { status: "running", publication: "pending", publication_error: null, stages: {
    ocr: { status: "running", tokens: null }, assessment: { status: "pending", tokens: 0 },
    verification: { status: "skipped", tokens: 0, error: reason },
  } } });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByText("Fix verification", { exact: true }).locator("..")).toContainText("skipped");
  await details.locator("summary", { hasText: "Technical details" }).click();
  await expect(details.getByRole("region", { name: "Fix verification technical details", exact: true })).toHaveText(reason);
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/verification-skip-reason-${info.project.name}.png` });
});

test("parallel stage details preserve an open popover while the run refreshes", async ({ page }, info) => {
  await mock(page, { run: {
    status: "running", publication: "pending", publication_error: null,
    stages: {
      ocr: { status: "running", tokens: null },
      assessment: { status: "pending", tokens: 0 },
      verification: { status: "completed", tokens: 1200 },
      summary: { status: "completed", tokens: 300 },
    },
    summary: { status: "completed", tokens: 300, limited_context: false },
  } });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details).toContainText("Token usage pending");
  await page.waitForTimeout(5500);
  await expect(details).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `/tmp/parallel-stage-details-${info.project.name}.png`, fullPage: true });
});

for (const withStages of [true, false]) {
  test(`assessment stays Running until processing finishes (${withStages ? "stages" : "legacy"})`, async ({ page }, info) => {
    const assessment = { candidates: 3, new_findings: 0, repeated_observations: 0, rejected_candidates: 0,
      withheld_candidates: 1, pending: 2, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 };
    const currentRun: Partial<Run> = { status: "running", publication: "pending", publication_error: null,
      stages: withStages ? {
        ocr: { status: "completed", tokens: 100 }, assessment: { status: "partial", tokens: 20 },
        verification: { status: "completed", tokens: 10 },
      } : undefined };
    await mock(page, { run: currentRun, assessment });
    await page.clock.install();
    await page.goto("/runs/run");
    if (withStages) await page.getByRole("button", { name: "Review stage details", exact: true }).click();
    const scope = withStages ? page.getByRole("dialog", { name: "Review stage details", exact: true })
      : page.getByRole("region", { name: "GitHub publication", exact: true });
    const row = scope.getByText("Assessment", { exact: true }).locator("..");
    await expect(row.getByText(/^running$/i)).toBeVisible();
    await expect(row.getByText(/^partial$/i)).toHaveCount(0);
    assessment.pending = 1;
    assessment.incomplete = assessment.withheld_candidates = 2;
    await page.clock.runFor(5500);
    await expect(row.getByText(/^running$/i)).toBeVisible();
    await page.screenshot({ path: `/tmp/assessment-running-${withStages ? "stages" : "legacy"}-${info.project.name}.png`, fullPage: true });
    if (currentRun.stages?.assessment) {
      currentRun.stages.assessment.status = "running";
      assessment.pending = 0;
      await page.clock.runFor(5500);
      await expect(row.getByText(/^running$/i)).toBeVisible();
      currentRun.stages.assessment.status = "partial";
      currentRun.stages.assessment.finished_at = at;
    }
    assessment.pending = 0;
    await page.clock.runFor(5500);
    await expect(row.getByText(/^partial$/i)).toBeVisible();
    await expect(row.getByText(/^running$/i)).toHaveCount(0);
    await noOverflow(page);
  });
}

test("stage copy buttons identify and copy their own diagnostics", async ({ page }, info) => {
  await mock(page, { run: { status: "failed", publication: "not_applicable", publication_error: null, stages: {
    ocr: { status: "failed", tokens: 0, error: "OCR transcript" },
    assessment: { status: "failed", tokens: 0, error: "Assessment transcript" },
    verification: { status: "failed", tokens: 0, error: "Verification transcript" },
  } } });
  await page.goto("/runs/run");
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: (value: string) => new Promise<void>((resolve) => {
        (window as Window & { finishCopy?: () => void }).finishCopy = () => {
          document.body.dataset.copiedStage = value;
          resolve();
        };
      }),
    } });
  });
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  for (const [name, transcript] of [["Analysis", "The retired engine transcript"], ["Assessment", "Assessment transcript"], ["Fix verification", "Verification transcript"]]) {
    const raw = page.getByRole("region", { name: `${name} technical details`, exact: true, includeHidden: true });
    await details.locator("details", { has: raw }).locator("summary").click();
    const copy = details.getByRole("button", { name: `Copy ${name} details`, exact: true });
    await expect(copy).toHaveAttribute("title", `Copy ${name} details`);
    await copy.focus();
    await page.keyboard.press("Enter");
    const copying = details.getByRole("button", { name: `Copying ${name} details`, exact: true });
    await expect(copying).toBeDisabled();
    await expect(copying).toHaveAttribute("title", `Copying ${name} details`);
    await page.evaluate(() => (window as Window & { finishCopy?: () => void }).finishCopy!());
    await expect(page.locator("body")).toHaveAttribute("data-copied-stage", transcript);
    await expect(copy).toBeEnabled();
    await expect(copy).toHaveAttribute("title", `Copy ${name} details`);
  }
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/pr26-stage-copy-labels-${info.project.name}.png` });
});

for (const [key, name] of [["ocr", "Analysis"], ["assessment", "Assessment"], ["verification", "Fix verification"]] as const) {
test(`${name} stage failure keeps only collapsed technical details`, async ({ page }, info) => {
  const error = "The fix could not be verified automatically. The AI did not finish checking this finding. Technical details: "
    + JSON.stringify({ exit_code: 0, status: "completed_with_warnings", summary: { total_tokens: 0, budget_exceeded: true },
      warnings: [{ message: "stopped in batch #0: used 0 tokens + next-file estimate exceeds budget 200000", type: "token_budget_reached" }],
      result_error: "The AI did not return exactly one final assessment.", diagnostics: "Saved diagnostics. ".repeat(300) });
  await mock(page, { run: { status: "cancelled", publication: "not_applicable", publication_error: null, stages: {
    ocr: { status: "cancelled", tokens: null }, assessment: { status: "cancelled", tokens: 0 },
    verification: { status: "cancelled", tokens: 0 }, summary: { status: "completed", tokens: 300 },
    [key]: { status: "failed", tokens: 0, error },
  } } });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("alert")).toHaveCount(0);
  const raw = details.getByRole("region", { name: `${name} technical details`, includeHidden: true });
  await expect(raw).toBeHidden();
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/stage-${key}-technical-details-${info.project.name}.png` });
  const disclosure = details.locator("summary", { hasText: "Technical details" });
  await expect(disclosure).toHaveCSS("font-size", "12px");
  await disclosure.focus();
  await disclosure.press("Enter");
  await expect(raw).toBeVisible();
  expect(await raw.textContent()).toBe(error);
  const copy = details.getByRole("button", { name: `Copy ${name} details`, exact: true });
  await expect(copy).toHaveAttribute("title", `Copy ${name} details`);
  if (info.project.name === "desktop") {
    await page.mouse.move(0, 0);
    await expect(copy).toHaveCSS("opacity", "0");
    await raw.hover();
    await expect(copy).toHaveCSS("opacity", "1");
    await page.mouse.move(0, 0);
    await raw.focus();
  }
  await expect(copy).toHaveCSS("opacity", "1");
  // Measure both elements in one frame while the expanded dialog may still reposition.
  const { textBounds, copyBounds } = await raw.evaluate((element) => ({
    textBounds: element.getBoundingClientRect().toJSON(),
    copyBounds: element.parentElement!.querySelector("button")!.getBoundingClientRect().toJSON(),
  }));
  expect(copyBounds!.x).toBeGreaterThanOrEqual(textBounds!.x);
  expect(copyBounds!.y).toBeGreaterThanOrEqual(textBounds!.y);
  expect(copyBounds!.x + copyBounds!.width).toBeLessThanOrEqual(textBounds!.x + textBounds!.width);
  expect(copyBounds!.y + copyBounds!.height).toBeLessThanOrEqual(textBounds!.y + textBounds!.height);
  await page.screenshot({ path: `/private/tmp/stage-${key}-technical-details-expanded-${info.project.name}.png` });
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async (value: string) => { document.body.dataset.copiedStage = value; },
    } });
  });
  await copy.click();
  await expect(page.locator("body")).toHaveAttribute("data-copied-stage", error);
  await noOverflow(page);
});
}

test("PR summary comes first inside review stages and retries synchronization without starting a review", async ({ page }, info) => {
  const requests = await mock(page, {
    run: { summary: { status: "completed", tokens: 1234, limited_context: true }, stages: {
      summary: { status: "completed", tokens: 1234 }, ocr: { status: "completed", tokens: 100 },
      assessment: { status: "completed", tokens: 20 }, verification: { status: "skipped", tokens: 0 },
    } },
    overview: { summary_url: "https://github.com/acme/payments/pull/148#issuecomment-10", summary_pending: true, summary_error: "GitHub rate limit reached." },
  });
  await page.goto("/runs/run");
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(publication.getByText("PR summary", { exact: true })).toHaveCount(0);
  await expect(publication.getByRole("button", { name: /^PR summary details:/ })).toHaveCount(0);
  const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
  await expect(trigger).toBeVisible();
  await expect(page.getByText("1,234 tokens", { exact: true })).not.toBeVisible();
  await expect(page.getByText(/The overview follows the latest review/)).toHaveCount(0);
  if (info.project.name === "desktop") {
    const comments = await publication.getByText("GitHub comments", { exact: true }).boundingBox();
    const stages = await page.getByRole("region", { name: "Review stages", exact: true }).boundingBox();
    expect(stages!.y).toBeGreaterThan(comments!.y);
  }
  await noOverflow(page);
  await page.screenshot({ path: `/tmp/pr-summary-run-${info.project.name}.png`, fullPage: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByText(/^(PR summary|Analysis|Assessment|Fix verification)$/)).toHaveText(["PR summary", "Analysis", "Assessment", "Fix verification"]);
  await expect(details.getByText("1,234 tokens", { exact: true })).toBeVisible();
  const summaryLink = details.getByRole("link", { name: "PR summary", exact: true });
  await expect(summaryLink).toHaveAttribute("href", /#issuecomment-10$/);
  await expect(summaryLink).toHaveAttribute("target", "_blank");
  await expect(summaryLink.locator("svg")).toHaveCount(1);
  await expect(summaryLink).toHaveCSS("text-decoration-line", "none");
  await expect(details.getByText("View PR overview", { exact: true })).toHaveCount(0);
  await expect(details.getByText(/^GitHub:/)).toHaveCount(0);
  await expect(details.getByRole("alert")).toHaveCount(0);
  await expect(details.getByRole("region", { name: "Summary technical details", includeHidden: true })).toBeHidden();
  await details.locator("summary", { hasText: "Technical details" }).click();
  await expect(details.getByRole("region", { name: "Summary technical details" })).toHaveText("GitHub rate limit reached.");
  await noOverflow(page);
  await page.screenshot({ path: `/tmp/pr-summary-details-${info.project.name}.png`, fullPage: true });
  await expect(details.getByRole("button", { name: "Retry summary generation" })).toHaveCount(0);
  await details.getByRole("button", { name: "Retry GitHub sync" }).click();
  expect(requests.map((request) => request.path)).toEqual(["/pulls/pr/summary"]);
  await page.keyboard.press("Escape");
  await expect(details).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test("review stage card keeps live details without a duplicate header badge", async ({ page }, info) => {
  await page.clock.install();
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const state: Partial<Run> = { status: "completed", publication: "published", publication_error: null,
    check_status: "completed", check_conclusion: "success", check_pending: false, check_error: null,
    stages: { summary: { status: "completed", tokens: 6449 }, ocr: { status: "completed", tokens: 133071 },
      assessment: { status: "completed", tokens: 0 }, verification: { status: "skipped", tokens: 0 } } };
  await mock(page, { run: state, overview: { summary_url: "https://github.com/acme/payments/pull/148#issuecomment-10" } });
  await page.goto("/runs/run");
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(publication.getByText("Review stages", { exact: true })).toHaveCount(0);
  const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
  await expect(trigger).toHaveAttribute("data-status", "completed");
  await expect(trigger.locator(".lucide-chevron-down")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Review stages", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 3");
  await trigger.focus();
  await trigger.press("Enter");
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByText(/^(PR summary|Analysis|Assessment|Fix verification)$/)).toHaveText(["PR summary", "Analysis", "Assessment", "Fix verification"]);
  await expect(details.getByText(/tokens$/)).toHaveText(["6,449 tokens", "133,071 tokens", "0 tokens", "0 tokens"]);
  await expect(details.locator('[data-slot="badge"]')).toHaveText(["completed", "completed", "completed", "skipped"]);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("review-stage-card-details.png") });
  await page.keyboard.press("Escape");
  await expect(details).toBeHidden();
  await expect(trigger).toBeFocused();
  state.status = "running";
  state.stages!.assessment!.status = "running";
  await page.clock.fastForward(5000);
  await expect(trigger).toHaveAttribute("data-status", "running");
  await trigger.click();
  await expect(details.getByText("Assessment", { exact: true }).locator("..")).toContainText("running");
  await page.keyboard.press("Escape");
});

test("run status tooltips stay concise and PR summary failure preserves read-only details", async ({ page }, info) => {
  await mock(page, { canWrite: false, run: {
    status: "completed", publication: "no_new_comments", publication_error: null,
    check_status: "completed", check_conclusion: "success", check_pending: false, check_error: null,
    stages: { ocr: { status: "completed", tokens: 10 }, assessment: { status: "completed", tokens: 5 }, verification: { status: "skipped", tokens: 0 } },
    summary: { status: "failed", tokens: null, limited_context: false, error: "Summary generation was interrupted." },
  } });
  await page.goto("/runs/run");
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  const stages = page.getByRole("region", { name: "Review stages", exact: true }).getByRole("button", { name: "Review stage details", exact: true });
  const tooltips = [
    [publication.getByText("No new comments", { exact: true }).locator(".."), "No additional GitHub comments were needed."],
    [publication.getByRole("link", { name: "View check on GitHub: Success", exact: true }), "Findings are informational, not approval to merge."],
  ] as const;
  for (const [trigger, text] of tooltips) {
    if (info.project.name === "desktop") {
      await trigger.hover();
      await expect(page.getByRole("tooltip")).toHaveText(text);
      await page.mouse.move(0, 0);
    }
    await trigger.focus();
    await expect(page.getByRole("tooltip")).toHaveText(text);
    await noOverflow(page);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
  }
  const card = page.getByRole("region", { name: "Review stages", exact: true });
  const height = (await card.boundingBox())!.height;
  await expect(stages).toHaveAttribute("aria-expanded", "false");
  await stages.locator(".lucide-chevron-down").click();
  const stageDetails = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(stages).toHaveAttribute("aria-expanded", "true");
  expect((await card.boundingBox())!.height).toBe(height);
  await page.screenshot({ path: info.outputPath("review-stage-details-chevron.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(stages).toBeFocused();
  await stages.press("Enter");
  await expect(stageDetails.getByText("Analysis", { exact: true })).toBeVisible();
  const details = stageDetails;
  await expect(details.getByText("Token usage unavailable", { exact: true })).toBeVisible();
  await expect(details.getByRole("alert")).toHaveCount(0);
  await details.locator("summary", { hasText: "Technical details" }).click();
  await expect(details.getByRole("region", { name: "Summary technical details" })).toHaveText("Summary generation was interrupted.");
  await expect(details.getByRole("button", { name: /Retry (summary generation|GitHub sync)/ })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(details).not.toBeVisible();
  await expect(stages).toBeFocused();
});

test("PR summary errors and warnings stay in copyable technical details", async ({ page }, info) => {
  const errors = ["Summary generation failed: " + "diagnostic ".repeat(500), "Diagram validation failed.", "GitHub synchronization failed."];
  await mock(page, { run: { status: "completed", summary: {
    status: "failed", tokens: 100, limited_context: false, error: errors[0], diagram_warning: errors[1],
  } }, overview: { summary_error: errors[2] } });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("alert")).toHaveCount(0);
  const raw = details.getByRole("region", { name: "Summary technical details", includeHidden: true });
  await expect(raw).toBeHidden();
  await page.screenshot({ path: `/private/tmp/summary-technical-details-${info.project.name}.png` });
  const disclosure = details.locator("summary", { hasText: "Technical details" });
  await expect(disclosure).toHaveCSS("font-size", "12px");
  await disclosure.focus();
  await disclosure.press("Enter");
  await expect(raw).toBeVisible();
  expect(await raw.textContent()).toBe(errors.join("\n\n"));
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: async (value: string) => { document.body.dataset.copiedSummary = value; },
    } });
  });
  await details.getByRole("button", { name: "Copy Summary details", exact: true }).click();
  await expect(page.locator("body")).toHaveAttribute("data-copied-summary", errors.join("\n\n"));
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/summary-technical-details-expanded-${info.project.name}.png` });
});

test("failed PR summary retries generation separately and removes recovery after success", async ({ page }) => {
  const summary: NonNullable<Run["summary"]> = { status: "failed", tokens: 0, limited_context: false, error: "Provider rejected the summary request." };
  const requests = await mock(page, { run: { engine: "mortis", status: "completed", summary }, overview: { summary_error: null } });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("button", { name: "Retry GitHub sync" })).toHaveCount(0);
  summary.status = "pending";
  summary.error = null;
  await details.getByRole("button", { name: "Retry summary generation" }).click();
  expect(requests.map((request) => request.path)).toEqual(["/runs/run/summary/retry"]);
  await expect(details.getByRole("button", { name: /Retry/ })).toHaveCount(0);
  summary.status = "completed";
  await page.clock.install();
  await page.clock.runFor(5500);
  await expect(details.getByRole("button", { name: /Retry/ })).toHaveCount(0);
});

test("PR summary stays mounted and still while polling GitHub sync state", async ({ page }) => {
  await page.clock.install();
  const overview = { summary_url: "https://github.com/acme/payments/pull/148#issuecomment-10", summary_pending: false, summary_error: null };
  await mock(page, { run: { status: "completed", summary: { status: "failed", tokens: 0, limited_context: false, error: "Summary request failed." } }, overview });
  await page.goto("/runs/run");
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await page.clock.runFor(500);
  await details.click({ trial: true });
  const original = await details.elementHandle();
  const focused = await page.locator(":focus").elementHandle();
  const bounds = await details.boundingBox();
  for (const pending of [true, false, true, false]) {
    overview.summary_pending = pending;
    await page.clock.runFor(5500);
    await expect(details.getByRole("link", { name: "PR summary", exact: true })).toHaveAttribute("href", overview.summary_url);
    await expect(details.getByText(/^GitHub:/)).toHaveCount(0);
    expect(await original!.evaluate((element) => element.isConnected)).toBe(true);
    expect(await details.boundingBox()).toEqual(bounds);
    expect(await focused!.evaluate((element) => element === document.activeElement)).toBe(true);
  }
  await noOverflow(page);
});

test("repository settings exposes the rules draft, run limits, and unsaved state", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings");
  const form = page.getByRole("form", { name: "Repository settings" });
  const save = form.getByRole("button", { name: "Save", exact: true });
  const unsaved = form.getByText("Unsaved changes", { exact: true });
  await expect(save).toBeInViewport();
  await expect(unsaved).toHaveCount(0);

  const jump = form.getByRole("link", { name: "Jump to run limits" });
  await jump.press("Enter");
  const limits = form.getByRole("heading", { name: "Run limits", exact: true });
  await expect(limits).toBeFocused();
  expect((await limits.boundingBox())!.y).toBeGreaterThanOrEqual((await page.getByRole("banner").boundingBox())!.height);
  expect(requests).toEqual([]);

  const time = form.getByLabel("Time limit (seconds)", { exact: true });
  await time.fill("");
  await expect(unsaved).toBeVisible();
  await time.fill("900");
  await expect(unsaved).toHaveCount(0);

  const disclosure = form.getByText("Custom review rules", { exact: true });
  const rules = form.getByRole("textbox", { name: "Custom review rules (JSON)" });
  await expect(rules).toBeHidden();
  await disclosure.click();
  await rules.fill("{");
  await disclosure.click();
  await repositorySection(page, "Security scans");
  await expect(unsaved).toBeVisible();
  await save.click();
  await expect(rules).toBeFocused();
  await expect(rules).toHaveValue("{");
  await expect(form.getByRole("alert")).toContainText("Rules must contain valid JSON");
  expect(requests).toEqual([]);

  await rules.fill('{"rules":[]}');
  await save.click();
  await expect(page.getByText("Repository settings saved.", { exact: true })).toBeVisible();
  await expect(unsaved).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ path: "/repositories/22", body: { settings: { rules: { rules: [] } } } });

  await rules.fill("");
  await save.click();
  await expect(rules).toBeHidden();
  await expect(unsaved).toHaveCount(0);
  expect(requests[1]).toMatchObject({ path: "/repositories/22", body: { settings: { rules: null } } });
  await disclosure.click();
  await expect(rules).toBeEditable();
});

test("repository settings keeps later edits marked unsaved after an in-flight save", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  const settings: Schema["RepositoryOut"]["settings"][] = [];
  await page.route("**/api/v1/repositories/22", async (route) => {
    settings.push(route.request().postDataJSON().settings);
    await pending;
    await route.fulfill({ json: { ...repo, settings: settings.at(-1) } });
  });
  await page.goto("/repositories/22/settings");
  const form = page.getByRole("form", { name: "Repository settings" });
  const bots = form.getByRole("switch", { name: "Include bot pull requests" });
  const save = form.getByRole("button", { name: "Save", exact: true });
  const unsaved = form.getByText("Unsaved changes", { exact: true });
  await bots.check();
  await save.click();
  await expect(form.getByRole("button", { name: "Saving…" })).toBeDisabled();
  await bots.uncheck();
  await form.getByText("Custom review rules", { exact: true }).click();
  const rules = form.getByRole("textbox", { name: "Custom review rules (JSON)" });
  await rules.fill('{"rules":[]}');
  release();
  await expect(save).toBeEnabled();
  await expect(unsaved).toBeVisible();
  await expect(rules).toHaveValue('{"rules":[]}');
  await save.click();
  await expect(unsaved).toHaveCount(0);
  expect(settings.map((draft) => draft.include_bot_prs)).toEqual([true, false]);
  expect(settings.map((draft) => draft.rules)).toEqual([null, { rules: [] }]);
});

test("repository configuration validates hidden fields and retains drafts after a failed save", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?tab=limits");
  await page.getByLabel("Time limit (seconds)", { exact: true }).fill("");
  await repositorySection(page, "Project context");
  await page.getByRole("textbox", { name: "Project context", exact: true }).fill("Keep this draft after validation and server errors.");
  await expect(page.getByLabel("Time limit (seconds)", { exact: true })).not.toBeVisible();
  expect(await page.getByLabel("Time limit (seconds)", { exact: true }).evaluate((element: HTMLInputElement) => element.willValidate)).toBe(true);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByLabel("Time limit (seconds)", { exact: true })).toBeFocused();
  expect(requests).toEqual([]);
  await page.getByLabel("Time limit (seconds)", { exact: true }).fill("1200");
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByRole("combobox", { name: "Default AI connection", exact: true }), "No connection");
  await repositorySection(page, "Pull request reviews");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Default AI connection", exact: true })).toBeFocused();
  expect(requests).toEqual([]);
  await chooseOption(page, page.getByRole("combobox", { name: "Default AI connection", exact: true }), "Team reviews");
  await page.route("**/api/v1/repositories/22", (route) => route.fulfill({ status: 422, json: { detail: "Repository settings could not be saved. Try again." } }));
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository settings could not be saved");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Keep this draft after validation and server errors.");
  await repositorySection(page, "Pull request reviews");
  await expect(page.getByLabel("Time limit (seconds)", { exact: true })).toHaveValue("1200");
});

test("repository configuration shows connection failures across every section", async ({ page }, info) => {
  const requests = await mock(page);
  const error = "AI connections could not be loaded. Try again.";
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ status: 503, json: { detail: error } }));
  await page.goto("/repositories/22/settings");
  const form = page.getByRole("form", { name: "Repository settings" });
  const notice = form.getByRole("alert").filter({ hasText: error });
  for (const section of ["Pull request reviews", "Security scans", "AI defaults", "Project context", "Code graph"]) {
    await repositorySection(page, section);
    await expect(notice).toBeVisible();
    await expect(notice).toHaveCount(1);
  }
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByRole("combobox", { name: "Default AI connection", exact: true }), "No connection");
  await repositorySection(page, "Pull request reviews");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Default AI connection", exact: true })).toBeFocused();
  await expect(notice).toBeVisible();
  await expect(form.getByRole("alert")).toHaveCount(2);
  const errors = form.getByRole("region", { name: "Settings errors" });
  await expect(errors).toHaveCSS("overflow-y", "auto");
  await expect(errors).toHaveAttribute("tabindex", "0");
  expect(requests).toEqual([]);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/repository-settings-connection-error-${info.project.name}.png`, fullPage: true, animations: "disabled" });
});

test("repository configuration sections retain navigation and fit both themes", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings?q=148&state=all&tab=connection");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Repository settings");
  await expect(breadcrumb.getByRole("link", { name: "payments", exact: true })).toHaveAttribute("href", "/repositories/22/overview");
  await openNavigation(page);
  const repository = page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true });
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Repository settings", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(repository).toHaveAttribute("href", "/repositories/22?q=148&state=all");
  await closeNavigation(page);
  const sections = page.getByRole("navigation", { name: "Repository settings sections" });
  const names = ["Pull request reviews", "Security scans", "AI defaults", "Project context", "Code graph"];
  await expect(sections.getByRole("link")).toHaveText(names);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    for (const name of names) {
      const link = sections.getByRole("link", { name, exact: true });
      await expect(link.locator("svg[aria-hidden=true]")).toBeVisible();
      await link.focus();
      await link.press("Enter");
      await expect(link).toHaveAttribute("aria-current", "page");
      const footer = page.getByRole("form", { name: "Repository settings" }).locator("footer");
      await expect(footer).toHaveCSS("border-top-width", "0px");
      await expect(footer.getByRole("button")).toHaveText(["Save"]);
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/repository-settings-${name.toLowerCase().replaceAll(" ", "-").replace("&-", "")}-${info.project.name}-${theme}.png`, fullPage: true });
    }
  }
  await repositorySection(page, "Security scans");
  await page.reload();
  await expect(page).toHaveURL(/tab=scan-limits$/);
  await expect(page.getByLabel("Parallel scan tasks", { exact: true })).toBeVisible();
  await (await repositoryPullsLink(page)).click();
  await expect(page).toHaveURL(/repositories\/22\?q=148&state=all$/);
  expect(requests).toEqual([]);
});

test("page tabs retain themed text, underlines and keyboard selection", async ({ page }, info) => {
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
        const surface = composite(getComputedStyle(document.body).backgroundColor, list);
        const background = composite(surface, style.backgroundColor);
        return { list: surface, background, count: composite(background, getComputedStyle(tab.querySelector("span")!).color), underline: getComputedStyle(tab, "::after").opacity };
      });
      expect(rendered.background).toBe(theme[mode]["--background"]);
      expect(rendered.underline).toBe("1");
      expect(contrast(rendered.count, rendered.background), `${theme.label} ${mode} count`).toBeGreaterThanOrEqual(4.5);
      const tabs = [findings, page.getByRole("tab", { name: /^Resolved/ }), page.getByRole("tab", { name: /^Dismissed/ }), history];
      for (let index = 1; index < tabs.length; index++) {
        await tabs[index - 1].press("ArrowRight");
        await expect(tabs[index]).toHaveAttribute("aria-selected", "true");
      }
      for (let index = tabs.length - 2; index >= 0; index--) {
        await tabs[index + 1].press("ArrowLeft");
        await expect(tabs[index]).toHaveAttribute("aria-selected", "true");
      }
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
      await expect(page.getByRole("form", { name: "Repository settings" })).toHaveCount(0);
    }
    for (const [id, repositories] of [[999, [repo]], [22, []]] as const) {
      await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: repositories }));
      await page.goto(`/repositories/${id}/settings?state=all&tab=limits`);
      await expect(page).toHaveURL(new RegExp(`/repositories/${id}\\?state=all$`));
      await expect(page.getByRole("heading", { name: "Repository unavailable" })).toBeVisible();
      await expect(page.getByRole("form", { name: "Repository settings" })).toHaveCount(0);
    }
    await page.route("**/api/v1/repositories", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
    await page.goto("/repositories/22/settings?tab=limits");
    await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository access was revoked.");
    await expect(page.getByRole("form", { name: "Repository settings" })).toHaveCount(0);
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
  await chooseRepository(page, other.full_name);
  const sync = page.getByRole("button", { name: "Sync", exact: true });
  await expect(sync).toBeEnabled();
  await expect(sync.locator("svg")).not.toHaveClass(/animate-spin/);
  await openNavigation(page);
  await chooseRepository(page, repo.full_name);
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
  await expect(page.getByRole("link", { name: /Add retry handling/ }).locator("xpath=ancestor::*[@data-pull-row]")).toContainText(/outdated/i);
  await expect(page.getByRole("link", { name: /Still running/ }).locator("xpath=ancestor::*[@data-pull-row]")).toContainText("A review is still running");
  await page.goto("/repositories/22?state=closed&review=running");
  await expect(page.getByRole("link", { name: /Still running/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toHaveCount(0);
  await mockRepositoryPulls(page, 22, () => Array.from({ length: 101 }, (_, index) => ({ ...pr, id: `many-${index}`, number: index + 1, title: `Change ${index + 1}`, latest_run: null })));
  await page.goto("/repositories/22?active_pages=999999");
  const open = page.getByRole("region", { name: "Open", exact: true });
  await expect(open.getByRole("link")).toHaveCount(1);
  await expect(open.getByRole("status")).toHaveText("Page 11 of 11 · 101 total");
  await expect(open.getByRole("button", { name: /Next/ })).toBeDisabled();
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
  await chooseRepository(page, "acme/other");
  await expect(page).toHaveURL(/repositories\/23\?state=open$/);
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  const paused = page.getByRole("region", { name: "Review configuration" }).getByRole("heading", { name: "Review policy" }).locator("..").getByText("Paused", { exact: true });
  await expect(paused).toHaveAttribute("data-variant", "outline");
});

test("repository navigation, filtering, and review workspace", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mock(page);
  await page.goto("/repositories/22");
  await expect(
    page.locator('[data-slot="page-title"]').getByRole("heading", { level: 1, name: "Pull requests", exact: true }),
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
      const [description, metadata, state] = Array.from(element.children);
      const d = description.getBoundingClientRect(), m = metadata.getBoundingClientRect(), s = state.getBoundingClientRect();
      return { metadata: m.top - d.bottom, stateBelow: s.top - m.bottom, stateTop: s.top - d.top };
    });
    expect(gaps.metadata).toBe(6);
    if (info.project.name === "desktop") expect(gaps.stateTop).toBe(0);
    else expect(gaps.stateBelow).toBe(6);
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
    const inboxNav = page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Inbox", exact: true, disabled: false });
    await expect(inboxNav).toHaveAttribute("href", inbox);
    await openNavigation(page);
    await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true })).toHaveAttribute("aria-current", "page");
    await closeNavigation(page);
    await (await breadcrumbAncestor(page, "#148")).click();
    await expect(page).toHaveURL((url) => url.pathname === "/pulls/pr");
    await openNavigation(page);
    await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Pull requests", exact: true })).toHaveAttribute("aria-current", "page");
    await closeNavigation(page);
    if (entry === "history") {
      await (await breadcrumbAncestor(page, "Pull requests")).click();
      await expect(page).toHaveURL("/repositories/22?state=open");
      await (await breadcrumbAncestor(page, "payments")).click();
      await expect(page).toHaveURL("/repositories/22/overview");
    }
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
  await expect(patch).toContainText("@@ -42,3 +42,3 @@");
  expect(await patch.locator('[data-slot="code-line-numbers"]').textContent()).toBe("\n42\n43\n\n\n44");
  await expect(patch.locator('[data-slot="code-line-numbers"]')).toHaveAttribute("aria-hidden", "true");
  await expect(patch.locator('[data-slot="code-line-numbers"]')).toHaveCSS("user-select", "none");
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
    const highlightsFillCodeArea = await patch.evaluate((element) => {
      const right = element.getBoundingClientRect().left + element.clientLeft + element.clientWidth
        - parseFloat(getComputedStyle(element).paddingRight);
      return [...element.querySelectorAll(".hljs-addition, .hljs-deletion")]
        .every((line) => line.getBoundingClientRect().right >= right - 1);
    });
    expect(highlightsFillCodeArea).toBe(true);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/code-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
    await patch.screenshot({ path: `../.impeccable/review/diff-width-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
  }
});

for (const example of [
  { name: "suggestion only", original: "", suggested: "const limit = 3;\n", path: "retry.tsx", language: "typescript", count: 1 },
  { name: "identical", original: "return True\n", suggested: "return True\n", path: "retry.py", language: "python", count: 1 },
  { name: "no suggestion", original: "FROM alpine\n", suggested: "", path: "Dockerfile", language: "dockerfile", count: 1 },
  { name: "blank lines", original: "/* original\n\n * comment */\nconst value = 1;\n", suggested: "", path: "retry.ts", language: "typescript", count: 1 },
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
      expect(await code.locator("code").textContent()).toBe(example.suggested || example.original);
      const gutter = panel.locator('[data-slot="code-line-numbers"]');
      if (example.original) {
        const numbers = (await gutter.textContent())!.split("\n");
        expect(numbers[0]).toBe("42");
        expect(numbers).toHaveLength(example.original.trimEnd().split("\n").length);
        expect(numbers.at(-1)).toBe(String(41 + numbers.length));
      } else await expect(gutter).toHaveCount(0);
      await expect(code.locator(".hljs")).toHaveCount(example.language ? 1 : 0);
      if (example.language) await expect(code.locator("code")).toHaveClass(`hljs language-${example.language}`);
      await expect(code.locator("script, img")).toHaveCount(0);
    }
    await noOverflow(page);
  });
}

test("finding diff keeps original line numbers across insertions and separate hunks", async ({ page }) => {
  const original = Array.from({ length: 30 }, (_, index) => `const value${index + 1} = ${index + 1};`);
  const suggested = [...original];
  suggested.splice(1, 0, "const inserted = true;");
  suggested[29] = "const value29 = 0;";
  await mock(page, { finding: { existing_code: original.join("\n"), suggestion: suggested.join("\n"), start_line: 100, end_line: 129 } });
  await page.goto("/pulls/pr?finding=finding");
  const patch = page.getByRole("dialog").getByRole("region", { name: "Suggested change diff" });
  const source = await patch.locator("code").textContent();
  const numbers = (await patch.locator('[data-slot="code-line-numbers"]').textContent())!.split("\n");
  const lines = source!.split("\n");
  expect(lines.filter((line) => line.startsWith("@@"))).toHaveLength(2);
  expect(numbers).toHaveLength(lines.length);
  expect(numbers[lines.indexOf(" const value1 = 1;")]).toBe("100");
  expect(numbers[lines.indexOf("+const inserted = true;")]).toBe("");
  expect(numbers[lines.indexOf("-const value29 = 29;")]).toBe("128");
  expect(numbers[lines.indexOf("+const value29 = 0;")]).toBe("");
  expect(numbers[lines.indexOf(" const value30 = 30;")]).toBe("129");
  await noOverflow(page);
});

test("finding snippets without a saved source location stay unnumbered", async ({ page }) => {
  await mock(page, { finding: { existing_code: "const value = 1;", suggestion: "", start_line: undefined } });
  await page.goto("/pulls/pr?finding=finding");
  const code = page.getByRole("dialog").getByRole("region", { name: "Code at detection" });
  await expect(code.locator("code")).toHaveText("const value = 1;");
  await expect(code.locator('[data-slot="code-line-numbers"]')).toHaveCount(0);
  await expect(code).not.toHaveAttribute("aria-description");
});

test("pull filters, history tabs, and finding tabs survive reload and browser history", async ({ page }) => {
  await mock(page);
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs: [run],
    findings: [{ ...finding }, { ...finding, id: "fixed", status: "resolved" }, { ...finding, id: "dismissed", status: "dismissed" }],
  }));
  await page.goto("/pulls/pr?q=retry&status=open&severity=high&category=bug");
  const open = page.getByRole("region", { name: "Open findings", exact: true });
  await expect(open.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  await open.getByRole("button").click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await sheet.getByRole("button", { name: "View activity", exact: true }).click();
  await page.reload();
  await expect(sheet.getByRole("tab", { name: "Activity 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Resolved 1", exact: true }).click();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true }).locator('[data-slot="finding-card"]')).toHaveCount(1);
  await page.goBack();
  await expect(page.getByLabel("Search findings", { exact: true })).toHaveValue("retry");
  await page.getByRole("tab", { name: /Run history/ }).click();
  await page.reload();
  await expect(page.getByRole("tab", { name: /Run history/ })).toHaveAttribute("aria-selected", "true");
});

test("run tabs and independent finding and assessment filters survive shared URLs", async ({ page }, info) => {
  const candidates: Schema["CandidateOut"][] = [{ id: "failed", snapshot: { path: "src/client.ts", content: "Timeout proposal.", severity: "low", category: "security" },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", assessment_only: false, retryable: false }];
  await mock(page, { candidates,
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1, pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
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
  await findingSheet.getByRole("tab", { name: "Current finding", exact: true }).click();
  await page.reload();
  await expect(findingSheet.getByRole("tab", { name: "Current finding", exact: true })).toHaveAttribute("aria-selected", "true");
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
    resolved: [{ ...finding, status: "open", sync_intent: null, content: "Original evidence preserved from the earlier review." }],
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
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText("Original evidence preserved from the earlier review.");
  await sheet.getByRole("button", { name: "View verification", exact: true }).click();
  await expect(sheet).toContainText("The retry loop now stops after three attempts.");
  await expect(sheet).toContainText("Automatic resolution: resolved");
  await page.reload();
  await expect(sheet.getByRole("tab", { name: /^Activity/ })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(tab).toBeFocused();
  const card = page.getByRole("region", { name: "Findings resolved by this run", exact: true }).getByRole("button");
  await expect(card).toContainText("Resolved");
  await card.focus();
  await page.keyboard.press("Enter");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText("Original evidence preserved from the earlier review.");
  await page.reload();
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText("Original evidence preserved from the earlier review.");
  await page.keyboard.press("Escape");
  await expect(tab).toBeFocused();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/run-resolved-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  expect(requests).toEqual([]);
});

for (const status of ["running", "completed"] as const) {
test(`run resolved tab catches late confirmations after opening a ${status} review`, async ({ page }) => {
  await page.clock.install();
  const resolved: Schema["FindingOut"][] = [];
  const verification = { pending: 2, resolved: 0, selected: 2 };
  const currentRun: Partial<Run> = { status, progress: { verification } };
  const mutations = await mock(page, { resolved, occurrences: [], run: currentRun });
  let reads = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/v1/runs/run/findings") reads++;
  });
  await page.goto("/runs/run?tab=resolved");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("Fixes appear here after verification and GitHub synchronization complete. Findings absent from a review are not automatically resolved.")).toBeVisible();
  currentRun.status = "completed";
  await page.clock.runFor(5500);
  await expect(page.getByRole("heading", { name: "No findings resolved by this run", exact: true })).toBeVisible();
  const cards = page.getByRole("region", { name: "Findings resolved by this run", exact: true }).locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(0);
  resolved.push({ ...finding, status: "resolved", sync_intent: null });
  verification.pending = 1;
  verification.resolved = 1;
  await page.clock.runFor(5500);
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.clock.runFor(5500);
  await expect(cards).toHaveCount(1);
  resolved.push({ ...finding, id: "last-resolution", status: "resolved", sync_intent: null });
  verification.pending = 0;
  verification.resolved = 2;
  await page.clock.runFor(5500);
  await expect(page.getByRole("tab", { name: "Resolved 2", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.clock.runFor(5500);
  await expect(cards).toHaveCount(2);
  const settledReads = reads;
  await page.clock.runFor(11000);
  expect(reads).toBe(settledReads);
  expect(mutations).toEqual([]);
  await noOverflow(page);
});
}

test("late resolutions refresh loaded pages without resetting resolved filters", async ({ page }) => {
  await page.clock.install();
  const resolved = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `resolved-${index}`,
    path: `src/sync-${index}.ts`, status: "resolved", sync_intent: null }));
  const mutations = await mock(page, { resolved, run: { status: "completed" } });
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runs/run/findings") requests.push(url.searchParams);
  });
  await page.goto("/runs/run?tab=resolved&resolved_q=sync&resolved_severity=high");
  const list = page.getByRole("region", { name: "Findings resolved by this run", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(10);
  await list.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await expect(list.getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  resolved.push({ ...finding, id: "new-resolution", path: "src/sync-new.ts", created_at: "2026-10-06T13:20:00Z", status: "resolved", sync_intent: null });
  resolved.push({ ...finding, id: "unmatched-resolution", path: "src/other.ts", severity: "low", status: "resolved", sync_intent: null });
  await page.clock.runFor(5500);
  await expect(page.getByRole("tab", { name: "Resolved 25", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.clock.runFor(5500);
  await expect(cards).toHaveCount(10);
  await expect(cards.first()).not.toContainText("src/sync-new.ts");
  await expect(list.getByRole("status")).toHaveText("Page 2 of 3 · 24 total");
  await expect(page.getByLabel("Search resolved findings", { exact: true })).toHaveValue("sync");
  expect(requests.at(-1)?.get("page")).toBe("2");
  expect(requests).toHaveLength(3);
  expect(requests.every((params) => params.get("q") === "sync" && params.get("severity") === "high")).toBe(true);
  const settledReads = requests.length;
  await page.clock.runFor(11000);
  expect(requests).toHaveLength(settledReads);
  expect(mutations).toEqual([]);
});

test("resolved findings use independent backend filters and reset pagination", async ({ page }, info) => {
  const resolved = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `resolved-${String(index).padStart(2, "0")}`,
    path: `src/handler-${index}.ts`, status: "resolved", sync_intent: null,
    severity: index % 2 ? "low" : "high", category: index === 1 ? "custom_rule" : "bug" }));
  await mock(page, { resolved });
  const requests: URLSearchParams[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/runs/run/findings") requests.push(url.searchParams);
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
  await list.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await expect(list.getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  expect(requests.at(-1)?.get("page")).toBe("2");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Low/ }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(10);
  await expect(list.getByRole("status")).toHaveText("Page 1 of 2 · 11 total");
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
  test(`resolved findings avoid unchanged requests for a ${status} run`, async ({ page }) => {
    await page.clock.install();
    await mock(page, { run: { status }, resolved: [] });
    let reads = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/runs/run/findings") reads++;
    });
    await page.goto("/runs/run?tab=resolved");
    await expect(page.getByRole("region", { name: "Findings resolved by this run", exact: true }).getByRole("heading")).toBeVisible();
    await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
    await page.clock.runFor(15000);
    expect(reads).toBe(1);
  });
}

test("resolved pages preserve loaded cards on failure and hide them on access denial", async ({ page }) => {
  const resolved = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `resolved-${index}`, status: "resolved", sync_intent: null }));
  await mock(page, { resolved });
  let failure = 500;
  await page.route("**/api/v1/runs/run/findings?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    return failure && Number(params.get("page")) > 1 ? route.fulfill({ status: failure, json: { detail: "Resolved page unavailable" } })
      : route.fallback();
  });
  await page.goto("/runs/run?tab=resolved");
  const list = page.getByRole("region", { name: "Findings resolved by this run", exact: true });
  const cards = list.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(10);
  await list.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Resolved page unavailable" })).toBeVisible();
  await expect(cards).toHaveCount(10);
  failure = 0;
  await list.getByRole("button", { name: "Retry resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(10);
  failure = 403;
  await list.getByRole("button", { name: "Next resolved findings", exact: true }).click();
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
    && !url.searchParams.has("severity") && !url.searchParams.has("category") && url.searchParams.getAll("status").join(",") === "open");
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
  await expect(saved).not.toContainText("Summary comment");
  await expect(saved.getByText(file, { exact: true })).toBeVisible();
  await expect(saved.getByText(file, { exact: true }).locator("..")).toHaveText(directory + file);
  await expect(saved).not.toContainText("View details");
  await expect(saved).not.toContainText("Sync needs attention");
  await noOverflow(page);
  await saved.press("Enter");
  await expect(page.getByRole("dialog", { name: "Finding details" }).getByRole("tab", { name: "Saved evidence" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(saved).toBeFocused();
  expect(requests).toEqual([]);
});

test("run findings preserve historical results after resolution and reopening", async ({ page }, info) => {
  await page.clock.install();
  const occurrences = Array.from({ length: 13 }, (_, index) => ({ finding_id: `found-${index}`,
    snapshot: { ...finding, content: `Saved finding ${index}`, path: `saved/${index}.ts` }, inline_line: 46,
    is_new: index === 0, current_status: "open" }));
  const resolved = Array.from({ length: 12 }, (_, index) => ({ ...finding, id: `fixed-${index}`, status: "resolved", content: `Saved fix ${index}` }));
  await mock(page, { occurrences, resolved, run: { status: "completed" } });
  await page.goto("/runs/run");
  await expect(page.getByRole("tab", { name: "Findings 13", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 12", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(1);
  const list = page.getByRole("region", { name: "Findings at this run", exact: true });
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await list.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(list.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 2 · 13 total");
  await page.getByRole("tab", { name: "Resolved 12", exact: true }).click();
  const fixes = page.getByRole("region", { name: "Findings resolved by this run", exact: true });
  await fixes.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(fixes.getByRole("status")).toHaveText("Page 2 of 2 · 12 total");
  const before = await fixes.textContent();
  occurrences.forEach((item) => { item.current_status = "resolved"; });
  resolved.forEach((item) => { item.status = "open"; item.content = "Changed after this run"; });
  await page.clock.runFor(5500);
  await expect(fixes).toHaveText(before!);
  await page.getByRole("tab", { name: "Findings 13", exact: true }).click();
  await expect(list.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 2 · 13 total");
  await expect(list).not.toContainText("Changed after this run");
  await page.reload();
  await expect(list.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 2 · 13 total");
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("historical-run.png"), fullPage: true });
});

test("run finding result filters combine with other filters and survive navigation", async ({ page }, info) => {
  const occurrences = [
    { finding_id: "new", snapshot: { ...finding, content: "New defect" }, inline_line: 46, is_new: true, current_status: "open" },
    { finding_id: "open", snapshot: { ...finding, content: "Open repeated defect", severity: "medium" }, inline_line: 46, is_new: false, current_status: "open" },
    { finding_id: "resolved", snapshot: { ...finding, content: "Resolved repeated defect" }, inline_line: 46, is_new: false, current_status: "resolved" },
    { finding_id: "dismissed", snapshot: { ...finding, content: "Dismissed repeated defect" }, inline_line: 46, is_new: false, current_status: "dismissed" },
    { finding_id: "legacy", snapshot: finding, inline_line: 46, is_new: null, current_status: "open" },
    { finding_id: "unknown", snapshot: finding, inline_line: 46, is_new: false, current_status: null },
  ];
  await mock(page, { occurrences, assessment: { candidates: 0, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0, pending: 0, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 } });
  await page.goto("/runs/run?assessment_outcome=withheld&outcome=invalid");
  const list = page.getByRole("region", { name: "Findings at this run", exact: true });
  await expect(list.getByRole("button")).toHaveCount(6);
  await expect(list.getByText("Recorded finding", { exact: true })).toBeVisible();
  await expect(page).toHaveURL((url) => !url.searchParams.has("outcome"));
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await expect(page.getByRole("group", { name: "Result", exact: true }).getByRole("checkbox")).toHaveCount(3);
  await page.getByRole("checkbox", { name: /^Repeated findings/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Repeated findings/ })).toBeChecked();
  await expect(list.getByRole("button")).toHaveCount(4);
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/run-finding-filters-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Edit Result: Repeated findings", exact: true })).toBeFocused();
  await expect(page).toHaveURL((url) => url.searchParams.get("outcome") === "repeated" && url.searchParams.get("assessment_outcome") === "withheld");
  await page.reload();
  await expect(list.getByRole("button")).toHaveCount(4);
  await page.getByRole("tab", { name: /^Assessment/ }).click();
  await expect(page.getByRole("button", { name: "Edit Result: Withheld", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings 6", exact: true }).click();
  await page.getByRole("button", { name: "Edit Result: Repeated findings", exact: true }).click();
  await page.getByRole("checkbox", { name: /^New findings/ }).click();
  await expect(page.getByRole("checkbox", { name: /^New findings/ })).toBeChecked();
  await expect(list.getByRole("button")).toHaveCount(5);
  await page.keyboard.press("Escape");
  await page.goBack();
  await expect(list.getByRole("button")).toHaveCount(4);
  await page.goForward();
  await expect(list.getByRole("button")).toHaveCount(5);
  await page.getByLabel("Search run findings").fill("New defect");
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Medium/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Medium/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "No findings match these filters" })).toBeVisible();
  await page.getByRole("tabpanel").getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(list.getByRole("button")).toHaveCount(6);
  await expect(page.getByLabel("Search run findings")).toHaveValue("");
  await expect(page).toHaveURL((url) => !url.searchParams.has("outcome") && !url.searchParams.has("severity") && url.searchParams.get("assessment_outcome") === "withheld");
  await page.goto("/runs/run?outcome=recorded");
  await expect(list.getByRole("button")).toHaveCount(1);
  await expect(list.getByRole("button")).toContainText(finding.content);
});

for (const [status, tokens, expected] of [
  ["completed", 1234567, "1,234,567 tokens"],
  ["completed", 0, "0 tokens"],
  ["completed", null, "Token usage unavailable"],
  ["running", null, "Token usage pending"],
] as const) {
  test(`review run header shows total tokens: ${status}, ${tokens}`, async ({ page }) => {
    await mock(page, { run: { status }, totalTokens: tokens });
    await page.goto("/runs/run");
    const metadata = page.getByLabel("Run statistics");
    await expect(metadata).toBeVisible();
    await expect(metadata).toHaveText(`Duration: 1m 30s0 reused · 0 failed · ${expected}`);
    await expect(page.locator("main header")).toContainText("Current commit");
    await noOverflow(page);
  });
}

test("review run statistics preserve reused, failed and unknown file coverage", async ({ page }) => {
  const current: Partial<Run> = { report: { manifest: { coverage: {
    selected: [{ path: "a.ts" }, { path: "b.ts" }, { path: "c.ts" }],
    completed: [{ path: "a.ts" }, { path: "c.ts" }], reused: [{ path: "b.ts" }], failed: [{ path: "c.ts" }],
  } } } };
  await mock(page, { run: current });
  await page.goto("/runs/run");
  const stats = page.getByLabel("Run statistics");
  await expect(stats).toContainText("1 reused · 1 failed · Token usage unavailable");
  current.report = null;
  await page.reload();
  await expect(stats).toContainText("Review statistics unavailable");
  await noOverflow(page);
});

test("review run statistics wait for results without inventing counts", async ({ page }) => {
  const current: Partial<Run> = { status: "running", report: null, progress: {} };
  await mock(page, { run: current });
  await page.goto("/runs/run");
  const stats = page.getByLabel("Run statistics");
  await expect(stats).toHaveText("Duration: 1m 30sAwaiting review results");
  current.progress = { selected: 8 };
  await page.reload();
  await expect(stats).toContainText("— reused · — failed · Token usage pending");
  current.progress = { selected: 8, completed: 0, reused: 0, failed: 0 };
  await page.reload();
  await expect(stats).toContainText("0 reused · 0 failed · Token usage pending");
  await noOverflow(page);
});

test("review run statistics show live duration and validated progress", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-05T13:20:10Z") });
  await page.clock.setFixedTime(new Date("2026-10-05T13:20:10Z"));
  const current: Partial<Run> = { status: "running", finished_at: null, report: null,
    progress: { selected: 8, completed: 3, failed: 1, reused: 0 } };
  await mock(page, { run: current, totalTokens: 12500 });
  await page.goto("/runs/run");
  const stats = page.getByLabel("Run statistics");
  await expect(stats).toHaveText("Duration: 10s elapsed0 reused · 1 failed · 12,500 tokens");
  await page.clock.setFixedTime(new Date("2026-10-05T13:20:15Z"));
  await page.clock.fastForward(5000);
  await expect(stats).toContainText("Duration: 15s elapsed");
  current.status = "completed";
  current.finished_at = "2026-10-05T13:20:13Z";
  await page.clock.fastForward(5000);
  await expect(stats).toContainText("Duration: 13s");
  await expect(stats).not.toContainText("elapsed");
  await noOverflow(page);
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
    occurrences, finding: { status: "resolved", path: "src/current.ts", severity: "low", category: "performance", content: "Current evidence has been updated after the fix." },
  });
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
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
  await expect(page.getByText("6 recorded findings", { exact: true })).toHaveCount(0);
  expect((await page.getByRole("tab", { name: "Findings 6", exact: true }).boundingBox())!.y).toBeGreaterThan((await page.getByRole("region", { name: "Review stages", exact: true }).boundingBox())!.y);
  await expect(page.getByRole("link", { name: `#148 · ${pr.title}` })).toHaveCount(0);
  await expect(await breadcrumbAncestor(page, "#148")).toHaveAttribute("href", "/pulls/pr");
  await page.keyboard.press("Escape");
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
  await page.getByRole("tab", { name: "Findings 6", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Assessment", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Coverage", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("region", { name: "Reviewed files", exact: true }).locator("summary").click();
  await expect(page.getByRole("region", { name: "Reviewed files", exact: true }).getByText("src/config.ts", { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-coverage-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText("Review setup, execution details, revisions, and the configuration saved for this run.");
  await expect(page.getByRole("tabpanel")).toContainText("Shared connection");
  await expect(page.getByRole("tabpanel")).toContainText(run.head_sha);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `../.impeccable/review/run-details-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Findings 6", exact: true }).click();
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
  await expect(sheet.getByRole("tab", { name: "Saved evidence" })).toHaveAttribute("aria-selected", "true");
  await expect(sheet.getByText("handler-1.ts", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  await expect(sheet).toContainText(finding.content);
  await expect(sheet.getByRole("region", { name: "Suggested change diff" })).toBeVisible();
  await sheet.getByRole("tab", { name: "Current finding" }).click();
  await expect(sheet.getByText("current.ts", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("low", { exact: true })).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet).toContainText("Current evidence has been updated after the fix.");
  await expect(sheet.getByText("resolved", { exact: true })).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/cohesive-pr-current-finding-${info.project.name}.png`, animations: "disabled" });
  await sheet.getByRole("tab", { name: "Saved evidence" }).click();
  await expect(sheet.getByText("handler-1.ts", { exact: true }).first()).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  await expect(sheet.getByText("resolved", { exact: true })).toBeVisible();
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
  await expect(reviewed.locator("details").first()).not.toHaveAttribute("open", "");
  await expect(gaps.locator("details").first()).not.toHaveAttribute("open", "");
  await expect(reviewed.getByRole("list")).toBeHidden();
  await page.screenshot({ path: `/private/tmp/pr-coverage-collapsed-${info.project.name}.png`, fullPage: true });
  await reviewed.locator("summary").first().focus();
  await page.keyboard.press("Enter");
  await gaps.getByRole("heading").click();
  await expect(reviewed.getByRole("listitem")).toHaveCount(10);
  await expect(gaps.getByRole("listitem")).toHaveCount(4);
  await expect(reviewed.getByText("Validated file result saved in this run.", { exact: true })).toHaveCount(0);
  await expect(gaps.getByRole("listitem").filter({ hasText: failed.path })).toContainText(failed.reason);
  await expect(gaps.getByRole("listitem").filter({ hasText: "README.md" })).toContainText("Outside scope");
  await reviewed.getByRole("button", { name: "Next reviewed files" }).click();
  await expect(reviewed.getByRole("listitem")).toHaveCount(2);
  await expect(reviewed.getByRole("status").last()).toHaveText("Page 2 of 2 · 12 total");
  await expect(reviewed.getByText(completed[10].path, { exact: true })).toBeVisible();
  await expect(reviewed.getByRole("listitem").filter({ hasText: reused.path })).toContainText("Reused");
  await expect(gaps.getByRole("listitem")).toHaveCount(4);
  await reviewed.getByRole("heading").click();
  await expect(reviewed.getByRole("list")).toBeHidden();
  await expect(gaps.getByRole("list")).toBeVisible();
  await reviewed.getByRole("heading").click();
  await expect(reviewed.getByRole("status").last()).toHaveText("Page 2 of 2 · 12 total");
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
  await expect(coverage.getByRole("region", { name: "Code snippet", exact: true })).toContainText(failed.reason);
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
    await coverage.getByRole("heading", { name: /^Files not reviewed/ }).click();
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
  await page.getByRole("heading", { name: /^Files not reviewed/ }).click();
  const row = page.getByRole("region", { name: "Coverage", exact: true }).getByRole("listitem").filter({ hasText: "src/live.ts" });
  await expect(row).toContainText("Awaiting review");
  state.report = { manifest: { coverage: { selected: [{ path: "src/live.ts" }], completed: [{ path: "src/live.ts" }] } } };
  await page.clock.fastForward(5000);
  await page.getByRole("heading", { name: /^Reviewed files/ }).click();
  await expect(row).toContainText("Reviewed");
  await page.route("**/api/v1/runs/run", (route) => route.fulfill({ status: 403, json: { detail: "Repository access revoked" } }));
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert").filter({ hasText: "Repository access revoked" })).toBeVisible();
  await expect(row).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Coverage", exact: true })).toHaveCount(0);
});

test("review run polls file progress without moving the stage layout", async ({ page }) => {
  await page.clock.install();
  const current: Partial<Run> = { status: "running", report: null, finished_at: null,
    progress: { selected: 8, completed: 0, reused: 0, failed: 0 } };
  await mock(page, { run: current });
  await page.goto("/runs/run");
  const stage = page.getByRole("region", { name: "Review stages", exact: true });
  const count = stage.getByLabel("Review progress count", { exact: true });
  await expect(count).toHaveText("0 / 8 files");
  const initial = await stage.boundingBox();
  for (const completed of [1, 3]) {
    current.progress = { selected: 8, completed, reused: 0, failed: 1 };
    await page.clock.fastForward(5000);
    await expect(count).toHaveText(`${completed} / 8 files`);
    await expect(page.getByLabel("Run statistics")).toContainText("0 reused · 1 failed");
    await expect(page.getByLabel("Run statistics")).not.toContainText("files reviewed");
    expect((await stage.boundingBox())?.height).toBe(initial?.height);
  }
  await noOverflow(page);
});

test("review run retains live progress and recovery actions while evidence is pending", async ({ page }, info) => {
  const requests = await mock(page, { run: { status: "running", publication: "pending", publication_error: null, report: null, finished_at: null, check_enabled: false, progress: { selected: 8, completed: 3 } }, occurrences: [], assessment: { candidates: 0, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0, pending: 0, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 } });
  await page.goto("/runs/run");
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
  await expect(page.getByText("The review is in progress.", { exact: true })).toHaveCount(0);
  await expect(page.getByText("This page updates automatically as results are saved. You can cancel the review if needed.", { exact: true })).toHaveCount(0);
  const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(publication).toContainText("Waiting to publish");
  await expect(publication.getByText("Comments are published after review results are saved.", { exact: true })).toHaveCount(0);
  await expect(publication.locator(":scope > p")).toHaveCount(0);
  const heading = page.getByRole("heading", { name: "Review run", exact: true });
  await expect(heading).toBeVisible();
  await expect(heading.locator("..").locator("[data-slot=badge]")).toHaveText("running");
  await expect(heading.locator("..").locator("svg")).toHaveClass(/lucide-loader-circle/);
  await expect(page.getByRole("region", { name: "Review stages", exact: true }).getByLabel("Review progress count", { exact: true })).toHaveText("3 / 8 files");
  await expect(page.getByRole("heading", { name: "Review in progress", exact: true })).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/run-status-cleanup-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
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
    await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "GitHub publication", exact: true }).locator(":scope > p")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Retry GitHub publication" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  });
}

test("partial review and publication retry remain distinct", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await expect(page.getByLabel("Run statistics")).toContainText("Duration: 1m 30s");
  await expect(
    page.getByRole("region", { name: "Review outcome" }),
  ).toHaveCount(0);
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

test("GitHub check handles pending, errors, conclusion-only, and empty states", async ({ page }, info) => {
  const checkState: Partial<Run> = {
    check_status: null,
    check_conclusion: null,
    check_url: null,
    check_error: null,
  };
  await mock(page, { run: checkState });
  await page.goto("/runs/run");
  const check = page.getByRole("region", { name: "GitHub publication", exact: true });
  await expect(check.getByText("Pending", { exact: true })).toBeVisible();
  await expect(check.getByRole("status")).toHaveCount(0);
  await expect(check.locator(":scope > :empty")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-pending-${info.project.name}.png`, fullPage: true });

  for (const pending of [false, true]) {
    checkState.check_pending = pending;
    checkState.check_error = "GitHub check could not be updated.";
    await page.reload();
    await expect(check.getByText("Error", { exact: true })).toBeVisible();
    await expect(check.getByText("Pending", { exact: true })).toHaveCount(0);
    await expect(check.getByRole("alert").filter({ hasText: checkState.check_error })).toBeVisible();
    await expect(check.getByRole("link", { name: /^View check on GitHub:/ })).toHaveCount(0);
  }
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("check-error.png"), fullPage: true });

  checkState.check_conclusion = "success";
  checkState.check_pending = false;
  checkState.check_error = null;
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
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
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
  await expect(page.getByRole("banner").getByRole("link", { name: "Settings", exact: true })).toHaveCount(1);
  await openNavigation(page);
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Repository settings", exact: true })).toHaveCount(0);
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
  const trigger = page.locator('[data-slot="input-group"]').filter({ has: provider }).getByRole("button", { name: "Show options" });
  if (info.project.name === "mobile") await trigger.tap();
  else await trigger.click();
  await expect(page.getByRole("option", { name: "Anthropic", exact: true })).toBeVisible();
  await expect(provider).toBeFocused();
  await provider.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toBeVisible();
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
  await page.goto("/repositories/22/settings");
  const language = page.getByRole("combobox", { name: "Review language", exact: true });
  const trigger = page.locator('[data-slot="input-group"]').filter({ has: language }).getByRole("button", { name: "Show options" });
  await language.fill("span");
  if (info.project.name === "mobile") await trigger.tap();
  else await trigger.click();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(language).toHaveValue("English");
  await trigger.click();
  await expect(page.getByRole("option", { name: "Spanish", exact: true })).toBeVisible();
  await page.getByRole("option", { name: "Spanish", exact: true }).click();
  await expect(language).toHaveValue("Spanish");
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
      await page.goto("/repositories/22/settings");
      const language = page.getByRole("combobox", { name: "Review language", exact: true });
      await language.fill(search);
      if (close !== "Save") {
        if (close === "Tab") await language.press("Tab");
        else await page.getByRole("heading", { name: "Repository settings", exact: true }).click();
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
    await page.goto("/repositories/22/settings");
    const language = page.getByRole("combobox", { name: "Review language", exact: true });
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
  await page.goto("/repositories/22/settings");
  const language = page.getByRole("combobox", { name: "Review language", exact: true });
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
    await page.goto("/repositories/22/settings");
  await repositorySection(page, "AI defaults");
    const picker = page.getByRole("combobox", { name: "Default AI connection", exact: true });
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
      await repositorySection(page, "Pull request reviews");
    await page.getByRole("switch", { name: "Enable pull request reviews", exact: true }).click();
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ enabled: false, connection_id: "connection" });
    }
    await repositorySection(page, "Pull request reviews");
    await page.getByRole("switch", { name: "Enable pull request reviews", exact: true }).uncheck();
    await repositorySection(page, "AI defaults");
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
  await expect(page.locator('[data-slot="input-group"]').filter({ has: picker }).getByRole("button", { name: "Show options" })).toBeDisabled();
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
    await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
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
  await page.goto("/repositories/22/settings");
  await repositorySection(page, "AI defaults");
  const model = page.getByLabel("Model override (optional)");
  await expect(model).toHaveAttribute("placeholder", "review-model");
  await expectOption(page, model, "next-model", 1);
  await chooseOption(page, model, "next-model");
  const aiConnection = page.getByRole("combobox", { name: "Default AI connection", exact: true });
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
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Default AI connection", { exact: true }), "No connection");
  await expect(model).toBeDisabled();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect(requests.filter((request) => request.path === "/repositories/22")).toHaveLength(1);
  await expect(page.getByRole("form", { name: "Repository settings" })).toBeVisible();
});

const paymentsSummary = "This project helps businesses accept payments and keeps their systems informed when a payment changes.";

test("repository summary appears above activity for members", async ({ page }) => {
  await mock(page, { role: "member" });
  let projectSummary: string | null = paymentsSummary.replace("accept payments", "**accept payments**");
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{
    ...repo, settings: { ...repo.settings, project_summary: projectSummary },
  }] }));
  await page.goto("/repositories/22/overview");
  const summary = page.getByText(paymentsSummary, { exact: true });
  const activity = page.getByRole("region", { name: "Repository activity" });
  await expect(summary).toBeVisible();
  await expect(summary.locator("strong")).toHaveText("accept payments");
  await expect(page.getByRole("button", { name: "Regenerate", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveCount(0);
  await expect(activity).toBeVisible();
  expect((await summary.boundingBox())!.width).toBeCloseTo((await activity.boundingBox())!.width);
  expect((await summary.boundingBox())!.y + (await summary.boundingBox())!.height).toBeLessThan((await activity.boundingBox())!.y);
  await noOverflow(page);
  projectSummary = null;
  await page.reload();
  await expect(activity).toBeVisible();
  await expect(summary).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Generate (summary|context)/ })).toHaveCount(0);
});

test("repository summary offers AI connection setup when no connection is saved", async ({ page }) => {
  const requests = await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{
    ...repo, enabled: false, connection_id: null,
  }] }));
  await page.goto("/repositories/22/overview");
  const configure = page.getByRole("main").getByRole("link", { name: /AI connection/i });
  await expect(configure).toHaveAttribute("href", "/repositories/22/settings?tab=connection");
  await expect(page.getByRole("button", { name: /Generate (summary|context)/ })).toHaveCount(0);
  expect(requests).toHaveLength(0);
});

test("repository summary notification opens Overview without a generation card, and Edit opens Settings", async ({ page }) => {
  const requests = await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{
    ...repo, settings: { ...repo.settings, project_summary: paymentsSummary },
  }] }));
  await page.goto("/repositories/22/overview?generation=completed-summary");
  await expect(page.getByRole("region", { name: "Repository activity" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Repository summary generation" })).toHaveCount(0);
  const edit = page.getByRole("link", { name: /^Edit(?: (?:repository )?summary)?$/ });
  await expect(edit).toHaveAttribute("href", "/repositories/22/settings?tab=context");
  const heading = page.getByRole("heading", { name: "Overview", exact: true });
  const regenerate = page.getByRole("button", { name: "Regenerate", exact: true });
  const headingBox = (await heading.boundingBox())!;
  for (const [action, label] of [[regenerate, "Regenerate"], [edit, "Edit"]] as const) {
    await expect(action).toBeEmpty();
    await expect(action.locator("svg")).toHaveCSS("width", "14px");
    const box = (await action.boundingBox())!;
    expect(box.x).toBeGreaterThan(headingBox.x + headingBox.width);
    expect(box.y + box.height / 2).toBeCloseTo(headingBox.y + headingBox.height / 2);
    await action.focus();
    await expect(page.getByRole("tooltip", { name: label, exact: true })).toBeVisible();
  }
  await noOverflow(page);
  await edit.click();
  await expect(page).toHaveURL("/repositories/22/settings?tab=context");
  await expect(page.getByRole("textbox", { name: "Repository summary", exact: true })).toHaveValue(paymentsSummary);
  expect(requests).toHaveLength(0);
});

for (const context of ["Technical context about payment processing.", ""]) {
  test(`repository summary Regenerate saves in Overview ${context ? "with" : "without"} context`, async ({ page }) => {
    const requests = await mock(page);
    await page.clock.install();
    const replacement = "This repository helps merchants **accept payments** and receive updates.";
    let savedSettings = { ...repo.settings, background: context, project_summary: paymentsSummary };
    let directoryUnavailable = false;
    await page.route("**/api/v1/repositories", (route) => route.fulfill(directoryUnavailable
      ? { status: 503, json: { detail: "Repository directory temporarily unavailable." } }
      : { json: [{ ...repo, settings: savedSettings }] }));
    let starts = 0;
    await page.route("**/api/v1/repositories/22/background", (route) => {
      expect(route.request().postDataJSON()).toEqual({ mode: "summary_refresh", generation_id: expect.any(String) });
      starts++;
      return route.fulfill({ status: 202, json: { id: "replacement-summary", status: "queued" } });
    });
    let finished = false;
    await page.route("**/api/v1/repositories/22/background/replacement-summary", (route) => {
      return route.fulfill({ json: { id: "replacement-summary", mode: "summary_refresh",
        status: finished ? "done" : "running", project_summary: finished ? replacement : null } });
    });
    await page.goto("/repositories/22/overview");
    const oldSummary = page.getByText(paymentsSummary, { exact: true });
    await expect(oldSummary).toBeVisible();
    await page.getByRole("button", { name: "Regenerate", exact: true }).click();
    await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
    await expect(page).toHaveURL("/repositories/22/overview");
    await expect(oldSummary).toBeVisible();
    await expect(page.getByText("Repository summary updated.", { exact: true })).toHaveCount(0);
    if (context) {
      directoryUnavailable = true;
      await page.clock.fastForward(6000);
      await expect(page.getByRole("alert")).toContainText("Repository directory temporarily unavailable.");
      await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
      directoryUnavailable = false;
    } else {
      const dialogs: string[] = [];
      page.on("dialog", (dialog) => { dialogs.push(dialog.type()); return dialog.accept(); });
      await page.getByRole("link", { name: "Edit", exact: true }).click();
      await expect(page).toHaveURL("/repositories/22/settings?tab=context");
      await page.goBack();
      await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
      await page.reload();
      await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
      await page.goto("/repositories/22/settings?tab=context");
      await page.goto("/repositories/22/overview");
      await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
      expect(dialogs).toEqual([]);
      expect(requests.filter(({ path }) => path.endsWith("/background/cancel"))).toHaveLength(0);
    }
    savedSettings = { ...savedSettings, project_summary: replacement };
    finished = true;
    await page.clock.fastForward(2000);
    await expect(page.locator("[data-sonner-toast]")).toContainText("Repository summary updated.");
    await expect(page.getByText("This repository helps merchants accept payments and receive updates.", { exact: true }).locator("strong")).toHaveText("accept payments");
    await expect(oldSummary).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Regenerate", exact: true })).toBeEnabled();
    await expect(page).toHaveURL("/repositories/22/overview");
    expect(starts).toBe(1);
    expect(requests.filter(({ method }) => method === "PUT")).toHaveLength(0);
    expect(savedSettings.background).toBe(context);
  });
}

test("repository summary generation retries and displays the saved paragraph on Overview", async ({ page }) => {
  await mock(page);
  let projectSummary: string | null = null;
  let starts = 0;
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{
    ...repo, settings: { ...repo.settings, background: "Technical context about payment processing.", project_summary: projectSummary },
  }] }));
  await page.route("**/api/v1/repositories/22/background", (route) => {
    expect(route.request().postDataJSON()).toEqual({ mode: "summary", generation_id: expect.any(String) });
    return route.fulfill({ status: 202, json: { id: `summary-job-${++starts}`, status: "queued" } });
  });
  await page.route("**/api/v1/repositories/22/background/summary-job-1", (route) => route.fulfill({
    json: { id: "summary-job-1", status: "failed", error: "The provider timed out." },
  }));
  let finish!: () => void;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/v1/repositories/22/background/summary-job-2", async (route) => {
    await finished;
    projectSummary = paymentsSummary;
    await route.fulfill({ json: { id: "summary-job-2", status: "done", project_summary: projectSummary } });
  });
  await page.goto("/repositories/22/overview");
  await expect(page.getByRole("heading", { name: "No repository summary yet" })).toBeVisible();
  await noOverflow(page);
  await page.getByRole("button", { name: "Generate summary", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("The provider timed out.");
  await page.getByRole("button", { name: "Retry generation", exact: true }).click();
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  await expect(page.getByText(paymentsSummary, { exact: true })).toHaveCount(0);
  finish();
  await expect(page.getByText(paymentsSummary, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No repository summary yet" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Regenerate", exact: true })).toBeEnabled();
  expect(starts).toBe(2);
  await expect(page).toHaveURL("/repositories/22/overview");
});

test("Overview allows a new generation after a rejected start with a configured connection", async ({ page }) => {
  const background: Schema["BackgroundJobOut"] = { id: "overview-retry", status: "queued" };
  await mock(page, { background });
  const attempts: string[] = [];
  await page.route("**/api/v1/repositories/22/background", (route) => {
    attempts.push(route.request().postDataJSON().generation_id);
    return attempts.length === 1
      ? route.fulfill({ status: 422, json: { detail: "The selected model is unavailable." } })
      : route.fallback();
  });
  await page.goto("/repositories/22/overview");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("The selected model is unavailable.");
  await page.getByRole("button", { name: "Retry generation", exact: true }).click();
  await expect(page).toHaveURL("/repositories/22/settings?tab=context");
  expect(attempts).toHaveLength(2);
  expect(attempts[0]).not.toBe(attempts[1]);
});

test("repository summary and context started from Overview require a Settings save", async ({ page }) => {
  const draft = "Payment workers dispatch callbacks and retry transient failures.";
  await mock(page);
  let savedSettings = { ...repo.settings, project_summary: null as string | null };
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, settings: savedSettings }] }));
  await page.route("**/api/v1/repositories/22", (route) => {
    expect(route.request().method()).toBe("PUT");
    const body = route.request().postDataJSON();
    expect(body.settings).toMatchObject({ background: draft, project_summary: paymentsSummary });
    savedSettings = body.settings;
    return route.fulfill({ json: { ...repo, settings: savedSettings } });
  });
  let jobStarts = 0;
  let jobPolls = 0;
  let finish!: () => void;
  const finished = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/v1/repositories/22/background", (route) => {
    expect(route.request().postDataJSON()).toEqual({ mode: "context", generation_id: expect.any(String) });
    jobStarts++;
    return route.fulfill({ status: 202, json: { id: "context-from-overview", status: "queued" } });
  });
  await page.route("**/api/v1/repositories/22/background/context-from-overview", async (route) => {
    jobPolls++;
    await finished;
    await route.fulfill({ json: { id: "context-from-overview", status: "done", background: draft, project_summary: paymentsSummary } });
  });
  await page.goto("/repositories/22/overview");
  await page.getByRole("button", { name: "Generate context", exact: true }).evaluate((button) => {
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?tab=context/);
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Generating project context");
  expect(savedSettings.project_summary).toBeNull();
  finish();
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue(draft);
  await expect(page.getByRole("textbox", { name: "Repository summary", exact: true })).toHaveValue(paymentsSummary);
  expect(savedSettings.background).toBe("");
  expect(savedSettings.project_summary).toBeNull();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => savedSettings.project_summary).toBe(paymentsSummary);
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue(draft);
  await expect(page.getByRole("status", { name: "Context generation status" })).toBeEmpty();
  expect(jobPolls).toBe(1);
  await page.goto("/repositories/22/overview");
  await expect(page.getByText(paymentsSummary, { exact: true })).toBeVisible();
  expect(jobStarts).toBe(1);
});

for (const [dismiss, status] of [["Stay", "done"], ["Escape", "done"], ["Stay", "failed"]] as const) {
  test(`an Overview context launch respects ${dismiss} when the job is ${status}`, async ({ page }) => {
    const draft = "Payment workers dispatch callbacks.";
    const background: Schema["BackgroundJobOut"] = {
      id: "deferred-context", status, mode: "context", background: status === "done" ? draft : null,
      project_summary: dismiss === "Stay" ? null : paymentsSummary,
    };
    const requests = await mock(page, { background });
    let finish!: () => void;
    const finished = new Promise<void>((resolve) => { finish = resolve; });
    await page.route("**/api/v1/repositories/22/background", async (route) => {
      await finished;
      await route.fulfill({ status: 202, json: { id: background.id, status: "queued" } });
    });
    await page.goto("/repositories/22/overview");
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
    await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
    await openNavigation(page);
    await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Repository settings" }).click();
    const dialog = page.getByRole("dialog", { name: "Leave and cancel generation?" });
    await expect(dialog).toBeVisible();
    const launched = page.waitForResponse((response) => response.url().endsWith("/22/background"));
    finish();
    await (await launched).finished();
    const polled = page.waitForResponse((response) => response.url().endsWith("/22/background/deferred-context"));
    if (dismiss === "Stay") await dialog.getByRole("button", { name: "Stay", exact: true }).click();
    else await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await (await polled).finished();
    await expect(page.getByText("Generating project context.", { exact: true })).toHaveCount(0);
    await expect(page).toHaveURL("/repositories/22/overview");
    if (status === "done") {
      await page.getByRole("button", { name: "Open project context" }).click();
      await expect(page).toHaveURL("/repositories/22/settings?tab=context");
      await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue(draft);
    } else {
      await expect(page.getByRole("alert")).toContainText("Project context generation failed.");
      await expect(page.getByRole("button", { name: "Retry generation" })).toBeEnabled();
    }
    expect(requests.filter(({ path }) => path.endsWith("/background/cancel"))).toHaveLength(0);
  });
}

test("repository summary can be entered without context and updates Overview on Save", async ({ page }) => {
  const requests = await mock(page);
  let savedSettings = { ...repo.settings, project_summary: null as string | null };
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, settings: savedSettings }] }));
  const edit = "Helps merchants **accept payments**.";
  let saves = 0;
  await page.route("**/api/v1/repositories/22", (route) => {
    expect(route.request().method()).toBe("PUT");
    const body = route.request().postDataJSON();
    expect(body.settings).toMatchObject({ project_summary: edit });
    expect(body.settings).not.toHaveProperty("background");
    savedSettings = { ...savedSettings, ...body.settings };
    saves++;
    return route.fulfill({ json: { ...repo, settings: savedSettings } });
  });
  await page.goto("/repositories/22/settings?tab=context");
  const editor = page.getByRole("textbox", { name: "Repository summary", exact: true });
  await expect(editor).toBeVisible();
  await expect(editor).toHaveValue("");
  await expect(editor).toHaveAttribute("maxlength", "600");
  await editor.fill(edit);
  const rendered = page.getByText("Helps merchants accept payments.", { exact: true });
  await expect(rendered).toHaveCount(0);
  expect(savedSettings.project_summary).toBeNull();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  await page.goto("/repositories/22/overview");
  await expect(rendered.locator("strong")).toHaveText("accept payments");
  expect(saves).toBe(1);
  expect(requests.filter((request) => request.path.endsWith("/background"))).toHaveLength(0);
});

test("repository summary survives clearing and saving project context", async ({ page }) => {
  await mock(page);
  let savedSettings = { ...repo.settings, background: "Saved context.", project_summary: paymentsSummary };
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, settings: savedSettings }] }));
  await page.route("**/api/v1/repositories/22", (route) => {
    expect(route.request().method()).toBe("PUT");
    const body = route.request().postDataJSON();
    expect(body.settings.background).toBe("");
    expect(body.settings).not.toHaveProperty("project_summary");
    savedSettings = { ...savedSettings, ...body.settings };
    return route.fulfill({ json: { ...repo, settings: savedSettings } });
  });
  await page.goto("/repositories/22/settings?tab=context");
  const context = page.getByRole("textbox", { name: "Project context", exact: true });
  const summary = page.getByRole("textbox", { name: "Repository summary", exact: true });
  await expect(summary).toHaveValue(paymentsSummary);
  await context.clear();
  await expect(summary).toHaveValue(paymentsSummary);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  await page.reload();
  await expect(context).toHaveValue("");
  await expect(summary).toHaveValue(paymentsSummary);
  await page.goto("/repositories/22/overview");
  await expect(page.getByText(paymentsSummary, { exact: true })).toBeVisible();
});

for (const target of ["Overview", "AI defaults"]) {
  test(`legacy Overview context draft stays running unless leaving for ${target} is confirmed`, async ({ page }) => {
    await mock(page, { background: { id: "active-context", status: "running" } });
    const ids: string[] = [];
    await page.route("**/api/v1/repositories/22/background/cancel", (route) => {
      ids.push(route.request().postDataJSON().generation_id);
      return route.fulfill({ json: { id: "active-context", status: "cancelled" } });
    });
    await page.goto("/repositories/22/overview");
    const started = page.waitForRequest((request) => request.url().endsWith("/22/background"));
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
    const generationId = (await started).postDataJSON().generation_id;
    expect(generationId).toMatch(/^[\da-f-]{36}$/);
    await expect(page).toHaveURL("/repositories/22/settings?tab=context");
    const dialog = page.getByRole("dialog", { name: "Leave and cancel generation?" });
    const destination = target === "Overview"
      ? page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: target, exact: true })
      : page.getByRole("navigation", { name: "Repository settings sections" }).getByRole("link", { name: target, exact: true });
    if (target === "Overview") await openNavigation(page);
    await destination.click();
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Stay", exact: true }).click();
    await expect(page).toHaveURL("/repositories/22/settings?tab=context");
    expect(ids).toEqual([]);
    if (target === "Overview" && !(await destination.isVisible())) await openNavigation(page);
    await destination.click();
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    if (target === "Overview") {
      await expect(page).toHaveURL("/repositories/22/overview");
    } else {
      await expect(page).toHaveURL(/tab=connection$/);
      await repositorySection(page, "Project context");
      await expect(page.getByRole("button", { name: "Generate context", exact: true })).toBeEnabled();
    }
    expect(ids).toEqual([generationId]);
  });
}


test("notification reads on a direct review visit use the exact completion revision", async ({ page }) => {
  await mock(page, { run: { activity_finished_at: at } });
  await page.route("**/api/v1/activity/read", (route) => route.fulfill({ status: 204 }));
  const receipt = page.waitForRequest((request) => request.url().endsWith("/api/v1/activity/read"));
  await page.goto("/runs/run");
  await expect(page.getByRole("heading", { name: "Review run" })).toBeVisible();
  expect((await receipt).postDataJSON()).toEqual({ mode: "one", kind: "review", id: run.id, finished_at: at });
});

test("notification reads a standalone PR summary separately from its parent review", async ({ page }) => {
  await mock(page);
  await page.clock.install();
  let finished = false;
  let polls = 0;
  await page.route("**/api/v1/runs/run/summary/attempts/summary", (route) => {
    polls++;
    return route.fulfill({ json: { id: "summary", status: finished ? "done" : "running", finished_at: finished ? at : null } });
  });
  const receipts: unknown[] = [];
  await page.route("**/api/v1/activity/read", (route) => {
    receipts.push(route.request().postDataJSON());
    return route.fulfill({ status: 204 });
  });
  await page.goto("/runs/run?summaryJob=summary");
  const summary = page.getByRole("region", { name: "PR summary generation" });
  await expect(summary).toContainText(/running/i);
  finished = true;
  await page.clock.fastForward(5000);
  await expect(summary).toContainText(/completed/i);
  await expect.poll(() => receipts).toEqual([{ mode: "one", kind: "pr_summary", id: "summary", finished_at: at }]);
  await page.clock.runFor(11000);
  expect(polls).toBe(2);
});

for (const status of [404, 503]) {
  test(`PR summary activity handles a ${status} polling error`, async ({ page }) => {
    await mock(page);
    await page.clock.install();
    let polls = 0;
    await page.route("**/api/v1/runs/run/summary/attempts/summary", (route) => {
      if (++polls === 1) return route.fulfill({ status, json: { detail: "Could not load the summary attempt" } });
      return route.fulfill({ json: { id: "summary", status: "done", finished_at: null } });
    });
    await page.goto("/runs/run?summaryJob=summary");
    const summary = page.getByRole("region", { name: "PR summary generation" });
    await expect(summary.getByRole("alert")).toContainText("Could not load the summary attempt");
    await page.clock.runFor(11000);
    if (status === 404) expect(polls).toBe(1);
    else await expect(summary).toContainText(/completed/i);
  });
}

test("notification links preserve an active context draft and prompt before leaving for another job", async ({ page }) => {
  const requests = await mock(page, { background: { id: "active-context", status: "running" } });
  await page.route("**/api/v1/activity", (route) => route.fulfill({ json: {
    running: [
      { kind: "context", id: "active-context", repository: "acme/payments", title: "Project context",
        href: "/repositories/22/settings?tab=context&generation=active-context", status: "running", stages: ["Generating context"], unread: false, finished_at: null },
      { kind: "review", id: "run", repository: "acme/payments", title: "PR review", href: "/runs/run",
        status: "running", stages: ["Assessment"], unread: false, finished_at: null },
    ],
    updates: [], running_count: 2, queued_count: 0, unread_count: 0, as_of: at, snapshot: "feed-snapshot", next_running_cursor: null, next_updates_cursor: null,
  } }));
  await page.goto("/repositories/22/overview");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Generating project context");
  await page.getByRole("button", { name: "Notifications, 2 running, 0 unread" }).click();
  const inbox = page.getByRole("dialog", { name: "Notifications" });
  await inbox.getByRole("link", { name: /Project context.*Generating context/ }).click();
  await expect(page).toHaveURL(/tab=context&generation=active-context$/);
  const navigationGuard = page.getByRole("dialog", { name: "Leave and cancel generation?" });
  await expect(navigationGuard).toBeHidden();
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Generating project context");
  await page.getByRole("button", { name: "Notifications, 2 running, 0 unread" }).click();
  await inbox.getByRole("link", { name: /PR review.*Assessment/ }).click();
  await expect(navigationGuard).toBeVisible();
  await navigationGuard.getByRole("button", { name: "Stay", exact: true }).click();
  expect(requests.filter(({ path }) => path.endsWith("/background/cancel"))).toHaveLength(0);
});

test("legacy Overview context draft cancels when the browser reload is confirmed", async ({ page }) => {
  const background: Schema["BackgroundJobOut"] = { id: "reload-context", status: "running" };
  await mock(page, { background });
  // Requests kept alive by the browser can outlive Playwright's page routing.
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = (url, init) => {
      if (String(url).endsWith("/22/background/cancel")) {
        sessionStorage.setItem("tested-context-cancellation", JSON.stringify(init));
        return Promise.resolve(new Response("{}"));
      }
      return fetch(url, init);
    };
  });
  await page.goto("/repositories/22/overview");
  const started = page.waitForRequest((request) => request.url().endsWith("/22/background"));
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  const generationId = (await started).postDataJSON().generation_id;
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.reload({ timeout: 1500 }).catch(() => {});
  expect(await page.evaluate(() => sessionStorage.getItem("tested-context-cancellation"))).toBeNull();
  page.once("dialog", (dialog) => {
    expect(dialog.type()).toBe("beforeunload");
    background.status = "cancelled";
    return dialog.accept();
  });
  await page.reload();
  const request = await page.evaluate(() => JSON.parse(sessionStorage.getItem("tested-context-cancellation")!));
  expect(request).toMatchObject({ method: "POST", keepalive: true, headers: { "X-CSRF-Token": "csrf-from-session" } });
  expect(JSON.parse(request.body)).toEqual({ generation_id: generationId });
  await expect(page.getByRole("button", { name: "Generate context", exact: true })).toBeEnabled();
});

test("project context continues after navigation and reload, then automatically saves both fields", async ({ page }) => {
  const background: Schema["BackgroundJobOut"] = { id: "active-context", status: "running" };
  const requests = await mock(page, { background });
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => { dialogs.push(dialog.type()); return dialog.accept(); });
  const settings = "/repositories/22/settings?tab=context";
  await page.goto(settings);
  const summary = page.getByRole("textbox", { name: "Repository summary", exact: true });
  await summary.fill("Temporary summary");
  await summary.clear();
  await expect(page.getByText("Unsaved changes", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  const generating = page.getByRole("button", { name: "Generating…", exact: true });
  await expect(generating).toBeDisabled();
  await repositorySection(page, "AI defaults");
  await repositorySection(page, "Project context");
  await expect(generating).toBeDisabled();
  await openNavigation(page);
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page).toHaveURL("/repositories/22/overview");
  await page.goBack();
  await expect(generating).toBeDisabled();
  await page.reload();
  await expect(generating).toBeDisabled();
  await page.goto("/repositories/22/overview");
  await page.goto(settings);
  await expect(generating).toBeDisabled();
  Object.assign(background, { status: "done", background: "The payment workers dispatch callbacks.", project_summary: paymentsSummary });
  await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue(background.background!);
  await expect(summary).toHaveValue(paymentsSummary);
  await expect(page.getByText("Unsaved changes", { exact: true })).toHaveCount(0);
  await expect(page.locator("[data-sonner-toast]")).toContainText("Project context and repository summary saved.");
  expect(dialogs).toEqual([]);
  expect(requests).toEqual([{ path: "/repositories/22/background", method: "POST", body: {
    mode: "context_refresh", generation_id: expect.any(String), connection_id: "connection", model: "",
    limits: { token_budget: 200000, timeout_seconds: 900 },
  }, csrf: "csrf-from-session" }]);
  await page.goto("/repositories/22/overview");
  await expect(page.getByText(paymentsSummary, { exact: true })).toBeVisible();
});

test("project context generation uses unsaved model selections and preserves manual edits", async ({ page }, info) => {
  const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "running", background: null, error: null };
  const requests = await mock(page, { background });
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection" }] }));
  await page.goto("/repositories/22/settings");
  await repositorySection(page, "Project context");
  const field = page.getByRole("textbox", { name: "Project context", exact: true });
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Default AI connection", { exact: true }), "Second connection");
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
  await repositorySection(page, "Project context");
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue("");
  await repositorySection(page, "Project context");
  await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Generating project context…");
  await repositorySection(page, "Project context");
  await expect(field).toHaveAttribute("maxlength", "20000");
  expect(requests).toEqual([{ path: "/repositories/22/background", method: "POST", body: { mode: "context_refresh", generation_id: expect.any(String), connection_id: "second", model: "next-model", limits: { token_budget: 200000, timeout_seconds: 900 } }, csrf: "csrf-from-session" }]);
  background.background = Array.from({ length: 40 }, (_, index) => `Module ${index + 1}: A payment service built with TypeScript. Workers deliver webhooks and handle retries.`).join("\n\n");
  Object.assign(background, { project_summary: paymentsSummary.replace("accept payments", "**accept payments**") });
  background.status = "done";
  await repositorySection(page, "Project context");
  await expect(field).toHaveValue(background.background);
  const summary = page.getByRole("textbox", { name: "Repository summary", exact: true });
  await expect(summary).toHaveValue(background.project_summary!);
  const editor = await field.evaluate((element: HTMLTextAreaElement) => ({ height: element.clientHeight, contentHeight: element.scrollHeight }));
  expect(editor.height).toBeLessThanOrEqual(384);
  expect(editor.contentHeight).toBeGreaterThan(editor.height);
  await field.evaluate((element: HTMLTextAreaElement) => { element.scrollTop = element.scrollHeight; });
  expect(await field.evaluate((element: HTMLTextAreaElement) => element.scrollTop)).toBeGreaterThan(0);
  await noOverflow(page);
  await field.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/project-background-${info.project.name}.png`, fullPage: true });
  expect(requests).toHaveLength(1);
  await repositorySection(page, "Pull request reviews");
  await page.getByRole("switch", { name: "Automatically review new commits", exact: true }).click();
  await repositorySection(page, "Project context");
  const editBase = background.background;
  background.id = "second-background-job";
  background.status = "running";
  await page.getByRole("button", { name: "Generate context", exact: true }).click();
  await field.fill("Edited generated context");
  Object.assign(background, { status: "done", background: "Another generated context", project_summary: "Another generated summary" });
  await expect(summary).toHaveValue("Another generated summary");
  await expect(field).toHaveValue("Edited generated context");
  await page.route("**/api/v1/repositories/22", (route) => {
    const body = route.request().postDataJSON();
    expect(body).toMatchObject({
      connection_id: "second", expected_background: editBase,
      settings: { model: "next-model", background: "Edited generated context", automatic: false },
    });
    expect(body.settings).not.toHaveProperty("project_summary");
    return route.fulfill({ status: 409, json: { detail: "Project context changed while you were editing." } });
  });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Project context changed while you were editing.");
  await expect(field).toHaveValue("Edited generated context");
});

for (const outcome of ["done", "failed"] as const) {
  for (const change of ["background", "summary"]) {
    test(`project background preserves edits to ${change} when generation is ${outcome}`, async ({ page }) => {
      const background: Schema["BackgroundJobOut"] = { id: "background-job", status: "running", background: null, error: null };
      const requests = await mock(page, { background });
      await page.goto("/repositories/22/settings");
      await repositorySection(page, "Project context");
      const field = page.getByRole("textbox", { name: "Project context", exact: true });
      await page.getByRole("button", { name: "Generate context", exact: true }).click();
      const editedField = change === "background" ? field : page.getByRole("textbox", { name: "Repository summary", exact: true });
      await editedField.fill("Keep this edit");
      background.background = "Stale generated context";
      background.project_summary = paymentsSummary;
      background.error = "Failure from the old selections";
      background.status = outcome;
      await repositorySection(page, "Project context");
      await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Your selections or context changed.");
      if (outcome === "done") {
        const otherField = change === "background" ? page.getByRole("textbox", { name: "Repository summary", exact: true }) : field;
        await expect(otherField).toHaveValue(change === "background" ? background.project_summary! : background.background);
      }
      await expect(page.getByText("Project context and repository summary saved.")).toHaveCount(0);
      await repositorySection(page, "Project context");
      await expect(editedField).toHaveValue("Keep this edit");
      await expect(page.getByRole("alert")).toHaveCount(0);
      await expect(page.getByText(/Generation reference:/)).toHaveCount(0);
      expect(requests).toHaveLength(1);
      expect(requests[0].body).toEqual({ mode: "context_refresh", generation_id: expect.any(String), connection_id: "connection", model: "", limits: { token_budget: 200000, timeout_seconds: 900 } });
    });
  }
}

for (const outcome of ["done", "failed"] as const) {
  for (const change of ["background", "model", "connection", "limits"]) {
    test(`project background clears ${outcome} feedback after changing ${change}`, async ({ page }) => {
      const background: Schema["BackgroundJobOut"] = {
        id: "feedback-job", status: outcome, background: "A generated draft", project_summary: paymentsSummary,
        error: "The provider denied model access.",
      };
      await mock(page, { background });
      await page.goto("/repositories/22/settings");
  await repositorySection(page, "Project context");
      await repositorySection(page, "Project context");
      await page.getByRole("button", { name: "Generate context", exact: true }).click();
      if (outcome === "done") await expect(page.locator("[data-sonner-toast]")).toContainText("Project context and repository summary saved.");
      else {
        await expect(page.getByRole("alert")).toContainText(background.error!);
        await expect(page.getByText("Generation reference: feedback-job")).toBeVisible();
      }
      if (change === "background") await page.getByRole("textbox", { name: "Project context", exact: true }).fill("New manual text");
      if (change === "model") await repositorySection(page, "AI defaults");
      if (change === "model") await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
      if (change === "connection") await repositorySection(page, "AI defaults");
      if (change === "connection") await chooseOption(page, page.getByLabel("Default AI connection", { exact: true }), "No connection");
      if (change === "limits") {
        await repositorySection(page, "Pull request reviews");
        await page.getByLabel("Time limit (seconds)", { exact: true }).fill("1200");
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
    const background: Schema["BackgroundJobOut"] = { id: "retry-job", status: "done", background: "Recovered context", project_summary: paymentsSummary };
    const requests = await mock(page, { background });
    const polls: string[] = [];
    await page.route("**/api/v1/repositories/22/background/*", async (route) => {
      polls.push(new URL(route.request().url()).pathname);
      if (polls.length <= 2) {
        if (transient === "network") await route.abort("failed");
        else await route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } });
      } else await route.fallback();
    });
    await page.goto("/repositories/22/settings");
  await repositorySection(page, "Project context");
    await repositorySection(page, "Project context");
    await page.getByRole("button", { name: "Generate context", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "Project context", exact: true })).toHaveValue("Recovered context");
    expect(polls).toEqual(Array(3).fill("/api/v1/repositories/22/background/retry-job"));
    expect(requests).toHaveLength(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    await repositorySection(page, "Project context");
    await expect(page.locator("[data-sonner-toast]")).toContainText("Project context and repository summary saved.");
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
    await page.goto("/repositories/22/settings");
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
  await page.goto("/repositories/22/settings");
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
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Default AI connection", { exact: true }), "No connection");
  await repositorySection(page, "Project context");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose an AI connection to generate context.")).toBeVisible();
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Default AI connection", { exact: true }), "No model");
  await repositorySection(page, "Project context");
  await expect(generate).toBeDisabled();
  await expect(page.getByText("Choose a model to generate context.")).toBeVisible();
  await repositorySection(page, "AI defaults");
  await chooseOption(page, page.getByLabel("Model override (optional)"), "next-model");
  await repositorySection(page, "Project context");
  await expect(generate).toBeEnabled();
});

for (const status of [422, 503]) {
  test(`project background retry after editing limits distinguishes a ${status} start error`, async ({ page }) => {
    const background: Schema["BackgroundJobOut"] = {
      id: "replay-context", status: "done", background: "Generated context", project_summary: paymentsSummary,
    };
    await mock(page, { background });
    const attempts: Schema["BackgroundInput"][] = [];
    await page.route("**/api/v1/repositories/22/background", (route) => {
      attempts.push(route.request().postDataJSON());
      return attempts.length === 1
        ? route.fulfill({ status, json: { detail: "Generation could not be started." } })
        : route.fallback();
    });
    await page.goto("/repositories/22/settings?tab=context");
    const generate = page.getByRole("button", { name: "Generate context", exact: true });
    await generate.click();
    await expect(page.getByRole("alert")).toContainText("Generation could not be started.");
    await repositorySection(page, "Pull request reviews");
    await page.getByLabel("Time limit (seconds)", { exact: true }).fill("1200");
    await repositorySection(page, "Project context");
    await generate.click();
    if (status === 503) {
      await expect(page.getByRole("status", { name: "Context generation status" })).toContainText("Your selections or context changed.");
      expect(attempts).toEqual([attempts[0], attempts[0]]);
      await expect(page.getByText("Project context and repository summary saved.")).toHaveCount(0);
    } else {
      await expect(page.getByText("Project context and repository summary saved.")).toBeVisible();
      expect(attempts).toHaveLength(2);
      expect(attempts[1].generation_id).not.toBe(attempts[0].generation_id);
      expect(attempts[1].limits?.timeout_seconds).toBe(1200);
    }
  });
}

test("project background preserves text when starting or polling fails", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/repositories/22/background", (route) => route.fulfill({ status: 422, json: { detail: "Authorize this repository first." } }));
  await page.goto("/repositories/22/settings");
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
    await page.goto("/repositories/22/settings");
  await repositorySection(page, "Project context");
    await repositorySection(page, "Pull request reviews");
    if (unlimited) {
      await page.getByLabel("Unlimited analysis token budget", { exact: true }).check();
      await page.getByLabel("Unlimited time limit (seconds)", { exact: true }).check();
    } else {
      await page.getByLabel("Analysis token budget", { exact: true }).fill("450000");
      await page.getByLabel("Time limit (seconds)", { exact: true }).fill("1200");
    }
    await repositorySection(page, "Project context");
    await expect(page.getByText(/Generation uses these review limits:/)).toHaveCount(0);
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
  await page.goto("/repositories/22/settings");
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

test("repository limits default to unlimited and preserve an explicit cap", async ({ page }, info) => {
  await mock(page);
  const limits = [
    ["Maximum reviewable files", "max_files"], ["Time limit (seconds)", "timeout_seconds"],
    ["Analysis token budget", "token_budget"], ["Assessment token budget", "assessment_token_budget"],
    ["Verification token budget", "verification_token_budget"],
    ["Summary time limit (seconds)", "summary_timeout_seconds"], ["Summary token budget", "summary_token_budget"],
  ] as const;
  const unlimited = Object.fromEntries(limits.map(([, key]) => [key, null]));
  let current = structuredClone(repo) as Schema["RepositoryOut"];
  for (const [, key] of limits) delete current.settings[key];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/repositories/22", async (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    await route.fulfill({ json: current });
  });
  await page.goto("/repositories/22/settings?tab=limits");
  for (const [label] of limits) {
    await expect(page.getByLabel(`Unlimited ${label.toLowerCase()}`, { exact: true })).toBeChecked();
    await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
    await expect(page.getByLabel(label, { exact: true })).toHaveValue("");
  }
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/unlimited-defaults-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  expect(current.settings).toMatchObject(unlimited);
  await page.goto("/repositories/22/settings?tab=limits");
  await page.getByLabel("Unlimited maximum reviewable files", { exact: true }).uncheck();
  await expect(page.getByLabel("Maximum reviewable files", { exact: true })).toHaveValue("40");
  await page.getByLabel("Maximum reviewable files", { exact: true }).fill("75");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Repository settings saved.", { exact: true }).last()).toBeVisible();
  expect(current.settings).toMatchObject({ ...unlimited, max_files: 75 });
  await page.goto("/repositories/22/settings?tab=limits");
  await expect(page.getByLabel("Unlimited maximum reviewable files", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Maximum reviewable files", { exact: true })).toHaveValue("75");
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
  await page.goto("/repositories/22/settings");
  await repositorySection(page, "Pull request reviews");
  const time = page.getByLabel("Time limit (seconds)", { exact: true });
  const tokens = page.getByLabel("Analysis token budget", { exact: true });
  const files = page.getByLabel("Maximum reviewable files", { exact: true });
  const unlimitedTime = page.getByRole("checkbox", { name: "Unlimited time limit (seconds)", exact: true });
  const unlimitedTokens = page.getByRole("checkbox", { name: "Unlimited analysis token budget", exact: true });
  const unlimitedFiles = page.getByRole("checkbox", { name: "Unlimited maximum reviewable files", exact: true });
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
      await expect(page.getByRole("form", { name: "Repository settings" })).toBeVisible();
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
  await page.reload();
  await expect(time).toHaveValue("1200");
  await unlimitedTokens.check();
  await unlimitedFiles.check();
  await unlimitedTime.check();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: null });
  await page.reload();
  await expect(unlimitedTime).toBeChecked();
  await expect(unlimitedTokens).toBeChecked();
  await expect(unlimitedFiles).toBeChecked();
  await unlimitedFiles.uncheck();
  await expect(files).toHaveValue("40");
  await noOverflow(page);
  await time.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Switch to dark mode" }).click();
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: 40 });
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
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet).toContainText(finding.content);
  await expect(sheet.getByRole("region", { name: "Suggested change diff" })).toBeVisible();
  await expect(sheet.getByRole("alert")).toContainText("Current finding unavailable");
  await expect(sheet.getByRole("button", { name: "Resolve finding", exact: true })).toHaveCount(0);
  await sheet.getByRole("tab", { name: "Current finding", exact: true }).click();
  await expect(sheet).toContainText("Current evidence could not be loaded.");
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("Bug", { exact: true })).toHaveCount(0);
  await sheet.getByRole("tab", { name: "Saved evidence", exact: true }).click();
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
  await sheet.getByRole("tab", { name: "Current finding", exact: true }).click();
  await expect(page.locator('header [role="progressbar"][aria-label="Loading workspace"]')).toBeVisible();
  await expect(sheet.locator('[data-slot="skeleton"]')).toHaveCount(0);
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("Bug", { exact: true })).toHaveCount(0);
  release();
  await expect(sheet.getByText("low", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Performance", { exact: true })).toBeVisible();
  await expect(sheet).toContainText("current.ts");
  await sheet.getByRole("tab", { name: "Saved evidence", exact: true }).click();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
});

for (const field of [
  { label: "Analysis token budget", key: "token_budget", valid: "6000", excessive: "5000001" },
  { label: "Parallel review tasks", key: "concurrency", valid: "4", excessive: "9" },
] as const) {
  test(`repository ${field.label} keeps invalid drafts out of saved settings`, async ({ page }) => {
    const requests = await mock(page);
    await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, {
      ...connection, id: "personal", name: "My ChatGPT", kind: "chatgpt", owner_id: 7, protocol: "native",
    }] }));
    await page.goto("/repositories/22/settings?tab=connection");
    await repositorySection(page, "Pull request reviews");
    const input = page.getByLabel(field.label, { exact: true });
    for (const invalid of ["", "0", field.excessive, "1e309"]) {
      await input.fill(invalid);
      await expect(input).toHaveValue(invalid === "1e309" ? "" : invalid);
      expect(await input.evaluate((element: HTMLInputElement) => element.validity.valid)).toBe(false);
      await repositorySection(page, "AI defaults");
      await page.getByRole("button", { name: "Save", exact: true }).click();
      await expect(input).toBeFocused();
      expect(requests).toEqual([]);
    }
    await input.clear();
    await input.pressSequentially(field.valid);
    await expect(input).toHaveValue(field.valid);
    await input.fill("0");
    await repositorySection(page, "AI defaults");
    await repositorySection(page, "Pull request reviews");
    await expect(input).toHaveValue("0");
    await input.fill(field.valid);
    await repositorySection(page, "Pull request reviews");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
      settings: { [field.key]: Number(field.valid) },
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
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText("41 reviewable files exceed the limit of 40. No files were reviewed.");
    await expect(outcome).toContainText("post the command shown in the GitHub skip notice");
    await expect(outcome).not.toContainText("@codemortis");
    await expect(outcome).toContainText("Choose a shared API connection.");
    if (publication === "failed" || publication === "partially_published") {
      await expect(outcome).toContainText("send the saved file-limit notice without running AI analysis");
      await expect(outcome).not.toContainText("send saved results");
    }
    await page.getByRole("link", { name: "View run details", exact: true }).click();
    await expect(outcome).toHaveCount(0);
    await expect(page.getByRole("region", { name: "GitHub publication", exact: true }).locator(":scope > p")).toHaveCount(0);
    await expect(page.getByText(label, { exact: true })).toBeVisible();
    await expect(page.getByText("The review results were published on GitHub.", { exact: true })).toHaveCount(0);
    const retry = page.getByRole("button", { name: "Retry GitHub publication" });
    if (publication === "failed" || publication === "partially_published") {
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
    await page.goto("/pulls/pr");
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText("This review was skipped. No specific reason was saved. Publication failed.");
    await expect(outcome).toContainText("Inspect the coverage report and repository settings before starting a new review.");
    await expect(outcome).not.toContainText("skip notice");
    await outcome.getByRole("link", { name: "View run details" }).click();
    await expect(outcome).toHaveCount(0);
    await expect(page.getByText("Publication failed", { exact: true })).toBeVisible();
    await expect(page.getByText("Skip notice publication failed", { exact: true })).toHaveCount(0);
    await noOverflow(page);
  });
}

test("oversized review explains its outcome and links to editable limits throughout the app", async ({ page }, info) => {
  const requests = await mock(page, { run: limitedRun });
  const reason = "41 reviewable files exceed the limit of 40. No files were reviewed.";
  await page.goto("/repositories/22");
  const row = page.getByRole("link", { name: /Add retry handling/ }).locator("xpath=ancestor::*[@data-pull-row]");
  await expect(row.getByText("Skipped", { exact: true })).toBeVisible();
  await expect(row).not.toContainText(reason);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText(reason);
  await page.getByRole("tab", { name: /Run history/ }).click();
  await expect(page.getByRole("link", { name: /GitHub: No comments published/ })).toContainText(reason);
  await page.getByRole("link", { name: "View run details", exact: true }).click();
  await expect(page.getByRole("region", { name: "Review outcome" })).toHaveCount(0);
  await expect(page.getByText("No comments published", { exact: true })).toBeVisible();
  await expect(page.getByText("not applicable", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry GitHub publication" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/file-limit-skipped-${info.project.name}.png`, fullPage: true });
  await page.goto("/pulls/pr");
  await page.getByRole("link", { name: "Adjust review settings" }).click();
  const settings = page.getByRole("form", { name: "Repository settings" });
  await expect(settings).toBeVisible();
  await expect(settings.getByLabel("Maximum reviewable files", { exact: true })).toBeVisible();
  await expect(settings.getByRole("checkbox", { name: "Unlimited maximum reviewable files" })).toBeVisible();
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
      pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
    candidates: [
      { id: "repeat", snapshot: { ...finding, content: "Same defect with different wording." },
        status: "completed", result: null, finding_id: "finding", is_new: false, error: null, assessment_only: false, retryable: false },
      { id: "uncertain", snapshot: { path: "frontend/src/index.css", start_line: 315,
          content: "Tailwind might fail to process this rule." }, status: "completed", finding_id: null, assessment_only: false, retryable: false,
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
  await expect(page.getByText("No additional GitHub comments were needed.", { exact: true })).toHaveCount(0);
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
  await expect(linked.getByRole("tab", { name: "Saved evidence" })).toHaveAttribute("data-state", "active");
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
    finding_id: index < 3 ? `finding-${index}` : null, is_new: index < 3, error: null, assessment_only: false, retryable: index === 9,
  }));
  const writes = await mock(page, { candidates, assessment: {
    candidates: 10, new_findings: 3, repeated_observations: 0, rejected_candidates: 5,
    withheld_candidates: 2, pending: 0, incomplete: 2, failed: 0, skipped: 0, published_comments: 3,
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
    { label: "Awaiting assessment", description: "Queued for assessment.", status: "queued" },
    { label: "Assessing", description: "Checking evidence before accepting this proposed finding.", status: "running" },
    { label: "Assessing", description: "Checking evidence before accepting this proposed finding.", status: "running", progress_phase: "validating" as const },
  ];
  const writes = await mock(page, { candidates: outcomes.map((outcome, index) => ({
    id: `result-${index}`, snapshot: { path: `src/result-${index}.ts`, content: `Proposal ${index}` },
    status: "completed", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false, ...outcome,
  })), assessment: { candidates: 7, new_findings: 1, repeated_observations: 1, rejected_candidates: 1, withheld_candidates: 1,
    pending: 3, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 } });
  await page.goto("/runs/run?tab=assessment");
  for (const [index, { label, description, status }] of outcomes.entries()) {
    const card = page.getByRole("button", { name: new RegExp(`Proposal ${index}`) });
    const badge = card.getByText(label, { exact: true });
    if (status === "running") await expect(badge).toHaveAttribute("data-variant", "default");
    if (status === "queued") await expect(badge).toHaveAttribute("data-variant", "outline");
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

for (const merged of [false, true]) for (const failure of ["technical", "history conflict", "legacy history conflict"] as const) {
test(`${merged ? "merged PR " : ""}${failure} assessment retry preserves its previous attempt and polls only the candidate`, async ({ page }, info) => {
  await page.clock.install();
  const error = failure === "technical" ? "Assessment timed out" : failure === "history conflict"
    ? "Finding history changed during assessment. Retry this assessment to check the updated history."
    : "The pull request or finding history changed during assessment; this proposed finding was withheld from GitHub. Check the latest review.";
  const candidate: Schema["CandidateOut"] = {
    id: "failed", snapshot: { path: "src/retry.ts", content: "Proposed retry defect." },
    status: failure === "technical" ? "failed" : "skipped", result: null, finding_id: null, is_new: false, error, assessment_only: false, retryable: true,
    failure_reason: failure === "history conflict" ? "history_changed" : null,
  };
  const writes = await mock(page, { candidates: [candidate],
    overview: { state: merged ? "merged" : "open" },
    run: { engine: "mortis", status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
  });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let retries = 0;
  await page.route("**/api/v1/runs/run/candidates/failed/retry", async (route) => {
    expect(route.request().method()).toBe("POST");
    retries++;
    await pending;
    candidate.attempts = [{ status: candidate.status, error: candidate.error, failure_reason: candidate.failure_reason, result: null,
      usage: { tokens: 25 }, finished_at: at }];
    candidate.status = "queued";
    candidate.error = null;
    candidate.failure_reason = null;
    candidate.retryable = false;
    candidate.assessment_only = merged;
    await route.fulfill({ status: 202, json: candidate });
  });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: /Assessment/ }).click();
  await page.getByRole("button", { name: /Proposed retry defect/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText(merged
    ? "Retry checks the original commit using the same connection and AI quota. No findings or comments will be created."
    : "Retry uses the same connection and AI quota.");
  if (failure !== "technical") await expect(sheet.getByRole("alert")).toContainText(failure === "history conflict" ? "Finding history changed" : "Assessment context changed");
  await sheet.screenshot({ path: `../.impeccable/review/retry-assessment-${merged ? "merged-" : ""}${failure.replaceAll(" ", "-")}-${info.project.name}.png` });
  await sheet.getByRole("button", { name: "Retry assessment", exact: true }).click();
  await expect(sheet.getByRole("button", { name: "Retrying assessment…" })).toBeDisabled();
  release();
  await expect(sheet).toContainText("Awaiting assessment");
  await sheet.locator("summary", { hasText: "Previous attempts (1)" }).click();
  await expect(sheet).toContainText("25 tokens used or reserved.");
  await expect(sheet).toContainText(error);
  candidate.status = "completed";
  candidate.result = { verdict: merged ? "confirmed" : "uncertain", relationship: merged ? "new" : "uncertain", finding_id: null,
    explanation: merged ? "The original commit confirms this issue." : "The supplied evidence is insufficient.", cause: "", trigger: "", consequence: "",
    history_explanation: "", contradicts_prior: false, changed_conditions: "", suggestion_supported: false, evidence: [] };
  await page.clock.fastForward(5000);
  await expect(sheet).toContainText(candidate.result.explanation);
  if (merged) {
    await expect(sheet).toContainText("Confirmed at original commit");
    await expect(sheet).toContainText("Findings, comments, and resolution states are unchanged.");
    await sheet.screenshot({ path: `../.impeccable/review/assessment-merged-completed-${failure.replaceAll(" ", "-")}-${info.project.name}.png` });
  }
  await expect(sheet.getByRole("button", { name: "Retry assessment", exact: true })).toHaveCount(0);
  expect(retries).toBe(1);
  expect(writes).toEqual([]);
  await noOverflow(page);
});
}

test("assessment explains when it is waiting for verification history", async ({ page }, info) => {
  await mock(page, { candidates: [{ id: "waiting", snapshot: { content: "Queued proposal." },
    status: "queued", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false }],
    run: { status: "running", publication: "pending", finished_at: null, stages: {
      ocr: { status: "completed", tokens: 100 }, assessment: { status: "waiting", tokens: 0, waiting_for: "verification" },
      verification: { status: "running", tokens: 0 }, summary: { status: "completed", tokens: 25 },
    } }, assessment: { candidates: 1, pending: 1, incomplete: 0, withheld_candidates: 0, rejected_candidates: 0,
      new_findings: 0, repeated_observations: 0, failed: 0, skipped: 0, published_comments: 0 },
  });
  await page.goto("/runs/run?tab=assessment&candidate=waiting");
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet.getByRole("status")).toHaveText("Waiting for fix verification to finish updating finding history.");
  await expect(sheet.getByRole("button", { name: "Retry assessment", exact: true })).toHaveCount(0);
  await sheet.getByRole("button", { name: "Close", exact: true }).first().click();
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details).toContainText("Waiting for fix verification to finish updating finding history.");
  await details.screenshot({ path: `../.impeccable/review/assessment-waiting-${info.project.name}.png` });
  await noOverflow(page);
});

test("assessment retry shows server errors without discarding the failed attempt", async ({ page }) => {
  await mock(page, { candidates: [{ id: "failed", snapshot: { content: "Failed proposal." },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", assessment_only: false, retryable: true }],
    run: { engine: "mortis", status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
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
    status: "failed", result: null, finding_id: null, is_new: false, error: "Assessment timed out", assessment_only: false, retryable: true }],
    run: { engine: "mortis", status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
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
    pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 }, candidates: [{ id: "proposal", snapshot: { content: "Proposed links.\n\n"
    + [...unsafe, ...safe].map(([name, href]) => `[${name}](${href})`).join("\n\n")
    + '\n\n<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>',
  }, status: "failed", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false }] });
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
    status: "running", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false }];
  await mock(page, { candidates, run: { status: "running", finished_at: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
      pending: 1, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 } });
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
  candidates[0].progress_phase = "validating";
  const parentPoll = page.waitForResponse((response) => response.url().endsWith("/api/v1/runs/run"));
  await page.clock.runFor(6000);
  await (await parentPoll).finished();
  await page.clock.runFor(50);
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
      pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
    candidates: [
      { id: "failed", snapshot: { path: "backend/client.py", start_line: 12, content: "Connection failure." },
        status: "failed", result: null, finding_id: null, is_new: false, assessment_only: false, retryable: true,
        error: "Assessment timed out; this candidate was withheld." },
      { id: "confirmed", snapshot: { path: "backend/retry.py", start_line: 7, content: "Retry has no limit." },
        status: "completed", finding_id: "finding", is_new: true, error: null, assessment_only: false, retryable: false, result: {
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
  await expect(code.locator("code")).toHaveText(source);
  expect(await code.locator('[data-slot="code-line-numbers"]').textContent()).toBe("7\n8");
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
  { name: "gateway already omitted arguments", error: "AI result delivery failed: task_done did not include the required DONE state and result object. The gateway returned incomplete or malformed completion arguments.",
    title: "AI result delivery failed", message: "The gateway response already contained incomplete or malformed arguments." },
  { name: "OCR changed gateway arguments", error: "AI result delivery failed: task_done did not include the required DONE state and result object. OCR's parsed completion differs from the gateway response.",
    title: "AI result delivery failed", message: "The engine changed or lost data from the gateway's completion." },
  { name: "gateway comparison unavailable", error: "AI result delivery failed: task_done did not include the required DONE state and result object. Gateway/OCR comparison was unavailable; the origin could not be determined.",
    title: "AI result delivery failed", message: "The gateway and engine could not be compared, so the origin is unknown." },
  { name: "incomplete completion call", error: 'The AI did not finish checking this finding. Technical details: {"status":"success","result_error":"The AI did not submit a complete structured result. task_done did not include the required DONE state and result object."}',
    title: "AI result delivery failed", message: "The AI completion call omitted the required DONE state or structured result." },
  { name: "multiple completion calls", error: 'AI result delivery failed: The response must submit exactly one complete task_done result. Technical details: {"result_count":2}',
    title: "AI result delivery failed", message: "The AI did not submit exactly one complete structured result." },
  { name: "malformed completion arguments", error: "AI result delivery failed: task_done arguments are not valid JSON.",
    title: "AI result delivery failed", message: "The AI completion call contained invalid JSON arguments." },
  { name: "unknown usage after incomplete delivery", error: 'The review engine did not report valid token usage. Technical details: {"result_error":"task_done did not include the required DONE state and result object."}',
    title: "Token usage could not be verified", message: "The AI's token usage was unavailable" },
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
        pending: 0, incomplete: 1, failed: 0, skipped: 0, published_comments: 0 },
      candidates: [{ id: "failed", snapshot: { path: "frontend/src/settings.tsx", start_line: 525, content: "Proposed finding." },
        status: "failed", result: null, finding_id: null, is_new: false, error: failure.error, assessment_only: false, retryable: true }],
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
    const displayed = failure.error.replace("Gateway/OCR", "Gateway and engine")
      .replace(/^OCR\b/, "The retired engine")
      .replace(/\bOCR\b/g, "retired engine");
    expect(await raw.textContent()).toBe(displayed);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async (value: string) => { document.body.dataset.copiedAssessment = value; },
      } });
    });
    await sheet.getByRole("button", { name: "Copy Assessment details", exact: true }).click();
    await expect(page.locator("body")).toHaveAttribute("data-copied-assessment", displayed);
    await expect(page.getByText("Assessment details copied", { exact: true })).toBeVisible();
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async () => { throw new Error("Clipboard denied"); },
      } });
    });
    await sheet.getByRole("button", { name: "Copy Assessment details", exact: true }).click();
    await expect(page.getByText("Select the text in Technical details and copy it manually.", { exact: true })).toBeVisible();
    await expect(sheet.getByRole("button", { name: "Copy Assessment details", exact: true })).toBeEnabled();
    expect(await raw.textContent()).toBe(displayed);
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
    status: "running", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false,
  };
  const currentRun: Partial<Run> = { status: "running", publication: "pending", publication_error: null };
  const requests = await mock(page, {
    run: currentRun, canWrite: false, candidates: [candidate],
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0, pending: 1, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 },
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
  await expect(page.locator("main h1")).toHaveText("Review run");
  const settledReads = { list: listReads, detail: detailReads };
  await page.clock.runFor(10_000);
  expect({ list: listReads, detail: detailReads }).toEqual(settledReads);
  await expect(sheet).toContainText("Not supported");
  const code = sheet.getByRole("region", { name: "Code snippet" });
  await expect(code.nth(0).locator("code")).toHaveText(source);
  await expect(code.nth(1).locator("code")).toHaveText("export const unchanged = true;");
  expect(await code.nth(0).locator('[data-slot="code-line-numbers"]').textContent()).toBe("10\n11");
  await expect(code.nth(1).locator('[data-slot="code-line-numbers"]')).toHaveText("10");
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
      id: "linked", snapshot: finding, status: "running", result: null, assessment_only: false, retryable: false,
      finding_id: "finding", is_new: true, error: null,
    };
    await mock(page, { candidates: [candidate], assessment: {
      candidates: 1, new_findings: 1, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
      pending: 1, incomplete: 0, failed: 0, skipped: 0, published_comments: 0,
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
    candidate.progress_phase = "validating";
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
    status: "completed", result: null, finding_id: index === 22 ? "finding" : null, is_new: index === 22, error: null, assessment_only: false, retryable: false,
  }));
  const writes = await mock(page, { candidates,
    assessment: { candidates: 23, new_findings: 1, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 22, pending: 0, incomplete: 0, failed: 0, skipped: 0, published_comments: 1 },
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
  await list.getByRole("button", { name: "Next assessments" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/assessment-pagination-${info.project.name}.png`, animations: "disabled" });
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(cards).toHaveCount(10);
  await expect(list.getByText("Page 2 of 3 · 23 total", { exact: true })).toBeVisible();
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
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(cards).toHaveCount(2);
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
    status: "completed", result: null, finding_id: null, is_new: false, error: null, assessment_only: false, retryable: false,
  }));
  await mock(page, { candidates, assessment: { candidates: 21, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 21, pending: 0, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 } });
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
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(list.getByRole("button", { name: "Retry assessments", exact: true })).toBeVisible();
  await expect(cards).toHaveCount(10);
  failure = 0;
  await list.getByRole("button", { name: "Retry assessments", exact: true }).click();
  await expect(cards).toHaveCount(10);
  failure = 403;
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(list.getByRole("alert")).toHaveText("Repository access revoked");
  await expect(cards).toHaveCount(0);
});

for (const status of ["running", "completed"]) {
  test(`empty candidate assessment explains ${status} review`, async ({ page }) => {
    await mock(page, {
      run: { ...run, status, publication: status === "running" ? "pending" : "no_new_comments", publication_error: null },
      assessment: { candidates: 0, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 0,
        pending: 0, incomplete: 0, failed: 0, skipped: 0, published_comments: 0 },
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
  await page.goto("/pulls/pr");
  const outcome = page.getByRole("region", { name: "Review outcome" });
  await expect(outcome).toContainText("Ask your administrator to adjust the repository settings.");
  await expect(outcome.getByRole("link", { name: "Adjust review settings" })).toHaveCount(0);
  await outcome.getByRole("link", { name: "View run details", exact: true }).click();
  await expect(outcome).toHaveCount(0);
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
    nextStep: "Start a new Mortis review using a shared API connection",
    fileReason: "Delegated context budget exceeded; reduce the supplied context.",
  },
  {
    name: "authentication",
    state: { status: "blocked", blocked_reason: "authentication", owner_id: 7 },
    summary: "The AI connection needs authentication.",
    nextStep: "Ask the administrator to check the shared connection credentials before retrying.",
  },
  {
    name: "quota",
    state: { status: "blocked", blocked_reason: "quota", owner_id: 8 },
    summary: "The AI connection reached its usage limit.",
    nextStep: "Retry with the same shared connection when usage becomes available.",
  },
  {
    name: "busy connection",
    state: { status: "queued", blocked_reason: "busy" },
    summary: "The selected AI connection is busy.",
    nextStep: "This review will retry automatically when the connection is available.",
  },
  {
    name: "unavailable service",
    state: { status: "queued", blocked_reason: "unavailable" },
    summary: "The review service is unavailable.",
    nextStep: "Ask the administrator to check the review worker and selected shared API connection.",
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

for (const publication of ["not_applicable", "failed"] as const) {
  for (const path of ["/", "/repositories/22"]) {
    test(`cancelled review rows omit cancellation explanation on ${path} with ${publication}`, async ({ page }, info) => {
      const currentRun = { ...run, status: "cancelled", publication };
      await mock(page, { run: currentRun });
      await page.route("**/api/v1/pulls?**", (route) => {
        const data = repositoryPage([{ ...pr, latest_run: currentRun }], new URLSearchParams("state=all"));
        return route.fulfill({ json: { ...data, groups: [{ ...data.groups[0], group: "all" }] } });
      });
      await page.goto(path);
      const row = page.locator("[data-pull-row]").filter({ has: page.getByRole("link", { name: pr.title, exact: true }) });
      await expect(row.getByText("Cancelled", { exact: true })).toBeVisible();
      await expect(row).not.toContainText("This review was cancelled before it finished.");
      if (publication === "failed" && path === "/") await expect(row).toContainText("Publication failed");
      else await expect(row).not.toContainText("Publication failed");
      await noOverflow(page);
      if (publication === "not_applicable") {
        await page.screenshot({ path: `/private/tmp/mortis-cancelled-row-${path === "/" ? "inbox" : "repository"}-${info.project.name}.png`, fullPage: true });
      }
    });
  }
}

for (const example of feedbackCases) {
  test(`review feedback: ${example.name}`, async ({ page }, info) => {
    await mock(page, { run: { engine: "mortis", publication: "not_applicable", publication_error: null, ...example.state } });
    await page.goto("/repositories/22");
    const row = page.locator("[data-pull-row]").filter({ has: page.getByRole("link", { name: pr.title, exact: true }) });
    await expect(row).toBeVisible();
    await expect(row).not.toContainText(example.summary);
    if (example.name === "empty scope") await page.screenshot({ path: `/private/tmp/mortis-pr-list-${info.project.name}.png`, fullPage: true });
    await row.getByRole("link", { name: pr.title, exact: true }).click();
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText(example.summary);
    await expect(outcome).toContainText(example.nextStep);
    if (example.state.status === "blocked") {
      await expect(outcome.getByRole("link", { name: "Manage AI connections" })).toHaveAttribute("href", "/settings?tab=connections");
    }
    await outcome.getByRole("link", { name: "View run details", exact: true }).click();
    await expect(outcome).toHaveCount(0);
    const publication = page.getByRole("region", { name: "GitHub publication", exact: true });
    await expect(publication.locator(":scope > p")).toHaveCount(0);
    await publication.getByText("No comments published", { exact: true }).hover();
    await expect(page.getByText(/No review comments were published for this run/)).toHaveCount(0);
    if (example.fileReason) {
      await page.getByRole("tab", { name: "Coverage", exact: true }).click();
      const files = page.getByRole("region", { name: "Files not reviewed", exact: true });
      await expect(files.getByText(example.fileReason, { exact: true })).toBeHidden();
      await files.locator("summary", { hasText: "Files not reviewed" }).click();
      if (example.name === "token budget") {
        await expect(files.getByText("The analysis could not continue within its configured limits. Review the token budget and prompt limit before retrying.", { exact: true })).toBeVisible();
        await expect(files.getByText(example.fileReason, { exact: true })).toBeHidden();
        await files.getByText("Technical details", { exact: true }).click();
      }
      await expect(files.getByText(example.fileReason, { exact: true })).toBeVisible();
    }
    if (example.state.status === "blocked") {
      await expect(page.getByRole("button", { name: "Resume review" })).toHaveCount(example.state.owner_id === 7 ? 1 : 0);
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
  await page.goto("/repositories/22");
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
    await expect(
      page.getByRole("heading", {
        name: configured ? "Repositories" : "Give your reviews a home.",
        exact: true,
      }),
    ).toBeVisible();
    const destination = entry;
    await expect(page).toHaveURL(`http://127.0.0.1:4178${destination}`);
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
  await expect(breadcrumb.getByRole("link", { name: "Inbox", exact: true })).toHaveCount(0);
  await noOverflow(page);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await page.screenshot({ path: `../.impeccable/review/header-${info.project.name}-${theme}.png`, animations: "disabled" });
  }
  const parent = await breadcrumbAncestor(page, "#148");
  await expect(parent).toHaveAttribute("href", "/pulls/pr");
  await parent.press("Enter");
  await expect(current).toHaveText("#148");
  await (await breadcrumbAncestor(page, "payments")).click();
  await expect(page).toHaveURL("/repositories/22/overview");
  await expect(current).toHaveText("Overview");
  await page.getByRole("link", { name: "Code Mortis home", exact: true }).click();
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  await expect(breadcrumb).toHaveCount(0);
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
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  await expect(breadcrumb).toHaveCount(0);
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
  await expect(await breadcrumbAncestor(page, fullName.split("/")[1])).toHaveAttribute("href", "/repositories/22/overview");
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
      ? ["AI connections", "API tokens", "MCP access", "GitHub", "Team", "Appearance"] : ["AI connections", "API tokens", "MCP access", "Appearance"]);
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
  await expect(page.getByRole("region", { name: "Code snippet" }).locator('[data-slot="code-line-numbers"]')).toHaveText("1");
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
    pending: 0, incomplete: 9, failed: 0, skipped: 0, published_comments: 0 } });
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
      pending: 0, incomplete: 5, failed: 0, skipped: 0, published_comments: 0,
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
  await expect(page.getByRole("tablist").getByRole("tab")).toHaveText(["Findings 1", "Resolved 0", /^Assessment\s*$/, "Coverage", "Details", "Activity 3"]);
  await noOverflow(page);
});


test("automatic resolution is opt-in and available for manual reviews", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings");
  const automatic = page.getByRole("switch", { name: "Automatically review new commits", exact: true });
  const resolution = page.getByRole("switch", { name: "Automatically resolve verified fixes", exact: true });
  await expect(resolution).not.toBeChecked();
  await automatic.uncheck();
  await expect(resolution).toBeEnabled();
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
  await page.goto("/repositories/22");
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
  await page.getByRole("article", { name: "acme" }).getByRole("button", { name: "Actions for acme" }).press("Enter");
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
      expect(nav!.width).toBe(192);
      expect(content!.x - nav!.x - nav!.width).toBe(32);
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
  await expect(personal).toHaveCount(0);
  await expect(shared.getByRole("article").first()).toHaveAccessibleName(longName);
  await expect(shared.getByRole("article").first().locator('[data-slot="badge"]')).toHaveText("connected");
  await expect(shared.getByRole("article").first().locator(".lucide-check")).toBeVisible();
  await expect(shared).not.toContainText("Model completed a tool-call round trip.");
  await expect(shared.locator("time")).toHaveCount(0);
  await expect(shared).toContainText("Not tested");
  await expect(shared).toContainText("openai · openai");
  const logo = shared.getByRole("article").first().locator(".settings-identity img");
  await expect(logo).toHaveAttribute("alt", "");
  await expect.poll(() => logo.evaluate((element: HTMLImageElement) => element.complete && element.naturalWidth > 0)).toBe(true);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/shared-connections-${info.project.name}.png`, fullPage: true });
  const apiActions = shared.getByRole("button", { name: "Actions for Untested API", exact: true });
  await apiActions.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveText(["Edit", "Test connection", "Delete"]);
  await page.keyboard.press("Escape");
  await expect(apiActions).toBeFocused();

});

test("shared API logos cover the provider catalog with custom and unknown fallbacks", async ({ page }, info) => {
  await mock(page);
  const catalog: { name: string; displayName: string; protocol: string }[] = JSON.parse(
    await readFile(new URL("../../backend/code_mortis/integrations/providers.json", import.meta.url), "utf8"),
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
    await readFile(new URL("../../backend/code_mortis/integrations/providers.json", import.meta.url), "utf8"),
  );
  await page.route("**/api/v1/providers", (route) => route.fulfill({ json: catalog }));
  await page.goto("/settings?tab=connections");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  const dialog = page.getByRole("dialog");
  const provider = dialog.getByRole("combobox", { name: "Provider", exact: true });
  const providerIcon = provider.locator("..").locator('[data-align="inline-start"]');
  await dialog.getByLabel("Connection name", { exact: true }).focus();
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(page.locator("html")).toHaveClass(colorScheme === "dark" ? "dark" : "");
    await expect(providerIcon.locator("img")).toHaveAttribute("src", "/brands/openai.svg");
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

});

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
  result = "Mortis connection test failed. Check credentials, protocol, and model.";
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
  const progress = page.getByRole("banner").getByRole("progressbar", { name: "Loading workspace" });
  await expect(progress).toBeVisible();
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "Settings sections" })).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toContainText("Connections could not be loaded");
  await expect(progress).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Shared API connections" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Your subscriptions" })).toHaveCount(0);
  await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [] }));
  await page.reload();
  await expect(page.getByRole("region", { name: "Shared API connections" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Your subscriptions" })).toHaveCount(0);
});

for (const role of ["admin", "member"] as const) {
  test(`empty connection groups show the next actions allowed for ${role}s`, async ({ page }, info) => {
    await mock(page, { role });
    await page.route("**/api/v1/connections", (route) => route.fulfill({ json: [] }));
    await page.goto("/settings?tab=connections");
    const shared = page.getByRole("region", { name: "Shared API connections", exact: true });
    const subscriptions = page.getByRole("region", { name: "Your subscriptions", exact: true });
    await expect(shared.getByRole("heading")).toHaveText("Shared API connections0");
    await expect(subscriptions).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/empty-connections-${role}-${info.project.name}.png`, fullPage: true });
    const dialog = page.getByRole("dialog");
    const addApi = shared.getByRole("button", { name: "Add API connection", exact: true });
    if (role === "admin") {
      await addApi.click();
      await expect(dialog.getByRole("combobox", { name: "Connection type" })).toHaveCount(0);
      await expect(dialog.getByLabel("API key", { exact: true })).toBeVisible();
      await dialog.getByLabel("API key", { exact: true }).focus();
      await page.keyboard.press("Escape");
      await expect(addApi).toBeFocused();
    } else {
      await expect(addApi).toHaveCount(0);
      await expect(shared).toContainText("An administrator can add an API connection for your team.");
    }
    await expect(page.getByRole("button", { name: "Add connection", exact: true })).toHaveCount(role === "admin" ? 1 : 0);
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

test("closing member menu cannot steal focus from the dialog role picker", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], failed_jobs: [], public_url: "https://reviews.example.com",
    members: [{ id: 7, login: "carlos", role: "admin" }, { id: 8, login: "sam", role: "admin" }],
  } }));
  await page.goto("/settings?tab=team");
  await page.addStyleTag({ content: '[data-slot="dropdown-menu-content"][data-state="closed"] { animation-duration: 10s !important; animation-play-state: paused !important; }' });
  await page.getByRole("button", { name: "Actions for @sam" }).click();
  const item = page.getByRole("menuitem", { name: "Edit role", includeHidden: true });
  await item.click();
  const role = page.getByRole("dialog").getByRole("combobox", { name: "Role", exact: true });
  await role.fill("Member");
  await expect(page.getByRole("menu", { includeHidden: true })).toHaveAttribute("data-state", "closed");
  await item.dispatchEvent("pointerout", { pointerType: "mouse" });
  await expect(role).toBeFocused();
  await page.getByRole("option", { name: "Member", exact: true }).click();
  await expect(role).toHaveValue("Member");
});

test("Team avatars match the navbar and use initials for missing or broken images", async ({ page }, info) => {
  await mock(page);
  const ownAvatar = "/fixtures/own-avatar.svg";
  const memberAvatar = "/fixtures/member-avatar.svg";
  await page.route("**/fixtures/*-avatar.svg", (route) => route.fulfill({
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#245c47"/></svg>',
  }));
  await page.route("**/fixtures/missing.png", (route) => route.fulfill({ status: 404 }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    id: 7, login: "carlos", role: "admin", avatar_url: ownAvatar, csrf: "csrf-from-session",
  } }));
  await page.route("**/api/v1/settings", (route) => route.fulfill({ json: {
    github_app: {}, installations: [], failed_jobs: [], public_url: "https://reviews.example.com",
    members: [
      { id: 7, login: "carlos", role: "admin" },
      { id: 8, login: "sam", role: "member", avatar_url: memberAvatar },
      { id: 9, login: "invitee", role: "member" },
      { id: 10, login: "observer", role: "member", avatar_url: "/fixtures/missing.png" },
    ],
  } }));
  await page.goto("/settings?tab=team");
  const team = page.getByRole("region", { name: "Team", exact: true });
  const avatar = (login: string) => team.locator(".settings-member-row")
    .filter({ has: page.getByText(`@${login}`, { exact: true }) }).locator('[data-slot="avatar"]');
  await expect(page.getByRole("button", { name: "Account menu for @carlos" }).locator("img")).toHaveAttribute("src", ownAvatar);
  await expect(avatar("carlos").locator("img")).toHaveAttribute("src", ownAvatar);
  await expect(avatar("sam").locator("img")).toHaveAttribute("src", memberAvatar);
  await expect(avatar("invitee")).toHaveText("IN");
  await expect(avatar("observer")).toHaveText("OB");
  await noOverflow(page);
  await team.screenshot({ path: `../.impeccable/review/team-avatars-${info.project.name}.png` });
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

for (const [field, invalid, message, advancedMessage] of [
  ["Extra headers JSON", '{"Authorization":"private-provider-key",', "Headers must contain valid JSON. Check quotes, commas, and braces, then try again.", "Extra headers JSON in the Advanced tab must contain valid JSON. Check quotes, commas, and braces, then try again."],
  ["Provider options JSON", "[]", "Options must be a JSON object.", "Provider options JSON in the Advanced tab must be a JSON object."],
] as const) {
  test(`new connection save reveals invalid ${field} inside collapsed advanced settings`, async ({ page }, info) => {
    const requests = await mock(page);
    await page.goto("/settings?tab=connections");
    await page.getByRole("button", { name: "Add connection", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Connection name").fill("New connection");
    await dialog.getByLabel("API key", { exact: true }).fill("draft-key");
    await chooseOption(page, dialog.getByLabel("Model", { exact: true }), "Enter a custom model ID");
    await dialog.getByLabel("Custom model ID").fill("custom-model");
    const summary = dialog.getByText("Advanced provider settings", { exact: true });
    const details = dialog.locator("details").filter({ hasText: "Advanced provider settings" });
    await summary.click();
    await dialog.getByLabel(field, { exact: true }).fill(invalid);
    await summary.click();
    await expect(details).not.toHaveAttribute("open");
    await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
    await expect(details).toHaveAttribute("open", "");
    await expect(summary).toBeFocused();
    await expect(summary).toBeInViewport();
    await expect(dialog.getByRole("alert")).toHaveText(message);
    await expect(dialog.getByRole("alert")).not.toContainText("private-provider-key");
    await expect(dialog.getByLabel(field, { exact: true })).toBeVisible();
    await expect(dialog.getByLabel(field, { exact: true })).toHaveValue(invalid);
    expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
    await noOverflow(page);
    await page.screenshot({ path: info.outputPath("new-connection-json-validation.png") });
    await dialog.getByLabel(field, { exact: true }).fill("{}");
    await dialog.getByRole("button", { name: "Save connection", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(requests.filter((request) => request.path === "/connections")).toHaveLength(1);
  });

  test(`model refresh identifies Advanced ${field} while keeping form errors and the current tab`, async ({ page }, info) => {
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
    await expect(dialog.getByRole("region", { name: "Default model", exact: true }).getByRole("alert")).toHaveText(advancedMessage);
    await expect(dialog.getByRole("alert").filter({ hasText: "private-provider-key" })).toHaveCount(0);
    expect(requests.filter((request) => request.path === "/connections/models")).toHaveLength(0);
    await noOverflow(page);
    await page.screenshot({ path: info.outputPath("model-advanced-json-error.png") });
    await dialog.getByRole("tab", { name: "Advanced", exact: true }).click();
    await dialog.getByLabel(field, { exact: true }).fill("{}");
    await dialog.getByRole("tab", { name: "General", exact: true }).click();
    await dialog.getByRole("button", { name: /^(Load|Refresh) models$/ }).click();
    await expect(dialog.getByRole("region", { name: "Default model", exact: true }).getByRole("status"))
      .toHaveText("Models reported by this endpoint. Tool support is checked by Test connection.");
    await expectOption(page, dialog.getByLabel("Model", { exact: true }), "Next model");
    await expect(dialog.getByRole("region", { name: "Default model", exact: true }).getByRole("alert")).toHaveCount(0);
    await expect(dialog.getByRole("alert").filter({ hasText: saveError })).toBeVisible();
    expect(requests.filter((request) => request.path === "/connections/models")).toHaveLength(1);
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
      await expect(dialog.getByRole("alert")).toHaveText(advancedMessage);
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
  await expect(dialog.getByRole("alert")).toHaveText("Extra headers JSON in the Advanced tab must contain valid JSON. Check quotes, commas, and braces, then try again.");
  await expect(dialog.getByRole("alert")).not.toContainText("private-provider-key");
  expect(requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  await noOverflow(page);
  await dialog.getByRole("alert").scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("json-error.png"), fullPage: true });
});

test("review feedback shows recorded usage and the configured token limit", async ({ page }) => {
  await mock(page, { run: { status: "partial", config: { settings: { token_budget: 5000000 } },
    report: { summary: { budget_exceeded: true, total_tokens: 5232697 } } } });
  await page.goto("/pulls/pr");
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

test("pull detail searches evidence and separates severity, status, and run history", async ({ page }, info) => {
  await mock(page);
  const latest = { ...run, status: "completed", publication: "published", head_sha: "c".repeat(40) };
  await mockPullDetail(page, () => ({
    pull_request: { ...pr, draft: true }, repository: repo, can_write: true, runs: [latest, { ...run, id: "older" }],
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
  await expect(list.getByRole("region")).toHaveCount(1);
  await expect(list.getByRole("heading")).toHaveCount(0);
  await expect(list.getByRole("button")).toHaveCount(1);
  await expect(page.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toBeVisible();
  await page.getByLabel("Search findings", { exact: true }).fill("no matching issue");
  await expect(list.getByRole("button")).toHaveCount(0);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "High", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "No findings match these filters" })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page.getByLabel("Search findings", { exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: /Remove (Status|Severity|Type) filter/ })).toHaveCount(0);
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByRole("tab", { name: "Resolved 1", exact: true }).click();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toContainText("Validate the response");
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true }).getByRole("heading")).toHaveCount(0);
  await expect(list).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await noOverflow(page);
    await page.screenshot({ path: `/private/tmp/code-mortis-pr-resolved-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.getByRole("tab", { name: /Run history/ }).click();
    await expect(page.getByRole("link", { name: /automatic.*ccccccc/ })).toContainText("GitHub: Published");
    await expect(page.getByRole("button", { name: "Next run history" })).toHaveCount(0);
    await page.getByRole("tab", { name: "Resolved 1", exact: true }).click();
  }
  await page.getByRole("tab", { name: "Findings 1", exact: true }).click();
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
  await expect(page.getByRole("heading", { name: "No open findings", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  await page.getByRole("button", { name: "View resolved findings", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toContainText("resolved");
  await expect(page.getByRole("region", { name: "Open findings", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText("Coverage is incomplete");
  await page.getByLabel("Search resolved findings", { exact: true }).fill("no matches");
  await expect(page.getByRole("heading", { name: "No findings match these filters", exact: true })).toBeVisible();
  await page.clock.install();
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByLabel("Search resolved findings", { exact: true }).fill("pending search with no matches");
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page.getByLabel("Search resolved findings", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Search resolved findings", { exact: true })).toBeFocused();
  await page.clock.fastForward(500);
  await page.clock.resume();
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true })).toContainText("resolved");
  findings = [];
  await page.reload();
  await expect(page.getByRole("heading", { name: "No resolved findings", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings 0", exact: true }).click();
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
  await expect(history.getByRole("status")).toHaveText("Page 1 of 3 · 23 total");
  expect(requests).toEqual([1]);
  await noOverflow(page);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/run-history-pagination-${info.project.name}.png`, fullPage: true });
  await page.clock.fastForward(5000);
  await expect.poll(() => requests.length).toBe(2);
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(10);
  await expect(history.getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  const expandedRequests = requests.length;
  await page.clock.fastForward(15000);
  await expect.poll(() => requests.length).toBeGreaterThan(expandedRequests);
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(3);
  await expect(history.getByRole("status")).toHaveText("Page 3 of 3 · 23 total");
  await expect(history.getByRole("button", { name: /Next/ })).toBeDisabled();
  expect(requests).toContain(3);
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
  const more = history.getByRole("button", { name: "Next run history" });
  await expect(history.getByRole("link")).toHaveCount(10);
  hold = true;
  await page.clock.fastForward(5000);
  await expect.poll(() => waiting).toBe(1);
  await expect(more).toBeEnabled();
  await more.click();
  await expect(history.getByRole("button", { name: "Next run history" })).toBeDisabled();
  hold = false;
  release();
  await expect(history.getByRole("link")).toHaveCount(10);
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
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByRole("button", { name: "Retry run history" })).toBeVisible();
  await expect(history.getByRole("link")).toHaveCount(10);
  await expect(history.getByRole("status")).toHaveText("Page 1 of 3 · 23 total");
  failure = 0;
  await history.getByRole("button", { name: "Retry run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(10);
  failure = 403;
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByText("Repository access revoked")).toBeVisible();
  await expect(history.getByRole("link")).toHaveCount(0);
});

test("run history replaces shifted pages and preserves the server total and cursor", async ({ page }) => {
  await mock(page);
  const runs = Array.from({ length: 23 }, (_, index) => ({ ...run, id: `history-${index}` }));
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs, findings: [] }));
  await page.goto("/pulls/pr");
  await page.getByRole("tab", { name: /Run history/ }).click();
  const history = page.getByRole("tabpanel");
  await expect(history.getByRole("link")).toHaveCount(10);
  runs.unshift({ ...run, id: "new-run" });
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(10);
  await expect(history.getByRole("status")).toHaveText("Page 2 of 3 · 24 total");
  await history.getByRole("button", { name: "Next run history" }).click();
  await expect(history.getByRole("link")).toHaveCount(4);
  await expect(history.getByRole("status")).toHaveText("Page 3 of 3 · 24 total");
  await expect(history.getByRole("button", { name: /Next/ })).toBeDisabled();
});

test("pull detail keeps independent findings and resolved backend filters, ordering and pages", async ({ page }, info) => {
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
  await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, runs: [], findings }));
  let holdResolved = true;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/pulls/pr/findings?**", async (route) => {
    if (holdResolved && new URL(route.request().url()).searchParams.get("status") === "resolved") await held;
    await route.fallback();
  });
  await page.goto("/pulls/pr");
  const panel = page.getByRole("tabpanel");
  const open = page.getByRole("region", { name: "Open findings", exact: true });
  const resolved = page.getByRole("region", { name: "Resolved findings", exact: true });
  const dismissed = page.getByRole("region", { name: "Dismissed findings", exact: true });
  const rows = (section: typeof open) => section.locator('[data-slot="finding-card"]');
  const findingsTab = page.getByRole("tab", { name: "Findings 21", exact: true });
  const resolvedTab = page.getByRole("tab", { name: "Resolved 21", exact: true });
  await expect(findingsTab).toHaveAttribute("aria-selected", "true");
  await expect(rows(open)).toHaveCount(10);
  await expect(rows(open).first()).toContainText(/high/i);
  await expect(resolved).toHaveCount(0);
  await expect(dismissed).toHaveCount(0);
  expect(requests[0].getAll("status")).toEqual(["open"]);
  await open.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(open.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await resolvedTab.click();
  await expect(findingsTab).toHaveText("Findings 21");
  await expect(resolvedTab).toHaveText("Resolved 21");
  holdResolved = false;
  release();
  await expect(rows(resolved)).toHaveCount(10);
  await expect(rows(resolved).first()).toContainText(/high/i);
  await expect(resolved.getByRole("status")).toHaveText("Page 1 of 3 · 21 total");
  await expect(panel.getByRole("button", { name: "Sort ascending", exact: true })).toHaveAttribute("title", "Highest first");
  await expect(panel.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  await resolved.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(resolved.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await findingsTab.click();
  await expect(open.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await panel.getByLabel("Search findings", { exact: true }).fill("handler");
  await expect(open.getByRole("status")).toHaveText("Page 1 of 3 · 21 total");
  await panel.getByRole("button", { name: "Sort by Severity", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "File", exact: true }).click();
  await open.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(open.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await resolvedTab.click();
  await expect(resolved.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await expect(panel.getByLabel("Search resolved findings", { exact: true })).toHaveValue("");
  await expect(panel.getByRole("button", { name: "Sort by Severity", exact: true })).toBeVisible();
  for (const [filter, values] of [["Severity", ["High", "Medium"]], ["Type", ["Bug", "Performance"]]] as const) {
    await panel.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("menuitem", { name: filter, exact: true }).click();
    for (const value of values) await page.getByRole("checkbox", { name: new RegExp(`^${value}(?: |$)`) }).click();
    await page.keyboard.press("Escape");
  }
  await expect(resolved.getByRole("status")).toHaveText("Page 1 of 2 · 20 total");
  await panel.getByLabel("Search resolved findings", { exact: true }).fill("resolved");
  await expect.poll(() => requests.at(-1)?.get("q")).toBe("resolved");
  await panel.getByRole("button", { name: "Sort ascending", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.get("ordering")).toBe("severity");
  expect(requests.at(-1)?.getAll("status")).toEqual(["resolved"]);
  expect(requests.at(-1)?.getAll("severity")).toEqual(["high", "medium"]);
  expect(requests.at(-1)?.getAll("category")).toEqual(["bug", "performance"]);
  await resolved.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(resolved.getByRole("status")).toHaveText("Page 2 of 2 · 20 total");
  await page.reload();
  await expect(resolvedTab).toHaveAttribute("aria-selected", "true");
  await expect(resolved.getByRole("status")).toHaveText("Page 2 of 2 · 20 total");
  await expect(panel.getByLabel("Search resolved findings", { exact: true })).toHaveValue("resolved");
  await findingsTab.click();
  await expect(open.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await expect(panel.getByLabel("Search findings", { exact: true })).toHaveValue("handler");
  await expect(panel.getByRole("button", { name: "Sort by File", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: /^Edit Severity:/ })).toHaveCount(0);
  expect(requests.at(-1)?.getAll("severity")).toEqual([]);
  await page.goBack();
  await expect(resolvedTab).toHaveAttribute("aria-selected", "true");
  await expect(resolved.getByRole("status")).toHaveText("Page 2 of 2 · 20 total");
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/code-mortis-pr-resolved-pagination-${info.project.name}.png`, fullPage: true });
  await page.goForward();
  await expect(panel.getByRole("button", { name: /^Edit Status:/ })).toHaveCount(0);
  await panel.getByRole("button", { name: "Filters", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "Status", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dismissed).toHaveCount(0);
  await expect(page.getByRole("tablist")).toHaveCount(1);
  const dismissedTab = page.getByRole("tab", { name: "Dismissed 21", exact: true });
  await dismissedTab.click();
  await expect(rows(dismissed)).toHaveCount(10);
  await expect(dismissed.getByRole("status")).toHaveText("Page 1 of 3 · 21 total");
  expect(requests.at(-1)?.getAll("status")).toEqual(["dismissed"]);
  await dismissed.getByRole("button", { name: "Next dismissed findings", exact: true }).click();
  await expect(dismissed.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await findingsTab.click();
  await expect(open.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await dismissedTab.click();
  await expect(dismissed.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await panel.getByLabel("Search dismissed findings", { exact: true }).fill("dismissed");
  await expect(dismissed.getByRole("status")).toHaveText("Page 1 of 3 · 21 total");
  await dismissed.getByRole("button", { name: "Next dismissed findings", exact: true }).click();
  await page.reload();
  await expect(dismissedTab).toHaveAttribute("aria-selected", "true");
  await expect(dismissed.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await expect(panel.getByLabel("Search dismissed findings", { exact: true })).toHaveValue("dismissed");
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/code-mortis-pr-dismissed-${info.project.name}.png`, fullPage: true });
  await page.goto("/pulls/pr?status=dismissed&dismissed_page=2&q=handler&ordering=path");
  await expect(dismissedTab).toHaveAttribute("aria-selected", "true");
  await expect(dismissed.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await expect(panel.getByLabel("Search dismissed findings", { exact: true })).toHaveValue("handler");
  await expect(panel.getByRole("button", { name: "Sort by File", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(1);
});

for (const state of ["merged", "closed", "paused", "running", "viewer", "fork"] as const) {
  test(`pull detail handles review availability for ${state}`, async ({ page }, info) => {
    if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
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
    if (["running", "merged", "closed"].includes(state)) await expect(page.getByText(messages[state], { exact: true })).toHaveCount(0);
    else await expect(summary).toContainText(messages[state]);
    if (["viewer", "merged", "closed"].includes(state)) {
      await expect(summary.getByRole("button")).toHaveCount(0);
      await expect(page.getByLabel("Review connection")).toHaveCount(0);
    } else if (state === "fork") await expect(summary.getByRole("button", { name: "Run review" })).toBeEnabled();
    else {
      await expect(summary.getByRole("button", { name: state === "running" ? "Review in progress" : "Run review", exact: true })).toBeDisabled();
      await expect(summary.getByRole("button", { name: "Show options" })).toBeDisabled();
      await expect(page.getByLabel("Review connection")).toBeDisabled();
    }
    if (state === "merged" || state === "closed") {
      await expect(page.locator("#review-unavailable")).toHaveCount(0);
      await summary.screenshot({ path: `/private/tmp/code-mortis-review-availability-${state}-${info.project.name}.png` });
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
    await group.getByRole("button", { name: /Next/ }).click();
    await expect(group.getByRole("alert")).toContainText("Page unavailable");
    await expect(rows).toHaveCount(10);
    expect(requests.some((params) => params.get("page") === "2")).toBe(true);
    failure = 0;
    await group.getByRole("button", { name: /Retry/ }).click();
    await expect(rows).toHaveCount(10);
    await expect(group.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
    failure = 403;
    await group.getByRole("button", { name: /Next/ }).click();
    await expect(page.getByRole("alert")).toContainText("Access denied");
    await expect(rows).toHaveCount(0);
  });
}

test("repository configuration defaults bot imports off and saves explicit opt-in", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/settings");
  const bots = page.getByRole("switch", { name: "Include bot pull requests", exact: true });
  await expect(bots).not.toBeChecked();
  await bots.check();
  await page.screenshot({ path: `/private/tmp/bot-pr-setting-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ settings: { include_bot_prs: true } });
});

function auditCandidate(index=0): Schema["CandidateOut"] {
  return { id:`audit-${index}`,snapshot:{path:`src/audit-${index}.ts`,content:`Audit proposal ${index}`,severity:"high",category:"bug"},status:"running",result:null,finding_id:null,is_new:false,error:null,assessment_only: false, retryable:false };
}
function auditSummary(count=1): Schema["AssessmentSummary"] {
  return {candidates:count,new_findings:0,repeated_observations:0,rejected_candidates:0,withheld_candidates:0,pending:count,incomplete:0,failed: 0, skipped: 0, published_comments:0};
}
function finish(candidate: Schema["CandidateOut"]) {
  candidate.status="completed";
  candidate.result={verdict:"rejected",relationship:"new",finding_id:null,explanation:"Finished assessment evidence",cause:"",trigger:"",consequence:"",history_explanation:"",contradicts_prior:false,changed_conditions:"",suggestion_supported:false,evidence:[]};
}
async function ticks(page: Page,count=3) {
  for(let i=0;i<count;i++) { const response=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs/run")); await page.clock.fastForward(5000); await (await response).finished(); }
}

test("live refresh updates all loaded assessment cards with the summary",async({page})=>{
  await page.clock.install();
  const candidates=Array.from({length:11},(_,i)=>auditCandidate(i));
  const assessment=auditSummary(11);
  const writes=await mock(page,{candidates,assessment,run:{status:"running",finished_at:null}});
  let reads=0;
  await page.route("**/api/v1/runs/run/candidates?**",r=>{reads++;return r.fallback();});
  await page.goto("/runs/run?tab=assessment");
  const list=page.getByRole("region",{name:"Candidate assessment",exact:true});
  await expect(list.locator("[data-slot=finding-card]")).toHaveCount(10);
  await list.getByRole("button",{name:"Next assessments"}).click();
  await expect(list.locator("[data-slot=finding-card]")).toHaveCount(1);
  const before=reads;
  candidates.forEach(finish);assessment.pending=0;assessment.rejected_candidates=11;
  await ticks(page);
  expect(reads).toBe(before + 1);
  await expect(list.getByText("Not supported",{exact:true})).toHaveCount(1);
  await expect(list.locator("dl div").filter({hasText:"not supported"})).toContainText("11");
  expect(writes).toEqual([]);
});

test("live refresh catches final assessment statuses after parent completion",async({page})=>{
  await page.clock.install();
  const candidate=auditCandidate();const assessment=auditSummary();
  const state:Partial<Run>={status:"running",finished_at:null};
  const writes=await mock(page,{candidates:[candidate],assessment,run:state});
  let reads=0;await page.route("**/api/v1/runs/run/candidates?**",r=>{reads++;return r.fallback();});
  await page.goto("/runs/run");await expect(page.locator("main h1").locator("..").locator("[data-slot=badge]")).toHaveText("running");
  await page.clock.fastForward(2000);
  await page.getByRole("tab",{name:/Assessment/}).click();
  const list=page.getByRole("region",{name:"Candidate assessment",exact:true});
  await expect(list.getByText("Assessing",{exact:true})).toHaveCount(1);
  const before=reads;
  finish(candidate);assessment.pending=0;assessment.rejected_candidates=1;state.status="completed";
  const response=page.waitForResponse(r=>r.url().endsWith("/api/v1/runs/run"));
  await page.clock.fastForward(3100);await (await response).finished();
  await expect(page.locator("main h1").locator("..").locator("[data-slot=badge]")).toHaveText("completed");
  await ticks(page);
  expect(reads).toBe(before + 1);
  await expect(list.getByText("Not supported",{exact:true})).toHaveCount(1);
  expect(writes).toEqual([]);
});

test("live refresh recovers the open assessment drawer from a transient error",async({page})=>{
  await page.clock.install();const candidate=auditCandidate();
  await mock(page,{candidates:[candidate],assessment:auditSummary(),run:{status:"running"}});
  let reads=0;let fail=true;
  await page.route("**/api/v1/runs/run/candidates/audit-0",r=>{reads++;return fail?r.fulfill({status:503,json:{detail:"Temporary audit outage"}}):r.fallback();});
  await page.goto("/runs/run?tab=assessment");await page.getByRole("button",{name:/Audit proposal 0/}).click();
  const sheet=page.getByRole("dialog",{name:"Assessment details"});
  await expect(sheet.getByText("Assessing",{exact:true})).toHaveCount(1);
  await ticks(page,2);await expect(sheet.getByRole("alert")).toContainText("Temporary audit outage");
  fail=false;finish(candidate);const before=reads;await ticks(page);
  expect(reads).toBeGreaterThan(before);
  await expect(sheet.getByText("Finished assessment evidence",{exact:true})).toBeVisible();
});

test("live refresh checks the backend when opening a stale terminal assessment",async({page})=>{
  await page.clock.install();const candidates=Array.from({length:11},(_,i)=>auditCandidate(i));
  candidates[10].status="failed";candidates[10].error="Old failure";candidates[10].retryable=true;
  const assessment=auditSummary(11);
  await mock(page,{candidates,assessment,run:{status:"running"}});
  let reads=0;await page.route("**/api/v1/runs/run/candidates/audit-10",r=>{reads++;return r.fallback();});
  await page.goto("/runs/run?tab=assessment");await page.getByRole("button",{name:"Next assessments"}).click();
  await expect(page.locator("[data-slot=finding-card]")).toHaveCount(1);
  finish(candidates[10]);candidates[10].error=null;candidates[10].retryable=false;
  await page.getByRole("button",{name:/Audit proposal 10/}).click();
  const sheet=page.getByRole("dialog",{name:"Assessment details"});
  await expect(sheet.getByText("Finished assessment evidence",{exact:true})).toBeVisible();
  await ticks(page,4);
  expect(reads).toBe(1);await expect(sheet.getByText("Finished assessment evidence",{exact:true})).toBeVisible();
});

test("live refresh preserves historical Resolved after current status changes",async({page})=>{
  await page.clock.install();const resolved=[{...structuredClone(finding),status:"resolved"}];
  await mock(page,{resolved,run:{status:"completed"}});
  let reads=0;await page.route("**/api/v1/runs/run/findings?**",r=>{reads++;return r.fallback();});
  await page.goto("/runs/run?tab=resolved");
  const list=page.getByRole("region",{name:"Findings resolved by this run"});
  await expect(list.getByText("Resolved",{exact:true})).toHaveCount(1);
  resolved[0].status="open";const before=reads;await ticks(page);
  expect(reads).toBe(before);await expect(list.getByText("Resolved",{exact:true})).toHaveCount(1);
  await expect(list.getByText("Currently open",{exact:true})).toHaveCount(0);
});

test("live refresh recovers Resolved from an error after completion",async({page})=>{
  await page.clock.install();const resolved:Schema["FindingOut"][]=[];
  await mock(page,{resolved,run:{status:"completed"}});
  let reads=0;let fail=true;
  await page.route("**/api/v1/runs/run/findings?**",r=>{reads++;return fail?r.fulfill({status:503,json:{detail:"Temporary resolved outage"}}):r.fallback();});
  await page.goto("/runs/run?tab=resolved");await expect(page.getByRole("alert").filter({hasText:"Temporary resolved outage"})).toBeVisible();
  fail=false;resolved.push({...structuredClone(finding),status:"resolved"});const before=reads;await ticks(page);
  await expect(page.getByRole("tab",{name:"Resolved 1",exact:true})).toBeVisible();
  expect(reads).toBe(before + 1);await expect(page.getByRole("region",{name:"Findings resolved by this run"}).locator("[data-slot=finding-card]")).toHaveCount(1);
});

test("live refresh: late resolutions update two loaded pages after completion",async({page})=>{
  await page.clock.install();const resolved=Array.from({length:11},(_,i)=>({...structuredClone(finding),id:`resolved-${i}`,status:"resolved",content:`Resolved item ${i}`}));
  await mock(page,{resolved,run:{status:"completed"}});
  await page.goto("/runs/run?tab=resolved");await page.getByRole("button",{name:"Next resolved findings"}).click();
  const cards=page.getByRole("region",{name:"Findings resolved by this run"}).locator("[data-slot=finding-card]");await expect(cards).toHaveCount(1);
  resolved.push({...structuredClone(finding),id:"new-resolution",status:"resolved",content:"Late confirmation"});
  await ticks(page);await expect(cards).toHaveCount(2);await expect(page.getByRole("button",{name:/Late confirmation/})).toBeVisible();
});

for(const tab of ["findings","coverage","details"]) {
  test(`live refresh: ${tab} follows parent updates without reload or switching tabs`,async({page})=>{
    await page.clock.install();
    const state:Partial<Run>={status:"running",finished_at:null,report:structuredClone(run.report),publication:"pending",publication_error:null,check_error:null};
    const occurrences=[{finding_id:"finding",snapshot:finding,inline_line:46,is_new:false,current_status:"open"}];
    await mock(page,{run:state,occurrences});
    await page.goto(`/runs/run?tab=${tab}`);await expect(page.locator("main h1").locator("..").locator("[data-slot=badge]")).toHaveText("running");
    state.status="completed";state.finished_at="2026-10-05T13:21:30Z";state.publication="published";state.check_status="completed";state.check_conclusion="success";
    occurrences[0].current_status="resolved";
    (state.report as any).manifest.coverage.completed.push({path:"src/late.ts"});
    await ticks(page,1);await expect(page.locator("main h1").locator("..").locator("[data-slot=badge]")).toHaveText("completed");
    if(tab==="findings") await expect(page.getByRole("region",{name:"Findings at this run"}).getByText("Repeated finding",{exact:true})).toBeVisible();
    if(tab==="coverage") await expect(page.getByRole("region",{name:"Coverage",exact:true})).toContainText("src/late.ts");
    if(tab==="details") await expect(page.getByRole("region",{name:"Execution",exact:true})).not.toContainText("Not finished");
    await expect(page.getByRole("region",{name:"GitHub publication"})).toContainText("Published");
    await expect(page.getByRole("link",{name:"View check on GitHub: Success"})).toBeVisible();
  });
}

test("live refresh: open stage popover follows parent stage updates",async({page})=>{
  await page.clock.install();const stage=(status:string)=>({status,tokens:null,error:null});
  const state:any={status:"running",stages:{ocr:stage("completed"),assessment:stage("running"),verification:stage("running")}};
  await mock(page,{run:state});await page.goto("/runs/run");await page.getByRole("button",{name:"Review stage details",exact:true}).click();
  const popup=page.getByRole("dialog",{name:"Review stage details",exact:true});
  await expect(popup.getByText("running",{exact:true})).toHaveCount(2);
  state.stages.assessment.status="completed";state.stages.verification.status="completed";state.stages.verification.tokens=321;
  await ticks(page,1);await expect(popup.getByText("completed",{exact:true})).toHaveCount(3);await expect(popup).toContainText("321 tokens");
});

test("live refresh: open PR summary popover follows generation and sync updates",async({page})=>{
  await page.clock.install();const state:any={status:"running",summary:{status:"running",tokens:null,error:null}};const overview:any={summary_pending:true};
  await mock(page,{run:state,overview});await page.goto("/runs/run");await page.getByRole("button",{name:"Review stage details",exact:true}).click();
  const popup=page.getByRole("dialog",{name:"Review stage details",exact:true});await expect(popup.getByText("PR summary",{exact:true})).toBeVisible();await expect(popup.getByRole("link",{name:"PR summary",exact:true})).toHaveCount(0);
  state.summary.status="completed";state.summary.tokens=123;overview.summary_pending=false;overview.summary_url="https://github.com/acme/payments/pull/148";
  await ticks(page,1);await expect(popup.getByRole("link",{name:"PR summary",exact:true})).toHaveAttribute("href",overview.summary_url);await expect(popup).toContainText("123 tokens");
});

test("live refresh: open finding drawer follows status and verification changes",async({page})=>{
  await page.clock.install();await mock(page);
  const current=structuredClone(finding);const verifications:any[]=[];const activities:any[]=[];
  await page.route("**/api/v1/findings/finding",r=>r.fulfill({json:{finding:current,verifications,activities}}));
  await page.goto("/runs/run?finding=finding&finding_tab=activity");const sheet=page.getByRole("dialog",{name:"Finding details"});await expect(sheet).toContainText("No activity recorded");
  current.status="resolved";activities.push({id:"audit",action:"resolved",actor:"Code Mortis",source:"verification",reason:"Automatic audit confirmation",created_at:at});
  await ticks(page,1);await expect(sheet).toContainText("Automatic audit confirmation");await expect(sheet.getByText("Resolved",{exact:true}).first()).toBeVisible();
});

for (const count of [10, 50, 110]) {
  test(`live refresh batches ${count} assessments and stays idle without changes`, async ({ page }, info) => {
    await page.clock.install();
    const candidates = Array.from({ length: count }, (_, index) => auditCandidate(index));
    const writes = await mock(page, { candidates, assessment: auditSummary(count), run: { status: "running" } });
    const requests: URL[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/runs/run/candidates") requests.push(url);
    });
    await page.goto("/runs/run?tab=assessment");
    const list = page.getByRole("region", { name: "Candidate assessment", exact: true });
    const cards = list.locator("[data-slot=finding-card]");
    await expect(cards).toHaveCount(10);
    for (let loaded = 20; loaded <= count; loaded += 10) {
      await list.getByRole("button", { name: "Next assessments" }).click();
      await expect(cards).toHaveCount(10);
      await expect(list.getByRole("navigation").getByRole("status")).toHaveText(`Page ${loaded / 10} of ${count / 10} · ${count} total`);
    }
    const initial = requests.length;
    await ticks(page);
    expect(requests).toHaveLength(initial);
    candidates.at(-1)!.progress_phase = "validating";
    await ticks(page, 1);
    await expect(cards.last()).toContainText("Assessing");
    await expect.poll(() => requests.length).toBe(initial + 1);
    expect(requests.at(-1)!.searchParams.get("page")).toBe(String(count / 10));
    const openResponse = page.waitForResponse((response) => response.url().endsWith(`/candidates/audit-${count - 1}`));
    await cards.last().click();
    await (await openResponse).finished();
    const sheet = page.getByRole("dialog", { name: "Assessment details" });
    await expect(sheet).toContainText("Response received; validating against");
    await noOverflow(page);
    if (count === 10) await page.screenshot({ path: `/private/tmp/mortis-refresh-${info.project.name}.png`, fullPage: true });
    finish(candidates.at(-1)!);
    candidates.at(-1)!.progress_phase = null;
    await ticks(page, 1);
    await expect(sheet).toContainText("Finished assessment evidence");
    await expect.poll(() => requests.length).toBe(initial + 2);
    await ticks(page);
    expect(requests).toHaveLength(initial + 2);
    expect(writes).toEqual([]);
  });
}

test("live refresh respects Retry-After and recovers after completion without manual retry", async ({ page }) => {
  await page.clock.install();
  const resolved = [{ ...structuredClone(finding), status: "resolved", sync_intent: null }];
  await mock(page, { resolved, run: { status: "completed" } });
  let reads = 0;
  await page.route("**/api/v1/runs/run/findings?**", (route) => {
    reads++;
    return reads === 1 ? route.fulfill({ status: 503, headers: { "Retry-After": "12" }, json: { detail: "Temporary outage" } }) : route.fallback();
  });
  await page.goto("/runs/run?tab=resolved");
  await expect(page.getByRole("alert").filter({ hasText: "Temporary outage" })).toBeVisible();
  await ticks(page, 2);
  expect(reads).toBe(1);
  await ticks(page, 1);
  await expect(page.getByRole("region", { name: "Findings resolved by this run" }).locator("[data-slot=finding-card]")).toHaveCount(1);
  expect(reads).toBe(2);
  await ticks(page, 3);
  expect(reads).toBe(2);
});

test("live refresh discards a late assessment response after filters change", async ({ page }) => {
  await page.clock.install();
  const candidates = Array.from({ length: 11 }, (_, index) => auditCandidate(index));
  await mock(page, { candidates, assessment: auditSummary(11), run: { status: "running" } });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let held = false;
  await page.route("**/api/v1/runs/run/candidates?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get("page") === "2" && !params.has("q")) {
      held = true;
      const old = candidatePage(candidates, params);
      await pending;
      await route.fulfill({ json: old }).catch(() => null);
    } else await route.fallback();
  });
  await page.goto("/runs/run?tab=assessment");
  await page.getByRole("button", { name: "Next assessments" }).click();
  await expect.poll(() => held).toBe(true);
  await page.getByLabel("Search assessments", { exact: true }).fill("proposal 10");
  await page.clock.fastForward(400);
  const cards = page.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("Audit proposal 10");
  release();
  await ticks(page, 1);
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("Audit proposal 10");
  await expect(page.getByLabel("Search assessments", { exact: true })).toHaveValue("proposal 10");
});

test("live refresh backs off temporary failures to thirty seconds and then recovers", async ({ page }) => {
  await page.clock.install();
  await mock(page, { resolved: [{ ...finding, status: "resolved" }], run: { status: "completed" } });
  const times: number[] = [];
  await page.route("**/api/v1/runs/run/findings?**", async (route) => {
    times.push(await page.evaluate(() => Date.now()));
    if (times.length === 3) return route.abort("failed");
    if (times.length <= 5) return route.fulfill({ status: times.length === 1 ? 429 : 503, json: { detail: `Temporary outage ${times.length}` } });
    return route.fallback();
  });
  await page.goto("/runs/run?tab=resolved");
  await expect(page.getByRole("alert").filter({ hasText: "Temporary outage 1" })).toBeVisible();
  for (let attempt = 2; attempt <= 6; attempt++) {
    const delay = Math.min(5000 * 2 ** (attempt - 2), 30000);
    for (let elapsed = 0; elapsed < delay; elapsed += 5000) {
      await ticks(page, 1);
      await page.clock.runFor(50);
    }
    await expect.poll(() => times.length).toBe(attempt);
    // Finish handling this response before advancing the fake clock toward its retry.
    if (attempt < 6) await expect(page.getByRole("alert").filter({ hasText: attempt === 3
      ? "Could not reach Code Mortis" : `Temporary outage ${attempt}` })).toBeVisible();
  }
  await expect(page.getByRole("region", { name: "Findings resolved by this run" }).locator("[data-slot=finding-card]")).toHaveCount(1);
  expect(times).toHaveLength(6);
  for (let index = 1; index < times.length; index++) {
    const delay = Math.min(5000 * 2 ** (index - 1), 30000);
    expect(times[index] - times[index - 1]).toBeGreaterThanOrEqual(delay);
    expect(times[index] - times[index - 1]).toBeLessThan(delay + 5500);
  }
});

test("live refresh discards a late drawer response after opening another candidate", async ({ page }) => {
  await page.clock.install();
  const candidates = [auditCandidate(0), auditCandidate(1)];
  await mock(page, { candidates, assessment: auditSummary(2), run: { status: "running" } });
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let held = false;
  await page.route("**/api/v1/runs/run/candidates/audit-0", async (route) => {
    held = true;
    await pending;
    await route.fulfill({ json: { ...candidates[0], revision: 0 } }).catch(() => null);
  });
  await page.goto("/runs/run?tab=assessment");
  await page.getByRole("button", { name: /Audit proposal 0/ }).click();
  await expect.poll(() => held).toBe(true);
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).first().click();
  await page.getByRole("button", { name: /Audit proposal 1/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText("Audit proposal 1");
  release();
  await ticks(page, 1);
  await expect(sheet).toContainText("Audit proposal 1");
  await expect(sheet).not.toContainText("Audit proposal 0");
});

test("live refresh coalesces in-flight changes and updates filtered rows after completion", async ({ page }) => {
  await page.clock.install();
  const candidates = Array.from({ length: 20 }, (_, index) => auditCandidate(index));
  const state: Partial<Run> = { status: "running" };
  await mock(page, { candidates, assessment: auditSummary(20), run: state });
  let reads = 0;
  let hold = false;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/runs/run/candidates?**", async (route) => {
    reads++;
    const data = candidatePage(candidates, new URL(route.request().url()).searchParams);
    if (hold) { hold = false; await pending; }
    await route.fulfill({ json: data });
  });
  await page.goto("/runs/run?tab=assessment&assessment_outcome=pending");
  const cards = page.locator("[data-slot=finding-card]");
  await expect(cards).toHaveCount(10);
  await page.getByRole("button", { name: "Next assessments" }).click();
  await expect(cards).toHaveCount(10);
  await page.getByLabel("Search assessments", { exact: true }).focus();
  const before = reads;
  hold = true;
  finish(candidates[0]);
  await ticks(page, 1);
  await expect.poll(() => reads).toBe(before + 1);
  candidates.forEach(finish);
  state.status = "completed";
  await ticks(page, 2);
  expect(reads).toBe(before + 1);
  release();
  await expect(cards).toHaveCount(9);
  await ticks(page, 1);
  await expect(cards).toHaveCount(0);
  expect(reads).toBe(before + 3);
  candidates[1] = auditCandidate(1);
  await ticks(page, 1);
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("Audit proposal 1");
  await expect(page.getByLabel("Search assessments", { exact: true })).toBeFocused();
  await expect(page).toHaveURL(/assessment_outcome=pending/);
});

for (const resource of ["candidates", "resolved"] as const) {
  test(`live refresh allows ${resource} pagination during a slow background refresh`, async ({ page }) => {
    await page.clock.install();
    const candidates = Array.from({ length: 21 }, (_, index) => auditCandidate(index));
    const resolved = Array.from({ length: 21 }, (_, index) => ({ ...finding, id: `resolved-${index}`, status: "resolved", sync_version: 0 }));
    await mock(page, { candidates, resolved, assessment: auditSummary(21), run: { status: "running" } });
    const sizes: number[] = [];
    let holdBackground = false;
    let failNext = false;
    let holdPage = true;
    let releaseBackground!: () => void;
    let releasePage!: () => void;
    const background = new Promise<void>((resolve) => { releaseBackground = resolve; });
    let nextPage = new Promise<void>((resolve) => { releasePage = resolve; });
    await page.route(`**/api/v1/runs/run/${resource === "resolved" ? "findings" : resource}?**`, async (route) => {
      const params = new URL(route.request().url()).searchParams;
      const size = Number(params.get("page"));
      sizes.push(size);
      if (failNext) {
        failNext = false;
        return route.fulfill({ status: 503, json: { detail: "Temporary list failure" } });
      }
      if (holdBackground && size === 1) {
        holdBackground = false;
        await background;
      } else if (holdPage && size === 2) {
        holdPage = false;
        await nextPage;
      }
      await route.fallback().catch(() => null);
    });
    const changeRevision = () => {
      candidates[0].progress_phase = candidates[0].progress_phase ? null : "validating";
    };
    await page.goto(`/runs/run?tab=${resource === "candidates" ? "assessment" : "resolved"}`);
    const list = page.getByRole("region", { name: resource === "candidates" ? "Candidate assessment" : "Findings resolved by this run", exact: true });
    const cards = list.locator("[data-slot=finding-card]");
    const suffix = resource === "candidates" ? "assessments" : "resolved findings";
    await expect(cards).toHaveCount(10);
    holdBackground = true;
    changeRevision();
    await ticks(page, 1);
    await expect.poll(() => sizes).toEqual([1, 1]);
    const loadMore = list.getByRole("button", { name: `Next ${suffix}` });
    await expect(loadMore).toBeEnabled();
    await loadMore.click();
    await expect.poll(() => sizes).toEqual([1, 1, 2]);
    await expect(list.getByRole("button", { name: `Next ${suffix}` })).toBeDisabled();
    await expect(cards).toHaveCount(10);
    releaseBackground();
    releasePage();
    await expect(list.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 3 · 21 total");

    failNext = true;
    changeRevision();
    await ticks(page, 1);
    const failure = page.getByRole("alert").filter({ hasText: "Temporary list failure" });
    await expect(failure).toBeVisible();
    nextPage = new Promise<void>((resolve) => { releasePage = resolve; });
    holdPage = true;
    const retry = list.getByRole("button", { name: `Retry ${suffix}` });
    await retry.click();
    await expect.poll(() => sizes).toEqual([1, 1, 2, 2, 2]);
    await expect(retry).toBeDisabled();
    await expect(cards).toHaveCount(10);
    releasePage();
    await expect(loadMore).toBeEnabled();
    await expect(failure).toHaveCount(0);
    await expect(cards).toHaveCount(10);
  });
}

for (const area of ["pull", "run", "assessment", "resolved"] as const) {
  test(`${area} filters send shared ordering to the backend and preserve focus`, async ({ page }) => {
    const rows = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `ordered-${index}`, content: `Backend ordered review ${index}`,
      path: `src/${String(22 - index).padStart(2, "0")}.py`, severity: index === 22 ? "critical" : index === 0 ? "low" : "medium" }));
    const candidates = rows.map((row, index) => ({ ...auditCandidate(index), snapshot: row }));
    const occurrences = rows.map((row) => ({ finding_id: row.id, snapshot: row, inline_line: null }));
    await mock(page, { candidates, resolved: rows, occurrences, assessment: auditSummary(23) });
    if (area === "pull") await mockPullDetail(page, () => ({ pull_request: pr, repository: repo, can_write: true, findings: rows, runs: [] }));
    const endpoint = area === "pull" ? "/pulls/pr/findings" : `/runs/run/${area === "assessment" ? "candidates" : "findings"}`;
    const requests: URLSearchParams[] = [];
    await page.route(`**/api/v1${endpoint}**`, (route) => {
      const url = new URL(route.request().url());
      if (url.pathname !== `/api/v1${endpoint}`) return route.fallback();
      const params = url.searchParams;
      requests.push(params);
      const ordered = ["-severity", "path"].includes(params.get("ordering") ?? "-severity") ? [...rows].reverse() : rows;
      const pageNumber = Number(params.get("page") ?? 1);
      const shown = ordered.slice((pageNumber - 1) * 10, pageNumber * 10);
      const data = area === "pull" ? findingPage(rows, params) : area === "assessment" ? candidatePage(candidates, params) : resolvedPage(rows, params);
      if (area === "pull") (data as Schema["FindingsOut"]).groups[0].items = shown;
      else if (area === "assessment") (data as Schema["CandidatePage"]).items = shown.map((row) => candidates[rows.indexOf(row)]);
      return route.fulfill({ json: area === "run" || area === "resolved" ? { ...data,
        items: shown.map((row) => ({ finding_id: row.id, snapshot: row, inline_line: null })), outcome_counts: {} } : data });
    });
    await page.goto(area === "pull" ? "/pulls/pr?ordering=-created_at" : `/runs/run?tab=${area === "run" ? "findings" : area === "assessment" ? "assessment&assessment_ordering=position" : "resolved&resolved_ordering=-created_at"}`);
    const panel = page.getByRole("tabpanel");
    if (area !== "run") {
      await panel.getByRole("button", { name: area === "pull" ? "Next open findings" : area === "assessment" ? "Next assessments" : "Next resolved findings", exact: true }).click();
      await expect(panel.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
    }
    const defaultField = area === "run" ? "Severity" : area === "assessment" ? "Assessment order" : "Created";
    await panel.getByRole("button", { name: `Sort by ${defaultField}`, exact: true }).click();
    const selected = area === "run" ? "File" : "Severity";
    await page.getByRole("menuitemradio", { name: selected, exact: true }).click();
    await expect.poll(() => requests.at(-1)?.get("ordering") ?? "-severity").toBe(area === "run" ? "path" : "-severity");
    if (area !== "run") expect(requests.at(-1)?.get("page") ?? "1").toBe("1");
    await expect(panel.locator('[data-slot="finding-card"]').first()).toContainText("Backend ordered review 22");
    await expect(panel.getByRole("button", { name: `Sort by ${selected}`, exact: true })).toBeFocused();
    await panel.getByRole("button", { name: area === "run" ? "Sort descending" : "Sort ascending", exact: true }).click();
    await expect(panel.locator('[data-slot="finding-card"]').first()).toContainText("Backend ordered review 0");
    await noOverflow(page);
  });
}

test("retired repository connections require explicit shared selection and use Mortis", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, connection_id: "personal", connection_requires_setup: true, settings: { ...repo.settings } }] }));
  await page.goto("/repositories/22/settings?tab=connection");
  await expect(page.getByText("The saved subscription connection is retired.", { exact: false })).toBeVisible();
  const picker = page.getByRole("combobox", { name: "Default AI connection", exact: true });
  await expect(picker).toHaveValue("Retired connection");
  await expect(page.getByLabel("Review engine", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Pull request reviews use these settings by default.", { exact: false })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/retired-connection-${info.project.name}.png`, fullPage: true });
  await chooseOption(page, picker, "Team reviews");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({ connection_id: "connection", settings: { analysis_engine: "mortis" } });
});

test("retired review default blocks inference until an explicit shared override", async ({ page }) => {
  const requests = await mock(page);
  await mockPullDetail(page, () => ({ pull_request: pr, repository: { ...repo, connection_requires_setup: true }, runs: [run], findings: [], can_write: true }));
  await page.goto("/pulls/pr");
  const review = page.getByRole("button", { name: "Run review", exact: true });
  await expect(review).toBeDisabled();
  await expect(page.getByText("The repository's connection is retired.", { exact: false })).toBeVisible();
  await chooseOption(page, page.getByRole("combobox", { name: "Review connection", exact: true }), "Team reviews");
  await expect(review).toBeEnabled();
  await review.click();
  await expect.poll(() => requests.find((request) => request.path === "/pulls/pr/runs")?.body).toEqual({ connection_id: "connection" });
});

test("native review history stays readable after subscription setup is retired", async ({ page }) => {
  await mock(page, { run: { engine: "chatgpt" } });
  await page.goto("/runs/run");
  await page.getByRole("tab", { name: "Details", exact: true }).click();
  await expect(page.getByText("Codex (ChatGPT)", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^Findings/ }).click();
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  await expect(page.getByRole("dialog", { name: "Finding details" })).toContainText(finding.content);
  await noOverflow(page);
});

for (const status of ["completed", "partial"] as const) test(`Mortis ${status} history retains saved configuration, coverage and summary without inference`, async ({ page }, info) => {
  const selected = [{ path: "src/retry.ts" }, { path: "src/config.ts" }];
  const requests = await mock(page, { canWrite: false, run: {
    engine: "mortis", status, publication: "published", publication_error: null,
    check_pending: false, check_error: null, check_status: "completed", check_conclusion: "neutral",
    config: { analysis_engine: "mortis", pipeline_version: 2,
      connection: { kind: "api", name: "Archived team connection", model: "saved-mortis-model" },
      settings: { summary_diagram_enabled: true, ai_fix_prompts_enabled: true } },
    report: { manifest: { coverage: { selected, completed: status === "completed" ? selected : selected.slice(0, 1),
      reused: [], failed: [], waived: status === "partial" ? [{ path: "src/config.ts", reason: "Token budget reached" }] : [] } } },
    stages: { analysis: { status, tokens: 100 }, assessment: { status: "completed", tokens: 20 },
      verification: { status: "skipped", tokens: 0 }, summary: { status: "completed", tokens: 40 } },
    summary: { status: "completed", tokens: 40, limited_context: false, settings: { summary_diagram_enabled: true, ai_fix_prompts_enabled: true },
      result: { narrative: "Payment retries now stop at their limit.",
        groups: [{ title: "Retry behavior", description: "Bound retry work.", paths: ["src/retry.ts"] }] } },
  }, overview: { summary_url: "https://github.com/acme/payments/pull/148#issuecomment-10", summary_pending: false } });
  await page.goto("/runs/run?tab=details");
  const setup = page.getByRole("region", { name: "Review setup", exact: true });
  await expect(setup.getByText("Mortis", { exact: true })).toBeVisible();
  await expect(setup.getByText("Archived team connection", { exact: true })).toBeVisible();
  await expect(setup.getByText("saved-mortis-model", { exact: true })).toBeVisible();
  await page.getByText("Configuration used", { exact: true }).click();
  await expect(page.getByRole("tabpanel")).toContainText('"summary_diagram_enabled": true');
  await expect(page.getByRole("tabpanel")).toContainText('"ai_fix_prompts_enabled": true');
  const trigger = page.getByRole("button", { name: "Review stage details", exact: true });
  await expect(trigger).toHaveAttribute("data-status", status);
  await trigger.click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("link", { name: "PR summary", exact: true })).toHaveAttribute("href", /#issuecomment-10$/);
  await expect(details.getByText("40 tokens", { exact: true })).toBeVisible();
  await expect(details.getByText("Analysis", { exact: true })).toBeVisible();
  await expect(details.getByText("OCR", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath(`mortis-${status}-review-stages.png`), fullPage: true });
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  const coverage = page.getByRole("region", { name: "Coverage", exact: true });
  await expect(coverage.getByText("selected", { exact: true }).locator("..")).toContainText("2");
  await expect(coverage.getByText("completed", { exact: true }).locator("..")).toContainText(status === "completed" ? "2" : "1");
  if (status === "partial") await expect(coverage.getByRole("heading", { name: /Files not reviewed/ })).toBeVisible();
  await noOverflow(page);
  expect(requests).toHaveLength(0);
});


test("Mortis is saved without an engine selector while custom review rules remain editable", async ({ page }, info) => {
  const requests = await mock(page);
  const legacySettings: Partial<Schema["RepositoryOut"]["settings"]> = { ...repo.settings, effort: "medium" };
  delete legacySettings.analysis_engine;
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repo, settings: legacySettings }] }));
  await page.goto("/repositories/22/settings?tab=connection");
  await expect(page.getByText("Pull request reviews use these settings by default.", { exact: false })).toBeVisible();
  await expect(page.getByLabel("Review engine", { exact: true })).toHaveCount(0);
  await repositorySection(page, "Pull request reviews");
  await expect(page.getByLabel("Analysis token budget", { exact: true })).toHaveValue("200000");
  const rules = { rules: [{ path: "**/*.py", rule: "Preserve ownership checks" }] };
  await page.getByText("Custom review rules", { exact: true }).click();
  await page.getByRole("textbox", { name: "Custom review rules (JSON)", exact: true }).fill(JSON.stringify(rules));
  await expect(page.getByRole("link", { name: "Rule file documentation" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath("mortis-only-settings.png"), fullPage: true });
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    settings: { analysis_engine: "mortis", rules, token_budget: 200000 },
  });
});

test("historical OCR reviews preserve origin and publication recovery without resuming inference", async ({ page }) => {
  const requests = await mock(page, { run: { engine: "ocr", status: "blocked", owner_id: 7,
    blocked_reason: "The saved OCR execution is retired.", publication: "failed", publication_error: "Publication failed.",
    stages: { ocr: { status: "completed", tokens: 10 }, assessment: { status: "failed", tokens: 0 } } } });
  await page.goto("/runs/run?tab=details");
  await expect(page.getByText("This historical review used a retired engine.", { exact: false })).toBeVisible();
  await expect(page.getByRole("region", { name: "Review setup", exact: true }).getByText("Retired engine", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume review", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry GitHub publication", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Review stage details", exact: true }).getByText("Analysis", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry summary generation", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: /^Findings/ }).click();
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  await expect(page.getByRole("dialog", { name: "Finding details" })).toContainText(finding.content);
  expect(requests).toHaveLength(0);
});

test("historical OCR candidates keep failed evidence without offering another assessment", async ({ page }) => {
  const writes = await mock(page, { candidates: [{ id: "failed", snapshot: { content: "Historical failed proposal." },
    status: "failed", result: null, finding_id: null, is_new: false, error: "Saved OCR failure", assessment_only: false, retryable: true }],
    run: { engine: "ocr", status: "completed", publication: "no_new_comments", publication_error: null },
    assessment: { candidates: 1, new_findings: 0, repeated_observations: 0, rejected_candidates: 0, withheld_candidates: 1,
      pending: 0, incomplete: 1, failed: 1, skipped: 0, published_comments: 0 },
  });
  await page.goto("/runs/run?tab=assessment");
  await page.getByRole("button", { name: /Historical failed proposal/ }).click();
  const sheet = page.getByRole("dialog", { name: "Assessment details" });
  await expect(sheet).toContainText("Saved retired engine failure");
  await expect(sheet.getByRole("button", { name: "Retry assessment", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});


for (const engine of ["ocr", "chatgpt", "claude_code"] as const) test(`historical ${engine} reviews can explicitly generate a new Mortis summary`, async ({ page }) => {
  const summary: NonNullable<Run["summary"]> = { status: "failed", tokens: 17, limited_context: false, error: "Saved summary failed." };
  const requests = await mock(page, { run: { engine, status: "completed", summary }, overview: { summary_error: null } });
  await page.goto("/runs/run?tab=details");
  const setup = page.getByRole("region", { name: "Review setup", exact: true });
  const originalEngine = engine === "ocr" ? "Retired engine" : engine === "chatgpt" ? "Codex (ChatGPT)" : "Claude Code";
  await expect(setup.getByText(originalEngine, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review stage details", exact: true }).click();
  const details = page.getByRole("dialog", { name: "Review stage details", exact: true });
  await expect(details.getByRole("button", { name: "Retry summary generation", exact: true })).toHaveCount(0);
  await expect(details).toContainText("Generating a new Mortis summary uses the repository's current shared API connection and limits.");
  await noOverflow(page);
  summary.status = "pending";
  summary.error = null;
  await details.getByRole("button", { name: "Generate summary with Mortis", exact: true }).click();
  expect(requests.map((request) => request.path)).toEqual(["/runs/run/summary/retry"]);
  await expect(details.getByRole("button", { name: "Generate summary with Mortis", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(setup.getByText(originalEngine, { exact: true })).toBeVisible();
});
