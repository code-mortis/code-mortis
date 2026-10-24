import { test, expect, type Page } from "@playwright/test";

const at = "2026-10-05T13:20:00Z";

const repositories = [
  { id: 22, full_name: "acme/payments", open_prs: 2, enabled: true, active: true },
  { id: 23, full_name: "acme/billing", open_prs: 0, enabled: true, active: true },
];

const settings = {
  reviews_enabled: true,
  security_enabled: false,
  management_enabled: false,
  allowed_client_domains: ["agent.example.com"],
  public_url: "https://reviews.example.com",
  can_manage: true,
};

const clients = [
  { client_id: "my-agent", client_name: "My agent", redirect_uris: ["https://agent.example.com/callback"] },
];

const grants = [
  {
    id: "grant-1", client_id: "my-agent", client_name: "My agent", domain: "reviews",
    scopes: ["reviews:read", "reviews:run"], repository_ids: [22],
    created_at: at, expires_at: "2026-11-04T13:20:00Z", last_used_at: at, revoked_at: null,
  },
];

const authorizePreview = {
  request_id: "req-1",
  client_name: "My agent",
  client_id: "my-agent",
  redirect_uri: "https://agent.example.com/callback",
  domain: "reviews",
  scopes: [
    { name: "reviews:read", label: "Read reviews", description: "List repositories, runs, findings and coverage." },
    { name: "reviews:run", label: "Run reviews", description: "Start, resume or stop reviews with the selected connection." },
  ],
  repositories: [
    { id: 22, full_name: "acme/payments" },
    { id: 23, full_name: "acme/billing" },
  ],
  expires_at: "2026-10-05T13:30:00Z",
};

const approvalDetail = {
  id: "approval-1",
  tool: "security_create_issue",
  domain: "security",
  summary: "Create a GitHub issue for finding finding-9 in acme/payments.",
  arguments: { repository_id: 22, finding_id: "finding-9", title: "Fix exposed secret", visibility: "public" },
  expires_at: "2026-10-05T14:20:00Z",
  approved_at: null,
  consumed_at: null,
};

type Writes = { method: string; path: string; body: unknown }[];

