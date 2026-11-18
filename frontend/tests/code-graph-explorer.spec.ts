import { expect, test, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

const head = "a".repeat(40), base = "b".repeat(40), mergeBase = "c".repeat(40);
const selected = "src/service.ts", related = "src/consumer.ts";
const location = { path: selected, start_byte: 0, end_byte: 36, start_line: 1, end_line: 3 };

export async function mockGraph(page: Page, admin = true) {
  const calls: { path: string; body: any }[] = [];
  const state = { reviewed: false, count: 0, resolvedCount: 0, dismissedCount: 0, fileCounts: {} as Record<string, { open_count: number; resolved_count: number; dismissed_count: number }>, countComplete: true, prState: "open", reviewRunning: false, userId: 7, policy: "test", padding: 0, enabled: true, status: "partial", baseStatus: "ready", paused: false, overviewError: 0,
    fileError: 0, codeError: 0, patchState: "available", unknownLineCounts: false, groups: false, moreFindings: false, verifiedHistory: false, verificationSource: head, reviewSource: head, historicalOnly: false, graphTruncated: false, large: false, empty: false, missingSource: false, long: false, definitionsPage: false, sourcePages: false, warningDetails: false };
  const repo = { id: 22, installation_id: 10, full_name: "acme/project", private: true, active: true,
    enabled: true, connection_id: null, open_prs: 1, settings: {} };
  const read = (revision = "head") => ({ repository_id: 22, commit_sha: revision === "head" ? head : mergeBase,
    snapshot_id: revision, status: revision === "head" ? state.status : state.baseStatus, extractor_version: "test",
    policy_digest: "test", capabilities: { defines: "supported", imports_file: "partial", callers: "unsupported", references: "unsupported" },
    definition_languages: ["python", "typescript"], gaps: [], truncated: state.graphTruncated, source_state: "not_requested", items: [], next_cursor: null,
    coverage: { parsed: 239, partial: (revision === "head" ? state.status : state.baseStatus) === "partial" ? 1 : 0,
      unsupported: 72, failed: 0, too_large: 0, visible_files: 312, complete: false,
      warning_files: state.warningDetails && (revision === "head" ? state.status : state.baseStatus) === "partial" ? [{ path: "src/parse-problem.ts", reason: "syntax_error", diagnostic: { start_byte: 50, line: 51, column: 20, context: "type_annotation", token: "import", missing_token: null } }] : [],
      reasons: (revision === "head" ? state.status : state.baseStatus) === "partial" ? { syntax_error: 1, unsupported_language: 72 } : { unsupported_language: 72 } } });
  const diff = (body: any) => ({ base_sha: base, head_sha: head, merge_base_sha: mergeBase,
        file: { path: body.path, change: "modified", additions: state.unknownLineCounts ? null : 1, deletions: state.unknownLineCounts ? null : 1 }, patch_state: state.patchState,
        lines: state.large && body.path === selected ? Array.from({ length: 2000 }, (_, i) => ({ kind: "addition", old_line: null, new_line: i + 1, text: "const line = " + i + ";" + "x".repeat(80) })) : state.patchState === "available" || state.patchState === "truncated" ? [
          { kind: "hunk", text: "@@ -1 +1 @@", old_line: null, new_line: null },
          { kind: "deletion", text: "return false;", old_line: 1, new_line: null },
          { kind: "addition", text: body.path === selected ? "return true; // mañana 😀" : "export const added = true;", old_line: null, new_line: 1 } ] : [],
        findings: state.groups ? [ { occurrence_id: "o1", finding_id: "f1", run_id: "run", source_sha: state.reviewSource, current_status: "open",
          snapshot: { path: selected, content: "Validate the response status", severity: "high", category: "bug", start_line: 1, end_line: 1, existing_code: "return true;", suggestion: "" }, side: "new", line: 1, placement: "inline" },
          { occurrence_id: "o2", finding_id: "f2", run_id: "old-run", source_sha: base, current_status: "resolved",
          snapshot: { path: selected, content: "Earlier saved evidence", start_line: 20, end_line: 22 }, side: state.verifiedHistory ? "new" : null, line: state.verifiedHistory ? 1 : null, placement: state.verifiedHistory ? "inline" : "historical",
          verification: state.verifiedHistory ? { id: "verification", verdict: "still_present", source_sha: state.verificationSource, start_line: 1, end_line: 1 } : null },
          ...(state.verifiedHistory ? [{ occurrence_id: "o2-older", finding_id: "f2", run_id: "older-run", source_sha: "e".repeat(40), current_status: "resolved",
            snapshot: { path: selected, content: "Earlier version of saved evidence", start_line: 30, end_line: 32 }, side: "new", line: 1, placement: "inline",
            verification: { id: "verification", verdict: "still_present", source_sha: state.verificationSource, start_line: 1, end_line: 1 } },
            { occurrence_id: "o2-oldest", finding_id: "f2", run_id: "oldest-run", source_sha: "f".repeat(40), current_status: "resolved",
              snapshot: { path: selected, content: "First saved review entry", start_line: 40, end_line: 42 }, side: "new", line: 1, placement: "inline",
              verification: { id: "verification", verdict: "still_present", source_sha: state.verificationSource, start_line: 1, end_line: 1 } }] : []),
          ...(state.moreFindings ? [
            { occurrence_id: "o3", finding_id: "f3", run_id: "run", source_sha: head, current_status: "resolved",
              snapshot: { path: selected, content: "Preserve response headers", severity: "medium", category: "bug", start_line: 1, end_line: 1 }, side: "new", line: 1, placement: "inline" },
            { occurrence_id: "o4", finding_id: "f4", run_id: "run", source_sha: mergeBase, current_status: "dismissed",
              snapshot: { path: selected, content: "Original response handling", severity: "low", start_line: 1, end_line: 1 }, side: "old", line: 1, placement: "inline" },
          ] : []) ].filter((item) => !state.historicalOnly || item.placement === "historical") : [],
        findings_truncated: false, github_url: "https://github.com/acme/project/compare/ccc...aaa" });
  const graph = (body: any) => {
    const result = read(body.revision);
      if (body.operation === "dependencies") return { ...result, items: state.empty ? [] : [{ ...location, path: related, kind: "dependency", target_path: selected,
        relationship: "imports_file", provenance: "relative_import" }] };
      if (body.operation === "symbols") return { ...result, items: [{ ...location, path: body.path, kind: "symbol", key: "definition", name: body.offset ? "laterDefinition" : "validateResponse", symbol_kind: "function" }],
        next_cursor: !body.offset && state.definitionsPage ? { offset: 100, start_byte: 0 } : null };
      if (body.operation === "source") return { ...result, source_state: state.missingSource ? "unavailable" : "available",
        truncated: state.sourcePages && body.start_byte === 0 && body.end_byte === 1048576, next_cursor: state.sourcePages && body.start_byte === 0 && body.end_byte === 1048576 ? { offset: 0, start_byte: 36 } : null,
        items: state.missingSource ? [] : [{ ...location, path: body.path, start_byte: body.start_byte, end_byte: body.start_byte + 36, start_line: body.start_byte ? 4 : 1,
          kind: "source", blob_sha: "d".repeat(40), text: (body.start_byte ? "export const next = true;" : "export function validateResponse() {\n  return true;\n}") + "x".repeat(state.padding) }] };
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const body = request.method() === "GET" ? null : request.postDataJSON();
    calls.push({ path, body });
    let data: unknown = [];
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: state.userId, login: "developer", role: admin ? "admin" : "member", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") data = [{ ...repo, has_running_review: state.reviewRunning }];
    else if (path === "/pulls/pr") data = { repository: repo, can_write: admin, run_count: 0, finding_counts: {}, has_active_run: false, cancellable_run_count: 0,
      pull_request: { id: "pr", repository_id: 22, repository_name: repo.full_name, number: 42, title: "Validate provider responses",
        author: "developer", state: state.prState, draft: false, fork: false, ignored: false, head_ref: "feature", head_sha: head, base_sha: base,
        created_at: "2026-10-27T12:00:00Z", updated_at: "2026-10-27T12:00:00Z", open_findings: 0, url: "https://github.com/acme/project/pull/42",
        review_status: "not_reviewed", has_running_review: false, run_id: null, publication: null, publication_error: null, latest_run: null } };
    else if (path.endsWith("/findings")) data = { groups: [], total: 0, registered_total: 0, status_counts: {}, category_counts: {}, page_size: 10 };
    else if (path === "/pulls/pr/code/workspace") {
      const error = state.overviewError || (body.view === "diff" ? state.codeError : state.fileError);
      if (error) return route.fulfill({ status: error, json: { detail: "Code access is unavailable." } });
      const files = state.empty ? [] : [
        { path: selected, change: "modified", additions: state.unknownLineCounts ? null : 1, deletions: state.unknownLineCounts ? null : 1 }, { path: "src/added.ts", change: "added", additions: 1, deletions: 0 },
        { path: "src/" + "long-directory-name/".repeat(6) + "parser.ts", change: "modified", additions: 1, deletions: 1 },
        ...Array.from({ length: 7 }, (_, i) => ({ path: `src/file${i}.ts`, change: "modified", additions: 1, deletions: 1 })),
        { path: "src/old.ts", change: "removed", additions: 0, deletions: 2 }];
      const groups = state.groups ? [{ id: "run:0", title: "Response validation", description: "Rejects malformed responses.", file_indexes: [0] }] : [];
      const file_states = files.map((file) => ({ reviewed: file.path === selected && state.reviewed, complete: state.countComplete,
        ...(state.fileCounts[file.path] ?? { open_count: file.path === selected ? state.count : 0,
          resolved_count: file.path === selected ? state.resolvedCount : 0, dismissed_count: file.path === selected ? state.dismissedCount : 0 }) }));
      const findings: string[] = body.findings ?? (body.with_findings ? ["open"] : []);
      const reviewed: boolean[] = body.reviewed ?? (body.unreviewed ? [false] : []);
      const visible = files.filter((file, i) => file.path.includes(body.query ?? "") && (!body.group || file.path === selected)
        && (!reviewed.length || reviewed.includes(file_states[i].reviewed)) && (!findings.length || findings.some((value) => value === "none"
          ? file_states[i].complete && file_states[i].open_count + file_states[i].resolved_count + file_states[i].dismissed_count === 0
          : !file_states[i].complete || (value === "any" ? file_states[i].open_count + file_states[i].resolved_count + file_states[i].dismissed_count
            : file_states[i][`${value}_count` as "open_count" | "resolved_count" | "dismissed_count"]) > 0)));
      const selectedPath = body.path ?? visible[((body.page ?? 1) - 1) * 10]?.path ?? null;
      if (selectedPath && body.view === "diff" && !files.some((file) => file.path === selectedPath)) {
        return route.fulfill({ status: 404, json: { detail: "This file is not available in the current comparison." } });
      }
      const revision = body.view !== "diff" && selectedPath === "src/old.ts" ? "base" : body.revision ?? "head";
      const normalized = { ...body, path: selectedPath, revision, operation: body.view === "definitions" ? "symbols" : body.view };
      data = { base_sha: base, head_sha: head, merge_base_sha: mergeBase, policy_digest: state.policy, files, file_states, groups,
        summary_run_id: state.groups ? "run" : null, comparison_truncated: true, metadata_truncated: false,
        graph_enabled: state.enabled, graph_paused: state.paused, path: selectedPath, revision, view: body.view,
        diff: selectedPath && body.view === "diff" ? diff(normalized) : null,
        graph: selectedPath && body.view !== "diff" && state.enabled ? graph(normalized) : null };
    } else if (path === "/pulls/pr/code-graph/index") { state.status = "queued"; data = { job_id: "graph-job", commit_sha: head }; }

    await route.fulfill({ json: data });
  });
  return { calls, state };
}

