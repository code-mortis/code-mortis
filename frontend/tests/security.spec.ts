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

async function mockReportFindings(page: Page, rows: Record<string, unknown>[]) {
  const requests: URLSearchParams[] = [];
  const state = { failure: 0 };
  await page.route("**/api/v1/security/scans/scan/report/findings**", (route) => {
    expect(route.request().method()).toBe("GET");
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    if (state.failure) return route.fulfill({ status: state.failure, json: { detail: state.failure === 403 ? "Access denied" : "Report temporarily unavailable" } });
    const counts = Object.fromEntries(["outcome", "severity", "kind"].map((key) => [key, Object.fromEntries(
      [...new Set(rows.map((row) => String(row[key] || "unknown")))].map((value) => [value, rows.filter((row) => String(row[key] || "unknown") === value).length]))]));
    const term = (params.get("q") ?? "").trim().toLowerCase();
    const filtered = rows.filter((row) => (!params.has("finding_id") || row.finding_id === params.get("finding_id"))
      && ["outcome", "severity", "kind"].every((key) => !params.has(key) || params.getAll(key).includes(String(row[key] || "unknown")))
      && (!term || [row.title, row.path, row.kind, (row.evidence as Record<string, unknown>)?.explanation].some((value) => String(value ?? "").toLowerCase().includes(term))));
    const pageNumber = Number(params.get("page") ?? 1);
    return route.fulfill({ json: { items: filtered.slice((pageNumber - 1) * 20, pageNumber * 20).map((row) => ({ ...row, id: row.finding_id })),
      total: filtered.length, registered_total: rows.length, outcome_counts: counts.outcome, severity_counts: counts.severity, kind_counts: counts.kind,
      page: pageNumber, page_size: 20 } });
  });
  return { requests, state };
}

function securityAssessment(id: string, changes: Partial<Schema["SecurityAssessmentOut"]> = {}): Schema["SecurityAssessmentOut"] {
  return { id, revision: "1", proposal: { ...finding.evidence, title: `Security candidate ${id}`, path: finding.path, severity: "high", category: "Access Control" },
    status: "completed", progress_phase: "finished", outcome: "rejected", tokens: 12, reused: false, finding_id: null, error: null,
    result: { verdict: "rejected", finding_id: null, explanation: "The supplied ownership guard protects this request.", evidence: finding.evidence.evidence, required_paths: [] }, ...changes };
}

async function mockSecurityAssessments(page: Page) {
  const writes = await mock(page, { running: true });
  const state = { revision: 1, fail: 0, detailFail: 0, requests: [] as string[], rows: [
    securityAssessment("0", { outcome: "confirmed", finding_id: "finding", result: { verdict: "confirmed", finding_id: null, explanation: "A cross-account lookup is reachable.", evidence: finding.evidence.evidence } }),
    securityAssessment("1"), securityAssessment("2", { outcome: "uncertain", result: { verdict: "uncertain", explanation: "The caller contract is unavailable.", evidence: [] } }),
    securityAssessment("3", { status: "failed", outcome: "failed", error: "OCR returned an invalid structured result: Security evidence does not match its cited source lines.", result: null, tokens: null }),
    securityAssessment("4", { status: "running", progress_phase: "checking", outcome: "pending", result: null, tokens: null }),
    securityAssessment("5", { status: "queued", progress_phase: "queued", outcome: "pending", result: null, tokens: null }),
    securityAssessment("6", { status: "running", progress_phase: "validating", outcome: "pending", result: { verdict: "confirmed", explanation: "Valid response awaiting persistence.", evidence: finding.evidence.evidence } }),
  ] };
  function summary() {
    const status_counts: Record<string, number> = {}, outcome_counts: Record<string, number> = {};
    for (const row of state.rows) { status_counts[row.status] = (status_counts[row.status] ?? 0) + 1; outcome_counts[row.outcome] = (outcome_counts[row.outcome] ?? 0) + 1; }
    return { total: state.rows.length, status_counts, outcome_counts, revision: String(state.revision) };
  }
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository, scan: { ...scan, status: "running", grade: null,
    stages: { ...scan.stages, assessment: { status: "running", total: state.rows.length, completed: summary().status_counts.completed ?? 0 } } },
    can_write: false, finding_count: 1, activity_count: 0, activity: [], assessment: summary() } }));
  await page.route("**/api/v1/security/scans/scan/assessments**", (route) => {
    const url = new URL(route.request().url());
    state.requests.push(url.pathname + url.search);
    expect(route.request().method()).toBe("GET");
    const id = url.pathname.match(/\/assessments\/(\d+)$/)?.[1];
    const failure = id ? state.detailFail : state.fail;
    if (failure) return route.fulfill({ status: failure, json: { detail: failure === 403 ? "Access denied" : "Assessment history temporarily unavailable" } });
    if (id) return route.fulfill({ json: { ...state.rows.find((row) => row.id === id), revision: String(state.revision) } });
    const q = url.searchParams.get("q")?.toLowerCase() ?? "";
    const rows = state.rows.filter((row) => (!q || `${row.proposal.title} ${row.proposal.path}`.toLowerCase().includes(q))
      && (!url.searchParams.has("outcome") || url.searchParams.getAll("outcome").includes(row.outcome))
      && (!url.searchParams.has("severity") || url.searchParams.getAll("severity").includes(String(row.proposal.severity))) );
    const size = Number(url.searchParams.get("page_size") ?? 20);
    return route.fulfill({ json: { items: rows.slice(0, size).map((row) => ({ ...row, revision: String(state.revision) })), total: rows.length,
      registered_total: state.rows.length, outcome_counts: summary().outcome_counts, severity_counts: { high: state.rows.length },
      category_counts: { "Access Control": state.rows.length }, revision: String(state.revision), page: 1, page_size: size } });
  });
  return { state, writes };
}

async function mock(page: Page, options: { partial?: boolean; secret?: boolean; canWrite?: boolean; scanError?: string; running?: boolean; longPath?: boolean; latestStatus?: string; summary?: Partial<Schema["SecuritySummary"]>; issue?: Schema["SecurityFindingOut"]["issue"]; activity?: Schema["SecurityActivityOut"][] } = {}) {
  let current = structuredClone(finding);
  if (options.longPath) current.path = `services/${"long-directory-name/".repeat(10)}account-order-ownership-with-a-very-long-filename.ts`;
  if (options.secret) current = { ...current, kind: "secret", title: "Exposed credential material", evidence: { ...current.evidence, identity: "keyed-digest" } };
  if (options.issue) current.issue = options.issue;
  const state = { ...(options.partial ? { ...summary, state: "incomplete", grade_scan_id: "previous", latest_status: "partial", counts: { critical: 1 } } : summary),
    ...(options.latestStatus ? { latest_status: options.latestStatus } : {}), ...options.summary };
  const saved = { ...scan, report: { commit: sha, grade: "D", status: "completed", findings: [{ ...current, finding_id: current.id, outcome: "present" }], problems: [] } };
  if (options.running) Object.assign(saved, { status: "running", grade: null, report: {},
    stages: Object.fromEntries(["snapshot", "trivy", "analysis", "assessment", "verification", "report"].map((name) => [name,
      name === "analysis" ? { status: "running", total: 163, completed: 11 } : { status: ["snapshot", "trivy"].includes(name) ? "completed" : "pending" }])),
    progress: { tokens: 4000, work: { completed: 11, failed: 0, analysis_total: 163, analysis_completed: 11 } } });
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
      if (path.endsWith("/scans") || path.endsWith("/retry")) json = { ...scan, id: "queued", status: "queued", grade: null };
      else if (path.endsWith("/github-issue")) { current.issue = { publication_status: "pending", sync_status: "pending" }; json = current; }
      else if (path.endsWith("/rotation")) { current.rotation = { acknowledged_at: at, reason: body.reason }; current.version++; json = current; }
      else if (method === "PATCH") { current.state = body.state; current.version++; json = current; }
    } else if (path === "/setup/status") json = { configured: true, public_url: "http://localhost" };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", csrf: "test", avatar_url: "" };
    else if (path === "/repositories") json = [{ ...repository, security: state }];
    else if (path === "/security/repositories") json = [{ repository, summary: state }];
    else if (path === "/security/repositories/22") json = { repository, summary: state, can_write: options.canWrite ?? true, finding_count: 1, scan_count: 1 };
    else if (path === "/security/repositories/22/scans") json = { items: [saved], total: 1, page: 1, page_size: 20 };
    else if (path === "/security/repositories/22/findings" || path === "/security/scans/scan/findings") json = { items: [current], total: 1, page: 1, page_size: 20 };
    else if (path === "/security/scans/scan" || path === "/security/scans/queued") json = { repository, scan: path.endsWith("queued") ? { ...scan, id: "queued", status: "queued", grade: null, report: {} }
      : { ...saved, report: Object.fromEntries(Object.entries(saved.report).filter(([key]) => key !== "findings")) },
      report_count: saved.report.findings?.length ?? 0, can_write: true, finding_count: path.endsWith("queued") ? 0 : 1, activity_count: 0, activity: [] };
    else if (path === "/security/findings/finding") json = { repository, finding: current, can_write: options.canWrite ?? true,
      observations: [{ scan_id: "scan", outcome: "present", created_at: at, snapshot: {} }], activity: options.activity ?? [] };
    else if (path === "/security/findings/finding/github-issue") json = { title: "[Security] Order lookup does not verify ownership", body: "## Evidence\n\nThe lookup lacks an ownership check.\n\n## Remediation\n\nConstrain access to the authenticated account.", repository: repository.full_name, public: true, version: current.version };
    else if (path === "/connections") json = [{ id: "connection", name: "Team API", kind: "api", model: "test-model", provider: "openai" }];
    else if (path === "/connections/connection/models") json = { status: "ready", source: "provider", models: [{ id: "test-model", name: "Test model" }] };
    await route.fulfill({ json });
  });
  await mockReportFindings(page, saved.report.findings ?? []);
  return writes;
}

test("security navigation shows scan activity and clears it when scanning stops", async ({ page }, info) => {
  await mock(page);
  let latestStatus: string | null = null;
  let denied = false;
  await page.route("**/api/v1/repositories", (route) => route.fulfill(denied
    ? { status: 403, json: { detail: "Access denied" } }
    : { json: [{ ...repository, security: { ...summary, latest_status: latestStatus } }] }));
  for (const status of [null, "queued", "running", "blocked", "completed", "partial", "failed", "cancelled"]) {
    latestStatus = status;
    await page.goto("/security");
    await expect(page.getByRole("heading", { name: "Security", exact: true })).toBeVisible();
    if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
    const link = page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: /^Security/ });
    const active = status === "queued" || status === "running";
    await expect(link).toHaveAccessibleName(active ? "Security, scan running" : "Security");
    const spinner = link.locator(".motion-safe\\:animate-spin");
    await expect(spinner).toHaveCount(active ? 1 : 0);
    if (active) await expect(spinner).toHaveCSS("animation-name", "none");
    if (status === "running") {
      await page.emulateMedia({ reducedMotion: "no-preference" });
      await expect(spinner).toHaveCSS("animation-name", "spin");
      await page.screenshot({ path: info.outputPath("security-sidebar-running.png"), animations: "disabled" });
      await page.getByRole("navigation", { name: "Main navigation" }).screenshot({ path: info.outputPath("security-sidebar-preview.png"), animations: "disabled" });
      await page.emulateMedia({ reducedMotion: "reduce" });
    }
  }
  denied = true;
  latestStatus = "running";
  await page.goto("/security");
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await expect(nav.getByRole("alert")).toHaveText("Repository access denied.");
  await expect(nav.getByRole("link", { name: "Security", exact: true }).locator(".motion-safe\\:animate-spin")).toHaveCount(0);
});

