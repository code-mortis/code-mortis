import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import type { Run, Schema } from "../src/api";

const at = "2026-10-05T13:20:00Z";
const repo = {
  id: 22,
  installation_id: 10,
  full_name: "acme/payments",
  private: true,
  active: true,
  enabled: true,
  connection_id: "connection",
  settings: {
    automatic: true,
    model: "",
    effort: "medium",
    language: "English",
    exclusions: "",
    background: "",
    timeout_seconds: 900,
    token_budget: 200000,
    max_files: 40,
    concurrency: 2,
    rules: null,
  },
  open_prs: 2,
};
const run = {
  id: "run",
  engine: "ocr",
  owner_id: null,
  actor_id: 7,
  progress: {},
  pr_id: "pr",
  trigger: "automatic",
  base_sha: "b".repeat(40),
  head_sha: "a".repeat(40),
  status: "partial",
  publication: "failed",
  created_at: at,
  started_at: at,
  finished_at: "2026-10-05T13:21:30Z",
  error: null,
  publication_error: "GitHub temporarily rate-limited this app.",
  check_enabled: true,
  check_pending: true,
  check_status: "in_progress",
  check_conclusion: null,
  check_url: "https://github.com/acme/payments/runs/42",
  check_error: "Enable Checks: Read and write on the GitHub App and approve the installation permission update.",
  config: {
    connection: {
      name: "Team reviews",
      model: "review-model",
      provider: "openai",
    },
  },
  report: {
    manifest: {
      coverage: {
        selected: [{ path: "src/retry.ts" }, { path: "src/config.ts" }],
        completed: [{ path: "src/retry.ts" }],
        failed: [],
        waived: [{ path: "src/config.ts", reason: "Token budget reached" }],
        reused: [],
      },
    },
  },
};
const pr = {
  id: "pr",
  repository_id: 22,
  number: 148,
  title: "Add retry handling for payment webhooks",
  author: "maria",
  url: "https://github.com/acme/payments/pull/148",
  state: "open",
  draft: false,
  fork: false,
  head_sha: run.head_sha,
  base_sha: run.base_sha,
  head_ref: "feat/webhook-retries",
  updated_at: at,
  latest_run: run,
  open_findings: 2,
};
const finding = {
  id: "finding",
  pr_id: "pr",
  status: "open",
  desired_status: null as string | null,
  sync_error: null,
  path: "src/webhooks/retry.ts",
  content:
    "The retry loop has no upper bound. A persistent failure keeps the worker busy and delays the remaining webhooks. Limit the attempts and move exhausted jobs to the failure queue.",
  severity: "high",
  category: "bug",
  start_line: 42,
  end_line: 46,
  existing_code:
    "while (!delivered) {\n  delivered = await sendWebhook(payload);\n}",
  suggestion:
    "for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {\n  if (await sendWebhook(payload)) break;\n}",
  thread_id: "thread",
  github_url: "https://github.com/acme/payments/pull/148#discussion",
  last_seen_run_id: "run",
  created_at: at,
  updated_at: at,
};
const connection = {
  id: "connection",
  name: "Team reviews",
  provider: "openai",
  protocol: "openai",
  model: "review-model",
  base_url: "https://api.openai.com/v1",
  options: {},
  has_key: true,
  header_names: [],
  last_test: "Connected. Model completed a tool-call round trip.",
  tested_at: at,
  kind: "api",
  owner_id: null,
  auth_status: "not_required",
  auth_message: null,
};

