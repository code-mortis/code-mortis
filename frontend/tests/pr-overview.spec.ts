import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const base = "b".repeat(40), head = "a".repeat(40);
const repo = {
  id: 22, installation_id: 10, full_name: "code-mortis/code-mortis", private: false,
  active: true, enabled: true, settings: {}, open_prs: 1,
};
const groups = [
  { id: "run:0", title: "Graph query API", description: "Look up definitions and relationships at the selected revision.", paths: ["backend/code_mortis/code_graph/service.py", "backend/code_mortis/api/routers/pulls.py"] },
  { id: "run:1", title: "Repository graph explorer", description: "Explore related symbols and files in the repository graph.", paths: ["frontend/src/components/code-graph-explorer.tsx", "frontend/tests/code-graph-explorer.spec.ts"] },
  { id: "run:2", title: "PR file navigation", description: "Open related files next to the pull request diff.", paths: ["frontend/src/components/pr-code.tsx", "frontend/src/lib/use-pr-code.ts"] },
];
const sampleSummary: Schema["PullOverviewSummary"] = {
  run_id: "run",
  narrative: "The **Graph API** serves both the repository explorer and the PR Code tab from the selected graph snapshot.",
  groups,
  diagram: {
    nodes: [
      { id: "snapshot", label: "Saved snapshot", paths: ["backend/code_mortis/code_graph/store.py"] },
      { id: "api", label: "Graph API", paths: groups[0].paths },
      { id: "explorer", label: "Graph explorer", paths: groups[1].paths },
      { id: "navigation", label: "PR Code tab", paths: groups[2].paths },
    ],
    edges: [{ source: "snapshot", target: "api" }, { source: "api", target: "explorer" }, { source: "api", target: "navigation" }],
  },
};

async function mockOverview(page: Page, options: Partial<Schema["PullOverview"]> = {}) {
  const paths: string[] = [];
  const groupsRequested: string[] = [];
  const pr = {
    id: "pr", repository_id: 22, number: 57, title: "Add interactive repository code graphs",
    author: "developer", url: "https://github.com/code-mortis/code-mortis/pull/57", state: "merged",
    head_ref: "feat/interactive-code-graph", head_sha: head, base_sha: base, draft: false, fork: false,
    updated_at: "2026-11-22T18:20:00Z", open_findings: 0,
    latest_run: { id: "run", head_sha: head, base_sha: base, status: "completed", publication: "published",
      created_at: "2026-11-22T18:20:00Z", config: {}, report: {}, progress: {}, summary: { status: "completed" } },
  };
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    paths.push(path);
    let data: unknown = [];
    if (path === "/setup/status") data = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") data = { id: 7, login: "developer", role: "admin", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") data = [repo];
    else if (path === "/pulls/pr") data = { pull_request: pr, repository: repo, run_count: 1, has_active_run: false,
      latest_observed_run_id: "run", finding_counts: { open: 0, resolved: 0, dismissed: 0 }, can_write: true };
    else if (path === "/pulls/pr/overview") data = { base_sha: base, head_sha: head, summary: sampleSummary,
      description: "Adds an interactive graph explorer so reviewers can follow definitions and dependencies alongside the PR diff.", ...options };
    else if (path === "/pulls/pr/findings") data = { groups: [], total: 0, registered_total: 0, status_counts: {}, category_counts: {}, page_size: 10 };
    else if (path === "/pulls/pr/code/workspace") {
      const query = route.request().postDataJSON();
      groupsRequested.push(query.group);
      const files = groups.flatMap((group) => group.paths).map((path) => ({ path, change: "modified" }));
      const group = groups.find(({ id }) => id === query.group);
      data = { base_sha: base, head_sha: head, merge_base_sha: "c".repeat(40), policy_digest: "test",
        files, file_states: files.map(() => ({ complete: true, open_count: 0, resolved_count: 0, dismissed_count: 0 })),
        groups: groups.map(({ paths, ...value }) => ({ ...value, file_indexes: paths.map((path) => files.findIndex((file) => file.path === path)) })),
        path: group?.paths[0] ?? null, revision: "head", view: "diff", diff: null, graph: null };
    }
    await route.fulfill({ json: data });
  });
  return { paths, groupsRequested };
}