async function mockMcp(page: Page, options: {
  role?: string;
  failFirstSettings?: boolean;
  empty?: boolean;
  settingsOverride?: Record<string, unknown>;
} = {}) {
  const writes: Writes = [];
  let currentClients = options.empty ? [] : structuredClone(clients);
  type Grant = {
    id: string; client_id: string; client_name: string; domain: string; scopes: string[];
    repository_ids: number[]; created_at: string; expires_at: string; last_used_at: string | null; revoked_at: string | null;
  };
  let currentGrants: Grant[] = options.empty ? [] : structuredClone(grants);
  let currentSettings = { ...settings, ...(options.settingsOverride ?? {}) };
  let settingsCalls = 0;
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
    if (path === "/mcp/settings") {
      if (method === "GET") {
        settingsCalls += 1;
        if (options.failFirstSettings && settingsCalls === 1) return route.fulfill({ status: 503, json: { detail: "MCP unavailable." } });
        return route.fulfill({ json: currentSettings });
      }
      currentSettings = { ...currentSettings, ...(request.postDataJSON() as object) };
      return route.fulfill({ json: currentSettings });
    }
    if (path === "/mcp/clients") {
      if (method === "GET") return route.fulfill({ json: currentClients });
      const body = request.postDataJSON() as { client_id: string; client_name: string; redirect_uris: string[] };
      currentClients = [...currentClients, body];
      return route.fulfill({ json: body });
    }
    if (path.startsWith("/mcp/clients/")) {
      const id = decodeURIComponent(path.split("/").at(-1)!);
      currentClients = currentClients.filter((c) => c.client_id !== id);
      currentGrants = currentGrants.filter((g) => g.client_id !== id);
      return route.fulfill({ json: { ok: true } });
    }
    if (path === "/mcp/grants") return route.fulfill({ json: currentGrants });
    if (path.startsWith("/mcp/grants/")) {
      const id = decodeURIComponent(path.split("/").at(-1)!);
      currentGrants = currentGrants.map((g) => (g.id === id ? { ...g, revoked_at: at } : g));
      return route.fulfill({ json: { ok: true } });
    }
    if (path === "/mcp/authorize" && method === "GET") {
      if (url.searchParams.get("request_id") !== "req-1") return route.fulfill({ status: 404, json: { detail: "Authorization request expired. Return to the agent and start again." } });
      return route.fulfill({ json: authorizePreview });
    }
    if (path === "/mcp/authorize") {
      const body = request.postDataJSON() as { approve: boolean };
      return route.fulfill({ json: { redirect_url: body.approve ? "/settings?tab=mcp&authorized=1" : "/settings?tab=mcp&denied=1" } });
    }
    if (path === "/mcp/approvals/approval-1" && method === "GET") return route.fulfill({ json: approvalDetail });
    if (path === "/mcp/approvals/approval-1") {
      const body = request.postDataJSON() as { approve: boolean };
      return route.fulfill({ json: { approved: body.approve } });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected test request: ${path}` } });
  });
  return writes;
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test("admin toggles domains, registers a client, and revokes a grant", async ({ page }, info) => {
  const writes = await mockMcp(page);
  await page.goto("/settings?tab=mcp");
  await expect(page.getByRole("heading", { name: "MCP access", exact: true })).toBeVisible();
  await expect(page.getByLabel("Security MCP", { exact: true })).not.toBeChecked();
  await expect(page.getByLabel("Reviews MCP", { exact: true })).toBeChecked();
  await expect(page.getByRole("form", { name: "Pre-register a client" })).not.toBeVisible();
  await expect(page.getByLabel("Reviews MCP endpoint")).toHaveCount(0);
  await expect(page.getByLabel("Security MCP endpoint")).toHaveCount(0);
  await expect(page.getByLabel("Allow management through MCP", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Allowed client domains")).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: "Advanced settings" })).toHaveCount(0);
  const registration = page.locator("summary").filter({ hasText: "Pre-registered clients" });
  const connected = page.getByRole("heading", { name: "Connected grants", exact: true });
  const typography = (element: Element) => {
    const css = getComputedStyle(element);
    return [css.fontFamily, css.fontSize, css.fontWeight, css.color];
  };
  expect(await registration.evaluate(typography)).toEqual(await connected.evaluate(typography));
  expect((await registration.boundingBox())!.y).toBeGreaterThan((await connected.boundingBox())!.y);

  await page.getByLabel("Security MCP", { exact: true }).click();
  await expect(page.getByText("Adds administration tools to Reviews and Security", { exact: false })).toBeVisible();
  await page.getByLabel("Allowed client domains").fill("agent.example.com\npartner.example.org");
  await page.getByRole("button", { name: "Save MCP settings", exact: true }).click();
  await expect(page.getByText("MCP settings saved.")).toBeVisible();
  expect(writes.find((w) => w.path === "/mcp/settings" && w.method === "PUT")?.body).toMatchObject({
    reviews_enabled: true, security_enabled: true, allowed_client_domains: ["agent.example.com", "partner.example.org"],
  });

  await expect(page.getByRole("article", { name: "My agent reviews grant" }).getByText("my-agent", { exact: true })).toBeVisible();
  await page.locator("summary").filter({ hasText: "Pre-registered clients" }).click();
  await expect(page.getByRole("form", { name: "Pre-register a client" })).toBeVisible();
  await page.getByLabel("Client ID").fill("second-agent");
  await page.getByLabel("Display name").fill("Second agent");
  await page.getByLabel(/Redirect URIs/).fill("https://second.example.com/callback");
  await page.getByRole("button", { name: "Register client", exact: true }).click();
  await expect(page.getByText("Client registered.")).toBeVisible();
  await expect(page.getByText("second-agent", { exact: true })).toBeVisible();
  expect(writes.find((w) => w.path === "/mcp/clients" && w.method === "POST")?.body).toMatchObject({ client_id: "second-agent" });
  await page.locator("summary").filter({ hasText: "Pre-registered clients" }).click();
  await expect(page.getByRole("form", { name: "Pre-register a client" })).not.toBeVisible();

  await page.getByRole("article", { name: "My agent reviews grant" }).getByRole("button", { name: "Revoke", exact: true }).click();
  await expect(page.getByText("Grant revoked.")).toBeVisible();
  await expect(page.getByRole("article", { name: "My agent reviews grant" }).getByText("Revoked")).toBeVisible();
  expect(writes.some((w) => w.path === "/mcp/grants/grant-1" && w.method === "DELETE")).toBe(true);

  if (info.project.name === "desktop") {
    await page.getByRole("button", { name: "Switch to dark mode" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
  }
  await noOverflow(page);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `/private/tmp/mcp-settings-viewport-${info.project.name}.png` });
  await page.screenshot({ path: `/private/tmp/mcp-settings-${info.project.name}.png`, fullPage: true });
});

test("member sees instance status and grants without admin switches", async ({ page }, info) => {
  await mockMcp(page, { role: "member", settingsOverride: { can_manage: false } });
  await page.goto("/settings?tab=mcp");
  await expect(page.getByRole("heading", { name: "MCP access", exact: true })).toBeVisible();
  await expect(page.getByText(/Reviews MCP is on, security MCP is off/)).toBeVisible();
  await expect(page.getByLabel("Reviews MCP", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Pre-registered clients")).toHaveCount(0);
  await expect(page.getByLabel("Reviews MCP endpoint")).toHaveCount(0);
  await expect(page.getByLabel("Security MCP endpoint")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reviews installation instructions", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Security installation instructions", exact: true })).toBeVisible();
  await expect(page.locator("summary").filter({ hasText: "Advanced settings" })).toHaveCount(0);
  await expect(page.getByRole("article", { name: "My agent reviews grant" })).toContainText("acme/payments");
  await expect(page.getByRole("navigation", { name: "Breadcrumb", exact: true }).locator('[aria-current="page"]')).toHaveText("MCP access");
  if (info.project.name === "mobile") {
    await page.evaluate(() => localStorage.setItem("theme", "dark"));
    await page.reload();
    await expect(page.getByRole("heading", { name: "MCP access", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveClass(/dark/);
  }
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/mcp-settings-member-${info.project.name}.png`, fullPage: true });
});

