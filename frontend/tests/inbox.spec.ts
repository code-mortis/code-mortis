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
  run_id: i === 3 ? null : `run-${i}`, publication: "published", publication_error: null,
  created_at: at, updated_at: "2026-10-09T12:00:00Z", draft: false, open_findings: [2, 0, 1, 0, 3, 0, 0, 0][i],
}));
const closed: Pull = { ...rows[0], id: "closed", title: "Abandoned change", state: "closed", review_status: "cancelled" };

async function mockInbox(page: Page, options: { many?: boolean; empty?: boolean } = {}) {
  const requests: URLSearchParams[] = [];
  const controls = { failMore: false, fail: 0, complete: false };
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const params = url.searchParams;
    let data: unknown;
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: 7, login: "developer", role: "admin", avatar_url: "", csrf: "test" };
    else if (path === "/connections") data = [];
    else if (path === "/repositories") data = [{ ...repositories[0], installation_id: 10, private: false, active: true, enabled: true, connection_id: null, settings: { automatic: true, model: "", effort: "medium", language: "English", exclusions: "", background: "", timeout_seconds: 900, token_budget: 200000, max_files: 40, concurrency: 2, rules: null }, open_prs: 6 }];
    else if (path === "/repositories/22/pulls") data = [];
    else if (path.startsWith("/pulls/")) data = {
      pull_request: { ...rows[0], url: "https://github.com/acme/code-mortis/pull/42", latest_run: null, head_ref: "feature", head_sha: "a".repeat(40), base_sha: "b".repeat(40), fork: false },
      repository: repositories[0], runs: [], findings: [], can_write: false,
    };
    else if (path === "/pulls") {
      requests.push(params);
      if (controls.fail || (controls.failMore && params.has("group"))) return route.fulfill({ status: controls.fail || 502, json: { detail: "Inbox temporarily unavailable" } });
      let all = options.empty ? [] : [...rows, closed];
      if (controls.complete) all = all.map((r) => r.id === "pr-0" ? { ...r, review_status: "completed" } : r);
      if (options.many) all = [...all, ...Array.from({ length: 40 }, (_, i) => ({ ...rows[3], id: `extra-${i}`, title: `Additional change ${i}`, number: 100 + i }))];
      let filtered = all.filter((r) => (!params.has("q") || `${r.title} ${r.number} ${r.author} ${r.repository_name}`.toLowerCase().includes(params.get("q")!.toLowerCase()))
        && (!params.has("repo") || params.getAll("repo").includes(String(r.repository_id)))
        && (!params.has("review") || params.getAll("review").includes(r.review_status))
        && (!params.has("author") || params.getAll("author").includes(r.author))
        && (!params.has("findings") || (params.get("findings") === "open" ? r.open_findings > 0 : r.open_findings === 0)));
      const state_counts = Object.fromEntries(["open", "merged", "closed"].map((state) => [state, filtered.filter((r) => r.state === state).length]));
      const states = params.has("state") ? params.getAll("state") : ["open", "merged"];
      filtered = filtered.filter((r) => states.includes("all") || states.includes(r.state));
      const groupNames = (["active", "reviewed", "merged", "closed"] as const).filter((g) => states.includes("all") || states.includes(g === "active" || g === "reviewed" ? "open" : g));
      data = {
        groups: groupNames.filter((g) => !params.has("group") || params.get("group") === g).map((group) => {
          const items = filtered.filter((r) => (r.state !== "open" ? r.state : r.review_status === "completed" ? "reviewed" : "active") === group);
          if (params.has("ordering")) {
            const ordering = params.get("ordering")!;
            const field = ordering.replace(/^-/, "");
            const key = (field === "repository" ? "repository_name" : field) as "created_at" | "updated_at" | "title" | "repository_name" | "author";
            const priority = (r: Pull) => group !== "active" ? 2 : r.review_status === "running" ? 0 : r.review_status === "queued" ? 1 : 2;
            items.sort((a, b) => priority(a) - priority(b) || (a[key] ?? "").localeCompare(b[key] ?? "") * (ordering.startsWith("-") ? -1 : 1) || b.id.localeCompare(a.id));
          }
          const index = Math.min(+(params.get("page") ?? 1), Math.max(1, Math.ceil(items.length / 20)));
          return { group, items: items.slice((index - 1) * 20, index * 20), total: items.length, page: index, has_more: index * 20 < items.length };
        }),
        total: filtered.length, repository_count: new Set(filtered.map((r) => r.repository_id)).size,
        registered_total: all.length, repositories, authors: ["maria", "leo", "sam"], state_counts, page_size: 20,
      } satisfies Schema["InboxOut"];
    } else data = [];
    await route.fulfill({ json: data });
  });
  return { requests, controls };
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test("grouped inbox and modern state filters round trip through the URL", async ({ page }) => {
  await mockInbox(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Inbox", exact: true })).toBeVisible();
  await expect(page.getByRole("status").first()).toContainText("8 pull requests · 3 repositories");
  await expect(page.getByRole("region", { name: /Active & needs review/ }).getByRole("link").first()).toContainText(titles[0]);
  await expect(page.getByRole("link", { name: /Bound model discovery/ }).locator("time")).toHaveAttribute("datetime", at);
  await expect(page.getByRole("link", { name: /Abandoned change/ })).toHaveCount(0);
  await page.getByRole("button", { name: /Edit PR state/ }).click();
  await expect(page.getByRole("checkbox", { name: /^Closed/ })).not.toBeChecked();
  await page.getByText("Closed", { exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /^Closed/ })).toBeChecked();
  await expect(page).toHaveURL(/state=all/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("link", { name: /Abandoned change/ })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: /Abandoned change/ })).toBeVisible();
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.getByRole("button", { name: "Remove PR state filter" }).click();
  await expect(page).toHaveURL(/state=all/);
  await page.goBack();
  await expect(page.getByRole("button", { name: "Show closed" })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("link", { name: /Abandoned change/ })).toBeVisible();
  await noOverflow(page);
});