test("impact links keyboard navigation, definitions, exact source and saved findings", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  state.definitionsPage = true;
  state.sourcePages = true;
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
  expect(calls.filter((call) => call.body?.view === "source").at(-1)?.body).toMatchObject({ path: related, expected: { base_sha: base, head_sha: head, merge_base_sha: mergeBase }, revision: "head", offset: 0, start_byte: 0, end_byte: 36 });
  await page.getByRole("button", { name: "Read from start" }).click();
  await expect(page.getByRole("button", { name: "Previous source" })).toBeDisabled();
  await page.getByRole("button", { name: "Next source" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("export const next");
  await expect(page.getByRole("button", { name: "Next source" })).toBeDisabled();
  await page.getByRole("button", { name: "Previous source" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("validateResponse");
  await page.getByRole("link", { name: "Search resolved findings" }).click();
  await expect(page).toHaveURL(/tab=resolved/);
  await expect(page.getByRole("textbox", { name: "Search resolved findings" })).toHaveValue(related);
  expect(new URL(page.url()).searchParams.has("resolved_severity")).toBe(false);
  expect(new URL(page.url()).searchParams.has("resolved_page")).toBe(false);
  expect(calls.filter((call) => call.path.endsWith("/code-graph/index"))).toHaveLength(0);
});

test("graph-only files keep diff tabs unavailable without losing related navigation", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.clock.install();
  await page.goto("/pulls/pr?tab=code&code_view=dependencies");
  await page.getByRole("list", { name: "File dependencies" }).getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText(related);
  await expect(page.getByRole("button", { name: "Indexed with warnings", exact: true })).toBeVisible();
  const before = calls.filter((call) => call.path.endsWith("/workspace")).length;
  for (const name of ["Diff", "Findings"]) {
    const tab = page.getByRole("tab", { name, exact: true });
    await expect(tab).toBeDisabled();
    await tab.hover({ force: true });
    await tab.focus();
    await page.clock.runFor(300);
    await expect(tab).not.toBeFocused();
  }
  expect(calls.filter((call) => call.path.endsWith("/workspace"))).toHaveLength(before);
  await expect(page.getByRole("button", { name: "Retry Code" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "Dependencies", exact: true })).toBeFocused();
  await expect(page.getByRole("list", { name: "File dependencies" })).toBeVisible();
  expect(calls.some((call) => call.body?.view === "diff" && call.body?.path === related)).toBe(false);
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/service.ts/ }).click();
  await expect(page.getByRole("tab", { name: "Diff", exact: true })).toBeEnabled();
  await expect(page.getByRole("tab", { name: "Findings", exact: true })).toBeEnabled();
});

for (const view of ["source", "definitions", "definition range"] as const) test(`automatic file selection resets ${view} position`, async ({ page }) => {
  const { state, calls } = await mockGraph(page);
  state.sourcePages = true;
  state.definitionsPage = true;
  await page.goto(`/pulls/pr?tab=code&code_view=${view === "source" ? "source" : "definitions"}`);
  if (view === "source") {
    await page.getByRole("button", { name: "Next source" }).click();
    await expect(page.getByLabel("Indexed source", { exact: true })).toContainText("export const next");
  } else if (view === "definitions") {
    await page.getByRole("button", { name: "Next definitions" }).click();
    await expect(page.getByRole("button", { name: /laterDefinition/ })).toBeVisible();
  } else {
    await page.getByRole("button", { name: /validateResponse/ }).click();
    await expect(page.getByRole("button", { name: "Read from start" })).toBeVisible();
  }
  await page.getByRole("textbox", { name: "Filter changed files" }).fill("added.ts");
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("src/added.ts");
  await expect.poll(() => calls.filter(call => call.body?.path === "src/added.ts").length).toBeGreaterThan(0);
  for (const call of calls.filter(call => call.body?.path === "src/added.ts")) {
    expect(call.body).toMatchObject({ offset: 0, start_byte: 0, end_byte: 1048576 });
  }
  if (view !== "definitions") await expect(page.getByRole("button", { name: "Previous source" })).toBeDisabled();
});