test("settings errors offer retry and empty grants stay readable", async ({ page }) => {
  await mockMcp(page, { failFirstSettings: true, empty: true });
  await page.goto("/settings?tab=mcp");
  await expect(page.getByRole("alert").first()).toBeVisible();
  await page.getByRole("button", { name: "Retry MCP settings", exact: true }).click();
  await expect(page.getByLabel("Reviews MCP", { exact: true })).toBeVisible();
  await page.locator("summary").filter({ hasText: "Pre-registered clients" }).click();
  await expect(page.getByText("No registered clients.", { exact: false })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No connected agents", exact: true })).toBeVisible();
});

for (const domain of ["reviews", "security"] as const) {
  test(`${domain} connection copies its configured endpoint and installation prompt`, async ({ page }, info) => {
    const origin = "https://code-mortis.my-company.example";
    const writes = await mockMcp(page, { settingsOverride: { public_url: `${origin}/` } });
    await page.goto("/settings?tab=mcp");
    const label = domain === "reviews" ? "Reviews" : "Security";
    const endpoint = `${origin}/mcp/${domain}`;
    await expect(page.getByLabel(`${label} MCP endpoint`)).toHaveCount(0);
    await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: {
      writeText: async (value: string) => { document.body.dataset.copiedMcp = value; },
    } }));
    if (info.project.name === "desktop") {
      await page.getByRole("button", { name: "Switch to dark mode" }).click();
      await expect(page.locator("html")).toHaveClass(/dark/);
    }

    const button = page.getByRole("button", { name: `${label} installation instructions`, exact: true });
    await expect(button).toHaveAttribute("aria-expanded", "false");
    await expect(button).toHaveAttribute("data-variant", "link");
    await expect(button).toHaveCSS("border-width", "0px");
    await expect(page.locator(`#mcp-${domain}-hint`).getByRole("button", { name: `${label} installation instructions`, exact: true })).toBeVisible();
    await button.focus();
    await page.keyboard.press("Enter");
    const dialog = page.getByRole("dialog", { name: `${label} installation instructions`, exact: true });
    await expect(dialog).toBeVisible();
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    await expect(dialog.getByLabel(`${label} MCP endpoint`)).toHaveText(endpoint);
    await expect(dialog.getByLabel(`${label} MCP endpoint`)).toHaveCSS("border-width", "0px");
    await dialog.getByRole("button", { name: `Copy ${label} endpoint`, exact: true }).click();
    await expect(page.locator("body")).toHaveAttribute("data-copied-mcp", endpoint);
    await expect(dialog.getByRole("button", { name: `Copy ${label} endpoint`, exact: true })).toHaveAttribute("title", "Copied");
    const text = page.getByRole("textbox", { name: `${label} installation prompt text`, exact: true });
    const prompt = await text.inputValue();
    expect(prompt).toContain(`Server URL: ${endpoint}`);
    expect(prompt).toContain(`codex mcp add codemortis-${domain}`);
    expect(prompt).toContain(`--oauth-resource '${endpoint}'`);
    expect(prompt).toContain("--oauth-client-registration dcr");
    expect(prompt).toContain("Let me complete sign-in and choose repositories and permissions in the browser.");
    expect(prompt).not.toContain(`/mcp/${domain === "reviews" ? "security" : "reviews"}`);
    expect(prompt.includes("security:issues")).toBe(domain === "security");
    const copyPrompt = dialog.getByRole("button", { name: `Copy ${label} installation prompt`, exact: true });
    await expect(copyPrompt).toHaveAttribute("data-variant", "ghost");
    if (info.project.name === "desktop") {
      await page.mouse.move(0, 0);
      await expect(copyPrompt).toHaveCSS("opacity", "0");
      await text.hover();
    }
    await expect(copyPrompt).toHaveCSS("opacity", "1");
    await copyPrompt.click();
    await expect(page.locator("body")).toHaveAttribute("data-copied-mcp", prompt);
    await expect(page.getByRole("status")).toContainText(`${label} installation prompt copied.`);
    expect(writes).toHaveLength(0);
    if (domain === "security") await expect(page.getByText("Security MCP is off.", { exact: false })).toBeVisible();
    await text.scrollIntoViewIfNeeded();
    await noOverflow(page);
    await page.screenshot({ path: `/private/tmp/mcp-install-${domain}-${info.project.name}.png` });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
    await expect(button).toBeFocused();
    await expect(text).toHaveCount(0);
  });
}

