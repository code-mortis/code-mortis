import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import type { Schema } from "../src/api";

type Pull = Schema["InboxPullOut"];
const at = "2026-10-08T12:00:00Z";
const repositories = [{ id: 22, full_name: "acme/code-mortis" }, { id: 23, full_name: "acme/noodle" }, { id: 24, full_name: "acme/noodle-site" }];
const titles = ["Bound model discovery requests", "Preserve response headers", "Handle provider rate limits", "Improve keyboard navigation", "Add repository context", "Persist request filters", "Clarify model defaults", "Improve installation guide"];
const rows: Pull[] = titles.map((title, i) => ({
  id: `pr-${i}`, repository_id: repositories[i % 3].id, repository_name: repositories[i % 3].full_name,
  title, number: 42 + i, author: ["maria", "leo", "sam"][i % 3], state: i > 5 ? "merged" : "open",
  review_status: ["running", "queued", "failed", "not_reviewed", "completed", "completed", "completed", "completed"][i],
  has_running_review: i === 0,
  run_id: i === 3 ? null : `run-${i}`, publication: "published", publication_error: null,
  created_at: at, updated_at: "2026-10-09T12:00:00Z", draft: false, ignored: false, fork: false, head_ref: "feature", open_findings: [2, 0, 1, 0, 3, 0, 0, 0][i],
}));
const closed: Pull = { ...rows[0], id: "closed", title: "Abandoned change", state: "closed", review_status: "cancelled", has_running_review: false };

async function mockInbox(page: Page, options: { many?: boolean; empty?: boolean; historyOnly?: boolean; extra?: number; authors?: string[] } = {}) {
  const requests: URLSearchParams[] = [];
  const controls = { failMore: false, fail: 0, complete: false, state: "open" };
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const params = url.searchParams;
    let data: unknown;
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: 7, login: "developer", role: "admin", avatar_url: "", csrf: "test" };
    else if (path === "/connections") data = [];
    else if (path === "/repositories") data = [{ ...repositories[0], installation_id: 10, private: false, active: true, enabled: true, connection_id: null, settings: { automatic: true, model: "", effort: "medium", language: "English", exclusions: "", background: "", timeout_seconds: 900, token_budget: 200000, max_files: 40, concurrency: 2, rules: null }, open_prs: 6, has_running_review: false }];
    else if (path === "/repositories/22/pulls") data = [];
    else if (path.endsWith("/findings")) data = { groups: [], total: 0, registered_total: 0, status_counts: {}, category_counts: {}, page_size: 10 };
    else if (path.endsWith("/runs")) data = { items: [], total: 0, page: 1, has_more: false, page_size: 10 };
    else if (path.startsWith("/pulls/")) data = {
      pull_request: { ...rows[0], url: "https://github.com/acme/code-mortis/pull/42", latest_run: null, head_ref: "feature", head_sha: "a".repeat(40), base_sha: "b".repeat(40), fork: false },
      repository: { ...repositories[0], settings: { analysis_engine: "ocr" }, connection_requires_setup: false }, run_count: 0, has_active_run: false, latest_observed_run_id: null, can_write: false,
    };
    else if (path === "/pulls") {
      requests.push(params);
      if (controls.fail || (controls.failMore && Number(params.get("page") ?? 1) > 1)) return route.fulfill({ status: controls.fail || 502, json: { detail: "Inbox temporarily unavailable" } });
      let all = options.empty ? [] : [...rows, closed];
      all = all.map((r) => r.id === "pr-0" ? { ...r, state: controls.state, ...(controls.complete ? { review_status: "completed", has_running_review: false } : {}) } : r);
      if (options.historyOnly) all = all.filter((r) => r.state !== "open");
      if (options.many) all = [...all, ...Array.from({ length: options.extra ?? 40 }, (_, i) => ({ ...rows[3], id: `extra-${i}`, title: `Additional change ${i}`, number: 100 + i }))];
      let filtered = all.filter((r) => (!params.has("q") || `${r.title} ${r.number} ${r.author} ${r.repository_name}`.toLowerCase().includes(params.get("q")!.toLowerCase()))
        && (!params.has("repo") || params.getAll("repo").includes(String(r.repository_id)))
        && (!params.has("review") || params.getAll("review").includes(r.review_status) || (params.getAll("review").includes("running") && r.has_running_review))
        && (!params.has("author") || params.getAll("author").includes(r.author))
        && (!params.has("findings") || (params.get("findings") === "open" ? r.open_findings > 0 : r.open_findings === 0)));
      const state_counts = Object.fromEntries(["open", "merged", "closed"].map((state) => [state, filtered.filter((r) => r.state === state).length]));
      const states = params.has("state") ? params.getAll("state") : ["open"];
      filtered = filtered.filter((r) => states.includes("all") || states.includes(r.state) || r.has_running_review);
      const groupNames = ["all"] as const;
      data = {
        groups: groupNames.filter((g) => !params.has("group") || params.get("group") === g).map((group) => {
          const items = [...filtered];
          if (params.has("ordering")) {
            const ordering = params.get("ordering")!;
            const field = ordering.replace(/^-/, "");
            const key = (field === "repository" ? "repository_name" : field) as "created_at" | "updated_at" | "title" | "repository_name" | "author";
            const priority = (r: Pull) => r.has_running_review ? 0 : r.state === "open" && r.review_status === "queued" ? 1 : 2;
            items.sort((a, b) => priority(a) - priority(b) || (a[key] ?? "").localeCompare(b[key] ?? "") * (ordering.startsWith("-") ? -1 : 1) || b.id.localeCompare(a.id));
          }
          const index = Math.min(+(params.get("page") ?? 1), Math.max(1, Math.ceil(items.length / 10)));
          return { group, items: items.slice((index - 1) * 10, index * 10), total: items.length, page: index, has_more: index * 10 < items.length };
        }),
        total: filtered.length, repository_count: new Set(filtered.map((r) => r.repository_id)).size,
        registered_total: all.length, repositories, authors: options.authors ?? ["maria", "leo", "sam"], state_counts, page_size: 10,
      } satisfies Schema["InboxOut"];
    } else data = [];
    await route.fulfill({ json: data });
  });
  return { requests, controls };
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