async function mock(
  page: Page,
  options: {
    configured?: boolean;
    role?: string;
    canWrite?: boolean;
    publicUrl?: string;
    personal?: boolean;
    claude?: boolean;
    run?: Partial<Run>;
    finding?: Partial<Schema["FindingOut"]>;
    verifications?: Schema["VerificationOut"][];
  } = {},
) {
  const current: Schema["FindingOut"] = { ...structuredClone(finding), sync_intent: null, ...options.finding };
  const requests: {
    path: string;
    method: string;
    body: unknown;
    csrf: string | undefined;
  }[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method();
    const currentRun = { ...run, ...options.run };
    if (method !== "GET")
      requests.push({
        path,
        method,
        body: request.postDataJSON(),
        csrf: request.headers()["x-csrf-token"],
      });
    let data: unknown;
    if (path === "/setup/status")
      data = {
        configured: options.configured !== false,
        public_url: options.publicUrl ?? new URL(request.url()).origin,
      };
    else if (path === "/auth/me")
      data = {
        id: 7,
        login: "carlos",
        role: options.role ?? "admin",
        avatar_url: "",
        csrf: "csrf-from-session",
      };
    else if (path === "/repositories") data = [repo];
    else if (path === "/repositories/22/pulls")
      data = [
        { ...pr, latest_run: currentRun },
        {
          ...pr,
          id: "pr-other",
          number: 147,
          title: "Validate webhook signatures",
          author: "sam",
          latest_run: { ...run, status: "completed", publication: "published" },
          open_findings: 0,
        },
      ];
    else if (path === "/pulls/pr")
      data = {
        pull_request: pr,
        repository: repo,
        runs: [currentRun],
        findings: [
          current,
          {
            ...finding,
            id: "finding2",
            severity: "medium",
            category: "performance",
            path: "src/webhooks/dispatch.ts",
            start_line: 18,
            end_line: 18,
            content:
              "Sequential delivery blocks independent webhook destinations. A slow endpoint delays notifications for the other recipients.",
            last_seen_run_id: "previous",
          },
        ],
        can_write: options.canWrite !== false,
      };
    else if (path === "/findings/finding") {
      if (method === "PATCH")
        current.desired_status = request.postDataJSON().status;
      data =
        method === "PATCH"
          ? current
          : {
              finding: current,
              verifications: options.verifications ?? [],
              activities: [
                {
                  id: "event",
                  action: "detected",
                  actor: "Code Mortis",
                  source: "review",
                  reason: "",
                  created_at: at,
                },
              ],
            };
    } else if (path === "/findings/finding/sync") {
      current.sync_error = null;
      data = { job_id: "sync-job" };
    } else if (path === "/runs/run")
      data = {
        run: currentRun,
        pull_request: pr,
        repository: repo,
        occurrences: currentRun.status === "skipped" ? [] : [
          { finding_id: "finding", snapshot: finding, inline_line: 46 },
        ],
        can_write: options.canWrite !== false,
      };
    else if (path === "/connections/models" || /^\/connections\/[^/]+\/models$/.test(path))
      data = {
        source: "provider", status: "ready", message: null,
        models: [{ id: "review-model", name: "Review model" }, { id: "next-model", name: "Next model" }],
      };
    else if (path === "/connections/connection" && method === "PUT")
      data = { ...connection, ...request.postDataJSON() };
    else if (path === "/connections/personal/auth")
      data = {
        status: "disconnected",
        attempt: options.claude ? null : {
          status: "pending",
          verificationUrl: "https://auth.openai.com/codex/device",
          userCode: "ABCD-1234",
        },
      };
    else if (path === "/connections/personal/console-ticket") data = { ticket: "single-use-test-ticket" };
    else if (path === "/connections/personal/grants") data = [];
    else if (path === "/connections" && options.personal)
      data = [
        {
          ...connection,
          id: "personal",
          name: options.claude ? "My Claude Code" : "My ChatGPT",
          kind: options.claude ? "claude_code" : "chatgpt",
          owner_id: 7,
          auth_status: "disconnected",
          protocol: "native",
          has_key: false,
          last_test: null,
          tested_at: null,
        },
      ];
    else if (path === "/connections")
      data =
        method === "GET"
          ? [connection]
          : { ...connection, ...request.postDataJSON() };
    else if (path === "/providers")
      data = [
        {
          name: "openai",
          displayName: "OpenAI API",
          protocol: "openai",
          baseUrl: "https://api.openai.com/v1",
        },
      ];
    else if (path === "/settings")
      data = {
        github_app: {
          id: "123",
          slug: "code-mortis-acme",
          install_url:
            "https://github.com/apps/code-mortis-acme/installations/new",
        },
        installations: [{ id: 10, account: "acme", active: true }],
        members: [{ id: 7, login: "carlos", role: "admin" }],
        failed_jobs: [],
        public_url: "https://reviews.example.com",
      };
    else if (path === "/installations/available")
      data = [{ id: 10, account: "acme" }];
    else if (method !== "GET") data = { ok: true, job_id: "job" };
    else
      return route.fulfill({
        status: 404,
        json: { detail: "Unexpected test request: " + path },
      });
    await route.fulfill({ json: data });
  });
  return requests;
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  for (const dialog of await page.getByRole("dialog").all()) {
    expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  }
}

test("repository navigation, filtering, and review workspace", async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mock(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Repositories", exact: true }),
  ).toBeVisible();
  await page.getByLabel("Search pull requests").fill("148");
  await expect(page.getByText("Validate webhook signatures")).toHaveCount(0);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await expect(page.getByRole("heading", { name: pr.title })).toBeVisible();
  await expect(page.getByText("Not observed in latest review")).toBeVisible();
  await noOverflow(page);
  await page.evaluate(() => document.fonts.ready);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({
    path: `../.impeccable/review/${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("tab", { name: "Run history" }).click();
  await expect(
    page.getByRole("link", { name: /automatic.*aaaaaaa/ }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test("resolution waits for GitHub and preserves the confirmed status", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/pulls/pr");
  await page
    .getByRole("button", { name: /retry loop has no upper bound/ })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/finding-${info.project.name}.png`,
    fullPage: false,
    animations: "disabled",
  });
  await page
    .getByRole("button", { name: "Resolve finding", exact: true })
    .click();
  await expect(
    page.getByText("Waiting for GitHub to confirm: resolved."),
  ).toBeVisible();
  expect(requests.at(-1)).toMatchObject({
    path: "/findings/finding",
    method: "PATCH",
    body: { status: "resolved" },
    csrf: "csrf-from-session",
  });
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toBeDisabled();
});