test("security errors explain failed evidence and keep diagnostics collapsed and copyable", async ({ page }, info) => {
  const error = 'OCR returned an invalid verification verdict: Security evidence does not match its cited source lines.; response={"findings":[{"title":"Synthetic finding","evidence":[{"quote":"unmatched code"}]}]}';
  await mock(page, { scanError: error });
  await page.goto("/security/scans/scan");
  const alert = page.getByRole("alert").first();
  await expect(alert.getByText("AI citation mismatch", { exact: true })).toBeVisible();
  await expect(alert).toContainText("The source was read; this is not a missing-file error.");
  const widths = await alert.locator("p.leading-relaxed").evaluate((node) => ({
    message: node.getBoundingClientRect().width, available: node.parentElement!.getBoundingClientRect().width,
  }));
  expect(Math.abs(widths.message - widths.available)).toBeLessThan(1);
  const details = alert.getByRole("region", { name: "AI citation mismatch technical details", includeHidden: true });
  await expect(details).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-readable-error.png"), fullPage: true });
  await alert.getByText("Technical details", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(details).toBeVisible();
  await expect(details).toHaveText(error);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { writeText: async (value: string) => { document.body.dataset.copiedError = value; } } }));
  await alert.getByRole("button", { name: "Copy AI citation mismatch details", exact: true }).click();
  await expect(page.locator("body")).toHaveAttribute("data-copied-error", error);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  await page.getByText("Analysis work units", { exact: true }).click();
  const work = page.getByRole("tabpanel").getByRole("alert");
  await expect(work.getByText("AI citation mismatch", { exact: true })).toBeVisible();
  await expect(work.getByRole("region", { includeHidden: true })).toBeHidden();
});

for (const grade of ["A", "D"]) test(`finished reports show grade ${grade} without an incomplete badge suffix`, async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const error = "AI citation mismatch in docker-compose.yml: the quoted text or line numbers do not match the supplied source.";
  await mock(page, { partial: true, summary: { grade, grade_scan_id: "scan", counts: grade === "D" ? { high: 1 } : {} } });
  await mockReportFindings(page, grade === "D" ? [{ ...finding, finding_id: finding.id, outcome: "present" }] : []);
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: { ...scan, status: "partial", grade, error,
      progress: { tokens: 4500, work: { completed: 80, failed: 1, analysis_total: 58, analysis_completed: 57 } },
      stages: { ...scan.stages, analysis: { status: "partial", total: 58, completed: 57, failed: 1 } },
      report: { ...scan.report, grade, status: "partial", findings: grade === "D" ? [{ ...finding, finding_id: finding.id, outcome: "present" }] : [], problems: [error] } },
    can_write: true, finding_count: grade === "D" ? 1 : 0, activity_count: 0, activity: [],
  } }));
  await page.goto("/security");
  const badge = page.getByRole("link", { name: `Security grade ${grade}`, exact: true });
  await expect(badge).toBeVisible();
  await expect(badge.locator('[data-slot="security-grade-shield"]')).toHaveText(grade);
  await badge.click();
  await expect(page.getByText(/Based on confirmed findings; coverage is incomplete/)).toHaveCount(0);
  await expect(page.getByText(new RegExp(`^Grade ${grade} saved`))).toHaveCount(0);
  await expect(page.getByText(/^Saved .* for aaaaaaa\. Freshness checked/)).toBeVisible();
  await expect(page.getByRole("link", { name: `Security ${grade}`, exact: true })).toBeVisible();
  await page.screenshot({ path: `/private/tmp/code-mortis-repository-header-${grade}-${info.project.name}.png`, fullPage: true });
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => route.fulfill({ json: {
    items: [{ ...scan, status: "partial", grade }], total: 1, page: 1, page_size: 20,
  } }));
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  const row = page.locator('[data-slot="security-scan-row"]');
  await expect(row.locator('[data-slot="security-grade-shield"]')).toHaveText(grade);
  await expect(row).not.toContainText("Incomplete");
  await page.goto("/security/scans/scan");
  await expect(page.getByText(grade, { exact: true })).toBeVisible();
  const shield = page.locator('[data-slot="security-grade-shield"]');
  const status = shield.locator("../..").locator('[data-slot="badge"]');
  await expect(status).toHaveText("Completed");
  await expect(status).toHaveClass(/text-amber-800/);
  await expect(status.locator(".lucide-shield-alert")).toBeVisible();
  await expect(shield).toHaveText(grade);
  await expect(shield).toHaveCSS("width", "24px");
  await expect(shield).toHaveClass(grade === "A" ? /text-emerald-800/ : /text-red-700/);
  const trigger = shield.locator("..");
  await expect(trigger.locator('[data-slot="badge"]')).toHaveCount(0);
  await trigger.focus();
  await expect(page.getByRole("tooltip")).toHaveText("Grade based on confirmed findings.");
  await expect(page.getByRole("tooltip")).not.toContainText("Coverage is incomplete; unvalidated code may change the grade.");
  await page.keyboard.press("Escape");
  await expect(page.getByText(`${grade} · Incomplete`, { exact: true })).toHaveCount(0);
  const alert = page.getByRole("alert").first();
  await expect(alert.getByText("AI citation mismatch", { exact: true })).toBeVisible();
  await expect(alert).toContainText("docker-compose.yml");
  await expect(alert).toContainText("not a missing-file error");
  await expect(page.getByRole("group", { name: "Analysis stage" })).toContainText("57 / 58 files");
  await expect(page.getByRole("group", { name: "Analysis stage" }).getByText("partial", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-partial-grade-${grade}-${info.project.name}.png`, fullPage: true });
});

test("security overview keeps historical grades qualified and filters in the URL", async ({ page }, info) => {
  await mock(page, { partial: true });
  await page.goto("/security");
  await expect(page.getByRole("heading", { name: "Security", exact: true })).toBeVisible();
  await expect(page.getByText("Incomplete", { exact: true })).toBeVisible();
  await expect(page.getByText("1 unresolved", { exact: false })).toBeVisible();
  await expect(page.getByText("1 critical", { exact: true })).toHaveClass("sr-only");
  await page.getByRole("textbox", { name: "Search repositories" }).fill("payments");
  await expect(page).toHaveURL(/search=payments/);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Grade", exact: true }).click();
  await page.getByRole("checkbox", { name: "D", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "D", exact: true })).toBeChecked();
  await expect(page).toHaveURL(/search=payments&grade=D/);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search repositories" })).toHaveValue("payments");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-overview-${info.project.name}.png`, fullPage: true });
});

for (const [state, badgeLabel] of [["current", "D"], ["incomplete", "Incomplete"], ["outdated", "Outdated"], ["not_scanned", "Unscanned"], ["unavailable", "Unverified"]] as const) {
  test(`repository grade badge uses one label for ${state}`, async ({ page }) => {
    await mock(page, { summary: { state, ...(state === "not_scanned" ? { grade: null, grade_sha: null } : {}) } });
    await page.goto("/security");
    const badge = page.getByRole("link", { name: state === "current" ? `Security grade ${badgeLabel}` : badgeLabel, exact: true });
    await expect(badge).toHaveText(badgeLabel);
    await badge.focus();
    await expect(badge).not.toHaveAttribute("data-state");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    if (["incomplete", "outdated", "unavailable"].includes(state)) {
      await page.goto("/security/repositories/22");
      await expect(page.getByText(/Historical report saved/)).toBeVisible();
    }
  });
}

test("repository security badges put the state shield before Security and the grade", async ({ page }, info) => {
  const writes = await mock(page);
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  let current = { ...summary };
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [{ ...repository, security: current }] }));
  await page.route("**/api/v1/repositories/22/pulls?**", (route) => route.fulfill({ json: {
    page_size: 10, groups: [], total: 0, registered_total: 0, repository_count: 1, repositories: [], authors: [], state_counts: {},
  } }));
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: { repository, summary: current, can_write: true, finding_count: 1, scan_count: 1 } }));
  const cases: { change: Partial<Schema["SecuritySummary"]>; label: string; icon: string }[] = [
    { change: {}, label: "D", icon: "shield-check" },
    { change: { state: "incomplete", latest_status: "partial" }, label: "D", icon: "shield-alert" },
    { change: { state: "not_scanned", grade: null, latest_scan_id: null, latest_status: null }, label: "Unscanned", icon: "shield-question" },
    { change: { state: "outdated" }, label: "Outdated", icon: "shield-ellipsis" },
    { change: { state: "unavailable" }, label: "Unverified", icon: "shield-question" },
    ...["failed", "cancelled", "queued", "running", "blocked", "completed"].map((status, index) => ({ change: { state: "incomplete", latest_status: status }, label: "Incomplete",
      icon: ["shield-x", "shield-off", "shield-ellipsis", "shield-ellipsis", "shield-alert", "shield-alert"][index] })),
  ];
  for (const [index, item] of cases.entries()) {
    current = { ...summary, ...item.change };
    await page.goto("/repositories/22?state=open");
    const name = item.label === "Unscanned" ? item.label : `Security ${item.label}`;
    const badge = page.getByRole("link", { name, exact: true });
    await expect(badge).toHaveAttribute("href", "/security/repositories/22");
    await expect(badge.locator("svg")).toHaveCount(1);
    await expect(badge.locator(`.lucide-${item.icon}`)).toBeVisible();
    if (item.label === "D") await expect(badge.locator('[data-slot="badge"]')).toHaveClass(/text-red-700/);
    else await expect(badge.locator('[data-slot="badge"]')).not.toHaveClass(/text-(red|amber|emerald)-/);
    const beforeText = await badge.evaluate((element) => {
      const content = element.querySelector('[data-slot="badge"]')!;
      const text = [...content.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!;
      const range = document.createRange(); range.selectNodeContents(text);
      const icon = content.querySelector("svg")!.getBoundingClientRect();
      return icon.right <= range.getBoundingClientRect().left;
    });
    expect(beforeText).toBe(true);
    if (index < 3) await badge.screenshot({ path: `/private/tmp/code-mortis-security-label-${index}-${info.project.name}.png` });
    if (index < 3) {
      await badge.click();
      await expect(page).toHaveURL(/\/security\/repositories\/22$/);
      await expect(page.getByRole("link", { name, exact: true }).locator(`.lucide-${item.icon}`)).toBeVisible();
      if (item.label === "D") await expect(page.getByRole("link", { name: "Security D", exact: true }).locator('[data-slot="badge"]')).toHaveClass(/text-red-700/);
    }
  }
  expect(writes).toHaveLength(0);
});