test("searchable multi-select filters and debounced search preserve browser history", async ({ page }) => {
  const { requests } = await mockInbox(page);
  await page.goto("/");
  await page.getByRole("button", { name: /Edit Repository/ }).click();
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
  await expect(page.getByRole("button", { name: /Edit Repository/ })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/repo=23&repo=24/);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page).not.toHaveURL(/q=/);
  await expect(page).toHaveURL(/repo=23&repo=24/);
  await expect(page.getByRole("textbox", { name: "Search pull requests", exact: true })).toBeFocused();
  await page.getByRole("button", { name: /Edit Repository/ }).click();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(page).not.toHaveURL(/repo=/);
  await expect(page.getByRole("button", { name: /Edit Repository/ })).toBeFocused();
});

test("independent loading, sharing, detail return and bounds", async ({ page }) => {
  await mockInbox(page, { many: true });
  await page.goto("/");
  const active = page.getByRole("region", { name: /Active & needs review/ });
  const reviewed = page.getByRole("region", { name: /Reviewed & open/ });
  await expect(active.getByRole("link")).toHaveCount(20);
  await expect(reviewed.getByRole("link")).toHaveCount(2);
  await active.getByRole("button", { name: /Load more/ }).click();
  await expect(page).toHaveURL(/active_pages=2/);
  await expect(active.getByRole("link")).toHaveCount(40);
  await expect(reviewed.getByRole("link")).toHaveCount(2);
  await page.reload();
  await expect(active.getByRole("link")).toHaveCount(40);
  await active.getByRole("link").first().click();
  await expect(page.getByRole("heading", { name: titles[0], exact: true })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Inbox", exact: true })).toHaveAttribute("href", "/?active_pages=2");
  await page.getByRole("link", { name: "Inbox", exact: true }).last().click();
  await expect(page).toHaveURL(/active_pages=2/);
  await expect(active.getByRole("link")).toHaveCount(40);
  await page.goto("/?active_pages=999&merged_pages=200");
  await expect(page).toHaveURL(/active_pages=3$/);
  await expect(active.getByRole("link")).toHaveCount(44);
  await page.getByRole("button", { name: "Show closed" }).click();
  await expect(page).not.toHaveURL(/pages=/);
  await expect(active.getByRole("link")).toHaveCount(20);
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
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toHaveAccessibleName("Edit PR state: Open, Merged");
  await page.getByRole("button", { name: /Edit PR state/ }).click();
  const merged = page.getByRole("checkbox", { name: /^Merged/ });
  await merged.focus();
  await page.keyboard.press("Space");
  await expect(merged).not.toBeChecked();
  await expect(page).toHaveURL(/state=open$/);
  await expect(page.getByRole("checkbox", { name: /^Open/ })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: /Edit PR state/ })).toBeFocused();
});

