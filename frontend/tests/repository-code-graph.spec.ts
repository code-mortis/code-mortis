import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const commit = "a".repeat(40), base = "/repositories/22/code-graph";
const service = "src/service.ts", lastFile = "modules/file-04999.ts";
const file = (path: string, incoming = 0, outgoing = 0): Schema["RepositoryGraphNode"] =>
  ({ id: `file:${path}`, kind: "file", path, language: "typescript", incoming, outgoing, file_count: 1 });
const files = [
  file(service, 1, 1), file("src/consumer.ts", 0, 1), file("src/lib/dependency.ts", 1),
  { ...file("tests/unlinked.py"), language: "python" },
];
const edges = [{ source: files[1].id, target: files[0].id }, { source: files[0].id, target: files[2].id }];
const graph = (data: Partial<Schema["RepositoryGraphView"]> = {}): Schema["RepositoryGraphView"] => ({
  status: "ready", commit_sha: commit, observed_sha: commit, branch: "main", snapshot_id: "snapshot",
  mode: "files", nodes: files, edges, total_files: 4, total_links: 2, max_connections: 2, truncated: false, ...data,
});

async function mockRepository(page: Page, options: { admin?: boolean; dense?: boolean; denied?: boolean; disabled?: boolean } = {}) {
  const calls: URL[] = [];
  const denseFiles = options.dense ? Array.from({ length: 5000 }, (_, i) =>
    file(`modules/file-${String(i).padStart(5, "0")}.ts`, 4, 4)) : [];
  const denseEdges = denseFiles.flatMap((node, i) => [1, 7, 43, 251].map((offset) => ({
    source: node.id, target: denseFiles[(i + offset) % denseFiles.length].id,
  })));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    calls.push(url);
    const path = url.pathname.slice("/api/v1".length);
    let body: unknown = [];
    if (path === "/setup/status") body = { configured: true, public_url: url.origin };
    else if (path === "/auth/me") body = { id: 7, login: "developer", role: options.admin ? "admin" : "member", csrf: "csrf", avatar_url: "" };
    else if (path === "/repositories") body = [{ id: 22, installation_id: 10, full_name: "acme/project", private: true,
      active: true, enabled: true, code_graph_enabled: !options.disabled, connection_id: null, open_prs: 0, settings: {} }];
    else if (path === `${base}/view`) {
      const focus = url.searchParams.get("focus");
      if (options.disabled) body = graph({ status: "disabled", snapshot_id: null, commit_sha: null, nodes: [], edges: [],
        total_files: 0, total_links: 0, max_connections: 0 });
      else if (options.dense && focus) body = graph({ nodes: [file(focus)], edges: [], total_files: 1, total_links: 0 });
      else if (focus) {
        if (options.denied) return route.fulfill({ status: 403, json: { detail: "Graph access was revoked." } });
        await new Promise((resolve) => setTimeout(resolve, 150));
        const incoming = url.searchParams.get("direction") === "incoming";
        body = graph({ nodes: files.slice(0, incoming ? 2 : 3), edges: incoming ? edges.slice(0, 1) : edges,
          total_files: incoming ? 2 : 3, total_links: incoming ? 1 : 2 });
      } else if (url.searchParams.get("folder") === "src/lib") body = graph({ nodes: [files[2]], edges: [], total_files: 1, total_links: 0 });
      else if (url.searchParams.get("folder") || url.searchParams.get("hide_unlinked")) body = graph({ nodes: files.slice(0, 3), total_files: 3 });
      else if (options.dense) body = graph({ nodes: denseFiles, edges: denseEdges, total_files: 5000, total_links: 20000, max_connections: 8 });
      else body = graph();
    } else if (path === `${base}/files`) body = { files: options.dense ? [file(lastFile)] : [files[0]], truncated: false };
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
      sample.globalAlpha = 1;
      sample.fillStyle = style.getPropertyValue("--background").trim();
      sample.fillRect(0, 0, 1, 1);
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

test("only the hovered connections are painted above the dots", async ({ page }) => {
  await mockRepository(page);
  await page.route("**/api/v1" + base + "/view", (route) => route.fulfill({
    json: graph({ edges: [...edges, { source: files[1].id, target: files[2].id }], total_links: 3 }),
  }));
  await page.route("**/assets/repository-graph-layout.worker-*.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: "self.onmessage = ({ data }) => self.postMessage({ settled: true, positions: Object.fromEntries(data.nodes.map((node, i) => [node.id, { x: [0, -80, 80, 35][i], y: 0 }])) })",
  }));
  await page.addInitScript(() => {
    const context = CanvasRenderingContext2D.prototype;
    const { clearRect, fill, stroke } = context;
    context.clearRect = function (...args) {
      if (this.canvas.dataset.testid === "repository-code-graph-canvas") this.canvas.dataset.paint = "";
      clearRect.apply(this, args);
    };
    context.fill = function (...args: unknown[]) {
      if (this.canvas.dataset.paint !== undefined) this.canvas.dataset.paint += "dot\n";
      Reflect.apply(fill, this, args);
    };
    context.stroke = function (...args: unknown[]) {
      if (this.canvas.dataset.paint !== undefined) this.canvas.dataset.paint +=
        [this.strokeStyle, this.lineWidth, this.getLineDash().length ? "dashed" : "solid"].join(" ") + "\n";
      Reflect.apply(stroke, this, args);
    };
  });
  await page.goto(base);
  const paint = () => canvas(page).evaluate((element) => {
    const drawn = element.getAttribute("data-paint")?.trim().split("\n") ?? [];
    const swatch = document.createElement("canvas").getContext("2d")!;
    const stroke = (token: string, dash = "solid") => {
      swatch.strokeStyle = getComputedStyle(element).getPropertyValue(token).trim();
      return swatch.strokeStyle + " 1 " + dash;
    };
    return {
      back: drawn.slice(0, Math.max(0, drawn.indexOf("dot"))),
      front: drawn.slice(drawn.lastIndexOf("dot") + 1),
      muted: stroke("--muted-foreground"),
      incoming: stroke("--muted-foreground", "dashed"),
      outgoing: stroke("--primary"),
    };
  });
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await expect.poll(async () => (await paint()).back.length).toBe(3);
  expect((await paint()).front).toEqual([]);
  await canvas(page).hover();
  await expect(page.getByRole("tooltip")).toContainText("service.ts");
  await expect.poll(async () => {
    const { back, front, muted, incoming, outgoing } = await paint();
    return back.length === 1 && back[0] === muted
      && front.join("\n") === [incoming, muted, outgoing, outgoing].join("\n");
  }).toBe(true);
});

