import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";
import { resultDeliveryFeedback } from "../src/lib/error-feedback";

test("diagnostic attribution ignores quoted provider content", () => {
  const error = 'AI result delivery failed: task_done did not include the required DONE state and result object.; response='
    + JSON.stringify({ explanation: "The gateway returned incomplete or malformed completion arguments." });
  expect(resultDeliveryFeedback(error)?.message).not.toContain("gateway response already");
  expect(resultDeliveryFeedback(error)?.message).toContain("omitted the required DONE state or structured result");
});

const at = "2026-10-18T15:00:00Z";
const sha = "a".repeat(40);
const repository: Schema["RepositoryOut"] = { id: 22, installation_id: 10, full_name: "acme/payments", private: false,
  active: true, enabled: false, code_graph_enabled: false, connection_id: "connection", connection_requires_setup: false, open_prs: 0, has_running_review: false,
  settings: { analysis_engine: "mortis", timeout_seconds: 900, token_budget: 200000, language: "English",
    summary_diagram_enabled: false, ai_fix_prompts_enabled: false, automatic: false, include_bot_prs: false,
    model: "", review_sensitivity: "balanced", background: "", exclusions: "",
    delegated_context_bytes: 800000, concurrency: 2, effort: "high", max_files: 40, auto_resolve: false } };
const summary: Schema["SecuritySummary"] = { grade: "D", state: "current", coverage: "complete", freshness: "current", grade_scan_id: "scan", grade_sha: sha,
  graded_at: at, latest_scan_id: "scan", latest_status: "completed", latest_at: at, default_branch: "main", head_sha: sha,
  checked_at: at, counts: { high: 1 } };
const scan: Schema["SecurityScanOut"] = { id: "scan", repository_id: 22, actor_id: 7, connection_id: "connection", parent_id: null,
  branch: "main", head_sha: sha, config: { analysis_engine: "mortis", connection: { id: "connection", kind: "api", name: "Team API", model: "test-model" },
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
    const projected: Record<string, unknown>[] = rows.map((row) => ({ ...row, state: ["open", "resolved", "false_positive", "accepted_risk"].includes(String(row.state)) ? row.state : "unknown" }));
    const counts = Object.fromEntries(["state", "outcome", "severity", "kind"].map((key) => [key, Object.fromEntries(
      [...new Set(projected.map((row) => String(row[key] || "unknown")))].map((value) => [value, projected.filter((row) => String(row[key] || "unknown") === value).length]))]));
    const term = (params.get("q") ?? "").trim().toLowerCase();
    const filtered = projected.filter((row) => (!params.has("finding_id") || row.finding_id === params.get("finding_id"))
      && ["state", "outcome", "severity", "kind"].every((key) => !params.has(key) || params.getAll(key).includes(String(row[key] || "unknown")))
      && (!term || [row.title, row.path, row.kind, (row.evidence as Record<string, unknown>)?.explanation].some((value) => String(value ?? "").toLowerCase().includes(term))));
    const ranks: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1 };
    if ((params.get("ordering") ?? "-severity") === "-severity") filtered.sort((a, b) => (ranks[String(b.severity)] ?? 0) - (ranks[String(a.severity)] ?? 0));
    const pageNumber = Number(params.get("page") ?? 1);
    return route.fulfill({ json: { items: filtered.slice((pageNumber - 1) * 10, pageNumber * 10).map((row) => ({ ...row, id: row.finding_id })),
      total: filtered.length, registered_total: rows.length, state_counts: counts.state, outcome_counts: counts.outcome, severity_counts: counts.severity, kind_counts: counts.kind,
      page: pageNumber, page_size: 10 } });
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
    can_write: false, finding_count: 1, finding_state_counts: { open: 1 }, activity_count: 0, activity: [], assessment: summary() } }));
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
    const size = Number(url.searchParams.get("page_size") ?? 10);
    const pageNumber = Number(url.searchParams.get("page") ?? 1);
    return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * size, pageNumber * size).map((row) => ({ ...row, revision: String(state.revision) })), total: rows.length,
      registered_total: state.rows.length, outcome_counts: summary().outcome_counts, severity_counts: { high: state.rows.length },
      category_counts: { "Access Control": state.rows.length }, revision: String(state.revision), page: pageNumber, page_size: size } });
  });
  return { state, writes };
}

async function mock(page: Page, options: { partial?: boolean; secret?: boolean; canWrite?: boolean; scanError?: string; running?: boolean; longPath?: boolean; latestStatus?: string; summary?: Partial<Schema["SecuritySummary"]>; issue?: Schema["SecurityFindingOut"]["issue"]; activity?: Schema["SecurityActivityOut"][]; workflowLimits?: boolean } = {}) {
  let current = structuredClone(finding);
  if (options.longPath) current.path = `services/${"long-directory-name/".repeat(10)}account-order-ownership-with-a-very-long-filename.ts`;
  if (options.secret) current = { ...current, kind: "secret", title: "Exposed credential material", evidence: { ...current.evidence, identity: "keyed-digest" } };
  if (options.issue) current.issue = options.issue;
  const historical = structuredClone(current);
  const state = { ...(options.partial ? { ...summary, state: "incomplete", grade_scan_id: "previous", latest_status: "partial", counts: { critical: 1 } } : summary),
    ...(options.latestStatus ? { latest_status: options.latestStatus } : {}), ...options.summary };
  state.coverage = options.summary?.coverage ?? (state.state === "not_scanned" ? "not_scanned" : state.state === "incomplete" || state.latest_status !== "completed" ? "incomplete" : "complete");
  state.freshness = options.summary?.freshness ?? (state.state === "outdated" ? "outdated" : state.state === "unavailable" ? "unavailable" : "current");
  const saved = { ...scan, report: { commit: sha, grade: "D", status: "completed", findings: [{ ...current, finding_id: current.id, outcome: "present" }], problems: [] } };
  if (options.workflowLimits) saved.config = {
    ...saved.config, execution_limits: "workflow/v1", limits: { concurrency: 3, token_budget: 200000, timeout_seconds: 900 },
  };
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
      else if (method === "PATCH") {
        current.state = body.state; current.version++; json = current;
        state.grade = ["open", "accepted_risk"].includes(current.state) ? "D" : "A";
        state.counts = ["open", "accepted_risk"].includes(current.state) ? { high: 1 } : {};
      }
    } else if (path === "/setup/status") json = { configured: true, public_url: "http://localhost" };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", csrf: "test", avatar_url: "" };
    else if (path === "/repositories") json = [{ ...repository, security: state }];
    else if (path === "/security/repositories") json = { items: [{ repository, summary: state }], total: 1, registered_total: 1, grade_counts: { [state.grade ?? "unrated"]: 1 }, status_counts: { [state.state]: 1 }, page: 1, page_size: 10 };
    else if (path === "/security/repositories/22") json = { repository, summary: state, can_write: options.canWrite ?? true, finding_count: 1, finding_state_counts: { [current.state]: 1 }, scan_count: 1 };
    else if (path === "/security/repositories/22/scans") json = { items: [saved], total: 1, page: 1, page_size: 10 };
    else if (path === "/security/repositories/22/findings" || path === "/security/scans/scan/findings") {
      const row = path.includes("/scans/") ? historical : current;
      json = { items: url.searchParams.get("state") === row.state ? [row] : [], total: url.searchParams.get("state") === row.state ? 1 : 0, registered_total: 1, state_counts: { [row.state]: 1 }, page: 1, page_size: 10 };
    }
    else if (path === "/security/scans/scan" || path === "/security/scans/queued") json = { repository, scan: path.endsWith("queued") ? { ...scan, id: "queued", status: "queued", grade: null, report: {} }
      : { ...saved, coverage: { ...saved.coverage, inventory_count: Object.keys(saved.coverage.inventory as object).length }, report: Object.fromEntries(Object.entries(saved.report).filter(([key]) => key !== "findings")) },
      finding_state_counts: path.endsWith("queued") ? {} : { [historical.state]: 1 }, report_count: saved.report.findings?.length ?? 0, can_write: true, finding_count: path.endsWith("queued") ? 0 : 1, activity_count: 0, activity: [] };
    else if (path === "/security/findings/finding") json = { repository, finding: current, can_write: options.canWrite ?? true,
      observations: [{ scan_id: "scan", outcome: "present", created_at: at, snapshot: {} }], activity: options.activity ?? [] };
    else if (path === "/security/scans/scan/activity") json = { items: [], total: 0, page: 1, page_size: 10 };
    else if (path.startsWith("/security/scans/scan/coverage/")) {
      const section = path.split("/").at(-1)!;
      const coverage = saved.coverage as Record<string, unknown>;
      const rows = section === "inventory" ? Object.keys(saved.coverage.inventory as object).sort().map((path) => ({ path })) : (coverage[section] ?? []) as Record<string, unknown>[];
      const pageNumber = Number(url.searchParams.get("page") ?? 1);
      json = { items: rows.map((row, index) => ({ ...row, id: String(index) })).slice((pageNumber - 1) * 10, pageNumber * 10), total: rows.length, page: pageNumber, page_size: 10 };
    }
    else if (path === "/security/findings/finding/observations") json = { items: [{ id: "observation", scan_id: "scan", outcome: "present", created_at: at }], total: 1, page: 1, page_size: 10 };
    else if (path === "/security/findings/finding/activity") json = { items: options.activity ?? [], total: options.activity?.length ?? 0, page: 1, page_size: 10 };
    else if (path === "/security/findings/finding/github-issue") json = { title: "[Security] Order lookup does not verify ownership", body: "## Evidence\n\nThe lookup lacks an ownership check.\n\n## Remediation\n\nConstrain access to the authenticated account.", repository: repository.full_name, public: true, version: current.version };
    else if (path === "/connections") json = [{ id: "connection", name: "Team API", kind: "api", model: "test-model", provider: "openai" }];
    else if (path === "/connections/connection/models") json = { status: "ready", source: "provider", models: [{ id: "test-model", name: "Test model" }] };
    await route.fulfill({ json });
  });
  await mockReportFindings(page, saved.report.findings ?? []);
  return writes;
}

test("security repository breadcrumbs show the name, fit narrow screens, and clear denied data", async ({ page }, info) => {
  await page.clock.install();
  const writes = await mock(page);
  const name = "organization-with-a-very-long-name/repository-with-a-very-long-name";
  let denied = false;
  await page.route("**/api/v1/security/repositories/22", (route) => denied
    ? route.fulfill({ status: 403, json: { detail: "Repository access denied." } })
    : route.fulfill({ json: { repository: { ...repository, full_name: name }, summary,
      can_write: false, finding_count: 1, finding_state_counts: { open: 1 }, scan_count: 1 } }));
  await page.goto("/security/repositories/22/?tab=resolved");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  const current = breadcrumb.locator('[aria-current="page"]');
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(current).toHaveText(name);
  await expect(current).toHaveAttribute("title", name);
  for (const width of info.project.name === "mobile" ? [390, 320] : [1440, 1024, 768, 640]) {
    await page.setViewportSize({ width, height: 938 });
    await expect(current).toBeInViewport();
    await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  denied = true;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Repository access denied.");
  await expect(current).toHaveText("Repository security");
  expect(writes).toHaveLength(0);
});

test("scan breadcrumbs contain the repository instead of a separate back-link", async ({ page }, info) => {
  const writes = await mock(page);
  const name = "organization-with-a-very-long-name/repository-with-a-very-long-name";
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository: { ...repository, full_name: name }, scan, can_write: false, finding_count: 0, activity_count: 0, activity: [],
  } }));
  await page.goto("/security/scans/scan?tab=coverage");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Security scan");
  await expect(page.getByRole("main").getByRole("link", { name, exact: true })).toHaveCount(0);
  await expect(breadcrumb.getByRole("link", { name, exact: true, includeHidden: true })).toHaveAttribute("href", "/security/repositories/22");
  for (const width of info.project.name === "mobile" ? [390, 320] : [1440, 768, 640]) {
    await page.setViewportSize({ width, height: 938 });
    await expect(breadcrumb.locator('[aria-current="page"]')).toBeInViewport();
    await expect(page.getByRole("button", { name: "Switch to dark mode" })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  if (info.project.name === "mobile") await breadcrumb.getByRole("button", { name: "Show breadcrumb ancestors" }).click();
  const parent = info.project.name === "mobile" ? page.getByRole("menuitem", { name, exact: true }) : breadcrumb.getByRole("link", { name, exact: true });
  await expect(parent).toHaveAttribute("href", "/security/repositories/22");
  await parent.press("Enter");
  await expect(page).toHaveURL(/\/security\/repositories\/22$/);
  await expect(page.getByRole("heading", { name: "acme/payments", exact: true })).toBeVisible();
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("acme/payments");
  if (info.project.use.viewport) await page.setViewportSize(info.project.use.viewport);
  await page.screenshot({ path: `../.impeccable/review/cohesive-security-repository-${info.project.name}.png`, fullPage: true, animations: "disabled" });
  expect(writes).toHaveLength(0);
});

test("scan breadcrumbs omit repository data after access is denied", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  let denied = false;
  await page.route("**/api/v1/security/scans/scan", (route) => denied
    ? route.fulfill({ status: 403, json: { detail: "Scan access denied." } })
    : route.fulfill({ json: { repository, scan, can_write: false, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.goto("/security/scans/scan");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
  await expect(breadcrumb.getByRole("link", { name: repository.full_name, exact: true, includeHidden: true })).toHaveAttribute("href", "/security/repositories/22");
  await expect(page.getByRole("region", { name: "Scan stages" })).toBeVisible();
  denied = true;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Scan access denied.");
  await expect(breadcrumb.getByRole("link", { name: repository.full_name, exact: true, includeHidden: true })).toHaveCount(0);
  await expect(breadcrumb.locator('[aria-current="page"]')).toHaveText("Security scan");
});

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
    const active = status === "queued" || status === "running" || status === "blocked";
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

test("scan alerts omit ordinary cancellation but preserve specific reasons and action errors", async ({ page }) => {
  await page.clock.install();
  const activity = [{ id: "cancelled", scan_id: "scan", finding_id: null, actor_id: 7,
    action: "scan_cancelled", detail: { reason: "Security scan cancelled." }, created_at: at }];
  await mock(page, { activity });
  await page.route("**/api/v1/security/scans/scan/activity?**", (route) => route.fulfill({
    json: { items: activity, total: 1, page: 1, page_size: 10 },
  }));
  const saved = { ...scan, status: "cancelled", error: "Security scan cancelled.",
    stages: { ...scan.stages, analysis: { status: "cancelled", total: 55, completed: 7 } } };
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: saved, can_write: true, finding_count: 0, activity_count: 1, activity,
  } }));
  await page.goto("/security/scans/scan?tab=activity");
  await expect(page.getByRole("region", { name: "Scan stages" })).toContainText("Analysis cancelled");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("tabpanel").last()).toContainText("Security scan cancelled.");
  for (const [status, error] of [["cancelled", "Security scan access was revoked."],
    ["cancelled", "Replaced by an explicit retry."], ["blocked", "Runner unavailable."],
    ["failed", "Repository snapshot failed."], ["partial", "Some source files could not be analyzed."]]) {
    Object.assign(saved, { status, error });
    await page.clock.fastForward(5000);
    await expect(page.getByRole("alert")).toHaveText(error);
  }
  Object.assign(saved, { status: "running", error: null });
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.route("**/api/v1/security/scans/scan/cancel", (route) => route.fulfill({
    status: 409, json: { detail: "Cancellation request failed." },
  }));
  await page.getByRole("button", { name: "Cancel scan", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Cancellation request failed.");
  Object.assign(saved, { status: "cancelled", error: "Security scan cancelled." });
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert")).toContainText("Cancellation request failed.");
});

