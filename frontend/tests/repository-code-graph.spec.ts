import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const commit = "a".repeat(40), base = "/repositories/22/code-graph";
const service = "src/service.ts", hidden = "modules/file-04999.ts";
const file = (path: string, incoming = 0, outgoing = 0): Schema["RepositoryGraphNode"] =>
  ({ id: `file:${path}`, kind: "file", path, language: "typescript", incoming, outgoing, file_count: 1 });
const files = [file(service, 1, 1), file("src/consumer.ts", 0, 1), file("src/dependency.ts", 1), file("src/unlinked.ts")];
const edges = [{ source: files[1].id, target: files[0].id }, { source: files[0].id, target: files[2].id }];
const graph = (data: Partial<Schema["RepositoryGraphView"]> = {}): Schema["RepositoryGraphView"] => ({
  status: "ready", commit_sha: commit, observed_sha: commit, branch: "main", snapshot_id: "snapshot",
  mode: "files", nodes: files, edges, total_files: 4, total_links: 2, max_connections: 2, truncated: false, ...data,
});

async function mockRepository(page: Page, options: { dense?: boolean; denied?: boolean; disabled?: boolean } = {}) {
  const calls: URL[] = [];
  const groups: Schema["RepositoryGraphNode"][] = Array.from({ length: 250 }, (_, i) => ({
    id: `group:${i}`, kind: "folder", path: `package-${i}`, language: null, file_count: 20, incoming: 4, outgoing: 4,
  }));
  const groupEdges = groups.flatMap((group, i) => Array.from({ length: 4 }, (_, offset) => ({
    source: group.id, target: groups[(i + offset + 1) % groups.length].id,
  })));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    calls.push(url);
    const path = url.pathname.slice("/api/v1".length);
    let body: unknown = [];
    if (path === "/setup/status") body = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") body = { id: 7, login: "developer", role: "member", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") body = [{ id: 22, installation_id: 10, full_name: "acme/project", private: true,
      active: true, enabled: true, code_graph_enabled: !options.disabled, connection_id: null, open_prs: 0, settings: {} }];
    else if (path === `${base}/view`) {
      const focus = url.searchParams.get("focus");
      if (options.disabled) body = graph({ status: "disabled", snapshot_id: null, commit_sha: null, nodes: [], edges: [],
        total_files: 0, total_links: 0, max_connections: 0 });
      else if (focus === hidden) body = graph({ nodes: [file(hidden)], edges: [], total_files: 1, total_links: 0 });
      else if (focus) {
        await new Promise((resolve) => setTimeout(resolve, 150));
        const incoming = url.searchParams.get("direction") === "incoming";
        body = graph({ nodes: files.slice(0, incoming ? 2 : 3), edges: incoming ? edges.slice(0, 1) : edges,
          total_files: incoming ? 2 : 3, total_links: incoming ? 1 : 2 });
      } else if (url.searchParams.get("folder") || url.searchParams.get("hide_unlinked")) body = graph({ nodes: files.slice(0, 3), total_files: 3 });
      else if (options.dense) body = graph({ mode: "folders", nodes: groups, edges: groupEdges, total_files: 5000, total_links: 20000 });
      else body = graph();
    } else if (path === `${base}/files`) body = { files: options.dense ? [file(hidden)] : [files[0]], truncated: false };
    else if (path === `${base}/source`) {
      if (options.denied) return route.fulfill({ status: 403, json: { detail: "Graph access was revoked." } });
      const start = Number(url.searchParams.get("start_byte") ?? 0);
      body = {
        repository_id: 22, commit_sha: commit, snapshot_id: "snapshot", status: "ready", policy_digest: "policy",
        capabilities: {}, definition_languages: ["typescript"], gaps: [], source_state: "available", extractor_version: "test",
        items: [{ kind: "source", path: url.searchParams.get("path")!, start_byte: start, end_byte: start + 40,
          start_line: start ? 2 : 1, end_line: start ? 2 : 1, text: start ? "export const next = 2;" : "export const indexed = true;", blob_sha: commit }],
        truncated: !start, next_cursor: start ? null : { offset: 0, start_byte: 40 },
      } satisfies Schema["GraphRead"];
    }
    await route.fulfill({ json: body });
  });
  return calls;
}

const canvas = (page: Page) => page.getByTestId("repository-code-graph-canvas");
const zoom = (page: Page) => page.getByLabel("Graph zoom");
async function openFirstNode(page: Page) {
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await canvas(page).press("ArrowRight");
  await canvas(page).press("Enter");
}