test("finding code shows a unified diff and safe Markdown highlighting in both themes", async ({ page }, info) => {
  const source = 'def deliver():\n    return "<img src=x onerror=alert(1)>"\n';
  await mock(page, { finding: { content: `The retry loop has no upper bound. Keep \`MAX_ATTEMPTS\` bounded.\n\n\`\`\`python\n${source}\`\`\`` } });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const panel = page.getByRole("dialog");
  const patch = panel.getByRole("region", { name: "Suggested change diff" });
  await expect(patch).toContainText("--- original\n+++ suggested\n@@ -1,3 +1,3 @@");
  await expect(patch.locator(".hljs-deletion").first()).toHaveText("-while (!delivered) {");
  await expect(patch.locator(".hljs-addition").first()).toContainText("+for (let attempt = 0;");
  expect(await patch.textContent()).toContain("-  delivered = await sendWebhook(payload);\n");
  await patch.focus();
  await expect(patch).toBeFocused();
  const markdown = panel.locator(".markdown pre");
  expect(await markdown.textContent()).toBe(source);
  await expect(markdown.locator(".hljs-keyword").first()).toHaveText("def");
  await expect(markdown.locator("img, script, pre")).toHaveCount(0);
  await expect(panel.locator(".markdown p > code")).toHaveText("MAX_ATTEMPTS");
  await mkdir("../.impeccable/review", { recursive: true });
  for (const colorScheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme });
    await expect(page.locator("html")).toHaveClass(colorScheme === "dark" ? /dark/ : /^(?!.*dark)/);
    await noOverflow(page);
    await page.screenshot({ path: `../.impeccable/review/code-${colorScheme}-${info.project.name}.png`, animations: "disabled" });
  }
});

for (const example of [
  { name: "suggestion only", original: "", suggested: "const limit = 3;\n", path: "retry.tsx", language: "typescript", count: 1 },
  { name: "identical", original: "return True\n", suggested: "return True\n", path: "retry.py", language: "python", count: 1 },
  { name: "no suggestion", original: "FROM alpine\n", suggested: "", path: "Dockerfile", language: "dockerfile", count: 1 },
  { name: "unknown language", original: "", suggested: '<script>alert(1)</script>\n\n' + "long line ".repeat(80), path: "file.unknown", language: null, count: 1 },
  { name: "no snippets", original: "", suggested: "", path: "retry.ts", language: null, count: 0 },
  { name: "large comparison", original: "a\n".repeat(16000), suggested: "b\n".repeat(16000), path: "file.txt", language: null, count: 2 },
]) {
  test(`finding code fallback: ${example.name}`, async ({ page }) => {
    await mock(page, { finding: { existing_code: example.original, suggestion: example.suggested, path: example.path } });
    await page.goto("/pulls/pr");
    await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
    const panel = page.getByRole("dialog");
    await expect(panel.getByRole("region", { name: "Suggested change diff" })).toHaveCount(0);
    await expect(panel.locator("pre")).toHaveCount(example.count);
    if (example.count) {
      const code = panel.locator("pre").last();
      expect(await code.textContent()).toBe(example.suggested || example.original);
      await expect(code.locator(".hljs")).toHaveCount(example.language ? 1 : 0);
      if (example.language) await expect(code.locator("code")).toHaveClass(`hljs language-${example.language}`);
      await expect(code.locator("script, img")).toHaveCount(0);
    }
    await noOverflow(page);
  });
}

