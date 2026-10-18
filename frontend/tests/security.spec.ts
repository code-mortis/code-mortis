import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const at = "2026-10-18T15:00:00Z";
const sha = "a".repeat(40);
const repository: Schema["RepositoryOut"] = { id: 22, installation_id: 10, full_name: "acme/payments", private: false,
  active: true, enabled: false, connection_id: "connection", open_prs: 0, has_running_review: false,
  settings: { timeout_seconds: 900, token_budget: 200000, language: "English",
    summary_diagram_enabled: false, ai_fix_prompts_enabled: false, automatic: false, include_bot_prs: false,
    model: "", review_sensitivity: "balanced", background: "", exclusions: "",
    delegated_context_bytes: 800000, concurrency: 2, effort: "high", max_files: 40, auto_resolve: false } };
const summary: Schema["SecuritySummary"] = { grade: "D", state: "current", grade_scan_id: "scan", grade_sha: sha,
  graded_at: at, latest_scan_id: "scan", latest_status: "completed", latest_at: at, default_branch: "main", head_sha: sha,
  checked_at: at, counts: { high: 1 } };
const scan: Schema["SecurityScanOut"] = { id: "scan", repository_id: 22, actor_id: 7, connection_id: "connection", parent_id: null,
  branch: "main", head_sha: sha, config: { connection: { id: "connection", kind: "api", name: "Team API", model: "test-model" },
    limits: { token_budget: 200000, timeout_seconds: 900 } }, status: "completed", grade: "D", error: null,
  stages: Object.fromEntries(["snapshot", "trivy", "analysis", "assessment", "verification", "report"].map((name) => [name, { status: "completed" }])),
  progress: { tokens: 4500, work: { completed: 3, failed: 0, analysis_total: 1, analysis_completed: 1 } },
  coverage: { inventory: { "src/orders.ts": { code: true, hash: "source", lines: 40 } }, excluded: [{ path: "dist/app.js", reason: "Generated directory" }],
    trivy: { version: "0.76.0", database_fresh: true }, targets: { "bun.lock": { type: "bun" } }, problems: [] },
  report: {}, created_at: at, started_at: at, finished_at: at };
const finding: Schema["SecurityFindingOut"] = { id: "finding", repository_id: 22, state: "open", kind: "code", severity: "high",
  title: "Order lookup does not verify ownership", path: "src/orders.ts", first_scan_id: "scan", last_scan_id: "scan", version: 0,
  rotation: null, issue: {}, evidence: { source_sha: sha, explanation: "A signed-in user can retrieve another account's order.",
    trigger: "Request an order belonging to a different account.", impact: "Private order details are disclosed.",
    remediation: "Constrain the order lookup to the authenticated account.", verification: "Add a cross-account access regression test.",
    evidence: [{ path: "src/orders.ts", start_line: 14, end_line: 14, quote: "return orders.find(id);" }] }, created_at: at, updated_at: at };