for (const theme of ["light", "dark"] as const) test(`repository grade shields retain badge colors and compact sizing (${theme})`, async ({ page }, info) => {
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
  const writes = await mock(page);
  const grades = ["A", "B", "C", "D", "F"];
  const rows: Schema["SecurityRepositoryOut"][] = grades.map((grade, index) => ({ repository: { ...repository, id: 22 + index, full_name: `acme/service-${grade.toLowerCase()}` },
    summary: { ...summary, grade, counts: { high: grade === "A" ? 0 : 1 } } }));
  rows.push({ repository: { ...repository, id: 27, full_name: "acme/unscanned" }, summary: { ...summary, state: "not_scanned", grade: null,
    grade_scan_id: null, latest_scan_id: null, latest_status: null, latest_at: null, counts: {} } },
    { repository: { ...repository, id: 28, full_name: "acme/historical" }, summary: { ...summary, state: "outdated" } },
    { repository: { ...repository, id: 29, full_name: "acme/partial-report" }, summary: { ...summary, state: "incomplete", latest_status: "partial" } });
  await page.route("**/api/v1/security/repositories", (route) => route.fulfill({ json: rows }));
  await page.goto("/security");
  for (const grade of grades) {
    const row = page.locator('[data-slot="security-repository-row"]').filter({ has: page.getByRole("link", { name: `acme/service-${grade.toLowerCase()}`, exact: true }) });
    const link = row.getByRole("link", { name: `Security grade ${grade}`, exact: true });
    const svg = link.locator('[data-slot="security-grade-shield"]');
    await expect(svg.locator("text")).toHaveText(grade);
    await expect(svg).toHaveCSS("width", "24px");
    await expect(svg).toHaveCSS("height", "24px");
    await expect(svg).toHaveClass(grade === "A" ? /text-emerald-800.*dark:text-emerald-300/ : ["D", "F"].includes(grade) ? /text-red-700.*dark:text-red-300/ : /text-amber-800.*dark:text-amber-300/);
    const color = await svg.evaluate((element) => getComputedStyle(element).color);
    await expect(svg.locator("path")).toHaveCSS("stroke", color);
    await expect(svg.locator("text")).toHaveCSS("fill", color);
    await expect(svg.locator("text")).toHaveCSS("fill-opacity", "1");
    await expect(row.locator('[data-slot="status-group"]')).toHaveText("completed");
    const shieldBox = await svg.boundingBox();
    const repositoryBox = await row.getByRole("link", { name: `acme/service-${grade.toLowerCase()}`, exact: true }).boundingBox();
    expect(shieldBox!.x + shieldBox!.width).toBeLessThan(repositoryBox!.x);
  }
  const unknown = page.getByRole("link", { name: "Security grade unknown", exact: true });
  await expect(unknown).toHaveAttribute("href", "/security/repositories/27");
  await expect(unknown.locator("text")).toHaveText("?");
  await expect(unknown.locator("svg")).toHaveClass(/text-muted-foreground/);
  const unscanned = page.getByRole("link", { name: "Unscanned", exact: true });
  await expect(unscanned.locator("svg")).toHaveCount(1);
  await expect(unscanned.locator(".lucide-shield-question")).toBeVisible();
  const historical = page.getByRole("link", { name: "Historical security grade D, outdated", exact: true });
  await expect(historical.locator("svg")).toHaveClass(/text-muted-foreground/);
  const partial = page.locator('[data-slot="security-repository-row"]').filter({ hasText: "acme/partial-report" });
  await expect(partial.locator('[data-slot="security-grade-shield"]')).toHaveClass(/text-red-700/);
  await expect(partial.locator('[data-slot="status-group"] [data-slot="badge"]')).toHaveText("Completed");
  await expect(partial.getByRole("link", { name: "View latest scan: completed with warnings", exact: true }).locator('[data-slot="badge"]')).toHaveClass(/text-amber-800.*dark:text-amber-300/);
  await unknown.focus();
  await expect(unknown).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-grade-shields-${theme}-${info.project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: "Security grade A", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/security\/repositories\/22$/);
  expect(writes).toHaveLength(0);
});

test("scan dialog uses existing connection and configurable concurrency without enabling PR reviews", async ({ page }, info) => {
  const writes = await mock(page);
  await page.goto("/security");
  await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
  await page.getByRole("menuitem", { name: "Run scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toHaveValue("200000");
  await expect(dialog.getByRole("spinbutton", { name: "Timeout (seconds)" })).toHaveValue("900");
  const concurrency = dialog.getByRole("spinbutton", { name: "Concurrent tasks" });
  await expect(dialog.getByText(/independent finding assessments at a time/)).toBeVisible();
  await expect(concurrency).toHaveValue("2");
  await concurrency.fill("9");
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  expect(writes).toHaveLength(0);
  await concurrency.fill("4");
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).check();
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).uncheck();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-concurrency-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0].body).toMatchObject({ connection_id: "connection", model: "test-model", token_budget: 200000, timeout_seconds: 900, concurrency: 4 });
  expect(String(writes[0].body.request_key).length).toBeGreaterThan(16);
});

test("personal security connections keep concurrency at one", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [
    { id: "connection", name: "Personal account", kind: "chatgpt", model: "test-model", owner_id: 7 },
  ] }));
  await page.goto("/security");
  await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
  await page.getByRole("menuitem", { name: "Run scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog.getByRole("spinbutton", { name: "Concurrent tasks" })).toHaveValue("1");
  await expect(dialog.getByRole("spinbutton", { name: "Concurrent tasks" })).toBeDisabled();
  await expect(dialog.getByText("Personal connections run one task at a time.")).toBeVisible();
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0].body.concurrency).toBe(1);
});

test("legacy scan retries can increase concurrency while preserving model and limits", async ({ page }) => {
  const writes = await mock(page, { scanError: "Security analysis did not finish." });
  await page.goto("/security/scans/scan");
  await page.getByRole("button", { name: "Retry scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Retry security scan" });
  await expect(dialog.getByRole("spinbutton", { name: "Concurrent tasks" })).toHaveValue("1");
  await expect(dialog.getByRole("spinbutton", { name: "Concurrent tasks" })).toBeEnabled();
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toBeDisabled();
  await dialog.getByRole("spinbutton", { name: "Concurrent tasks" }).fill("4");
  await dialog.getByRole("button", { name: "Retry scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0]).toMatchObject({ path: "/security/scans/scan/retry", body: { concurrency: 4, token_budget: 200000, timeout_seconds: 900 } });
});

test("rescan can change concurrency after an incomplete scan", async ({ page }) => {
  const writes = await mock(page, { scanError: "Security analysis did not finish." });
  await page.goto("/security/scans/scan");
  await page.getByRole("button", { name: "Rescan repository", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog.getByRole("spinbutton", { name: "Concurrent tasks" })).toBeEnabled();
  await dialog.getByRole("spinbutton", { name: "Concurrent tasks" }).fill("4");
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0]).toMatchObject({ path: "/security/repositories/22/scans", body: { concurrency: 4 } });
});

test("saved scan exposes actionable report, coverage, findings and downloads", async ({ page }, info) => {
  await mock(page);
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("heading", { name: "Security scan", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Report 1", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download Markdown" })).toHaveAttribute("href", /format=markdown/);
  const report = page.getByRole("region", { name: "Findings at this scan", exact: true });
  await expect(report.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "Remediation", exact: true })).toHaveCount(0);
  await report.getByRole("button").click();
  const saved = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(saved.getByRole("tab", { name: "At this scan", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(saved.getByRole("heading", { name: "Remediation", exact: true })).toBeVisible();
  await expect(saved.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(report.getByRole("button")).toBeFocused();
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  await expect(page.getByText("Within 24-hour freshness limit", { exact: true })).toBeVisible();
  await page.getByText("Prepared source inventory", { exact: true }).click();
  await expect(page.getByText("src/orders.ts", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings 1", exact: true }).click();
  await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
  await expect(page.getByRole("dialog", { name: "Finding details", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Code snippet" })).toContainText("return orders.find(id)");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-finding-${info.project.name}.png`, fullPage: true });
});

test("report filters and pagination request server pages and preserve independent tab filters and downloads", async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page);
  const rows = [
    { ...finding, finding_id: finding.id, outcome: "present" },
    { ...finding, finding_id: "fixed", outcome: "fixed", title: "Restricted container port", path: "Dockerfile", kind: "infrastructure", severity: "medium" },
    { ...finding, finding_id: "dependency", outcome: "present", title: "Vulnerable dependency", path: "bun.lock", kind: "dependency", severity: "critical" },
    { ...finding, finding_id: "earlier", outcome: "not_observed", title: "Earlier policy observation", path: "infra/job.yml", kind: "custom_policy", severity: "low" },
    { ...finding, finding_id: "legacy", outcome: null, title: "Legacy observation", kind: null, severity: null },
    ...Array.from({ length: 21 }, (_, index) => ({ ...finding, finding_id: `uncertain-${index}`, outcome: "uncertain", title: `Saved observation ${index}`, severity: "low" })),
  ];
  const server = await mockReportFindings(page, rows);
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository,
    scan: { ...scan, report: { problems: ["Unvalidated result remains visible."] } },
    report_count: 26, can_write: true, finding_count: 5, activity_count: 0, activity: [],
  } }));
  await page.goto("/security/scans/scan?outcome=open_repeat&severity=low&assessment_outcome=withheld");
  const report = page.getByRole("region", { name: "Findings at this scan", exact: true });
  const cards = report.locator('[data-slot="finding-card"]');
  const search = page.getByRole("textbox", { name: "Search report findings", exact: true });
  await expect(cards).toHaveCount(20);
  await report.getByRole("button", { name: "Load more report findings", exact: true }).click();
  await expect(cards).toHaveCount(26);
  expect(server.requests.some((params) => params.get("page") === "2")).toBe(true);
  await search.fill("DOCKERFILE");
  await expect(cards).toHaveCount(1);
  await expect(report).toContainText("Restricted container port");
  await expect(page).toHaveURL(/report_q=DOCKERFILE/);
  expect(server.requests.at(-1)?.get("q")).toBe("DOCKERFILE");
  expect(server.requests.at(-1)?.get("page")).toBe("1");
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await page.getByRole("checkbox", { name: "Present 2", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(2);
  await page.getByRole("button", { name: "Edit Result: Present", exact: true }).click();
  await page.getByRole("checkbox", { name: "Fixed 1", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(3);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "High 1", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Infrastructure 1", exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Code 22", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "Report 26", exact: true })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "1 of 26 report findings" })).toBeAttached();
  await expect.poll(() => server.requests.at(-1)?.get("kind")).toBe("code");
  expect(server.requests.at(-1)?.getAll("outcome")).toEqual(["present", "fixed"]);
  expect(server.requests.at(-1)?.get("severity")).toBe("high");
  await expect(page.getByRole("link", { name: "Download JSON", exact: true })).toHaveAttribute("href", "/api/v1/security/scans/scan/report?format=json");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-report-filters-${info.project.name}.png`, fullPage: true });
  await cards.click();
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("tab", { name: "At this scan", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet.getByText("A signed-in user can retrieve another account's order.", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(cards).toBeFocused();
  await page.reload();
  await expect(cards).toHaveCount(1);
  await page.getByRole("tab", { name: "Findings 5", exact: true }).click();
  await expect(page.getByRole("button", { name: "Edit Severity: Low", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Report 26", exact: true }).click();
  await expect(cards).toHaveCount(1);
  await search.fill("no matching observation");
  await expect(page.getByRole("heading", { name: "No report findings match these filters", exact: true })).toBeVisible();
  await expect(page.getByText("Unvalidated result remains visible.", { exact: true })).toBeVisible();
  await page.getByRole("tabpanel").getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(cards).toHaveCount(20);
  await expect(search).toBeFocused();
  await expect(page).toHaveURL((url) => ![...url.searchParams.keys()].some((key) => key.startsWith("report_"))
    && url.searchParams.get("outcome") === "open_repeat" && url.searchParams.get("assessment_outcome") === "withheld");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Type", exact: true }).click();
  await page.getByRole("textbox", { name: "Search type options", exact: true }).fill("custom");
  await page.getByRole("checkbox", { name: "Custom policy 1", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(cards).toHaveCount(1);
  await expect(report).toContainText("Earlier policy observation");
  await page.goto("/security/scans/scan?report_outcome=unknown");
  await expect(cards).toHaveCount(1);
  await expect(report).toContainText("Legacy observation");
  server.state.failure = 503;
  await page.goto("/security/scans/scan?report_kind=dependency");
  await expect(page.getByText("Report temporarily unavailable", { exact: true })).toBeVisible();
  server.state.failure = 0;
  await page.getByRole("button", { name: "Retry report findings", exact: true }).click();
  await expect(cards).toHaveCount(1);
  server.state.failure = 403;
  await page.goto("/security/scans/scan?report_kind=code");
  await expect(page.getByText("Access denied", { exact: true })).toBeVisible();
  await expect(cards).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`security report uses compact shared finding cards and no inline remediation blocks (${theme})`, async ({ page }, info) => {
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    const writes = await mock(page, { scanError: "Security analysis did not finish." });
    const rows = ["Dockerfile", "Dockerfile.relay", "Dockerfile.runner"].map((path, index) => ({ ...finding, finding_id: `finding-${index}`,
      path, kind: "infrastructure", title: "No HEALTHCHECK defined", severity: "low", outcome: "present",
      evidence: { ...finding.evidence, explanation: "You should add a HEALTHCHECK instruction to your container image.",
        remediation: "Add a HEALTHCHECK instruction.", verification: "Rescan the changed configuration." } }));
    await mockReportFindings(page, rows);
    await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository,
      scan: { ...scan, status: "partial", grade: null, report: { commit: sha, status: "partial", grade: null, findings: rows } },
      can_write: true, finding_count: 3, activity_count: 0, activity: [] } }));
    await page.goto("/security/scans/scan");
    await expect(page.getByRole("tab", { name: "Report 3", exact: true })).toBeVisible();
    const report = page.getByRole("region", { name: "Findings at this scan", exact: true });
    const cards = report.locator('[data-slot="finding-card"]');
    await expect(cards).toHaveCount(3);
    await expect(cards.first()).toContainText("Dockerfile");
    await expect(cards.first()).toContainText("No HEALTHCHECK defined");
    await expect(cards.first().locator('[data-slot="badge"]')).toHaveCount(3);
    await expect(report.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Inspect finding", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /^(Remediation|Verification)$/ })).toHaveCount(0);
    if (info.project.name === "desktop") {
      const before = await cards.first().evaluate((node) => getComputedStyle(node).backgroundColor);
      await cards.first().hover();
      await expect.poll(() => cards.first().evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(before);
      await cards.first().getByText("present", { exact: true }).hover();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/private/tmp/code-mortis-security-report-${theme}-${info.project.name}.png`, fullPage: true });
    expect(writes).toHaveLength(0);
  });
}