test("PR Overview uses saved diagram navigation and opens the filtered Code tab", async ({ page }, info) => {
  const screenshots = process.env.PR_OVERVIEW_SCREENSHOT_DIR;
  if (screenshots) {
    if (info.project.name === "desktop") await page.setViewportSize({ width: 1359, height: 900 });
    await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  }
  const { paths, groupsRequested } = await mockOverview(page);
  await page.goto("/pulls/pr");
  const tabs = page.getByRole("tablist", { name: "Pull request views" });
  await expect(tabs.getByRole("tab")).toHaveText([/^Findings/, /^Resolved/, /^Dismissed/, "Code", "Overview", /^Run history/]);
  await expect(tabs.getByRole("tab", { name: /^Findings/ })).toHaveAttribute("aria-selected", "true");
  expect(paths).not.toContain("/pulls/pr/overview");
  await tabs.getByRole("tab", { name: "Overview", exact: true }).click();
  await expect(page).toHaveURL(/tab=overview/);
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText(["PR description", "Mortis summary", "Change diagram", "Grouped changes"]);
  const description = panel.getByRole("region", { name: "PR description", exact: true });
  const summary = panel.getByRole("region", { name: "Mortis summary", exact: true });
  const authorMarkdown = description.locator(".markdown");
  const mortisMarkdown = summary.locator(".markdown");
  await expect(authorMarkdown).toBeHidden();
  await description.locator("summary").press("Enter");
  await expect(authorMarkdown).toBeVisible();
  await expect(mortisMarkdown).toBeVisible();
  const panelWidth = (await panel.boundingBox())!.width;
  for (const markdown of [authorMarkdown, mortisMarkdown]) {
    expect((await markdown.boundingBox())!.width).toBeCloseTo(panelWidth);
  }
  await description.locator("summary").click();
  await expect(authorMarkdown).toBeHidden();
  await summary.locator("summary").press("Space");
  await expect(mortisMarkdown).toBeHidden();
  await summary.locator("summary").press("Space");
  await expect(panel.locator("strong")).toHaveText("Graph API");
  const diagram = panel.getByRole("region", { name: "Change diagram", exact: true });
  const activeNode = diagram.getByRole("button", { name: "Graph API", exact: true });
  const idleNode = diagram.getByRole("button", { name: "Saved snapshot", exact: true });
  await expect(activeNode).toHaveAttribute("aria-pressed", "true");
  await diagram.locator("summary").click();
  await expect(activeNode).toBeHidden();
  await diagram.locator("summary").click();
  await expect(activeNode).toBeVisible();
  const rootNode = await idleNode.boundingBox();
  const apiNode = await activeNode.boundingBox();
  const explorerNode = await diagram.getByRole("button", { name: "Graph explorer", exact: true }).boundingBox();
  expect(rootNode!.y + rootNode!.height).toBeLessThan(apiNode!.y);
  expect(apiNode!.y + apiNode!.height).toBeLessThan(explorerNode!.y);
  const nodeColors = (node: Element) => {
    const { backgroundColor, borderTopColor } = getComputedStyle(node);
    return { backgroundColor, borderTopColor };
  };
  for (const mode of ["light", "dark"] as const) {
    const toggle = page.getByRole("button", { name: `Switch to ${mode} mode` });
    if (await toggle.isVisible()) await toggle.click();
    await expect(page.getByRole("button", { name: `Switch to ${mode === "dark" ? "light" : "dark"} mode` })).toBeVisible();
    await expect.poll(async () => {
      const [active, idle] = await Promise.all([activeNode.evaluate(nodeColors), idleNode.evaluate(nodeColors)]);
      return active.backgroundColor !== idle.backgroundColor && active.borderTopColor !== idle.borderTopColor;
    }, { message: `The selected node has a different background and border in ${mode} mode` }).toBe(true);
  }
  await expect(panel.locator("path[marker-end]")).toHaveCount(3);
  await expect(panel.getByRole("list", { name: "Diagram connections" })).toContainText("Graph API leads to PR Code tab");
  expect(paths).not.toContain("/pulls/pr/findings");
  expect(paths).not.toContain("/pulls/pr/code/workspace");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width);
  if (screenshots) {
    await mkdir(screenshots, { recursive: true });
    await page.screenshot({ path: join(screenshots, `pr-overview-${info.project.name}.png`), fullPage: true });
  }

  const change = panel.getByRole("region", { name: "Grouped changes" });
  await expect(change.getByRole("link", { name: "Open in Code" })).toBeVisible();
  const groupArticle = change.getByRole("article");
  const groupDescription = groupArticle.getByText(groups[0].description, { exact: true });
  expect((await groupDescription.boundingBox())!.width).toBeCloseTo((await groupArticle.getByRole("list").boundingBox())!.width);
  await change.locator("summary").click();
  await expect(change.getByRole("link", { name: "Open in Code" })).toBeHidden();
  await panel.getByRole("button", { name: "Saved snapshot" }).press("Enter");
  await expect(change.getByText("backend/code_mortis/code_graph/store.py", { exact: true })).toBeVisible();
  await expect(change.getByRole("link", { name: "Open in Code" })).toHaveCount(0);
  await panel.getByRole("button", { name: "Graph explorer", exact: true }).press("Space");
  await expect(change.getByRole("heading", { name: groups[1].title })).toBeVisible();
  let releaseCode!: () => void;
  const holdCode = new Promise<void>((resolve) => { releaseCode = resolve; });
  await page.route("**/api/v1/pulls/pr/code/workspace", async (route) => {
    await holdCode;
    await route.fallback();
  }, { times: 1 });
  const progress = page.getByRole("banner").getByRole("progressbar", { name: "Loading workspace" });
  await expect(progress).toHaveCount(0);
  try {
    await change.getByRole("link", { name: "Open in Code" }).click();
    await expect(progress).toBeVisible();
  } finally {
    releaseCode();
  }
  await expect(tabs.getByRole("tab", { name: "Code", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(new URL(page.url()).searchParams.get("code_group")).toBe("run:1");
  await expect.poll(() => groupsRequested[0]).toBe("run:1");
  await expect(page.getByRole("combobox", { name: "Change group" })).toHaveValue(groups[1].title);
  await expect(progress).toHaveCount(0);
  const code = page.getByRole("region", { name: "Pull request code", exact: true });
  const codeDescription = code.getByText(groups[1].description, { exact: true });
  await expect(codeDescription).toBeVisible();
  expect((await codeDescription.boundingBox())!.width).toBeCloseTo((await code.boundingBox())!.width);
  await page.goBack();
  await expect(tabs.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByRole("button", { name: "Graph explorer", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("PR Overview zooms, pans and fits the diagram while keeping nodes selectable", async ({ page, isMobile }) => {
  await mockOverview(page);
  await page.goto("/pulls/pr?tab=overview");
  const diagram = page.getByRole("region", { name: "Change diagram", exact: true });
  const viewport = diagram.getByRole("region", { name: "Diagram viewport" });
  const zoom = diagram.getByLabel("Graph zoom");
  const node = diagram.getByRole("button", { name: "Graph API", exact: true });
  const width = () => node.evaluate((element) => element.getBoundingClientRect().width);
  await expect(zoom).toHaveText("100%");
  const nodeWidth = await width();
  await diagram.getByRole("button", { name: "Zoom in" }).click();
  await expect(zoom).toHaveText("120%");
  await expect.poll(width).toBeCloseTo(nodeWidth * 1.2);

  await viewport.scrollIntoViewIfNeeded();
  const bounds = (await viewport.boundingBox())!;
  const x = Math.round(bounds.x + 8), y = Math.round(bounds.y + bounds.height / 2);
  const top = () => viewport.evaluate((element) => element.scrollTop);
  const beforeDrag = await top();
  if (isMobile) {
    const session = await page.context().newCDPSession(page);
    await session.send("Input.synthesizeScrollGesture", { x, y, yDistance: -60, gestureSourceType: "touch" });
    await session.detach();
  } else {
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y - 60, { steps: 4 });
    await page.mouse.up();
  }
  await expect.poll(top).toBeGreaterThan(beforeDrag);
  await expect(node).toHaveAttribute("aria-pressed", "true");
  await diagram.getByRole("button", { name: "Zoom out" }).click();
  await expect(zoom).toHaveText("100%");
  await diagram.getByRole("button", { name: "Zoom in" }).click();
  await diagram.getByRole("button", { name: "Fit graph" }).click();
  await expect.poll(() => viewport.evaluate((element) =>
    element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight)).toBe(true);
  expect(Number.parseInt(await zoom.innerText(), 10)).toBeLessThanOrEqual(100);
  await expect.poll(top).toBe(0);

  await diagram.getByRole("button", { name: "Graph explorer", exact: true }).click();
  await expect(page.getByRole("region", { name: "Grouped changes" }).getByRole("heading", { name: groups[1].title })).toBeVisible();
});

test("PR Overview safely renders author Markdown and leaves groups usable without a diagram", async ({ page }) => {
  await mockOverview(page, { summary: { ...sampleSummary, diagram: null }, description: [
    "**Author text**", "", "| Check | Result |", "| --- | --- |", "| Smoke | Passed |", "", "- [x] Verify Code", "",
    "[Unsafe](javascript:alert%281%29) ![Remote image](https://example.com/remote.png)", "",
    "<script>alert(1)</script>", "", "<iframe src='https://example.com/frame'></iframe>",
  ].join("\n") });
  await page.goto("/pulls/pr?tab=overview");
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByRole("heading", { level: 2 })).toHaveText(["PR description", "Mortis summary", "Grouped changes"]);
  await panel.getByRole("region", { name: "PR description", exact: true }).locator("summary").click();
  await expect(panel.getByRole("cell", { name: "Smoke" })).toBeVisible();
  await expect(panel.getByRole("checkbox")).toBeChecked();
  await expect(panel.getByRole("checkbox")).toBeDisabled();
  await expect(panel.getByText("Unsafe")).toBeVisible();
  await expect(panel.getByRole("link", { name: "Unsafe" })).toHaveCount(0);
  await expect(panel.locator("script, iframe, img")).toHaveCount(0);
  await panel.getByRole("button", { name: /PR file navigation/ }).click();
  await expect(panel.getByText(groups[2].paths[0], { exact: true })).toBeVisible();
});

test("PR Overview routes the PR #54 cycle around the nodes", async ({ page }, info) => {
  const nodes = [
    { id: "api", label: "Activity feed API" },
    { id: "storage", label: "Activity storage" },
    { id: "activity", label: "Repository activity view" },
    { id: "background", label: "Background generation" },
    { id: "security", label: "Security scans" },
    { id: "inbox", label: "Notification inbox UI" },
    { id: "context", label: "Context generation UI" },
    { id: "review", label: "Review detail views" },
  ];
  const edges = [
    ["background", "api"], ["security", "api"], ["api", "storage"], ["activity", "storage"],
    ["inbox", "api"], ["context", "background"], ["review", "api"], ["storage", "inbox"],
  ].map(([source, target]) => ({ source, target }));
  await mockOverview(page, { summary: {
    ...sampleSummary,
    diagram: { nodes: nodes.map((node) => ({ ...node, paths: groups[0].paths })), edges },
  } });
  await page.route((url) => url.pathname === "/pulls/pr", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(),
      "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
    } });
  });
  await page.goto("/pulls/pr?tab=overview");
  const viewport = page.getByRole("region", { name: "Diagram viewport" });
  await expect(viewport.getByRole("button")).toHaveText(nodes.map(({ label }) => label));
  await expect(viewport.locator("path[marker-end]")).toHaveCount(edges.length);

  // Sample the actual rendered curves, including returning edges in the cycle.
  const overlaps = await viewport.evaluate((element) => {
    const boxes = Array.from(element.querySelectorAll("button"), (button) => button.getBoundingClientRect());
    const paths = element.querySelectorAll<SVGPathElement>("path[marker-end]");
    return Array.from(paths, (path) => {
      const matrix = path.getScreenCTM()!;
      const length = path.getTotalLength();
      for (let distance = 0; distance <= length; distance += 1) {
        const { x, y } = path.getPointAtLength(distance).matrixTransform(matrix);
        if (boxes.some((box) => x > box.left + 2 && x < box.right - 2 && y > box.top + 2 && y < box.bottom - 2)) return true;
      }
      return false;
    });
  });
  expect(overlaps).toEqual(edges.map(() => false));
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width);
  const screenshots = process.env.PR_OVERVIEW_SCREENSHOT_DIR;
  if (screenshots) {
    await page.getByRole("button", { name: "Fit graph" }).click();
    await mkdir(screenshots, { recursive: true });
    await viewport.screenshot({ path: join(screenshots, `pr-overview-cycle-${info.project.name}.png`) });
  }
});