test("complete source hides pagination while definition ranges retain read from start", async ({ page }, info) => {
  await mockGraph(page);
  await page.goto("/pulls/pr?tab=code&code_view=source&graph_file=src%2Fservice.ts");
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^(Previous|Next) source$/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Read from start" })).toHaveCount(0);
  await page.screenshot({ path: `../.impeccable/review/complete-source-${info.project.name}.png`, fullPage: true });
  await page.getByRole("tab", { name: "Definitions", exact: true }).click();
  await page.getByRole("button", { name: /validateResponse/ }).click();
  await expect(page.getByRole("button", { name: /^(Previous|Next) source$/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Read from start" }).click();
  await expect(page.getByRole("button", { name: "Read from start" })).toHaveCount(0);
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
});

test("status tooltips explain coverage without pushing down the files", async ({ page, isMobile }) => {
  const { state } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=impact");
  const impact = page.getByRole("region", { name: "Pull request code" });
  await expect(impact.locator("select")).toHaveCount(0);
  await expect(impact.getByRole("heading", { name: "Explore change impact" })).toHaveCount(0);
  await expect(impact.getByRole("button", { name: "Filters", exact: true })).toHaveCount(1);
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  const badge = page.getByRole("button", { name: "Indexed with warnings", exact: true });
  if (isMobile) await badge.tap(); else await badge.focus();
  await expect(page.getByRole("tooltip")).toContainText("1 file could only be partly parsed");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  state.status = "ready";
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await page.getByRole("button", { name: "Indexed", exact: true }).click();
  await expect(page.getByRole("tooltip")).toContainText("72 files use formats without structural support");
  await expect(page.getByRole("tooltip")).not.toContainText("partly parsed");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Limited comparison" }).click();
  await expect(page.getByRole("tooltip")).toContainText("at most 300 files");
});

test("indexing file warnings open a dialog outside the status tooltip", async ({ page }) => {
  const { state } = await mockGraph(page);
  state.warningDetails = true;
  await page.goto("/pulls/pr?tab=code&code_view=definitions");
  const warningButton = page.getByRole("button", { name: "View 1 warning", exact: true });
  await expect(warningButton).toHaveCSS("text-decoration-line", "underline");
  await expect(warningButton).toHaveCSS("padding-left", "0px");
  await expect(warningButton).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await warningButton.focus();
  await page.keyboard.press("Enter");
  const warnings = page.getByRole("dialog", { name: "Indexing warnings" });
  await expect(warnings.getByText("src/parse-problem.ts", { exact: true })).toBeVisible();
  await expect(warnings).toContainText('The parser could not read "import" within type annotation.');
  await expect(warnings).toContainText("Line 51, column 20");
  await page.keyboard.press("Escape");
  await expect(warnings).toHaveCount(0);
  await expect(warningButton).toBeFocused();
  await page.getByRole("button", { name: "Indexed with warnings", exact: true }).click();
  await expect(page.getByRole("tooltip")).toContainText("1 file could only be partly parsed");
  await expect(page.getByRole("tooltip").locator("summary")).toHaveCount(0);
});

test("revision uses the shared combobox with mouse, keyboard and touch", async ({ page, isMobile }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=impact");
  const impact = page.getByRole("region", { name: "Pull request code" });
  const revision = impact.getByRole("combobox", { name: "Revision", exact: true });
  const arrow = impact.getByRole("button", { name: "Show options", exact: true });
  if (isMobile) await arrow.tap(); else await arrow.click();
  await expect(page.getByRole("option")).toHaveCount(2);
  await page.getByRole("option", { name: "Merge base · ccccccc" }).click();
  await expect(revision).toHaveValue("Merge base · ccccccc");
  await expect(page).toHaveURL(/graph_revision=base/);
  await revision.fill("PR head");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(revision).toHaveValue("PR head · aaaaaaa");
  await arrow.click();
  await page.keyboard.press("Escape");
  await expect(revision).toHaveAttribute("aria-expanded", "false");
  expect(calls.filter((call) => call.path.endsWith("/code-graph/index"))).toHaveLength(0);
});

test("filters and pagination preserve selection and use the merge base for removals", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=impact");
  await page.getByRole("button", { name: "Next changed files" }).click();
  await page.getByRole("button", { name: /src\/old.ts removed/i }).click();
  await expect(page.getByRole("combobox", { name: "Revision", exact: true })).toHaveValue("Merge base · ccccccc");
  await expect.poll(() => calls.filter((call) => call.body?.revision === "base").length).toBeGreaterThan(0);
  await page.getByRole("textbox", { name: "Filter changed files" }).fill("service");
  await expect(page).toHaveURL(/graph_q=service/);
  expect(new URL(page.url()).searchParams.has("graph_page")).toBe(false);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Filter changed files" })).toHaveValue("service");
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Filter changed files" })).toHaveValue("");
});

test("graph views share revision controls beside their bounded-result notice", async ({ page, isMobile }, info) => {
  const { state, calls } = await mockGraph(page);
  state.padding = 220; state.graphTruncated = true;
  await page.goto("/pulls/pr?tab=code&code_view=dependencies");
  const file = page.getByRole("region", { name: "Selected file", exact: true });
  const header = file.getByRole("group", { name: "Code revision" });
  const revision = header.getByRole("combobox", { name: "Revision" });
  await expect(revision).toHaveCount(1);
  const notice = header.getByText("This result is bounded. More relationships or source may exist.", { exact: true });
  await expect(notice).toBeVisible();
  const headerBox = await header.boundingBox(), noticeBox = await notice.boundingBox();
  const controlBox = await header.locator('[data-slot="input-group"]').boundingBox();
  expect(Math.abs(headerBox!.x + headerBox!.width - controlBox!.x - controlBox!.width - (isMobile ? 16 : 20))).toBeLessThanOrEqual(1);
  if (!isMobile) expect(controlBox!.x).toBeGreaterThanOrEqual(noticeBox!.x + noticeBox!.width);
  await revision.fill("Merge base");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  for (const [view, name] of [["Dependencies", "File dependencies"], ["Definitions", "Indexed definitions"], ["Source", "Indexed source"]]) {
    await page.getByRole("tab", { name: view, exact: true }).click();
    const content = view === "Source" ? page.getByRole("region", { name, exact: true }) : page.getByRole("list", { name, exact: true });
    await expect(content).toBeVisible();
    if (view === "Definitions") await expect(header).toContainText("Symbols defined in this file.");
    if (view === "Source") await expect(header).toContainText("File contents at the selected revision.");
    await expect(content).toHaveCSS("border-left-width", "0px");
    await expect(content).toHaveCSS("border-radius", "0px");
    const panel = await file.locator('[data-slot="tabs"]').boundingBox();
    const body = await content.boundingBox();
    expect(Math.abs(body!.x - panel!.x - 1)).toBeLessThanOrEqual(1);
    await expect(revision).toHaveValue("Merge base · ccccccc");
    expect(await revision.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(calls.filter((call) => call.body?.view === view.toLowerCase()).at(-1)?.body.revision).toBe("base");
    if (view === "Source") {
      expect(await content.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
      await content.focus();
      await expect(content).toBeFocused();
    }
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await file.screenshot({ path: `../.impeccable/review/pr-code-flat-${view.toLowerCase()}-${info.project.name}.png` });
  }
  state.graphTruncated = false;
  await page.getByRole("button", { name: "Refresh Code", exact: true }).click();
  await expect(notice).toHaveCount(0);
  await expect(header).toContainText("File contents at the selected revision.");
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(revision).toHaveCount(0);
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("return false;");
  await page.getByRole("tab", { name: "Dependencies", exact: true }).click();
  await expect(revision).toHaveValue("Merge base · ccccccc");
  await page.reload();
  await expect(revision).toHaveValue("Merge base · ccccccc");
});

test("Code filtering keeps refresh beside search with groups and active filters", async ({ page }, info) => {
  const { state } = await mockGraph(page);
  state.groups = true; state.count = 3; state.graphTruncated = true;
  for (const width of [390, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/pulls/pr?tab=code&code_view=dependencies&code_unreviewed=1&code_findings=1");
    await expect(page.getByRole("button", { name: "Remove Review status filter" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Remove Findings filter" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Change group" })).toHaveValue("All changes");
    const search = await page.getByRole("textbox", { name: "Filter changed files" }).boundingBox();
    const refresh = await page.getByRole("button", { name: "Refresh Code", exact: true }).boundingBox();
    expect(Math.abs(search!.y - refresh!.y)).toBeLessThanOrEqual(1);
    expect(refresh!.x).toBeGreaterThanOrEqual(search!.x + search!.width);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.impeccable/review/pr-code-toolbar-${width}-${info.project.name}.png`, fullPage: true });
  }
});

test("explicit indexing, paused and disabled configuration do not admit hidden jobs", async ({ page, isMobile }, info) => {
  const { calls, state } = await mockGraph(page);
  state.status = "unavailable";
  await page.goto("/pulls/pr?tab=impact");
  await expect(page.getByText("This revision is not ready", { exact: true })).toBeVisible();
  const empty = page.getByRole("heading", { name: "This revision is not ready" }).locator("..");
  await expect(empty.getByRole("button", { name: "Index selected revision" })).toBeVisible();
  if (!isMobile) {
    const list = await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/service.ts modified/i }).boundingBox();
    const notice = await page.getByRole("tablist", { name: "File information" }).boundingBox();
    expect(Math.abs(notice!.y - list!.y)).toBeLessThanOrEqual(1);
  }
  await page.screenshot({ path: `../.impeccable/review/pr-code-graph-unavailable-${info.project.name}.png`, fullPage: true });
  expect(calls.filter((call) => call.body && !call.path.endsWith("/code/workspace"))).toHaveLength(0);
  await page.getByRole("button", { name: "Index selected revision" }).click();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toBeDisabled();
  expect(calls.filter((call) => call.path.endsWith("/index"))).toHaveLength(1);
  state.paused = true;
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toHaveCount(0);
  state.enabled = false;
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect(page.getByRole("link", { name: "Enable it in Code graph settings" })).toHaveAttribute("href", "/repositories/22/settings?tab=code-graph");
});

test("members cannot index; empty and missing source remain explicit", async ({ page }) => {
  const { state } = await mockGraph(page, false);
  state.status = "unavailable";
  await page.goto("/pulls/pr?tab=impact&graph_file=src%2Fservice.ts");
  await expect(page.getByText("This revision is not ready", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Index selected revision" })).toHaveCount(0);
  state.status = "ready"; state.empty = true;
  await page.getByRole("button", { name: "Refresh Code" }).click();
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
  await expect(page.getByRole("button", { name: "Retry Code" })).toBeVisible();
  state.overviewError = error;
  await page.getByRole("button", { name: "Retry Code" }).click();
  await expect(page.getByRole("region", { name: "Selected file", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry Code" })).toBeVisible();
});

test("impact fits the app at both themes and required widths", async ({ page }, info) => {
  await mockGraph(page);
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    await page.goto("/pulls/pr?tab=impact");
    await expect(page.getByRole("list", { name: "File dependencies" })).toBeVisible();
    if (info.project.name === "desktop") {
      const files = page.getByRole("region", { name: "Changed files", exact: true });
      const file = page.getByRole("region", { name: "Selected file", exact: true });
      const title = await files.getByRole("heading").locator("..").boundingBox();
      const path = await file.getByRole("heading").boundingBox();
      expect(Math.abs(title!.y - path!.y)).toBeLessThanOrEqual(1);
      const row = await files.getByRole("button", { name: /src\/service.ts modified/i }).boundingBox();
      const tabs = await file.getByRole("tablist", { name: "File information" }).boundingBox();
      expect(Math.abs(row!.y - tabs!.y)).toBeLessThanOrEqual(1);
    }
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `../.impeccable/review/pr-code-related-${info.project.name}-${theme}.png`, fullPage: true });
    if (info.project.name === "desktop" && theme === "dark") {
      await page.setViewportSize({ width: 1024, height: 768 });
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: "../.impeccable/review/pr-code-related-tablet-dark.png", fullPage: true });
      await page.setViewportSize({ width: 2544, height: 1034 });
      await page.screenshot({ path: "../.impeccable/review/pr-code-related-user-2544.png", fullPage: true });
    }
  }
});

test("Code opens a diff with graph disabled and no graph or inference requests", async ({ page }) => {
  const { state, calls } = await mockGraph(page);
  state.enabled = false;
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("tab", { name: "Code", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("mañana 😀");
  const diffTab = page.getByRole("tab", { name: "Diff", exact: true });
  const comparison = `Changes from merge base ${mergeBase} to PR head ${head}.`;
  await expect(diffTab).toHaveAttribute("title", comparison);
  await expect(diffTab).toHaveAttribute("aria-description", comparison);
  await expect(page.getByText("ccccccc…aaaaaaa", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Index selected/ })).toHaveCount(0);
  expect(calls.filter((call) => call.path.includes("code-graph"))).toHaveLength(0);
  expect(calls.filter((call) => call.body && !call.path.endsWith("/code/workspace"))).toHaveLength(0);
  await page.getByRole("tab", { name: "Dependencies", exact: true }).click();
  await expect(page.getByText("Code graph is disabled", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
});

test("diff reserves the findings column only when inline markers exist", async ({ page }, info) => {
  const { state } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff).toContainText("mañana 😀");
  await expect(diff.getByRole("columnheader")).toHaveText(["Original line", "New line", "Change"]);
  await expect(diff.locator("tbody tr").nth(1).getByRole("cell")).toHaveText(["1", "", "-return false;"]);
  await expect(diff.locator("tbody tr").nth(2).getByRole("cell")).toHaveText(["", "1", "+return true; // mañana 😀"]);
  await diff.scrollIntoViewIfNeeded();
  await page.screenshot({ path: `../.impeccable/review/pr-code-no-markers-${info.project.name}.png` });
  state.groups = true;
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect(diff.getByRole("button")).toHaveCount(1);
  await expect(diff.getByRole("columnheader")).toHaveText(["Findings", "Original line", "New line", "Change"]);
  await expect.poll(() => diff.locator("tbody tr").evaluateAll((rows) => rows.map((row) => row.children.length))).toEqual([4, 4, 4]);
  state.historicalOnly = true;
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect(diff.getByRole("button")).toHaveCount(0);
  await expect(diff.getByRole("columnheader")).toHaveText(["Original line", "New line", "Change"]);
  await expect.poll(() => diff.locator("tbody tr").evaluateAll((rows) => rows.map((row) => row.children.length))).toEqual([3, 3, 3]);
  await page.getByRole("tab", { name: "Findings", exact: true }).click();
  await expect(page.getByRole("region", { name: "File findings" })).toContainText("Earlier saved evidence");
});

test("saved groups filter files and findings open immutable evidence through reload", async ({ page, isMobile }) => {
  const { state, calls } = await mockGraph(page);
  state.groups = true;
  await page.goto("/pulls/pr?tab=code");
  const group = page.getByRole("combobox", { name: "Change group" });
  const arrow = page.getByRole("region", { name: "Pull request code" }).getByRole("button", { name: "Show options" });
  if (isMobile) await arrow.tap(); else await arrow.click();
  await page.getByRole("option", { name: "Response validation", exact: true }).click();
  await expect(page.getByText("Rejects malformed responses.", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { pressed: true })).toHaveCount(1);
  await page.getByRole("button", { name: /Validate the response status/ }).click();
  await expect(page.getByRole("dialog")).toContainText("Validate the response status");
  await expect(page.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Validate the response status");
  await page.keyboard.press("Escape");
  await page.getByRole("tablist", { name: "File information" }).getByRole("tab", { name: "Findings", exact: true }).click();
  await expect(page.getByRole("region", { name: "File findings" })).toContainText("Earlier revision · bbbbbbb");
  await group.fill("All changes"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
  await expect(page.getByText("Rejects malformed responses.", { exact: true })).toHaveCount(0);
  expect(calls.filter((call) => /summary|index$|review$/.test(call.path))).toHaveLength(0);
});

test("diff finding markers preview saved evidence and open the sidebar by mouse, keyboard and touch", async ({ page, isMobile }, info) => {
  const { state, calls } = await mockGraph(page); state.groups = true; state.moreFindings = true;
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  const marker = diff.getByRole("button", { name: "Open finding at new line 1: Validate the response status", exact: true });
  await expect(diff.locator("tbody tr")).toHaveCount(3);
  await expect(diff.locator("tbody tr").nth(1).getByRole("button")).toHaveCount(1);
  await expect(diff.locator("tbody tr").nth(2).getByRole("button")).toHaveCount(2);
  await expect(diff.getByText("Validate the response status", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "File findings" })).toHaveCount(0);
  const reads = calls.length;
  const preview = page.getByRole("tooltip");
  if (!isMobile) {
    await marker.hover();
    await expect(preview).toContainText("Validate the response status");
    await expect(preview).toContainText("high");
    await expect(preview).toContainText("Bug");
    await expect(preview).toContainText("open");
    await expect(preview).toContainText("src/service.ts:1");
    await page.mouse.move(0, 0, { steps: 5 });
    await expect(preview).not.toBeVisible();
  }
  await marker.scrollIntoViewIfNeeded();
  await marker.focus();
  await expect(preview).toBeVisible();
  const box = await preview.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/pr-code-finding-preview-${info.project.name}.png`, fullPage: false });
  await page.keyboard.press("Escape");
  await expect(preview).not.toBeVisible();
  expect(calls.length).toBe(reads);
  if (isMobile) await marker.tap(); else await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toContainText("Validate the response status");
  await expect(page.getByRole("tab", { name: "Saved evidence", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(preview).not.toBeVisible();
  await page.keyboard.press("Escape");
  await expect(marker).toBeFocused();
  const resolved = diff.getByRole("button", { name: "Open finding at new line 1: Preserve response headers", exact: true });
  await resolved.focus();
  await expect(page.getByRole("tooltip", { name: /Preserve response headers/ })).toContainText("resolved");
  await resolved.click();
  await expect(page.getByRole("dialog")).toContainText("Preserve response headers");
});

test("verified historical findings appear once in the diff and retain original evidence links", async ({ page, isMobile }) => {
  const { state } = await mockGraph(page); state.groups = true; state.verifiedHistory = true;
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff.getByRole("button")).toHaveCount(2);
  await expect(page.getByRole("region", { name: "File findings" })).toHaveCount(0);
  const marker = diff.getByRole("button", { name: "Open finding at new line 1: Earlier saved evidence", exact: true });
  if (isMobile) await marker.tap(); else {
    await marker.hover();
    const preview = page.getByRole("tooltip");
    await expect(preview).toContainText("resolved");
    await expect(preview).toContainText("Verification at aaaaaaa: still present");
    await expect(preview).toContainText("src/service.ts:1");
    await marker.click();
  }
  await expect(page.getByRole("dialog")).toContainText("Earlier saved evidence");
  await expect(page.getByRole("dialog")).toContainText("Saved at bbbbbbb");
  await page.goto("/pulls/pr?tab=code&code_occurrence=o2-older");
  await expect(page.getByRole("dialog")).toContainText("Earlier version of saved evidence");
  await expect(page.getByRole("dialog")).toContainText("Saved at eeeeeee");
  await page.keyboard.press("Escape");
  await expect(diff.getByRole("button")).toHaveCount(2);
  await expect(page.getByRole("list", { name: "Findings for this file", exact: true })).toBeHidden();
});

test("unchanged historical markers display the actual review and verification commits", async ({ page, isMobile }) => {
  const { state } = await mockGraph(page);
  state.groups = true;
  state.verifiedHistory = true;
  state.reviewSource = "d".repeat(40);
  state.verificationSource = "e".repeat(40);
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff.getByRole("button")).toHaveCount(2);
  const original = diff.getByRole("button", { name: "Open finding at new line 1: Validate the response status", exact: true });
  const verified = diff.getByRole("button", { name: "Open finding at new line 1: Earlier saved evidence", exact: true });
  if (!isMobile) {
    await original.hover();
    await expect(page.getByRole("tooltip")).toContainText("Evidence at ddddddd");
    await original.press("Escape");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await verified.focus();
    await expect(page.getByRole("tooltip")).toContainText("Verification at eeeeeee: still present");
    await expect(page.getByRole("tooltip")).not.toContainText("Verification at aaaaaaa");
  }
  await original.click();
  await expect(page.getByRole("dialog")).toContainText("Saved at ddddddd");
  await page.keyboard.press("Escape");
  await verified.click();
  await expect(page.getByRole("dialog")).toContainText("Earlier saved evidence");
});

test("Findings tab groups review entries and preserves each saved snapshot without another read", async ({ page, isMobile }, info) => {
  const { state, calls } = await mockGraph(page); state.groups = true; state.verifiedHistory = true; state.enabled = false;
  await page.goto("/pulls/pr?tab=code");
  const tabs = page.getByRole("tablist", { name: "File information" });
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff.getByRole("button")).toHaveCount(2);
  const reads = calls.length;
  const findings = tabs.getByRole("tab", { name: "Findings", exact: true });
  if (isMobile) await findings.tap(); else { await findings.focus(); await page.keyboard.press("Enter"); }
  await expect(page).toHaveURL(/code_view=findings/);
  const section = page.getByRole("region", { name: "File findings", exact: true });
  await expect(section.locator(":scope > p")).toHaveText("2 findings· 4 review entries");
  await expect(diff).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Revision" })).toHaveCount(0);
  const list = page.getByRole("list", { name: "Findings for this file", exact: true });
  await expect(list.locator(":scope > li")).toHaveCount(2);
  const card = list.locator('[data-slot="finding-card"]').filter({ hasText: "Validate the response status" });
  await expect(card).toHaveAttribute("aria-haspopup", "dialog");
  await expect(card).toContainText("high");
  await expect(card).toContainText("Bug");
  await expect(card).toContainText("src/service.ts");
  await expect(card).toContainText("open");
  const group = list.locator(":scope > li").filter({ hasText: "Earlier saved evidence" });
  await expect(group.getByRole("button")).toHaveCount(1);
  const history = group.locator("summary");
  await expect(history).toHaveText("3 review entries");
  await expect(group.locator("details")).not.toHaveAttribute("open", "");
  if (isMobile) await history.tap(); else { await history.focus(); await page.keyboard.press("Enter"); }
  const entries = group.getByRole("list", { name: "Review entries", exact: true });
  await expect(entries.getByRole("button")).toHaveCount(3);
  expect(calls.length).toBe(reads);
  await section.scrollIntoViewIfNeeded();
  await mkdir("../.impeccable/review", { recursive: true });
  await page.screenshot({ path: `../.impeccable/review/pr-code-finding-history-${info.project.name}.png` });
  for (const [name, sha] of [["Earlier saved evidence", "bbbbbbb"], ["Earlier version of saved evidence", "eeeeeee"], ["First saved review entry", "fffffff"]]) {
    const entry = entries.getByRole("button", { name: new RegExp(name) });
    if (isMobile) await entry.tap(); else await entry.click();
    await expect(page.getByRole("dialog")).toContainText(`Saved at ${sha}`);
    await expect(page.getByRole("dialog")).toContainText(name);
    await page.keyboard.press("Escape");
    await expect(entry).toBeFocused();
  }
  await entries.getByRole("button", { name: /First saved review entry/ }).click();
  await page.reload();
  await expect(page.getByRole("dialog")).toContainText("Saved at fffffff");
  await page.keyboard.press("Escape");
  await expect(findings).toHaveAttribute("aria-selected", "true");
  await expect(entries).toBeHidden();
  await history.click();
  await expect(entries).toBeVisible();
  await tabs.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(diff.getByRole("button")).toHaveCount(2);
  await expect(section).toHaveCount(0);
  await findings.click();
  await expect(entries).toBeHidden();
});

test("Findings tab has an empty state and keeps removed file navigation independent of the graph", async ({ page }, info) => {
  const { state, calls } = await mockGraph(page); state.enabled = false;
  await page.goto("/pulls/pr?tab=code&code_view=findings&graph_file=src%2Fold.ts&graph_page=2");
  const empty = page.getByRole("heading", { name: "No saved findings for this file", exact: true }).locator("..");
  await expect(empty).toBeVisible();
  await expect(empty).toHaveCSS("border-width", "0px");
  await expect(empty).toContainText("Findings from completed reviews appear here, including earlier revisions.");
  await page.getByRole("region", { name: "Selected file", exact: true }).screenshot({ path: `../.impeccable/review/pr-code-empty-findings-${info.project.name}.png` });
  expect(calls.find((call) => call.path.endsWith("/code/workspace"))?.body).toMatchObject({ view: "diff", path: "src/old.ts" });
  await page.getByRole("button", { name: "Previous file", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "File information" }).getByRole("tab", { name: "Findings", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("No saved findings for this file", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Revision" })).toHaveCount(0);
});

test("legacy base links retain merge-base source; new file navigation supports back", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=impact&graph_revision=base&graph_file=src%2Fold.ts");
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  expect(calls.find((call) => call.body?.view === "source")?.body.revision).toBe("base");
  await page.goto("/pulls/pr?tab=code");
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/added.ts/ }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("export const added");
  await page.goBack();
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("mañana");
});

for (const stateName of ["unavailable", "too_large", "withheld", "truncated"]) test(`diff ${stateName} stays explicit and graph-independent`, async ({ page }) => {
  const { state } = await mockGraph(page); state.patchState = stateName;
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("link", { name: "Open on GitHub", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "File diff" })).toHaveCount(stateName === "truncated" ? 1 : 0);
});

test("unavailable line counts stay unknown while known zero deletions remain visible", async ({ page }) => {
  const { state } = await mockGraph(page);
  state.unknownLineCounts = true;
  state.patchState = "unavailable";
  await page.goto("/pulls/pr?tab=code");
  const selectedFile = page.getByRole("button", { name: selected, exact: false }).filter({ hasText: "Modified" });
  await expect(selectedFile).toBeVisible();
  await expect(selectedFile).not.toContainText("+0");
  await expect(selectedFile).not.toContainText("−0");
  await expect(selectedFile).not.toContainText("+1");
  await expect(page.getByRole("button", { name: /src\/added.ts/ })).toContainText("+1 −0");
});

for (const error of [403, 409]) test(`diff refetch ${error} removes old content`, async ({ page }) => {
  const { state } = await mockGraph(page); await page.clock.install();
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  state.codeError = error; await page.clock.runFor(31000);
  await expect(page.getByRole("region", { name: "File diff" })).toHaveCount(0);
  await expect(page.getByText("Code access is unavailable.", { exact: true })).toBeVisible();
});

test("new selection cancels delayed diff and does not display stale text", async ({ page }) => {
  await mockGraph(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/v1/pulls/pr/code/workspace", async (route) => {
    if (route.request().postDataJSON().path === selected) await pending;
    await route.fallback();
  });
  await page.goto("/pulls/pr?tab=code&graph_file=src%2Fadded.ts");
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/service.ts/ }).click();
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/added.ts/ }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("export const added");
  release();
  await expect(page.getByRole("region", { name: "File diff" })).not.toContainText("mañana");
});

