import { expect, test, type Page } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import type { Schema } from "../src/api";
import { analyticsCompactNumber, analyticsRange } from "../src/lib/analytics";
import { themes } from "../src/lib/appearance";

const repositories = [{ id: 22, full_name: "acme/code-mortis" }, { id: 23, full_name: "acme/noodle" }];
const scope: Schema["AnalyticsScope"] = { from_at: "2026-09-19T00:00:00Z", to_at: "2026-10-19T00:00:00Z", timezone: "America/Santiago", bucket: "day", repositories };
const total: Schema["AnalyticsUsageTotals"] = { tokens: 1245800, known_reports: 84, missing_reports: 2, complete_runs: 26, total_runs: 28, mean_per_run: 43900, reviewed_pr_samples: 18, mean_per_reviewed_pr: 65120 };
const severities = [{ key: "critical", count: 3 }, { key: "high", count: 8 }, { key: "medium", count: 14 }, { key: "low", count: 6 }];
const categories = [{ key: "bug", count: 14 }, { key: "performance", count: 8 }, { key: "maintainability", count: 5 }, { key: "security", count: 3 }, { key: "other", count: 1 }];
const trend = Array.from({ length: 30 }, (_, index) => ({ date: `2026-09-${String(index + 1).padStart(2, "0")}`, runs: index % 7 === 0 ? 0 : (index * 7) % 9, findings: index % 5, tokens: index === 16 ? null : (index * 1709) % 74000, missing_reports: index === 16 ? 2 : 0 }));
const overview: Schema["AnalyticsOverview"] = { scope, reviewed_prs: 24, findings: 31, resolved: 19, usage: total, trend, severities, categories,
  repositories: [{ id: 22, name: "acme/code-mortis", reviewed_prs: 16, findings: 21, resolved: 14 }, { id: 23, name: "acme/noodle", reviewed_prs: 8, findings: 10, resolved: 5 }] };
const findings: Schema["AnalyticsFindings"] = { scope, total: 31, unfiltered_total: 31, category_options: categories.map((row) => row.key), severities, categories, statuses: [{ key: "open", count: 10 }, { key: "resolved", count: 19 }, { key: "dismissed", count: 2 }], trend };
const reviews: Schema["AnalyticsReviews"] = { scope, runs: 28, reviewed_prs: 24, mean_duration_seconds: 180, median_duration_seconds: 125, duration_samples: 26,
  outcomes: [{ key: "completed", count: 20 }, { key: "partial", count: 4 }, { key: "failed", count: 2 }, { key: "running", count: 2 }],
  coverage: [{ key: "selected", count: 143 }, { key: "completed", count: 110 }, { key: "reused", count: 20 }, { key: "failed", count: 8 }, { key: "waived", count: 5 }], coverage_missing_runs: 2,
  assessments: [{ key: "new", count: 31 }, { key: "repeated", count: 12 }, { key: "rejected", count: 8 }, { key: "withheld", count: 3 }, { key: "pending", count: 2 }], assessment_missing_runs: 1,
  contributors: [{ key: "maria", count: 16 }, { key: "leo", count: 8 }] };
const usage: Schema["AnalyticsUsage"] = { scope, total, trend,
  stages: ["review", "assessment", "verification", "summary"].map((key) => ({ key, repository_id: null, usage: { ...total, tokens: 311450 } })),
  repositories: repositories.map((repo) => ({ key: repo.full_name, repository_id: repo.id, usage: total })),
  models: [{ key: "review-model", repository_id: null, usage: total }], efforts: [{ key: "high", repository_id: null, usage: total }] };
const security: Schema["AnalyticsSecurity"] = { scope, scans: 12, findings: 12, open: 6, resolved: 3,
  trend: trend.map((point, index) => ({ ...point, runs: ({ 1: 2, 10: 5, 20: 5 }[index] ?? 0), findings: ({ 1: 2, 10: 7, 20: 3 }[index] ?? 0) })),
  severities: [{ key: "critical", count: 2 }, { key: "high", count: 5 }, { key: "medium", count: 3 }, { key: "low", count: 2 }],
  kinds: [{ key: "code", count: 5 }, { key: "dependency", count: 4 }, { key: "secret", count: 2 }, { key: "infrastructure", count: 1 }],
  states: [{ key: "open", count: 6 }, { key: "resolved", count: 3 }, { key: "false_positive", count: 1 }, { key: "accepted_risk", count: 2 }],
  outcomes: [{ key: "completed", count: 8 }, { key: "partial", count: 2 }, { key: "failed", count: 1 }, { key: "cancelled", count: 1 }],
};

async function mock(page: Page, override?: (view: string, params: URLSearchParams) => object | undefined) {
  const requests: URL[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    let json: unknown;
    if (path === "/setup/status") json = { configured: true, public_url: "http://localhost" };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", csrf: "test", avatar_url: "" };
    else if (path === "/repositories") json = repositories.map((repo) => ({ ...repo, open_prs: 2, enabled: true, active: true, settings: { background: "", automatic: true, language: "English" } }));
    else if (/^\/repositories\/\d+\/activity$/.test(path)) json = { days: [{ date: "2026-11-20", reviews: 0, scans: 0 }] };
    else if (path.startsWith("/analytics/")) {
      requests.push(url);
      const view = path.split("/").at(-1)!;
      json = override?.(view, url.searchParams) ?? ({ overview, findings, reviews, usage, security }[view]);
    } else json = {};
    await route.fulfill({ json });
  });
  return requests;
}