test("the graph header holds Filters and settings and the toolbar holds the revision", async ({ page }) => {
  const calls = await mockRepository(page, { admin: true });
  await page.route(`**/api/v1${base}/view`, (route) => route.fulfill({
    json: graph({ nodes: [files[0]], edges: [], total_files: 1, total_links: 0 }),
  }));
  await page.goto(base);
  const header = page.locator("header").filter({ has: page.getByRole("heading", { name: "Code graph", exact: true }) });
  const filters = header.getByRole("button", { name: "Filters" });
  const settings = header.getByRole("link", { name: "Code graph settings" });
  await expect(filters).toBeVisible();
  await expect(settings).toHaveAttribute("href", "/repositories/22/settings?tab=code-graph");
  const settingsBox = (await settings.boundingBox())!;
  const filtersBox = (await filters.boundingBox())!;
  expect(settingsBox.x).toBeGreaterThan(filtersBox.x + filtersBox.width);

  const badge = page.getByTitle(commit);
  await expect(badge.getByText(commit.slice(0, 7), { exact: true })).toBeVisible();
  const badgeBox = (await badge.boundingBox())!;
  expect(badgeBox.y).toBeGreaterThan(settingsBox.y + settingsBox.height);
  expect(badgeBox.x + badgeBox.width).toBeCloseTo(settingsBox.x + settingsBox.width, 0);

  const footer = page.locator("footer");
  const footerBox = (await footer.boundingBox())!;
  expect(footerBox.y + footerBox.height).toBeCloseTo(page.viewportSize()!.height, 0);

  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await canvas(page).click();
  await expect(page.getByText("3 files · 2 resolved imports")).toBeVisible();
  await expect(footer.getByText("service.ts", { exact: true })).toBeVisible();
  const imports = footer.getByText("1 imports · 1 imported by", { exact: true });
  await expect(imports).toBeVisible();
  await expect(footer.getByTitle(commit)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close file" })).toHaveCount(0);
  expect(calls.some((url) => url.pathname.endsWith(`${base}/source`))).toBe(false);
});

test("graph filters offer indexed folders and languages even after filtering", async ({ page }) => {
  const calls = await mockRepository(page);
  await page.goto(base);
  await page.getByRole("button", { name: "Filters" }).click();
  const dialog = page.getByRole("dialog", { name: "Filters" });
  const { x, width } = (await dialog.boundingBox())!;
  expect(width).toBeGreaterThan(240);
  expect(x + width).toBeLessThanOrEqual(page.viewportSize()!.width);

  const language = dialog.getByRole("combobox", { name: "Language", exact: true });
  const folder = dialog.getByRole("combobox", { name: "Folder" });
  await expect(language.locator("option")).toHaveText(["All languages", "Python", "TypeScript"]);
  await dialog.getByRole("button", { name: "Show options" }).click();
  await expect(page.getByRole("option", { name: "src", exact: true })).toBeVisible();
  await folder.fill("lib");
  await page.getByRole("option", { name: "src/lib", exact: true }).click();
  await expect(folder).toHaveValue("src/lib");
  await language.selectOption("typescript");
  await dialog.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByText("1 files · 0 resolved imports")).toBeVisible();
  expect(calls.some((url) => url.searchParams.get("folder") === "src/lib"
    && url.searchParams.get("language") === "typescript" && url.searchParams.get("commit_sha") === commit)).toBe(true);
  await expect(language.locator("option")).toHaveText(["All languages", "Python", "TypeScript"]);

  await dialog.getByRole("button", { name: "Clear filters" }).click();
  await expect(folder).toHaveValue("");
  await expect(language).toHaveValue("");
  await expect(page.getByText("4 files · 2 resolved imports")).toBeVisible();
});

