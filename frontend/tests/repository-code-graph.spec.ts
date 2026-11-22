import { expect, test, type Page } from "@playwright/test";
import type { Schema } from "../src/api";

const commit = "a".repeat(40), base = "/repositories/22/code-graph";
const service = "src/service.ts", lastFile = "modules/file-04999.ts";
const fileUrl = "/repositories/22/code/file?" + new URLSearchParams({ path: service, commit_sha: commit });
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
const sourcePage = (start: number, path = service): Schema["GraphRead"] => ({
  repository_id: 22, commit_sha: commit, snapshot_id: "snapshot", status: "ready", source_state: "available",
  policy_digest: null, extractor_version: "test", capabilities: {}, definition_languages: [], gaps: [],
  truncated: !start, next_cursor: start ? null : { offset: 0, start_byte: 18 },
  items: [{ kind: "source", path, start_byte: start, end_byte: start + 18, start_line: start ? 2 : 1,
    end_line: start ? 2 : 1, blob_sha: commit, text: start ? "// continued line\n" : "export const x=1;\n" }],
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
        body = files.some((node) => node.path === focus)
          ? graph({ nodes: files.slice(0, incoming ? 2 : 3), edges: incoming ? edges.slice(0, 1) : edges,
            total_files: incoming ? 2 : 3, total_links: incoming ? 1 : 2 })
          : graph({ nodes: [], edges: [], total_files: 0, total_links: 0 });
      } else if (url.searchParams.get("folder") === "src/lib") body = graph({ nodes: [files[2]], edges: [], total_files: 1, total_links: 0 });
      else if (url.searchParams.get("folder") || url.searchParams.get("hide_unlinked")) body = graph({ nodes: files.slice(0, 3), total_files: 3 });
      else if (options.dense) body = graph({ nodes: denseFiles, edges: denseEdges, total_files: 5000, total_links: 20000, max_connections: 8 });
      else body = graph();
    } else if (path === `${base}/files`) body = { files: options.dense ? [file(lastFile)] : [files[0]], truncated: false };
    else if (path === base + "/source") body = sourcePage(Number(url.searchParams.get("start_byte")));
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

test("wheel events in the same frame accumulate zoom", async ({ page }) => {
  await mockRepository(page);
  await page.goto(base);
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await expect(zoom(page)).toHaveText("100%");
  await canvas(page).evaluate((element) => {
    for (let i = 0; i < 2; i++) {
      element.dispatchEvent(new WheelEvent("wheel", { deltaY: -120, cancelable: true }));
    }
  });
  await expect(zoom(page)).toHaveText("132%");
});