test("Code diff alignment and contained overflow at desktop, intermediate and mobile widths", async ({ page, isMobile }, info) => {
  const { state } = await mockGraph(page); state.groups = true;
  await mkdir("../.impeccable/review", { recursive: true });
  for (const theme of ["light", "dark"]) {
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    await page.goto("/pulls/pr?tab=code");
    await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
    for (const width of isMobile ? [390] : [1440, 1024]) {
      await page.setViewportSize({ width, height: isMobile ? 844 : 1000 });
      if (!isMobile) {
        const left = page.getByRole("region", { name: "Changed files", exact: true });
        const right = page.getByRole("region", { name: "Selected file", exact: true });
        const header = await left.getByRole("heading").locator("..").boundingBox();
        const title = await right.getByRole("heading", { name: "Selected file details" }).boundingBox();
        expect(Math.abs(header!.y - title!.y)).toBeLessThanOrEqual(1);
        const row = await left.getByRole("button", { name: /src\/service.ts/ }).boundingBox();
        const tabs = await right.getByRole("tablist").boundingBox();
        expect(Math.abs(row!.y - tabs!.y)).toBeLessThanOrEqual(1);
      }
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `../.impeccable/review/pr-code-${info.project.name}-${width}-${theme}.png`, fullPage: true });
    }
  }
});