test("partial review and publication retry remain distinct", async ({
  page,
}, info) => {
  const requests = await mock(page);
  await page.goto("/runs/run");
  await expect(page.locator("main header")).toContainText("· 90s");
  await expect(
    page.getByRole("region", { name: "Review outcome" }),
  ).toContainText("Coverage is incomplete.");
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].path).toBe("/runs/run/publish");
  await expect(page.getByRole("link", { name: "View check on GitHub" })).toHaveAttribute("href", run.check_url);
  await expect(page.getByText(run.check_error)).toBeVisible();
  await expect(page.getByText("Check update pending. Review results are saved separately.")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-${info.project.name}.png`, fullPage: true });
  await page.getByText("Configuration used", { exact: true }).click();
  const config = page.locator("pre .language-json").last();
  await expect(config.locator(".hljs-attr").first()).toHaveText('"connection"');
  expect(await config.textContent()).toBe(JSON.stringify(run.config, null, 2));
  await page.getByText("Inspect coverage report", { exact: true }).click();
  await expect(page.locator("pre .language-json .hljs-attr").first()).toHaveText('"manifest"');
});

test("GitHub check handles pending, conclusion-only, and empty states", async ({ page }, info) => {
  const checkState: Partial<Run> = {
    check_status: null,
    check_conclusion: null,
    check_url: null,
    check_error: null,
  };
  await mock(page, { run: checkState });
  await page.goto("/runs/run");
  const check = page.getByText("GitHub check", { exact: true }).locator("..");
  await expect(check.getByRole("status")).toHaveText("Updating the check on GitHub…");
  await expect(check.locator(":scope > :empty")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-pending-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = "success";
  checkState.check_pending = false;
  await page.reload();
  await expect(check.getByText("success", { exact: true })).toBeVisible();
  await expect(check.getByRole("status")).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-conclusion-${info.project.name}.png`, fullPage: true });

  checkState.check_conclusion = null;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Review run", exact: true })).toBeVisible();
  await expect(page.getByText("GitHub check", { exact: true })).toHaveCount(0);
  await expect(page.getByText("GitHub comments", { exact: true })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/check-empty-${info.project.name}.png`, fullPage: true });
});

test("read-only members cannot change findings or instance settings", async ({
  page,
}) => {
  await mock(page, { role: "member", canWrite: false });
  await page.goto("/pulls/pr");
  await expect(page.getByRole("heading", { name: pr.title })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Settings", exact: true }),
  ).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Run review", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: /retry loop has no upper bound/ })
    .click();
  await expect(
    page.getByRole("button", { name: "Resolve finding", exact: true }),
  ).toHaveCount(0);
});

test("connection editing never preloads saved credentials", async ({
  page,
}, info) => {
  await mock(page);
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { name: "Settings", exact: true }),
  ).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/settings-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Replace API key (optional)")).toHaveValue("");
  await expect(page.getByLabel("Model", { exact: true })).toHaveValue("review-model");
});

test("saved model selection refreshes without replacing custom IDs", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator("option", { hasText: "Next model" })).toHaveCount(1);
  await model.selectOption("next-model");
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  expect(requests.find((request) => request.path === "/connections/models")?.body).toEqual({
    kind: "api", provider: "openai", protocol: "openai", base_url: "https://api.openai.com/v1",
    options: {}, connection_id: "connection",
  });
  await expect(model).toHaveValue("next-model");
  await model.selectOption({ label: "Enter a custom model ID" });
  await page.getByLabel("Custom model ID").fill("private-model");
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh models", exact: true })).toBeEnabled();
  await expect(page.getByLabel("Custom model ID")).toHaveValue("private-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/model-picker-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections/connection")?.body).toMatchObject({ model: "private-model" });
});

test("API drafts load models before saving and support keyboard selection", async ({ page }) => {
  const requests = await mock(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection name").fill("New connection");
  await page.getByLabel("API key", { exact: true }).fill("draft-key");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator("option", { hasText: "Review model" })).toHaveCount(1);
  expect(requests.filter((request) => request.path === "/connections")).toHaveLength(0);
  expect(requests.find((request) => request.path === "/connections/models")?.body).toMatchObject({ api_key: "draft-key" });
  await model.focus();
  await model.press("r");
  await model.press("Tab");
  await expect(model).toHaveValue("review-model");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests.find((request) => request.path === "/connections")?.body).toMatchObject({ model: "review-model", api_key: "draft-key" });
});

test("changed draft discards late models and keeps manual entry on errors", async ({ page }) => {
  await mock(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/connections/models", async (route) => {
    const old = route.request().postDataJSON().base_url === "https://api.openai.com/v1";
    if (old) await held;
    await route.fulfill({ json: { source: "provider", status: "ready", models: [{ id: old ? "old-model" : "new-model", name: old ? "Old model" : "New model" }] } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("API key", { exact: true }).fill("key");
  const first = page.waitForRequest("**/api/v1/connections/models");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  await first;
  await page.getByLabel("API base URL").fill("https://new.example/v1");
  await page.getByRole("button", { name: "Load models", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator('option[value="new-model"]')).toHaveCount(1);
  const late = page.waitForResponse((response) => response.url().endsWith("/connections/models") && response.request().postDataJSON().base_url === "https://api.openai.com/v1");
  release();
  await late;
  await expect(model.locator('option[value="old-model"]')).toHaveCount(0);
  await expect(model.locator('option[value="new-model"]')).toHaveCount(1);
  await model.selectOption("new-model");
  await page.route("**/api/v1/connections/models", (route) => route.fulfill({ json: { source: "provider", status: "unsupported", message: "This endpoint does not support model discovery. Enter a custom model ID.", models: [] } }));
  await page.getByRole("button", { name: "Refresh models", exact: true }).click();
  await expect(page.getByText("This endpoint does not support model discovery. Enter a custom model ID.")).toBeVisible();
  await expect(model).toHaveValue("new-model");
  await model.selectOption({ label: "Enter a custom model ID" });
  await page.getByLabel("Custom model ID").fill("fallback");
  await expect(page.getByLabel("Custom model ID")).toHaveValue("fallback");
});

test("repository model choices follow its selected connection", async ({ page }, info) => {
  const requests = await mock(page);
  await page.route("**/api/v1/connections?repository_id=22", (route) => route.fulfill({ json: [connection, { ...connection, id: "second", name: "Second connection", model: "second-model" }] }));
  await page.route("**/api/v1/connections/second/models?repository_id=22", (route) => route.fulfill({ json: { source: "provider", status: "ready", models: [{ id: "second-model", name: "Second model" }] } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const model = page.getByLabel("Model override (optional)");
  await expect(model.locator("option:checked")).toHaveText("review-model");
  await expect(model.locator('option[value="next-model"]')).toHaveCount(1);
  await model.selectOption("next-model");
  await page.getByLabel("AI connection", { exact: true }).selectOption("second");
  await expect(model).toHaveValue("");
  await expect(model.locator("option:checked")).toHaveText("second-model");
  await expect(model.locator('option[value="second-model"]')).toHaveCount(1);
  await expect(model.locator('option[value="next-model"]')).toHaveCount(0);
  await model.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/connection-default-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => requests.find((request) => request.path === "/repositories/22")?.body).toMatchObject({
    connection_id: "second", settings: { model: "" },
  });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByLabel("AI connection", { exact: true }).selectOption("");
  await expect(model).toBeDisabled();
});

test("review limits save and reopen independent unlimited choices", async ({ page }, info) => {
  await mock(page);
  let current = structuredClone(repo) as Schema["RepositoryOut"];
  await page.route("**/api/v1/repositories", (route) => route.fulfill({ json: [current] }));
  await page.route("**/api/v1/repositories/22", async (route) => {
    current = { ...current, ...route.request().postDataJSON() };
    await route.fulfill({ json: current });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByText("Review limits and rules", { exact: true }).click();
  const time = page.getByLabel("Timeout (seconds)", { exact: true });
  const tokens = page.getByLabel("Token budget", { exact: true });
  const files = page.getByLabel("Maximum files", { exact: true });
  const unlimitedTime = page.getByRole("checkbox", { name: "Unlimited timeout (seconds)", exact: true });
  const unlimitedTokens = page.getByRole("checkbox", { name: "Unlimited token budget", exact: true });
  const unlimitedFiles = page.getByRole("checkbox", { name: "Unlimited maximum files", exact: true });
  await expect(time).toHaveValue("900");
  await expect(tokens).toHaveValue("200000");
  await expect(files).toHaveValue("40");
  await time.fill("1200");
  await unlimitedTime.check();
  await expect(time).toBeDisabled();
  await expect(tokens).toBeEnabled();
  await expect(files).toBeEnabled();
  await unlimitedTime.uncheck();
  await expect(time).toHaveValue("1200");
  await files.fill("0");
  expect(await files.evaluate((element: HTMLInputElement) => element.validity.valid)).toBe(false);
  await files.fill("40");
  await unlimitedTokens.check();
  await unlimitedFiles.check();
  await unlimitedTime.check();
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: null });
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByText("Review limits and rules", { exact: true }).click();
  await expect(unlimitedTime).toBeChecked();
  await expect(unlimitedTokens).toBeChecked();
  await expect(unlimitedFiles).toBeChecked();
  await unlimitedFiles.uncheck();
  await expect(files).toHaveValue("40");
  await noOverflow(page);
  await time.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/review-limits-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => current.settings).toMatchObject({ timeout_seconds: null, token_budget: null, max_files: 40 });
});

test("personal review limits retain their separate context budget", async ({ page }) => {
  await mock(page, { personal: true });
  await page.goto("/");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  await page.getByLabel("AI connection", { exact: true }).selectOption("personal");
  await page.getByText("Review limits and rules", { exact: true }).click();
  await expect(page.getByLabel("Token budget", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Native context budget (bytes)")).toHaveValue("800000");
  await page.getByRole("checkbox", { name: "Unlimited timeout (seconds)" }).check();
  await page.getByRole("checkbox", { name: "Unlimited maximum files" }).check();
  await expect(page.getByLabel("Native context budget (bytes)")).toBeEnabled();
  await noOverflow(page);
});

const limitedRun: Partial<Run> = {
  status: "skipped", publication: "not_applicable", publication_error: null,
  check_status: "completed", check_conclusion: "skipped", check_pending: false, check_error: null,
  config: { ...run.config, settings: { max_files: 40 } },
  report: {
    message: "41 reviewable files exceeds the maximum of 40.",
    manifest: { schema_version: "code-mortis.file-limit/v1", coverage: {
      selected: Array.from({ length: 41 }, (_, index) => ({ path: `src/file-${index}.py` })),
      completed: [], reused: [], failed: [],
      waived: Array.from({ length: 41 }, (_, index) => ({ path: `src/file-${index}.py`, reason: "41 reviewable files exceeds the maximum of 40." })),
    } },
  },
};

test("oversized review explains its outcome and links to editable limits throughout the app", async ({ page }, info) => {
  const requests = await mock(page, { run: limitedRun });
  const reason = "41 reviewable files exceed the limit of 40. No files were reviewed.";
  await page.goto("/");
  await expect(page.getByRole("link", { name: /Add retry handling/ })).toContainText(reason);
  await page.getByRole("link", { name: /Add retry handling/ }).click();
  await expect(page.getByRole("region", { name: "Review outcome" })).toContainText(reason);
  await page.getByRole("tab", { name: /Run history/ }).click();
  await expect(page.getByRole("link", { name: /GitHub: No comments published/ })).toContainText(reason);
  await page.getByRole("link", { name: "View run details", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: reason })).toBeVisible();
  await expect(page.getByText("No comments published", { exact: true })).toBeVisible();
  await expect(page.getByText("not applicable", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry GitHub publication" })).toHaveCount(0);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/file-limit-skipped-${info.project.name}.png`, fullPage: true });
  await page.getByRole("link", { name: "Adjust review settings" }).click();
  const settings = page.getByRole("dialog");
  await expect(settings).toBeVisible();
  await expect(settings.getByLabel("Maximum files", { exact: true })).toBeVisible();
  await expect(settings.getByRole("checkbox", { name: "Unlimited maximum files" })).toBeVisible();
  expect(requests).toEqual([]);
  await noOverflow(page);
});

