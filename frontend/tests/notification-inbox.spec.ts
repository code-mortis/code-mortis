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
const reviewHref = "/runs/new-review";
const reviewTitle = "Add activity inbox and generation status to the workspace";
type ReviewStatus = "queued" | "running" | "completed";

async function mockActivity(page: Page) {
  const calls: Record<string, unknown>[] = [];
  const recent = Array.from({ length: 5 }, (_, index) => ({
    ...draft, id: `read-${index}`, title: `Older run ${index + 1}`, unread: false,
  }));
  const state = {
    unread: true, running: true, failActivity: false, failDetail: false, more: false,
    activityGets: 0, cursor: "", reviewStatus: null as ReviewStatus | null,
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    let json: unknown = [];
    if (path === "/setup/status") json = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") json = { id: 7, login: "developer", role: "admin", avatar_url: "", csrf: "csrf" };
    else if (path === "/pulls" || path === "/repositories/22/pulls") {
      const items: Schema["InboxPullOut"][] = state.reviewStatus ? [{
        id: "new-pr", number: 54, title: reviewTitle, repository_id: 22, repository_name: "acme/payments",
        author: "developer", state: "open", draft: false, ignored: false, fork: false, head_ref: "queue-visibility",
        created_at: completedAt, updated_at: completedAt,
        review_status: state.reviewStatus, has_running_review: state.reviewStatus === "running", run_id: "new-review",
        publication: null, publication_error: null, open_findings: 0,
      }] : [];
      const total = items.length;
      json = {
        groups: [{ group: path === "/pulls" ? "all" : "active", items, total, page: 1, has_more: false }],
        total, repository_count: total, registered_total: total, repositories: [], state_counts: {}, authors: [], page_size: 10,
      } satisfies Schema["InboxOut"];
    }
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
      const review: Schema["ActivityItem"] | null = state.reviewStatus ? {
        kind: "review", id: "new-review", repository: "acme/payments-platform-integrations-and-infrastructure", title: `#54 ${reviewTitle}`,
        href: reviewHref, status: state.reviewStatus, stages: state.reviewStatus === "running" ? ["Analysis"] : [],
        finished_at: state.reviewStatus === "completed" ? completedAt : null, unread: state.reviewStatus === "completed",
      } : null;
      const active = state.running ? state.cursor ? [{ ...running, id: "second", title: "Second page job", stages: ["Fix verification"] }] : [running] : [];
      const updates = state.unread ? [draft, ...recent] : [{ ...draft, unread: false }, ...recent.slice(0, 4)];
      if (review?.unread) updates.unshift(review);
      else if (review) active.unshift(review);
      json = {
        running: active, updates, running_count: Number(state.running) + Number(state.reviewStatus === "running"),
        queued_count: Number(state.reviewStatus === "queued"), unread_count: Number(state.unread) + Number(review?.unread ?? false),
        as_of: snapshot, snapshot: "feed-snapshot",
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
  await expect(inbox.getByRole("tab", { name: "In progress 1" })).toBeFocused();
  const panel = inbox.getByRole("tabpanel", { name: "In progress 1" });
  const row = panel.getByRole("link");
  expect(await row.boundingBox()).toEqual(await panel.boundingBox());
  if (info.project.name === "desktop") {
    await row.hover({ position: { x: 1, y: 1 } });
    await expect(row).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await page.screenshot({ path: info.outputPath("notification-hover.png") });
  }
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

for (const [surface, url] of [["workspace Inbox", "/"], ["repository PR list", "/repositories/22"]] as const) {
  test(`the ${surface} refreshes the bell when a review is queued`, async ({ page }, info) => {
    await page.clock.install();
    const { state } = await mockActivity(page);
    state.running = false;
    state.unread = false;
    await page.goto(url);
    await expect(page.getByRole("button", { name: "Notifications, 0 running, 0 unread" })).toBeVisible();
    await expect(page.getByText(surface === "workspace Inbox" ? "Your inbox is empty" : "No pull requests here yet")).toBeVisible();
    expect(state.activityGets).toBe(1);

    const pr = page.locator("[data-pull-row]").filter({ hasText: reviewTitle });
    state.reviewStatus = "queued";
    await page.clock.fastForward(5100);
    await expect(pr.getByText("Queued", { exact: true })).toBeVisible();
    const queued = page.getByRole("button", { name: "Notifications, 0 running, 1 queued, 0 unread" });
    await expect(queued).toHaveText("1");
    await expect(queued.locator(".lucide-bell")).toBeVisible();
    expect(state.activityGets).toBe(2);
    await queued.click();
    const inbox = page.getByRole("dialog", { name: "Notifications" });
    const review = inbox.getByRole("link", { name: new RegExp(reviewTitle) });
    await expect(inbox.getByRole("tab", { name: "In progress 1" })).toBeFocused();
    const title = review.getByText(`#54 ${reviewTitle}`, { exact: true });
    await expect(title).toHaveCSS("white-space", "nowrap");
    await expect(title).toHaveCSS("text-overflow", "ellipsis");
    await expect(title).toHaveAttribute("title", `#54 ${reviewTitle}`);
    const subtitleText = "Review · acme/payments-platform-integrations-and-infrastructure";
    const subtitle = review.getByText(subtitleText, { exact: true });
    await expect(subtitle).toHaveCSS("white-space", "nowrap");
    await expect(subtitle).toHaveCSS("text-overflow", "ellipsis");
    await expect(subtitle).toHaveAttribute("title", subtitleText);
    await expect(review.getByText("Queued", { exact: true })).toBeVisible();
    await expect(review).toHaveAttribute("href", reviewHref);
    await page.screenshot({ path: info.outputPath("queued-review.png") });

    await page.clock.fastForward(5100);
    await expect.poll(() => state.activityGets).toBe(3);
    state.reviewStatus = "running";
    await page.clock.fastForward(5100);
    await expect(pr.getByText("Running", { exact: true })).toBeVisible();
    const working = page.getByRole("button", { name: "Notifications, 1 running, 0 unread" });
    await expect(working).toHaveText("1");
    await expect(working.locator(".lucide-loader-circle")).toBeVisible();
    await expect(review.getByText("Running · Analysis", { exact: true })).toBeVisible();
    await expect(review).toHaveAttribute("href", reviewHref);

    state.reviewStatus = "completed";
    await page.clock.fastForward(5100);
    await expect(pr.getByText("Completed", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Notifications, 0 running, 1 unread" })).toHaveText("1");
    await expect(inbox.getByRole("tab", { name: "In progress 0" })).toBeVisible();
    await expect(review).toHaveCount(0);
    await inbox.getByRole("tab", { name: "Updates 1" }).click();
    await expect(review.getByText("Completed", { exact: true })).toBeVisible();
    await expect(review.getByText("Unread", { exact: true })).toHaveCount(1);
    await expect(review).toHaveAttribute("href", reviewHref);
  });
}

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