test("bounded large diff stays scrollable and responsive", async ({ page }, info) => {
  const { state } = await mockGraph(page); state.large = true; state.patchState = "truncated";
  const started = Date.now();
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff.locator("tbody tr")).toHaveCount(2000);
  const firstLoadMs = Date.now() - started;
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const frameMs = await page.evaluate(() => new Promise<number>((resolve) => {
    const start = performance.now(); requestAnimationFrame(() => resolve(performance.now() - start));
  }));
  const navigation = Date.now();
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/added.ts/ }).click();
  await expect(diff).toContainText("export const added");
  await mkdir("../.impeccable/review", { recursive: true });
  await writeFile(`../.impeccable/review/pr-code-large-${info.project.name}.json`, JSON.stringify({ mocked: true, rows: 2000, firstLoadMs, navigationMs: Date.now() - navigation, frameMs }));
});


test("one bootstrap, cached file/view revisits and eviction on leaving Code", async ({ page }) => {
  const { calls } = await mockGraph(page);
  const reads = () => calls.filter((call) => call.path.endsWith("/code/workspace"));
  await page.goto("/pulls/pr?tab=code");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff).toContainText("mañana");
  expect(reads()).toHaveLength(1);
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/added.ts/ }).click();
  await expect(diff).toContainText("export const added");
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: /src\/service.ts/ }).click();
  await expect(diff).toContainText("mañana");
  expect(reads()).toHaveLength(2);
  for (const name of ["Dependencies", "Definitions", "Source"]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expect(page.getByRole("button", { name: "Indexed with warnings", exact: true })).toBeVisible();
  }
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(diff).toContainText("mañana");
  expect(reads()).toHaveLength(5);
  expect(calls.some((call) => call.path === "/pulls/pr/code-graph" || call.path.endsWith("/code-graph/query"))).toBe(false);
  await page.getByRole("tab", { name: "Findings 0", exact: true }).click();
  await page.getByRole("tab", { name: "Code", exact: true }).click();
  await expect(diff).toContainText("mañana");
  expect(reads()).toHaveLength(6);
});

