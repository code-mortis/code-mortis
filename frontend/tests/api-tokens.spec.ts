import { test, expect, type Page } from "@playwright/test";

const at = "2026-10-05T13:20:00Z";

const repositories = [
  { id: 22, full_name: "acme/payments", open_prs: 2, enabled: true, active: true },
  { id: 23, full_name: "acme/billing", open_prs: 0, enabled: true, active: true },
];

const permissions = [
  { id: "repositories:read", label: "Read repositories", description: "List repositories you can access." },
  { id: "reviews:read", label: "Read reviews", description: "Read pull requests, runs, findings, and coverage." },
  { id: "reviews:write", label: "Write reviews", description: "Resolve findings and sync review state." },
  { id: "reviews:run", label: "Run reviews", description: "Start reviews. Uses AI provider quota and can publish comments." },
  { id: "security:read", label: "Read security", description: "Read scans, reports, and findings." },
  { id: "security:write", label: "Write security", description: "Triage findings." },
  { id: "security:run", label: "Run scans", description: "Start scans. Uses provider quota." },
  { id: "connections:read", label: "Read connections", description: "List connection metadata without secrets." },
  { id: "analytics:read", label: "Read analytics", description: "Read analytics for selected repositories." },
];

type Token = {
  id: string; name: string; prefix: string; scopes: string[]; repository_ids: number[];
  created_at: string; expires_at: string; last_used_at: string | null; revoked_at: string | null; status: string;
};

function record(name: string, overrides: Partial<Token> = {}): Token {
  return {
    id: "token-1", name, prefix: "ab12cd34", scopes: ["reviews:read"], repository_ids: [22],
    created_at: at, expires_at: "2026-11-04T13:20:00Z", last_used_at: null, revoked_at: null, status: "active",
    ...overrides,
  };
}

type Writes = { method: string; path: string; body: unknown }[];

