import { expect, test, type Page } from "@playwright/test";

async function mockPullActions(page: Page, options: { write?: boolean; admin?: boolean; paused?: boolean; closed?: boolean; ignored?: boolean } = {}) {
  const calls: { path: string; method: string; body: unknown }[] = [];
  const controls = { ignored: options.ignored ?? false, active: 2, latestRun: false, failAction: false, failDetail: false, write: options.write !== false };
  const repo = {
    id: 22, installation_id: 10, full_name: "acme/project", private: false, active: true,
    enabled: !options.paused, connection_id: "connection", open_prs: 2, has_running_review: true,
    settings: { automatic: true, include_bot_prs: false, language: "English", max_files: 40 },
  };
  function pull(id: string) {
    return {
      id, repository_id: 22, repository_name: repo.full_name, number: id === "start" ? 42 : 43,
      title: id === "start" ? "Validate provider responses" : "Preserve review history",
      author: "developer", state: options.closed ? "closed" : "open", draft: false, fork: false,
      ignored: id === "start" && controls.ignored, head_ref: "feature", head_sha: "a".repeat(40), base_sha: "b".repeat(40),
      created_at: "2026-10-17T12:00:00Z", updated_at: "2026-10-17T12:00:00Z", open_findings: 0,
      url: `https://github.com/acme/project/pull/${id === "start" ? 42 : 43}`,
      review_status: id === "running" && controls.active ? "running" : "not_reviewed",
      has_running_review: id === "running" && !!controls.active, run_id: null, publication: null,
      publication_error: null, latest_run: null,
    };
  }
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = request.method();
    calls.push({ path, method, body: method === "GET" ? null : request.postDataJSON() });
    if (controls.failAction && method !== "GET") return route.fulfill({ status: 403, json: { detail: "Write access was removed. Refresh and try again." } });
    let data: unknown = [];
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: 7, login: "developer", role: options.admin === false ? "member" : "admin", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") data = [repo];
    else if (path === "/pulls/start/ignore") {
      controls.ignored = request.postDataJSON().ignored;
      data = pull("start");
    } else if (path === "/pulls/start/runs" && method === "POST") data = { id: "new-run" };
    else if (path === "/pulls/running/cancel") { controls.active = 0; data = []; }
    else if (/^\/pulls\/(start|running)$/.test(path)) {
      if (controls.failDetail) return route.fulfill({ status: 502, json: { detail: "Review actions unavailable. Try again." } });
      const pr = pull(path.split("/").at(-1)!);
      data = { pull_request: controls.latestRun ? { ...pr, latest_run: { id: "latest-run" } } : pr, repository: repo, run_count: 0, has_active_run: pr.id === "running" && !!controls.active,
        cancellable_run_count: pr.id === "running" ? controls.active : 0, latest_observed_run_id: null, can_write: controls.write };
    } else if (path.endsWith("/findings")) data = { groups: [], total: 0, registered_total: 0, status_counts: {}, category_counts: {}, page_size: 10 };
    else if (path === "/pulls" || path === "/repositories/22/pulls") {
      const visibility = url.searchParams.get("visibility") ?? "visible";
      const items = [pull("start"), pull("running")].filter((pr) => visibility === "all" || pr.ignored === (visibility === "ignored"));
      data = {
        groups: [{ group: url.searchParams.get("group") ?? "active", items, total: items.length, page: 1, has_more: false }],
        total: items.length, registered_total: items.length, repository_count: 1,
        repositories: [{ id: 22, full_name: repo.full_name }], authors: ["developer"],
        state_counts: { open: options.closed ? 0 : items.length, merged: 0, closed: options.closed ? items.length : 0 }, page_size: 10,
      };
    }
    await route.fulfill({ json: data });
  });
  return { calls, controls };
}