test("settled graph dots pop on load and file navigation without moving their centers", async ({ page }) => {
  type Circle = [x: number, y: number, radius: number];
  await mockRepository(page);
  let workers = 0;
  page.on("worker", () => workers++);
  await page.route("**/assets/repository-graph-layout.worker-*.js", (route) => route.fulfill({
    contentType: "text/javascript",
    body: `self.onmessage = ({ data }) => {
      const positions = (x) => Object.fromEntries(data.nodes.map((node, i) =>
        [node.id, { x: [x, -80, 80, 0][i], y: [0, -40, 40, 0][i] }]));
      const preview = positions(-40);
      self.previewLayout = () => self.postMessage({ positions: preview, settled: false });
      self.settleLayout = () => self.postMessage({ positions: positions(40), settled: true });
      self.postMessage({ positions: { [data.nodes[0].id]: preview[data.nodes[0].id] }, settled: true });
    }`,
  }));
  await page.addInitScript(() => {
    const context = CanvasRenderingContext2D.prototype;
    const { clearRect, arc } = context;
    const frames: Circle[][] = [];
    Object.assign(window, { graphFrames: frames });
    context.clearRect = function (...args) {
      if (this.canvas.dataset.testid === "repository-code-graph-canvas") frames.push([]);
      clearRect.apply(this, args);
    };
    context.arc = function (...args) {
      if (this.canvas.dataset.testid === "repository-code-graph-canvas") {
        frames.at(-1)?.push([args[0], args[1], args[2]]);
      }
      arc.apply(this, args);
    };
  });

  for (const reducedMotion of ["no-preference", "reduce"] as const) {
    await page.emulateMedia({ reducedMotion });
    const [worker] = await Promise.all([page.waitForEvent("worker"), page.goto(base)]);
    const frames = () => page.evaluate(() => (window as Window & { graphFrames?: Circle[][] }).graphFrames ?? []);
    await expect(canvas(page)).toHaveAttribute("data-ready", "false");
    expect((await frames()).every((frame) => frame.length === 0)).toBe(true);
    await worker.evaluate("self.previewLayout()");
    await expect(canvas(page)).toHaveAttribute("data-ready", "false");
    await expect(canvas(page)).toHaveAttribute("data-settled", "false");
    expect((await frames()).every((frame) => frame.length === 0)).toBe(true);
    await worker.evaluate("setTimeout(self.settleLayout, 10)");
    await expect(canvas(page)).toHaveAttribute("data-settled", "true");
    await expect.poll(async () => {
      const frame = (await frames()).at(-1);
      return frame?.length === 4 ? frame[0][2] / frame[3][2] : 0;
    }).toBeCloseTo(11 / 4.5, 7);
    const visibleFrames = (await frames()).filter((frame) => frame.length);
    expect([...new Set(visibleFrames.map((frame) => frame.length))]).toEqual(reducedMotion === "reduce" ? [4] : [1, 3, 4]);
    const final = visibleFrames.at(-1)!;
    for (const frame of visibleFrames) {
      expect(frame.map(([x, y]) => [x, y])).toEqual(final.slice(-frame.length).map(([x, y]) => [x, y]));
    }
    for (const [index, circle] of final.entries()) {
      const radii = visibleFrames.flatMap((frame) => {
        const dot = frame.at(index - final.length);
        return dot ? [dot[2]] : [];
      });
      if (reducedMotion === "reduce") {
        expect(radii.every((radius) => radius === circle[2])).toBe(true);
      } else {
        expect(radii[0]).toBeLessThan(circle[2]);
        expect(Math.max(...radii)).toBeGreaterThan(circle[2]);
      }
    }
    if (reducedMotion === "reduce") continue;

    const clearFrames = () => page.evaluate(() => (window as Window & { graphFrames?: Circle[][] }).graphFrames?.splice(0));
    const searchForService = async () => {
      await page.getByRole("combobox", { name: "Find a file" }).fill("service");
      await page.getByRole("option", { name: /src\/service\.ts/ }).click();
    };
    const finishFocus = () => expect.poll(async () => {
      const frame = (await frames()).at(-1);
      return frame?.length === 3 ? frame[0][2] / frame[1][2] : 0;
    }).toBeCloseTo(11 / 7.5, 7);
    const expectFocusReveal = async (path: string) => {
      await expect(page).toHaveURL((url) => url.pathname === base && url.searchParams.get("focus") === path);
      await expect(page.getByRole("link", { name: "Open " + path, exact: true })).toBeVisible();
      await expect.poll(async () => (await frames()).some((frame) => frame.length === 2)).toBe(true);
      await finishFocus();
      expect(workers).toBe(1);
    };

    await clearFrames();
    await searchForService();
    await expectFocusReveal(service);
    const [x, y] = (await frames()).at(-1)![1];

    await clearFrames();
    await canvas(page).click({ position: { x, y } });
    await expectFocusReveal(files[1].path);

    await clearFrames();
    await page.getByRole("button", { name: "2 hops" }).click();
    await expect(page.getByRole("button", { name: "2 hops" })).toHaveAttribute("aria-pressed", "true");
    await expect(canvas(page)).toBeVisible();
    await finishFocus();
    expect((await frames()).filter((frame) => frame.length).every((frame) => frame.length === 3)).toBe(true);

    await clearFrames();
    await openFirstNode(page);
    await expectFocusReveal(service);

    await page.getByRole("button", { name: "Repository graph" }).click();
    await expect(page).toHaveURL((url) => url.pathname === base && !url.search);
    await expect(page.getByRole("link", { name: `Open ${service}`, exact: true })).toHaveCount(0);
    await expect(canvas(page)).toHaveAttribute("data-ready", "true");
    await clearFrames();
    await searchForService();
    await expectFocusReveal(service);
  }
});