test("viewers receive an administrator next step without settings or retry actions", async ({ page }) => {
  await mock(page, { run: limitedRun, role: "member", canWrite: false });
  await page.goto("/runs/run");
  const outcome = page.getByRole("region", { name: "Review outcome" });
  await expect(outcome).toContainText("Ask your administrator to adjust the repository settings.");
  await expect(outcome.getByRole("link", { name: "Adjust review settings" })).toHaveCount(0);
  await expect(outcome.getByRole("link", { name: "View pull request", exact: true })).toHaveAttribute("href", "/pulls/pr");
  await expect(page.getByRole("button", { name: /Resume review|Retry GitHub publication/ })).toHaveCount(0);
});

const feedbackCases: { name: string; state: Partial<Run>; summary: string; nextStep: string; fileReason?: string }[] = [
  {
    name: "unsupported files",
    state: { status: "skipped", report: { manifest: { coverage: { selected: [] } }, excluded_files: [{ path: "README.md", exclude_reason: "unsupported_ext" }] } },
    summary: "The changed file types are not supported by the review engine.",
    nextStep: "These changes need a manual review.",
    fileReason: "File type is not supported by the review engine.",
  },
  {
    name: "empty scope",
    state: { status: "skipped", report: { manifest: { coverage: { selected: [] } }, excluded_files: [{ path: "src/vendor.ts", exclude_reason: "user_exclude" }] } },
    summary: "No changed files were eligible for review.",
    nextStep: "Check file exclusions and review rules.",
    fileReason: "Excluded by the review rules or repository settings.",
  },
  {
    name: "time limit",
    state: { status: "failed", failure_reason: "timeout", report: null },
    summary: "The review reached its time limit.",
    nextStep: "Increase Timeout, choose Unlimited, or reduce the review scope",
  },
  {
    name: "token budget",
    state: { report: { manifest: { coverage: { ...run.report.manifest.coverage, waived: [{ path: "src/config.ts", classification: "budget", reason: "Token budget reached" }] } } } },
    summary: "Coverage is incomplete. The token budget was reached.",
    nextStep: "Increase Token budget, choose Unlimited",
    fileReason: "Token budget reached",
  },
  {
    name: "native context limit",
    state: { report: { manifest: { coverage: { selected: [{ path: "source.py" }], failed: [{ path: "source.py" }] } }, errors: [{ path: "source.py", message: "Delegated context budget exceeded; increase the repository limit." }] } },
    summary: "The native context budget was reached.",
    nextStep: "Increase Native context budget or reduce the review scope",
    fileReason: "Delegated context budget exceeded; increase the repository limit.",
  },
  {
    name: "authentication",
    state: { status: "blocked", blocked_reason: "authentication", owner_id: 7 },
    summary: "The AI connection needs to sign in again.",
    nextStep: "The connection owner must reconnect the account, then resume this review.",
  },
  {
    name: "quota",
    state: { status: "blocked", blocked_reason: "quota", owner_id: 8 },
    summary: "The AI connection reached its usage limit.",
    nextStep: "The connection owner can resume this review when usage becomes available.",
  },
  {
    name: "busy connection",
    state: { status: "queued", blocked_reason: "busy" },
    summary: "The selected personal connection is reviewing another pull request.",
    nextStep: "This review will retry automatically when the connection is available.",
  },
  {
    name: "interrupted worker",
    state: { status: "failed", failure_reason: "worker_stopped", report: null },
    summary: "The review worker stopped before the run could finish.",
    nextStep: "Interrupted AI usage may already have been charged.",
  },
  {
    name: "cancelled review",
    state: { status: "cancelled" },
    summary: "This review was cancelled before it finished.",
    nextStep: "Start a new review from the pull request",
  },
  {
    name: "superseded review",
    state: { status: "superseded" },
    summary: "The pull request changed or closed",
    nextStep: "Open the pull request to inspect the latest commit",
  },
  {
    name: "older saved reason",
    state: { status: "skipped", report: { message: "A saved reason from an older run." } },
    summary: "A saved reason from an older run.",
    nextStep: "Inspect the coverage report and repository settings",
  },
  {
    name: "older partial run without coverage",
    state: { status: "partial", report: null },
    summary: "The review finished with incomplete coverage.",
    nextStep: "Inspect the error and file results in the run details",
  },
];