test("PR Overview keeps groups usable if the diagram layout fails", async ({ page }) => {
  await mockOverview(page, { summary: {
    ...sampleSummary,
    diagram: { ...sampleSummary.diagram!, edges: [{ source: "snapshot", target: "missing" }] },
  } });
  await page.goto("/pulls/pr?tab=overview");
  await expect(page.getByRole("status").filter({ hasText: "The diagram could not be displayed." })).toBeVisible();
  const changes = page.getByRole("region", { name: "Grouped changes" });
  await changes.getByRole("button", { name: /PR file navigation/ }).click();
  await expect(changes.getByRole("heading", { name: "PR file navigation" })).toBeVisible();
  await expect(changes.getByRole("link", { name: "Open in Code" })).toBeVisible();
});

test("PR Overview renders a saved self-loop and keeps its node label literal", async ({ page }) => {
  const label = `Revisión <lista> & “código” ${"W".repeat(40)}`;
  await mockOverview(page, { summary: {
    ...sampleSummary,
    diagram: {
      nodes: [{ id: "loop", label, paths: groups[0].paths }],
      edges: [{ source: "loop", target: "loop" }, { source: "loop", target: "loop" }],
    },
  } });
  await page.goto("/pulls/pr?tab=overview");
  const viewport = page.getByRole("region", { name: "Diagram viewport" });
  const node = viewport.getByRole("button", { name: label, exact: true });
  await expect(node).toBeVisible();
  expect(await node.evaluate((button) => {
    const text = button.firstElementChild!.getBoundingClientRect();
    const bounds = button.getBoundingClientRect();
    return text.top >= bounds.top && text.bottom <= bounds.bottom && text.width <= bounds.width;
  })).toBe(true);
  await expect(viewport.locator("path[marker-end]")).toHaveCount(1);
});