test("node colors use the three connection bands even in a small repository", async ({ page }) => {
  await mockRepository(page);
  await page.goto(base);
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await expect.poll(() => canvas(page).evaluate((element: HTMLCanvasElement) => {
    const ctx = element.getContext("2d")!;
    const pixels = ctx.getImageData(0, 0, element.width, element.height).data;
    const sampleCanvas = document.createElement("canvas");
    sampleCanvas.width = sampleCanvas.height = 1;
    const sample = sampleCanvas.getContext("2d")!;
    const style = getComputedStyle(element);
    return ["--muted-foreground", "--code-number", "--code-added"].every((token) => {
      sample.clearRect(0, 0, 1, 1);
      sample.globalAlpha = 0.82;
      sample.fillStyle = style.getPropertyValue(token).trim();
      sample.fillRect(0, 0, 1, 1);
      const [r, g, b, a] = sample.getImageData(0, 0, 1, 1).data;
      for (let offset = 0; offset < pixels.length; offset += 4) {
        if (pixels[offset] === r && pixels[offset + 1] === g && pixels[offset + 2] === b && pixels[offset + 3] === a) return true;
      }
      return false;
    });
  })).toBe(true);
});

test("file focus and source share an exact revision and the repository camera is restored", async ({ page }, info) => {
  const calls = await mockRepository(page);
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Code graph" })).toBeVisible();
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Code graph", exact: true })).toHaveAttribute("aria-current", "page");
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Close navigation" }).click();
  await page.getByRole("button", { name: "Zoom out" }).click();
  const previousZoom = await zoom(page).innerText();
  const width = (await canvas(page).boundingBox())!.width;
  await openFirstNode(page);
  const panel = page.getByRole("complementary", { name: "service.ts" });
  await expect(panel.getByText("export const indexed = true;")).toBeVisible();
  await expect(page.getByRole("button", { name: "1 hop" })).toHaveAttribute("aria-pressed", "true");
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  expect((await canvas(page).boundingBox())!.width).toBe(width);
  if (info.project.name === "desktop") {
    const rightmost = await canvas(page).evaluate((canvas: HTMLCanvasElement) => {
      const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
      let right = 0;
      for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 128) right = Math.max(right, ((i - 3) / 4) % canvas.width);
      return canvas.getBoundingClientRect().left + right * canvas.clientWidth / canvas.width;
    });
    expect(rightmost).toBeLessThan((await panel.boundingBox())!.x);
    await page.screenshot({ path: info.outputPath("focused-code-graph.png") });
  }
  await page.getByRole("button", { name: "Next lines" }).click();
  await expect(panel.getByText("export const next = 2;")).toBeVisible();
  await page.getByRole("button", { name: "2 hops" }).click();
  await expect(page.getByRole("button", { name: "2 hops" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Filters" }).click();
  await page.getByLabel("Import direction").selectOption("incoming");
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByText("2 files · 1 resolved imports")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByLabel("Import direction")).toHaveValue("both");
  await expect(page.getByText("3 files · 2 resolved imports")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Close file" }).click();
  await expect(panel).toHaveCount(0);
  await expect(canvas(page)).toBeFocused();
  await expect(page.getByRole("group", { name: "Connection depth" })).toBeVisible();
  await page.getByRole("button", { name: "Repository graph" }).click();
  await expect(zoom(page)).toHaveText(previousZoom);
  const pinned = calls.filter((request) => request.pathname.includes(base) && request.search);
  expect(pinned.every((request) => request.searchParams.get("commit_sha") === commit)).toBe(true);
  expect(pinned.some((request) => request.searchParams.get("depth") === "2" && request.searchParams.get("direction") === "incoming")).toBe(true);
});

test("bounded 5,000-file graph stays searchable beyond displayed folders", async ({ page }, info) => {
  const calls = await mockRepository(page, { dense: true });
  const start = Date.now();
  await page.goto(base);
  await expect(page.getByText("5000 files · 20000 resolved imports")).toBeVisible();
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  const overviewMs = Date.now() - start;
  if (info.project.name === "desktop") await page.screenshot({ path: info.outputPath("repository-code-graph.png") });
  await openFirstNode(page);
  await expect(page.getByText("3 files · 2 resolved imports")).toBeVisible();
  expect(calls.some((url) => url.searchParams.get("folder") === "package-0")).toBe(true);
  await page.getByRole("button", { name: "Repository graph" }).click();
  const search = page.getByRole("combobox", { name: "Find a file" });
  const focusStart = Date.now();
  await search.fill("file-04999");
  await expect(page.getByRole("option", { name: /file-04999.ts/ })).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await expect(page.getByRole("complementary", { name: "file-04999.ts" })).toBeVisible();
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  info.annotations.push({ type: "timing", description: `Overview ${overviewMs} ms. Search and focus ${Date.now() - focusStart} ms.` });
  expect(calls.some((url) => url.searchParams.get("focus") === hidden)).toBe(true);
});

test("access loss withholds the current graph and source", async ({ page }) => {
  await mockRepository(page, { denied: true });
  await page.goto(base);
  await openFirstNode(page);
  await expect(page.getByRole("alert")).toContainText("Graph access was revoked");
  await expect(canvas(page)).toHaveCount(0);
  await expect(page.getByLabel("Indexed source")).toHaveCount(0);
});

test("disabled graphs show a readable empty state", async ({ page }) => {
  await mockRepository(page, { disabled: true });
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Code graph is off" })).toBeVisible();
  await expect(canvas(page)).toHaveCount(0);
});
