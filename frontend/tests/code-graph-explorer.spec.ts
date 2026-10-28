import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const head = "a".repeat(40), base = "b".repeat(40), mergeBase = "c".repeat(40);
const selected = "src/service.ts", related = "src/consumer.ts";
const location = { path: selected, start_byte: 0, end_byte: 36, start_line: 1, end_line: 3 };

async function mockGraph(page: Page, admin = true) {
  const calls: { path: string; body: any }[] = [];
  const state = { enabled: true, status: "partial", baseStatus: "ready", paused: false, overviewError: 0,
    fileError: 0, empty: false, missingSource: false, long: false, definitionsPage: false };
  const repo = { id: 22, installation_id: 10, full_name: "acme/project", private: true, active: true,
    enabled: true, connection_id: null, open_prs: 1, settings: {} };
  const read = (revision = "head") => ({ repository_id: 22, commit_sha: revision === "head" ? head : mergeBase,
    snapshot_id: revision, status: revision === "head" ? state.status : state.baseStatus, extractor_version: "test",
    policy_digest: "test", capabilities: { defines: "supported", imports_file: "partial", callers: "unsupported", references: "unsupported" },
    definition_languages: ["python", "typescript"], gaps: [], truncated: false, source_state: "not_requested", items: [], next_cursor: null });
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const body = request.method() === "GET" ? null : request.postDataJSON();
    calls.push({ path, body });
    let data: unknown = [];
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: 7, login: "developer", role: admin ? "admin" : "member", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") data = [repo];
    else if (path === "/pulls/pr") data = { repository: repo, can_write: admin, run_count: 0, has_active_run: false, cancellable_run_count: 0,
      pull_request: { id: "pr", repository_id: 22, repository_name: repo.full_name, number: 42, title: "Validate provider responses",
        author: "developer", state: "open", draft: false, fork: false, ignored: false, head_ref: "feature", head_sha: head, base_sha: base,
        created_at: "2026-10-27T12:00:00Z", updated_at: "2026-10-27T12:00:00Z", open_findings: 0, url: "https://github.com/acme/project/pull/42",
        review_status: "not_reviewed", has_running_review: false, run_id: null, publication: null, publication_error: null, latest_run: null } };
    else if (path.endsWith("/findings")) data = { groups: [], total: 0, registered_total: 0, status_counts: {}, category_counts: {}, page_size: 10 };
    else if (path === "/pulls/pr/code-graph") {
      if (state.overviewError) return route.fulfill({ status: state.overviewError, json: { detail: "Code access is unavailable. Refresh impact." } });
      const pageNumber = Number(url.searchParams.get("page") ?? 1);
      const files = state.empty ? [] : pageNumber === 2 ? [{ path: "src/old.ts", change: "removed" }] : [
        { path: selected, change: "modified" }, { path: "src/added.ts", change: "added" },
        { path: "src/" + "long-directory-name/".repeat(6) + "parser.ts", change: "modified" }];
      const visible = files.filter((file) => file.path.includes(url.searchParams.get("q") ?? ""));
      data = { enabled: state.enabled, paused: state.paused, base_sha: base, head_sha: head, merge_base_sha: mergeBase,
        base: read("base"), head: read(), files: visible, total: state.empty ? 0 : url.searchParams.get("q") ? visible.length : 11,
        page: pageNumber, has_more: pageNumber === 1, comparison_truncated: true };
    } else if (path === "/pulls/pr/code-graph/index") { state.status = "queued"; data = { job_id: "graph-job", commit_sha: head }; }
    else if (path === "/pulls/pr/code-graph/query") {
      if (state.fileError) return route.fulfill({ status: state.fileError, json: { detail: state.fileError === 409 ? "The pull request revision changed. Refresh impact before continuing." : "Code access is unavailable." } });
      const result = read(body.revision);
      if (body.operation === "dependencies") data = { ...result, items: state.empty ? [] : [{ ...location, path: related, kind: "dependency", target_path: selected,
        relationship: "imports_file", provenance: "relative_import" }] };
      if (body.operation === "symbols") data = { ...result, items: [{ ...location, path: body.path, kind: "symbol", key: "definition", name: body.offset ? "laterDefinition" : "validateResponse", symbol_kind: "function" }],
        next_cursor: !body.offset && state.definitionsPage ? { offset: 100, start_byte: 0 } : null };
      if (body.operation === "source") data = { ...result, source_state: state.missingSource ? "unavailable" : "available",
        truncated: body.start_byte === 0 && body.end_byte === 1048576, next_cursor: body.start_byte === 0 && body.end_byte === 1048576 ? { offset: 0, start_byte: 36 } : null,
        items: state.missingSource ? [] : [{ ...location, path: body.path, start_byte: body.start_byte, end_byte: body.start_byte + 36, start_line: body.start_byte ? 4 : 1,
          kind: "source", blob_sha: "d".repeat(40), text: body.start_byte ? "export const next = true;" : "export function validateResponse() {\n  return true;\n}" }] };
    }
    await route.fulfill({ json: data });
  });
  return { calls, state };
}