test("repository Overview shows daily activity before opening Analytics", async ({ page }, info) => {
  const requests = await mock(page, (view, params) =>
    view === "findings" && params.get("status") === "open" ? { ...findings, total: 10 } : undefined);
  await page.route("**/api/v1/repositories/22/pulls?**", (route) => {
    const filters = new URL(route.request().url()).searchParams;
    expect(filters.getAll("state")).toEqual(["open"]);
    expect(filters.getAll("review")).toEqual(["not_reviewed", "outdated"]);
    expect(filters.get("strict_state")).toBe("true");
    return route.fulfill({ json: { total: 12 } });
  });
  await page.route("**/api/v1/security/repositories/22", (route) => route.fulfill({ json: {
    summary: { grade: "D", freshness: "outdated", latest_scan_id: "scan-22", latest_status: "cancelled", latest_at: "2026-11-19T12:00:00Z" },
    finding_state_counts: { open: 7, accepted_risk: 2, resolved: 4 },
    open_finding_severity_counts: { critical: 1, high: 3, low: 3 },
    last_completed_scan_at: "2026-11-17T12:00:00Z",
    finding_count: 13,
  } }));
  const days = Array.from({ length: 365 }, (_, index) => ({
    date: new Date(Date.UTC(2025, 10, 21 + index)).toISOString().slice(0, 10),
    reviews: index === 364 ? 6 : 0,
    scans: index === 364 ? 2 : 0,
  }));
  const recentResults: Schema["RepositoryResult"][] = [
    { kind: "review", at: "2026-11-20T15:00:00Z", run_id: "review-22", title: "Add repository context", pr_number: 148 },
    { kind: "scan", at: "2026-11-20T14:00:00Z", run_id: "scan-22", title: "main", scan_status: "partial" },
    { kind: "scan_fix", at: "2025-11-20T13:55:00Z", run_id: "scan-21", title: "Restrict access to archived records", finding_id: "verified-fix" },
  ];
  let activityReads = 0;
  await page.route("**/api/v1/repositories/22/activity?**", async (route) => {
    expect(new URL(route.request().url()).searchParams.get("timezone")).toBeTruthy();
    activityReads++;
    await route.fulfill({ json: { days, recent_results: recentResults } });
  });
  await page.goto("/repositories/22/overview");
  await expect(page.getByRole("heading", { name: "Overview", level: 1, exact: true })).toBeVisible();
  const activity = page.getByRole("region", { name: "Repository activity" });
  await expect(activity).toContainText("8 total runs");
  await expect(activity).toContainText("6 review runs");
  await expect(activity).toContainText("2 security scans");
  const reviewCard = page.getByRole("region", { name: "Reviews", exact: true });
  const securityCard = page.getByRole("region", { name: "Security", exact: true });
  const enabledBadge = reviewCard.getByText("Enabled", { exact: true });
  const gradeBadge = securityCard.getByText("Grade D · Outdated", { exact: true });
  await expect(reviewCard).toContainText(/10\s*open findings/);
  for (const badge of [enabledBadge, gradeBadge]) {
    await expect(badge).toBeVisible();
    await expect(badge).toHaveAttribute("data-variant", "default");
    await expect(badge).toHaveCSS("border-radius", "5px");
    await expect(badge).toHaveCSS("padding", "3px 8px");
    await expect(badge).toHaveCSS("line-height", "17px");
    await expect(badge).toHaveCSS("font-weight", "600");
  }
  await expect(reviewCard).toContainText(/Automatic reviews\s*On/);
  await expect(reviewCard.getByText("Review language", { exact: true })).toHaveCount(0);
  await expect(reviewCard.getByRole("link", { name: "12 PRs needing review", exact: true })).toHaveAttribute("href", "/repositories/22?state=open&review=not_reviewed&review=outdated&strict_state=true");
  await expect(reviewCard.getByRole("link", { name: "View reviews" })).toHaveAttribute("href", "/repositories/22");
  await expect(securityCard).toContainText(/7\s*open findings/);
  await expect(securityCard.getByRole("link", { name: "1 open critical finding", exact: true })).toHaveAttribute("href", "/security/repositories/22?severity=critical");
  await expect(securityCard.getByRole("link", { name: "3 open high findings", exact: true })).toHaveAttribute("href", "/security/repositories/22?severity=high");
  await expect(securityCard.getByText("Latest completed scan", { exact: true })).toBeVisible();
  await expect(securityCard.locator("time")).toHaveAttribute("datetime", "2026-11-17T12:00:00Z");
  await expect(securityCard.getByText("Scan status", { exact: true })).toHaveCount(0);
  await expect(securityCard.getByRole("link", { name: "View security" })).toHaveAttribute("href", "/security/repositories/22");
  const [chartBox, reviewBox, securityBox] = await Promise.all([activity.boundingBox(), reviewCard.boundingBox(), securityCard.boundingBox()]);
  expect(chartBox!.y).toBeGreaterThan(Math.max(reviewBox!.y + reviewBox!.height, securityBox!.y + securityBox!.height));
  const results = page.getByRole("region", { name: "Recent results", exact: true });
  await expect(results.getByRole("listitem")).toHaveCount(3);
  expect((await results.boundingBox())!.y).toBeGreaterThan(chartBox!.y + chartBox!.height);
  const resultLinks = results.getByRole("link");
  await expect(resultLinks.nth(0)).toHaveAccessibleName(/Review completed.*PR #148.*Add repository context/);
  await expect(resultLinks.nth(0)).toHaveAttribute("href", "/runs/review-22");
  await expect(resultLinks.nth(1)).toHaveAccessibleName(/Partial security scan.*main/);
  await expect(resultLinks.nth(1)).toHaveAttribute("href", "/security/scans/scan-22");
  await expect(resultLinks.nth(2)).toHaveAccessibleName(/Fix verified.*Restrict access to archived records/);
  await expect(resultLinks.nth(2)).toHaveAttribute("href", "/security/scans/scan-21?tab=resolved&finding=verified-fix");
  await expect(resultLinks.nth(2).locator("time")).toHaveAttribute("datetime", recentResults[2].at);
  await expect(resultLinks.nth(2).locator("time")).toHaveText(/\b2025\b/);
  if (info.project.name === "mobile") {
    expect(securityBox!.y).toBeGreaterThan(reviewBox!.y);
  } else {
    expect(securityBox!.y).toBe(reviewBox!.y);
    expect(securityBox!.x).toBeGreaterThan(reviewBox!.x);
  }
  const calendar = activity.getByRole("group", { name: /Daily activity/ });
  await expect(calendar.getByRole("button")).toHaveCount(365);
  await expect(calendar.locator('[tabindex="0"]')).toHaveCount(1);
  const today = calendar.getByRole("button", { name: /8 total, 6 review runs, 2 security scans/ });
  const primaryColor = await today.evaluate((cell) => getComputedStyle(cell).backgroundColor);
  await expect(reviewCard.getByRole("link", { name: "View reviews" })).toHaveCSS("color", primaryColor);
  await expect(securityCard.getByRole("link", { name: "View security" })).toHaveCSS("color", primaryColor);
  if (info.project.name === "mobile") {
    await today.scrollIntoViewIfNeeded();
    await expect(today).toBeInViewport({ ratio: 1 });
    await today.tap();
  } else await today.hover();
  const tooltip = activity.getByRole("tooltip");
  await expect(tooltip).toContainText("Nov 20, 2026 · 8 runs");
  await expect(tooltip.getByText("6", { exact: true })).toBeVisible();
  await expect(tooltip.getByText("2", { exact: true })).toBeVisible();
  if (info.project.name === "desktop") {
    await calendar.getByRole("button", { name: /^Nov 19, 2026/ }).hover({ position: { x: 6, y: 2 } });
    await expect(tooltip).toContainText("Nov 19, 2026 · 0 runs");
    await today.hover();
    await expect(tooltip).toContainText("Nov 20, 2026 · 8 runs");
    await today.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(tooltip).toContainText("Nov 13, 2026 · 0 runs");
    await page.keyboard.press("Escape");
    await page.getByRole("heading", { name: "Overview", exact: true }).hover();
  } else {
    await page.getByRole("heading", { name: "Overview", exact: true }).tap();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  }
  await expect(tooltip).toHaveCount(0);
  expect(activityReads).toBe(1);
  await expect(page.getByRole("group", { name: "Analytics filters", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tabpanel")).toHaveCount(0);
  expect(requests.map((url) => Object.fromEntries(url.searchParams))).toEqual([{ repo: "22", all_time: "true", status: "open" }]);
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Analytics", exact: true }).click();
  await expect(page).toHaveURL("/repositories/22/analytics");
  await expect(page.getByRole("heading", { name: "Analytics", level: 1, exact: true })).toBeVisible();
  await expect.poll(() => requests.some((url) => url.pathname.endsWith("/analytics/overview"))).toBe(true);
  expect(requests.every((url) => url.searchParams.get("repo") === "22")).toBe(true);
});

test("repository cards distinguish an unscanned repository from an unavailable count", async ({ page }) => {
  await mock(page);
  await page.route("**/api/v1/analytics/findings?**", (route) => route.fulfill({ status: 503, json: {} }));
  let countsAvailable = true;
  await page.route("**/api/v1/repositories/23/pulls?**", (route) => countsAvailable
    ? route.fulfill({ json: { total: 0 } }) : route.fulfill({ status: 403, json: {} }));
  await page.route("**/api/v1/security/repositories/23", (route) => route.fulfill({ json: {
    summary: { state: "not_scanned" }, finding_state_counts: {},
    open_finding_severity_counts: countsAvailable ? {} : undefined,
    last_completed_scan_at: countsAvailable ? null : undefined,
  } }));
  await page.goto("/repositories/23/overview");
  const reviewCard = page.getByRole("region", { name: "Reviews", exact: true });
  await expect(reviewCard).toContainText("Finding count unavailable");
  await expect(reviewCard.getByRole("link", { name: "0 PRs needing review", exact: true })).toBeVisible();
  const securityCard = page.getByRole("region", { name: "Security", exact: true });
  await expect(securityCard).toContainText("Unscanned");
  await expect(securityCard).toContainText(/0\s*open findings/);
  await expect(securityCard).toContainText(/Latest completed scan\s*Never/);
  await expect(securityCard.getByRole("link", { name: "0 open critical findings", exact: true })).toBeVisible();
  await expect(securityCard.getByRole("link", { name: "0 open high findings", exact: true })).toBeVisible();
  countsAvailable = false;
  await page.reload();
  await expect(reviewCard).toContainText(/PRs needing review\s*Unavailable/);
  await expect(securityCard).toContainText(/Priority findings\s*Unavailable/);
  await expect(securityCard).toContainText(/Latest completed scan\s*Unavailable/);
  await expect(reviewCard.getByRole("link", { name: /needing review/ })).toHaveCount(0);
  await expect(securityCard.getByRole("link", { name: /open (critical|high) findings/ })).toHaveCount(0);
});

test("repository activity keeps load errors separate from empty history", async ({ page }) => {
  await mock(page);
  let failed = false;
  await page.route("**/api/v1/repositories/23/activity?**", async (route) => {
    if (!failed) {
      failed = true;
      await route.fulfill({ status: 503, json: { detail: "Temporarily unavailable." } });
      return;
    }
    const days = Array.from({ length: 365 }, (_, index) => ({
      date: new Date(Date.UTC(2025, 10, 21 + index)).toISOString().slice(0, 10), reviews: 0, scans: 0,
    }));
    await route.fulfill({ json: { days } });
  });
  await page.goto("/repositories/23/overview");
  await expect(page.getByRole("alert")).toContainText("Could not load repository activity");
  await expect(page.getByText("No review runs or security scans in this period.")).toHaveCount(0);
  await page.getByRole("button", { name: "Retry activity" }).click();
  await expect(page.getByText("No review runs or security scans in this period.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Recent results", exact: true })).toHaveCount(0);
});

test("legacy repository Overview analytics filters open Analytics", async ({ page }) => {
  await mock(page);
  await page.goto("/repositories/23/overview?period=90&tab=usage");
  await expect(page).toHaveURL("/repositories/23/analytics?period=90&tab=usage");
  await expect(page.getByRole("heading", { name: "Usage by model", exact: true })).toBeVisible();
});

test("date presets and custom ranges preserve local calendar boundaries", () => {
  const today = new Date(2026, 9, 18, 12);
  for (const days of [7, 30, 90]) {
    const range = analyticsRange(new URLSearchParams(`period=${days}&repo=22`), today);
    expect(range.error).toBeUndefined();
    const start = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1 - days);
    expect(range.query?.get("from_at")).toBe(start.toISOString());
    expect(range.query?.get("repo")).toBe("22");
  }
  expect(analyticsRange(new URLSearchParams("period=custom&start=2026-02-30&end=2026-03-02"), today).error).toBeTruthy();
  expect(analyticsRange(new URLSearchParams("period=custom&start=2026-10-19&end=2026-10-18"), today).error).toBeTruthy();
  expect(analyticsRange(new URLSearchParams("period=all"), today).query?.get("from_at")).toBeNull();
  const defaults = analyticsRange(new URLSearchParams(), today);
  expect(defaults.period).toBe("30");
  expect(defaults.start).toBe("2026-09-19");
  expect(defaults.end).toBe("2026-10-18");
  expect(analyticsRange(new URLSearchParams("period=invalid"), today)).toEqual(defaults);
});

test("date range arrow opens and closes choices without typing", async ({ page }, info) => {
  await mock(page);
  await page.goto("/repositories/22/analytics");
  await expect(page.getByRole("heading", { name: "Analytics", level: 1, exact: true })).toBeVisible();
  const period = page.getByRole("combobox", { name: "Date range", exact: true });
  const trigger = page.getByRole("group", { name: "Analytics filters", exact: true }).locator('button[aria-label="Show options"]');
  const toggle = () => info.project.name === "mobile" ? trigger.tap() : trigger.click();
  await toggle();
  await expect(page.getByRole("option", { name: "Last 7 days", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "All time", exact: true })).toBeVisible();
  await expect(period).toHaveAttribute("aria-expanded", "true");
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(period).toBeFocused();
  await page.screenshot({ path: `../.impeccable/review/combobox-arrow-${info.project.name}.png`, animations: "disabled" });
  await toggle();
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(period).toHaveValue("Last 30 days");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.press("Enter");
  await page.getByRole("option", { name: "Last 7 days", exact: true }).click();
  await expect(period).toHaveValue("Last 7 days");
  await expect(page).toHaveURL(/period=7/);
  await trigger.press("Space");
  await expect(page.getByRole("option", { name: "All time", exact: true })).toBeVisible();
  await period.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(period).toBeFocused();
});

test("last 30 days rolls forward by default while explicit custom dates stay fixed", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-18T12:00:00Z"));
  const requests = await mock(page);
  await page.goto("/repositories/22/analytics");
  await expect(page.getByRole("combobox", { name: "Date range", exact: true })).toHaveValue("Last 30 days");
  await expect(page.getByRole("button", { name: "From", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Through", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("from_at")).toContain("2026-09-19");
  expect(requests.at(-1)?.searchParams.get("to_at")).toContain("2026-10-19");
  await page.clock.setFixedTime(new Date("2026-10-19T12:00:00Z"));
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Date range", exact: true })).toHaveValue("Last 30 days");
  await expect.poll(() => requests.at(-1)?.searchParams.get("from_at")).toContain("2026-09-20");
  expect(requests.at(-1)?.searchParams.get("to_at")).toContain("2026-10-20");
  await expect(page).not.toHaveURL(/start=|end=/);
  await page.getByRole("combobox", { name: "Date range", exact: true }).fill("Custom");
  await page.getByRole("option", { name: "Custom dates", exact: true }).click();
  await expect(page).toHaveURL(/period=custom&start=2026-09-20&end=2026-10-19/);
  await expect(page.getByRole("button", { name: "From", exact: true })).toHaveAccessibleDescription("Sep 20, 2026");
  await expect(page.getByRole("button", { name: "Through", exact: true })).toHaveAccessibleDescription("Oct 19, 2026");
  await page.clock.setFixedTime(new Date("2026-10-20T12:00:00Z"));
  await page.reload();
  await expect(page.getByRole("combobox", { name: "Date range", exact: true })).toHaveValue("Custom dates");
  await expect(page).toHaveURL(/start=2026-09-20&end=2026-10-19/);
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("to_at")).toContain("2026-10-20");
});

test("compact numbers preserve zero, missing values and magnitude boundaries", () => {
  for (const [value, expected] of [[0, "0"], [999, "999"], [1000, "1k"], [1245, "1.2k"], [999999, "1M"], [441757065, "441.8M"], [1200000000, "1.2B"], [null, "Unavailable"], [undefined, "Unavailable"]] as const) {
    expect(analyticsCompactNumber(value)).toBe(expected);
  }
});

test("compact metrics and table values expose full numbers on hover, focus and tap", async ({ page }, info) => {
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1292, height: 889 });
  await mock(page, (view) => ({
    overview: { ...overview, usage: { ...total, tokens: 441757065 }, severities: [{ key: "critical", count: 1200 }] },
    usage: { ...usage, trend: [trend[1], trend[16]] },
    security: { ...security, scans: 1200 },
  }[view]));
  await page.goto("/repositories/22/analytics");
  const metric = page.locator("dl").getByRole("button", { name: "441,757,065", exact: true });
  await expect(metric).toHaveText("441.8M");
  if (info.project.name === "mobile") await metric.tap();
  else await metric.hover();
  await expect(page.getByRole("tooltip")).toHaveText("441,757,065");
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/analytics-compact-${info.project.name}.png`, animations: "disabled" });
  if (info.project.name === "mobile") await page.locator("dl dt").filter({ hasText: "PRs reviewed" }).tap();
  else await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await page.getByRole("tab", { name: "Overview", exact: true }).focus();
  await metric.focus();
  await expect(page.getByRole("tooltip")).toHaveText("441,757,065");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Critical: 1,200 findings", exact: true })).toContainText("1.2k");
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(page.locator("#analytics-panel dl dd")).toHaveText(["1.2M", "26 / 28", "43.9k", "65.1k"]);
  const modelValue = page.getByRole("table", { name: "Usage by model", exact: true }).getByRole("button", { name: "1,245,800", exact: true });
  await expect(modelValue).toHaveText("1.2M");
  await modelValue.focus();
  await expect(page.getByRole("tooltip")).toHaveText("1,245,800");
  const usagePanel = page.getByRole("region", { name: "Token usage", exact: true });
  const usageChart = usagePanel.getByRole("application");
  await page.mouse.move(0, 0);
  await usageChart.focus();
  await expect(usagePanel.getByRole("status")).toContainText("1,709");
  await usageChart.press("ArrowRight");
  await expect(usagePanel.getByRole("status")).toContainText("Missing reports: 2");
  await page.getByRole("tab", { name: "Security", exact: true }).click();
  await expect(page.locator("dl").getByRole("button", { name: "1,200", exact: true })).toHaveText("1.2k");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("overview loads only its endpoint and shows charts without redundant tables", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/22/analytics");
  await expect(page.getByRole("heading", { name: "Analytics", level: 1, exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Categories", exact: true, level: 2 })).toBeVisible();
  await expect(page.getByRole("region", { name: "Repository", exact: true })).toHaveCount(0);
  await expect(page.locator("#analytics-panel dd").filter({ hasText: "61% of findings detected" })).toContainText("19");
  expect(requests.every((url) => url.pathname.endsWith("/overview"))).toBe(true);
  await expect(page.getByRole("region", { name: "Review activity", exact: true }).getByRole("application")).toBeVisible();
  await expect(page.getByRole("tabpanel").getByRole("table")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("Page not found");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("one Analytics CSV includes all tabs in the current repository and date range", async ({ page }) => {
  const requests = await mock(page, (view) => {
    if (view === "findings") return { ...findings, total: 8 };
    if (view === "usage") return { ...usage, repositories: usage.repositories.filter((repo) => repo.repository_id === 22) };
    if (view === "overview") return {
      ...overview,
      repositories: overview.repositories.filter((repo) => repo.id === 22),
      categories: [
        ...Array.from({ length: 10 }, (_, index) => ({ key: `category-${index}`, count: index + 1 })),
        { key: 'résultat, "quoted"\r\nsecond line', count: 2 },
        { key: "  =1+1", count: 1 },
      ],
    };
  });
  await page.goto("/repositories/22/analytics?tab=reviews&period=custom&start=2026-09-19&end=2026-10-18&repo=23&severity=high&category=bug&status=open");
  await expect(page.getByRole("heading", { name: "Review outcomes", exact: true })).toBeVisible();
  expect(requests.map((request) => request.pathname.split("/").at(-1))).toEqual(["reviews"]);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toBe("analytics-acme-code-mortis-2026-09-19-to-2026-10-18.csv");
  const csv = await readFile(await file.path(), "utf8");
  expect(csv).toMatch(/^\uFEFFrepository,repository_id,from_at,to_at_exclusive,timezone,bucket,tab,section,item,date,metric,value\r\n/);
  expect(csv).toContain('"acme/code-mortis",22,"2026-09-19T00:00:00Z","2026-10-19T00:00:00Z","America/Santiago","day"');
  expect(csv).toContain('"overview","activity","","2026-09-02","review_runs",7\r\n');
  expect(csv).toContain('"overview","categories","résultat, ""quoted""\r\nsecond line","","count",2\r\n');
  expect(csv).toContain(`"overview","categories","'  =1+1","","count",1\r\n`);
  expect(csv).toContain('"findings","filters","","","severity","high"\r\n');
  expect(csv).toContain('"findings","summary","","","total",8\r\n');
  expect(csv).toContain('"reviews","summary","","","contributor_count",2\r\n');
  expect(csv).toContain('"reviews","coverage","completed","","count",110\r\n');
  expect(csv).toContain('"usage","activity","","2026-09-17","tokens",\r\n');
  expect(csv).toContain('"usage","activity","","2026-09-17","missing_reports",2\r\n');
  expect(csv).toContain('"usage","by_model","review-model","","tokens",1245800\r\n');
  expect(csv).toContain('"security","states","accepted_risk","","count",2\r\n');
  expect(csv).not.toContain("acme/noodle");
  const exportRequests = requests.slice(1);
  expect(exportRequests.map((request) => request.pathname.split("/").at(-1)).sort()).toEqual(["findings", "overview", "reviews", "security", "usage"]);
  for (const { pathname, searchParams } of exportRequests) {
    expect(searchParams.getAll("repo")).toEqual(["22"]);
    for (const name of ["from_at", "to_at", "timezone"]) {
      expect(searchParams.get(name)).toBeTruthy();
      expect(searchParams.get(name)).toBe(requests[0].searchParams.get(name));
    }
    const findingsOnly = pathname.endsWith("/findings");
    expect(searchParams.get("severity")).toBe(findingsOnly ? "high" : null);
    expect(searchParams.get("category")).toBe(findingsOnly ? "bug" : null);
    expect(searchParams.get("status")).toBe(findingsOnly ? "open" : null);
  }
  await expect(page.getByRole("tab", { name: "Reviews", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("Analytics CSV waits for all sections and can be retried after a failure", async ({ page }) => {
  await mock(page);
  let attempts = 0;
  let failExport!: () => void;
  const pending = new Promise<void>((resolve) => { failExport = resolve; });
  await page.route("**/api/v1/analytics/security?**", async (route) => {
    if (++attempts > 1) return route.fulfill({ json: security });
    await pending;
    return route.fulfill({ status: 503, json: { detail: "Security analytics is temporarily unavailable." } });
  });
  const downloads: string[] = [];
  page.on("download", (file) => downloads.push(file.suggestedFilename()));
  await page.goto("/repositories/22/analytics?period=all");
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  const button = page.getByRole("button", { name: "Download", exact: true });
  await button.click();
  try {
    await expect(button).toBeDisabled();
    await expect(page.getByRole("group", { name: "Analytics filters" }).getByRole("status")).toHaveText("Preparing Analytics CSV");
  } finally {
    failExport();
  }
  await expect(page.getByRole("alert")).toContainText("Security analytics is temporarily unavailable.");
  expect(downloads).toEqual([]);
  const downloaded = page.waitForEvent("download");
  await button.click();
  const file = await downloaded;
  expect(file.suggestedFilename()).toMatch(/^analytics-acme-code-mortis-all-time-through-\d{4}-\d{2}-\d{2}\.csv$/);
  expect(downloads).toHaveLength(1);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(button).toBeEnabled();
});

test("repository overview locks scope across tabs, URL filters, reload and retries", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/23/analytics?repo=22&repo=999");
  await expect(page.getByRole("heading", { name: "Analytics", exact: true, level: 1 })).toBeVisible();
  await expect(page.getByRole("group", { name: "Analytics filters", exact: true }).getByRole("button", { name: "Filters", exact: true })).toHaveCount(0);
  await page.getByRole("combobox", { name: "Date range", exact: true }).fill("90");
  await page.getByRole("option", { name: "Last 90 days", exact: true }).click();
  for (const name of ["Findings", "Reviews", "Usage", "Security"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("tabpanel")).toBeVisible();
  }
  await page.reload();
  await expect(page.getByRole("tab", { name: "Security", exact: true })).toHaveAttribute("aria-selected", "true");
  const failedRequests: URL[] = [];
  await page.route("**/api/v1/analytics/security?**", (route) => {
    failedRequests.push(new URL(route.request().url()));
    return route.fulfill({ status: 503, json: { detail: "Temporary failure" } });
  });
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("button", { name: "Clear repository selection" })).toHaveCount(0);
  await page.getByRole("button", { name: "Retry analytics", exact: true }).click();
  await expect.poll(() => failedRequests.length).toBe(2);
  expect(failedRequests.every((url) => url.searchParams.getAll("repo").join() === "23")).toBe(true);
  expect(requests.length).toBeGreaterThan(4);
  expect(requests.every((url) => url.searchParams.getAll("repo").join() === "23")).toBe(true);
  await page.goBack();
  await expect(page).toHaveURL(/period=90/);
});

test("severity controls open a filtered Findings tab and filters clear", async ({ page }) => {
  const requests = await mock(page, (view, params) => view === "findings" && params.has("severity") ? {
    ...findings, total: 3, statuses: [{ key: "open", count: 1 }, { key: "resolved", count: 2 }, { key: "dismissed", count: 0 }],
  } : undefined);
  await page.goto("/repositories/22/analytics");
  await page.getByRole("button", { name: "Critical: 3 findings", exact: true }).click();
  await expect(page).toHaveURL(/tab=findings/);
  await expect(page).toHaveURL(/severity=critical/);
  await expect(page.locator("#analytics-panel dl > div > dd:first-of-type")).toHaveText(["3", "1", "2", "0"]);
  expect(requests.at(-1)?.searchParams.get("severity")).toBe("critical");
  await expect(page.getByRole("button", { name: "Edit Severity: Critical", exact: true })).toBeVisible();
  const filters = page.getByRole("group", { name: "Finding filters", exact: true });
  await filters.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Category", exact: true }).click();
  await page.getByRole("textbox", { name: "Search category options", exact: true }).fill("performance");
  await page.getByRole("checkbox", { name: "Performance", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Performance", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/category=performance/);
  await page.getByRole("button", { name: "Remove Category filter", exact: true }).click();
  await expect(page).not.toHaveURL(/category=/);
  await expect(filters.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Clear finding filters", exact: true }).click();
  await expect(page).not.toHaveURL(/severity=/);
  await expect(page.locator("#analytics-panel dl > div > dd:first-of-type")).toHaveText(["31", "10", "19", "2"]);
});

test("custom dates validate before requesting and keyboard switches tabs", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-10-18T12:00:00Z"));
  const requests = await mock(page);
  await page.goto("/repositories/22/analytics?period=custom&start=2026-02-30&end=bad");
  await expect(page.getByRole("alert")).toContainText("Choose a valid");
  await expect(page.getByRole("button", { name: "Download", exact: true })).toBeDisabled();
  expect(requests).toHaveLength(0);
  await page.getByRole("button", { name: "From", exact: true }).click();
  await page.getByRole("button", { name: /October 1st, 2026/ }).click();
  await expect(page.getByRole("button", { name: "From", exact: true })).toBeFocused();
  expect(requests).toHaveLength(0);
  await page.getByRole("button", { name: "Through", exact: true }).press("Enter");
  await expect(page.getByRole("button", { name: /October 19th, 2026/ })).toBeDisabled();
  await page.getByRole("button", { name: /October 18th, 2026/ }).press("Enter");
  await expect(page.getByRole("button", { name: "Through", exact: true })).toBeFocused();
  await expect(page).toHaveURL(/start=2026-10-01&end=2026-10-18/);
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Findings", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(requests.at(-1)?.searchParams.get("to_at")).toContain("2026-10-19");
});

for (const tab of ["usage", "security"]) test(`permission loss hides cached ${tab} analytics and offers recovery`, async ({ page }) => {
  await mock(page);
  const heading = tab === "usage" ? "Usage by model" : "Security activity";
  await page.goto(`/repositories/22/analytics?tab=${tab}`);
  await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  await page.route(`**/api/v1/analytics/${tab}?**`, (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access was revoked");
  await expect(page.getByRole("heading", { name: heading, exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry analytics", exact: true })).toBeVisible();
});

test("security analytics preserves filters and exposes accessible scan data", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/repositories/23/analytics?tab=security&period=90&repo=23&severity=critical&status=dismissed");
  const tab = page.getByRole("tab", { name: "Security", exact: true });
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#analytics-panel dl dd")).toHaveText(["12", "12", "6", "3"]);
  expect(requests.every((url) => url.pathname.endsWith("/security"))).toBe(true);
  expect(requests.at(-1)?.searchParams.get("repo")).toBe("23");
  expect(requests.at(-1)?.searchParams.has("severity")).toBe(false);
  expect(requests.at(-1)?.searchParams.has("status")).toBe(false);
  const scanPanel = page.getByRole("region", { name: "Security activity", exact: true });
  await scanPanel.getByRole("application").focus();
  await expect(scanPanel.getByRole("status")).toContainText("Security scans");
  await expect(scanPanel.getByRole("status")).toContainText("Findings detected");
  await expect(page.getByRole("region", { name: "Current resolution", exact: true }).getByRole("application")).toContainText("Accepted risk");
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Usage", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowRight");
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=security/);
  await page.reload();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/period=90&repo=23/);
  await page.getByText("About these metrics", { exact: true }).click();
  await expect(page.getByText(/not the entire backlog/)).toBeVisible();
});

test("security distinguishes empty history from scans without findings", async ({ page }) => {
  let scans = 0;
  await mock(page, (view) => view === "security" ? { ...security, scans, findings: 0, open: 0, resolved: 0,
    trend: [], severities: [], kinds: [], states: [], outcomes: scans ? [{ key: "failed", count: 1 }] : [],
  } : undefined);
  await page.goto("/repositories/22/analytics?tab=security&period=7");
  await expect(page.getByText("No security activity in this period", { exact: true })).toBeVisible();
  scans = 1;
  await page.getByRole("button", { name: "View all time", exact: true }).click();
  await expect(page).toHaveURL(/period=all/);
  await expect(page.locator("#analytics-panel dl dd")).toHaveText(["1", "0", "0", "0"]);
  await expect(page.getByRole("heading", { name: "Scan outcomes", exact: true })).toBeVisible();
  await expect(page.getByText(/Partial, failed, and unscanned work/)).toBeVisible();
});

test("date controls align, wrap and show themed calendar popovers", async ({ page }, info) => {
  if (info.project.name === "desktop") await page.setViewportSize({ width: 1581, height: 938 });
  await page.clock.setFixedTime(new Date("2026-10-18T12:00:00Z"));
  await mock(page);
  await page.goto("/repositories/22/analytics?period=custom&start=2026-09-19&end=2026-10-18");
  await mkdir("../.impeccable/review", { recursive: true });
  for (const mode of ["light", "dark"]) {
    await page.evaluate((mode) => localStorage.setItem("theme", mode), mode);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
    await expect(page.getByText("Review impact and model usage across your repositories.")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "From", exact: true })).toHaveAccessibleDescription("Sep 19, 2026");
    const download = await page.getByRole("button", { name: "Download", exact: true }).boundingBox();
    const refresh = await page.getByRole("button", { name: "Refresh", exact: true }).boundingBox();
    expect(Math.abs(download!.y - refresh!.y)).toBeLessThan(1);
    expect(download!.x + download!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    if (info.project.name === "desktop") {
      const from = await page.getByRole("button", { name: "From", exact: true }).boundingBox();
      const title = await page.getByRole("heading", { level: 1, name: "Analytics" }).boundingBox();
      expect(Math.abs(refresh!.y - from!.y)).toBeLessThan(1);
      expect(Math.abs((refresh!.y + refresh!.height / 2) - (title!.y + title!.height / 2))).toBeLessThan(2);
    }
    await page.screenshot({ path: `../.impeccable/review/analytics-filters-${info.project.name}-${mode}.png`, animations: "disabled" });
    await page.getByRole("button", { name: "From", exact: true }).click();
    const calendar = page.getByRole("dialog", { name: "From date", exact: true });
    await expect(calendar.getByRole("button", { name: /September 19th, 2026, selected/ })).toBeFocused();
    const bounds = await calendar.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.impeccable/review/analytics-calendar-${info.project.name}-${mode}.png`, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "From", exact: true })).toBeFocused();
  }
});