test("selecting a search result focuses the file at the indexed revision without fetching source", async ({ page }, info) => {
  const calls = await mockRepository(page);
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Code graph" })).toBeVisible();
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Code graph", exact: true })).toHaveAttribute("aria-current", "page");
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Close navigation" }).click();
  await page.getByRole("button", { name: "Zoom out" }).click();
  const previousZoom = await zoom(page).innerText();
  const width = (await canvas(page).boundingBox())!.width;
  await page.getByRole("combobox", { name: "Find a file" }).fill("service.ts");
  await page.getByRole("option", { name: /service.ts/ }).click();
  await expect(page.getByText("3 files · 2 resolved imports")).toBeVisible();
  await expect(page.locator("footer").getByText("service.ts", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Close file" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "1 hop" })).toHaveAttribute("aria-pressed", "true");
  await expect(canvas(page)).toBeVisible();
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  expect((await canvas(page).boundingBox())!.width).toBe(width);
  if (info.project.name === "desktop") await page.screenshot({ path: info.outputPath("focused-code-graph.png") });
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
  await expect(page.getByRole("group", { name: "Connection depth" })).toBeVisible();
  await page.getByRole("button", { name: "Repository graph" }).click();
  await expect(zoom(page)).toHaveText(previousZoom);
  const pinned = calls.filter((request) => request.pathname.includes(base) && request.search);
  expect(pinned.some((request) => request.searchParams.get("focus") === service)).toBe(true);
  expect(pinned.every((request) => request.searchParams.get("commit_sha") === commit)).toBe(true);
  expect(pinned.some((request) => request.searchParams.get("depth") === "2" && request.searchParams.get("direction") === "incoming")).toBe(true);
  expect(calls.some((url) => url.pathname.endsWith(`${base}/source`))).toBe(false);
});

test("the 5,000-file overview is interactive before it settles and stays searchable", async ({ page }, info) => {
  test.setTimeout(45_000);
  const calls = await mockRepository(page, { dense: true });
  const workerScript = "**/assets/repository-graph-layout.worker-*.js";
  let releaseWorker = () => {};
  const workerGate = new Promise<void>((resolve) => { releaseWorker = resolve; });
  await page.route(workerScript, async (route) => {
    await workerGate;
    await route.continue();
  });
  const start = Date.now();
  try {
    await Promise.all([page.waitForRequest(workerScript), page.goto(base, { waitUntil: "domcontentloaded" })]);
    await expect(page.getByText("5000 files · 20000 resolved imports")).toBeVisible();
    await expect(canvas(page)).toHaveAttribute("data-ready", "false");
    const wheelAllowed = await canvas(page).evaluate((element) => element.dispatchEvent(new WheelEvent("wheel", {
      deltaY: -120, bubbles: true, cancelable: true,
    })));
    expect(wheelAllowed).toBe(true);
  } finally {
    releaseWorker();
  }
  await expect(canvas(page)).toHaveAttribute("data-ready", "true", { timeout: 15_000 });
  await expect(canvas(page)).toHaveAttribute("data-settled", "false");
  await expect(zoom(page)).toHaveText("100%");
  const overviewMs = Date.now() - start;
  await page.getByRole("button", { name: "Zoom out" }).click();
  await expect(zoom(page)).toHaveText("83%");
  await expect(canvas(page)).toHaveAttribute("data-settled", "true", { timeout: 30_000 });
  await expect(zoom(page)).toHaveText("83%");
  if (info.project.name === "desktop") await page.screenshot({ path: info.outputPath("repository-code-graph.png") });
  await openFirstNode(page);
  await expect(page.locator("footer").getByText("file-00000.ts", { exact: true })).toBeVisible();
  await expect(page.getByText("1 files · 0 resolved imports")).toBeVisible();
  expect(calls.some((url) => url.searchParams.get("focus") === "modules/file-00000.ts")).toBe(true);
  await page.getByRole("button", { name: "Repository graph" }).click();
  await expect(canvas(page)).toHaveAttribute("data-settled", "true");
  await expect(zoom(page)).toHaveText("83%");
  const search = page.getByRole("combobox", { name: "Find a file" });
  const focusStart = Date.now();
  await search.fill("file-04999");
  await expect(page.getByRole("option", { name: /file-04999.ts/ })).toBeVisible();
  await search.press("ArrowDown");
  await search.press("Enter");
  await expect(page.locator("footer").getByText("file-04999.ts", { exact: true })).toBeVisible();
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  info.annotations.push({ type: "timing", description: `Overview ${overviewMs} ms. Search and focus ${Date.now() - focusStart} ms.` });
  expect(calls.some((url) => url.searchParams.get("focus") === lastFile)).toBe(true);
});

test("access loss withholds the current graph", async ({ page }) => {
  await mockRepository(page, { denied: true });
  await page.goto(base);
  await page.getByRole("combobox", { name: "Find a file" }).fill("service.ts");
  await page.getByRole("option", { name: /service.ts/ }).click();
  await expect(page.getByRole("alert")).toContainText("Graph access was revoked");
  await expect(canvas(page)).toHaveCount(0);
  await expect(page.locator("footer")).toHaveCount(0);
});

test("disabled graphs show a readable empty state", async ({ page }) => {
  await mockRepository(page, { disabled: true });
  await page.goto(base);
  await expect(page.getByRole("heading", { name: "Code graph is off" })).toBeVisible();
  await expect(canvas(page)).toHaveCount(0);
});