for (const example of feedbackCases) {
  test(`review feedback: ${example.name}`, async ({ page }) => {
    await mock(page, { run: { publication: "not_applicable", publication_error: null, ...example.state } });
    await page.goto("/runs/run");
    const outcome = page.getByRole("region", { name: "Review outcome" });
    await expect(outcome).toContainText(example.summary);
    await expect(outcome).toContainText(example.nextStep);
    if (example.fileReason) {
      const files = page.locator("details").filter({ has: page.getByText("Files not reviewed (1)", { exact: true }) });
      if (await files.getAttribute("open") === null) await files.locator("summary").click();
      await expect(files.getByText(example.fileReason, { exact: true })).toBeVisible();
    }
    if (example.state.status === "blocked") {
      await expect(page.getByRole("button", { name: "Resume review" })).toHaveCount(example.state.owner_id === 7 ? 1 : 0);
      await expect(outcome.getByRole("link", { name: "Manage AI connections" })).toHaveAttribute("href", "/settings?tab=connections");
    }
    await noOverflow(page);
  });
}

test("publication failure after a completed review offers a publication retry", async ({ page }) => {
  const requests = await mock(page, { run: { status: "completed" } });
  await page.goto("/pulls/pr");
  const outcome = page.getByRole("region", { name: "Review outcome" });
  await expect(outcome).toContainText("The selected review scope completed. Publication failed.");
  await expect(outcome).toContainText("Retry GitHub publication from the run details to send saved results without rerunning the review.");
  await outcome.getByRole("link", { name: "View run details" }).click();
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.map((request) => request.path)).toEqual(["/runs/run/publish"]);
});