test("node colors and sizes share the three connection bands", async ({ page }) => {
  await mockRepository(page);
  await page.route("**/api/v1" + base + "/view", (route) => route.fulfill({
    json: graph({ nodes: [file(service, 3, 1), files[1], file(files[2].path, 2), files[3]], max_connections: 4 }),
  }));
  await page.addInitScript(() => {
    const context = CanvasRenderingContext2D.prototype;
    const { clearRect, arc } = context;
    context.clearRect = function (...args) {
      if (this.canvas.dataset.testid === "repository-code-graph-canvas") this.canvas.dataset.radii = "";
      clearRect.apply(this, args);
    };
    context.arc = function (...args) {
      if (this.canvas.dataset.radii !== undefined) this.canvas.dataset.radii += args[2] + " ";
      arc.apply(this, args);
    };
  });
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
  const radii = (await canvas(page).getAttribute("data-radii"))!.trim().split(" ").map(Number);
  expect(radii).toHaveLength(4);
  expect(radii[1]).toBe(radii[3]);
  expect(radii[2] / radii[1]).toBeCloseTo(7.5 / 4.5);
  expect(radii[0] / radii[1]).toBeCloseTo(11 / 4.5);
});

test("the overview raises hovered connections in the selected direction", async ({ page }) => {
  const calls = await mockRepository(page);
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
      border: stroke("--border"),
      muted: stroke("--muted-foreground"),
      incoming: stroke("--muted-foreground", "dashed"),
      outgoing: stroke("--primary"),
    };
  });
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await expect.poll(async () => {
    const { back, border } = await paint();
    return back.length === 3 && back.every((stroke) => stroke === border);
  }).toBe(true);
  expect((await paint()).front).toEqual([]);
  await canvas(page).hover();
  await expect(page.getByRole("tooltip")).toContainText("service.ts");
  await expect.poll(async () => {
    const { back, front, border, muted, incoming, outgoing } = await paint();
    return back.length === 1 && back[0] === border
      && front.join("\n") === [incoming, muted, outgoing, outgoing].join("\n");
  }).toBe(true);
  for (const direction of ["incoming", "outgoing"]) {
    await page.getByRole("button", { name: "Filters" }).click();
    await page.getByLabel("Import direction", { exact: true }).selectOption(direction);
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await page.keyboard.press("Escape");
    await canvas(page).hover();
    await expect.poll(async () => {
      const { back, front, border, muted, incoming, outgoing } = await paint();
      const expected = direction === "incoming" ? [incoming, muted] : [outgoing, outgoing];
      return back.length === 2 && back.every((stroke) => stroke === border)
        && front.join("\n") === expected.join("\n");
    }).toBe(true);
  }
  expect(calls.some((url) => url.pathname.endsWith(base + "/view") && url.search)).toBe(false);
});