test("copy failure keeps the endpoint and prompt available for manual copying", async ({ page }) => {
  await mockMcp(page, { role: "member", settingsOverride: { can_manage: false } });
  await page.goto("/settings?tab=mcp");
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { value: {
    writeText: async () => { throw new Error("Clipboard denied"); },
  } }));
  await page.getByRole("button", { name: "Reviews installation instructions", exact: true }).click();
  await page.getByRole("button", { name: "Copy Reviews endpoint", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Could not copy. Select the endpoint and copy it manually.");
  await expect(page.getByLabel("Reviews MCP endpoint")).toHaveText("https://reviews.example.com/mcp/reviews");
  await page.getByRole("textbox", { name: "Reviews installation prompt text", exact: true }).hover();
  await page.getByRole("button", { name: "Copy Reviews installation prompt", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Could not copy. Select the prompt and copy it manually.");
  await expect(page.getByRole("textbox", { name: "Reviews installation prompt text", exact: true })).toBeVisible();
  await noOverflow(page);
});

test("consent selects a subset, approves, and denies exact scopes", async ({ page }, info) => {
  const writes = await mockMcp(page);
  await page.goto("/mcp/authorize?request_id=req-1");
  await expect(page.getByRole("heading", { name: "Authorize this agent?", exact: true })).toBeVisible();
  await expect(page.getByText("https://agent.example.com/callback")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /acme\/payments/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Run reviews/ })).not.toBeChecked();

  await page.getByRole("checkbox", { name: /acme\/billing/ }).click();
  await expect(page.getByRole("checkbox", { name: /acme\/billing/ })).not.toBeChecked();
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/mcp-authorize-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Approve selected access", exact: true }).click();
  await expect(page).toHaveURL(/settings\?tab=mcp&authorized=1/);
  expect(writes.find((w) => w.path === "/mcp/authorize" && w.method === "POST")?.body).toEqual({
    request_id: "req-1", repository_ids: [22], scopes: ["reviews:read"], approve: true,
  });

  await page.goto("/mcp/authorize?request_id=req-1");
  await page.getByRole("button", { name: "Deny", exact: true }).click();
  await expect(page).toHaveURL(/settings\?tab=mcp&denied=1/);
  expect(writes.filter((w) => w.path === "/mcp/authorize" && w.method === "POST").at(-1)?.body).toEqual({
    request_id: "req-1", repository_ids: [], scopes: [], approve: false,
  });

  await page.goto("/mcp/authorize?request_id=stale");
  await expect(page.getByRole("alert")).toContainText("expired");

  await page.goto("/mcp/authorize");
  await expect(page.getByRole("alert")).toContainText("missing its request");
});