test("inactive source expires from its fetch, not from cached revisits or other refreshes", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.clock.install();
  await page.goto("/pulls/pr?tab=code");
  const reads = () => calls.filter((call) => call.path.endsWith("/code/workspace") && call.body.view === "diff");
  const diff = page.getByRole("region", { name: "File diff" });
  await expect(diff).toBeVisible();
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await page.clock.runFor(15000);
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(diff).toBeVisible();
  expect(reads()).toHaveLength(1);
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await page.clock.runFor(17000);
  const before = reads().length;
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(diff).toBeVisible();
  expect(reads()).toHaveLength(before + 1);
});

test("idle Code does not poll hidden findings and keeps periodic calls bounded", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  state.prState = "merged";
  await page.clock.install();
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  const start = calls.length;
  await page.clock.runFor(61000);
  const periodic = calls.slice(start);
  expect(periodic.filter((call) => call.path.endsWith("/findings"))).toHaveLength(0);
  expect(periodic.length).toBeLessThanOrEqual(6);
});

test("Code on a closed PR refreshes the sidebar while a review is running", async ({ page, isMobile }) => {
  const { state } = await mockGraph(page);
  state.prState = "closed";
  state.reviewRunning = true;
  await page.clock.install();
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  if (isMobile) await page.getByRole("button", { name: "Open navigation" }).click();
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  const reviewing = nav.getByRole("link", { name: "acme/project, review running", exact: true });
  await expect(reviewing.locator(".motion-safe\\:animate-spin")).toBeVisible();

  state.reviewRunning = false;
  await page.clock.fastForward(6000);
  await expect(reviewing).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "acme/project", exact: true })).toBeVisible();
});