test("impact links keyboard navigation, definitions, exact source and saved findings", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  state.definitionsPage = true;
  await page.goto("/pulls/pr?tab=impact&graph_file=src%2Fservice.ts&severity=high&resolved_severity=low&resolved_page=4");
  const dependencies = page.getByRole("list", { name: "File dependencies" });
  await expect(dependencies).toBeVisible();
  await dependencies.getByRole("button").focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/graph_file=src%2Fconsumer.ts/);
  await expect(page.getByRole("heading", { name: "Selected file details" })).toBeFocused();
  await page.getByRole("tab", { name: "Definitions", exact: true }).click();
  await page.getByRole("button", { name: "Next definitions" }).click();
  await page.getByRole("button", { name: /laterDefinition/ }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("validateResponse");
  expect(calls.filter((call) => call.body?.operation === "source").at(-1)?.body).toMatchObject({ path: related, base_sha: base, head_sha: head, revision: "head", offset: 0, start_byte: 0, end_byte: 36 });
  await page.getByRole("button", { name: "Read from start" }).click();
  await page.getByRole("button", { name: "Next source" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("export const next");
  await page.getByRole("button", { name: "Previous source" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("validateResponse");
  await page.getByRole("link", { name: "Search resolved findings" }).click();
  await expect(page).toHaveURL(/tab=resolved/);
  await expect(page.getByRole("textbox", { name: "Search resolved findings" })).toHaveValue(related);
  expect(new URL(page.url()).searchParams.has("resolved_severity")).toBe(false);
  expect(new URL(page.url()).searchParams.has("resolved_page")).toBe(false);
  expect(calls.filter((call) => call.path.endsWith("/code-graph/index"))).toHaveLength(0);
});

test("filters and pagination preserve selection and use the merge base for removals", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=impact");
  await page.getByRole("button", { name: "Next changed files" }).click();
  await page.getByRole("button", { name: "src/old.ts Removed" }).click();
  await expect(page.getByRole("combobox", { name: "Revision", exact: true })).toHaveValue("base");
  await expect.poll(() => calls.filter((call) => call.body?.revision === "base").length).toBeGreaterThan(0);
  await page.getByRole("textbox", { name: "Filter changed files" }).fill("service");
  await page.getByRole("button", { name: "Filter", exact: true }).click();
  await expect(page).toHaveURL(/graph_q=service/);
  expect(new URL(page.url()).searchParams.has("graph_page")).toBe(false);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Filter changed files" })).toHaveValue("service");
});

test("explicit indexing, paused and disabled configuration do not admit hidden jobs", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  state.status = "unavailable";
  await page.goto("/pulls/pr?tab=impact");
  await expect(page.getByText("This revision is not ready", { exact: true })).toBeVisible();
  expect(calls.filter((call) => call.body)).toHaveLength(0);
  await page.getByRole("button", { name: "Index selected revision" }).click();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toBeDisabled();
  expect(calls.filter((call) => call.path.endsWith("/index"))).toHaveLength(1);
  state.paused = true;
  await page.getByRole("button", { name: "Refresh impact" }).click();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toHaveCount(0);
  state.enabled = false;
  await page.getByRole("button", { name: "Refresh impact" }).click();
  await expect(page.getByRole("link", { name: "Enable the code graph in Project context" })).toHaveAttribute("href", "/repositories/22/settings?tab=context");
});

test("members cannot index; empty and missing source remain explicit", async ({ page }) => {
  const { state } = await mockGraph(page, false);
  state.status = "unavailable";
  await page.goto("/pulls/pr?tab=impact&graph_file=src%2Fservice.ts");
  await expect(page.getByText("This revision is not ready", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toHaveCount(0);
  state.status = "ready"; state.empty = true;
  await page.getByRole("button", { name: "Refresh impact" }).click();
  await expect(page.getByText("No supported dependencies returned", { exact: true })).toBeVisible();
  state.missingSource = true;
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByText("Source unavailable", { exact: true })).toBeVisible();
  state.missingSource = false;
  await page.getByRole("button", { name: "Refresh file" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
});

for (const error of [403, 409]) test(`revoked or changed revision ${error} hides previous source`, async ({ page }) => {
  const { state } = await mockGraph(page);
  await page.clock.install();
  await page.goto("/pulls/pr?tab=impact");
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  state.fileError = error;
  await page.clock.runFor(31000);
  await expect(page.getByLabel("Indexed source", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh file" })).toBeVisible();
  state.overviewError = error;
  await page.getByRole("button", { name: "Refresh impact" }).click();
  await expect(page.getByRole("region", { name: "Selected file", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry impact" })).toBeVisible();
});

test("impact fits the app at both themes and required widths", async ({ page }, info) => {
  await mockGraph(page);
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    await page.goto("/pulls/pr?tab=impact");
    await expect(page.getByRole("list", { name: "File dependencies" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.impeccable/review/phase4-${info.project.name}-${theme}.png`, fullPage: true });
    if (info.project.name === "desktop" && theme === "dark") {
      await page.setViewportSize({ width: 2544, height: 1034 });
      await page.screenshot({ path: "../.impeccable/review/phase4-user-2544.png", fullPage: true });
    }
  }
});