test("approval previews exact arguments and records approve or deny", async ({ page }, info) => {
  const writes = await mockMcp(page);
  await page.goto("/mcp/approvals/approval-1");
  await expect(page.getByRole("heading", { name: "Review this action", exact: true })).toBeVisible();
  await expect(page.getByText("Create a GitHub issue for finding finding-9 in acme/payments.")).toBeVisible();
  const args = page.getByRole("region", { name: "Exact arguments" }).or(page.locator("pre"));
  await expect(args.first()).toContainText('"finding_id": "finding-9"');
  await expect(args.first()).toContainText('"visibility": "public"');
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/mcp-approval-${info.project.name}.png`, fullPage: true });

  await page.getByRole("button", { name: "Approve this action", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Approved. The agent may now run this exact action once.");
  expect(writes.find((w) => w.path === "/mcp/approvals/approval-1" && w.method === "POST")?.body).toEqual({ approve: true });

  await page.goto("/mcp/approvals/approval-1");
  await page.getByRole("button", { name: "Deny", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Denied. This action cannot run with this approval.");
  await expect(page.getByRole("button", { name: "Approve this action", exact: true })).toHaveCount(0);
  expect(writes.filter((w) => w.path === "/mcp/approvals/approval-1" && w.method === "POST").at(-1)?.body).toEqual({ approve: false });
  await page.route("**/api/v1/mcp/approvals/approval-1", (route) => route.fulfill({ json: { ...approvalDetail, denied_at: at } }));
  await page.reload();
  await expect(page.getByRole("status")).toContainText("Denied. This action cannot run with this approval.");
  await expect(page.getByRole("button", { name: "Approve this action", exact: true })).toHaveCount(0);
});

test("changing consent requests resets selections and errors", async ({ page }, info) => {
  const writes = await mockMcp(page);
  await page.route("**/api/v1/mcp/authorize?request_id=req-2", (route) => route.fulfill({ json: {
    ...authorizePreview, request_id: "req-2", client_name: "Second agent", domain: "security",
    repositories: [{ id: 23, full_name: "acme/billing" }, { id: 24, full_name: "acme/orders" }],
    scopes: [
      { name: "security:read", label: "Read security", description: "Read saved scans." },
      { name: "security:run", label: "Run scans", description: "Start security scans." },
    ],
  } }));
  await page.route("**/api/v1/mcp/authorize", (route) => {
    if (route.request().postDataJSON()?.request_id === "req-1") {
      return route.fulfill({ status: 409, json: { detail: "First request changed." } });
    }
    return route.fallback();
  });
  await page.goto("/mcp/authorize?request_id=req-1");
  await page.getByRole("checkbox", { name: /acme\/billing/ }).uncheck();
  await page.getByRole("checkbox", { name: /Run reviews/ }).check();
  await page.getByRole("button", { name: "Approve selected access", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("First request changed.");
  await page.evaluate(() => {
    history.pushState(history.state, "", "/mcp/authorize?request_id=req-2");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByText("Second agent", { exact: false })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /acme\/billing/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /acme\/orders/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Read security/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /Run scans/ })).not.toBeChecked();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `/private/tmp/mcp-consent-reset-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Approve selected access", exact: true }).click();
  await expect(page).toHaveURL(/authorized=1/);
  expect(writes.find((write) => write.path === "/mcp/authorize" && write.method === "POST")?.body).toEqual({
    request_id: "req-2", repository_ids: [23, 24], scopes: ["security:read"], approve: true,
  });
});