for (const surface of ["/", "/repositories/22"]) {
  test(`PR menus load on demand and start with the default connection on ${surface}`, async ({ page }, info) => {
    const { calls, controls } = await mockPullActions(page);
    controls.latestRun = true;
    await page.goto(surface === "/" ? surface : `${surface}?q=provider&state=all`);
    const trigger = page.getByRole("button", { name: "Actions for #42: Validate provider responses" });
    await expect(trigger).toBeVisible();
    expect(calls.filter((call) => call.path === "/pulls/start")).toHaveLength(0);
    await trigger.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toBeEnabled();
    await expect(page.getByRole("menuitem", { name: "Stop review", exact: true })).toHaveCount(0);
    await expect(page.getByRole("menu").locator('[role="menuitem"], [role="separator"]')).toHaveText([
      "Start review", "",
      "View latest review", "View pull request", "Open on GitHub", "Repository settings", "",
      "Ignore pull request",
    ]);
    await expect(page.getByRole("menuitem", { name: "View latest review", exact: true })).toHaveAttribute("href", "/runs/latest-run");
    await expect(page.getByRole("menuitem", { name: "Ignore pull request", exact: true })).toBeInViewport({ ratio: 1 });
    await expect(page).not.toHaveURL(/\/pulls\//);
    await expect(page.getByRole("menuitem", { name: "Open on GitHub" })).toHaveAttribute("href", "https://github.com/acme/project/pull/42");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/private/tmp/pr-actions-${surface === "/" ? "inbox" : "repository"}-${info.project.name}.png` });
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    await trigger.click();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toBeEnabled();
    await page.screenshot({ path: `/private/tmp/pr-actions-${surface === "/" ? "inbox" : "repository"}-${info.project.name}-dark.png` });
    await page.getByRole("menuitem", { name: "Start review", exact: true }).click();
    await expect(page.getByText("Review started", { exact: true })).toBeVisible();
    expect(calls.find((call) => call.method === "POST" && call.path === "/pulls/start/runs")?.body).toEqual({});
    await expect(page.getByRole("button", { name: "View review", exact: true })).toBeVisible();
    await expect(page).not.toHaveURL(/\/runs\//);
    await trigger.click();
    await page.getByRole("menuitem", { name: "Repository settings", exact: true }).click();
    await expect(page).toHaveURL(/\/repositories\/22\/settings/);
    if (surface !== "/") expect(new URL(page.url()).searchParams.get("q")).toBe("provider");
  });

  test(`PR menus stop all reviews, ignore, undo, and restore on ${surface}`, async ({ page }, info) => {
    const { calls, controls } = await mockPullActions(page);
    await page.goto(surface);
    const start = page.getByRole("button", { name: "Actions for #42: Validate provider responses" });
    const running = page.getByRole("button", { name: "Actions for #43: Preserve review history" });
    await running.click();
    await expect(page.getByRole("menuitem", { name: "Stop review", exact: true })).toBeEnabled();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toHaveCount(0);
    await page.screenshot({ path: `/private/tmp/pr-actions-running-${surface === "/" ? "inbox" : "repository"}-${info.project.name}.png` });
    await page.getByRole("menuitem", { name: "Stop review", exact: true }).click();
    await expect(page.getByText("Reviews stopped", { exact: true })).toBeVisible();
    expect(controls.active).toBe(0);
    await running.click();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toBeEnabled();
    await expect(page.getByRole("menuitem", { name: "Stop review", exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
    await start.click();
    await page.getByRole("menuitem", { name: "Ignore pull request", exact: true }).click();
    const ignoreDialog = page.getByRole("dialog", { name: "Ignore pull request #42?", exact: true });
    await expect(ignoreDialog).toBeVisible();
    await expect(ignoreDialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    expect(calls.filter((call) => call.path === "/pulls/start/ignore")).toHaveLength(0);
    expect(controls.ignored).toBe(false);
    await ignoreDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(ignoreDialog).toHaveCount(0);
    await expect(start).toBeFocused();
    await start.click();
    await page.getByRole("menuitem", { name: "Ignore pull request", exact: true }).focus();
    await page.keyboard.press("Enter");
    await expect(ignoreDialog).toBeVisible();
    await page.screenshot({ path: `/private/tmp/pr-ignore-confirm-${surface === "/" ? "inbox" : "repository"}-${info.project.name}.png` });
    await ignoreDialog.getByRole("button", { name: "Ignore pull request", exact: true }).click();
    await expect(start).toHaveCount(0);
    await expect(running).toBeFocused();
    await page.getByRole("button", { name: "Undo", exact: true }).click();
    await expect(start).toBeVisible();
    await start.click();
    await page.getByRole("menuitem", { name: "Ignore pull request", exact: true }).click();
    await ignoreDialog.getByRole("button", { name: "Ignore pull request", exact: true }).click();
    await expect(start).toHaveCount(0);
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("menuitem", { name: "Visibility", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: "Visible", exact: true })).toBeChecked();
    await page.getByRole("checkbox", { name: "Ignored", exact: true }).click();
    await page.keyboard.press("Escape");
    await expect(page).toHaveURL(/visibility=ignored/);
    await expect(start).toBeVisible();
    await start.click();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByText("Restore this pull request before reviewing it.", { exact: true })).toHaveCount(0);
    await page.getByRole("menuitem", { name: "Restore pull request", exact: true }).click();
    const restoreDialog = page.getByRole("dialog", { name: "Restore pull request #42?", exact: true });
    await expect(restoreDialog).toBeVisible();
    expect(calls.filter((call) => call.path === "/pulls/start/ignore")).toHaveLength(3);
    await page.keyboard.press("Escape");
    await expect(restoreDialog).toHaveCount(0);
    await expect(start).toBeFocused();
    await start.click();
    await page.getByRole("menuitem", { name: "Restore pull request", exact: true }).click();
    await restoreDialog.getByRole("button", { name: "Restore pull request", exact: true }).click();
    await expect(start).toHaveCount(0);
    await expect(page.locator("[data-pull-list-focus]")).toBeFocused();
    expect(calls.filter((call) => call.path === "/pulls/start/ignore").map((call) => call.body)).toEqual([
      { ignored: true }, { ignored: false }, { ignored: true }, { ignored: false },
    ]);
  });
}

for (const id of ["start", "running"]) {
  test(`PR menus keep cached ${id} actions visible while refetching`, async ({ page }) => {
    await mockPullActions(page);
    let finish!: () => void;
    let pending = new Promise<void>((resolve) => { finish = resolve; });
    await page.route(`**/api/v1/pulls/${id}`, async (route) => {
      await pending;
      await route.fallback();
    });
    await page.goto("/");
    const trigger = page.getByRole("button", { name: id === "start" ? /Actions for #42:/ : /Actions for #43:/ });
    await trigger.click();
    await expect(page.getByRole("menu").getByRole("status")).toHaveText("Loading review actions…");
    await expect(page.getByRole("menuitem", { name: "Ignore pull request" })).toHaveCount(0);
    finish();
    const reviewAction = page.getByRole("menuitem", { name: id === "start" ? "Start review" : "Stop review", exact: true });
    await expect(reviewAction).toBeEnabled();
    pending = new Promise<void>((resolve) => { finish = resolve; });
    await reviewAction.click();
    await expect(page.getByText(id === "start" ? "Review started" : "Reviews stopped", { exact: true })).toBeVisible();
    const refetch = page.waitForRequest(`**/api/v1/pulls/${id}`);
    await trigger.click();
    await refetch;
    await expect(reviewAction).toBeEnabled();
    await expect(page.getByRole("menuitem", { name: "Ignore pull request" })).toBeEnabled();
    await expect(page.getByText("Loading review actions…", { exact: true })).toHaveCount(0);
    finish();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toBeEnabled();
  });
}

test("PR menus handle read-only permissions, stale authorization, and request failures", async ({ page }) => {
  const { controls } = await mockPullActions(page, { write: false, admin: false });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: /Actions for #42:/ });
  controls.failDetail = true;
  await trigger.click();
  await expect(page.getByRole("alert")).toContainText("Review actions unavailable");
  controls.failDetail = false;
  await page.getByRole("menuitem", { name: "Retry actions" }).click();
  await expect(page.getByText("You have read-only access to this pull request.")).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Start review" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "Repository settings" })).toHaveCount(0);
  await expect(page.getByRole("menu").getByRole("separator")).toHaveCount(0);
  await page.keyboard.press("Escape");
  controls.write = true;
  await page.reload();
  await trigger.click();
  await expect(page.getByRole("menuitem", { name: "Ignore pull request" })).toBeEnabled();
  controls.failAction = true;
  await page.getByRole("menuitem", { name: "Ignore pull request" }).click();
  const dialog = page.getByRole("dialog", { name: "Ignore pull request #42?", exact: true });
  await dialog.getByRole("button", { name: "Ignore pull request", exact: true }).click();
  await expect(page.getByText("Write access was removed. Refresh and try again.")).toBeVisible();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(trigger).toBeFocused();
  await expect(trigger).toBeVisible();
});

test("ignored PR details confirm restore and keep review controls disabled", async ({ page }, info) => {
  const { calls, controls } = await mockPullActions(page, { ignored: true });
  await page.goto("/pulls/start");
  await expect(page.getByRole("region", { name: "Ignored pull request" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run review", exact: true })).toBeDisabled();
  const reason = "Reviews are stopped while this pull request is ignored. Restore it to run reviews.";
  await expect(page.getByText(reason, { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run review", exact: true })).toHaveAccessibleDescription(reason);
  await expect(page.getByRole("combobox", { name: "Review connection" })).toBeDisabled();
  await expect(page.getByRole("combobox", { name: "Review connection" })).toHaveAccessibleDescription(reason);
  await page.screenshot({ path: `/private/tmp/pr-ignored-detail-${info.project.name}.png` });
  await expect(page.getByText("Restore this pull request before reviewing it.", { exact: true })).toHaveCount(0);
  const trigger = page.getByRole("region", { name: "Ignored pull request" }).getByRole("button", { name: "Restore pull request", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Restore pull request #42?", exact: true });
  await expect(dialog).toBeVisible();
  expect(calls.filter((call) => call.path === "/pulls/start/ignore")).toHaveLength(0);
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: `/private/tmp/pr-restore-confirm-detail-${info.project.name}.png` });
  controls.failAction = true;
  await dialog.getByRole("button", { name: "Restore pull request", exact: true }).click();
  await expect(dialog).toContainText("Write access was removed. Refresh and try again.");
  expect(controls.ignored).toBe(true);
  controls.failAction = false;
  await dialog.getByRole("button", { name: "Restore pull request", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Ignored pull request" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "#42 Validate provider responses", exact: true })).toBeFocused();
  await expect(page.getByRole("button", { name: "Run review", exact: true })).toBeEnabled();
  await expect(page.getByText(reason, { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run review", exact: true })).not.toHaveAttribute("aria-describedby");
});

test("PR visibility confirmation cannot close or submit twice while saving", async ({ page }) => {
  const { calls, controls } = await mockPullActions(page);
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/v1/pulls/start/ignore", async (route) => {
    calls.push({ path: "/pulls/start/ignore", method: "PUT", body: route.request().postDataJSON() });
    await pending;
    controls.ignored = true;
    await route.fulfill({ json: {} });
  });
  await page.goto("/");
  const trigger = page.getByRole("button", { name: /Actions for #42:/ });
  await trigger.click();
  await page.getByRole("menuitem", { name: "Ignore pull request", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Ignore pull request #42?", exact: true });
  await dialog.getByRole("button", { name: "Ignore pull request", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Ignoring…", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  expect(calls.filter((call) => call.path === "/pulls/start/ignore")).toHaveLength(1);
  finish();
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toHaveCount(0);
});

for (const options of [{ paused: true }, { closed: true }]) {
  test(`PR menus explain disabled starts for ${options.paused ? "paused repositories" : "closed PRs"}`, async ({ page }) => {
    await mockPullActions(page, options);
    await page.goto("/?state=all");
    await page.getByRole("button", { name: /Actions for #42:/ }).click();
    await expect(page.getByRole("menuitem", { name: "Start review", exact: true })).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByText(options.paused ? "Reviews are paused for this repository." : "Reviews can only run on open pull requests.")).toBeVisible();
  });
}