async function mock(page: Page, options: { partial?: boolean; secret?: boolean; canWrite?: boolean; scanError?: string } = {}) {
  let current = structuredClone(finding);
  if (options.secret) current = { ...current, kind: "secret", title: "Exposed credential material", evidence: { ...current.evidence, identity: "keyed-digest" } };
  const state = options.partial ? { ...summary, state: "incomplete", latest_status: "partial", counts: { critical: 1 } } : summary;
  const saved = { ...scan, report: { commit: sha, grade: "D", status: "completed", findings: [{ ...current, finding_id: current.id, outcome: "present" }], problems: [] } };
  if (options.scanError) Object.assign(saved, { status: "partial", grade: null, error: options.scanError,
    stages: { ...saved.stages, analysis: { status: "failed", total: 163, completed: 13 },
      assessment: { status: "skipped" }, verification: { status: "skipped" } },
    progress: { tokens: 4500, work: { completed: 13, failed: 1, analysis_total: 163, analysis_completed: 13 } },
    coverage: { ...saved.coverage, units: [{ task: "analysis:src/orders.ts", status: "failed", error: options.scanError }] },
    report: { ...saved.report, grade: null, status: "partial", problems: [options.scanError] } });
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const method = route.request().method();
    let json: unknown = {};
    if (method !== "GET") {
      const body = route.request().postDataJSON() ?? {};
      writes.push({ path, body });
      if (path.endsWith("/scans")) json = { ...scan, id: "queued", status: "queued", grade: null };
      else if (path.endsWith("/github-issue")) { current.issue = { publication_status: "pending", sync_status: "pending" }; json = current; }
      else if (path.endsWith("/rotation")) { current.rotation = { acknowledged_at: at, reason: body.reason }; current.version++; json = current; }
      else if (method === "PATCH") { current.state = body.state; current.version++; json = current; }
    } else if (path === "/setup/status") json = { configured: true, public_url: "http://localhost" };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", csrf: "test", avatar_url: "" };
    else if (path === "/repositories") json = [{ ...repository, security: state }];
    else if (path === "/security/repositories") json = [{ repository, summary: state }];
    else if (path === "/security/repositories/22") json = { repository, summary: state, can_write: options.canWrite ?? true };
    else if (path === "/security/repositories/22/scans") json = { items: [saved], total: 1, page: 1, page_size: 20 };
    else if (path === "/security/repositories/22/findings" || path === "/security/scans/scan/findings") json = { items: [current], total: 1, page: 1, page_size: 20 };
    else if (path === "/security/scans/scan" || path === "/security/scans/queued") json = { repository, scan: path.endsWith("queued") ? { ...scan, id: "queued", status: "queued", grade: null, report: {} } : saved, can_write: true, activity: [] };
    else if (path === "/security/findings/finding") json = { repository, finding: current, can_write: options.canWrite ?? true,
      observations: [{ scan_id: "scan", outcome: "present", created_at: at, snapshot: {} }], activity: [] };
    else if (path === "/security/findings/finding/github-issue") json = { title: "[Security] Order lookup does not verify ownership", body: "## Evidence\n\nThe lookup lacks an ownership check.\n\n## Remediation\n\nConstrain access to the authenticated account.", repository: repository.full_name, public: true, version: current.version };
    else if (path === "/connections") json = [{ id: "connection", name: "Team API", kind: "api", model: "test-model", provider: "openai" }];
    else if (path === "/connections/connection/models") json = { status: "ready", source: "provider", models: [{ id: "test-model", name: "Test model" }] };
    await route.fulfill({ json });
  });
  return writes;
}

test("security errors explain failed evidence and keep diagnostics collapsed and copyable", async ({ page }, info) => {
  const error = 'OCR returned an invalid verification verdict: Security evidence does not match its cited source lines.; response={"findings":[{"title":"Synthetic finding","evidence":[{"quote":"unmatched code"}]}]}';
  await mock(page, { scanError: error });
  await page.goto("/security/scans/scan");
  const alert = page.getByRole("alert").first();
  await expect(alert.getByText("Evidence could not be verified", { exact: true })).toBeVisible();
  await expect(alert).toContainText("Review the technical details and retry the run.");
  const details = alert.getByRole("region", { name: "Evidence could not be verified technical details", includeHidden: true });
  await expect(details).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-readable-error.png"), fullPage: true });
  await alert.getByText("Technical details", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(details).toBeVisible();
  await expect(details).toHaveText(error);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async (value: string) => { document.body.dataset.copiedError = value; } } }));
  await alert.getByRole("button", { name: "Copy Evidence could not be verified details", exact: true }).click();
  await expect(page.locator("body")).toHaveAttribute("data-copied-error", error);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  await page.getByText("Analysis work units", { exact: true }).click();
  const work = page.getByRole("tabpanel").getByRole("alert");
  await expect(work.getByText("Evidence could not be verified", { exact: true })).toBeVisible();
  await expect(work.getByRole("region", { includeHidden: true })).toBeHidden();
});