for (const initialFilter of [true, false]) test(`empty findings filter refreshes metadata and discovers new findings (${initialFilter ? "initial" : "local"} filter)`, async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  state.prState = "merged";
  await page.clock.install();
  await page.goto(`/pulls/pr?tab=code${initialFilter ? "&code_findings=open" : ""}`);
  if (!initialFilter) {
    await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
    await page.getByRole("button", { name: "Filters", exact: true }).click();
    await page.getByRole("menuitem", { name: "Findings", exact: true }).click();
    await page.getByRole("checkbox", { name: "With open findings", exact: true }).click();
    await page.keyboard.press("Escape");
  }
  const empty = page.getByText("No returned files match these filters.", { exact: true });
  await expect(empty).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh Code", exact: true })).toBeEnabled();
  const reads = () => calls.filter((call) => call.path.endsWith("/workspace"));
  const before = reads().length;
  for (const query of ["src", "src/service"]) {
    await page.getByRole("textbox", { name: "Filter changed files" }).fill(query);
    await page.clock.runFor(350);
    await expect(page).toHaveURL(new RegExp(`graph_q=${encodeURIComponent(query)}`));
    await expect(empty).toBeVisible();
  }
  expect(reads()).toHaveLength(before);
  await page.clock.runFor(61000);
  const refreshes = reads().slice(before);
  expect(refreshes.length).toBeGreaterThan(0);
  expect(refreshes.length).toBeLessThanOrEqual(3);
  for (const call of refreshes) expect(call.body).toMatchObject({ path: null, query: "src/service", findings: ["open"],
    expected: { base_sha: base, head_sha: head, merge_base_sha: mergeBase } });
  await expect(empty).toBeVisible();
  await expect(page.getByRole("region", { name: "File diff" })).toHaveCount(0);
  state.count = 1; state.groups = true;
  await page.clock.runFor(26000);
  await expect(page.getByRole("button", { name: `Open findings for ${selected}`, exact: true })).toHaveText("1");
  await expect(page.getByRole("region", { name: "File diff" })).toContainText("mañana");
  await expect(page).toHaveURL(/code_findings=open/);
});

test("permission loss purges other cached views before retry", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  state.fileError = 403;
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect(page.getByRole("button", { name: "Retry Code" })).toBeVisible();
  await expect(page.getByLabel("Indexed source", { exact: true })).toHaveCount(0);
  state.fileError = 0;
  await page.getByRole("button", { name: "Retry Code" }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  const start = calls.filter((call) => call.path.endsWith("/code/workspace")).length;
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace"))).toHaveLength(start + 1);
});


test("policy changes invalidate cached diffs and graph views", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  state.policy = "changed";
  await page.getByRole("button", { name: "Refresh Code" }).click();
  await expect.poll(() => calls.filter((call) => call.path.endsWith("/code/workspace")).length).toBe(3);
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace"))).toHaveLength(4);
});

test("content cache evicts beyond ten entries", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code");
  const files = page.getByRole("region", { name: "Changed files", exact: true });
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  for (let i = 1; i < 10; i++) {
    const rows = files.locator('button[aria-pressed]');
    const path = await rows.nth(i).locator("span").first().textContent();
    await rows.nth(i).click();
    await expect(page.getByRole("heading", { name: "Selected file details" })).toHaveText(path!);
    await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  }
  await page.getByRole("button", { name: "Next changed files" }).click();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toHaveText("src/old.ts");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  const before = calls.filter((call) => call.path.endsWith("/code/workspace") && call.body.path === selected).length;
  await page.getByRole("button", { name: "Previous changed files" }).click();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toHaveText(selected);
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace") && call.body.path === selected)).toHaveLength(before + 1);
});

test("byte budget evicts large source results while leaving them displayable", async ({ page }) => {
  const { calls, state } = await mockGraph(page); state.padding = 1000000;
  await page.goto("/pulls/pr?tab=code&code_view=source");
  const source = page.getByLabel("Indexed source", { exact: true });
  await expect(source).toBeVisible();
  const rows = page.getByRole("region", { name: "Changed files", exact: true }).locator('button[aria-pressed]');
  for (let i = 1; i < 6; i++) {
    const path = await rows.nth(i).locator("span").first().textContent();
    await rows.nth(i).click();
    await expect(page.getByRole("heading", { name: "Selected file details" })).toHaveText(path!);
    await expect(source).toBeVisible();
  }
  const before = calls.filter((call) => call.path.endsWith("/code/workspace")).length;
  await rows.first().click();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toHaveText(selected);
  await expect(source).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace"))).toHaveLength(before + 1);
});


test("a changed browser account remounts and reauthorizes the Code workspace", async ({ page }) => {
  const { calls, state } = await mockGraph(page);
  await page.clock.install();
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  await page.getByRole("tab", { name: "Source", exact: true }).click();
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await page.clock.runFor(16000);
  state.userId = 8;
  const before = calls.filter((call) => call.path.endsWith("/code/workspace")).length;
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => calls.filter((call) => call.path.endsWith("/code/workspace")).length).toBeGreaterThan(before);
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  const refreshed = calls.filter((call) => call.path.endsWith("/code/workspace")).length;
  await page.getByRole("tab", { name: "Diff", exact: true }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace"))).toHaveLength(refreshed + 1);
});


test("going offline removes retained code and requires a fresh read after reconnecting", async ({ page, context }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByRole("region", { name: "File diff" })).toHaveCount(0);
  await expect(page.getByText("You are offline. Connect to the internet, then retry Code.")).toBeVisible();
  await context.setOffline(false);
  const before = calls.filter((call) => call.path.endsWith("/code/workspace")).length;
  await page.getByRole("button", { name: "Retry Code" }).click();
  await expect(page.getByRole("region", { name: "File diff" })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/code/workspace"))).toHaveLength(before + 1);
});