test("saved report details preserve original evidence separately from current status and restore focus", async ({ page }, info) => {
  const writes = await mock(page);
  await page.clock.install();
  const currentSha = "b".repeat(40);
  await page.route("**/api/v1/security/findings/finding", (route) => route.fulfill({ json: { repository,
    finding: { ...finding, state: "resolved", version: 7, path: "src/account-orders.ts", evidence: { ...finding.evidence,
      source_sha: currentSha, remediation: "Current remediation after the verified fix." } }, can_write: true, observations: [], activity: [] } }));
  await page.goto("/security/scans/scan");
  const card = page.getByRole("region", { name: "Findings at this scan", exact: true }).getByRole("button");
  await card.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("tab", { name: "At this scan", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("resolved");
  await expect(sheet.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("link", { name: "View source", exact: true })).toHaveAttribute("href", `https://github.com/acme/payments/blob/${sha}/src/orders.ts`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-saved-evidence-${info.project.name}.png`, fullPage: true });
  await sheet.getByRole("tab", { name: "Current status", exact: true }).click();
  await expect(sheet.getByText("Current remediation after the verified fix.", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("link", { name: "View source", exact: true })).toHaveAttribute("href", `https://github.com/acme/payments/blob/${currentSha}/src/account-orders.ts`);
  await page.clock.fastForward(5000);
  await expect(sheet.getByRole("tab", { name: "Current status", exact: true })).toHaveAttribute("aria-selected", "true");
  await sheet.getByRole("tab", { name: "At this scan", exact: true }).click();
  await expect(sheet.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Current remediation after the verified fix.", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  await expect(page).not.toHaveURL(/finding=/);
  expect(writes).toHaveLength(0);
});

test("uncertain security candidates keep their assessment and evidence in a disclosure", async ({ page }) => {
  const writes = await mock(page);
  await mockReportFindings(page, []);
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository,
    scan: { ...scan, status: "partial", grade: null, report: { status: "partial", findings: [], unsettled_candidates: [{
      proposal: { title: "Potential configuration exposure", ...finding.evidence },
      assessment: { explanation: "Required deployment configuration was unavailable." },
    }] } }, can_write: true, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Report 0", exact: true })).toBeVisible();
  const disclosure = page.locator("details").filter({ hasText: "Potential configuration exposure" });
  await expect(disclosure).not.toHaveAttribute("open");
  await disclosure.locator("summary").click();
  await expect(disclosure.getByText("Required deployment configuration was unavailable.", { exact: true })).toBeVisible();
  await expect(disclosure.getByRole("heading", { name: "Remediation", exact: true })).toBeVisible();
  await expect(disclosure.getByRole("region", { name: "Code snippet", exact: true })).toContainText("return orders.find(id);");
  await expect(page.getByRole("button", { name: "Create GitHub issue", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
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

async function mockBulkIssues(page: Page, isPublic = true) {
  const writes = await mock(page);
  const state = { previewError: 0, submissionError: 0, requests: 0, items: Array.from({ length: 21 }, (_, index): Schema["SecurityIssueCandidate"] => ({
    id: `candidate-${index}`, title: `[Security] Order ownership check ${index + 1}`, path: `services/${"account-orders/".repeat(5)}route-${index + 1}.ts`,
    severity: index === 0 ? "high" : "medium", repository: repository.full_name, public: isPublic, version: index,
    body: `### Evidence\n\nOrder lookup ${index + 1} lacks an ownership check.\n\n### Remediation\n\nConstrain access to the authenticated account.`,
  })) };
  await page.route("**/api/v1/security/repositories/22/github-issues**", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON();
      writes.push({ path: "/security/repositories/22/github-issues", body });
      return route.fulfill(state.submissionError
        ? { status: state.submissionError, json: { detail: "The finding changed. Refresh its issue preview." } }
        : { status: 202, json: body.issues.map((draft: Schema["SecurityIssueDraft"]) => ({ ...finding, id: draft.finding_id, issue: { publication_status: "pending" } })) });
    }
    state.requests++;
    const pageNumber = Number(new URL(request.url()).searchParams.get("page") ?? 1);
    return route.fulfill(state.previewError
      ? { status: state.previewError, json: { detail: "Issue previews are unavailable." } }
      : { json: { items: state.items.slice((pageNumber - 1) * 20, pageNumber * 20), total: state.items.length,
        page: pageNumber, page_size: 20, repository: repository.full_name, public: isPublic } });
  });
  return { state, writes };
}

test("bulk GitHub issues require selection, review, count and public confirmation", async ({ page }, info) => {
  const { state, writes } = await mockBulkIssues(page);
  await page.goto("/security/repositories/22");
  const trigger = page.getByRole("button", { name: "Create GitHub issues…", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("button", { name: "Review 0 issues", exact: true })).toBeDisabled();
  await expect(dialog.getByText("Destination: acme/payments", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("note")).toContainText("These issues will be public.");
  await expect(dialog.getByText("0 issues selected · 21 eligible findings", { exact: true })).toBeVisible();
  await dialog.getByRole("checkbox", { name: `Select ${state.items[0].title}`, exact: true }).check();
  await page.screenshot({ path: `/private/tmp/code-mortis-bulk-select-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Load more findings" }).click();
  await dialog.getByRole("checkbox", { name: `Select ${state.items[20].title}`, exact: true }).check();
  await dialog.getByRole("button", { name: "Review 2 issues", exact: true }).click();
  await expect(dialog.getByText("2 issues selected for creation", { exact: true })).toBeVisible();
  const first = dialog.locator("details").first();
  await first.getByRole("textbox", { name: "Issue title", exact: true }).fill("Security: enforce order ownership");
  await page.keyboard.press("Enter");
  await first.getByRole("tab", { name: "Edit", exact: true }).click();
  await first.getByRole("textbox", { name: "Issue body", exact: true }).fill("A reviewed finding with a concrete ownership fix.");
  await first.getByRole("tab", { name: "Preview", exact: true }).click();
  await expect(first.getByText("A reviewed finding with a concrete ownership fix.", { exact: true })).toBeVisible();
  expect(writes).toHaveLength(0);
  expect(await dialog.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-bulk-review-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Back to selection", exact: true }).click();
  await expect(dialog.getByRole("checkbox", { name: `Select ${state.items[20].title}`, exact: true })).toBeChecked();
  await dialog.getByRole("button", { name: "Review 2 issues", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Issue title", exact: true }).first()).toHaveValue("Security: enforce order ownership");
  await dialog.getByRole("button", { name: "Create 2 issues", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "2 GitHub issues queued." })).toBeVisible();
  await expect(trigger).toBeFocused();
  expect(writes).toEqual([{ path: "/security/repositories/22/github-issues", body: { issues: [
    { finding_id: "candidate-0", title: "Security: enforce order ownership", body: "A reviewed finding with a concrete ownership fix.", version: 0, public: true },
    { finding_id: "candidate-20", title: state.items[20].title, body: state.items[20].body, version: 20, public: true },
  ] } }]);
});

test("bulk issue review preserves drafts on stale approval and cancellation never publishes", async ({ page }) => {
  const { state, writes } = await mockBulkIssues(page, false);
  await page.goto("/security/repositories/22");
  const trigger = page.getByRole("button", { name: "Create GitHub issues…", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("note")).toContainText("These issues will be private");
  await dialog.getByRole("button", { name: "Select shown", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Review 20 issues", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "Clear selection", exact: true }).click();
  await dialog.getByRole("checkbox").first().check();
  await dialog.getByRole("button", { name: "Review 1 issue", exact: true }).click();
  await dialog.getByRole("textbox", { name: "Issue title", exact: true }).fill("Reviewed draft to preserve");
  state.submissionError = 409;
  await dialog.getByRole("button", { name: "Create 1 issue", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("The finding changed.");
  await expect(dialog.getByRole("textbox", { name: "Issue title", exact: true })).toHaveValue("Reviewed draft to preserve");
  expect(writes).toHaveLength(1);
  await dialog.getByRole("button", { name: "Back to selection", exact: true }).click();
  state.items = [];
  await dialog.getByRole("button", { name: "Refresh findings", exact: true }).click();
  await expect(dialog.getByText("No eligible findings", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Review 0 issues", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await trigger.click();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(writes).toHaveLength(1);
});

test("bulk issue preview failures cannot publish and permission denial hides candidates", async ({ page }) => {
  const { state, writes } = await mockBulkIssues(page);
  state.previewError = 503;
  await page.goto("/security/repositories/22");
  const trigger = page.getByRole("button", { name: "Create GitHub issues…", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("button", { name: "Review 0 issues", exact: true })).toBeDisabled();
  state.previewError = 0;
  await dialog.getByRole("button", { name: "Refresh findings", exact: true }).click();
  await dialog.getByRole("checkbox").first().check();
  state.previewError = 403;
  await dialog.getByRole("button", { name: "Load more findings" }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Review 1 issue", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(writes).toHaveLength(0);
});

test("secret rotation requires an explicit reason and acknowledgment", async ({ page }) => {
  const writes = await mock(page, { secret: true });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await sheet.getByRole("button", { name: "Triage", exact: true }).click();
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
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Create GitHub issue" })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Save decision" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create GitHub issues…", exact: true })).toHaveCount(0);
});

test("security controls support keyboard focus and dark narrow layouts", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 900 });
  await page.goto("/security");
  const actions = page.getByRole("button", { name: "Actions for acme/payments", exact: true });
  await actions.focus();
  await page.keyboard.press("Enter");
  const run = page.getByRole("menuitem", { name: "Run scan", exact: true });
  await run.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  await expect(dialog).toBeVisible();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe("dark");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-dark-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(actions).toBeFocused();
});

for (const theme of ["light", "dark"] as const) {
test(`security repository rows share hover feedback and keyboard-accessible grade links (${theme})`, async ({ page }, info) => {
  await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
  const writes = await mock(page, { partial: true });
  await page.goto("/security");
  const row = page.locator('[data-slot="security-repository-row"]');
  const repository = row.getByRole("link", { name: "acme/payments", exact: true });
  if (info.project.name === "desktop") {
    const dateColumn = await page.locator('[data-slot="security-repository-heading"] > span').nth(3).boundingBox();
    const scanDate = await row.locator(":scope > time").boundingBox();
    expect(dateColumn).not.toBeNull();
    expect(scanDate).not.toBeNull();
    expect(Math.abs(dateColumn!.x - scanDate!.x)).toBeLessThanOrEqual(1);
    const centerOffset = await row.locator(":scope > div").first().evaluate((element) => {
      const icon = element.children[0].getBoundingClientRect();
      const details = element.children[1].getBoundingClientRect();
      return Math.abs(icon.y + icon.height / 2 - details.y - details.height / 2);
    });
    expect(centerOffset).toBeLessThanOrEqual(1);
    const background = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
    const owner = repository.locator("span");
    const ownerColor = await owner.evaluate((element) => getComputedStyle(element).color);
    await repository.hover();
    await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe(background);
    await expect(owner).not.toHaveCSS("color", ownerColor);
    await expect(owner).toHaveCSS("color", await repository.evaluate((element) => getComputedStyle(element).color));
    await page.screenshot({ path: info.outputPath("security-repository-name-hover.png"), fullPage: true });
  }
  const grade = page.getByRole("link", { name: "Incomplete", exact: true });
  await expect(grade).not.toHaveAttribute("title");
  await grade.focus();
  await expect(grade).not.toHaveAttribute("data-state");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  expect(await grade.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe("solid");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toBeHidden();
  await page.screenshot({ path: info.outputPath("security-overview-aligned.png"), fullPage: true });
  await repository.click();
  await expect(page).toHaveURL(/\/security\/repositories\/22$/);
  expect(writes).toHaveLength(0);
});
}

test("repository action menu checks access only when opened and preserves navigation", async ({ page }, info) => {
  const writes = await mock(page);
  let reads = 0;
  await page.route("**/api/v1/security/repositories/22", (route) => { reads++; return route.fallback(); });
  await page.goto("/security");
  const actions = page.getByRole("button", { name: "Actions for acme/payments", exact: true });
  await expect(actions).toBeVisible();
  expect(reads).toBe(0);
  await expect(page.getByRole("button", { name: "Run scan", exact: true })).toHaveCount(0);
  await actions.click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem", { name: "Run scan", exact: true })).toBeEnabled();
  expect(reads).toBe(1);
  await expect(menu.getByRole("menuitem", { name: "View repository security", exact: true })).toHaveAttribute("href", "/security/repositories/22");
  await expect(menu.getByRole("menuitem", { name: "Repository settings", exact: true })).toHaveAttribute("href", "/repositories/22/settings");
  await expect(menu.getByRole("menuitem", { name: "Open on GitHub", exact: true })).toHaveAttribute("href", "https://github.com/acme/payments");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-repository-actions.png"), fullPage: true });
  await menu.getByRole("menuitem", { name: "View latest scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/scan$/);
  expect(writes).toHaveLength(0);
});

test("read-only repository action menus keep scan inspection without offering execution", async ({ page }) => {
  const writes = await mock(page, { canWrite: false });
  await page.goto("/security");
  await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByText("You have read-only access to this repository.", { exact: true })).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Run scan", exact: true })).toHaveCount(0);
  await expect(menu.getByRole("menuitem", { name: "View latest scan", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Actions for acme/payments", exact: true })).toBeFocused();
  expect(writes).toHaveLength(0);
});

test("repository action errors can retry without starting a scan", async ({ page }) => {
  const writes = await mock(page);
  let available = false;
  await page.route("**/api/v1/security/repositories/22", (route) => available ? route.fallback() : route.fulfill({ status: 503, json: { detail: "GitHub is temporarily unavailable." } }));
  await page.goto("/security");
  await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("alert")).toBeVisible();
  await expect(menu.getByRole("menuitem", { name: "Run scan", exact: true })).toHaveCount(0);
  available = true;
  await menu.getByRole("menuitem", { name: "Retry actions", exact: true }).click();
  await expect(menu.getByRole("menuitem", { name: "Run scan", exact: true })).toBeEnabled();
  expect(writes).toHaveLength(0);
});

for (const status of ["queued", "running", "blocked"]) {
  test(`repository action menu prevents a competing ${status} scan`, async ({ page }) => {
    const writes = await mock(page, { latestStatus: status });
    await page.goto("/security");
    await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
    const menu = page.getByRole("menu");
    await expect(menu.getByRole("menuitem", { name: "Run scan", exact: true })).toBeDisabled();
    await expect(menu.getByRole("menuitem", { name: "View latest scan", exact: true })).toBeVisible();
    expect(writes).toHaveLength(0);
  });
}

for (const status of ["queued", "running", "blocked", "completed", "partial", "failed", "cancelled"]) {
  test(`repository links directly to its latest ${status} scan`, async ({ page }, info) => {
    if (status === "running") {
      await page.addInitScript(() => localStorage.setItem("theme", "dark"));
      await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 1000 });
    }
    const writes = await mock(page, { latestStatus: status, running: status === "running" });
    await page.goto("/security/repositories/22");
    const latest = page.getByRole("region", { name: "Latest security scan", exact: true });
    await expect(latest.getByText(status, { exact: true })).toBeVisible();
    const inspect = latest.getByRole("link", { name: "Inspect scan", exact: true });
    await expect(inspect).toHaveAttribute("href", "/security/scans/scan");
    const run = page.getByRole("button", { name: "Run scan", exact: true });
    if (["queued", "running", "blocked"].includes(status)) await expect(run).toBeDisabled();
    else await expect(run).toBeEnabled();
    if (info.project.name === "desktop") {
      await inspect.hover();
      await expect(inspect).toHaveCSS("text-decoration-line", "underline");
    }
    await inspect.focus();
    await expect(inspect).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (status === "running") await page.screenshot({ path: info.outputPath("security-repository-running-scan-link.png"), fullPage: true });
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/security\/scans\/scan$/);
    await expect(page.getByRole("heading", { name: "Security scan", exact: true })).toBeVisible();
    expect(writes).toHaveLength(0);
  });
}

for (const head of ["b".repeat(40), null]) test(`latest scan metadata uses the current commit when ${head ? "known" : "unavailable"}`, async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page, { summary: { state: head ? "outdated" : "unavailable", head_sha: head } });
  await page.goto("/security/repositories/22");
  const latest = page.getByRole("region", { name: "Latest security scan", exact: true });
  await expect(latest.locator("time")).toHaveAttribute("datetime", at);
  if (head) {
    await expect(latest.getByText("Current commit · bbbbbbb", { exact: true })).toBeVisible();
    await expect(latest.locator("code")).toHaveAttribute("title", head);
    await expect(latest.locator("code")).toHaveCSS("font-size", "12px");
    await latest.screenshot({ path: `/private/tmp/code-mortis-latest-scan-commit-${info.project.name}.png` });
  } else await expect(latest.locator("code")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toHaveLength(0);
});

test("repository without a scan does not show an inspection link", async ({ page }) => {
  const writes = await mock(page, { canWrite: false, summary: { latest_scan_id: null, latest_status: null, latest_at: null } });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("heading", { name: "acme/payments", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Inspect scan", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Latest security scan", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

test("read-only repository users can inspect a running scan", async ({ page }) => {
  await mock(page, { canWrite: false, latestStatus: "running", running: true });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("button", { name: "Run scan", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "Inspect scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/scan$/);
});

test("running scan stages explain validated file progress without changing the scan", async ({ page }, info) => {
  const writes = await mock(page, { running: true });
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Report 0", exact: true })).toBeVisible();
  const stage = page.getByRole("group", { name: "Analysis stage", exact: true });
  await expect(stage).toContainText("11 / 163 files");
  await stage.locator("[data-security-badge]").focus();
  await expect(page.getByRole("tooltip")).toContainText("A file counts only after its result passes validation.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Cancel scan", exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("security-running-aligned.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toHaveLength(0);
});

for (const [status, icon] of [["queued", "loader-circle"], ["running", "loader-circle"], ["completed", "shield-check"],
  ["partial", "shield-alert"], ["failed", "shield-x"], ["cancelled", "shield-off"], ["blocked", "shield-alert"], ["unknown", "shield-question"]]) {
  test(`latest scan ${status} badge has no tooltip or underline on hover or focus`, async ({ page }, info) => {
    if (info.project.name === "mobile" || status === "running" || status === "failed") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
    const writes = await mock(page, { latestStatus: status });
    await page.goto("/security");
    const badge = page.getByRole("link", { name: `View latest scan: ${status === "partial" ? "completed with warnings" : status}`, exact: true });
    await expect(badge.locator('[data-slot="badge"]')).toHaveText(status === "partial" ? "Completed" : status);
    await expect(badge.locator("svg")).toHaveCount(1);
    await expect(badge.locator(`.lucide-${icon}`)).toBeVisible();
    expect(await badge.evaluate((element) => {
      const content = element.querySelector('[data-slot="badge"]')!;
      const text = [...content.childNodes].find((node) => node.nodeType === Node.TEXT_NODE)!;
      const range = document.createRange(); range.selectNodeContents(text);
      return content.querySelector("svg")!.getBoundingClientRect().left >= range.getBoundingClientRect().right;
    })).toBe(true);
    if (["completed", "partial"].includes(status)) await badge.screenshot({ path: `/private/tmp/code-mortis-scan-status-${status}-${info.project.name}.png` });
    if (status === "partial") await expect(badge.locator('[data-slot="badge"]')).toHaveClass(/bg-amber-700\/8.*text-amber-800/);
    const background = await badge.evaluate((element) => getComputedStyle(element).backgroundColor);
    if (info.project.name === "desktop") await badge.hover();
    else await badge.focus();
    await expect(badge).not.toHaveAttribute("data-state");
    await expect(badge).not.toHaveAttribute("title");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(badge).toHaveCSS("cursor", "pointer");
    await expect(badge).toHaveCSS("text-decoration-line", "none");
    await expect(badge).toHaveCSS("background-color", background);
    for (const segment of await badge.locator('[data-slot="badge"]').all()) await expect(segment).toHaveCSS("text-decoration-line", "none");
    expect(await badge.evaluate((element) => {
      for (let current: Element | null = element; current; current = current.parentElement) {
        if (getComputedStyle(current).textDecorationLine.includes("underline")) return true;
      }
      return false;
    })).toBe(false);
    if (status === "running") await page.screenshot({ path: info.outputPath("security-running-badge-hover.png"), fullPage: true });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toBeHidden();
    await badge.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/security\/scans\/scan$/);
    expect(writes).toHaveLength(0);
  });
}

for (const [name, counts, total, breakdown] of [
  ["one severity", { low: 3 }, 3, "3 low"],
  ["mixed severities", { high: 1, low: 3 }, 4, "1 high, 3 low"],
  ["no findings", {}, 0, ""],
] as const) {
  test(`overview separates ${name} from the grade and scan capsule like Inbox`, async ({ page }, info) => {
    await page.addInitScript(() => localStorage.setItem("theme", "dark"));
    const writes = await mock(page, { partial: true, latestStatus: "running", summary: { grade: "A", counts } });
    await page.goto("/security");
    const row = page.locator('[data-slot="security-repository-row"]');
    const badge = page.getByRole("link", { name: "View latest scan: running", exact: true });
    const capsule = row.locator('[data-slot="status-group"]');
    const segments = capsule.locator('[data-slot="badge"]');
    await expect(segments).toHaveCount(2);
    await expect(segments.first()).toHaveText("Incomplete");
    await expect(segments.last()).toHaveText("running");
    await expect(capsule.getByRole("link", { name: "A", exact: true })).toHaveCount(0);
    const findings = row.locator(':scope > div > [data-slot="badge"]');
    await expect(findings).toHaveText(`${total} unresolved findings`);
    await expect(findings).toHaveClass(total ? /text-destructive/ : /text-muted-foreground/);
    if (breakdown) await expect(row.getByText(breakdown, { exact: true })).toHaveClass("sr-only");
    await expect(capsule).toHaveCSS("border-top-width", "1px");
    for (const segment of await segments.all()) {
      await expect(segment).toHaveCSS("border-top-width", "0px");
      await expect(segment).toHaveCSS("border-top-left-radius", "0px");
    }
    const first = await segments.first().boundingBox();
    const last = await segments.last().boundingBox();
    expect(first?.y).toBe(last?.y);
    expect(Math.abs(first!.x + first!.width - last!.x)).toBeLessThanOrEqual(1);
    const findingsBox = await findings.boundingBox();
    expect(findingsBox!.x).toBeGreaterThan(last!.x + last!.width);
    expect(Math.abs(findingsBox!.y + findingsBox!.height / 2 - first!.y - first!.height / 2)).toBeLessThan(1);
    expect(await segments.first().evaluate((element) => getComputedStyle(element).backgroundColor))
      .not.toBe(await segments.last().evaluate((element) => getComputedStyle(element).backgroundColor));
    if (info.project.name === "desktop") await badge.hover();
    else await badge.focus();
    await expect(badge).not.toHaveAttribute("data-state");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(page.locator("[data-security-badge] [data-security-badge]")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const scanDate = row.locator(":scope > time");
    await expect(scanDate).toHaveText("Oct 18");
    await expect(scanDate).toHaveAttribute("datetime", at);
    await expect(scanDate).toHaveAttribute("title", /2026/);
    await page.screenshot({ path: info.outputPath("security-inbox-badges.png"), fullPage: true });
    expect(writes).toHaveLength(0);
  });
}

test("unscanned repositories keep the unrated state beside a separate zero count", async ({ page }) => {
  await mock(page, { summary: { state: "not_scanned", grade: null, latest_scan_id: null, latest_status: null, latest_at: null, counts: {} } });
  await page.goto("/security");
  const row = page.locator('[data-slot="security-repository-row"]');
  await expect(row.locator('[data-slot="status-group"] [data-slot="badge"]')).toHaveText("Unscanned");
  await expect(row.getByRole("link", { name: /View latest scan/ })).toHaveCount(0);
  await expect(row.getByText("0 unresolved", { exact: false })).toBeVisible();
  await expect(row.getByText("Not scanned", { exact: true })).toBeVisible();
});

test("security list and repository badges stay informational while scan stages retain their help", async ({ page }, info) => {
  const writes = await mock(page, { partial: true, latestStatus: "running" });
  await page.goto("/security");
  const row = page.locator('[data-slot="security-repository-row"]');
  await expect(row).toBeVisible();
  await expect(row.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
  for (const badge of await row.locator("[data-security-badge]").all()) {
    if (info.project.name === "desktop") await badge.hover();
    else await badge.focus();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await expect(badge).toHaveCSS("text-decoration-line", "none");
  }
  await page.goto("/security/repositories/22");
  const latest = page.getByRole("region", { name: "Latest security scan", exact: true });
  await expect(latest.getByText("running", { exact: true })).toBeVisible();
  const card = page.locator('[data-slot="finding-card"]');
  await expect(card).toBeVisible();
  await expect(card.locator('[data-slot="badge"]')).toHaveCount(3);
  await expect(page.locator('main [data-slot="tooltip-trigger"]')).toHaveCount(0);
  await expect(page.locator('main [data-slot="badge"][tabindex="0"]')).toHaveCount(0);
  if (info.project.name === "desktop") {
    await latest.getByText("running", { exact: true }).hover();
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    for (const badge of await card.locator('[data-slot="badge"]').all()) {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
    }
  }
  await card.click();
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet).toBeVisible();
  await expect(sheet.locator('[data-slot="badge"]')).toHaveCount(3);
  await expect(sheet.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
  if (info.project.name === "desktop") {
    for (const badge of await sheet.locator('[data-slot="badge"]').all()) {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
    }
  }
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  const history = page.locator('[data-slot="security-scan-row"]');
  await expect(history.locator('[data-slot="badge"]')).toHaveCount(1);
  await expect(history.getByRole("img", { name: "Saved security grade D", exact: true })).toBeVisible();
  await expect(history.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
  if (info.project.name === "desktop") {
    for (const badge of await history.locator('[data-slot="badge"]').all()) {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toHaveCount(0);
      await expect(history).toHaveCSS("text-decoration-line", "none");
    }
  }
  await page.goto("/security/scans/scan");
  const stage = page.getByRole("group", { name: "Analysis stage", exact: true });
  if (info.project.name === "desktop") await stage.locator("[data-security-badge]").hover();
  else await stage.locator("[data-security-badge]").focus();
  await expect(page.getByRole("tooltip")).toContainText("A file counts only after its result passes validation");
  expect(writes).toHaveLength(0);
});

test("security finding sheet separates evidence and activity while keeping actions visible through scrolling and polling", async ({ page }, info) => {
  const writes = await mock(page, { activity: [{ id: "observed", scan_id: "scan", finding_id: "finding", actor_id: null, action: "finding_observed", detail: { reason: "Confirmed against saved source." }, created_at: at }] });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByText("src/orders.ts", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Current status", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("tab", { name: "Evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet.getByRole("heading", { name: "Order lookup does not verify ownership", exact: true })).toBeVisible();
  const scroll = sheet.locator('[data-slot="finding-scroll"]');
  const footer = sheet.locator('[data-slot="security-finding-actions"]');
  await expect(scroll.locator('[data-slot="security-finding-actions"]')).toHaveCount(0);
  await scroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expect(footer.getByRole("button", { name: "Create GitHub issue", exact: true })).toBeInViewport();
  await expect(footer.getByRole("button", { name: "Triage", exact: true })).toBeInViewport();
  await sheet.getByRole("button", { name: "View activity", exact: true }).click();
  const activity = sheet.getByRole("tab", { name: "Activity 2", exact: true });
  await expect(activity).toBeFocused();
  await expect(activity).toHaveAttribute("aria-selected", "true");
  await expect(sheet.getByRole("heading", { name: "Observations", exact: true })).toBeVisible();
  await sheet.getByText("View reason", { exact: true }).click();
  await expect(sheet.getByText("Confirmed against saved source.", { exact: true })).toBeVisible();
  await page.waitForResponse((response) => response.url().endsWith("/api/v1/security/findings/finding") && response.request().method() === "GET");
  await expect(activity).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/finding_tab=activity/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-finding-activity.png"), fullPage: true });
  expect(writes).toHaveLength(0);
});

test("security finding triage requires a reason and keeps the verified-resolution boundary", async ({ page }) => {
  const writes = await mock(page);
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await sheet.getByRole("button", { name: "Triage", exact: true }).click();
  const save = sheet.getByRole("button", { name: "Save decision", exact: true });
  await expect(save).toBeDisabled();
  await sheet.getByRole("combobox", { name: "Decision", exact: true }).selectOption("accepted_risk");
  await sheet.getByRole("textbox", { name: "Reason", exact: true }).fill("Restricted to an isolated environment until remediation.");
  await save.click();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ path: "/security/findings/finding", body: { state: "accepted_risk", reason: "Restricted to an isolated environment until remediation.", version: 0 } });
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("accepted risk");
  await expect(sheet.getByRole("button", { name: "Triage", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(sheet.getByRole("button", { name: "Resolve finding", exact: true })).toHaveCount(0);
});

test("security finding footer preserves linked issue failures and separate finding status", async ({ page }) => {
  const writes = await mock(page, { issue: { number: 42, url: "https://github.com/acme/payments/issues/42", remote_state: "closed", sync_status: "failed", error: "GitHub is temporarily unavailable." } });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  const footer = sheet.locator('[data-slot="security-finding-actions"]');
  await expect(footer.getByRole("link", { name: "View issue #42", exact: true })).toHaveAttribute("href", "https://github.com/acme/payments/issues/42");
  await expect(footer.getByRole("alert")).toBeVisible();
  await expect(footer.getByText(/This finding remains unresolved until a fix is verified/)).toBeVisible();
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("open");
  await expect(footer.getByRole("button", { name: "Create GitHub issue", exact: true })).toHaveCount(0);
  await footer.getByRole("button", { name: "Retry issue synchronization", exact: true }).click();
  expect(writes).toHaveLength(1);
  expect(writes[0].path).toBe("/security/findings/finding/github-issue/sync");
});

test("security finding sheet hides evidence and mutations after access is denied", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/security/findings/finding", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("alert")).toContainText("Repository access was revoked.");
  await expect(sheet.getByRole("tablist", { name: "Finding information", exact: true })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Triage", exact: true })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Create GitHub issue", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

test("scan history fills missing grades without substituting a previous grade", async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page);
  const statuses = [["partial", "shield-alert"], ["failed", "shield-x"], ["cancelled", "shield-off"],
    ["blocked", "shield-alert"], ["running", "loader-circle"], ["queued", "loader-circle"]];
  const otherGrades = ["A", "B", "C", "F"];
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => route.fulfill({ json: {
    items: [...statuses.map(([status]) => ({ ...scan, id: status, status, grade: null })), { ...scan, id: "warnings", status: "partial" }, scan,
      ...otherGrades.map((grade) => ({ ...scan, id: `grade-${grade}`, grade }))], total: 12, page: 1, page_size: 20,
  } }));
  await page.goto("/security/repositories/22?tab=history");
  for (const [status, icon] of statuses) {
    const row = page.locator(`[data-slot="security-scan-row"][href="/security/scans/${status}"]`);
    await expect(row.locator('[data-slot="badge"]').first()).toHaveText("Unrated");
    await expect(row.locator('[data-slot="security-grade-shield"]')).toHaveCount(0);
    const badge = row.locator('[data-slot="badge"]').last();
    await expect(badge).toHaveText(status === "partial" ? "Completed" : status);
    await expect(badge.locator("svg")).toHaveCount(1);
    await expect(badge.locator(`.lucide-${icon}`)).toBeVisible();
    await expect(row.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
    await expect(row.locator("a")).toHaveCount(0);
  }
  const completed = page.locator('[data-slot="security-scan-row"][href="/security/scans/scan"]');
  for (const grade of ["A", "B", "C", "D", "F"]) {
    const row = page.locator(`[data-slot="security-scan-row"][href="/security/scans/${grade === "D" ? "scan" : `grade-${grade}`}"]`);
    const shield = row.getByRole("img", { name: `Saved security grade ${grade}`, exact: true }).locator('[data-slot="security-grade-shield"]');
    await expect(shield).toHaveText(grade);
    await expect(shield).toHaveCSS("width", "24px");
    await expect(shield).toHaveCSS("height", "24px");
    await expect(shield).toHaveClass(grade === "A" ? /text-emerald-800/ : ["D", "F"].includes(grade) ? /text-red-700/ : /text-amber-800/);
    await expect(row.locator("a")).toHaveCount(0);
  }
  await expect(completed.locator('[data-slot="badge"]').last()).toHaveClass(/text-emerald-800/);
  await expect(completed.locator(".lucide-shield-check")).toBeVisible();
  const warning = page.locator('[data-slot="security-scan-row"][href="/security/scans/warnings"]');
  await expect(warning.getByRole("img", { name: "Saved security grade D", exact: true }).locator('[data-slot="security-grade-shield"]')).toHaveClass(/text-red-700/);
  await expect(warning.locator('[data-slot="badge"]').last()).toHaveText("Completed");
  await expect(warning.locator('[data-slot="badge"]').last()).toHaveClass(/text-amber-800/);
  await expect(warning.locator(".lucide-shield-alert")).toBeVisible();
  await expect(warning).toContainText("with warnings");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-history-badges-${info.project.name}.png`, fullPage: true });
  await completed.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/security\/scans\/scan$/);
  expect(writes).toHaveLength(0);
});

test("repository security tabs show stable totals before opening lists and refresh with metadata", async ({ page }, info) => {
  await mock(page);
  await page.clock.install();
  let totals = { finding_count: 3, scan_count: 4 };
  let findingRequests = 0;
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: { repository, summary, can_write: true, ...totals } }));
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => route.fulfill({ json: {
    items: Array.from({ length: 4 }, (_, index) => ({ ...scan, id: `scan-${index}` })), total: 4, page: 1, page_size: 20,
  } }));
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => {
    findingRequests++;
    const filtered = new URL(route.request().url()).searchParams.has("search");
    return route.fulfill({ json: { items: filtered ? [] : Array.from({ length: 3 }, (_, index) => ({ ...finding, id: `finding-${index}` })),
      total: filtered ? 0 : 3, page: 1, page_size: 20 } });
  });
  await page.goto("/security/repositories/22?tab=history");
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scan history 4", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-slot="security-scan-row"]')).toHaveCount(4);
  expect(findingRequests).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-tab-counts-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Findings 3", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(3);
  await page.getByRole("textbox", { name: "Search findings", exact: true }).fill("missing");
  await expect(page.getByText("No findings in this view", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  totals = { finding_count: 4, scan_count: 5 };
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tab", { name: "Findings 4", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scan history 5", exact: true })).toBeVisible();
});

test("scan activity shows past stage transitions separately from the current stage state", async ({ page }, info) => {
  const writes = await mock(page);
  const activity = [
    { id: "analysis-started", action: "scan_stage", detail: { stage: "analysis", status: "running" }, created_at: "2026-10-18T15:00:03Z" },
    { id: "trivy-completed", action: "scan_stage", detail: { stage: "trivy", status: "completed" }, created_at: "2026-10-18T15:00:02Z" },
    { id: "trivy-started", action: "scan_stage", detail: { stage: "trivy", status: "running" }, created_at: "2026-10-18T15:00:00Z" },
    { id: "snapshot-partial", action: "scan_stage", detail: { stage: "snapshot", status: "partial" }, created_at: "2026-10-18T14:59:00Z" },
  ].map((row) => ({ ...row, scan_id: "scan", finding_id: null, actor_id: null }));
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository,
    scan: { ...scan, status: "running", grade: null, stages: { ...scan.stages, analysis: { status: "running" } } },
    can_write: true, finding_count: 1, activity_count: activity.length, activity } }));
  await page.goto("/security/scans/scan?tab=activity");
  await expect(page.getByRole("group", { name: "Trivy stage", exact: true })).toContainText("completed");
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByText("Trivy started", { exact: true })).toBeVisible();
  await expect(panel.getByText("Trivy completed", { exact: true })).toBeVisible();
  await expect(panel.getByText("Analysis started", { exact: true })).toBeVisible();
  await expect(panel.getByText("Snapshot finished with incomplete work", { exact: true })).toBeVisible();
  await expect(panel.locator("li").filter({ hasText: "Trivy" })).toHaveCount(2);
  await expect(panel.locator('[data-slot="badge"], .animate-spin, [data-slot="tooltip-trigger"]')).toHaveCount(0);
  const times = panel.locator("li").filter({ hasText: "Trivy" }).locator("time");
  await expect(times.first()).toHaveAttribute("datetime", "2026-10-18T15:00:02Z");
  await expect(times.last()).toHaveAttribute("datetime", "2026-10-18T15:00:00Z");
  expect(await times.first().innerText()).not.toBe(await times.last().innerText());
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-activity-events-${info.project.name}.png`, fullPage: true });
  expect(writes).toHaveLength(0);
});

test("security scan tabs count saved report observations separately from current findings and activity", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  const findings = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `finding-${index}` }));
  await mockReportFindings(page, findings.slice(0, 3).map((finding) => ({ ...finding, finding_id: finding.id, outcome: "present" })));
  const activity = Array.from({ length: 200 }, (_, index) => ({ id: `event-${index}`, scan_id: scan.id, finding_id: null,
    actor_id: null, action: "finding_observed", detail: {}, created_at: at }));
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: { ...scan, report: { findings: findings.slice(0, 3).map((finding) => ({ ...finding, finding_id: finding.id, outcome: "present" })) } },
    can_write: true, finding_count: 23, activity_count: 201, activity,
  } }));
  await page.route("**/api/v1/security/scans/scan/findings?**", (route) => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({ json: { items: findings.slice((pageNumber - 1) * 20, pageNumber * 20), total: 23, page: pageNumber, page_size: 20 } });
  });
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Report 3", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Findings at this scan", exact: true }).locator('[data-slot="finding-card"]')).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "Findings 23", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Activity 201", exact: true })).toBeVisible();
  const tabList = await page.getByRole("tablist").boundingBox();
  const lastTab = await page.getByRole("tab", { name: "Activity 201", exact: true }).boundingBox();
  expect(lastTab!.y + lastTab!.height).toBeLessThanOrEqual(tabList!.y + tabList!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-scan-tab-counts-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Findings 23", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(20);
  await page.getByRole("button", { name: "Load more findings", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(23);
  await expect(page.getByRole("tab", { name: "Findings 23", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Report 3", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Activity 201", exact: true }).click();
  await expect(page.getByText("Showing the latest 200 of 201 activities.", { exact: true })).toBeVisible();
});

test("scan findings filter new and repeated observations like review runs without changing saved reports", async ({ page }, info) => {
  await mock(page);
  await mockReportFindings(page, [{ ...finding, finding_id: finding.id, outcome: "present", title: "Original report evidence" }]);
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const rows = [
    { result: "new", finding: { ...finding, title: "New defect" } },
    ...Array.from({ length: 21 }, (_, index) => ({ result: "open_repeat", finding: { ...finding, id: `repeat-${index}`, title: `Earlier defect ${index + 1}`, first_scan_id: "earlier", severity: "medium" } })),
    ...["resolved", "false_positive", "accepted_risk"].map((state) => ({ result: `${state}_repeat`, finding: { ...finding, id: state, title: `${state} repeated defect`, first_scan_id: "earlier", state } })),
    ...["fixed", "not_observed", "uncertain"].map((outcome) => ({ result: outcome, finding: { ...finding, id: outcome, title: `Verification ${outcome}`, first_scan_id: "earlier" } })),
  ];
  const resultCounts = Object.fromEntries([...new Set(rows.map((row) => row.result))].map((key) => [key, rows.filter((row) => row.result === key).length]));
  const writes: string[] = [];
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository, scan: { ...scan,
    report: { findings: [{ ...finding, finding_id: finding.id, outcome: "present", title: "Original report evidence" }] } },
    can_write: true, finding_count: rows.length, activity_count: 0, activity: [] } }));
  await page.route("**/api/v1/security/scans/scan/findings**", (route) => {
    const request = route.request();
    if (request.method() !== "GET") writes.push(request.method());
    const params = new URL(request.url()).searchParams;
    const selected = rows.filter((row) => (!params.has("outcome") || params.getAll("outcome").includes(row.result))
      && (!params.has("severity") || row.finding.severity === params.get("severity"))
      && (!params.has("search") || row.finding.title.toLowerCase().includes(params.get("search")!.toLowerCase())));
    const pageNumber = Number(params.get("page") ?? 1);
    return route.fulfill({ json: { items: selected.slice((pageNumber - 1) * 20, pageNumber * 20).map((row) => row.finding),
      total: selected.length, page: pageNumber, page_size: 20, result_counts: resultCounts } });
  });
  await page.goto("/security/scans/scan?tab=findings&assessment_outcome=withheld");
  const list = page.getByRole("region", { name: "Current findings for this scan", exact: true });
  await expect(page.getByRole("tab", { name: "Findings 28", exact: true })).toBeVisible();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(20);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /^Open repeats/ })).toHaveAccessibleName("Open repeats 21");
  await page.getByRole("checkbox", { name: /^Open repeats/ }).click();
  await page.keyboard.press("Escape");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(20);
  await list.getByRole("button", { name: "Load more findings", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(21);
  await expect(list.getByText("open", { exact: true })).toHaveCount(21);
  await expect(page.getByRole("status").filter({ hasText: "21 of 28 findings" })).toBeAttached();
  await page.getByRole("button", { name: "Edit Result: Open repeats", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-scan-finding-results-${info.project.name}.png` });
  await page.getByRole("checkbox", { name: /^New findings/ }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL((url) => url.searchParams.getAll("outcome").length === 2 && url.searchParams.get("assessment_outcome") === "withheld");
  await page.getByRole("textbox", { name: "Search scan findings", exact: true }).fill("New defect");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
  await page.getByRole("checkbox", { name: "Medium", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Medium", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Filter by Severity", exact: true })).toBeHidden();
  await expect(page.getByRole("heading", { name: "No findings match these filters", exact: true })).toBeVisible();
  await page.getByRole("tabpanel").getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(20);
  await expect(page.getByRole("textbox", { name: "Search scan findings", exact: true })).toBeFocused();
  await page.goto("/security/scans/scan?tab=findings&outcome=not_observed");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(list).toContainText("Verification not_observed");
  await page.reload();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await page.getByRole("tab", { name: "Report 1", exact: true }).click();
  await expect(page.getByRole("region", { name: "Findings at this scan", exact: true })).toContainText("Original report evidence");
  await page.getByRole("tab", { name: "Findings 28", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  expect(writes).toHaveLength(0);
});