test("empty activity, filtered findings and unknown tokens remain distinct", async ({ page }) => {
  await mock(page, (view) => view === "overview" ? { ...overview, reviewed_prs: 0, findings: 0, trend: [] } : view === "findings" ? { ...findings, total: 0, statuses: findings.statuses.map((row) => ({ ...row, count: 0 })) } : view === "usage" ? { ...usage, total: { ...total, tokens: null, mean_per_run: null, mean_per_reviewed_pr: null } } : undefined);
  await page.goto("/repositories/22/analytics");
  await expect(page.getByText("No review activity in this period", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings", exact: true }).click();
  await expect(page.getByText("No findings match these filters", { exact: true })).toBeVisible();
  await expect(page.locator("#analytics-panel dl > div > dd:first-of-type")).toHaveText(["0", "0", "0", "0"]);
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(page.locator("dl").getByText("Unavailable", { exact: true })).toHaveCount(3);
});

test("charts follow every palette and remain keyboard accessible", async ({ page }) => {
  await mock(page);
  await page.goto("/repositories/22/analytics");
  for (const theme of themes) for (const mode of ["light", "dark"]) {
    await page.evaluate(({ theme, mode }) => {
      localStorage.setItem("color-theme", theme);
      localStorage.setItem("theme", mode);
    }, { theme: theme.id, mode });
    await page.reload();
    await expect(page.locator("[data-slot=chart]").first()).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-color-theme", theme.id);
    const tokens = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return [style.getPropertyValue("--chart-1").trim(), style.getPropertyValue("--primary").trim()];
    });
    expect(tokens[0]).toBe(tokens[1]);
    const muted = theme[mode as "light" | "dark"]["--muted-foreground"];
    const rgb = `rgb(${[1, 3, 5].map((start) => parseInt(muted.slice(start, start + 2), 16)).join(", ")})`;
    await expect(page.locator(".recharts-cartesian-axis-tick-value").first()).toHaveCSS("fill", rgb);
  }
  const severityPanel = page.getByRole("region", { name: "Severity", exact: true });
  await severityPanel.getByRole("application").focus();
  await expect(severityPanel.getByRole("status")).toContainText("Critical");
});