test("personal setup saves without a model then signs in and selects one", async ({ page }, info) => {
  await mock(page);
  let connected = false;
  let savedModel = "";
  await page.route("**/api/v1/connections", (route) => route.request().method() === "POST"
    ? route.fulfill({ json: { ...connection, ...route.request().postDataJSON(), id: "personal", model: "", owner_id: 7, auth_status: "disconnected" } })
    : route.fallback());
  await page.route("**/api/v1/connections/personal/auth", (route) => {
    if (route.request().method() === "POST") connected = true;
    return route.fulfill({ json: { status: connected ? "connected" : "disconnected" } });
  });
  await page.route("**/api/v1/connections/personal/models", (route) => route.fulfill({ json: { source: "client", status: "ready", models: [{ id: "personal-model", name: "Personal model" }] } }));
  await page.route("**/api/v1/connections/personal", (route) => {
    savedModel = route.request().postDataJSON().model;
    return route.fulfill({ json: { ...connection, kind: "chatgpt", id: "personal", model: savedModel } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Add connection", exact: true }).click();
  await page.getByLabel("Connection type").selectOption("chatgpt");
  await page.getByLabel("Connection name").fill("Personal setup");
  await page.getByRole("button", { name: "Save connection", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Personal setup" })).toBeVisible();
  await expect(page.getByText("Choose a model to finish setup and enable reviews.")).toBeVisible();
  await page.getByRole("button", { name: "Connect ChatGPT", exact: true }).click();
  const model = page.getByLabel("Model", { exact: true });
  await expect(model.locator('option[value="personal-model"]')).toHaveCount(1);
  await model.selectOption("personal-model");
  await page.getByRole("button", { name: "Save model", exact: true }).click();
  await expect(page.getByText("Choose a model to finish setup and enable reviews.")).toHaveCount(0);
  expect(savedModel).toBe("personal-model");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/personal-model-picker-${info.project.name}.png`, fullPage: true });
});

test("new instance sends the bootstrap token in the request header", async ({
  page,
}, info) => {
  await mock(page, { configured: false });
  await page.route("**/api/v1/setup/manifest", (route) => route.fulfill({
    status: 403,
    json: { detail: "This test stops before GitHub registration." },
  }));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Give your reviews a home." }),
  ).toBeVisible();
  await expect(page.getByLabel("Bootstrap token")).toHaveAttribute(
    "type",
    "password",
  );
  await page.getByLabel("Bootstrap token").fill("test-bootstrap-token");
  const sent = page.waitForRequest("**/api/v1/setup/manifest");
  await page.getByRole("button", { name: "Register GitHub App" }).click();
  const request = await sent;
  expect(request.headers()["x-setup-token"]).toBe("test-bootstrap-token");
  expect(request.postDataJSON()).toEqual({ organization: "" });
  await expect(page.getByText("This test stops before GitHub registration.")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/bootstrap-${info.project.name}.png`,
    fullPage: true,
  });
});

for (const configured of [false, true]) {
  test(`keeps the development browser origin before ${configured ? "sign-in" : "registration"}`, async ({
    page,
  }) => {
    const publicUrl = "http://localhost:4178";
    const requests = await mock(page, { configured, publicUrl });
    const sessionOrigins: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/auth/me") sessionOrigins.push(url.origin);
    });
    await page.goto("/?from=local#setup");
    await expect(page).toHaveURL("http://127.0.0.1:4178/?from=local#setup");
    await expect(
      page.getByRole("heading", {
        name: configured ? "Repositories" : "Give your reviews a home.",
        exact: true,
      }),
    ).toBeVisible();
    expect(sessionOrigins).toEqual(configured ? ["http://127.0.0.1:4178"] : []);
    expect(requests).toEqual([]);
  });
}

test("system appearance follows OS changes and explicit choices persist", async ({
  page,
}, info) => {
  await mock(page);
  await page.goto("/pulls/pr");
  const selector = page.getByRole("combobox", {
    name: "Appearance",
    exact: true,
  });
  await expect(selector).toHaveValue("system");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass("dark");
  await page.screenshot({
    path: `../.impeccable/review/dark-${info.project.name}.png`,
    fullPage: true,
    animations: "disabled",
  });
  await selector.selectOption("light");
  await expect(page.locator("html")).not.toHaveClass("dark");
  await page.reload();
  await expect(selector).toHaveValue("light");
  await selector.selectOption("system");
  await expect(page.locator("html")).toHaveClass("dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).not.toHaveClass("dark");
});

test("members manage personal subscriptions without administrative controls", async ({
  page,
}, info) => {
  await mock(page, { role: "member", personal: true });
  await page.goto("/settings");
  await expect(
    page.getByRole("tab", { name: "Team", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "GitHub", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Manage account" }).click();
  await expect(page.getByText("ABCD-1234")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Authorize with ChatGPT" }),
  ).toHaveAttribute("href", "https://auth.openai.com/codex/device");
  await expect(
    page.getByRole("switch", {
      name: "I authorize automatic reviews with my subscription",
    }),
  ).not.toBeChecked();
  await noOverflow(page);
  await page.screenshot({
    path: `../.impeccable/review/subscription-${info.project.name}.png`,
    fullPage: true,
  });
  const automatic = page.getByRole("switch", { name: "I authorize automatic reviews with my subscription" });
  await automatic.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/subscription-consent-${info.project.name}.png` });
});

test("Claude login uses a private console and closes with its dialog", async ({ page }, info) => {
  await mock(page, { role: "member", personal: true, claude: true });
  const messages: string[] = [];
  let closed = false;
  await page.routeWebSocket("**/connections/personal/console", (socket) => {
    socket.onMessage((message) => {
      messages.push(String(message));
      if (messages.length === 1) socket.send("Open \x1b]8;;https://claude.com/cai/oauth/authorize?test=fixture\x1b\\https://claude.com/cai/oauth/authorize?test=fixture\x1b]8;;\x1b\\\nPaste the authorization code:");
    });
    socket.onClose(() => { closed = true; });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "Manage account" }).click();
  await page.getByRole("button", { name: "Connect Claude Code", exact: true }).click();
  await expect(page.getByRole("link", { name: "Open Claude authorization" })).toBeVisible();
  const response = page.getByLabel("Response to the official login prompt");
  await expect(response).toHaveAttribute("type", "password");
  await response.fill("test-authorization-code");
  await page.getByRole("button", { name: "Send to Claude Code" }).click();
  await expect(response).toHaveValue("");
  expect(messages).toEqual([JSON.stringify({ ticket: "single-use-test-ticket" }), "test-authorization-code\r"]);
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/claude-console-${info.project.name}.png` });
  await page.getByRole("button", { name: "Close console" }).click();
  await expect(page.getByLabel("Official Claude Code login console")).toHaveCount(0);
  await expect.poll(() => closed).toBe(true);
});

test("OAuth errors remain recoverable inside the app", async ({ page }) => {
  await mock(page);
  await page.goto("/?auth_error=expired&diagnostic=request-123");
  await expect(page.getByRole("alert")).toContainText("This attempt expired");
  await expect(page.getByText("Diagnostic: request-123")).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Continue with GitHub" }),
  ).toHaveAttribute("href", "/api/v1/auth/login");
});

test("verified fixes show evidence, pending sync, retry and manual override", async ({ page }, info) => {
  const requests = await mock(page, {
    finding: {
      desired_status: "resolved", sync_error: "GitHub unavailable",
      sync_intent: { verification_id: "verification", finding_id: "finding", version: 0 },
    },
    verifications: [{
      id: "verification", run_id: "run", head_sha: "c".repeat(40), status: "completed", verdict: "fixed",
      resolution: "failed", error: "GitHub unavailable", created_at: at, finished_at: at,
      result: {
        verdict: "fixed", original_issue_present: true, dismissal_recommended: false,
        explanation: "The retry loop now stops after three attempts.",
        change_explanation: "A bounded loop replaces the unbounded retry.",
        evidence: [{ revision: "current", path: "src/retry.ts", start_line: 1, end_line: 1, quote: "for (let attempt = 0; attempt < 3; attempt++) {" }],
      },
    }],
  });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  await expect(page.getByText("The retry loop now stops after three attempts.")).toBeVisible();
  await expect(page.getByText("Automatic resolution: failed")).toBeVisible();
  await page.getByText("Current evidence: src/retry.ts:1–1").click();
  await expect(page.getByText("for (let attempt = 0; attempt < 3; attempt++) {", { exact: true })).toBeVisible();
  await expect(page.locator("pre .language-typescript .hljs-keyword").first()).toHaveText("for");
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/verification-${info.project.name}.png`, fullPage: false });
  await page.getByRole("button", { name: "Retry sync", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/findings/finding/sync", method: "POST" });
  await page.getByRole("button", { name: "Keep finding open", exact: true }).click();
  expect(requests.at(-1)).toMatchObject({ path: "/findings/finding", method: "PATCH", body: { status: "open" } });
});

test("run shows verification separately from confirmed resolutions", async ({ page }) => {
  await mock(page, { run: { progress: { verification: {
    selected: 3, completed: 3, fixed: 1, still_present: 1, uncertain: 1, resolved: 0, pending: 1,
  } } } });
  await page.goto("/runs/run");
  await expect(page.getByText("3 of 3 findings verified. 0 automatically resolved, 1 pending synchronization.")).toBeVisible();
  await noOverflow(page);
});


test("automatic resolution is opt-in and requires automatic reviews", async ({ page }, info) => {
  const requests = await mock(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const automatic = page.getByRole("switch", { name: "Automatic reviews", exact: true });
  const resolution = page.getByRole("switch", { name: "Automatically resolve verified fixes", exact: true });
  await expect(resolution).not.toBeChecked();
  await automatic.uncheck();
  await expect(resolution).toBeDisabled();
  await automatic.check();
  await resolution.check();
  await noOverflow(page);
  await page.screenshot({ path: `../.impeccable/review/auto-resolution-setting-${info.project.name}.png`, fullPage: true });
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect.poll(() => requests.some((request) => request.path === "/repositories/22" &&
    (request.body as { settings?: { auto_resolve?: boolean } }).settings?.auto_resolve === true)).toBe(true);
});


test("pending verification has one status and failed verification has an alert", async ({ page }) => {
  await mock(page, { verifications: [
    { id: "pending", run_id: "run", head_sha: "c".repeat(40), status: "queued", verdict: null,
      resolution: "not_applicable", error: null, result: null, created_at: at, finished_at: null },
    { id: "failed", run_id: "run", head_sha: "d".repeat(40), status: "failed", verdict: "uncertain",
      resolution: "not_applicable", error: "Verification context is unavailable", result: null,
      created_at: at, finished_at: at },
  ] });
  await page.goto("/pulls/pr");
  await page.getByRole("button", { name: /retry loop has no upper bound/ }).click();
  const panel = page.getByRole("dialog");
  await expect(panel.getByText("queued", { exact: true })).toHaveCount(1);
  await expect(panel.getByRole("alert")).toContainText("Verification context is unavailable");
  await expect(panel.getByText("Automatic resolution: not applicable")).toHaveCount(2);
  await noOverflow(page);
});

for (const verification of [null, [1, 2], { selected: "3", completed: 3 }, { selected: 3, pending: -1 }]) {
  test(`invalid verification counts are not presented: ${JSON.stringify(verification)}`, async ({ page }) => {
    await mock(page, { run: { progress: { verification } } });
    await page.goto("/runs/run");
    await expect(page.getByText("Review run", { exact: true })).toBeVisible();
    await expect(page.getByText(/findings verified/)).toHaveCount(0);
  });
}