test("security tabs display zero for empty repositories and scans", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/security/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/repositories/22")) return route.fulfill({ json: { repository, summary, can_write: true, finding_count: 0, scan_count: 0 } });
    if (path.endsWith("/scans/scan")) return route.fulfill({ json: { repository, scan, can_write: true, finding_count: 0, activity_count: 0, activity: [] } });
    if (path.endsWith("/findings") || path.endsWith("/scans")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
    return route.fallback();
  });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scan history 0", exact: true })).toBeVisible();
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Report 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Activity 0", exact: true })).toBeVisible();
});

test("security lists omit pagination when all results fit on one page", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => route.fulfill({ json: {
    items: Array.from({ length: 3 }, (_, index) => ({ ...finding, id: `finding-${index}` })), total: 3, page: 1, page_size: 20,
  } }));
  await page.goto("/security/repositories/22");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(3);
  await expect(page.locator('[data-slot="load-more-footer"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^(Previous|Next|Load more)/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-no-pagination-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  await expect(page.locator('[data-slot="security-scan-row"]')).toHaveCount(1);
  await expect(page.locator('[data-slot="load-more-footer"]')).toHaveCount(0);
  await page.goto("/security/scans/scan?tab=findings");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.locator('[data-slot="load-more-footer"]')).toHaveCount(0);
});