test("the graph toolbar places the revision before search and the counts at the right edge", async ({ page }) => {
  const calls = await mockRepository(page, { admin: true });
  await page.route(`**/api/v1${base}/view`, (route) => route.fulfill({
    json: graph({ nodes: [files[0]], edges: [], total_files: 1, total_links: 0 }),
  }));
  await page.goto(base);
  const header = page.locator("header").filter({ has: page.getByRole("heading", { name: "Code graph", exact: true }) });
  await expect(header.getByRole("heading", { name: "Code graph", exact: true })).toHaveCSS("font-size", "24px");
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
  const searchBox = (await page.getByRole("combobox", { name: "Find a file" }).boundingBox())!;
  expect(badgeBox.y).toBeGreaterThan(settingsBox.y + settingsBox.height);
  expect(badgeBox.x + badgeBox.width).toBeLessThan(searchBox.x);
  expect(Math.abs(badgeBox.y - searchBox.y)).toBeLessThan(4);

  const footer = page.locator("footer");
  const footerBox = (await footer.boundingBox())!;
  expect(footerBox.y + footerBox.height).toBeCloseTo(page.viewportSize()!.height, 0);

  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  await canvas(page).click();
  const counts = page.getByText("3 files · 2 resolved imports", { exact: true });
  await expect(counts).toBeVisible();
  const countsBox = (await counts.boundingBox())!;
  expect(countsBox.x + countsBox.width).toBeCloseTo(settingsBox.x + settingsBox.width, 0);
  await expect(footer.getByText("service.ts", { exact: true })).toBeVisible();
  const imports = footer.getByText("1 imports · 1 imported by", { exact: true });
  await expect(imports).toBeVisible();
  await expect(footer.getByTitle(commit)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close file" })).toHaveCount(0);
  expect(calls.some((url) => url.pathname.endsWith(`${base}/source`))).toBe(false);
});

test("the focused file link opens and pages through its exact indexed source", async ({ page }, info) => {
  const calls = await mockRepository(page);
  await page.route((url) => url.pathname.endsWith(base + "/source") && url.searchParams.get("start_byte") === "18",
    (route) => route.fulfill({ status: 503, json: { detail: "Source is temporarily unavailable." } }), { times: 1 });
  await page.goto(base);
  await openFirstNode(page);
  const footer = page.locator("footer");
  const link = footer.getByRole("link", { name: "Open " + service });
  await expect(link).toHaveAttribute("href", fileUrl);
  const icon = (await link.locator("svg").boundingBox())!;
  const imports = (await link.getByText("1 imports · 1 imported by", { exact: true }).boundingBox())!;
  expect(imports.x).toBeCloseTo(icon.x, 0);
  expect(imports.y).toBeGreaterThan(icon.y + icon.height);
  expect(calls.some((url) => url.pathname.endsWith(base + "/source"))).toBe(false);
  await link.hover();
  await expect(link.getByText("service.ts", { exact: true })).toHaveCSS("text-decoration-line", "none");

  await link.press("Enter");
  await expect(page).toHaveURL((url) => url.pathname + url.search === fileUrl);
  await expect(page.getByRole("heading", { name: "service.ts", exact: true })).toBeVisible();
  const code = page.getByRole("region", { name: "Indexed source" });
  await expect(code.getByText("export const x=1;")).toBeVisible();
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Repository sections" }).getByRole("link", { name: "Code graph", exact: true })).toHaveAttribute("aria-current", "page");
  if (info.project.name === "mobile") await page.getByRole("button", { name: "Close navigation" }).click();

  const next = page.getByRole("button", { name: "Next page" });
  const previous = page.getByRole("button", { name: "Previous page" });
  await next.press("Enter");
  await expect(page.getByRole("alert")).toContainText("Source is temporarily unavailable.");
  await expect(code).toHaveCount(0);
  await expect(next).toBeFocused();
  await expect(next).toBeDisabled();
  await expect(previous).toBeEnabled();
  await page.getByRole("button", { name: "Retry source" }).click();
  await expect(code.getByText("// continued line")).toBeVisible();
  await expect(next).toBeDisabled();
  await previous.press("Enter");
  await expect(code.getByText("export const x=1;")).toBeVisible();
  await expect(previous).toBeFocused();
  const reads = calls.filter((url) => url.pathname.endsWith(base + "/source"));
  expect(reads.map((url) => [url.searchParams.get("path"), url.searchParams.get("commit_sha"), url.searchParams.get("start_byte")]))
    .toEqual([[service, commit, "0"], [service, commit, "18"], [service, commit, "0"]]);
  await page.getByRole("link", { name: "Repository graph", exact: true }).click();
  await expect(page).toHaveURL((url) => url.pathname === base && !url.search);
});

test("the source viewer rejects a mismatched revision and handles denied access", async ({ page }) => {
  await mockRepository(page);
  let denied = false;
  await page.route("**/api/v1" + base + "/source?*", (route) => denied
    ? route.fulfill({ status: 403, json: { detail: "Graph access was revoked." } })
    : route.fulfill({ json: { ...sourcePage(0), commit_sha: "b".repeat(40) } }));
  await page.goto(fileUrl);
  await expect(page.getByRole("heading", { name: "Source unavailable" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Indexed source" })).toHaveCount(0);
  denied = true;
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Graph access was revoked.");
  await expect(page.getByRole("region", { name: "Indexed source" })).toHaveCount(0);
});

test("the file viewer keeps repository IDs within the route and renders unknown extensions", async ({ page }) => {
  const calls = await mockRepository(page);
  await page.goto(fileUrl.replace("/22/", "/22%2F..%2F22/"));
  await expect(page.getByRole("heading", { name: "File link is incomplete" })).toBeVisible();
  await expect(page.locator('[data-slot="page-title"]').getByRole("link", { name: "Repositories", exact: true })).toHaveAttribute("href", "/repositories");
  expect(calls.some((url) => url.pathname.endsWith("/source"))).toBe(false);

  const path = "src/notes.__proto__";
  await page.route("**/api/v1" + base + "/source?*", (route) => route.fulfill({ json: sourcePage(0, path) }));
  await page.goto("/repositories/22/code/file?" + new URLSearchParams({ path, commit_sha: commit }));
  await expect(page.getByRole("region", { name: "Indexed source" })).toContainText("export const x=1;");
  await expect(page.locator("code.hljs")).toHaveCount(0);
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

  await folder.fill("src");
  await page.getByRole("option", { name: "src", exact: true }).click();
  await expect(folder).toHaveValue("src");
  await page.goBack();
  await expect(folder).toHaveValue("src/lib");
  await expect(language).toHaveValue("typescript");
  await expect(page.getByText("1 files · 0 resolved imports")).toBeVisible();
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

test("graph links restore the focused file, depth, and filters on reload and through browser history", async ({ page }) => {
  const calls = await mockRepository(page);
  const link = base + "?" + new URLSearchParams({ focus: service, depth: "2", folder: "src", direction: "incoming" });
  const focusedFile = page.getByRole("link", { name: "Open " + service, exact: true });
  const twoHops = page.getByRole("button", { name: "2 hops" });
  await page.goto(link);
  await expect(focusedFile).toBeVisible();
  await expect(twoHops).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("2 files · 1 resolved imports")).toBeVisible();
  expect(calls.some(({ searchParams }) => searchParams.get("commit_sha") === commit
    && searchParams.get("focus") === service && searchParams.get("depth") === "2"
    && searchParams.get("folder") === "src" && searchParams.get("direction") === "incoming")).toBe(true);

  await page.getByRole("button", { name: "1 hop" }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("focus") === service && !url.searchParams.has("depth"));
  await page.getByRole("button", { name: "Repository graph" }).click();
  await expect(page).toHaveURL((url) => !url.searchParams.has("focus") && url.searchParams.get("folder") === "src");
  await expect(focusedFile).toHaveCount(0);

  await page.goBack();
  await expect(focusedFile).toBeVisible();
  await expect(page.getByRole("button", { name: "1 hop" })).toHaveAttribute("aria-pressed", "true");
  await page.goBack();
  await expect(page).toHaveURL((url) => url.pathname + url.search === link);
  await expect(twoHops).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByText("2 files · 1 resolved imports")).toBeVisible();
  await expect(twoHops).toHaveAttribute("aria-pressed", "true");
  await page.goForward();
  await expect(focusedFile).toBeVisible();
  await expect(page.getByRole("button", { name: "1 hop" })).toHaveAttribute("aria-pressed", "true");

  await page.goto(base + "?" + new URLSearchParams({ focus: "src/deleted file.ts" }));
  await expect(page.getByText("File not in the index")).toBeVisible();
  await page.getByRole("button", { name: "Repository graph" }).click();
  await expect(page).toHaveURL((url) => url.pathname === base && !url.search);
  await expect(canvas(page)).toHaveAttribute("data-ready", "true");
  expect(calls.some((url) => url.pathname.endsWith(`${base}/source`))).toBe(false);
});

test("the 5,000-file overview waits for the settled layout and stays searchable", async ({ page }, info) => {
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
    await expect(page.getByText("Arranging files…")).toBeVisible();
    await expect(page.getByRole("button", { name: "Zoom out" })).toBeDisabled();
    const wheelAllowed = await canvas(page).evaluate((element) => element.dispatchEvent(new WheelEvent("wheel", {
      deltaY: -120, bubbles: true, cancelable: true,
    })));
    expect(wheelAllowed).toBe(true);
  } finally {
    releaseWorker();
  }
  await expect(canvas(page)).toHaveAttribute("data-ready", "true", { timeout: 30_000 });
  await expect(canvas(page)).toHaveAttribute("data-settled", "true");
  await expect(zoom(page)).toHaveText("100%");
  const overviewMs = Date.now() - start;
  await page.getByRole("button", { name: "Zoom out" }).click();
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