async function mockTokens(page: Page, options: {
  role?: string;
  reauthRequired?: boolean;
  failCreate?: "validation" | "rate-limit" | "network" | null;
  seed?: Token[];
} = {}) {
  const writes: Writes = [];
  let tokens: Token[] = options.seed ?? [record("noodle-laptop", { last_used_at: at })];
  let createCalls = 0;
  const fail = options.failCreate ?? null;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const method = request.method();
    if (method !== "GET") writes.push({ method, path, body: request.postDataJSON() });
    if (path === "/setup/status") return route.fulfill({ json: { configured: true, public_url: "https://reviews.example.com" } });
    if (path === "/auth/me") return route.fulfill({ json: { id: 7, login: "carlos", role: options.role ?? "admin", avatar_url: "", csrf: "csrf-from-session" } });
    if (path === "/repositories") return route.fulfill({ json: repositories });
    if (path === "/settings") return route.fulfill({ json: { github_app: { id: "1", slug: "app", install_url: "https://github.com" }, installations: [], members: [], failed_jobs: [], public_url: "https://reviews.example.com" } });
    if (path === "/connections") return route.fulfill({ json: method === "GET" ? [] : {} });
    if (path === "/providers") return route.fulfill({ json: [] });
    if (path === "/api-tokens" && method === "GET") {
      const status = url.searchParams.get("status") ?? "active";
      const items = status === "all" ? tokens : tokens.filter((item) => status === "inactive" ? item.status !== "active" : item.status === status);
      const currentPage = Number(url.searchParams.get("page") ?? "1");
      return route.fulfill({ json: {
        items: items.slice((currentPage - 1) * 10, currentPage * 10), total: items.length,
        page: currentPage, has_more: currentPage * 10 < items.length,
        active_count: tokens.filter((item) => item.status === "active").length,
        permissions, default_expiration_days: 30, max_expiration_days: 365,
        reauth_required: !!options.reauthRequired,
      } });
    }
    if (path === "/api-tokens" && method === "POST") {
      createCalls += 1;
      if (fail === "network" && createCalls === 1) return route.abort("failed");
      if (fail === "rate-limit") {
        return route.fulfill({ status: 429, headers: { "Retry-After": "30" }, json: { detail: "Too many token requests." } });
      }
      const body = request.postDataJSON() as { name: string; scopes: string[]; repository_ids: number[]; expires_in_days: number };
      if (fail === "validation" || !body.name?.trim() || !body.scopes?.length || !body.repository_ids?.length) {
        return route.fulfill({ status: 422, json: { detail: "Select at least one permission and repository." } });
      }
      const secret = `cm_pat_${"s".repeat(43)}`;
      const item = record(body.name, {
        id: `token-${tokens.length + 1}`, prefix: secret.slice(7, 15),
        scopes: body.scopes, repository_ids: body.repository_ids,
      });
      tokens = [...tokens, item];
      return route.fulfill({ status: 201, json: { token: secret, item } });
    }
    if (path.startsWith("/api-tokens/") && method === "DELETE") {
      const id = decodeURIComponent(path.split("/").at(-1)!);
      tokens = tokens.map((item) => (item.id === id ? { ...item, status: "revoked", revoked_at: at } : item));
      return route.fulfill({ status: 202, json: { revoked: true } });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected test request: ${path}` } });
  });
  return writes;
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

async function openCreate(page: Page) {
  await page.getByRole("link", { name: "New token", exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/api-tokens\/new$/);
  await expect(page.getByRole("form", { name: "New API token", exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

async function openToken(page: Page, name: string) {
  await page.getByRole("button", { name: `View API token ${name}`, exact: true }).click();
  const panel = page.getByRole("dialog", { name, exact: true });
  await expect(panel).toBeVisible();
  return panel;
}

for (const role of ["admin", "member"] as const) {
  test(`${role} sees the API tokens section with metadata and navigation`, async ({ page }, info) => {
    await mockTokens(page, { role });
    await page.goto("/settings?tab=api-tokens");
    await expect(page.getByRole("heading", { name: "API tokens", exact: true })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Breadcrumb", exact: true }).locator('[aria-current="page"]')).toHaveText("API tokens");
    await expect(page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "API tokens", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true })).toBeVisible();
    await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true }).getByText("Active", { exact: true })).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "1 active" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Show", exact: false })).toHaveCount(0);
    if (info.project.name === "desktop") {
      const heading = await page.getByRole("heading", { name: "API tokens", exact: true }).boundingBox();
      const button = await page.getByRole("link", { name: "New token", exact: true }).boundingBox();
      expect(Math.abs(button!.y - heading!.y)).toBeLessThan(2);
      expect(button!.x).toBeGreaterThan(heading!.x + heading!.width);
    }
    if (info.project.name === "mobile") {
      await page.evaluate(() => localStorage.setItem("theme", "dark"));
      await page.reload();
      await expect(page.getByRole("heading", { name: "API tokens", exact: true })).toBeVisible();
      await expect(page.locator("html")).toHaveClass(/dark/);
    }
    await noOverflow(page);
    await page.screenshot({ path: `/private/tmp/api-tokens-list-${role}-${info.project.name}.png`, fullPage: true });
  });
}

test("create shows the secret once, reports copy failure, and clears it on leaving the page", async ({ page }) => {
  const writes = await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  await expect(page.getByRole("checkbox", { name: /Read reviews/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Run reviews/ })).not.toBeChecked();
  await page.getByLabel("Token name", { exact: true }).fill("scripts-box");
  await expect(page.getByLabel("Expires in", { exact: true })).toHaveValue("30");
  await page.getByRole("checkbox", { name: /Run reviews/ }).check();
  await page.getByRole("checkbox", { name: /acme\/billing/ }).check();
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: {
    writeText: async () => { throw new Error("Clipboard denied"); },
  } }));
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await expect(panel).toBeVisible();
  const secret = await panel.getByLabel("One-time token", { exact: true }).inputValue();
  expect(secret).toMatch(/^cm_pat_[A-Za-z0-9_-]{43}$/);
  expect(writes.find((w) => w.path === "/api-tokens" && w.method === "POST")?.body).toMatchObject({
    name: "scripts-box",
    scopes: expect.arrayContaining(["reviews:read", "reviews:run"]),
    repository_ids: [23],
    expires_in_days: 30,
  });
  await panel.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("Select the token and copy it manually");
  await expect(panel.getByLabel("One-time token", { exact: true })).toHaveValue(secret);
  await panel.getByRole("button", { name: "Done", exact: true }).click();
  await expect(panel).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(secret);
  await expect(page.getByRole("article", { name: "API token scripts-box", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator("body")).not.toContainText(secret);
});

test("copy success announces without persisting the secret", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: {
    writeText: async (value: string) => { document.body.dataset.copiedToken = value; },
  } }));
  await openCreate(page);
  await page.getByLabel("Token name", { exact: true }).fill("copy-box");
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await panel.getByRole("button", { name: "Copy", exact: true }).click();
  const copied = await page.evaluate(() => document.body.dataset.copiedToken);
  expect(copied).toMatch(/^cm_pat_/);
  await expect(panel.getByRole("status")).toContainText("Token copied.");
  await panel.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.locator("body")).not.toContainText(String(copied));
});

test("stale sessions see the sign-in link while emergency revoke stays available", async ({ page }) => {
  const writes = await mockTokens(page, { reauthRequired: true });
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await expect(panel.getByRole("alert").first()).toContainText("Sign in with GitHub again");
  await expect(panel.getByRole("link", { name: "Sign in with GitHub", exact: true }))
    .toHaveAttribute("href", "/api/v1/auth/login?return_to=%2Fsettings%2Fapi-tokens%2Fnew");
  await expect(panel.getByRole("button", { name: "Create token", exact: true })).toBeDisabled();
  await panel.getByLabel("Token name", { exact: true }).fill("stale-session");
  await panel.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await panel.getByLabel("Token name", { exact: true }).press("Enter");
  await expect(panel.getByRole("alert")).toHaveCount(1);
  expect(writes).toHaveLength(0);
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("link", { name: "New token", exact: true })).toBeFocused();
  const details = await openToken(page, "noodle-laptop");
  await details.getByRole("button", { name: "Revoke", exact: true }).click();
  const revoke = page.getByRole("dialog", { name: /Revoke/, exact: false });
  await expect(revoke).toContainText("“noodle-laptop”");
  await expect(revoke).toContainText("unfinished work started by this token will be stopped");
  await revoke.getByRole("button", { name: "Revoke token", exact: true }).click();
  await expect(revoke).toHaveCount(0);
  await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "API tokens", exact: true })).toBeFocused();
  await page.locator("summary").filter({ hasText: "Inactive tokens" }).click();
  await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true }).getByText("Revoked", { exact: true })).toBeVisible();
});

test("creation disables duplicate submission and return controls while pending", async ({ page }) => {
  const writes = await mockTokens(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/api-tokens", async (route) => {
    if (route.request().method() === "POST") await gate;
    return route.fallback();
  });
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  await page.getByLabel("Token name", { exact: true }).fill("delayed-token");
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(page.getByRole("button", { name: "Creating…", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/settings\/api-tokens\/new$/);
  release();
  await expect(page.getByRole("heading", { name: "Token created", exact: true })).toBeFocused();
  await expect(page.getByLabel("One-time token", { exact: true })).toHaveValue(/^cm_pat_/);
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.getByRole("article", { name: "API token delayed-token", exact: true })).toBeVisible();
  expect(writes.filter((write) => write.method === "POST" && write.path === "/api-tokens")).toHaveLength(1);
});

test("repository names load before selected repositories are labeled unavailable", async ({ page }) => {
  await mockTokens(page, { seed: [record("loading-repos", { repository_ids: [22, 999] })] });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/repositories", async (route) => {
    await gate;
    return route.fallback();
  });
  await page.goto("/settings?tab=api-tokens");
  const token = await openToken(page, "loading-repos");
  await expect(token).toContainText("Loading repositories…");
  await expect(token).not.toContainText("unavailable");
  release();
  await expect(token).toContainText("acme/payments");
  await expect(token).toContainText("#999 unavailable");
});

test("empty tokens show creation guidance without an empty history disclosure", async ({ page }) => {
  await mockTokens(page, { seed: [] });
  await page.goto("/settings?tab=api-tokens");
  await expect(page.getByText("No active tokens", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "New token", exact: true })).toBeEnabled();
  await expect(page.locator("summary").filter({ hasText: "Inactive tokens" })).toHaveCount(0);
});

test("revoked and expired tokens stay in collapsed history like MCP grants", async ({ page }, info) => {
  await mockTokens(page, { seed: [
    record("current-token"),
    record("revoked-token", { id: "token-2", status: "revoked", revoked_at: at }),
    record("expired-token", { id: "token-3", status: "expired", expires_at: at }),
  ] });
  await page.goto("/settings?tab=api-tokens");
  const history = page.locator("details").filter({ has: page.locator("summary").filter({ hasText: "Inactive tokens" }) });
  await expect(history.locator("summary")).toHaveText("Inactive tokens 2");
  await expect(history).not.toHaveAttribute("open", "");
  await expect(page.getByRole("article", { name: "API token current-token", exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "API token revoked-token", exact: true })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/api-tokens-history-collapsed-${info.project.name}.png`, fullPage: true });
  await history.locator("summary").focus();
  await page.keyboard.press("Enter");
  await expect(history.getByRole("article")).toHaveCount(2);
  await expect(history.getByText("Revoked", { exact: true })).toBeVisible();
  await expect(history.getByText("Expired", { exact: true })).toBeVisible();
  await expect(history.getByRole("button", { name: "Revoke", exact: true })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/api-tokens-history-expanded-${info.project.name}.png`, fullPage: true });
  for (const name of ["revoked-token", "expired-token"]) {
    const details = await openToken(page, name);
    await expect(details.getByRole("button", { name: "Revoke", exact: true })).toHaveCount(0);
    await expect(details).toContainText("reviews:read");
    await page.keyboard.press("Escape");
  }
});

test("active tokens and inactive history paginate independently", async ({ page }) => {
  await mockTokens(page, { seed: [
    ...Array.from({ length: 11 }, (_, i) => record(`active-${i}`, { id: `active-${i}` })),
    ...Array.from({ length: 11 }, (_, i) => record(`inactive-${i}`, { id: `inactive-${i}`, status: i % 2 ? "revoked" : "expired" })),
  ] });
  await page.goto("/settings?tab=api-tokens");
  const activePagination = page.getByRole("navigation", { name: "Active tokens pagination", exact: true });
  await activePagination.getByRole("button", { name: "Next tokens" }).click();
  await expect(activePagination).toContainText("Page 2 · 11 total");
  await expect(page.getByRole("article", { name: "API token active-10", exact: true })).toBeVisible();
  await page.locator("summary").filter({ hasText: "Inactive tokens" }).click();
  const inactivePagination = page.getByRole("navigation", { name: "Inactive tokens pagination", exact: true });
  await inactivePagination.getByRole("button", { name: "Next tokens" }).click();
  await expect(inactivePagination).toContainText("Page 2 · 11 total");
  await expect(page.getByRole("article", { name: "API token inactive-10", exact: true })).toBeVisible();
  await expect(activePagination).toContainText("Page 2 · 11 total");
  await inactivePagination.getByRole("button", { name: "Previous tokens" }).click();
  await expect(inactivePagination).toContainText("Page 1 · 11 total");
});

test("lost responses keep the name for recovery instead of retrying silently", async ({ page }) => {
  await mockTokens(page, { failCreate: "network" });
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  await page.getByLabel("Token name", { exact: true }).fill("retry-box");
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await expect(panel.getByRole("alert").filter({ hasText: "may have been created" })).toContainText("“retry-box” may have been created");
  await expect(page.getByLabel("Token name", { exact: true })).toHaveValue("retry-box");
  await expect(panel.getByRole("button", { name: "Create token", exact: true })).toBeEnabled();
});

test("validation, server, and rate-limit errors stay visible", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await page.getByLabel("Token name", { exact: true }).fill("bad-box");
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(panel.getByRole("alert").first()).toContainText("Select at least one repository");
  await expect(panel.getByRole("alert").first()).toBeInViewport();
  await page.route("**/api/v1/api-tokens", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 422, json: { detail: "Select at least one permission and repository." } });
    }
    return route.fallback();
  }, { times: 1 });
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(panel.getByRole("alert").first()).toContainText("Select at least one permission and repository");
  await page.route("**/api/v1/api-tokens", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 429, headers: { "Retry-After": "30" }, json: { detail: "Too many token requests." } });
    }
    return route.fallback();
  }, { times: 1 });
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(panel.getByRole("alert").first()).toContainText("Too many token requests");
  await noOverflow(page);
});

test("keyboard users can open creation, cancel, and reach revoke", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await page.getByRole("link", { name: "New token", exact: true }).focus();
  await expect(page.getByRole("link", { name: "New token", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("region", { name: "Create API token", exact: true });
  await expect(panel).toBeVisible();
  await expect(page.getByRole("heading", { name: "New API token", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "Cancel", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole("link", { name: "New token", exact: true })).toBeFocused();
  const trigger = page.getByRole("button", { name: "View API token noodle-laptop", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const details = page.getByRole("dialog", { name: "noodle-laptop", exact: true });
  await expect(details).toBeVisible();
  const revokeButton = details.getByRole("button", { name: "Revoke", exact: true });
  await revokeButton.focus();
  await page.keyboard.press("Enter");
  const revoke = page.getByRole("dialog", { name: /Revoke/, exact: false });
  await expect(revoke.getByRole("button", { name: "Revoke token", exact: true })).toBeVisible();
  await revoke.getByRole("button", { name: "Cancel", exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(revoke).toHaveCount(0);
  await expect(revokeButton).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(details).toHaveCount(0);
  await expect(trigger).toBeFocused();
});


test("compact token cards open complete details without overflowing", async ({ page }, info) => {
  const name = `Script${"WithALongName".repeat(6)}`;
  const repository = `acme/${"long-repository-name-".repeat(5)}service`;
  const writes = await mockTokens(page, { seed: [record(name, {
    scopes: permissions.map((permission) => permission.id), repository_ids: [22, 23, 999], last_used_at: at,
  })] });
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [repositories[0], { ...repositories[1], full_name: repository }] }));
  await page.goto("/settings?tab=api-tokens");
  const card = page.getByRole("article", { name: `API token ${name}`, exact: true });
  await expect(card).toContainText("3 repositories");
  await expect(card).toContainText("Expires");
  await expect(card).not.toContainText("reviews:read");
  await expect(card).toContainText("ab12cd34");
  await expect(card).not.toContainText(repository);
  await card.click({ position: { x: 12, y: 12 } });
  const details = page.getByRole("dialog", { name, exact: true });
  await expect(details.getByRole("heading", { name, exact: true })).toBeFocused();
  for (const permission of permissions) await expect(details.getByText(permission.id, { exact: true })).toBeVisible();
  await expect(details).toContainText(repository);
  await expect(details).toContainText("#999 unavailable");
  await expect(details).toContainText("ab12cd34");
  await expect(details).toContainText("Last used");
  await expect(details).toContainText("approximate");
  for (const width of info.project.name === "desktop" ? [1277, 768] : [390, 320]) {
    await page.setViewportSize({ width, height: 905 });
    await noOverflow(page);
    expect(await details.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await page.screenshot({ path: `/private/tmp/api-token-details-${width}.png` });
  }
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: `View API token ${name}`, exact: true })).toBeFocused();
  expect(writes).toEqual([]);
});


for (const role of ["admin", "member"] as const) {
  test(`${role} can open the creation URL directly with settings navigation and breadcrumbs`, async ({ page }, info) => {
    const writes = await mockTokens(page, { role });
    await page.goto("/settings/api-tokens/new");
    await expect(page.getByRole("form", { name: "New API token", exact: true })).toBeVisible();
    const navigation = page.getByRole("navigation", { name: "Settings sections" });
    await expect(navigation.getByRole("link", { name: "API tokens", exact: true })).toHaveAttribute("aria-current", "page");
    const crumbs = page.getByRole("navigation", { name: "Breadcrumb", exact: true });
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText("New token");
    if (info.project.name === "desktop") {
      await expect(crumbs.getByRole("link", { name: "API tokens", exact: true })).toHaveAttribute("href", "/settings?tab=api-tokens");
    }
    await expect(page.getByRole("dialog")).toHaveCount(0);
    for (const theme of ["light", "dark"]) {
      await page.evaluate((value) => localStorage.setItem("theme", value), theme);
      await page.reload();
      await expect(page.getByRole("form", { name: "New API token", exact: true })).toBeVisible();
      await noOverflow(page);
      await page.screenshot({ path: `/private/tmp/token-create-${role}-${info.project.name}-${theme}.png`, fullPage: true });
    }
    await navigation.getByRole("link", { name: "API tokens", exact: true }).click();
    await expect(page).toHaveURL(/\/settings\?tab=api-tokens$/);
    expect(writes).toEqual([]);
  });
}

test("creation metadata errors offer retry without showing an unusable form", async ({ page }) => {
  await mockTokens(page);
  let fail = true;
  await page.route("**/api/v1/api-tokens?**", (route) => fail
    ? route.fulfill({ status: 503, json: { detail: "Token settings unavailable" } }) : route.fallback());
  await page.goto("/settings/api-tokens/new");
  await expect(page.getByRole("alert")).toContainText("Token settings unavailable");
  await expect(page.getByRole("form", { name: "New API token", exact: true })).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "Retry token settings", exact: true }).click();
  await expect(page.getByRole("form", { name: "New API token", exact: true })).toBeVisible();
});

test("browser back and reload never restore a created secret", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  await page.getByLabel("Token name", { exact: true }).fill("one-time");
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  const secret = await page.getByLabel("One-time token", { exact: true }).inputValue();
  expect(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage, state: history.state }))).not.toContain(secret);
  expect(page.url()).not.toContain(secret);
  await page.goBack();
  await expect(page.getByRole("article", { name: "API token one-time", exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByRole("form", { name: "New API token", exact: true })).toBeVisible();
  await expect(page.getByLabel("One-time token", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("One-time token", { exact: true })).toHaveCount(0);
});
