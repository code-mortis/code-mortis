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
      const items = status === "all" ? tokens : tokens.filter((item) => item.status === status);
      return route.fulfill({ json: {
        items, total: items.length, page: 1, has_more: false,
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
  await page.getByRole("button", { name: "New token", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "New API token", exact: true })).toBeVisible();
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
    await expect(page.getByRole("combobox", { name: "Show", exact: false })).toHaveValue("active");
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

test("create shows the secret once, reports copy failure, and clears it on close", async ({ page }) => {
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
  const dialog = page.getByRole("dialog", { name: "Token created", exact: true });
  await expect(dialog).toBeVisible();
  const secret = await dialog.getByLabel("One-time token", { exact: true }).inputValue();
  expect(secret).toMatch(/^cm_pat_[A-Za-z0-9_-]{43}$/);
  expect(writes.find((w) => w.path === "/api-tokens" && w.method === "POST")?.body).toMatchObject({
    name: "scripts-box",
    scopes: expect.arrayContaining(["reviews:read", "reviews:run"]),
    repository_ids: [23],
    expires_in_days: 30,
  });
  await dialog.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Select the token and copy it manually");
  await expect(dialog.getByLabel("One-time token", { exact: true })).toHaveValue(secret);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await expect(dialog).toHaveCount(0);
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
  const dialog = page.getByRole("dialog", { name: "Token created", exact: true });
  await dialog.getByRole("button", { name: "Copy", exact: true }).click();
  const copied = await page.evaluate(() => document.body.dataset.copiedToken);
  expect(copied).toMatch(/^cm_pat_/);
  await expect(dialog.getByRole("status")).toContainText("Token copied.");
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.locator("body")).not.toContainText(String(copied));
});

test("stale sessions see the sign-in link while emergency revoke stays available", async ({ page }) => {
  await mockTokens(page, { reauthRequired: true });
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  const dialog = page.getByRole("dialog", { name: "New API token", exact: true });
  await expect(dialog.getByRole("alert").first()).toContainText("Sign in with GitHub again");
  await expect(dialog.getByRole("link", { name: "Sign in with GitHub", exact: true }))
    .toHaveAttribute("href", "/api/v1/auth/login?return_to=%2Fsettings%3Ftab%3Dapi-tokens");
  await expect(dialog.getByRole("button", { name: "Create token", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "New token", exact: true })).toBeFocused();
  await page.getByRole("article", { name: "API token noodle-laptop", exact: true }).getByRole("button", { name: "Revoke", exact: true }).click();
  const revoke = page.getByRole("dialog", { name: /Revoke/, exact: false });
  await expect(revoke).toContainText("“noodle-laptop”");
  await expect(revoke).toContainText("unfinished work started by this token will be stopped");
  await revoke.getByRole("button", { name: "Revoke token", exact: true }).click();
  await expect(revoke).toHaveCount(0);
  await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true })).toHaveCount(0);
  await page.getByLabel("Show", { exact: true }).selectOption("revoked");
  await expect(page.getByRole("article", { name: "API token noodle-laptop", exact: true }).getByText("Revoked", { exact: true })).toBeVisible();
});

test("lost responses keep the name for recovery instead of retrying silently", async ({ page }) => {
  await mockTokens(page, { failCreate: "network" });
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  await page.getByLabel("Token name", { exact: true }).fill("retry-box");
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New API token", exact: true });
  await expect(dialog.getByRole("alert").filter({ hasText: "may have been created" })).toContainText("“retry-box” may have been created");
  await expect(page.getByLabel("Token name", { exact: true })).toHaveValue("retry-box");
  await expect(dialog.getByRole("button", { name: "Create token", exact: true })).toBeEnabled();
});

test("validation, server, and rate-limit errors stay visible", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await openCreate(page);
  const dialog = page.getByRole("dialog", { name: "New API token", exact: true });
  await page.getByLabel("Token name", { exact: true }).fill("bad-box");
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(dialog.getByRole("alert").first()).toContainText("Select at least one repository");
  await page.route("**/api/v1/api-tokens", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 422, json: { detail: "Select at least one permission and repository." } });
    }
    return route.fallback();
  }, { times: 1 });
  await page.getByRole("checkbox", { name: /acme\/payments/ }).check();
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(dialog.getByRole("alert").first()).toContainText("Select at least one permission and repository");
  await page.route("**/api/v1/api-tokens", (route) => {
    if (route.request().method() === "POST") {
      return route.fulfill({ status: 429, headers: { "Retry-After": "30" }, json: { detail: "Too many token requests." } });
    }
    return route.fallback();
  }, { times: 1 });
  await page.getByRole("button", { name: "Create token", exact: true }).click();
  await expect(dialog.getByRole("alert").first()).toContainText("Too many token requests");
  await noOverflow(page);
});

test("keyboard users can create, dismiss, and reach revoke", async ({ page }) => {
  await mockTokens(page);
  await page.goto("/settings?tab=api-tokens");
  await page.getByRole("button", { name: "New token", exact: true }).focus();
  await expect(page.getByRole("button", { name: "New token", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "New API token", exact: true });
  await expect(dialog).toBeVisible();
  await expect(page.getByLabel("Token name", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New token", exact: true })).toBeFocused();
  const revokeButton = page.getByRole("article", { name: "API token noodle-laptop", exact: true }).getByRole("button", { name: "Revoke", exact: true });
  await revokeButton.focus();
  await page.keyboard.press("Enter");
  const revoke = page.getByRole("dialog", { name: /Revoke/, exact: false });
  await expect(revoke.getByRole("button", { name: "Revoke token", exact: true })).toBeVisible();
  await revoke.getByRole("button", { name: "Cancel", exact: true }).focus();
  await page.keyboard.press("Escape");
  await expect(revoke).toHaveCount(0);
  await expect(revokeButton).toBeFocused();
});