async function openStateFilter(page: Page) {
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
  const edit = page.getByRole("button", { name: /Edit PR state/ });
  if (await edit.isVisible()) await edit.click();
  else {
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("menuitem", { name: "PR state", exact: true }).click();
  }
}

async function includeState(page: Page, state: "Merged" | "Closed") {
  await openStateFilter(page);
  const option = page.getByRole("checkbox", { name: new RegExp(`^${state}`) });
  await expect(option).toBeVisible();
  if (await option.getAttribute("aria-checked") !== "true") await option.click();
  await expect(option).toBeChecked();
  await page.keyboard.press("Escape");
}

test("Filters menu enables conditions, restores them from the URL, and returns focus", async ({ page }) => {
  await mockInbox(page);
  await page.goto("/?state=all");
  await expect(page.getByRole("status").first()).toContainText("9 pull requests");
  const filters = page.getByRole("button", { name: "Filters", exact: true });
  await expect(page.getByRole("button", { name: /^Edit / })).toHaveCount(0);
  await filters.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitem")).toHaveText(["Repository", "PR state", "Review status", "Author", "Open findings", "Visibility"]);
  await page.getByRole("menuitem", { name: "Repository", exact: true }).press("Enter");
  await expect(page.getByRole("textbox", { name: "Search repository options" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(filters).toBeFocused();
  await expect(page.getByRole("button", { name: /Edit Repository/ })).toHaveCount(0);
  await filters.click();
  await page.getByRole("menuitem", { name: "Review status", exact: true }).click();
  const running = page.getByRole("checkbox", { name: "Running", exact: true });
  await expect(running).toBeFocused();
  await page.keyboard.press("Space");
  await expect(running).toBeChecked();
  await expect(page).toHaveURL(/\?state=all&review=running$/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toBeFocused();
  await page.reload();
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toContainText("Running");
  await filters.click();
  await expect(page.getByRole("menuitem", { name: "Review status", exact: true })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "PR state", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  await page.getByRole("checkbox", { name: /^Merged/ }).click();
  await expect(page).toHaveURL(/state=open&state=closed&review=running/);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Remove Review status filter" }).click();
  await expect(filters).toBeFocused();
  await expect(page).not.toHaveURL(/review=/);
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toBeVisible();
  await page.goto("/?repo=22&state=open&review=running&author=maria&findings=open&visibility=all");
  await filters.click();
  await expect(page.getByText("All filters are in use.")).toBeVisible();
  await noOverflow(page);
  await page.keyboard.press("Escape");
  await expect(filters).toBeFocused();
});

test("inbox defaults to open and explicit filters survive reload and history", async ({ page }) => {
  const { requests } = await mockInbox(page);
  await page.goto("/");
  await expect(page.getByRole("status").first()).toContainText("6 pull requests · 3 repositories");
  expect(requests.at(-1)?.getAll("state")).toEqual(["open"]);
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("region", { name: "Merged", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Closed", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  await expect(page.getByRole("link", { name: /Bound model discovery/ }).locator("xpath=ancestor::*[@data-pull-row]").locator("time")).toHaveAttribute("datetime", at);
  await openStateFilter(page);
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeChecked();
  for (const state of ["Merged", "Closed"]) await expect(page.getByRole("checkbox", { name: new RegExp(`^${state}`) })).not.toBeChecked();
  await page.getByRole("checkbox", { name: /^Closed/ }).click();
  await expect(page).toHaveURL(/state=open&state=closed$/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Pull requests", exact: true }).getByRole("link")).toHaveCount(7);
  await page.reload();
  await expect(page.getByRole("region", { name: "Pull requests", exact: true }).getByRole("link")).toHaveCount(7);
  await page.getByRole("button", { name: "Remove PR state filter" }).click();
  await expect(page).toHaveURL(/\?state=all$/);
  await expect(page.getByRole("region", { name: "Pull requests", exact: true }).getByRole("link")).toHaveCount(9);
  await expect(page.getByRole("heading", { level: 2 })).toHaveCount(0);
  await page.goBack();
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open, Closed");
  await page.goForward();
  await expect(page.getByRole("link", { name: /Abandoned change/ })).toBeVisible();
  await noOverflow(page);
});

test("searchable multi-select filters and debounced search preserve browser history", async ({ page }) => {
  const { requests } = await mockInbox(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Repository", exact: true }).click();
  await page.getByRole("textbox", { name: "Search repository options" }).fill("noodle");
  await page.getByRole("checkbox", { name: "acme/noodle", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "acme/noodle", exact: true })).toBeChecked();
  await page.getByRole("checkbox", { name: "acme/noodle-site", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "acme/noodle-site", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/repo=23&repo=24/);
  await noOverflow(page);
  const historyLength = await page.evaluate(() => window.history.length);
  await page.getByRole("textbox", { name: "Search pull requests", exact: true }).fill("headers");
  await expect(page).toHaveURL(/q=headers/);
  expect(await page.evaluate(() => window.history.length)).toBe(historyLength);
  expect(requests.at(-1)?.getAll("repo")).toEqual(["23", "24"]);
  await expect(page.getByRole("link", { name: /Preserve response headers/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Search pull requests", exact: true })).toHaveValue("headers");
  await page.getByRole("button", { name: "Remove Repository filter" }).click();
  await expect(page).not.toHaveURL(/repo=/);
  await expect(page.getByRole("button", { name: /Edit Repository/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/repo=23&repo=24/);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page).not.toHaveURL(/q=/);
  await expect(page).toHaveURL(/repo=23&repo=24/);
  await expect(page.getByRole("textbox", { name: "Search pull requests", exact: true })).toBeFocused();
  await page.getByRole("button", { name: /Edit Repository/ }).click();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page).not.toHaveURL(/repo=/);
  await expect(page.getByRole("button", { name: /Edit Repository/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeFocused();
});

test("unified loading, sharing, detail return and legacy bounds", async ({ page }, info) => {
  await mockInbox(page, { many: true });
  await page.goto("/?state=open&state=merged");
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(10);
  await active.getByRole("button", { name: /Next/ }).click();
  await expect(page).toHaveURL(/all_pages=2/);
  await expect(active.getByRole("link")).toHaveCount(10);
  await page.reload();
  await expect(active.getByRole("link")).toHaveCount(10);
  await active.getByRole("link").first().click();
  await expect(page.getByRole("heading", { name: `#42 ${titles[0]}`, exact: true })).toBeVisible();
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Inbox", exact: true })).toHaveAttribute("href", "/?state=open&state=merged&all_pages=2");
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Inbox", exact: true })).not.toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "acme/code-mortis", exact: true })).toHaveAttribute("aria-current", "page");
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Close navigation" }).click();
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Inbox", exact: true, disabled: false }).click();
  await expect(page).toHaveURL(/all_pages=2/);
  await expect(active.getByRole("link")).toHaveCount(10);
  await page.goto("/?active_pages=999&merged_pages=200");
  await expect(page).toHaveURL(/all_pages=5$/);
  await expect(active.getByRole("link")).toHaveCount(6);
  await page.goto("/?state=open&reviewed_pages=2");
  await expect(page).toHaveURL(/\?state=open&all_pages=2$/);
  await expect(active.getByRole("link")).toHaveCount(10);
  await includeState(page, "Closed");
  await expect(page).not.toHaveURL(/pages=/);
  await expect(active.getByRole("link")).toHaveCount(10);
});

test("loading another page does not restart a pending search debounce", async ({ page }) => {
  await page.clock.install();
  await mockInbox(page, { many: true });
  await page.goto("/");
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(10);
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  await page.getByRole("textbox", { name: "Search pull requests", exact: true }).fill("headers");
  await page.clock.runFor(200);
  await active.getByRole("button", { name: /Next/ }).click();
  await expect(page).toHaveURL(/all_pages=2/);
  await page.clock.runFor(100);
  await expect(page).toHaveURL(/\?q=headers&state=open$/);
  await page.clock.resume();
  await expect(active.getByRole("link")).toHaveCount(1);
});

test("normalization, closed-only selection and keyboard focus", async ({ page }) => {
  await mockInbox(page);
  await page.goto("/?repo=nope&repo=22&repo=22&state=closed&review=bad&merged_pages=-1&q=%20%20&ordering=--title");
  await expect(page).toHaveURL(/\?repo=22&state=closed$/);
  await page.getByRole("button", { name: /Edit PR state/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("checkbox", { name: /^Closed/ })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toBeFocused();
  await page.goto("/?state=open");
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open");
  await openStateFilter(page);
  const merged = page.getByRole("checkbox", { name: /^Merged/ });
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeDisabled();
  await merged.focus();
  await page.keyboard.press("Space");
  await expect(merged).toBeChecked();
  await expect(page).toHaveURL(/state=open&state=merged$/);
  await page.keyboard.press("Space");
  await expect(merged).not.toBeChecked();
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toBeFocused();
});

test("errors retain loaded rows, retry pagination and clear denied data", async ({ page }) => {
  await page.clock.install();
  const { controls } = await mockInbox(page, { many: true });
  await page.goto("/");
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(10);
  controls.failMore = true;
  await active.getByRole("button", { name: /Next/ }).click();
  await expect(active.getByRole("alert")).toContainText("Inbox temporarily unavailable");
  await expect(active.getByRole("link")).toHaveCount(10);
  controls.failMore = false;
  await active.getByRole("button", { name: /Retry/ }).click();
  await expect(active.getByRole("link")).toHaveCount(10);
  controls.fail = 403;
  await page.clock.fastForward(16000);
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("link", { name: /Bound model discovery/ })).toHaveCount(0);
});

test("mixed-case authors stay checked without duplicate options", async ({ page }) => {
  await mockInbox(page, { authors: ["Maria", "maria", "Leo"] });
  await page.goto("/?author=%40MARIA");
  await expect(page).toHaveURL(/\?state=open&author=maria$/);
  await expect(page.getByRole("status").first()).toContainText("2 pull requests");
  await page.getByRole("button", { name: /Edit Author/ }).click();
  const maria = page.getByRole("checkbox", { name: /^@maria$/i });
  await expect(maria).toHaveCount(1);
  await expect(maria).toBeChecked();
  await maria.click();
  await expect(page).not.toHaveURL(/author=/);
});

test("moving rows replace the page without changing server totals or page cursors", async ({ page }) => {
  await mockInbox(page);
  const requested: number[] = [];
  await page.route("**/api/v1/pulls?**", async (route) => {
    const index = Number(new URL(route.request().url()).searchParams.get("page") ?? 1);
    requested.push(index);
    const items = [rows.slice(0, 2), rows.slice(1, 3), rows.slice(3, 5)][index - 1];
    await route.fulfill({ json: {
      groups: [{ group: "all", items, total: 6, page: index, has_more: index < 3 }],
      total: 6, repository_count: 3, registered_total: 6, repositories,
      authors: ["maria", "leo", "sam"], state_counts: { open: 6 }, page_size: 2,
    } satisfies Schema["InboxOut"] });
  });
  await page.goto("/?all_pages=2");
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(2);
  await expect(page.getByRole("status").first()).toContainText("6 pull requests");
  await active.getByRole("button", { name: /Next/ }).click();
  await expect(active.getByRole("link")).toHaveCount(2);
  await expect.poll(() => requested.at(-1)).toBe(3);
  await expect(active.getByRole("button", { name: /Next/ })).toBeDisabled();
  await expect(page.getByRole("status").first()).toContainText("6 pull requests");
});

test("rows changing state between pages stay visible once and preserve server totals", async ({ page }) => {
  await mockInbox(page);
  const requested: URLSearchParams[] = [];
  await page.route("**/api/v1/pulls?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    requested.push(params);
    const index = Number(params.get("page") ?? 1);
    return route.fulfill({ json: {
      groups: [{ group: "all", items: index === 2
        ? [{ ...rows[0], state: "merged", has_running_review: false, review_status: "completed" }, rows[7]]
        : [rows[0], rows[1], rows[6]], total: 5, page: index, has_more: index < 2 }],
      total: 5, repository_count: 3, registered_total: 5, repositories,
      authors: ["maria", "leo", "sam"], state_counts: { open: 2, merged: 3 }, page_size: 3,
    } satisfies Schema["InboxOut"] });
  });
  await page.goto("/?state=all&merged_pages=2");
  const list = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(list.getByRole("link")).toHaveCount(2);
  await expect(page).toHaveURL(/state=all&all_pages=2$/);
  await expect(page.getByRole("link", { name: new RegExp(rows[0].title) })).toHaveCount(1);
  await expect(page.getByRole("status").first()).toContainText("5 pull requests");
  await expect(list.getByRole("button", { name: /Next/ })).toBeDisabled();
  expect(requested.map((params) => [params.get("group"), params.get("page")])).toEqual([["all", "2"]]);
});

test("large shared pagination reaches every page and keeps the current page live", async ({ page }, info) => {
  await page.clock.install();
  const { requests } = await mockInbox(page, { many: true, extra: 150 });
  await page.goto("/?all_pages=999999");
  await expect(page).toHaveURL(/all_pages=16$/);
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(6);
  await expect(active.getByRole("button", { name: "Next pull requests" })).toBeDisabled();
  await active.getByRole("button", { name: "Previous pull requests" }).click();
  await expect(active.getByRole("link")).toHaveCount(10);
  await expect(active.getByRole("status")).toHaveText("Page 15 of 16 · 156 total");
  await active.getByRole("button", { name: "Next pull requests" }).focus();
  await page.keyboard.press("Enter");
  await expect(active.getByRole("status")).toHaveText("Page 16 of 16 · 156 total");
  await expect(active.getByRole("link")).toHaveCount(6);
  const count = requests.length;
  await page.clock.fastForward(5100);
  await expect.poll(() => requests.length).toBeGreaterThan(count);
  expect(requests.at(-1)?.get("page")).toBe("16");
  await page.screenshot({ path: `/private/tmp/code-mortis-app-pagination-${info.project.name}.png` });
  await noOverflow(page);
});

test("search keeps loaded rows and cancels initial and paginated requests", async ({ page }) => {
  await mockInbox(page, { many: true });
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = (input, init) => {
      const path = String(input);
      if (path.includes("/pulls?") && (path.includes("q=slow") || new URL(path, location.origin).searchParams.has("page"))) {
        sessionStorage.setItem("pending-inbox", path);
        return new Promise((_, reject) => init?.signal?.addEventListener("abort", () => {
          sessionStorage.setItem("aborted-inbox", path);
          reject(new DOMException("Aborted", "AbortError"));
        }, { once: true }));
      }
      return original(input, init);
    };
  });
  await page.goto("/");
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  const search = page.getByRole("textbox", { name: "Search pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(10);
  await search.fill("slow");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("pending-inbox"))).toContain("q=slow");
  await expect(active.getByRole("link")).toHaveCount(10);
  await expect(page.getByRole("status").first()).toContainText("Updating pull requests");
  await expect(active.getByRole("button", { name: /Next/ })).toBeDisabled();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(search).toHaveValue("");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("aborted-inbox"))).toContain("q=slow");
  await active.getByRole("button", { name: /Next/ }).click();
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("pending-inbox"))).toContain("page=2");
  await search.fill("headers");
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("aborted-inbox"))).toContain("page=2");
  await expect(active.getByRole("link")).toHaveCount(1);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("empty workspace, no matches, and legacy repository links", async ({ page }) => {
  await mockInbox(page, { empty: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your inbox is empty" })).toBeVisible();
  await page.goto("/?repo=22&configure=limits");
  await expect(page).toHaveURL(/\/repositories\/22\/settings\?tab=limits/);
  await expect(page.getByRole("form", { name: "Repository configuration" })).toBeVisible();
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toBeVisible();
});

test("merged running reviews stay visible once and respect state filters after finishing", async ({ page }) => {
  const { controls } = await mockInbox(page);
  await page.goto("/");
  const list = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(list.getByRole("link")).toHaveCount(6);
  controls.state = "merged";
  await expect(list.getByRole("link", { name: /Bound model discovery/ }).locator("xpath=ancestor::*[@data-pull-row]")).toContainText("merged", { timeout: 10000 });
  await expect(page.getByRole("link", { name: /Bound model discovery/ })).toHaveCount(1);
  controls.complete = true;
  await expect(list.getByRole("link")).toHaveCount(5, { timeout: 10000 });
  await expect(list.getByRole("link", { name: /Bound model discovery/ })).toHaveCount(0);
  await includeState(page, "Merged");
  await expect(list.getByRole("link")).toHaveCount(8);
  await expect(list.getByRole("link", { name: /Bound model discovery/ })).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Merged", exact: true })).toHaveCount(0);
});

test("an empty open inbox offers all history and preserves that choice on reload", async ({ page }) => {
  await mockInbox(page, { historyOnly: true });
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Pull requests", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "No matching pull requests" })).toBeVisible();
  await page.getByRole("button", { name: "view all pull requests", exact: true }).click();
  await expect(page).toHaveURL(/\?state=all$/);
  const list = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(list.getByRole("link")).toHaveCount(3);
  await expect(list.getByText("merged", { exact: true })).toHaveCount(2);
  await expect(list.getByText("closed", { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(list.getByRole("link")).toHaveCount(3);
  await expect(list.getByText("merged", { exact: true })).toHaveCount(2);
  await expect(list.getByText("closed", { exact: true })).toHaveCount(1);
});

test("shared combined filters and a search with no matches", async ({ page }) => {
  const { requests } = await mockInbox(page);
  await page.goto("/?repo=22&state=open&review=running&author=maria&findings=open&visibility=all");
  await expect(page.getByRole("status").first()).toHaveText("1 pull request · 1 repository");
  if (test.info().project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Inbox", exact: true })).toHaveAccessibleDescription("6 open pull requests");
  if (test.info().project.name === "mobile") await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toContainText("Running");
  await expect(page.getByRole("button", { name: /Edit Author/ })).toContainText("@maria");
  await expect(page.getByRole("button", { name: /Edit Open findings/ })).toContainText("Has open findings");
  expect(requests.at(-1)?.getAll("state")).toEqual(["open"]);
  await page.getByRole("textbox", { name: "Search pull requests", exact: true }).fill("does not exist");
  await expect(page.getByRole("heading", { name: "No matching pull requests" })).toBeVisible();
  await page.getByRole("button", { name: "view all pull requests", exact: true }).click();
  await expect(page.getByRole("status").first()).toContainText("9 pull requests");
  await noOverflow(page);
});

test("long titles and filter values wrap within the viewport", async ({ page }) => {
  await mockInbox(page);
  await page.route("**/api/v1/pulls?**", async (route) => {
    const long = { ...rows[0], created_at: null, title: "Preserve".repeat(35), repository_name: "organization/" + "repository".repeat(10), author: "developer".repeat(4) };
    await route.fulfill({ json: { groups: [{ group: "all", total: 1, page: 1, has_more: false, items: [long] }], total: 1, repository_count: 1, registered_total: 1, repositories: [{ id: 22, full_name: long.repository_name }], authors: [long.author], state_counts: { open: 1, merged: 0, closed: 0 }, page_size: 10 } });
  });
  await page.goto("/?repo=22");
  await expect(page.getByRole("link", { name: /PreservePreserve/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /PreservePreserve/ }).locator("xpath=ancestor::*[@data-pull-row]")).toContainText("Pending sync");
  await noOverflow(page);
  await page.getByRole("button", { name: /Edit Repository/ }).click();
  await expect(page.getByRole("checkbox", { name: /organization/ })).toBeVisible();
  await noOverflow(page);
});

test("ordering survives sharing, history and details and resets loaded pages", async ({ page }) => {
  const { requests } = await mockInbox(page, { many: true });
  await page.goto("/?ordering=title&all_pages=2");
  await expect(page.getByRole("button", { name: "Reset ordering", exact: true })).toHaveCount(0);
  const active = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(active.getByRole("link")).toHaveCount(10);
  await expect(active.getByRole("link").first()).toContainText("Additional change");
  await expect(active.getByRole("status")).toHaveText("Page 2 of 5 · 46 total");
  expect(requests.filter((p) => p.has("group")).at(-1)?.get("ordering")).toBe("title");
  await page.getByRole("button", { name: "Sort descending", exact: true }).click();
  await expect(page).toHaveURL(/\?state=open&ordering=-title$/);
  await expect(active.getByRole("link")).toHaveCount(10);
  await expect(active.getByRole("link").nth(2)).toContainText("Persist request filters");
  await page.reload();
  await expect(page.getByRole("button", { name: "Sort by Title", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toHaveAttribute("title", "Z to A");
  await active.getByRole("link").first().click();
  await page.getByRole("navigation", { name: "Breadcrumb", exact: true }).getByRole("link", { name: "Inbox", exact: true, disabled: false }).click();
  await expect(page).toHaveURL(/\?state=open&ordering=-title$/);
  const sort = page.getByRole("button", { name: "Sort by Title", exact: true });
  await sort.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitemradio", { name: "Title", exact: true })).toBeChecked();
  await page.getByRole("menuitemradio", { name: "Updated", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\?state=open&ordering=-updated_at$/);
  await expect(page.getByRole("button", { name: "Sort by Updated", exact: true })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/\?state=open&ordering=-title$/);
  await page.goForward();
  await expect(page).toHaveURL(/\?state=open&ordering=-updated_at$/);
  await page.getByRole("button", { name: "Sort by Updated", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "Created", exact: true }).click();
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("button", { name: "Sort by Created", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Sort ascending", exact: true }).click();
  await expect(page).toHaveURL(/\?state=open&ordering=created_at$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\?state=open$/);
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toHaveAttribute("title", "Newest first");
  await noOverflow(page);
});

test("inbox visual states in light and dark", async ({ page }, info) => {
  await mockInbox(page);
  await page.goto("/");
  await expect(page.getByRole("link", { name: /Bound model discovery/ })).toBeVisible();
  const list = page.getByRole("region", { name: "Pull requests", exact: true });
  await expect(list.locator('[data-slot=badge]').filter({ hasText: /^open$/ })).toHaveCount(6);
  await expect(list.getByRole("link", { name: /Add repository context/ }).locator("xpath=ancestor::*[@data-pull-row]").getByText("completed", { exact: true })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  if (info.project.name === "mobile") {
    const search = await page.getByRole("textbox", { name: "Search pull requests", exact: true }).boundingBox();
    expect(search!.width).toBeGreaterThan(page.viewportSize()!.width * 0.8);
  }
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    await expect(page.getByRole("button", { name: /Switch to (light|dark) mode/ })).toBeVisible();
    const toggle = page.getByRole("button", { name: `Switch to ${theme} mode` });
    if (await toggle.isVisible()) await toggle.click();
    if (theme === "dark") await expect(page.locator("html")).toHaveClass("dark");
    else await expect(page.locator("html")).not.toHaveClass("dark");
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await expect(page.getByRole("menuitem", { name: "Repository", exact: true })).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-filter-menu-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await openStateFilter(page);
    await expect(page.getByRole("checkbox", { name: /^Closed/ })).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-filters-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Sort by Created", exact: true }).click();
    await expect(page.getByRole("menuitemradio", { name: "Created", exact: true })).toBeChecked();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-ordering-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await includeState(page, "Merged");
    await includeState(page, "Closed");
    await expect(page.getByRole("region", { name: "Pull requests", exact: true }).getByRole("link", { name: /Abandoned change/ })).toBeVisible();
    await expect(list.locator('[data-slot=badge]').filter({ hasText: /^merged$/ })).toHaveCount(2);
    await expect(list.locator('[data-slot=badge]').filter({ hasText: /^closed$/ })).toHaveCount(1);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-history-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.goto("/");
  }
  if (info.project.name === "desktop") {
    await expect(page.getByRole("button", { name: /Switch to (light|dark) mode/ })).toBeVisible();
    await page.getByRole("button", { name: "Switch to light mode" }).click();
    for (const width of [1536, 1388, 768]) {
      await page.setViewportSize({ width, height: width === 1536 ? 1024 : 938 });
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/inbox-${width}.png`, fullPage: true, animations: "disabled" });
    }
  }
});