for (const approve of [true, false]) {
  test(`changing approval requests clears a previous ${approve ? "approval" : "denial"}`, async ({ page }, info) => {
    await mockMcp(page);
    await page.route("**/api/v1/mcp/approvals/approval-2", (route) => route.fulfill({ json: {
      ...approvalDetail, id: "approval-2", summary: "Second pending action.",
    } }));
    await page.goto("/mcp/approvals/approval-1");
    await page.getByRole("button", { name: approve ? "Approve this action" : "Deny", exact: true }).click();
    await expect(page.getByRole("status")).toContainText(approve ? "Approved." : "Denied.");
    await page.evaluate(() => {
      history.pushState(history.state, "", "/mcp/approvals/approval-2");
      dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByText("Second pending action.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve this action", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Deny", exact: true })).toBeEnabled();
    await expect(page.getByText(approve ? "Approved." : "Denied.", { exact: false })).toHaveCount(0);
    await noOverflow(page);
    await page.screenshot({ path: `/private/tmp/mcp-approval-reset-${approve}-${info.project.name}.png`, fullPage: true });
  });
}

test("a late consent redirect cannot replace the current request", async ({ page }) => {
  await mockMcp(page);
  await page.route("**/api/v1/mcp/authorize?request_id=req-2", (route) => route.fulfill({ json: {
    ...authorizePreview, request_id: "req-2", client_name: "Second agent",
  } }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/mcp/authorize", async (route) => {
    await pending;
    return route.fulfill({ json: { redirect_url: "/settings?tab=mcp&stale=1" } });
  });
  await page.goto("/mcp/authorize?request_id=req-1");
  const response = page.waitForResponse((result) => result.request().method() === "POST"
    && new URL(result.url()).pathname === "/api/v1/mcp/authorize");
  await page.getByRole("button", { name: "Approve selected access", exact: true }).click();
  await expect(page.getByRole("button", { name: "Working…", exact: true })).toBeDisabled();
  try {
    await page.evaluate(() => {
      history.pushState(history.state, "", "/mcp/authorize?request_id=req-2");
      dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByText("Second agent", { exact: false })).toBeVisible();
  } finally {
    release();
  }
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page).toHaveURL(/request_id=req-2/);
  await expect(page.getByRole("button", { name: "Approve selected access", exact: true })).toBeEnabled();
});

test("a late approval decision cannot settle the next action", async ({ page }) => {
  await mockMcp(page);
  await page.route("**/api/v1/mcp/approvals/approval-2", (route) => route.fulfill({ json: {
    ...approvalDetail, id: "approval-2", summary: "Second pending action.",
  } }));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/mcp/approvals/approval-1", async (route) => {
    if (route.request().method() === "GET") return route.fallback();
    await pending;
    return route.fulfill({ json: { approved: true } });
  });
  await page.goto("/mcp/approvals/approval-1");
  const response = page.waitForResponse((result) => result.request().method() === "POST"
    && result.url().endsWith("/mcp/approvals/approval-1"));
  await page.getByRole("button", { name: "Approve this action", exact: true }).click();
  await expect(page.getByRole("button", { name: "Working…", exact: true })).toBeDisabled();
  try {
    await page.evaluate(() => {
      history.pushState(history.state, "", "/mcp/approvals/approval-2");
      dispatchEvent(new PopStateEvent("popstate"));
    });
    await expect(page.getByText("Second pending action.")).toBeVisible();
  } finally {
    release();
  }
  await (await response).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByRole("button", { name: "Approve this action", exact: true })).toBeEnabled();
  await expect(page.getByText("Approved.", { exact: false })).toHaveCount(0);
});