test("capture Analytics in each tab and theme with long labels", async ({ page }, info) => {
  await page.clock.setFixedTime(new Date("2026-09-30T12:00:00Z"));
  await mock(page, (view) => view === "usage" ? { ...usage, models: [{ key: "provider/a-very-long-model-identifier-that-must-wrap-without-widening-the-workspace", repository_id: null, usage: total }] } : undefined);
  await page.goto("/repositories/22/analytics");
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  await mkdir("../.impeccable/review", { recursive: true });
  for (const [tab, lead, supporting] of [
    ["Overview", "Review activity", "Severity"],
    ["Findings", "Finding trends", "Current resolution"],
    ["Reviews", "Review outcomes", "File review results"],
    ["Usage", "Token usage", "Usage by stage"],
    ["Security", "Security activity", "Severity"],
  ]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator("#analytics-panel > p, #analytics-panel section > div > details")).toHaveCount(0);
    await expect(page.locator("[data-slot=chart]").first()).toBeVisible();
    await expect(page.locator("#analytics-panel dl dt")).toHaveCount(4);
    await expect(page.locator("#analytics-panel dl dd")).toHaveCount(4);
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await expect.poll(() => page.locator("[data-slot=chart]").evaluateAll((charts) => charts.every((chart) => {
      const svg = chart.querySelector("svg.recharts-surface");
      return svg && Math.abs(svg.getBoundingClientRect().width - chart.getBoundingClientRect().width) < 2;
    }))).toBe(true);
    const leadPanel = page.getByRole("heading", { name: lead, exact: true, level: 2 }).locator("..");
    const supportingPanel = page.getByRole("heading", { name: supporting, exact: true, level: 2 }).locator("..");
    const [leadBox, supportingBox] = await Promise.all([leadPanel.boundingBox(), supportingPanel.boundingBox()]);
    const supportingBackground = await supportingPanel.evaluate((panel) => getComputedStyle(panel).backgroundColor);
    await expect(leadPanel).not.toHaveCSS("background-color", supportingBackground);
    if (info.project.name === "desktop") {
      expect(Math.abs(leadBox!.y - supportingBox!.y)).toBeLessThan(1);
      expect(leadBox!.width).toBeGreaterThan(supportingBox!.width);
    } else {
      expect(Math.abs(leadBox!.width - supportingBox!.width)).toBeLessThan(1);
      const [first, second] = tab === "Findings" ? [supportingBox!, leadBox!] : [leadBox!, supportingBox!];
      expect(first.y + first.height).toBeLessThan(second.y);
    }
    if (tab === "Findings") {
      await expect(page.locator("#analytics-panel dl dt")).toHaveText(["Findings", "Open", "Resolved", "Dismissed"]);
      const metrics = await page.locator("#analytics-panel dl").boundingBox();
      const filters = await page.getByRole("group", { name: "Finding filters", exact: true }).boundingBox();
      const severity = await page.getByRole("region", { name: "Severity", exact: true }).boundingBox();
      expect(filters!.y).toBeGreaterThanOrEqual(metrics!.y + metrics!.height);
      expect(filters!.y + filters!.height).toBeLessThanOrEqual(severity!.y);
      await expect(page.getByRole("tabpanel").getByRole("heading", { level: 2 })).toHaveText(["Severity", "Categories", "Current resolution", "Finding trends"]);
    }
    if (tab === "Reviews") {
      await expect(page.locator("#analytics-panel dl dt")).toHaveText(["Review runs", "PRs reviewed", "Average review duration", "Contributors"]);
      await expect(page.locator("#analytics-panel dl > div").filter({ hasText: "Average review duration" })).toContainText("3 min");
      await expect(page.locator("#analytics-panel dl > div").filter({ hasText: "Contributors" })).toContainText("2");
      const chart = page.getByRole("region", { name: "Assessment outcomes", exact: true }).locator("[data-slot=chart]");
      expect(await chart.evaluate((element) => {
        const box = element.getBoundingClientRect();
        const ticks = [...element.querySelectorAll(".recharts-cartesian-axis-tick-value")];
        return ticks.length > 0 && ticks.every((tick) => tick.getBoundingClientRect().left >= box.left);
      })).toBe(true);
    }
    if (tab === "Usage") {
      await expect(page.getByRole("table", { name: "Usage by model", exact: true }).getByRole("columnheader")).toHaveCount(3);
      await expect(page.getByRole("table", { name: "Usage by repository", exact: true }).getByRole("columnheader")).toHaveCount(5);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.impeccable/review/analytics-${tab.toLowerCase()}-${info.project.name}-light.png`, fullPage: true, animations: "disabled" });
  }
  await page.getByRole("tab", { name: "Overview", exact: true }).click();
  await page.getByRole("button", { name: "Switch to dark mode", exact: true }).click();
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.documentElement).colorScheme)).toBe("dark");
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.screenshot({ path: `../.impeccable/review/analytics-overview-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Security", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Security activity", exact: true })).toBeVisible();
  await page.screenshot({ path: `../.impeccable/review/analytics-security-${info.project.name}-dark.png`, fullPage: true, animations: "disabled" });
  if (info.project.name === "mobile") {
    await page.setViewportSize({ width: 320, height: 720 });
    const tabs = await page.getByRole("tablist").boundingBox();
    for (const name of ["Overview", "Findings", "Reviews", "Usage", "Security"]) {
      const box = await page.getByRole("tab", { name, exact: true }).boundingBox();
      expect(box!.x + box!.width).toBeLessThanOrEqual(tabs!.x + tabs!.width + 1);
      expect(box!.y + box!.height).toBeLessThanOrEqual(tabs!.y + tabs!.height + 1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

for (const tab of ["overview", "findings", "reviews", "usage", "security"] as const) {
  test(`analytics ${tab} hides ordering controls and preserves saved ordering`, async ({ page }, info) => {
    if (info.project.name === "desktop") await page.setViewportSize({ width: 1292, height: 889 });
    const requests = await mock(page);
    await page.goto(`/repositories/22/analytics?tab=${tab}&period=all`);
    await expect(page.locator("#analytics-panel dl dd")).toHaveCount(4);
    await expect(page.getByRole("group", { name: "Ordering", exact: true })).toHaveCount(0);
    expect(requests.at(-1)?.searchParams.get("ordering")).toBe("-count");
    await page.goto(`/repositories/22/analytics?tab=${tab}&period=all&ordering=-key`);
    await expect.poll(() => requests.at(-1)?.searchParams.get("ordering")).toBe("-key");
    await expect(page.locator("#analytics-panel dl dd")).toHaveCount(4);
    expect(requests.at(-1)?.pathname).toBe(`/api/v1/analytics/${tab}`);
    await expect(page.getByRole("group", { name: "Ordering", exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (tab === "overview" || tab === "findings") {
      await mkdir("../.impeccable/review", { recursive: true });
      await page.screenshot({ path: `../.impeccable/review/analytics-no-ordering-${tab}-${info.project.name}.png`, animations: "disabled" });
    }
  });
}

for (const path of ["/analytics", "/analytics?repo=22&repo=23", "/security"]) test(`legacy global route ${path} opens repository directory`, async ({ page }) => {
  const requests = await mock(page);
  await page.goto(path);
  await expect(page).toHaveURL("/repositories");
  await expect(page.getByRole("navigation", { name: "Repositories", exact: true }).getByRole("link")).toHaveCount(2);
  expect(requests).toHaveLength(0);
});

test("legacy single repository analytics links preserve the selected period and tab", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/analytics?repo=23&period=90&tab=usage");
  await expect(page).toHaveURL("/repositories/23/analytics?period=90&tab=usage");
  await expect(page.getByRole("heading", { name: "Usage by model", exact: true })).toBeVisible();
  expect(requests.every((url) => url.searchParams.getAll("repo").join() === "23")).toBe(true);
});
