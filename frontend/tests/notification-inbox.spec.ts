import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const completedAt = "2026-11-20T10:00:00Z";
const snapshot = "2026-11-20T10:01:00Z";
const href = "/repositories/22/settings?tab=context&generation=finished-draft";
const draft: Schema["ActivityItem"] = {
  kind: "repository_summary", id: "finished-draft", repository: "acme/payments",
  title: "Repository summary", href, status: "done", stages: [], finished_at: completedAt, unread: true,
};
const running: Schema["ActivityItem"] = {
  kind: "scan", id: "active", repository: "acme/payments", title: "Default branch",
  href: "/security/scans/active", status: "running", stages: ["Assessment"], finished_at: null, unread: false,
};

async function mockActivity(page: Page) {
  const calls: Record<string, unknown>[] = [];
  const recent = Array.from({ length: 5 }, (_, index) => ({
    ...draft, id: `read-${index}`, title: `Older run ${index + 1}`, unread: false,
  }));
  const state = { unread: true, running: true, failActivity: false, failDetail: false, more: false, activityGets: 0, cursor: "" };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    let json: unknown = [];
    if (path === "/setup/status") json = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", avatar_url: "", csrf: "csrf" };
    else if (path === "/pulls") json = { groups: [], total: 0, repository_count: 0, registered_total: 0, repositories: [], state_counts: {}, authors: [], page_size: 10 };
    else if (path === "/repositories") json = [{
      id: 22, installation_id: 10, full_name: "acme/payments", active: true, enabled: true, connection_id: "connection",
      settings: { analysis_engine: "mortis", automatic: true, model: "review-model", effort: "medium", language: "English",
        exclusions: "", background: "", timeout_seconds: 900, token_budget: 200000, max_files: 40, concurrency: 2 },
    }];
    else if (path === "/repositories/22/background/finished-draft") {
      if (state.failDetail) return route.fulfill({ status: 403, json: { detail: "Generation unavailable." } });
      json = { id: draft.id, status: "done", mode: "summary_draft", finished_at: completedAt, project_summary: "A saved summary draft." };
    } else if (path === "/activity") {
      state.activityGets++;
      if (state.failActivity) return route.fulfill({ status: 503, json: { detail: "Activity unavailable." } });
      state.cursor = url.searchParams.get("running_cursor") ?? "";
      json = {
        running: state.running ? state.cursor ? [{ ...running, id: "second", title: "Second page job", stages: ["Fix verification"] }] : [running] : [],
        updates: state.unread ? [draft, ...recent] : [{ ...draft, unread: false }, ...recent.slice(0, 4)],
        running_count: state.running ? 1 : 0, unread_count: Number(state.unread), as_of: snapshot, snapshot: "feed-snapshot",
        next_running_cursor: state.more && !state.cursor ? "continue" : null, next_updates_cursor: null,
      } satisfies Schema["ActivityFeed"];
    } else if (path === "/activity/read") {
      expect(request.headers()["x-csrf-token"]).toBe("csrf");
      const body = request.postDataJSON();
      calls.push(body);
      if (body.mode === "all" || body.kind === draft.kind && body.id === draft.id) state.unread = false;
      return route.fulfill({ status: 204 });
    }
    return route.fulfill({ json });
  });
  return { calls, state };
}

test("notification inbox keeps recent read updates, opens and dismisses by keyboard, and caps its scroller", async ({ page }, info) => {
  const { calls } = await mockActivity(page);
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Notifications, 1 running, 1 unread" });
  await expect(trigger).toHaveText("2");
  await trigger.press("Enter");
  const inbox = page.getByRole("dialog", { name: "Notifications" });
  await expect(inbox.getByRole("tab", { name: "Running 1" })).toBeFocused();
  await expect(inbox.getByText("Assessment", { exact: true })).toHaveCSS("animation-name", "none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(inbox.getByText("Assessment", { exact: true })).toHaveCSS("animation-duration", "3.4s");
  await expect(inbox.getByRole("button", { name: "Close" })).toHaveCount(0);
  await inbox.getByRole("tab", { name: "Updates 1" }).click();
  await expect(inbox.getByRole("link", { name: /Repository summary.*Unread/ })).toBeVisible();
  const scroller = inbox.getByRole("tabpanel", { name: "Updates 1" });
  const bounds = await inbox.boundingBox();
  expect(bounds?.height).toBeLessThanOrEqual((page.viewportSize()?.height ?? 0) * (info.project.name === "mobile" ? 0.62 : 0.74) + 1);
  await expect(scroller).toHaveCSS("overflow-y", "auto");
  await page.screenshot({ path: info.outputPath("notification-inbox.png") });
  const brush = inbox.getByRole("button", { name: "Mark all as read" });
  await expect(brush).toHaveText("");
  await expect(brush.locator("svg")).toBeVisible();
  await brush.click();
  const updatedTrigger = page.getByRole("button", { name: "Notifications, 1 running, 0 unread" });
  await expect(updatedTrigger).toHaveText("1");
  await expect(brush).toHaveAttribute("aria-disabled", "true");
  await expect(inbox.getByRole("link")).toHaveCount(5);
  await expect(inbox.getByRole("link", { name: /^Repository summary.*Completed/ })).toBeVisible();
  await expect(inbox.getByRole("link", { name: /Unread/ })).toHaveCount(0);
  expect(calls).toEqual([{ mode: "all", as_of: snapshot, snapshot: "feed-snapshot" }]);
  await page.keyboard.press("Escape");
  await expect(inbox).toBeHidden();
  await expect(updatedTrigger).toBeFocused();
});

for (const source of ["inbox", "direct", "denied"] as const) {
  test(`summary draft result marks read only on a successful ${source} visit`, async ({ page }) => {
    const { calls, state } = await mockActivity(page);
    state.failDetail = source === "denied";
    await page.goto(source === "inbox" ? "/" : href);
    if (source === "inbox") {
      await page.getByRole("button", { name: "Notifications, 1 running, 1 unread" }).click();
      const inbox = page.getByRole("dialog", { name: "Notifications" });
      await inbox.getByRole("tab", { name: "Updates 1" }).click();
      await inbox.getByRole("link", { name: /Repository summary.*Unread/ }).click();
    }
    if (source === "denied") {
      await expect(page.getByText("Generation unavailable.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Notifications, 1 running, 1 unread" })).toBeVisible();
      expect(calls).toEqual([]);
      return;
    }
    await expect(page.getByText("A saved summary draft.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Notifications, 1 running, 0 unread" })).toBeVisible();
    expect(calls).toEqual([{ mode: "one", kind: "repository_summary", id: "finished-draft", finished_at: completedAt }]);
  });
}

test("the running list paginates and retains the last successful count on refresh failure", async ({ page }) => {
  await page.clock.install();
  const { state } = await mockActivity(page);
  state.more = true;
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "Notifications, 1 running, 1 unread" });
  await trigger.click();
  const inbox = page.getByRole("dialog", { name: "Notifications" });
  await inbox.getByRole("button", { name: "Next" }).click();
  await expect(inbox.getByText("Second page job", { exact: true })).toBeVisible();
  expect(state.cursor).toBe("continue");
  await inbox.getByRole("button", { name: "Previous" }).click();
  state.failActivity = true;
  await page.clock.fastForward(5100);
  await expect(inbox.getByText("Couldn’t refresh notifications.")).toBeVisible();
  await expect(trigger).toHaveText("2");
  state.failActivity = false;
  state.running = false;
  await inbox.getByRole("button", { name: "Retry" }).click();
  await expect(inbox.getByText("No jobs in progress.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Notifications, 0 running, 1 unread" })).toHaveText("1");
});