test("errors retain loaded rows, retry individually and clear denied data", async ({ page }) => {
  const { controls } = await mockInbox(page, { many: true });
  await page.goto("/");
  const active = page.getByRole("region", { name: /Active & needs review/ });
  await expect(active.getByRole("link")).toHaveCount(20);
  controls.failMore = true;
  await active.getByRole("button", { name: /Load more/ }).click();
  await expect(active.getByRole("alert")).toContainText("Inbox temporarily unavailable");
  await expect(active.getByRole("link")).toHaveCount(20);
  controls.failMore = false;
  await active.getByRole("button", { name: /Retry/ }).click();
  await expect(active.getByRole("link")).toHaveCount(40);
  controls.fail = 403;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 10000 });
  await expect(page.getByRole("link", { name: /Bound model discovery/ })).toHaveCount(0);
});

test("empty workspace, no matches, and legacy repository links", async ({ page }) => {
  await mockInbox(page, { empty: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your inbox is empty" })).toBeVisible();
  await page.goto("/?repo=22&configure=limits");
  await expect(page).toHaveURL(/\/repositories\?repo=22&configure=limits/);
  await expect(page.getByRole("dialog", { name: "Repository configuration" })).toBeVisible();
  await expect(page.getByLabel("Timeout (seconds)", { exact: true })).toBeVisible();
});

test("live reviews move groups and preserve collapsed groups", async ({ page }) => {
  const { controls } = await mockInbox(page);
  await page.goto("/");
  await expect(page.getByRole("region", { name: /Active & needs review/ }).getByRole("link")).toHaveCount(4);
  await page.getByRole("button", { name: "Merged (2)", exact: true }).click();
  controls.complete = true;
  await expect(page.getByRole("region", { name: /Reviewed & open/ }).getByRole("link")).toHaveCount(3, { timeout: 10000 });
  await expect(page.getByRole("button", { name: "Merged (2)", exact: true })).toHaveAttribute("aria-expanded", "false");
});

test("shared combined filters and a search with no matches", async ({ page }) => {
  const { requests } = await mockInbox(page);
  await page.goto("/?repo=22&state=open&review=running&author=maria&findings=open");
  await expect(page.getByRole("status").first()).toHaveText("1 pull request · 1 repository");
  await expect(page.getByRole("button", { name: /Edit Review status/ })).toContainText("Running");
  await expect(page.getByRole("button", { name: /Edit Author/ })).toContainText("@maria");
  await expect(page.getByRole("button", { name: /Edit Open findings/ })).toContainText("Has open findings");
  expect(requests.at(-1)?.get("state")).toBe("open");
  await page.getByRole("textbox", { name: "Search pull requests", exact: true }).fill("does not exist");
  await expect(page.getByRole("heading", { name: "No matching pull requests" })).toBeVisible();
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page.getByRole("status").first()).toContainText("8 pull requests");
  await noOverflow(page);
});

test("long titles and filter values wrap within the viewport", async ({ page }) => {
  await mockInbox(page);
  await page.route("**/api/v1/pulls?**", async (route) => {
    const long = { ...rows[0], created_at: null, title: "Preserve".repeat(35), repository_name: "organization/" + "repository".repeat(10), author: "developer".repeat(4) };
    await route.fulfill({ json: { groups: [{ group: "active", total: 1, page: 1, has_more: false, items: [long] }], total: 1, repository_count: 1, registered_total: 1, repositories: [{ id: 22, full_name: long.repository_name }], authors: [long.author], state_counts: { open: 1, merged: 0, closed: 0 }, page_size: 20 } });
  });
  await page.goto("/?repo=22");
  await expect(page.getByRole("link", { name: /PreservePreserve/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /PreservePreserve/ })).toContainText("Pending sync");
  await noOverflow(page);
  await page.getByRole("button", { name: /Edit Repository/ }).click();
  await expect(page.getByRole("checkbox", { name: /organization/ })).toBeVisible();
  await noOverflow(page);
});

test("ordering survives sharing, history and details and resets loaded pages", async ({ page }) => {
  const { requests } = await mockInbox(page, { many: true });
  await page.goto("/?ordering=title&active_pages=2");
  const active = page.getByRole("region", { name: /Active & needs review/ });
  await expect(active.getByRole("link")).toHaveCount(40);
  await expect(active.getByRole("link").nth(2)).toContainText("Additional change 0");
  expect(requests.filter((p) => p.has("group")).at(-1)?.get("ordering")).toBe("title");
  await page.getByRole("button", { name: "Sort descending", exact: true }).click();
  await expect(page).toHaveURL(/\?ordering=-title$/);
  await expect(active.getByRole("link")).toHaveCount(20);
  await expect(active.getByRole("link").nth(2)).toContainText("Improve keyboard navigation");
  await page.reload();
  await expect(page.getByRole("button", { name: "Sort by Title", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toHaveAttribute("title", "Z to A");
  await active.getByRole("link").first().click();
  await page.getByRole("link", { name: "Inbox", exact: true }).last().click();
  await expect(page).toHaveURL(/\?ordering=-title$/);
  const sort = page.getByRole("button", { name: "Sort by Title", exact: true });
  await sort.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menuitemradio", { name: "Title", exact: true })).toBeChecked();
  await page.getByRole("menuitemradio", { name: "Updated", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\?ordering=-updated_at$/);
  await expect(page.getByRole("button", { name: "Sort by Updated", exact: true })).toBeFocused();
  await page.goBack();
  await expect(page).toHaveURL(/\?ordering=-title$/);
  await page.goForward();
  await expect(page).toHaveURL(/\?ordering=-updated_at$/);
  await page.getByRole("button", { name: "Reset ordering", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Sort by Created", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Sort ascending", exact: true }).click();
  await expect(page).toHaveURL(/\?ordering=created_at$/);
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Sort ascending", exact: true })).toHaveAttribute("title", "Newest first");
  await noOverflow(page);
});

test("inbox visual states in light and dark", async ({ page }, info) => {
  await mockInbox(page);
  await page.goto("/");
  await expect(page.getByRole("link", { name: /Bound model discovery/ })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  if (info.project.name === "mobile") {
    const search = await page.getByRole("textbox", { name: "Search pull requests", exact: true }).boundingBox();
    expect(search!.width).toBeGreaterThan(page.viewportSize()!.width * 0.8);
  }
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    await page.getByLabel("Appearance").filter({ visible: true }).selectOption(theme);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.getByRole("button", { name: /Edit PR state/ }).click();
    await expect(page.getByRole("checkbox", { name: /^Closed/ })).toBeVisible();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-filters-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Sort by Created", exact: true }).click();
    await expect(page.getByRole("menuitemradio", { name: "Created", exact: true })).toBeChecked();
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/inbox-ordering-${info.project.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
  }
  if (info.project.name === "desktop") {
    await page.getByLabel("Appearance").filter({ visible: true }).selectOption("light");
    for (const width of [1536, 1388, 768]) {
      await page.setViewportSize({ width, height: width === 1536 ? 1024 : 938 });
      await noOverflow(page);
      await page.screenshot({ path: `../.impeccable/review/inbox-${width}.png`, fullPage: true, animations: "disabled" });
    }
  }
});