test("file navigation crosses pages, preserves views and ignores editing and overlays", async ({ page }) => {
  const { calls } = await mockGraph(page);
  await page.goto("/pulls/pr?tab=code&graph_file=src%2Fservice.ts&code_view=source");
  await expect(page.getByLabel("Indexed source", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous file", exact: true })).toBeDisabled();
  const heading = page.getByRole("heading", { name: "Selected file details" });
  await heading.focus();
  await page.keyboard.press("Alt+ArrowDown");
  await expect(heading).toContainText("src/added.ts");
  await expect(heading).toBeFocused();
  await expect(page.getByRole("tab", { name: "Source", exact: true })).toHaveAttribute("aria-selected", "true");
  const before = page.url();
  await page.getByRole("textbox", { name: "Filter changed files" }).focus();
  await page.keyboard.press("Alt+ArrowDown");
  expect(page.url()).toBe(before);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.keyboard.press("Alt+ArrowDown");
  expect(page.url()).toBe(before);
  await page.keyboard.press("Escape");
  for (let i = 1; i < 10; i++) await page.getByRole("button", { name: "Next file", exact: true }).click();
  await expect(heading).toContainText("src/old.ts");
  await expect(page).toHaveURL(/graph_page=2/);
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toBeDisabled();
  expect(calls.filter((c) => c.path.endsWith("/workspace")).at(-1)?.body.revision).toBe("base");
  expect(calls.filter((c) => c.path.endsWith("/progress"))).toHaveLength(0);
});

test("reviewed files show an automatic check with no manual control", async ({ page }) => {
  const { state, calls } = await mockGraph(page);
  state.reviewed = true;
  await page.goto("/pulls/pr?tab=code");
  await expect(page.getByRole("img", { name: "Reviewed by Code Mortis", exact: true })).toHaveCount(1);
  await expect(page.getByRole("checkbox", { name: "Reviewed", exact: true })).toHaveCount(0);
  await expect(page.getByText("1 of 11 shown files reviewed", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("img", { name: "Reviewed by Code Mortis", exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Review status", exact: true }).click();
  await page.getByRole("checkbox", { name: "Not reviewed", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("src/added.ts");
  await expect(page.getByRole("img", { name: "Reviewed by Code Mortis", exact: true })).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/progress"))).toHaveLength(0);
});

test("finding indicators filter locally and recheck current evidence before opening the sheet", async ({ page }) => {
  const { state, calls } = await mockGraph(page);
  state.count = 1; state.groups = true;
  await page.goto("/pulls/pr?tab=code&code_view=source");
  await page.getByRole("button", { name: `Open findings for ${selected}`, exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Validate the response status");
  await expect(page).toHaveURL(/code_view=diff/);
  await page.keyboard.press("Escape");
  const before = calls.filter((c) => c.path.endsWith("/workspace")).length;
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With open findings", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { pressed: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toHaveCount(0);
  expect(calls.filter((c) => c.path.endsWith("/workspace"))).toHaveLength(before);
  state.groups = false; state.count = 0;
  await page.getByRole("button", { name: `Open findings for ${selected}`, exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Saved evidence unavailable");
  await expect(page.getByText("Validate the response status", { exact: true })).toHaveCount(0);
});

test("incomplete finding counts remain filterable without claiming zero", async ({ page }) => {
  const { state } = await mockGraph(page);
  state.countComplete = false;
  await page.goto("/pulls/pr?tab=code&code_findings=1");
  await expect(page.getByRole("button", { name: `Open findings for ${selected}`, exact: true })).toHaveText("?");
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toBeVisible();
});

test("finding filters include resolved historical evidence and retain selections on reload", async ({ page }, info) => {
  const { state, calls } = await mockGraph(page);
  state.groups = true; state.historicalOnly = true; state.resolvedCount = 1;
  await page.goto("/pulls/pr?tab=code");
  const indicator = page.getByRole("button", { name: `Open findings for ${selected}`, exact: true });
  await expect(indicator).toHaveText("1");
  const before = calls.filter((call) => call.path.endsWith("/workspace")).length;
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Findings", exact: true }).click();
  for (const name of ["With findings", "Without findings", "With open findings", "With resolved findings", "With dismissed findings"]) {
    await expect(page.getByRole("checkbox", { name, exact: true })).toBeVisible();
  }
  await page.screenshot({ path: `../.impeccable/review/pr-code-finding-filters-${info.project.name}.png`, fullPage: true });
  await page.getByRole("checkbox", { name: "With findings", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/code_findings=any/);
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toHaveCount(0);
  expect(calls.filter((call) => call.path.endsWith("/workspace"))).toHaveLength(before);
  await page.getByRole("button", { name: "Edit Findings: With findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With resolved findings", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "With findings", exact: true })).toBeChecked();
  await page.getByRole("checkbox", { name: "With findings", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.getByRole("button", { name: "Edit Findings: With resolved findings", exact: true })).toBeVisible();
  expect(calls.filter((call) => call.path.endsWith("/workspace")).at(-1)?.body.findings).toEqual(["resolved"]);
  await indicator.click();
  await expect(page.getByRole("dialog")).toContainText("Earlier saved evidence");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Edit Findings: With resolved findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With open findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With resolved findings", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByText("No returned files match these filters.", { exact: true })).toBeVisible();
  await page.getByRole("region", { name: "Changed files", exact: true }).getByRole("button", { name: "Clear filters", exact: true }).click();
  await expect(indicator).toBeVisible();
  await expect(page).not.toHaveURL(/code_findings=/);
});

test("dismissed and without-findings filters match row counts and preserve unknown counts", async ({ page }) => {
  const { state } = await mockGraph(page);
  state.groups = true; state.moreFindings = true; state.dismissedCount = 1;
  await page.goto("/pulls/pr?tab=code&code_findings=dismissed");
  await page.getByRole("button", { name: `Open findings for ${selected}`, exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Original response handling");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Edit Findings: With dismissed findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "Without findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With dismissed findings", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("src/added.ts");
  await expect(page.getByRole("button", { name: `Open findings for ${selected}`, exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("src/added.ts");
  state.countComplete = false;
  await page.getByRole("button", { name: "Refresh Code", exact: true }).click();
  await expect(page.getByText("No returned files match these filters.", { exact: true })).toBeVisible();
});

test("review status supports reviewed files and restores legacy unreviewed links", async ({ page }) => {
  const { state } = await mockGraph(page); state.reviewed = true;
  await page.goto("/pulls/pr?tab=code&code_unreviewed=1");
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("src/added.ts");
  await page.getByRole("button", { name: "Edit Review status: Not reviewed", exact: true }).click();
  await page.getByRole("checkbox", { name: "Reviewed", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "Not reviewed", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Reviewed", exact: true })).toBeChecked();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/code_reviewed=1/);
  await expect(page).not.toHaveURL(/code_unreviewed=/);
  expect(new URL(page.url()).searchParams.getAll("code_reviewed")).toEqual(["0", "1"]);
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText(selected);
  await page.reload();
  await expect(page.getByRole("img", { name: "Reviewed by Code Mortis", exact: true })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Remove Review status filter", exact: true }).click();
  await expect(page.getByRole("button", { name: "Next file", exact: true })).toBeVisible();
});

test("Code combines finding states with review status and restores multi-value navigation", async ({ page }, info) => {
  const { state, calls } = await mockGraph(page);
  state.count = 1; state.resolvedCount = 1; state.dismissedCount = 1; state.reviewed = true; state.groups = true;
  state.fileCounts = {
    "src/added.ts": { open_count: 0, resolved_count: 1, dismissed_count: 0 },
    "src/file0.ts": { open_count: 0, resolved_count: 0, dismissed_count: 1 },
  };
  await page.goto("/pulls/pr?tab=code&code_findings=open");
  const files = page.getByRole("region", { name: "Changed files", exact: true });
  const rows = files.locator('button[aria-pressed]');
  await expect(rows).toHaveCount(1);
  const before = calls.filter((call) => call.path.endsWith("/workspace")).length;
  await page.getByRole("button", { name: "Edit Findings: With open findings", exact: true }).click();
  await page.getByRole("checkbox", { name: "With resolved findings", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "With open findings", exact: true })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "With resolved findings", exact: true })).toBeChecked();
  await page.screenshot({ path: `../.impeccable/review/pr-code-multiple-filters-${info.project.name}.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await expect(rows).toHaveCount(2);
  await expect(files.getByRole("button", { name: `Open findings for ${selected}`, exact: true })).toHaveText("2");
  expect(calls.filter((call) => call.path.endsWith("/workspace"))).toHaveLength(before);
  expect(new URL(page.url()).searchParams.getAll("code_findings")).toEqual(["open", "resolved"]);
  await page.reload();
  await expect(rows).toHaveCount(2);
  expect(calls.filter((call) => call.path.endsWith("/workspace")).at(-1)?.body.findings).toEqual(["open", "resolved"]);
  await page.getByRole("button", { name: "Filters", exact: true }).click();
  await page.getByRole("menuitem", { name: "Review status", exact: true }).click();
  await page.getByRole("checkbox", { name: "Not reviewed", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("src/added.ts");
  await page.reload();
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("src/added.ts");
  await page.getByRole("button", { name: "Remove Review status filter", exact: true }).click();
  await expect(rows).toHaveCount(2);
  await page.goBack();
  await expect(rows).toHaveCount(1);
  await page.goForward();
  await expect(rows).toHaveCount(2);
});

test("workflow controls fit populated and empty states at three widths and both themes", async ({ page }, testInfo) => {
  const { state } = await mockGraph(page);
  state.count = 3; state.countComplete = false; state.reviewed = true;
  for (const width of [390, 1024, 1440]) for (const theme of ["light", "dark"]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
    await page.goto("/pulls/pr?tab=code");
    await expect(page.getByRole("button", { name: `Open findings for ${selected}`, exact: true })).toHaveText("3+");
    await expect(page.getByRole("img", { name: "Reviewed by Code Mortis", exact: true })).toHaveCount(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`workflow-${width}-${theme}.png`), fullPage: true });

  }
  state.empty = true;
  await page.reload();
  await expect(page.getByRole("heading", { name: "Selected file details" })).toContainText("Select a file");
  await expect(page.getByRole("checkbox", { name: "Reviewed", exact: true })).toHaveCount(0);
});

test("existing finding actions refresh row counts without changing automatic review checks", async ({ page }) => {
  const { state, calls } = await mockGraph(page);
  state.groups = true; state.count = 1;
  let status = "open";
  await page.route("**/api/v1/findings/f1", async (route) => {
    if (route.request().method() === "PATCH") {
      status = route.request().postDataJSON().status; state.count = 0; state.groups = false;
    }
    await route.fulfill({ json: { finding: { id: "f1", path: selected, content: "Validate the response status", status, severity: "high", category: "bug", start_line: 1, end_line: 1 }, activities: [], verifications: [] } });
  });
  await page.goto("/pulls/pr?tab=code");
  const badge = page.getByRole("button", { name: `Open findings for ${selected}`, exact: true });
  await badge.click();
  await page.getByRole("button", { name: "Resolve finding", exact: true }).click();
  await expect(badge).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByText("0 of 11 shown files reviewed", { exact: true })).toBeVisible();
  expect(calls.filter((c) => c.path.endsWith("/progress"))).toHaveLength(0);
});