test("security prompt limits stay visible for long combined verification warnings", async ({ page }) => {
  const error = 'Fix verification remains uncertain for a previously reported issue (apps/users/views.py): Inference request exceeds the connection prompt token ceiling; '.repeat(5);
  expect(error.length).toBeGreaterThan(600);
  await mock(page, { scanError: error });
  await page.goto("/security/scans/scan");
  const alert = page.getByRole("alert").first();
  await expect(alert.getByText("AI prompt limit reached", { exact: true })).toBeVisible();
  await expect(alert).toContainText("Those checks remain unverified.");
  await expect(alert).not.toContainText("ask the administrator");
  const details = alert.getByRole("region", { name: "AI prompt limit reached technical details", includeHidden: true });
  await expect(details).toBeHidden();
  await alert.getByText("Technical details", { exact: true }).click();
  await expect(details).toHaveText(error.trim());
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
  await page.getByRole("heading", { name: /^Work units/ }).click();
  const work = page.getByRole("region", { name: "Work units", exact: true }).getByRole("listitem");
  await expect(work).toContainText("The source was read; this is not a missing-file error.");
  await expect(work.getByRole("region", { includeHidden: true })).toBeHidden();
  await work.getByText("Technical details", { exact: true }).click();
  await expect(work.getByRole("region")).toHaveText(error);
});

for (const error of [
  "AI result delivery failed for src/scriptSchemaValidator.ts: task_done did not include the required DONE state and result object.",
  'The AI did not finish checking this finding. Technical details: {"status":"success","result_error":"task_done did not include the required DONE state and result object."}',
]) test(`security errors explain incomplete completion calls: ${error.slice(0, 30)}`, async ({ page }) => {
  await mock(page, { scanError: error });
  await page.goto("/security/scans/scan");
  const alert = page.getByRole("alert").first();
  await expect(alert.getByText("AI result delivery failed", { exact: true })).toBeVisible();
  await expect(alert).toContainText("The AI completion call omitted the required DONE state or structured result.");
  await expect(alert).toContainText("The affected check was not accepted.");
  await alert.getByText("Technical details", { exact: true }).click();
  await expect(alert.getByRole("region")).toHaveText(error);
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
  await expect(page.getByText(/^Last graded report saved .* for aaaaaaa\. Freshness checked/)).toBeVisible();
  await expect(page.getByRole("link", { name: `Security ${grade}`, exact: true })).toBeVisible();
  await page.screenshot({ path: `/private/tmp/code-mortis-repository-header-${grade}-${info.project.name}.png`, fullPage: true });
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => route.fulfill({ json: {
    items: [{ ...scan, status: "partial", grade }], total: 1, page: 1, page_size: 10,
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
  await expect(page.getByRole("region", { name: "Scan stages" })).toContainText("57 / 58 files");
  await expect(page.getByRole("group", { name: "Scan progress details" })).toContainText("Partial · 57 / 58 files");
  await page.keyboard.press("Escape");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-partial-grade-${grade}-${info.project.name}.png`, fullPage: true });
});

test("security overview separates current grades from scan status and keeps filters in the URL", async ({ page }, info) => {
  await mock(page, { partial: true });
  await page.goto("/security");
  await expect(page.getByRole("heading", { name: "Security", exact: true })).toBeVisible();
  await expect(page.getByText("Incomplete", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Security grade D", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "View latest scan: completed with warnings", exact: true })).toBeVisible();
  await expect(page.getByText("1 unresolved", { exact: false })).toBeVisible();
  await expect(page.getByText("1 critical", { exact: true })).toHaveClass("sr-only");
  await page.getByRole("textbox", { name: "Search repositories" }).fill("payments");
  await expect(page).toHaveURL(/search=payments/);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Grade", exact: true }).click();
  await page.getByRole("checkbox", { name: /^D(?: |$)/ }).click();
  await expect(page.getByRole("checkbox", { name: /^D(?: |$)/ })).toBeChecked();
  await expect(page).toHaveURL(/search=payments&grade=D/);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search repositories" })).toHaveValue("payments");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-overview-${info.project.name}.png`, fullPage: true });
});

for (const [state, badgeLabel] of [["current", "D"], ["incomplete", "D"], ["outdated", "D"], ["not_scanned", "Unscanned"], ["unavailable", "D"]] as const) {
  test(`repository detail grade badge uses one label for ${state}`, async ({ page }) => {
    await mock(page, { summary: { state, ...(["outdated", "unavailable"].includes(state) ? { latest_status: "partial" } : {}), ...(state === "not_scanned" ? { grade: null, grade_sha: null } : {}) } });
    await page.goto("/security/repositories/22");
    const label = badgeLabel === "Unscanned" ? badgeLabel : `Security ${badgeLabel}`;
    const badge = page.getByRole("link", { name: label, exact: true });
    await expect(badge).toHaveText(label);
    await badge.focus();
    const explanation = { current: "current default-branch commit", incomplete: "latest assessment is incomplete", outdated: "Current changes have not been scanned", not_scanned: "No security scan has been recorded", unavailable: "freshness could not be verified" }[state];
    await expect(page.getByRole("tooltip")).toContainText(explanation);
    if (["outdated", "unavailable"].includes(state)) {
      await expect(badge.locator(state === "outdated" ? ".lucide-history" : ".lucide-shield-question")).toBeVisible();
      await expect(badge.locator('[data-slot="badge"]')).toHaveClass(/text-red-700/);
      await expect(page.getByText(state === "outdated" ? "Security Outdated" : "Security Unverified", { exact: true })).toHaveCount(0);
    }
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toBeHidden();
    if (["incomplete", "outdated", "unavailable"].includes(state)) {
      await expect(page.getByText(/Last graded report saved/)).toBeVisible();
    }
  });
}

for (const incomplete of [false, true]) test(`triage immediately updates the repository grade and preserves scan context (${incomplete ? "incomplete" : "complete"})`, async ({ page }, info) => {
  const writes = await mock(page, { summary: incomplete ? { state: "outdated", latest_status: "partial", coverage: "incomplete", freshness: "outdated" } : {} });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("link", { name: "Security D", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
  const sheet = page.getByRole("dialog", { name: "Finding details" });
  await sheet.getByRole("button", { name: "Change status", exact: true }).click();
  await expect(sheet.getByText(/Changing status updates the repository grade immediately/)).toBeVisible();
  await sheet.getByLabel("Decision", { exact: true }).selectOption("false_positive");
  await sheet.getByLabel("Reason", { exact: true }).fill("The existing ownership guard covers this request.");
  await sheet.getByRole("button", { name: "Save decision", exact: true }).click();
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("false positive");
  await sheet.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("link", { name: "Security A", exact: true })).toBeVisible();
  await expect(page.getByLabel("Scan coverage", { exact: true })).toHaveText(incomplete ? "Coverage incomplete" : "Coverage complete");
  await expect(page.getByLabel("Scan freshness", { exact: true })).toHaveText(incomplete ? "Outdated scan" : "Current commit");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Security A", exact: true }).focus();
  await expect(page.getByRole("tooltip")).toContainText("Based on current findings and triage decisions");
  if (incomplete) {
    await expect(page.getByRole("tooltip")).toContainText("The latest assessment is incomplete");
    await expect(page.getByRole("tooltip")).toContainText("Current changes have not been scanned");
  }
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  await expect(page.locator('[data-slot="security-scan-row"] [data-slot="security-grade-shield"]')).toHaveText("D");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`triaged-grade-${incomplete ? "incomplete" : "complete"}.png`), fullPage: true });
  expect(writes).toHaveLength(1);
  expect(writes[0].body.state).toBe("false_positive");
});

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
    { change: { state: "outdated" }, label: "D", icon: "history" },
    { change: { state: "unavailable" }, label: "D", icon: "shield-question" },
    ...["failed", "cancelled", "queued", "running", "blocked", "completed"].map((status, index) => ({ change: { state: "incomplete", latest_status: status }, label: "D",
      icon: ["shield-x", "shield-off", "shield-ellipsis", "shield-ellipsis", "shield-alert", "shield-alert"][index] })),
  ];
  for (const [index, item] of cases.entries()) {
    current = { ...summary, ...item.change,
      coverage: item.change.state === "incomplete" ? "incomplete" : "complete",
      freshness: item.change.state === "outdated" ? "outdated" : item.change.state === "unavailable" ? "unavailable" : "current" };
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
    if (item.icon === "history") {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toContainText("The report covers an earlier commit or default branch.");
      await expect(page.getByRole("tooltip")).toContainText("Grade D:");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `/private/tmp/code-mortis-outdated-grade-${info.project.name}.png` });
      await page.keyboard.press("Escape");
      await expect(page.getByRole("tooltip")).toBeHidden();
    }
    if (item.change.state === "unavailable") {
      await badge.hover();
      await expect(page.getByRole("tooltip")).toContainText("Grade D:");
      await expect(page.getByRole("tooltip")).toContainText("freshness could not be verified");
      await expect(page.getByRole("tooltip")).toHaveCSS("opacity", "1");
      await page.screenshot({ path: `/private/tmp/code-mortis-unverified-grade-${info.project.name}.png` });
      await page.keyboard.press("Escape");
      await expect(page.getByRole("tooltip")).toBeHidden();
      await badge.click();
      await expect(page).toHaveURL(/\/security\/repositories\/22$/);
      await expect(page.getByRole("link", { name: "Security D", exact: true }).locator(".lucide-shield-question")).toBeVisible();
    }
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
    { repository: { ...repository, id: 28, full_name: "acme/historical" }, summary: { ...summary, state: "outdated", latest_status: "partial" } },
    { repository: { ...repository, id: 29, full_name: "acme/partial-report" }, summary: { ...summary, state: "incomplete", latest_status: "partial" } },
    { repository: { ...repository, id: 30, full_name: "acme/running-scan" }, summary: { ...summary, state: "incomplete", latest_status: "running", grade_scan_id: "previous" } });
  await page.route("**/api/v1/security/repositories?**", (route) => route.fulfill({ json: { items: rows, total: rows.length, registered_total: rows.length, grade_counts: {}, status_counts: {}, page: 1, page_size: 10 } }));
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
    await link.focus();
    await expect(page.getByRole("tooltip")).toContainText(`Grade ${grade}:`);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("tooltip")).toBeHidden();
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
  const historical = page.locator('[data-slot="security-repository-row"]').filter({ hasText: "acme/historical" }).getByRole("link", { name: "Security grade D", exact: true });
  await expect(historical.locator("svg")).toHaveClass(/text-red-700.*dark:text-red-300/);
  const running = page.locator('[data-slot="security-repository-row"]').filter({ hasText: "acme/running-scan" });
  await expect(running.locator('[data-slot="security-grade-shield"]')).toHaveText("D");
  await expect(running.locator('[data-slot="security-grade-shield"]')).toHaveClass(/text-red-700.*dark:text-red-300/);
  await expect(running.locator('[data-slot="status-group"]')).toHaveText("running");
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
  await expect(dialog).toContainText("Scan the full default branch with Mortis and Trivy.");
  await expect(dialog.getByRole("checkbox", { name: "Unlimited token budget" })).toBeChecked();
  await expect(dialog.getByRole("checkbox", { name: "Unlimited timeout" })).toBeChecked();
  const concurrency = dialog.getByRole("spinbutton", { name: "Concurrent tasks" });
  await expect(dialog.getByText(/independent finding assessments at a time/)).toBeVisible();
  await expect(concurrency).toHaveValue("4");
  await concurrency.fill("9");
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  expect(writes).toHaveLength(0);
  await concurrency.fill("4");
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).check();
  await expect(dialog.getByRole("spinbutton", { name: "Token budget" })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "Unlimited token budget" }).uncheck();
  await dialog.getByRole("spinbutton", { name: "Token budget" }).fill("250000");
  await dialog.getByRole("checkbox", { name: "Unlimited timeout" }).uncheck();
  await dialog.getByRole("spinbutton", { name: "Timeout (seconds)" }).fill("1200");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-concurrency-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0].body).toMatchObject({ connection_id: "connection", model: "test-model", token_budget: 250000, timeout_seconds: 1200, concurrency: 4 });
  expect(String(writes[0].body.request_key).length).toBeGreaterThan(16);
});

test("retired security connections cannot start a scan", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [
    { id: "connection", name: "Personal account", kind: "chatgpt", model: "test-model", owner_id: 7 },
  ] }));
  await page.goto("/security");
  await page.getByRole("button", { name: "Actions for acme/payments", exact: true }).click();
  await page.getByRole("menuitem", { name: "Run scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Run security scan" });
  const picker = dialog.getByRole("combobox", { name: "AI connection", exact: true });
  await picker.fill("Personal");
  await expect(page.getByRole("option", { name: "Personal account", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "Run scan", exact: true })).toBeDisabled();
  expect(writes).toHaveLength(0);
});

