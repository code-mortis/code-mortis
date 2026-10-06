import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";

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
    concurrency: 2,
    rules: null,
  },
  open_prs: 2,
};
const run = {
  id: "run",
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
};

async function mock(
  page: Page,
  options: { configured?: boolean; role?: string; canWrite?: boolean } = {},
) {
  const current = structuredClone(finding);
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
        public_url: "https://reviews.example.com",
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
        pr,
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
        runs: [run],
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
    } else if (path === "/runs/run")
      data = {
        run,
        pull_request: pr,
        repository: repo,
        occurrences: [
          { finding_id: "finding", snapshot: finding, inline_line: 46 },
        ],
        can_write: options.canWrite !== false,
      };
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

test("partial review and publication retry remain distinct", async ({
  page,
}) => {
  const requests = await mock(page);
  await page.goto("/runs/run");
  await expect(
    page.getByText("This review is incomplete.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry GitHub publication" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].path).toBe("/runs/run/publish");
  await noOverflow(page);
});

test("read-only members cannot change findings or instance settings", async ({
  page,
}) => {
  await mock(page, { role: "member", canWrite: false });
  await page.goto("/pulls/pr");
  await expect(page.getByRole("heading", { name: pr.title })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Settings", exact: true }),
  ).toHaveCount(0);
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
  await expect(page.getByLabel("Model identifier")).toHaveValue("review-model");
});

test("new instance shows registration with required bootstrap token", async ({
  page,
}) => {
  await mock(page, { configured: false });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Give your reviews a home." }),
  ).toBeVisible();
  await expect(page.getByLabel("Bootstrap token")).toHaveAttribute(
    "type",
    "password",
  );
  await noOverflow(page);
});

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
