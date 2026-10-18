import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import type { Schema } from "../src/api";
import { analyticsRange } from "../src/lib/analytics";
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
const reviews: Schema["AnalyticsReviews"] = { scope, runs: 28, reviewed_prs: 24, median_duration_seconds: 125, duration_samples: 26,
  outcomes: [{ key: "completed", count: 20 }, { key: "partial", count: 4 }, { key: "failed", count: 2 }, { key: "running", count: 2 }],
  coverage: [{ key: "selected", count: 143 }, { key: "completed", count: 110 }, { key: "reused", count: 20 }, { key: "failed", count: 8 }, { key: "waived", count: 5 }], coverage_missing_runs: 2,
  assessments: [{ key: "new", count: 31 }, { key: "repeated", count: 12 }, { key: "rejected", count: 8 }, { key: "withheld", count: 3 }, { key: "pending", count: 2 }], assessment_missing_runs: 1,
  contributors: [{ key: "maria", count: 16 }, { key: "leo", count: 8 }] };
const usage: Schema["AnalyticsUsage"] = { scope, total, trend,
  stages: ["review", "assessment", "verification", "summary"].map((key) => ({ key, repository_id: null, usage: { ...total, tokens: 311450 } })),
  repositories: repositories.map((repo) => ({ key: repo.full_name, repository_id: repo.id, usage: total })),
  models: [{ key: "review-model", repository_id: null, usage: total }], efforts: [{ key: "high", repository_id: null, usage: total }] };

async function mock(page: Page, override?: (view: string, params: URLSearchParams) => object | undefined) {
  const requests: URL[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    let json: unknown;
    if (path === "/setup/status") json = { configured: true, public_url: "http://localhost" };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", csrf: "test", avatar_url: "" };
    else if (path === "/repositories") json = repositories.map((repo) => ({ ...repo, open_prs: 2, enabled: true, active: true }));
    else if (path.startsWith("/analytics/")) {
      requests.push(url);
      const view = path.split("/").at(-1)!;
      json = override?.(view, url.searchParams) ?? ({ overview, findings, reviews, usage }[view]);
    } else json = {};
    await route.fulfill({ json });
  });
  return requests;
}

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
  expect(analyticsRange(new URLSearchParams("period=invalid"), today).period).toBe("30");
});

test("overview loads only its endpoint and charts offer data tables", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "Analytics", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "Top repositories", exact: true }).first()).toBeVisible();
  expect(requests.every((url) => url.pathname.endsWith("/overview"))).toBe(true);
  await page.getByText("View review activity data", { exact: true }).click();
  await expect(page.getByRole("table", { name: "Review activity data", exact: true })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Page not found");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("repository and period selection survive tabs, reload and browser back", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/analytics");
  await page.getByRole("button", { name: "Repositories: All", exact: true }).click();
  await page.getByRole("textbox", { name: "Search repositories" }).fill("noodle");
  await page.getByRole("checkbox", { name: "acme/noodle" }).click();
  await expect(page.getByRole("checkbox", { name: "acme/noodle" })).toBeChecked();
  await page.keyboard.press("Escape");
  await page.getByLabel("Date range", { exact: true }).selectOption("90");
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Usage by model", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/repo=23/);
  await expect(page).toHaveURL(/period=90/);
  expect(requests.at(-1)?.searchParams.get("repo")).toBe("23");
  await page.reload();
  await expect(page.getByRole("tab", { name: "Usage", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "true");
});

test("severity controls open a filtered Findings tab and filters clear", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/analytics");
  await page.getByRole("button", { name: "Critical: 3 findings", exact: true }).click();
  await expect(page).toHaveURL(/tab=findings/);
  await expect(page).toHaveURL(/severity=critical/);
  await expect(page.getByText("31 of 31 findings match")).toBeVisible();
  expect(requests.at(-1)?.searchParams.get("severity")).toBe("critical");
  await page.getByRole("button", { name: "Clear finding filters", exact: true }).click();
  await expect(page).not.toHaveURL(/severity=/);
});

test("custom dates validate before requesting and keyboard switches tabs", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/analytics?period=custom&start=2026-02-30&end=bad");
  await expect(page.getByRole("alert")).toContainText("Choose a valid");
  expect(requests).toHaveLength(0);
  await page.getByLabel("From", { exact: true }).fill("2026-10-01");
  await page.getByLabel("Through", { exact: true }).fill("2026-10-18");
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Findings", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(requests.at(-1)?.searchParams.get("to_at")).toContain("2026-10-19");
});

test("permission loss hides cached analytics and offers recovery", async ({ page }) => {
  await mock(page);
  await page.goto("/analytics?tab=usage");
  await expect(page.getByRole("heading", { name: "Usage by model", exact: true })).toBeVisible();
  await page.route("**/api/v1/analytics/usage?**", (route) => route.fulfill({ status: 403, json: { detail: "Repository access was revoked." } }));
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access was revoked");
  await expect(page.getByRole("heading", { name: "Usage by model", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry analytics", exact: true })).toBeVisible();
});

test("empty activity, filtered findings and unknown tokens remain distinct", async ({ page }) => {
  await mock(page, (view) => view === "overview" ? { ...overview, reviewed_prs: 0, findings: 0, trend: [] } : view === "findings" ? { ...findings, total: 0 } : view === "usage" ? { ...usage, total: { ...total, tokens: null, mean_per_run: null, mean_per_reviewed_pr: null } } : undefined);
  await page.goto("/analytics");
  await expect(page.getByText("No review activity in this period", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Findings", exact: true }).click();
  await expect(page.getByText("No findings match these filters", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Usage", exact: true }).click();
  await expect(page.locator("dl").getByText("Unavailable", { exact: true })).toHaveCount(3);
  await expect(page.getByRole("status")).toContainText("Partial reporting");
});

test("charts follow every palette and remain keyboard accessible", async ({ page }) => {
  await mock(page);
  await page.goto("/analytics");
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
  await page.getByText("View severity data", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("table", { name: "Severity data", exact: true })).toBeVisible();
});

test("capture Analytics in each tab and theme with long labels", async ({ page }, info) => {
  await mock(page, (view) => view === "usage" ? { ...usage, models: [{ key: "provider/a-very-long-model-identifier-that-must-wrap-without-widening-the-workspace", repository_id: null, usage: total }] } : undefined);
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { name: "Review activity", exact: true })).toBeVisible();
  await mkdir("../.impeccable/review", { recursive: true });
  for (const tab of ["Overview", "Findings", "Reviews", "Usage"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-busy", "false");
    await expect(page.locator("[data-slot=chart]").first()).toBeVisible();
    await page.evaluate(async () => {
      await document.fonts.ready;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    });
    await expect.poll(() => page.locator("[data-slot=chart]").evaluateAll((charts) => charts.every((chart) => {
      const svg = chart.querySelector("svg.recharts-surface");
      return svg && Math.abs(svg.getBoundingClientRect().width - chart.getBoundingClientRect().width) < 2;
    }))).toBe(true);
    if (tab === "Findings") {
      await expect(page.getByRole("tabpanel").getByRole("heading", { level: 2 })).toHaveText(["Severity", "Categories", "Current resolution", "Finding trends"]);
    }
    if (tab === "Reviews") {
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
});