test("Mortis scan retries can increase concurrency while preserving model and limits", async ({ page }) => {
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

test("retry preserves all saved workflow limits", async ({ page }) => {
  const writes = await mock(page, { scanError: "Security analysis did not finish.", workflowLimits: true });
  await page.goto("/security/scans/scan");
  await page.getByRole("button", { name: "Retry scan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Retry security scan" });
  for (const [name, value] of [["Concurrent tasks", "3"], ["Token budget", "200000"], ["Timeout (seconds)", "900"]]) {
    const input = dialog.getByRole("spinbutton", { name });
    await expect(input).toHaveValue(value);
    await expect(input).toBeDisabled();
  }
  await dialog.getByRole("button", { name: "Retry scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes[0]).toMatchObject({
    path: "/security/scans/scan/retry", body: { concurrency: 3, token_budget: 200000, timeout_seconds: 900 },
  });
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
  expect(writes[0]).toMatchObject({
    path: "/security/repositories/22/scans", body: { concurrency: 4, token_budget: null, timeout_seconds: null },
  });
});

test("saved scan exposes findings, resolved, coverage and report downloads", async ({ page }, info) => {
  await mock(page);
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("heading", { name: "Security scan", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: /^Report/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Download report", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "Download Markdown" })).toHaveAttribute("href", /format=markdown/);
  await expect(page.getByRole("menuitem", { name: "Download JSON" })).toHaveAttribute("href", /format=json/);
  await page.keyboard.press("Escape");
  const report = page.getByRole("region", { name: "Findings recorded in this scan", exact: true });
  await expect(report.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.getByRole("heading", { name: "How to fix it", exact: true })).toHaveCount(0);
  await report.getByRole("button").click();
  const saved = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(saved.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(saved.getByRole("heading", { name: "How to fix it", exact: true })).toBeVisible();
  await expect(saved.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(report.getByRole("button")).toBeFocused();
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  await page.getByText("Scanner details", { exact: true }).click();
  await expect(page.getByText("Within 24-hour freshness limit", { exact: true })).toBeVisible();
  await page.getByRole("heading", { name: /^Prepared source inventory/ }).click();
  await expect(page.getByText("src/orders.ts", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings 1", exact: true }).click();
  await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
  await expect(page.getByRole("dialog", { name: "Finding details", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Code snippet" })).toContainText("return orders.find(id)");
  await expect(page.getByRole("region", { name: "Code snippet" }).locator('[data-slot="code-line-numbers"]')).toHaveText("14");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-finding-${info.project.name}.png`, fullPage: true });
});

test("scan state tabs paginate saved findings and retain historical evidence", async ({ page }, info) => {
  const writes = await mock(page);
  const rows = [
    { ...finding, result: "present", state: "resolved", title: "Resolved at this scan" },
    { ...finding, id: "unverified", result: "not_observed", title: "Absent but unverified" },
    ...Array.from({ length: 13 }, (_, index) => ({ ...finding, id: `fixed-${index}`, result: "fixed", state: "resolved", title: `Verified fix ${index}` })),
    { ...finding, id: "reopened", result: "fixed", state: "open", title: "Open at this scan" },
  ];
  const requests: URLSearchParams[] = [];
  let failure = 0;
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: { ...scan, report: { problems: ["Some files could not be analyzed."] } },
    can_write: true, finding_count: rows.length, finding_state_counts: { open: 2, resolved: 14 }, resolved_count: 14, activity_count: 0, activity: [],
  } }));
  await page.route("**/api/v1/security/scans/scan/findings?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    if (failure) return route.fulfill({ status: failure, json: { detail: failure === 403 ? "Access denied" : "Findings temporarily unavailable" } });
    const filtered = rows.filter((row) => row.state === params.get("state") && (!params.get("search") || row.title.includes(params.get("search")!)));
    const number = Number(params.get("page") ?? 1);
    return route.fulfill({ json: { items: filtered.slice((number - 1) * 10, number * 10), total: filtered.length,
      registered_total: rows.length, page: number, page_size: 10, state_counts: { open: 2, resolved: 14 } } });
  });
  await page.goto("/security/scans/scan?tab=report&assessment_outcome=withheld");
  await expect(page.getByRole("tab", { name: "Findings 2", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page).not.toHaveURL(/tab=report/);
  const cards = page.locator('[data-slot="finding-card"]');
  await expect(cards).toHaveCount(2);
  await expect(page.getByText("Open at this scan", { exact: true })).toBeVisible();
  await expect(page.getByText("Absent but unverified", { exact: true })).toBeVisible();
  await expect(page.getByText("Resolved at this scan", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Some files could not be analyzed.", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Search scan findings", exact: true }).fill("Open at");
  await expect(cards).toHaveCount(1);
  await page.getByRole("tab", { name: "Resolved 14", exact: true }).click();
  await expect(cards).toHaveCount(10);
  await expect(page.getByText("Resolved at this scan", { exact: true })).toBeVisible();
  await expect(page.getByText("Open at this scan", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Next resolved findings", exact: true }).click();
  await expect(cards).toHaveCount(4);
  await expect(page.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 2 · 14 total");
  const search = page.getByRole("textbox", { name: "Search resolved findings", exact: true });
  await search.fill("Resolved at");
  await expect(cards).toHaveCount(1);
  await expect(page).toHaveURL((url) => url.searchParams.get("resolved_search") === "Resolved at" && url.searchParams.get("search") === "Open at");
  await page.reload();
  await expect(cards).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("scan-resolved-state.png"), fullPage: true });
  await cards.click();
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await search.fill("no matches");
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(search).toBeFocused();
  await expect(cards).toHaveCount(10);
  await page.getByRole("tab", { name: "Findings 2", exact: true }).click();
  await expect(cards).toHaveCount(1);
  failure = 503;
  await page.reload();
  await expect(page.getByText("Findings temporarily unavailable", { exact: true })).toBeVisible();
  failure = 0;
  await page.getByRole("button", { name: "Retry findings", exact: true }).click();
  await expect(cards).toHaveCount(1);
  failure = 403;
  await page.reload();
  await expect(page.getByText("Access denied", { exact: true })).toBeVisible();
  await expect(cards).toHaveCount(0);
  expect(requests.some((params) => params.get("state") === "resolved" && params.get("page") === "2")).toBe(true);
  expect(writes).toHaveLength(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`security scan uses compact shared finding cards and no inline remediation blocks (${theme})`, async ({ page }, info) => {
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    const writes = await mock(page, { scanError: "Security analysis did not finish." });
    const rows = ["Dockerfile", "Dockerfile.relay", "Dockerfile.runner"].map((path, index) => ({ ...finding, finding_id: `finding-${index}`,
      path, kind: "infrastructure", title: "No HEALTHCHECK defined", severity: "low", outcome: "present",
      evidence: { ...finding.evidence, explanation: "You should add a HEALTHCHECK instruction to your container image.",
        remediation: "Add a HEALTHCHECK instruction.", verification: "Rescan the changed configuration." } }));
    await mockReportFindings(page, rows);
    await page.route("**/api/v1/security/scans/scan/findings?**", (route) => {
      const items = new URL(route.request().url()).searchParams.get("state") === "open" ? rows.map((row) => ({ ...row, id: row.finding_id })) : [];
      return route.fulfill({ json: { items, total: items.length, page: 1, page_size: 10 } });
    });
    await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository,
      scan: { ...scan, status: "partial", grade: null, report: { commit: sha, status: "partial", grade: null, findings: rows } },
      can_write: true, finding_count: 3, finding_state_counts: { open: 3 }, activity_count: 0, activity: [] } }));
    await page.goto("/security/scans/scan");
    await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
    const report = page.getByRole("region", { name: "Findings recorded in this scan", exact: true });
    const cards = report.locator('[data-slot="finding-card"]');
    await expect(cards).toHaveCount(3);
    await expect(cards.first()).toContainText("Dockerfile");
    await expect(cards.first()).toContainText("No HEALTHCHECK defined");
    await expect(cards.first().locator('[data-slot="badge"]')).toHaveCount(3);
    await expect(report.locator('[data-slot="tooltip-trigger"]')).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Inspect finding", exact: true })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: /^(How to fix it|How to verify the fix)$/ })).toHaveCount(0);
    if (info.project.name === "desktop") {
      const before = await cards.first().evaluate((node) => getComputedStyle(node).backgroundColor);
      await cards.first().hover();
      await expect.poll(() => cards.first().evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(before);
      await cards.first().getByText("open", { exact: true }).hover();
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
    finding: { ...finding, state: "resolved", version: 7, path: "src/account-orders.ts", severity: "low", kind: "infrastructure", evidence: { ...finding.evidence,
      source_sha: currentSha, remediation: "Current remediation after the verified fix." } }, can_write: true, observations: [], activity: [] } }));
  await page.goto("/security/scans/scan");
  const card = page.getByRole("region", { name: "Findings recorded in this scan", exact: true }).getByRole("button");
  await card.focus();
  await page.keyboard.press("Enter");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(sheet.getByText("orders.ts", { exact: true })).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Code", { exact: true })).toBeVisible();
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("resolved");
  await expect(sheet.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("link", { name: "View source", exact: true })).toHaveAttribute("href", `https://github.com/acme/payments/blob/${sha}/src/orders.ts`);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-saved-evidence-${info.project.name}.png`, fullPage: false });
  await sheet.getByRole("tab", { name: "Current finding", exact: true }).click();
  await expect(sheet.getByText("account-orders.ts", { exact: true })).toBeVisible();
  await expect(sheet.getByText("low", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Infrastructure", { exact: true })).toBeVisible();
  await expect(sheet.getByText("orders.ts", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("high", { exact: true })).toHaveCount(0);
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("resolved");
  await expect(sheet.getByText("Current remediation after the verified fix.", { exact: true })).toBeVisible();
  await expect(sheet.getByRole("link", { name: "View source", exact: true })).toHaveAttribute("href", `https://github.com/acme/payments/blob/${currentSha}/src/account-orders.ts`);
  await page.clock.fastForward(5000);
  await expect(sheet.getByRole("tab", { name: "Current finding", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.screenshot({ path: `../.impeccable/review/cohesive-security-current-finding-${info.project.name}.png`, animations: "disabled" });
  await sheet.getByRole("tab", { name: "Saved evidence", exact: true }).click();
  await expect(sheet.getByText("orders.ts", { exact: true })).toBeVisible();
  await expect(sheet.getByText("high", { exact: true })).toBeVisible();
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("resolved");
  await expect(sheet.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await expect(sheet.getByText("Current remediation after the verified fix.", { exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(card).toBeFocused();
  await expect(page).not.toHaveURL(/finding=/);
  expect(writes).toHaveLength(0);
});

test("saved uncertain verification explains the blocker separately from remediation", async ({ page }, info) => {
  const writes = await mock(page);
  const explanation = "No independently established safe version is reported for this advisory. Review the advisory or record a triage decision.";
  await mockReportFindings(page, [{ ...finding, finding_id: finding.id, outcome: "uncertain",
    verification: { verdict: "uncertain", explanation } }]);
  await page.goto("/security/scans/scan");
  await page.getByRole("region", { name: "Findings recorded in this scan", exact: true }).getByRole("button").click();
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  const verification = sheet.getByRole("region", { name: "Fix verification", exact: true });
  await expect(verification.getByText(explanation, { exact: true })).toBeVisible();
  await expect(sheet.getByRole("heading", { name: "How to fix it", exact: true })).toBeVisible();
  const suggestedChecks = sheet.getByRole("heading", { name: "How to verify the fix", exact: true }).locator("..");
  await expect(suggestedChecks).toContainText("Suggested checks to run after making the change.");
  await expect(suggestedChecks).toContainText("Add a cross-account access regression test.");
  await expect(suggestedChecks).not.toContainText(explanation);
  await expect(sheet.getByRole("heading", { name: "Verification", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-verification-reason-${info.project.name}.png`, fullPage: false });
  await sheet.locator('[data-slot="finding-scroll"]').evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await page.screenshot({ path: `../.impeccable/review/cohesive-security-suggested-checks-${info.project.name}.png`, animations: "disabled" });
  await sheet.getByRole("tab", { name: "Current finding", exact: true }).click();
  await expect(sheet.getByText(explanation, { exact: true })).toHaveCount(0);
  await sheet.getByRole("tab", { name: "Saved evidence", exact: true }).click();
  await expect(verification.getByText(explanation, { exact: true })).toBeVisible();
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
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  const disclosure = page.locator("details").filter({ hasText: "Potential configuration exposure" });
  await expect(disclosure).not.toHaveAttribute("open");
  await disclosure.locator("summary").click();
  await expect(disclosure.getByText("Required deployment configuration was unavailable.", { exact: true })).toBeVisible();
  await expect(disclosure.getByRole("heading", { name: "How to fix it", exact: true })).toBeVisible();
  await expect(disclosure.getByRole("region", { name: "Code snippet", exact: true })).toContainText("return orders.find(id);");
  await expect(disclosure.locator('[data-slot="code-line-numbers"]')).toHaveText("14");
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

test("oversized initial issue drafts remain editable and cannot publish", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/security/findings/finding/github-issue", (route) => route.request().method() === "GET"
    ? route.fulfill({ json: { title: "Security finding", body: "x".repeat(50001), version: 0, public: true, repository: repository.full_name } })
    : route.fallback());
  await page.goto("/security/repositories/22?finding=finding");
  await page.getByRole("button", { name: "Create GitHub issue", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issue", exact: true });
  await expect(dialog.getByRole("alert")).toContainText("50,000 characters or fewer");
  await expect(dialog.getByRole("button", { name: "Create issue", exact: true })).toBeDisabled();
  expect(writes).toHaveLength(0);
  await dialog.getByRole("tab", { name: "Edit", exact: true }).click();
  await dialog.getByRole("textbox", { name: "Issue body" }).fill("Reviewed shorter evidence.");
  await expect(dialog.getByRole("button", { name: "Create issue", exact: true })).toBeEnabled();
  await dialog.getByRole("button", { name: "Create issue", exact: true }).click();
  expect(writes).toHaveLength(1);
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
      : { json: { items: state.items.slice((pageNumber - 1) * 10, pageNumber * 10), total: state.items.length,
        page: pageNumber, page_size: 10, repository: repository.full_name, public: isPublic } });
  });
  return { state, writes };
}

test("bulk issues are disabled for an empty repository, not an empty filtered view", async ({ page }) => {
  await page.clock.install();
  const { state, writes } = await mockBulkIssues(page);
  let total = 0;
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: {
    repository, summary, can_write: true, finding_count: total, finding_state_counts: { open: total }, scan_count: 0,
  } }));
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => route.fulfill({ json: {
    items: [], total: 0, registered_total: total, page: 1, page_size: 10,
  } }));
  await page.goto("/security/repositories/22?search=missing");
  const trigger = page.getByRole("button", { name: "Create GitHub issues", exact: true });
  await expect(trigger).toBeDisabled();
  await expect(page.getByRole("heading", { name: "No open findings", exact: true })).toBeVisible();
  expect(state.requests).toBe(0);
  total = 1;
  await page.clock.fastForward(5000);
  await expect(trigger).toBeEnabled();
  await expect(page.getByRole("heading", { name: "No findings match these filters", exact: true })).toBeVisible();
  total = 0;
  await page.clock.fastForward(5000);
  await expect(trigger).toBeDisabled();
  expect(writes).toHaveLength(0);
});

test("oversized bulk issue drafts cannot queue other selected issues", async ({ page }) => {
  const { state, writes } = await mockBulkIssues(page);
  state.items[0].body = "x".repeat(50001);
  await page.goto("/security/repositories/22");
  await page.getByRole("button", { name: "Create GitHub issues", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await dialog.getByRole("checkbox", { name: `Select ${state.items[0].title}`, exact: true }).check();
  await dialog.getByRole("button", { name: "Review 1 issue", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("50,000 characters or fewer");
  await expect(dialog.getByRole("button", { name: "Create 1 issue", exact: true })).toBeDisabled();
  expect(writes).toHaveLength(0);
});

test("bulk GitHub issues require selection, review, count and public confirmation", async ({ page }, info) => {
  const { state, writes } = await mockBulkIssues(page);
  await page.goto("/security/repositories/22");
  const trigger = page.getByRole("button", { name: "Create GitHub issues", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("button", { name: "Review 0 issues", exact: true })).toBeDisabled();
  await expect(dialog.getByText("Destination: acme/payments", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("note")).toContainText("These issues will be public.");
  await expect(dialog.getByText("0 issues selected · 21 eligible findings", { exact: true })).toBeVisible();
  await dialog.getByRole("checkbox", { name: `Select ${state.items[0].title}`, exact: true }).check();
  await page.screenshot({ path: `/private/tmp/code-mortis-bulk-select-${info.project.name}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "Next findings" }).click();
  await expect(dialog.getByRole("checkbox", { name: `Select ${state.items[10].title}`, exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Next findings" }).click();
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
  const trigger = page.getByRole("button", { name: "Create GitHub issues", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("note")).toContainText("These issues will be private");
  await dialog.getByRole("button", { name: "Select shown", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Review 10 issues", exact: true })).toBeEnabled();
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
  const trigger = page.getByRole("button", { name: "Create GitHub issues", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Create GitHub issues", exact: true });
  await expect(dialog.getByRole("button", { name: "Review 0 issues", exact: true })).toBeDisabled();
  state.previewError = 0;
  await dialog.getByRole("button", { name: "Refresh findings", exact: true }).click();
  await dialog.getByRole("checkbox").first().check();
  state.previewError = 403;
  await dialog.getByRole("button", { name: "Next findings" }).click();
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
  await sheet.getByRole("button", { name: "Change status", exact: true }).click();
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
  await expect(page.getByRole("button", { name: "Create GitHub issues", exact: true })).toHaveCount(0);
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
  const grade = page.getByRole("link", { name: "Security grade D", exact: true });
  await expect(grade).not.toHaveAttribute("title");
  await grade.focus();
  await expect(page.getByRole("tooltip")).toContainText("The latest assessment is incomplete.");
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
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const writes = await mock(page, { running: true });
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  const stages = page.getByRole("region", { name: "Scan stages" });
  const animation = () => stages.locator(".stage-divider").evaluate((element) => {
    const style = getComputedStyle(element, "::before");
    return { name: style.animationName, state: style.animationPlayState, transform: style.transform };
  });
  await expect(stages).toHaveAttribute("data-motion", "active");
  expect(await animation()).toMatchObject({ name: "stage-sweep", state: "running" });
  const start = await animation();
  await expect.poll(async () => (await animation()).transform).not.toBe(start.transform);
  const height = (await stages.boundingBox())!.height;
  await page.screenshot({ path: info.outputPath("security-stage-animation.png") });
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect((await animation()).name).toBe("none");
  expect((await stages.boundingBox())!.height).toBe(height);
  await expect(stages.getByLabel("Scan progress count")).toHaveText("11 / 163 files");
  await expect(stages.getByRole("group", { name: "Scan progress details" })).toContainText("Analysis");
  await expect(stages.getByRole("group", { name: "Previous stage" })).toHaveCount(0);
  await expect(stages.getByRole("group", { name: "Next stage" })).toHaveCount(0);
  const trigger = stages.getByRole("button", { name: "Scan stage details", exact: true });
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger.locator(".lucide-chevron-down")).toBeVisible();
  await expect(stages.getByRole("progressbar")).toHaveCount(0);
  await expect(stages.locator('[data-slot="badge"], .animate-spin')).toHaveCount(0);
  await expect(stages).not.toContainText("of 6");
  await expect(stages.getByText("Reviewing source files for vulnerabilities.", { exact: true })).toBeVisible();
  await trigger.focus();
  await trigger.press("Enter");
  const details = page.getByRole("dialog", { name: "Scan stage details", exact: true });
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(details.locator('[data-slot="badge"]')).toHaveCount(6);
  await expect(details).toContainText("A file counts only after its result passes validation.");
  await expect(details.getByText("11 / 163 files", { exact: true })).toBeVisible();
  expect((await stages.boundingBox())!.height).toBe(height);
  await page.screenshot({ path: info.outputPath("security-stage-details.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(details).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await trigger.locator(".lucide-chevron-down").click();
  await expect(details).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Cancel scan", exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("security-focus-running.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toHaveLength(0);
});

test("focus strip updates validated progress and the current stage across polling", async ({ page }, info) => {
  await page.clock.install({ time: new Date("2026-10-18T15:08:42Z") });
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page, { running: true });
  const saved = { ...scan, status: "running", grade: null, finished_at: null,
    progress: { tokens: 4500, work: { analysis_completed: 117, analysis_total: 163, completed: 117, failed: 0 } },
    stages: { snapshot: { status: "completed" }, trivy: { status: "completed" },
      analysis: { status: "running", total: 163, completed: 117 }, assessment: { status: "pending", total: 32, completed: 0 },
      verification: { status: "pending" }, report: { status: "pending" } } };
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: saved, can_write: false, finding_count: 0, activity_count: 0, activity: [],
  } }));
  await page.goto("/security/scans/scan");
  const track = page.getByRole("region", { name: "Scan stages" });
  await expect(track.getByLabel("Scan progress count")).toHaveText("117 / 163 files");
  await expect(track).not.toContainText("0 / 32");
  await page.screenshot({ path: info.outputPath("scan-focus-running.png"), fullPage: true });
  await track.getByRole("button", { name: "Scan stage details" }).click();
  const details = page.getByRole("dialog", { name: "Scan stage details", exact: true });
  await expect(details.getByText("117 / 163 files", { exact: true })).toBeVisible();
  saved.stages.analysis.completed = 163;
  saved.stages.analysis.status = "completed";
  saved.stages.assessment.status = "running";
  saved.stages.assessment.completed = 8;
  saved.progress.work.analysis_completed = 163;
  saved.progress.work.completed = 171;
  await page.clock.fastForward(5000);
  await expect(track.getByLabel("Scan progress count")).toHaveText("8 / 32 candidates");
  await expect(track.getByText("Validating findings against source evidence.", { exact: true })).toBeVisible();
  await expect(track.getByRole("group", { name: "Scan progress details" })).toContainText("Assessment");
  await expect(details.getByText("Analysis", { exact: true }).locator("..")).toContainText("completed");
  await expect(details.getByText("Assessment", { exact: true }).locator("..")).toContainText("running");
  await expect(details.getByText("8 / 32 candidates", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.screenshot({ path: info.outputPath("scan-focus-assessment.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(writes).toHaveLength(0);
});

test("focus strip keeps its rows and report tabs fixed across every stage and outcome", async ({ page }, info) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.clock.install({ time: new Date("2026-10-18T15:00:48Z") });
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page, { running: true });
  const saved: Schema["SecurityScanOut"] = { ...scan, status: "queued", grade: null, finished_at: null,
    progress: { tokens: 0, work: { analysis_completed: 0, analysis_total: 0, completed: 0, failed: 0 } }, stages: {} };
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: saved, can_write: false, finding_count: 0, activity_count: 0, activity: [],
  } }));
  const stages = page.getByRole("region", { name: "Scan stages" });
  const current = stages.getByRole("group", { name: "Scan progress details" });
  const count = stages.locator('[aria-label="Scan progress count"], [aria-label="Stage progress unavailable"]');
  const geometry = async () => {
    const region = (await stages.boundingBox())!;
    const rows = await Promise.all([stages.getByRole("status"), count, stages.locator("p"),
      page.getByRole("tablist").first()].map(async (row) => {
        const box = (await row.boundingBox())!;
        return { top: box.y - region.y, height: box.height };
      }));
    return { height: region.height, rows };
  };
  const names = Object.keys(scan.stages);
  const explanations = ["Preparing repository files.", "Checking dependencies and configuration.",
    "Reviewing source files for vulnerabilities.", "Validating findings against source evidence.",
    "Checking whether earlier findings are fixed.", "Saving the report and security grade."];
  for (const width of info.project.name === "mobile" ? [320, 390] : [768, 1440]) {
    saved.status = "queued"; saved.stages = {}; saved.report = {};
    await page.setViewportSize({ width, height: 960 });
    await page.goto("/security/scans/scan");
    await expect(current).toContainText("Waiting to start");
    await expect(stages.locator("p")).toHaveText("Waiting for a worker to start the scan.");
    await expect(count).toHaveText("—");
    expect(await stages.locator(".stage-divider").evaluate((element) => getComputedStyle(element, "::before").animationName)).toBe("none");
    const initial = await geometry();
    saved.status = "running";
    await page.clock.fastForward(5000);
    await expect(current).toContainText("Preparing scan");
    expect(await geometry()).toEqual(initial);
    saved.stages = Object.fromEntries(names.map((name) => [name, { status: "pending" }]));
    for (let index = 0; index < names.length; index++) {
      if (index) saved.stages[names[index - 1]] = { ...saved.stages[names[index - 1]] as object, status: "completed" };
      saved.stages[names[index]] = { status: "running" };
      await page.clock.fastForward(5000);
      await expect(current).toContainText(names[index] === "verification" ? "Fix verification" : names[index][0].toUpperCase() + names[index].slice(1));
      await expect(stages.locator("p")).toHaveText(explanations[index]);
      expect(await stages.locator(".stage-divider").evaluate((element) => getComputedStyle(element, "::before").animationName)).toBe("stage-sweep");
      expect(await stages.locator("p").evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
      await expect(count).toHaveText("—");
      expect(await geometry()).toEqual(initial);
      if (["analysis", "assessment", "verification"].includes(names[index])) {
        saved.stages[names[index]] = { status: "running", total: 10000, completed: 9999 };
        await page.clock.fastForward(5000);
        await expect(count).toContainText("9,999 / 10,000");
        expect(await geometry()).toEqual(initial);
      }
      await page.screenshot({ path: info.outputPath(`scan-focus-${width}-${names[index]}.png`), fullPage: true });
    }
    saved.stages.report = { status: "completed" };
    saved.report = { findings: [] };
    for (const [status, title] of [["completed", "Scan complete"], ["partial", "Scan complete with warnings"],
      ["blocked", "Analysis blocked"], ["failed", "Analysis failed"], ["cancelled", "Analysis cancelled"]]) {
      saved.status = status;
      saved.stages.analysis = { status: status === "completed" ? "completed" : status, total: 163, completed: 117 };
      await page.clock.fastForward(5000);
      await expect(current).toContainText(title);
      expect(await stages.locator(".stage-divider").evaluate((element) => getComputedStyle(element, "::before").animationName)).toBe("none");
      await expect(stages).not.toContainText("Report ready");
      expect(await stages.locator("p").evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
      await expect(count).toHaveText("117 / 163 files");
      expect(await geometry()).toEqual(initial);
      expect(await stages.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      await page.screenshot({ path: info.outputPath(`scan-focus-${width}-${status}.png`), fullPage: true });
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  expect(writes).toHaveLength(0);
});

test("focus strip preserves terminal states and rejects invalid counts", async ({ page }, info) => {
  await page.clock.install();
  const writes = await mock(page);
  const saved: Schema["SecurityScanOut"] = { ...scan };
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: saved, can_write: false, finding_count: 0, activity_count: 0, activity: [],
  } }));
  await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 960 });
  await page.goto("/security/scans/scan");
  const track = page.getByRole("region", { name: "Scan stages" });
  await expect(track.getByRole("group", { name: "Scan progress details" })).toContainText("Scan complete");
  await expect(track.getByLabel("Stage progress unavailable")).toHaveText("—");
  for (const status of ["partial", "blocked", "failed", "cancelled"]) {
    saved.status = status;
    saved.stages = { ...scan.stages, analysis: { status: status === "blocked" ? "failed" : status, total: 163, completed: 117 },
      assessment: { status: "skipped" }, verification: { status: "skipped" } };
    await page.clock.fastForward(5000);
    await expect(track.getByRole("status")).toContainText(status === "partial" ? "Scan complete with warnings" : `Analysis ${status}`);
    await expect(track.getByLabel("Scan progress count")).toHaveText("117 / 163 files");
    await expect(track.getByRole("group", { name: "Scan progress details", exact: true })).toContainText(status === "blocked" ? "Failed" : status[0].toUpperCase() + status.slice(1));
    expect(await track.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
  saved.status = "running";
  saved.stages = { analysis: { status: "running", total: 10000, completed: 9999 } };
  await page.clock.fastForward(5000);
  await expect(track.getByLabel("Scan progress count")).toHaveText("9,999 / 10,000 files");
  saved.stages = { analysis: { status: "running", total: 0, completed: 0 } };
  await page.clock.fastForward(5000);
  await expect(track.getByLabel("Scan progress count")).toHaveText("0 / 0 files");
  for (const values of [{}, { total: 10, completed: 12 }, { total: "163", completed: 11 }, { total: 163, completed: -1 }, { total: 12, completed: 1.5 }]) {
    saved.stages = { analysis: { status: "running", ...values } };
    await page.clock.fastForward(5000);
    await expect(track.getByLabel("Stage progress unavailable")).toHaveText("—");
    await expect(track).not.toContainText("%");
  }
  saved.status = "completed";
  saved.stages = {};
  await page.clock.fastForward(5000);
  await expect(track.getByRole("group", { name: "Scan progress details" })).toContainText("Unknown");
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
  test(`overview separates ${name} from the grade and single scan status`, async ({ page }, info) => {
    await page.addInitScript(() => localStorage.setItem("theme", "dark"));
    const writes = await mock(page, { partial: true, latestStatus: "running", summary: { grade: "A", counts } });
    await page.goto("/security");
    const row = page.locator('[data-slot="security-repository-row"]');
    const badge = page.getByRole("link", { name: "View latest scan: running", exact: true });
    const capsule = row.locator('[data-slot="status-group"]');
    const segments = capsule.locator('[data-slot="badge"]');
    await expect(segments).toHaveCount(1);
    await expect(segments).toHaveText("running");
    await expect(row.getByText("Incomplete", { exact: true })).toHaveCount(0);
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
    const status = await segments.boundingBox();
    const findingsBox = await findings.boundingBox();
    expect(findingsBox!.x).toBeGreaterThan(status!.x + status!.width);
    expect(Math.abs(findingsBox!.y + findingsBox!.height / 2 - status!.y - status!.height / 2)).toBeLessThan(1);
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

test("security grades and scan stages keep their help while finding and scan status badges stay informational", async ({ page }, info) => {
  const writes = await mock(page, { partial: true, latestStatus: "running" });
  await page.goto("/security");
  const row = page.locator('[data-slot="security-repository-row"]');
  await expect(row).toBeVisible();
  const grade = row.getByRole("link", { name: "Security grade D", exact: true });
  await expect(row.locator('[data-slot="tooltip-trigger"]')).toHaveCount(1);
  if (info.project.name === "desktop") await grade.hover();
  else await grade.focus();
  await expect(page.getByRole("tooltip")).toContainText("The latest assessment is incomplete.");
  await page.keyboard.press("Escape");
  for (const badge of await row.locator('[data-security-badge]:not([data-slot="tooltip-trigger"])').all()) {
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
  await expect(page.locator('main [data-slot="tooltip-trigger"]')).toHaveCount(1);
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
  await page.getByRole("button", { name: "Scan stage details", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Scan stage details", exact: true })).toContainText("A file counts only after its result passes validation");
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
  await expect(footer.getByRole("button", { name: "Change status", exact: true })).toBeInViewport();
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
  await sheet.getByRole("button", { name: "Change status", exact: true }).click();
  const save = sheet.getByRole("button", { name: "Save decision", exact: true });
  await expect(save).toBeDisabled();
  await sheet.getByRole("combobox", { name: "Decision", exact: true }).selectOption("accepted_risk");
  await sheet.getByRole("textbox", { name: "Reason", exact: true }).fill("Restricted to an isolated environment until remediation.");
  await save.click();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ path: "/security/findings/finding", body: { state: "accepted_risk", reason: "Restricted to an isolated environment until remediation.", version: 0 } });
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("accepted risk");
  await expect(sheet.getByRole("button", { name: "Change status", exact: true })).toHaveAttribute("aria-expanded", "false");
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

test("issue synchronization takeover requires explicit review and preserves failed approvals", async ({ page }, info) => {
  const writes = await mock(page, { issue: { number: 42, actor_id: 99, operation_id: "publication-operation", url: "https://github.com/acme/payments/issues/42",
    sync_status: "failed", error: "The user who approved this issue no longer has access.", sync_approval: { actor_id: 98, revision: 2 } } });
  let stale = true;
  await page.route("**/api/v1/security/findings/finding/github-issue/reapprove", async (route) => {
    writes.push({ path: "/security/findings/finding/github-issue/reapprove", body: route.request().postDataJSON() });
    await route.fulfill(stale ? { status: 409, json: { detail: "The synchronization approval changed. Refresh and review it again." } }
      : { status: 202, json: { ...finding, issue: { sync_status: "pending" } } });
  });
  await page.goto("/security/repositories/22?finding=finding");
  await page.getByRole("button", { name: "Review sync approval", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Approve issue synchronization", exact: true });
  await expect(dialog.getByRole("link", { name: "View issue #42", exact: true })).toHaveAttribute("href", "https://github.com/acme/payments/issues/42");
  await expect(dialog.getByText(/Original publication approved by member #99/)).toBeVisible();
  await expect(dialog.getByText(/Human closure overrides/)).toBeVisible();
  const approve = dialog.getByRole("button", { name: "Approve synchronization", exact: true });
  await expect(approve).toBeDisabled();
  expect(writes).toHaveLength(0);
  await dialog.getByRole("textbox", { name: "Reason for taking responsibility" }).fill("I reviewed the linked issue and will manage status updates.");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-sync-approval-${info.project.name}.png`, fullPage: true });
  await approve.click();
  await expect(dialog.getByRole("alert")).toContainText("approval changed");
  await expect(dialog.getByRole("textbox", { name: "Reason for taking responsibility" })).toHaveValue("I reviewed the linked issue and will manage status updates.");
  stale = false;
  await approve.click();
  await expect(dialog).toBeHidden();
  expect(writes[1]).toMatchObject({ path: "/security/findings/finding/github-issue/reapprove", body: {
    version: 0, operation_id: "publication-operation", approval_revision: 2, reason: "I reviewed the linked issue and will manage status updates.",
  } });
});

test("sync approval supports keyboard cancellation in dark narrow layouts", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.setViewportSize({ width: info.project.name === "mobile" ? 320 : 1440, height: 900 });
  const writes = await mock(page, { issue: { number: 42, actor_id: 99, operation_id: "publication-operation",
    url: "https://github.com/acme/payments/issues/42", sync_status: "failed" } });
  await page.goto("/security/repositories/22?finding=finding");
  const trigger = page.getByRole("button", { name: "Review sync approval", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Approve issue synchronization", exact: true });
  const reason = dialog.getByRole("textbox", { name: "Reason for taking responsibility" });
  await reason.fill("I reviewed this issue.");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Approve synchronization", exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-sync-approval-dark-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  expect(writes).toHaveLength(0);
});

test("read-only users cannot retry or reapprove linked issue synchronization", async ({ page }) => {
  const writes = await mock(page, { canWrite: false, issue: { number: 42, actor_id: 99, operation_id: "publication-operation",
    url: "https://github.com/acme/payments/issues/42", sync_status: "failed" } });
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("link", { name: "View issue #42", exact: true })).toBeVisible();
  await expect(sheet.getByRole("button", { name: "Review sync approval", exact: true })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Retry issue synchronization", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

test("uncertain issue creation needs an individually reviewed recovery approval", async ({ page }, info) => {
  const writes = await mock(page, { issue: { operation_id: "original", started: true, sync_status: "uncertain", publication_status: "creating" } });
  await page.route("**/api/v1/security/findings/finding/github-issue", (route) => route.request().method() === "GET"
    ? route.fulfill({ json: { title: "Security finding", body: "Check ownership.", version: 0, public: true, repository: repository.full_name, recovery_token: "attempt-one" } })
    : route.fallback());
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await sheet.getByRole("button", { name: "Review issue again", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Retry GitHub issue creation", exact: true });
  await expect(dialog.getByText(/This issue will be public/)).toBeVisible();
  await expect(dialog.getByText(/Another attempt may create a duplicate/)).toBeVisible();
  const approve = dialog.getByRole("button", { name: "Approve another attempt", exact: true });
  await expect(approve).toBeDisabled();
  expect(writes).toHaveLength(0);
  await dialog.getByRole("checkbox", { name: /I checked GitHub and approve another attempt/ }).check();
  await expect(approve).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-issue-recovery-${info.project.name}.png`, fullPage: true });
  await approve.click();
  await expect(dialog).toBeHidden();
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ path: "/security/findings/finding/github-issue", body: {
    title: "Security finding", body: "Check ownership.", version: 0, public: true, recovery_token: "attempt-one",
  } });
});

test("security evidence sanitizes AI links in every Markdown section", async ({ page }) => {
  await mock(page);
  const content = "[unsafe](javascript:alert%281%29) [data](data:text/html,unsafe) [vbscript](vbscript:msgbox%281%29) [safe](https://example.com)\n\n<script>alert(1)</script>";
  await page.route("**/api/v1/security/findings/finding", (route) => route.fulfill({ json: { repository, finding: { ...finding,
    evidence: { ...finding.evidence, ...Object.fromEntries(["explanation", "trigger", "impact", "remediation", "verification"].map((field) => [field, content])) },
  }, can_write: true, observations: [], activity: [] } }));
  await page.goto("/security/repositories/22?finding=finding");
  const markdown = page.getByRole("dialog", { name: "Finding details", exact: true }).locator(".markdown");
  await expect(markdown).toHaveCount(5);
  for (const name of ["unsafe", "data", "vbscript"]) {
    const links = markdown.getByRole("link", { name, exact: true });
    await expect(links).toHaveCount(5);
    for (const link of await links.all()) await expect(link).toHaveAttribute("href", "");
  }
  for (const link of await markdown.getByRole("link", { name: "safe", exact: true }).all()) await expect(link).toHaveAttribute("href", "https://example.com");
  await expect(markdown.locator("script")).toHaveCount(0);
});

test("security finding sheet hides evidence and mutations after access is denied", async ({ page }) => {
  const writes = await mock(page);
  await page.route("**/api/v1/security/findings/finding", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.goto("/security/repositories/22?finding=finding");
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(sheet.getByRole("alert")).toContainText("Repository access was revoked.");
  await expect(sheet.getByRole("tablist", { name: "Finding information", exact: true })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Change status", exact: true })).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "Create GitHub issue", exact: true })).toHaveCount(0);
  expect(writes).toHaveLength(0);
});

test("scan history fills missing grades without substituting a previous grade", async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const writes = await mock(page);
  const statuses = [["partial", "shield-alert"], ["failed", "shield-x"], ["cancelled", "shield-off"],
    ["blocked", "shield-alert"], ["running", "loader-circle"], ["queued", "loader-circle"]];
  const otherGrades = ["A", "B", "C", "F"];
  const rows = [...statuses.map(([status]) => ({ ...scan, id: status, status, grade: null })), { ...scan, id: "warnings", status: "partial" }, scan,
    ...otherGrades.map((grade) => ({ ...scan, id: `grade-${grade}`, grade }))];
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 10, pageNumber * 10), total: rows.length, page: pageNumber, page_size: 10 } });
  });
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
  let currentPage = 1;
  for (const grade of ["A", "B", "C", "D", "F"]) {
    const targetPage = ["C", "F"].includes(grade) ? 2 : 1;
    if (targetPage !== currentPage) {
      await page.getByRole("button", { name: `${targetPage === 2 ? "Next" : "Previous"} scan history`, exact: true }).click();
      currentPage = targetPage;
    }
    const row = page.locator(`[data-slot="security-scan-row"][href="/security/scans/${grade === "D" ? "scan" : `grade-${grade}`}"]`);
    const shield = row.getByRole("img", { name: `Saved security grade ${grade}`, exact: true }).locator('[data-slot="security-grade-shield"]');
    await expect(shield).toHaveText(grade);
    await expect(shield).toHaveCSS("width", "24px");
    await expect(shield).toHaveCSS("height", "24px");
    await expect(shield).toHaveClass(grade === "A" ? /text-emerald-800/ : ["D", "F"].includes(grade) ? /text-red-700/ : /text-amber-800/);
    await expect(row.locator("a")).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Previous scan history", exact: true }).click();
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
  let totals = { finding_count: 5, finding_state_counts: { open: 3, resolved: 2 }, scan_count: 4 };
  let findingRequests = 0;
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: { repository, summary, can_write: true, ...totals } }));
  await page.route("**/api/v1/security/repositories/22/scans?**", (route) => route.fulfill({ json: {
    items: Array.from({ length: 4 }, (_, index) => ({ ...scan, id: `scan-${index}` })), total: 4, page: 1, page_size: 10,
  } }));
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => {
    if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    findingRequests++;
    const filtered = new URL(route.request().url()).searchParams.has("search");
    return route.fulfill({ json: { items: filtered ? [] : Array.from({ length: 3 }, (_, index) => ({ ...finding, id: `finding-${index}` })),
      total: filtered ? 0 : 3, page: 1, page_size: 10 } });
  });
  await page.goto("/security/repositories/22?tab=history");
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 2", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scan history 4", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-slot="security-scan-row"]')).toHaveCount(4);
  expect(findingRequests).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-tab-counts-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Findings 3", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(3);
  await page.getByRole("textbox", { name: "Search findings", exact: true }).fill("missing");
  await expect(page.getByText("No open findings", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 3", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 2", exact: true })).toBeVisible();
  totals = { finding_count: 7, finding_state_counts: { open: 4, resolved: 3 }, scan_count: 5 };
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tab", { name: "Findings 4", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 3", exact: true })).toBeVisible();
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
  await expect(page.getByRole("group", { name: "Scan progress details", exact: true })).toContainText("Analysis");
  await page.keyboard.press("Escape");
  const panel = page.getByRole("tabpanel").last();
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

test("security scan tabs count findings and activity independently of report snapshots", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  const findings = Array.from({ length: 23 }, (_, index) => ({ ...finding, id: `finding-${index}` }));
  await mockReportFindings(page, findings.slice(0, 3).map((finding) => ({ ...finding, finding_id: finding.id, outcome: "present" })));
  const activity = Array.from({ length: 201 }, (_, index) => ({ id: `event-${index}`, scan_id: scan.id, finding_id: null,
    actor_id: null, action: "scan_stage", detail: { stage: "analysis", status: "completed" }, created_at: at }));
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan: { ...scan, report: { findings: findings.slice(0, 3).map((finding) => ({ ...finding, finding_id: finding.id, outcome: "present" })) } },
    can_write: true, finding_count: 23, finding_state_counts: { open: 23 }, activity_count: 201, activity,
  } }));
  await page.route("**/api/v1/security/scans/scan/findings?**", (route) => {
    if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({ json: { items: findings.slice((pageNumber - 1) * 10, pageNumber * 10), total: 23, page: pageNumber, page_size: 10 } });
  });
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Findings recorded in this scan", exact: true }).locator('[data-slot="finding-card"]')).toHaveCount(10);
  await expect(page.getByRole("tab", { name: "Findings 23", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Activity 201", exact: true })).toBeVisible();
  const tabList = await page.getByRole("tablist", { name: "Security scan", exact: true }).boundingBox();
  const lastTab = await page.getByRole("tab", { name: "Activity 201", exact: true }).boundingBox();
  expect(lastTab!.y + lastTab!.height).toBeLessThanOrEqual(tabList!.y + tabList!.height);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-scan-tab-counts-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Findings 23", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await page.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "open findings pagination" }).getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await page.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(3);
  await expect(page.getByRole("tab", { name: "Findings 23", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Activity 201", exact: true }).click();
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(timeline.getByRole("listitem")).toHaveCount(201);
  await timeline.getByRole("listitem").last().scrollIntoViewIfNeeded();
  await expect(timeline.getByRole("listitem").last()).toBeVisible();
  await expect(page.getByRole("navigation", { name: "activities pagination" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Next activities", exact: true })).toHaveCount(0);
});

test("scan findings filter new and repeated observations like review runs without changing saved reports", async ({ page }, info) => {
  await mock(page);
  await mockReportFindings(page, [{ ...finding, finding_id: finding.id, outcome: "present", title: "Original report evidence" }]);
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const rows = [
    { result: "new", finding: { ...finding, title: "New defect" } },
    ...Array.from({ length: 21 }, (_, index) => ({ result: "open_repeat", finding: { ...finding, id: `repeat-${index}`, title: `Earlier defect ${index + 1}`, first_scan_id: "earlier", severity: "medium" } })),
    ...["resolved", "false_positive", "accepted_risk"].map((state) => ({ result: `${state}_repeat`, finding: { ...finding, id: state, title: `${state} repeated defect`, first_scan_id: "earlier", state } })),
    ...["fixed", "not_observed", "uncertain"].map((outcome) => ({ result: outcome, finding: { ...finding, id: outcome, title: `Verification ${outcome}`, first_scan_id: "earlier", state: outcome === "fixed" ? "resolved" : "open" } })),
  ];
  const resultCounts = Object.fromEntries([...new Set(rows.map((row) => row.result))].map((key) => [key, rows.filter((row) => row.result === key).length]));
  const writes: string[] = [];
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository, scan: { ...scan,
    report: { findings: [{ ...finding, finding_id: finding.id, outcome: "present", title: "Original report evidence" }] } },
    can_write: true, finding_count: rows.length, finding_state_counts: { open: 24, resolved: 2, false_positive: 1, accepted_risk: 1 }, resolved_count: 1, activity_count: 0, activity: [] } }));
  await page.route("**/api/v1/security/scans/scan/findings**", (route) => {
    const request = route.request();
    if (request.method() !== "GET") writes.push(request.method());
    const params = new URL(request.url()).searchParams;
    const selected = rows.filter((row) => row.finding.state === params.get("state") && (!params.has("outcome") || params.getAll("outcome").includes(row.result))
      && (!params.has("severity") || row.finding.severity === params.get("severity"))
      && (!params.has("search") || row.finding.title.toLowerCase().includes(params.get("search")!.toLowerCase())));
    const pageNumber = Number(params.get("page") ?? 1);
    return route.fulfill({ json: { items: selected.slice((pageNumber - 1) * 10, pageNumber * 10).map((row) => row.finding),
      total: selected.length, registered_total: rows.length, state_counts: { open: 24, resolved: 2, false_positive: 1, accepted_risk: 1 }, page: pageNumber, page_size: 10, result_counts: resultCounts } });
  });
  await page.goto("/security/scans/scan?tab=findings&assessment_outcome=withheld");
  const list = page.getByRole("region", { name: "Findings recorded in this scan", exact: true });
  await expect(page.getByRole("tab", { name: "Findings 24", exact: true })).toBeVisible();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Result", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /^Open repeats/ })).toHaveAccessibleName("Open repeats 21");
  await page.getByRole("checkbox", { name: /^Open repeats/ }).click();
  await page.keyboard.press("Escape");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await list.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(list.getByRole("status")).toHaveText("Page 2 of 3 · 21 total");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await list.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(list.getByText("open", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("status").filter({ hasText: "21 of 24 findings" })).toBeAttached();
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
  await page.getByRole("checkbox", { name: /^Medium(?: |$)/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Medium(?: |$)/ })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Filter by Severity", exact: true })).toBeHidden();
  await expect(page.getByRole("heading", { name: "No findings match these filters", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await expect(page.getByRole("textbox", { name: "Search scan findings", exact: true })).toBeFocused();
  await page.goto("/security/scans/scan?tab=findings&outcome=not_observed");
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(list).toContainText("Verification not_observed");
  await page.reload();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await page.getByRole("tab", { name: "Resolved 2", exact: true }).click();
  await expect(list).toContainText("Verification fixed");
  await page.getByRole("tab", { name: "Findings 24", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(1);
  expect(writes).toHaveLength(0);
});

test("security tabs display zero for empty repositories and scans", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/security/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/repositories/22")) return route.fulfill({ json: { repository, summary, can_write: true, finding_count: 0, scan_count: 0 } });
    if (path.endsWith("/scans/scan")) return route.fulfill({ json: { repository, scan, can_write: true, finding_count: 0, activity_count: 0, activity: [] } });
    if (path.endsWith("/findings") || path.endsWith("/scans")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    return route.fallback();
  });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scan history 0", exact: true })).toBeVisible();
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Activity 0", exact: true })).toBeVisible();
});

test("security lists omit pagination when all results fit on one page", async ({ page }, info) => {
  await mock(page);
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => route.fulfill({ json: {
    items: new URL(route.request().url()).searchParams.get("state") === "resolved" ? [] : Array.from({ length: 3 }, (_, index) => ({ ...finding, id: `finding-${index}` })), total: new URL(route.request().url()).searchParams.get("state") === "resolved" ? 0 : 3, page: 1, page_size: 10,
  } }));
  await page.goto("/security/repositories/22");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(3);
  await expect(page.locator('[data-slot="pagination"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^(Previous|Next|Load more)/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `/private/tmp/code-mortis-security-no-pagination-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Scan history 1", exact: true }).click();
  await expect(page.locator('[data-slot="security-scan-row"]')).toHaveCount(1);
  await expect(page.locator('[data-slot="pagination"]')).toHaveCount(0);
  await page.goto("/security/scans/scan?tab=findings");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await expect(page.locator('[data-slot="pagination"]')).toHaveCount(0);
});

for (const history of [false, true]) {
  test(`security ${history ? "scan history" : "findings"} keeps loaded rows on page failure and hides them after access loss`, async ({ page }) => {
    await mock(page);
    await page.clock.install();
    const label = history ? "scan history" : "open findings";
    const rows = Array.from({ length: 5 }, (_, index) => ({ ...(history ? scan : finding), id: `item-${index}` }));
    let failure = 503;
    const requests: number[] = [];
    await page.route(`**/api/v1/security/repositories/22/${history ? "scans" : "findings"}?**`, (route) => {
      if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
      const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
      requests.push(pageNumber);
      return route.fulfill(failure ? { status: failure, json: { detail: failure === 403 ? "Repository access revoked" : "Results temporarily unavailable" } }
        : { json: { items: rows.slice((pageNumber - 1) * 2, pageNumber * 2), total: rows.length, page: pageNumber, page_size: 2 } });
    });
    await page.goto(`/security/repositories/22${history ? "?tab=history" : ""}`);
    const panel = page.getByRole("tabpanel").last();
    const cards = panel.locator(`[data-slot="${history ? "security-scan-row" : "finding-card"}"]`);
    await expect(panel.getByText("Results temporarily unavailable", { exact: true })).toBeVisible();
    await expect(panel.getByText(history ? "No security scans yet" : "No open findings", { exact: true })).toHaveCount(0);
    failure = 0;
    await panel.getByRole("button", { name: history ? "Retry scan history" : "Retry findings", exact: true }).first().click();
    await expect(cards).toHaveCount(2);
    const footer = panel.locator('[data-slot="pagination"]');
    await expect(footer.getByRole("status")).toHaveText("Page 1 of 3 · 5 total");
    failure = 503;
    await panel.getByRole("button", { name: `Next ${label}`, exact: true }).click();
    await expect(panel.getByRole("button", { name: history ? "Retry scan history" : "Retry findings", exact: true }).first()).toBeVisible();
    await expect(cards).toHaveCount(2);
    await expect(footer.getByRole("status")).toHaveText("Page 1 of 3 · 5 total");
    failure = 0;
    await panel.getByRole("button", { name: history ? "Retry scan history" : "Retry findings", exact: true }).first().click();
    await expect(cards).toHaveCount(2);
    await expect(footer.getByRole("status")).toHaveText("Page 2 of 3 · 5 total");
    failure = 403;
    await panel.getByRole("button", { name: `Next ${label}`, exact: true }).click();
    await expect(panel.getByText("Repository access revoked", { exact: true })).toBeVisible();
    await expect(cards).toHaveCount(0);
    expect(requests).toEqual([1, 1, 2, 2, 3]);
  });
}

test("security findings replace pages, keep live polling, and reset when filters change", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  await page.clock.install();
  const rows = Array.from({ length: 4 }, (_, index) => ({ ...finding, id: `finding-${index}`, title: `Security finding ${index}` }));
  const requests: { page: number; search: string | null }[] = [];
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => {
    if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    const params = new URL(route.request().url()).searchParams;
    const pageNumber = Number(params.get("page"));
    const search = params.get("search");
    requests.push({ page: pageNumber, search });
    return route.fulfill({ json: { items: search ? [rows[3]] : pageNumber === 1 ? rows.slice(0, 2) : rows.slice(2, 4),
      total: search ? 1 : 4, page: pageNumber, page_size: 2 } });
  });
  await page.goto("/security/repositories/22");
  const panel = page.getByRole("tabpanel").last();
  const cards = panel.locator('[data-slot="finding-card"]');
  const footer = panel.locator('[data-slot="pagination"]');
  await expect(cards).toHaveCount(2);
  await expect(footer.getByRole("status")).toHaveText("Page 1 of 2 · 4 total");
  await page.clock.fastForward(5000);
  await expect.poll(() => requests.length).toBe(2);
  await panel.getByRole("button", { name: "Next open findings", exact: true }).focus();
  await page.screenshot({ path: `/private/tmp/code-mortis-security-pagination-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Enter");
  await expect(cards).toHaveCount(2);
  await expect(footer.getByRole("status")).toHaveText("Page 2 of 2 · 4 total");
  await expect(panel.getByRole("button", { name: "Next open findings", exact: true })).toBeDisabled();
  await page.clock.fastForward(15000);
  await expect.poll(() => requests.filter((request) => request.page === 2).length).toBeGreaterThan(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const search = page.getByRole("textbox", { name: "Search findings", exact: true });
  await search.fill("ownership");
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText("Security finding 3");
  await expect(footer).toHaveCount(0);
  await search.fill("");
  await expect(cards).toHaveCount(2);
  await expect(footer.getByRole("status")).toHaveText("Page 1 of 2 · 4 total");
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
  await page.route("**/api/v1/security/repositories/22/scans**", (route) => route.fulfill({ json: { items: rows, total: rows.length, page: 1, page_size: 10 } }));
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: { repository, scan: timed, can_write: false, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.route("**/api/v1/security/scans/running", (route) => route.fulfill({ json: { repository, scan: running, can_write: false, finding_count: 0, activity_count: 0, activity: [] } }));
  await page.goto("/security/repositories/22?tab=history");
  const history = page.getByRole("tabpanel").last();
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
  for (const id of [4, 6]) {
    const assessing = list.getByRole("button", { name: new RegExp(`Security candidate ${id}`) }).getByText("Assessing", { exact: true });
    await expect(assessing).toBeVisible();
    await expect(assessing).toHaveClass(/bg-primary/);
  }
  const queued = list.getByRole("button", { name: /Security candidate 5/ }).getByText("Awaiting assessment", { exact: true });
  await expect(queued).toBeVisible();
  await expect(queued).not.toHaveClass(/bg-primary/);
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
  await expect(drawer.getByRole("region", { name: "Code snippet" }).locator('[data-slot="code-line-numbers"]')).toHaveText("14");
  await drawer.getByText("Original proposal", { exact: true }).click();
  await expect(drawer.getByText("How to fix it", { exact: true })).toBeVisible();
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
  await expect(drawer.getByText("Assessing", { exact: true })).toHaveClass(/bg-primary/);
  state.rows[4] = securityAssessment("4", { status: "running", progress_phase: "validating", outcome: "pending", result: { verdict: "confirmed", explanation: "Validated response", evidence: finding.evidence.evidence } });
  state.revision++;
  await page.clock.fastForward(5100);
  await expect(drawer.getByText("Response received; validating against the current source and finding history.")).toBeVisible();
  await expect(drawer.getByText("Assessing", { exact: true })).toHaveClass(/bg-primary/);
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
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(list.getByRole("status")).toHaveText("Page 2 of 3 · 25 total");
  await list.getByRole("button", { name: "Next assessments" }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(5);
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

test("security overview uses backend multiselect counts and pages and resets previously loaded filters", async ({ page }, info) => {
  await mock(page);
  const requests: URLSearchParams[] = [];
  await page.route("**/api/v1/security/repositories?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    const filtered = params.has("grade") || params.has("status") || params.has("search");
    const pageNumber = Number(params.get("page"));
    const rows = Array.from({ length: filtered ? 3 : 5 }, (_, index) => ({ repository: { ...repository, id: 22 + index, full_name: `team/server-result-${index}` }, summary }));
    return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 2, pageNumber * 2), total: rows.length, registered_total: 505,
      grade_counts: { A: 400, D: 100, unrated: 5 }, status_counts: { current: 500, not_scanned: 5 }, page: pageNumber, page_size: 2 } });
  });
  await page.goto("/security");
  await expect(page.locator('[data-slot="security-repository-row"]')).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Previous repositories", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Next repositories", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "repositories pagination" }).getByRole("status")).toHaveText("Page 2 of 3 · 5 total");
  await expect(page.locator('[data-slot="security-repository-row"]')).toHaveCount(2);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Grade", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("400", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /^A(?: |$)/ }).click();
  await page.getByRole("checkbox", { name: /^D(?: |$)/ }).click();
  await page.keyboard.press("Escape");
  await expect.poll(() => requests.at(-1)?.getAll("grade")).toEqual(["A", "D"]);
  await expect(page.locator('[data-slot="security-repository-row"]')).toHaveCount(2);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Scan status", exact: true }).click();
  await page.getByRole("checkbox", { name: /^Current(?: |$)/ }).click();
  await page.getByRole("checkbox", { name: /^Not scanned(?: |$)/ }).click();
  await page.keyboard.press("Escape");
  await expect.poll(() => requests.at(-1)?.getAll("status")).toEqual(["current", "not_scanned"]);
  await expect(page.locator('[data-slot="security-repository-row"]')).toHaveCount(2);
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("1");
  await page.screenshot({ path: info.outputPath("security-multiselect-overview.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Remove Grade filter", exact: true }).click();
  await page.getByRole("button", { name: "Remove Scan status filter", exact: true }).click();
  await expect(page.locator('[data-slot="security-repository-row"]')).toHaveCount(2);
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("1");
});

for (const scope of ["repository", "scan"] as const) test(`${scope} security findings send every selected severity and type to backend pages`, async ({ page }, info) => {
  await mock(page);
  const requests: URLSearchParams[] = [];
  const path = scope === "scan" ? "/security/scans/scan/findings" : "/security/repositories/22/findings";
  await page.route(`**/api/v1${path}?**`, (route) => {
    if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    const pageNumber = Number(params.get("page"));
    const rows = Array.from({ length: 3 }, (_, index) => ({ ...finding, id: String(index), title: `Backend finding ${index}` }));
    return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 2, pageNumber * 2), total: 3, registered_total: 900,
      result_counts: { new: 3 }, state_counts: { open: 700, resolved: 200 }, severity_counts: { high: 700, medium: 200 }, kind_counts: { code: 700, secret: 200 },
      page: pageNumber, page_size: 2 } });
  });
  await page.goto(scope === "scan" ? "/security/scans/scan?tab=findings" : "/security/repositories/22");
  await page.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
  for (const [filter, values] of [["Severity", ["High", "Medium"]], ["Type", ["Code", "Secret"]]] as [string, string[]][]) {
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("menuitem", { name: filter, exact: true }).click();
    await page.getByRole("checkbox", { name: new RegExp(`^${values[0]}(?: |$)`) }).click();
    await page.getByRole("checkbox", { name: new RegExp(`^${values[1]}(?: |$)`) }).click();
    await page.keyboard.press("Escape");
  }
  await expect.poll(() => requests.at(-1)?.getAll("severity")).toEqual(["high", "medium"]);
  await expect.poll(() => requests.at(-1)?.getAll("kind")).toEqual(["code", "secret"]);
  await expect.poll(() => requests.at(-1)?.getAll("state")).toEqual(["open"]);
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("1");
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(2);
  await page.screenshot({ path: info.outputPath(`security-${scope}-multiselect.png`), fullPage: true });
  await page.getByRole("button", { name: "Next open findings", exact: true }).click();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
  expect(requests.at(-1)?.get("page")).toBe("2");
  expect(requests.at(-1)?.getAll("severity")).toEqual(["high", "medium"]);
});

test("security assessments load backend pages beyond one hundred and keep rows on next-page failure", async ({ page }) => {
  const { state } = await mockSecurityAssessments(page);
  state.rows = Array.from({ length: 105 }, (_, index) => securityAssessment(String(index)));
  await page.goto("/security/scans/scan?tab=assessment");
  const list = page.getByRole("region", { name: "Security assessments" });
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  state.fail = 503;
  await list.getByRole("button", { name: "Next assessments", exact: true }).click();
  await expect(list.getByRole("button", { name: "Retry assessments", exact: true })).toBeVisible();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  state.fail = 0;
  await list.getByRole("button", { name: "Retry assessments", exact: true }).click();
  await expect(list.getByRole("status")).toHaveText("Page 2 of 11 · 105 total");
  for (let pageNumber = 3; pageNumber <= 11; pageNumber++) {
    await list.getByRole("button", { name: "Next assessments", exact: true }).click();
    await expect(list.getByRole("status")).toHaveText(`Page ${pageNumber} of 11 · 105 total`);
    await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(pageNumber === 11 ? 5 : 10);
  }
  expect(state.requests.some((path) => path.includes("page=11"))).toBe(true);
  expect(state.requests.some((path) => path.includes("page_size="))).toBe(false);
  await expect(list.getByRole("button", { name: "Next assessments", exact: true })).toBeDisabled();
  await list.getByRole("button", { name: "Previous assessments", exact: true }).click();
  await expect(list.locator('[data-slot="finding-card"]')).toHaveCount(10);
  await expect(list.getByRole("status")).toHaveText("Page 10 of 11 · 105 total");
});

test("security coverage uses compact rows with authoritative counts and independent pages", async ({ page }, info) => {
  await mock(page);
  const longPath = `src/${"long-directory/".repeat(10)}pending.ts`;
  const units = [
    { id: "0", task: "analysis:src/orders.ts", status: "completed" },
    { id: "1", task: `analysis:${longPath}`, status: "running" },
    ...Array.from({ length: 9 }, (_, index) => ({ id: String(index + 2), task: `assessment:finding-${index}`, status: "completed" })),
  ];
  await page.route("**/api/v1/security/scans/scan/coverage/units?**", (route) => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({ json: { items: units.slice((pageNumber - 1) * 10, pageNumber * 10), total: units.length, page: pageNumber, page_size: 10 } });
  });
  await page.route("**/api/v1/security/scans/scan/coverage/problems?**", (route) => route.fulfill({ json: {
    items: [{ id: "0", path: "large.bin", reason: "File exceeds the source size limit." }], total: 1, page: 1, page_size: 10,
  } }));
  await page.goto("/security/scans/scan?tab=coverage");
  const coverage = page.getByRole("region", { name: "Coverage", exact: true });
  const work = coverage.getByRole("region", { name: "Work units", exact: true });
  const excluded = coverage.getByRole("region", { name: "Excluded and unsupported files", exact: true });
  await expect(work.getByRole("heading")).toHaveText("Work units11");
  await expect(work.getByRole("list")).toBeHidden();
  await expect(excluded.getByRole("list")).toBeHidden();
  await page.screenshot({ path: `/private/tmp/security-coverage-collapsed-${info.project.name}.png`, fullPage: true });
  await work.locator("summary").first().focus();
  await page.keyboard.press("Enter");
  await excluded.getByRole("heading").click();
  await coverage.getByRole("heading", { name: /^Coverage problems/ }).click();
  await expect(work.getByRole("listitem")).toHaveCount(10);
  await expect(work.getByRole("listitem").filter({ hasText: longPath })).toContainText(/running/i);
  await expect(coverage.locator("dl > div").filter({ hasText: "Analyzed files" })).toHaveText("Analyzed files1");
  await expect(excluded.getByRole("listitem")).toContainText("Generated directory");
  await expect(excluded.getByRole("listitem")).toContainText("Not analyzed");
  await expect(coverage.getByRole("region", { name: "Coverage problems", exact: true })).toContainText("File exceeds the source size limit.");
  await expect(coverage).not.toContainText("This action could not be completed.");
  for (const theme of ["light", "dark"]) {
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await work.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/private/tmp/security-coverage-${info.project.name}-${theme}.png` });
  }
  await work.getByRole("button", { name: "Next work units", exact: true }).click();
  await expect(work.getByRole("listitem")).toHaveCount(1);
  await expect(work.getByRole("heading")).toHaveText("Work units11");
  await expect(excluded.getByRole("listitem")).toHaveCount(1);
  await work.getByRole("heading").click();
  await expect(work.getByRole("list")).toBeHidden();
  await expect(excluded.getByRole("list")).toBeVisible();
  await work.getByRole("heading").click();
  await expect(work.getByRole("listitem")).toHaveCount(1);
  await coverage.getByRole("heading", { name: /^Prepared source inventory/ }).click();
  const inventory = coverage.getByRole("region", { name: "Prepared source inventory", exact: true });
  await expect(inventory.getByRole("listitem")).toHaveText("src/orders.ts");
  await expect(inventory.locator('[data-slot="badge"]')).toHaveCount(0);
  await coverage.getByText("Scanner details", { exact: true }).click();
  await expect(coverage.getByText("0.76.0", { exact: true })).toBeVisible();
});

test("security coverage retains empty, retry, polling and access-loss states", async ({ page }) => {
  await page.clock.install();
  await mock(page, { running: true });
  let failure = 503;
  let units: Record<string, unknown>[] = [];
  await page.route("**/api/v1/security/scans/scan/coverage/units?**", (route) => failure
    ? route.fulfill({ status: failure, json: { detail: "Coverage unavailable" } })
    : route.fulfill({ json: { items: units, total: units.length, page: 1, page_size: 10 } }));
  await page.goto("/security/scans/scan?tab=coverage");
  const work = page.getByRole("region", { name: "Work units", exact: true });
  await expect(work.getByRole("heading")).toContainText("Unavailable");
  await work.getByRole("heading").click();
  await expect(work.getByRole("button", { name: "Retry work units", exact: true })).toBeVisible();
  await expect(work.getByText(/No work results/)).toHaveCount(0);
  failure = 0;
  await work.getByRole("button", { name: "Retry work units", exact: true }).click();
  await expect(work.getByText(/No work results recorded yet/)).toBeVisible();
  units = [{ id: "0", task: "analysis:private/source.ts", status: "running" }];
  await page.clock.fastForward(5000);
  await expect(work.getByRole("listitem")).toContainText(/running/i);
  units[0].status = "completed";
  await page.clock.fastForward(5000);
  await expect(work.getByRole("listitem")).toContainText(/completed/i);
  failure = 403;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tabpanel").last().getByRole("alert")).toBeVisible();
  await expect(page.getByText("analysis:private/source.ts", { exact: true })).toHaveCount(0);
});

test("scan activity shows milestones while individual events stay in finding history", async ({ page }, info) => {
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const created = { id: "created", scan_id: "scan", finding_id: "finding", actor_id: null, action: "finding_created", detail: {}, created_at: at };
  await mock(page, { activity: [created] });
  const rows = [
    { id: "completed", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_completed", detail: {}, created_at: at },
    { id: "started", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_stage", detail: { stage: "analysis", status: "running" }, created_at: at },
  ];
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, scan, can_write: true, finding_count: 1, finding_state_counts: { open: 1 }, activity_count: 2, activity: rows,
  } }));
  await page.goto("/security/scans/scan?tab=activity");
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(page.getByRole("tab", { name: "Activity 2", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(timeline.locator("li p.font-medium")).toHaveText(["Scan completed", "Analysis started"]);
  await expect(timeline.getByRole("button")).toHaveCount(0);
  await expect(timeline).not.toContainText("Finding created");
  await expect(page.getByRole("navigation", { name: "activities pagination" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("activity-milestones.png"), fullPage: true });
  await page.getByRole("tab", { name: "Findings 1", exact: true }).click();
  await page.locator('[data-slot="finding-card"]').click();
  const drawer = page.getByRole("dialog", { name: "Finding details" });
  await drawer.getByRole("tab", { name: "Activity 2", exact: true }).click();
  await expect(drawer.getByText("Finding created", { exact: true })).toBeVisible();
});

test("scan activity uses compact rows and preserves partial outcomes and diagnostics", async ({ page }, info) => {
  await page.clock.install();
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await mock(page);
  const failure = "AI result delivery failed. Technical details: saved scan response " + "detail".repeat(120);
  let items = [
    { id: "partial", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_completed", detail: { status: "partial" }, created_at: at },
    { id: "failed", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_failed", detail: { reason: failure }, created_at: at },
    { id: "started", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_stage", detail: { stage: "analysis", status: "running" }, created_at: at },
  ];
  let unavailable = false;
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill(unavailable
    ? { status: 503, json: { detail: "Activity is temporarily unavailable." } }
    : { json: { repository, scan, can_write: true, finding_count: 1, activity_count: items.length, activity: items } }));
  await page.goto("/security/scans/scan?tab=activity");
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(timeline.locator("li p.font-medium")).toHaveText(["Scan finished with incomplete work", "Scan failed", "Analysis started"]);
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 3");
  await expect(timeline.locator("time").first()).toHaveAttribute("datetime", at);
  await timeline.getByText("Technical details", { exact: true }).click();
  await expect(timeline.getByRole("region", { name: "Scan failed technical details" })).toHaveText(failure);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("scan-activity.png"), fullPage: true });
  await page.getByRole("tab", { name: "Coverage", exact: true }).click();
  items.unshift({ id: "cancelled", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_cancelled", detail: { status: "cancelled" }, created_at: at });
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 4");
  await page.getByRole("tab", { name: "Activity 4", exact: true }).click();
  await expect(timeline.getByRole("listitem")).toHaveCount(4);
  items = [];
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tablist").getByRole("tab").last()).toHaveText("Activity 0");
  await expect(page.getByText("No activity recorded yet.")).toBeVisible();
  unavailable = true;
  await page.clock.fastForward(5000);
  await expect(page.getByText("No activity recorded yet.")).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveText("Activity is temporarily unavailable.");
  unavailable = false;
  await page.getByRole("button", { name: "Reload scan", exact: true }).click();
  await expect(page.getByText("No activity recorded yet.")).toBeVisible();
});

test("security coverage stays paginated while scan activity shows all milestones and hides them on access loss", async ({ page }) => {
  await page.clock.install();
  await mock(page);
  const requests: string[] = [];
  let denied = false;
  const activity = [
    { id: "completed", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_completed", detail: {}, created_at: at },
    { id: "created", scan_id: "scan", finding_id: null, actor_id: null, action: "scan_created", detail: {}, created_at: at },
  ];
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill(denied
    ? { status: 403, json: { detail: "Access denied" } }
    : { json: { repository, scan, can_write: true, finding_count: 1, activity_count: activity.length, activity } }));
  await page.route("**/api/v1/security/scans/scan/coverage/inventory?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params.toString());
    if (denied) return route.fulfill({ status: 403, json: { detail: "Access denied" } });
    const pageNumber = Number(params.get("page"));
    return route.fulfill({ json: { items: [{ id: String(pageNumber), path: `private/source-${pageNumber}.ts` }], total: 2, page: pageNumber, page_size: 1 } });
  });
  await page.goto("/security/scans/scan?tab=coverage");
  await page.getByRole("heading", { name: /^Prepared source inventory/ }).click();
  await expect(page.getByText("private/source-1.ts", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Next prepared files", exact: true }).click();
  await expect(page.getByText("private/source-2.ts", { exact: true })).toBeVisible();
  expect(requests).toContain("page=2");
  await page.getByRole("tab", { name: /Activity/ }).click();
  const timeline = page.getByRole("list", { name: "Activity events" });
  await expect(timeline.locator("li p.font-medium")).toHaveText(["Scan completed", "Scan created"]);
  activity.unshift({ ...activity[0], id: "cancelled", action: "scan_cancelled" });
  await page.clock.fastForward(5000);
  await expect(page.getByRole("tab", { name: "Activity 3", exact: true })).toBeVisible();
  await expect(timeline.locator("li p.font-medium")).toHaveText(["Scan cancelled", "Scan completed", "Scan created"]);
  await expect(page.getByRole("navigation", { name: "activities pagination" })).toHaveCount(0);
  denied = true;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("alert")).toHaveText("Access denied");
  await expect(timeline).toHaveCount(0);
});

test("security finding histories use independent backend pages and hide both histories after access loss", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/security/findings/finding", (route) => route.fulfill({ json: { repository, finding, can_write: true,
    observations: [], activity: [], observation_count: 125, activity_count: 205 } }));
  let denied = false;
  const requests: string[] = [];
  for (const section of ["observations", "activity"]) await page.route(`**/api/v1/security/findings/finding/${section}?**`, (route) => {
    const url = new URL(route.request().url());
    requests.push(url.pathname + url.search);
    if (denied) return route.fulfill({ status: 403, json: { detail: "Access denied" } });
    const pageNumber = Number(url.searchParams.get("page"));
    const item = section === "observations" ? { id: String(pageNumber), scan_id: `scan-${pageNumber}`, outcome: pageNumber === 1 ? "present" : "not_observed", created_at: at }
      : { id: String(pageNumber), scan_id: "scan", finding_id: "finding", actor_id: null, action: `private_activity_${pageNumber}`, detail: {}, created_at: at };
    return route.fulfill({ json: { items: [item], total: 2, page: pageNumber, page_size: 1 } });
  });
  await page.goto("/security/repositories/22?finding=finding&finding_tab=activity");
  const drawer = page.getByRole("dialog", { name: "Finding details" });
  await expect(drawer.getByRole("tab", { name: "Activity 330", exact: true })).toBeVisible();
  await expect(drawer.getByText("Private activity 1", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Next observations", exact: true }).click();
  await expect(drawer.getByRole("link", { name: "Not observed", exact: true })).toBeVisible();
  expect(requests.some((path) => path.endsWith("/observations?page=2"))).toBe(true);
  expect(requests.some((path) => path.endsWith("/activity?page=2"))).toBe(false);
  denied = true;
  await drawer.getByRole("button", { name: "Next finding activity", exact: true }).click();
  await expect(drawer.getByRole("alert")).toBeVisible();
  await expect(drawer.getByText("Private activity 1", { exact: true })).toHaveCount(0);
  await expect(drawer.getByRole("link", { name: "Not observed", exact: true })).toHaveCount(0);
});

test("security repository ordering is backend driven, defaults to updated descending, and resets pages", async ({ page }, info) => {
  await mock(page);
  const requests: URLSearchParams[] = [];
  const rows = ["alpha", "bravo", "charlie"].map((name, index) => ({ repository: { ...repository, id: 22 + index, full_name: `team/${name}` }, summary }));
  await page.route("**/api/v1/security/repositories?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    const ordering = params.get("ordering");
    const ordered = ordering === "updated_at" ? [rows[2], rows[0], rows[1]] : ordering === "repository" ? rows
      : ordering === "-repository" ? [...rows].reverse() : [rows[1], rows[2], rows[0]];
    const pageNumber = Number(params.get("page"));
    return route.fulfill({ json: { items: ordered.slice(pageNumber - 1, pageNumber), total: 3, registered_total: 3,
      grade_counts: { D: 3 }, status_counts: { current: 3 }, page: pageNumber, page_size: 1 } });
  });
  await page.goto("/security?search=team&grade=D&grade=unrated");
  const list = page.locator('[data-slot="security-repository-row"]');
  await expect(page.getByRole("button", { name: "Sort by Updated", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toBeVisible();
  await expect(list).toHaveCount(1);
  await expect(list.first()).toContainText("team/bravo");
  expect(requests.at(-1)?.get("ordering")).toBe("-updated_at");
  await page.getByRole("button", { name: "Next repositories", exact: true }).click();
  await expect(list).toHaveCount(1);
  await expect(list.first()).toContainText("team/charlie");
  await page.getByRole("button", { name: "Sort ascending", exact: true }).click();
  await expect(page).toHaveURL(/ordering=updated_at/);
  await expect(list).toHaveCount(1);
  await expect(list.first()).toContainText("team/charlie");
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("1");
  expect(requests.at(-1)?.getAll("grade")).toEqual(["D", "unrated"]);
  expect(requests.at(-1)?.get("search")).toBe("team");
  await page.getByRole("button", { name: "Sort by Updated", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Repository", exact: true }).click();
  await expect(list.first()).toContainText("team/alpha");
  await expect(page).toHaveURL(/ordering=repository/);
  await page.getByRole("button", { name: "Sort descending", exact: true }).click();
  await expect(list.first()).toContainText("team/charlie");
  await page.reload();
  await expect(page.getByRole("button", { name: "Sort by Repository", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toBeVisible();
  await expect(list.first()).toContainText("team/charlie");
  expect(requests.at(-1)?.get("ordering")).toBe("-repository");
  await page.screenshot({ path: info.outputPath("security-ordering.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto("/security?ordering=invalid");
  await expect(page.getByRole("button", { name: "Sort by Updated", exact: true })).toBeVisible();
  await expect(list.first()).toContainText("team/bravo");
  expect(requests.at(-1)?.get("ordering")).toBe("-updated_at");
});

test("shared security pagination replaces ten-item pages, keeps focus, and recovers when the last page shrinks", async ({ page }, info) => {
  await mock(page);
  await page.clock.install();
  let total = 23;
  let release: (() => void) | undefined;
  let hold = true;
  const requests: number[] = [];
  await page.route("**/api/v1/security/repositories/22/findings?**", async (route) => {
    if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    requests.push(pageNumber);
    if (pageNumber === 2 && hold) await new Promise<void>((resolve) => { release = resolve; });
    const rows = Array.from({ length: total }, (_, index) => ({ ...finding, id: String(index), title: `Paged finding ${index}` }));
    return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 10, pageNumber * 10), total, page: pageNumber, page_size: 10 } });
  });
  await page.goto("/security/repositories/22");
  const panel = page.getByRole("tabpanel").last();
  const cards = panel.locator('[data-slot="finding-card"]');
  const pagination = panel.getByRole("navigation", { name: "open findings pagination", exact: true });
  const previous = pagination.getByRole("button", { name: "Previous open findings", exact: true });
  const next = pagination.getByRole("button", { name: "Next open findings", exact: true });
  await expect(cards).toHaveCount(10);
  await expect(pagination.getByRole("status")).toHaveText("Page 1 of 3 · 23 total");
  await expect(previous).toBeDisabled();
  await next.focus();
  await page.keyboard.press("Enter");
  await expect(next).toBeDisabled();
  await expect(cards.first()).toContainText("Paged finding 0");
  await page.keyboard.press("Enter");
  expect(requests).toEqual([1, 2]);
  hold = false;
  await expect.poll(() => !!release).toBe(true);
  release!();
  await expect(cards.first()).toContainText("Paged finding 10");
  await expect(next).toBeFocused();
  await expect(cards).toHaveCount(10);
  await expect(pagination.getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  await page.screenshot({ path: info.outputPath("security-pagination.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await next.click();
  await expect(cards).toHaveCount(3);
  await expect(next).toBeDisabled();
  await previous.click();
  await expect(cards.first()).toContainText("Paged finding 10");
  await expect(pagination.getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
  const lastPageResponse = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/repositories/22/findings") && new URL(response.url()).searchParams.get("page") === "3");
  await next.click();
  await (await lastPageResponse).finished();
  await page.clock.runFor(1);
  await expect(pagination.getByRole("status")).toHaveText("Page 3 of 3 · 23 total");
  await expect(pagination).toHaveAttribute("aria-busy", "false");
  total = 2;
  await page.clock.fastForward(5100);
  await expect(cards).toHaveCount(2);
  await expect(cards.first()).toContainText("Paged finding 0");
  await expect(pagination).toHaveCount(0);
  expect(requests.at(-1)).toBe(1);
  await expect(page.getByRole("button", { name: /Load more/ })).toHaveCount(0);
});

for (const area of ["repository", "scan", "assessment"] as const) {
  test(`${area} filters share right-aligned backend ordering and reset the page`, async ({ page }, info) => {
    await mock(page);
    const endpoint = area === "repository" ? "/security/repositories/22/findings" : `/security/scans/scan/${area === "scan" ? "findings" : "assessments"}`;
    const requests: URLSearchParams[] = [];
    await page.route(`**/api/v1${endpoint}?**`, (route) => {
      if (["resolved", "false_positive", "accepted_risk"].includes(new URL(route.request().url()).searchParams.get("state") ?? "")) return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10 } });
      const params = new URL(route.request().url()).searchParams;
      requests.push(params);
      const pageNumber = Number(params.get("page"));
      let rows = Array.from({ length: 23 }, (_, index) => {
        const severity = index === 22 ? "critical" : index === 0 ? "low" : "medium";
        const title = `Backend ordered finding ${index}`;
        return area === "assessment" ? securityAssessment(String(index), { proposal: { title, path: `src/${index}.py`, severity } })
          : { ...finding, id: String(index), finding_id: String(index), title, severity, outcome: "present" };
      });
      if ((params.get("ordering") ?? "-severity") === "-severity") rows = [...rows].reverse();
      return route.fulfill({ json: { items: rows.slice((pageNumber - 1) * 10, pageNumber * 10), total: 23, registered_total: 23,
        page: pageNumber, page_size: 10, revision: "1", outcome_counts: {}, severity_counts: {}, category_counts: {}, kind_counts: {} } });
    });
    await page.goto(area === "repository" ? "/security/repositories/22?ordering=-updated_at" : `/security/scans/scan?tab=${area === "scan" ? "findings&ordering=-updated_at" : "assessment&assessment_ordering=position"}`);
    const panel = page.getByRole("tabpanel").last();
    const label = area === "assessment" ? "assessments" : "open findings";
    await panel.getByRole("button", { name: `Next ${label}`, exact: true }).click();
    await expect(panel.getByRole("navigation").getByRole("status")).toHaveText("Page 2 of 3 · 23 total");
    const field = area === "assessment" ? "Assessment order" : area === "scan" ? "Severity" : "Updated";
    await panel.getByRole("button", { name: `Sort by ${field}`, exact: true }).click();
    if (area === "scan") {
      await expect(page.getByRole("menuitemradio", { name: "Created", exact: true })).toHaveCount(0);
      await expect(page.getByRole("menuitemradio", { name: "Updated", exact: true })).toHaveCount(0);
      await page.getByRole("menuitemradio", { name: "Report order", exact: true }).click();
      await panel.getByRole("button", { name: "Sort by Report order", exact: true }).click();
    }
    await page.getByRole("menuitemradio", { name: "Severity", exact: true }).click();
    await expect.poll(() => requests.at(-1)?.get("ordering") ?? "-severity").toBe("-severity");
    expect(requests.at(-1)?.get("page")).toBe("1");
    await expect(panel.locator('[data-slot="finding-card"]').first()).toContainText("Backend ordered finding 22");
    const sort = panel.getByRole("button", { name: "Sort by Severity", exact: true });
    await expect(sort).toBeFocused();
    const sortBounds = await sort.boundingBox();
    const filterBounds = await panel.getByRole("button", { name: "Filters", exact: true }).boundingBox();
    expect(sortBounds!.x).toBeGreaterThan(filterBounds!.x);
    await panel.getByRole("button", { name: "Sort ascending", exact: true }).click();
    await expect.poll(() => requests.at(-1)?.get("ordering")).toBe("severity");
    await expect(panel.locator('[data-slot="finding-card"]').first()).toContainText("Backend ordered finding 0");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (area === "repository") await page.screenshot({ path: `/private/tmp/code-mortis-ordering-${info.project.name}.png` });
  });
}

for (const scope of ["repository", "scan"] as const) test(`${scope} finding state tabs keep counts, filters and pagination independent`, async ({ page }, info) => {
  await mock(page);
  if (info.project.name === "mobile") await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const stateCounts = { open: 13, accepted_risk: 13, false_positive: 13, resolved: 13 };
  const rows = Object.entries(stateCounts).flatMap(([state, total]) => Array.from({ length: total }, (_, index) => ({
    ...finding, id: `${state}-${index}`, state, title: `${state} defect ${index}`, path: `${state}/${index}.py`, severity: index === 12 ? "critical" : "high",
  })));
  const url = scope === "repository" ? "/security/repositories/22" : "/security/scans/scan";
  await page.route(`**/api/v1${url}`, (route) => route.fulfill({ json: scope === "repository" ? {
    repository, summary, can_write: true, finding_count: rows.length, finding_state_counts: stateCounts, scan_count: 1,
  } : { repository, scan, can_write: true, finding_count: rows.length, finding_state_counts: stateCounts, activity_count: 0, activity: [] } }));
  const requests: URLSearchParams[] = [];
  let failure = 0;
  await page.route(`**/api/v1${url}/findings?**`, (route) => {
    const params = new URL(route.request().url()).searchParams;
    requests.push(params);
    if (failure) return route.fulfill({ status: failure, json: { detail: "Findings unavailable" } });
    const selected = rows.filter((row) => row.state === params.get("state") && (!params.has("severity") || params.getAll("severity").includes(row.severity))
      && (!params.has("kind") || params.getAll("kind").includes(row.kind)) && row.title.includes(params.get("search") ?? ""));
    if ((params.get("ordering") ?? "-severity") === "-severity") selected.sort((a, b) => Number(b.severity === "critical") - Number(a.severity === "critical"));
    else if (params.get("ordering") === "path") selected.sort((a, b) => a.path.localeCompare(b.path));
    const number = Number(params.get("page"));
    return route.fulfill({ json: { items: selected.slice((number - 1) * 10, number * 10), total: selected.length,
      registered_total: rows.length, state_counts: stateCounts, severity_counts: { critical: 4, high: 48 }, kind_counts: { code: 52 }, page: number, page_size: 10 } });
  });
  await page.goto(url);
  const names = ["Findings", "Accepted risk", "False positives", "Resolved"];
  const states = Object.keys(stateCounts);
  const cards = page.locator('[data-slot="finding-card"]');
  const pagination = page.getByRole("navigation", { name: /findings pagination/ }).getByRole("status");
  for (const name of names) await expect(page.getByRole("tab", { name: `${name} 13`, exact: true })).toBeVisible();
  for (const [index, state] of states.entries()) {
    await page.getByRole("tab", { name: `${names[index]} 13`, exact: true }).click();
    await expect(cards).toHaveCount(10);
    await expect(cards.first()).toContainText(`${state} defect 12`);
    await expect(pagination).toHaveText("Page 1 of 2 · 13 total");
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect(page.getByRole("menuitem", { name: "State", exact: true })).toHaveCount(0);
    await page.getByRole("menuitem", { name: "Severity", exact: true }).click();
    await page.getByRole("checkbox", { name: /^High(?: |$)/ }).click();
    await page.getByRole("checkbox", { name: /^Critical(?: |$)/ }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("textbox", { name: /^Search .*findings$/ }).fill(state);
    await expect.poll(() => requests.at(-1)?.get("search")).toBe(state);
    await page.getByRole("button", { name: `Next ${state} findings`, exact: true }).click();
    await expect(pagination).toHaveText("Page 2 of 2 · 13 total");
    await expect(cards).toHaveCount(3);
  }
  for (const [index, state] of states.entries()) {
    await page.getByRole("tab", { name: `${names[index]} 13`, exact: true }).click();
    await expect(pagination).toHaveText("Page 2 of 2 · 13 total");
    await expect(page.getByRole("textbox", { name: /^Search .*findings$/ })).toHaveValue(state);
    await expect.poll(() => requests.at(-1)?.get("state")).toBe(state);
    expect(requests.at(-1)?.has("resolved")).toBe(false);
  }
  await page.getByRole("tab", { name: "False positives 13", exact: true }).click();
  await page.getByRole("button", { name: "Sort by Severity", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "File", exact: true }).click();
  await expect(pagination).toHaveText("Page 1 of 2 · 13 total");
  await page.getByRole("button", { name: "Next false_positive findings", exact: true }).click();
  await expect(pagination).toHaveText("Page 2 of 2 · 13 total");
  await page.reload();
  await expect(page.getByRole("tab", { name: "False positives 13", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(pagination).toHaveText("Page 2 of 2 · 13 total");
  await expect(page.getByRole("button", { name: "Sort by File", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Accepted risk 13", exact: true }).click();
  await expect(pagination).toHaveText("Page 2 of 2 · 13 total");
  await expect(page.getByRole("button", { name: "Sort by Severity", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("tab", { name: "False positives 13", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath(`${scope}-finding-state-tabs.png`), fullPage: true });
  failure = 403;
  await expect(cards).toHaveCount(0, { timeout: 10000 });
});

for (const scope of ["repository"] as const) test(`${scope} triage moves findings between counted state tabs immediately`, async ({ page }) => {
  const writes = await mock(page);
  await page.goto(scope === "repository" ? "/security/repositories/22" : "/security/scans/scan");
  const openName = "Findings";
  let previous = openName;
  for (const [state, name] of [["accepted_risk", "Accepted risk"], ["false_positive", "False positives"], ["open", openName]]) {
    await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
    const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
    await sheet.getByRole("button", { name: "Change status", exact: true }).click();
    await sheet.getByLabel("Decision", { exact: true }).selectOption(state);
    await sheet.getByLabel("Reason", { exact: true }).fill("Reviewed the source evidence and updated this decision.");
    await sheet.getByRole("button", { name: "Save decision", exact: true }).click();
    await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText(state.replaceAll("_", " "));
    await sheet.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.getByRole("tab", { name: `${previous} 0`, exact: true })).toBeVisible();
    await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: `${name} 1`, exact: true }).click();
    await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
    previous = name;
  }
  expect(writes.map((write) => write.body.state)).toEqual(["accepted_risk", "false_positive", "open"]);
});

for (const scope of ["repository", "scan"] as const) test(`${scope} legacy state links select the corresponding tab`, async ({ page }) => {
  await mock(page);
  const url = scope === "repository" ? "/security/repositories/22" : "/security/scans/scan";
  await page.goto(`${url}?state=false_positive&search=guard&false_positive_page=2`);
  const key = "tab";
  await expect(page).toHaveURL((url) => url.searchParams.get(key) === "false_positive" && !url.searchParams.has("state") && url.searchParams.get("false_positive_search") === "guard");
  await expect(page.getByRole("tab", { name: "False positives 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("textbox", { name: "Search false positive findings", exact: true })).toHaveValue("guard");
  await page.goto(`${url}?tab=resolved&resolved_state=open&resolved_search=fix`);
  await expect(page.getByRole("tab", { name: "Resolved 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL((url) => !url.searchParams.has("resolved_state"));
  await expect(page.getByRole("textbox", { name: "Search resolved findings", exact: true })).toHaveValue("fix");
});

test("repositories with only resolved findings open on Findings and link to Resolved", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: {
    repository, summary, can_write: true, finding_count: 1, finding_state_counts: { resolved: 1 }, scan_count: 1,
  } }));
  await page.route("**/api/v1/security/repositories/22/findings?**", (route) => {
    const resolved = new URL(route.request().url()).searchParams.get("state") === "resolved";
    return route.fulfill({ json: { items: resolved ? [{ ...finding, state: "resolved" }] : [], total: resolved ? 1 : 0,
      registered_total: 1, state_counts: { resolved: 1 }, page: 1, page_size: 10 } });
  });
  await page.goto("/security/repositories/22");
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("No open findings", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "View resolved findings", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Resolved 1", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Resolved findings", exact: true }).locator('[data-slot="finding-card"]')).toHaveCount(1);
});

for (const state of ["current", "unavailable"] as const) test(`unrated security badge explains that no grade is available for ${state}`, async ({ page }) => {
  await mock(page, { summary: { state, grade: null } });
  await page.goto("/security/repositories/22");
  await page.getByRole("link", { name: "Security Unrated", exact: true }).focus();
  await expect(page.getByRole("tooltip")).toContainText("No security grade is available.");
  await expect(page.getByLabel("Scan coverage", { exact: true })).toHaveText("Coverage complete");
  await expect(page.getByLabel("Scan freshness", { exact: true })).toHaveText(state === "current" ? "Current commit" : "Freshness unverified");
});


test("scan findings remain historical after triage while the repository updates", async ({ page }) => {
  await mock(page);
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toBeVisible();
  await expect(page.getByRole("tablist")).toHaveCount(1);
  await page.getByRole("button", { name: /Order lookup does not verify ownership/ }).click();
  const sheet = page.getByRole("dialog", { name: "Finding details", exact: true });
  await sheet.getByRole("button", { name: "Change status", exact: true }).click();
  await sheet.getByLabel("Decision", { exact: true }).selectOption("false_positive");
  await sheet.getByLabel("Reason", { exact: true }).fill("Reviewed the saved evidence and current implementation.");
  await sheet.getByRole("button", { name: "Save decision", exact: true }).click();
  await expect(sheet.locator('[data-slot="security-finding-status"]')).toContainText("false positive");
  await sheet.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Findings 1", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "False positives 0", exact: true })).toBeVisible();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
  await page.goto("/security/repositories/22?tab=false_positive");
  await expect(page.getByRole("tab", { name: "Findings 0", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "False positives 1", exact: true })).toBeVisible();
  await expect(page.locator('[data-slot="finding-card"]')).toHaveCount(1);
});

for (const status of ["completed", "partial"] as const) test(`Mortis ${status} security history keeps saved evidence and downloadable reports without new work`, async ({ page }) => {
  const problem = "One source file could not be analyzed within its stage budget.";
  const writes = await mock(page, { canWrite: false, ...(status === "partial" ? { scanError: problem } : {}) });
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, can_write: false, finding_count: 1, finding_state_counts: { open: 1 }, report_count: 1,
    activity_count: 0, activity: [],
    scan: { ...scan, status, grade: status === "completed" ? "D" : null,
      config: { ...scan.config, analysis_engine: "mortis" },
      stages: { ...scan.stages, analysis: { status, completed: 1, total: status === "partial" ? 2 : 1 } },
      report: { commit: sha, status, grade: status === "completed" ? "D" : null,
        problems: status === "partial" ? [problem] : [] } },
  } }));
  await page.goto("/security/scans/scan");
  await expect(page.getByRole("button", { name: "Scan stage details", exact: true })).toHaveAttribute("data-status", status);
  if (status === "partial") await expect(page.getByRole("heading", { name: "Incomplete analysis", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Download report", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "Download Markdown", exact: true })).toHaveAttribute("href", /\/scans\/scan\/report\?format=markdown$/);
  await expect(page.getByRole("menuitem", { name: "Download JSON", exact: true })).toHaveAttribute("href", /\/scans\/scan\/report\?format=json$/);
  await page.keyboard.press("Escape");
  const findings = page.getByRole("region", { name: "Findings recorded in this scan", exact: true });
  await findings.getByRole("button").click();
  const detail = page.getByRole("dialog", { name: "Finding details", exact: true });
  await expect(detail.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(detail.getByText("Constrain the order lookup to the authenticated account.", { exact: true })).toBeVisible();
  await expect(detail.getByRole("region", { name: "Code snippet" })).toContainText("return orders.find(id)");
  expect(writes).toHaveLength(0);
});


for (const analysisEngine of [undefined, "ocr"] as const) test(`historical ${analysisEngine ?? "implicit OCR"} security scan cannot retry inference and can start a new Mortis scan`, async ({ page }, info) => {
  const writes = await mock(page, { scanError: "Saved analysis did not complete." });
  const config = { ...scan.config };
  delete config.analysis_engine;
  if (analysisEngine) config.analysis_engine = analysisEngine;
  await page.route("**/api/v1/security/scans/scan", (route) => route.fulfill({ json: {
    repository, can_write: true, finding_count: 1, finding_state_counts: { open: 1 }, report_count: 1,
    activity_count: 0, activity: [], scan: { ...scan, status: "partial", config },
  } }));
  await page.goto("/security/scans/scan");
  await expect(page.getByText("This historical scan used Open Code Review, which is retired.", { exact: false })).toBeVisible();
  await expect(page.getByText("Open Code Review", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry scan", exact: true }).click();
  const retry = page.getByRole("dialog", { name: "Retry security scan", exact: true });
  await expect(retry).toContainText("This saved scan used a retired engine.");
  await expect(retry).toContainText("This scan used Open Code Review, which is retired.");
  await expect(retry.getByRole("button", { name: "Retry scan", exact: true })).toBeDisabled();
  expect(writes).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("retired-ocr-security-retry.png"), fullPage: true });
  await retry.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Rescan repository", exact: true }).click();
  const next = page.getByRole("dialog", { name: "Run security scan", exact: true });
  await expect(next).toContainText("Scan the full default branch with Mortis and Trivy.");
  await next.getByRole("button", { name: "Run scan", exact: true }).click();
  await expect(page).toHaveURL(/\/security\/scans\/queued$/);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({ path: "/security/repositories/22/scans", body: { connection_id: "connection", model: "test-model" } });
  expect(writes[0].body).not.toHaveProperty("analysis_engine");
});