for (const history of [false, true]) {
  test(`security ${history ? "scan history" : "findings"} keeps loaded rows on page failure and hides them after access loss`, async ({ page }) => {
    await mock(page);
    await page.clock.install();
    const label = history ? "scan history" : "findings";
    const rows = Array.from({ length: 5 }, (_, index) => ({ ...(history ? scan : finding), id: `item-${index}` }));
    let failure = 503;
    const requests: number[] = [];
    await page.route(`**/api/v1/security/repositories/22/${history ? "scans" : "findings"}?**`, (route) => {
      const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
      requests.push(pageNumber);
      return route.fulfill(failure ? { status: failure, json: { detail: failure === 403 ? "Repository access revoked" : "Results temporarily unavailable" } }
        : { json: { items: rows.slice((pageNumber - 1) * 2, pageNumber * 2), total: rows.length, page: pageNumber, page_size: 2 } });
    });
    await page.goto(`/security/repositories/22${history ? "?tab=history" : ""}`);
    const panel = page.getByRole("tabpanel");
    const cards = panel.locator(`[data-slot="${history ? "security-scan-row" : "finding-card"}"]`);
    await expect(panel.getByText("Results temporarily unavailable", { exact: true })).toBeVisible();
    await expect(panel.getByText(history ? "No security scans yet" : "No findings in this view", { exact: true })).toHaveCount(0);
    failure = 0;
    await panel.getByRole("button", { name: `Retry ${label}`, exact: true }).click();
    await expect(cards).toHaveCount(2);
    const footer = panel.locator('[data-slot="load-more-footer"]');
    await expect(footer.getByRole("status")).toHaveText("2 of 5");
    failure = 503;
    await panel.getByRole("button", { name: `Load more ${label}`, exact: true }).click();
    await expect(panel.getByRole("button", { name: `Retry ${label}`, exact: true })).toBeVisible();
    await expect(cards).toHaveCount(2);
    await expect(footer.getByRole("status")).toHaveText("2 of 5");
    failure = 0;
    await panel.getByRole("button", { name: `Retry ${label}`, exact: true }).click();
    await expect(cards).toHaveCount(4);
    await expect(footer.getByRole("status")).toHaveText("4 of 5");
    failure = 403;
    await panel.getByRole("button", { name: `Load more ${label}`, exact: true }).click();
    await expect(panel.getByText("Repository access revoked", { exact: true })).toBeVisible();
    await expect(cards).toHaveCount(0);
    expect(requests).toEqual([1, 1, 2, 2, 3]);
  });
}