test("PR Overview omits a blank description and selects all groups sharing a diagram path", async ({ page }) => {
  const summary = {
    ...sampleSummary,
    diagram: { ...sampleSummary.diagram!, nodes: sampleSummary.diagram!.nodes.map((node) =>
      node.id === "explorer" ? { ...node, paths: [groups[0].paths[0], groups[1].paths[0]] } : node) },
  };
  await mockOverview(page, { description: "  ", summary });
  await page.goto("/pulls/pr?overview_change=node%3Arun%3Aexplorer");
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByRole("heading", { name: "PR description" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Graph explorer", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(panel.getByRole("link", { name: "Open in Code" })).toHaveCount(2);
  await expect(panel.getByRole("heading", { name: groups[0].title, exact: true })).toBeVisible();
  await expect(panel.getByRole("heading", { name: groups[1].title, exact: true })).toBeVisible();
});

test("PR Overview keeps author text without a review and preserves legacy findings links", async ({ page }) => {
  const { paths } = await mockOverview(page, { summary: null, description: "Author text only" });
  await page.goto("/pulls/pr?tab=overview&status=open");
  await expect(page.getByRole("heading", { name: "PR description" })).toBeVisible();
  await expect(page.getByText("Author text only")).toBeHidden();
  await page.getByRole("region", { name: "PR description", exact: true }).locator("summary").click();
  await expect(page.getByText("Author text only")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mortis summary" })).toHaveCount(0);
  const overviewReads = paths.filter((path) => path.endsWith("/overview")).length;
  await page.goto("/pulls/pr?q=retry");
  await expect(page.getByRole("tab", { name: /^Findings/ })).toHaveAttribute("aria-selected", "true");
  const search = page.getByRole("textbox", { name: "Search findings" });
  await expect(search).toHaveValue("retry");
  await search.clear();
  await expect.poll(() => new URL(page.url()).searchParams.get("q")).toBeNull();
  await expect(page.getByRole("tab", { name: /^Findings/ })).toHaveAttribute("aria-selected", "true");
  await page.route("**/api/v1/findings/old", (route) => route.fulfill({ status: 404, json: { detail: "Finding removed" } }));
  await page.goto("/pulls/pr?finding=old");
  await expect(page.getByRole("dialog", { name: "Finding details" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tab", { name: /^Findings/ })).toHaveAttribute("aria-selected", "true");
  await page.goto("/pulls/pr?tab=resolved");
  await expect(page.getByRole("tab", { name: /^Resolved/ })).toHaveAttribute("aria-selected", "true");
  expect(paths.filter((path) => path.endsWith("/overview"))).toHaveLength(overviewReads);
});
