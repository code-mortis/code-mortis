import { expect, test } from "@playwright/test";

for (const fail of [false, true]) test(`navbar progress tracks initial loading and settles after ${fail ? "failure" : "success"}`, async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/setup/status") return route.fulfill({ json: { configured: true } });
    if (path === "/auth/me") return route.fulfill({ json: { id: 7, login: "developer", role: "member", csrf: "test" } });
    if (path === "/repositories") {
      await held;
      return route.fulfill(fail ? { status: 503, json: { detail: "Directory temporarily unavailable." } } : { json: [] });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/repositories");
  const progress = page.getByRole("banner").getByRole("progressbar", { name: "Loading workspace" });
  await expect(progress).toBeVisible();
  await expect(page.locator('[data-slot="skeleton"]')).toHaveCount(0);
  await expect(page.getByText("Loading workspace…", { exact: true })).toHaveCount(0);
  await expect(progress).toHaveCSS("height", "2px");
  release();
  await expect(progress).toHaveCount(0);
  await expect(page.getByRole(fail ? "alert" : "heading", { name: fail ? undefined : "Connect your first repository" }).first()).toBeVisible();
});

test("cached directory polling leaves the navbar progress hidden", async ({ page }) => {
  await page.clock.install();
  let requests = 0;
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/setup/status") return route.fulfill({ json: { configured: true } });
    if (path === "/auth/me") return route.fulfill({ json: { id: 7, login: "developer", role: "member", csrf: "test" } });
    if (path === "/repositories") {
      requests++;
      if (requests > 1) await held;
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/repositories");
  await expect(page.getByRole("heading", { name: "Connect your first repository" })).toBeVisible();
  await page.clock.fastForward(5000);
  await expect.poll(() => requests).toBeGreaterThan(1);
  await expect(page.getByRole("progressbar", { name: "Loading workspace" })).toHaveCount(0);
  release();
});