test("security findings append without duplicates, pause expanded polling, and reset when filters change", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  await page.clock.install();
  const rows = Array.from({ length: 4 }, (_, index) => ({ ...finding, id: `finding-${index}`, title: `Security finding ${index}` }));
  const requests: { page: number; search: string | null }[] = [];
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const pageNumber = Number(params.get("page"));
    const search = params.get("search");
    requests.push({ page: pageNumber, search });
    return route.fulfill({ json: { items: search ? [rows[3]] : pageNumber === 1 ? rows.slice(0, 2) : rows.slice(1, 3),
      total: search ? 1 : 4, page: pageNumber, page_size: 2 } });
  });
  await page.goto("/security/repositories/22");
  const panel = page.getByRole("tabpanel");
  const cards = panel.locator('[data-slot="finding-card"]');
  const footer = panel.locator('[data-slot="load-more-footer"]');
  await expect(cards).toHaveCount(2);
  await expect(footer.getByRole("status")).toHaveText("2 of 4");
  await page.clock.fastForward(5000);
  await expect.poll(() => requests.length).toBe(2);
  await panel.getByRole("button", { name: "Load more findings", exact: true }).focus();
  await page.screenshot({ path: `/private/tmp/code-mortis-security-load-more-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Enter");
  await expect(cards).toHaveCount(3);
  await expect(footer.getByRole("status")).toHaveText("3 of 4");
  await expect(panel.getByRole("button", { name: "Load more findings", exact: true })).toHaveCount(0);
  await page.clock.fastForward(15000);
  expect(requests.map((request) => request.page)).toEqual([1, 1, 2]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const search = page.getByRole("textbox", { name: "Search findings", exact: true });
  await search.fill("ownership");
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Security finding 3");
  await expect(footer).toHaveCount(0);
  await search.fill("");
  await expect(cards).toHaveCount(2);
  await expect(footer.getByRole("status")).toHaveText("2 of 4");
  expect(requests.slice(-2)).toEqual([{ page: 1, search: "ownership" }, { page: 1, search: null }]);
});

test("security history and details show recorded durations and live elapsed time", async ({ page }, info) => {
  await page.clock.install({ time: new Date("2026-10-18T23:08:00Z") });
  await page.clock.setFixedTime(new Date("2026-10-18T23:08:00Z"));
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page);
  const timed = { ...scan, status: "partial", grade: null, started_at: "2026-10-18T22:38:14.256114Z", finished_at: "2026-10-18T23:07:56.739231Z" };
  const running: Schema["SecurityScanOut"] = { ...scan, id: "running", status: "running", started_at: "2026-10-18T23:07:50Z", finished_at: null };
  const rows = [timed, running, { ...scan, id: "queued", status: "queued", started_at: null, finished_at: null },
    { ...scan, id: "unknown", status: "cancelled", finished_at: null }];
  await page.route("**/api/v1/security/repositories/22/scans**", (route) => route.fulfill({ json: { items: rows, total: rows.length, page: 1, page_size: 20 } }));
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository, scan: timed, can_write: false, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.route("**/api/v1/security/scans/running", (route) => route.fulfill({ json: { repository, scan: running, can_write: false, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.goto("/security/repositories/22?tab=history");
  const history = page.getByRole("tabpanel");
  await expect(history.locator('a[href="/security/scans/scan"]')).toContainText("29m 42s");
  await expect(history.locator('a[href="/security/scans/running"]')).toContainText("10s elapsed");
  await expect(history.locator('a[href="/security/scans/queued"]')).toContainText("Not started");
  await expect(history.locator('a[href="/security/scans/unknown"]')).toContainText("Unavailable");
  await page.clock.setFixedTime(new Date("2026-10-18T23:08:05Z"));
  await page.clock.fastForward(5000);
  await expect(history.locator('a[href="/security/scans/running"]')).toContainText("15s elapsed");
  await expect(history.locator('a[href="/security/scans/scan"]')).toContainText("29m 42s");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-history-duration-dark.png"), fullPage: true });
  await history.locator('a[href="/security/scans/scan"]').click();
  await expect(page.locator("main header")).toContainText("Duration: 29m 42s");
  await page.goto("/security/scans/running");
  await expect(page.locator("main header")).toContainText("Duration: 15s elapsed");
  await page.clock.setFixedTime(new Date("2026-10-18T23:08:10Z"));
  await page.clock.fastForward(5000);
  await expect(page.locator("main header")).toContainText("Duration: 20s elapsed");
  running.status = "completed";
  running.finished_at = "2026-10-18T23:08:08Z";
  await page.clock.fastForward(5000);
  await expect(page.locator("main header")).toContainText("Duration: 18s");
  await expect(page.locator("main header")).not.toContainText("elapsed");
  await page.clock.fastForward(5000);
  await expect(page.locator("main header")).toContainText("Duration: 18s");
  expect(writes).toHaveLength(0);
});

test("security finding sheets and history retain focus and fit dark narrow layouts", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page, { longPath: true });
  await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 960 });
  await page.goto("/security/repositories/22");
  const card = page.getByRole("button", { name: /Order lookup does not verify ownership/ });
  await card.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-finding-aligned-dark.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  const saved = page.locator('[data-slot="security-scan-row"]');
  await expect(saved.getByText("D", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("security-history-aligned-dark.png"), fullPage: true });
  await saved.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/security\/scans\/scan$/);
  expect(writes).toHaveLength(0);
});

test("security assessment tab shares PR status tooltips, evidence, filters and finding navigation", async ({ page }, info) => {
  const { writes } = await mockSecurityAssessments(page);
  await page.goto("/security/scans/scan?tab=assessment");
  await expect(page.getByRole("tab", { name: "Assessment 7", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toBeVisible();
  const list = page.getByRole("region", { name: "Security assessments", exact: true });
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(7);
  await expect(page.getByRole("status", { name: "Assessment progress" })).toContainText("1 queued · 2 running · 3 completed · 1 failed");
  const rejected = list.getByRole("button", { name: /Security candidate 1/ });
  await rejected.getByText("Not supported", { exact: true }).hover();
  await expect(page.getByRole("tooltip")).toHaveText("The evidence did not support this proposed issue.");
  await page.keyboard.press("Escape");
  await rejected.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("dialog", { name: "Assessment details" });
  await expect(drawer.getByRole("tab", { name: "Assessment", exact: true })).toBeFocused();
  await expect(drawer.getByText("Why it was not supported", { exact: true })).toBeVisible();
  await expect(drawer.getByText("The supplied ownership guard protects this request.")).toBeVisible();
  await drawer.getByText("Not supported", { exact: true }).locator("..").focus();
  await expect(page.getByRole("tooltip")).toHaveText("The evidence did not support this proposed issue.");
  await drawer.getByRole("tab", { name: "Evidence 1", exact: true }).click();
  await expect(drawer.getByRole("tabpanel", { name: "Evidence 1" }).getByText("return orders.find(id);", { exact: false })).toBeVisible();
  await drawer.getByText("Original proposal", { exact: true }).click();
  await expect(drawer.getByText("Remediation", { exact: true })).toBeVisible();
  await page.screenshot({ path: `/private/tmp/code-mortis-security-assessment-${info.project.name}.png`, fullPage: true });
  await page.screenshot({ path: `/private/tmp/code-mortis-security-assessment-viewport-${info.project.name}.png` });
  await page.keyboard.press("Escape");
  await expect(drawer).not.toBeVisible();
  await expect(rejected).toBeFocused();
  await page.getByRole("textbox", { name: "Search assessments" }).fill("candidate 0");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page).toHaveURL(/assessment_q=candidate\+0/);
  await list.getByRole("button", { name: /Security candidate 0/ }).click();
  await drawer.getByRole("button", { name: "Open finding", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Finding details" })).toBeVisible();
  await expect(drawer).not.toBeVisible();
  await expect(page).toHaveURL(/finding=finding/);
  expect(writes).toEqual([]);
});

test("security assessments and open drawer update live through validation without changing tabs or focus", async ({ page }) => {
  await page.clock.install();
  const { state, writes } = await mockSecurityAssessments(page);
  await page.goto("/security/scans/scan?tab=assessment&assessment=4");
  const drawer = page.getByRole("dialog", { name: "Assessment details" });
  await expect(drawer.getByText("Assessment is being checked against source evidence and finding history.")).toBeVisible();
  state.rows[4] = securityAssessment("4", { status: "running", progress_phase: "validating", outcome: "pending", result: { verdict: "confirmed", explanation: "Validated response", evidence: finding.evidence.evidence } });
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(drawer.getByText("Response received; validating against the current source and finding history.")).toBeVisible();
  await expect(drawer.getByText("Validating", { exact: true })).toBeVisible();
  await expect(drawer.getByRole("button", { name: "Open finding" })).toHaveCount(0);
  await drawer.getByRole("tab", { name: "Evidence 1", exact: true }).click();
  state.rows[4] = securityAssessment("4", { outcome: "confirmed", finding_id: "finding", result: { verdict: "confirmed", finding_id: null, explanation: "Accepted", evidence: [{ revision: "current", path: "src/orders.ts", start_line: 14, end_line: 14, quote: "return acceptedOrder;" }] } });
  state.rows.push(securityAssessment("7"));
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(drawer.getByRole("button", { name: "Open finding" })).toBeVisible();
  await expect(drawer.getByRole("tab", { name: "Evidence 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(drawer.getByText("return acceptedOrder;", { exact: false })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.clock.runFor(350);
  await expect(page.getByRole("tab", { name: "Assessment 8", exact: true })).toBeFocused();
  await expect(page.getByRole("region", { name: "Security assessments" }).locator('[data-slot="finding-card"]')).toHaveCount(8);
  expect(writes).toEqual([]);
});

test("security assessment pagination retains live updates and failures hide sensitive history after access loss", async ({ page }) => {
  await page.clock.install();
  const { state } = await mockSecurityAssessments(page);
  state.rows = Array.from({ length: 25 }, (_, index) => securityAssessment(String(index)));
  await page.goto("/security/scans/scan?tab=assessment");
  const list = page.getByRole("region", { name: "Security assessments" });
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(20);
  await list.getByRole("button", { name: "Load more assessments" }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(25);
  state.rows[24] = securityAssessment("24", { outcome: "uncertain", result: { verdict: "uncertain", explanation: "Caller contract is missing.", evidence: [] } });
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(list.getByRole("button", { name: /Security candidate 24/ })).toContainText("Withheld");
  await list.getByRole("button", { name: /Security candidate 24/ }).click();
  const drawer = page.getByRole("dialog", { name: "Assessment details" });
  await expect(drawer.getByText("Caller contract is missing.")).toBeVisible();
  state.detailFail = 500;
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(drawer.getByRole("alert")).toBeVisible();
  await expect(drawer.getByText("Caller contract is missing.")).toBeVisible();
  state.detailFail = 403;
  await drawer.getByRole("button", { name: "Reload assessment" }).click();
  await expect(drawer.getByText("Caller contract is missing.")).toHaveCount(0);
  await expect(drawer.getByRole("alert")).toBeVisible();
  await page.keyboard.press("Escape");
  state.fail = 403;
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(page.getByRole("tabpanel", { name: /Assessment/ }).getByRole("alert")).toBeVisible();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(0);
});

test("security assessment result filters and failed diagnostics preserve separate proposal evidence", async ({ page }) => {
  await mockSecurityAssessments(page);
  await page.goto("/security/scans/scan?tab=assessment");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Failed/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Failed/ })).toBeChecked();
  await page.keyboard.press("Escape");
  const list = page.getByRole("region", { name: "Security assessments" });
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.getByRole("tab", { name: "Assessment 7", exact: true })).toBeVisible();
  await list.getByRole("button", { name: /Security candidate 3/ }).click();
  const drawer = page.getByRole("dialog", { name: "Assessment details" });
  await expect(drawer.getByText("AI citation mismatch", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Token usage not recorded.")).toBeVisible();
  await drawer.getByRole("tab", { name: "Evidence 0", exact: true }).click();
  await expect(drawer.getByText("No supporting evidence was recorded.")).toBeVisible();
  await drawer.getByText("Original proposal", { exact: true }).click();
  await expect(drawer.getByText("return orders.find(id);", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