test("security overview keeps historical grades qualified and filters in the URL", async ({ page }, info) => {
  await mock(page, { partial: true });
  await page.goto("/security");
  await expect(page.getByRole("heading", { name: "Security", exact: true })).toBeVisible();
  await expect(page.getByText("Security D · Incomplete", { exact: true })).toBeVisible();
  await expect(page.getByText("1 critical", { exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search repositories" }).fill("payments");
  await page.getByRole("combobox", { name: "Grade", exact: true }).selectOption("D");
  await expect(page).toHaveURL(/search=payments&grade=D/);
  await page.reload();
  await expect(page.getByRole("searchbox", { name: "Search repositories" })).toHaveValue("payments");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-overview-${info.project.name}.png`, fullPage: true });
});

test("scan dialog uses existing connection and limits without enabling PR reviews", async ({ page }) => {
  const writes = await mock(page);
  await page.goto("/security");
  await page.getByRole("button", { name: "Run scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toHaveValue("200000");
  await expect(dialog.getByRole("spinbutton", { name: "Timeout (seconds)" })).toHaveValue("900");
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).check();
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).uncheck();
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0].body).toMatchObject({ connection_id: "connection", model: "test-model", token_budget: 200000, timeout_seconds: 900 });
  expect(String(writes[0].body.request_key).length).toBeGreaterThan(16);
});

test("saved scan exposes actionable report, coverage, findings and downloads", async ({ page }, info) => {
  await mock(page);
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("heading", { name: "Security scan", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download Markdown" })).toHaveAttribute("href", /format=markdown/);
  await expect(page.getByRole("heading", { name: "Remediation", exact: true })).toBeVisible();
  await expect(page.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  await expect(page.getByText("Within 24-hour freshness limit", { exact: true })).toBeVisible();
  await page.getByText("Prepared source inventory", { exact: true }).click();
  await expect(page.getByText("src/orders.ts", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings", exact: true }).click();
  await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
  await expect(page.getByRole("dialog", { name: "Order lookup does not verify ownership" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Code snippet" })).toContainText("return orders.find(id)");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-finding-${info.project.name}.png`, fullPage: true });
});

test("GitHub publication requires a reviewed individual issue and explicit public confirmation", async ({ page }, info) => {
  const writes = await mock(page);
  await page.goto("/security/repositories/22?finding=finding");
  await page.getByRole("button", { name: "Create GitHub issue", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issue", exact: true });
  await expect(dialog.getByText(/This issue will be public/)).toBeVisible();
  expect(writes).toHaveLength(0);
  await dialog.getByRole("textbox", { name: "Issue title" }).fill("Security: enforce order ownership");
  await dialog.getByRole("tab", { name: "Edit", exact: true }).click();
  await dialog.getByRole("textbox", { name: "Issue body" }).fill("A reviewed finding with a concrete fix.");
  await dialog.getByRole("tab", { name: "Preview", exact: true }).click();
  await expect(dialog.getByText("A reviewed finding with a concrete fix.")).toBeVisible();
  await page.screenshot({ path: `/private/tmp/code-mortis-security-issue-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Create issue", exact: true }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toMatchObject({ title: "Security: enforce order ownership", body: "A reviewed finding with a concrete fix.", version: 0 });
  await expect(dialog).not.toBeVisible();
});

test("secret rotation requires an explicit reason and acknowledgment", async ({ page }) => {
  const writes = await mock(page, { secret: true });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Exposed credential material" });
  const acknowledgment = sheet.getByRole("button", { name: "I rotated or revoked this credential" });
  await expect(acknowledgment).toBeDisabled();
  await sheet.getByRole("textbox", { name: "Reason", exact: true }).fill("Revoked the old credential and deployed its replacement.");
  await acknowledgment.click();
  await expect(sheet.getByText("Rotation acknowledged.", { exact: false })).toBeVisible();
  expect(writes[0].path).toContain("/rotation");
});

test("read-only users cannot publish or change finding state", async ({ page }) => {
  await mock(page, { canWrite: false });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Order lookup does not verify ownership" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Create GitHub issue" })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Save decision" })).toHaveCount(0);
});

test("security controls support keyboard focus and dark narrow layouts", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 900 });
  await page.goto("/security");
  const run = page.getByRole("button", { name: "Run scan", exact: true });
  await run.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog).toBeVisible();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe("dark");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-dark-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(run).toBeFocused();
});
