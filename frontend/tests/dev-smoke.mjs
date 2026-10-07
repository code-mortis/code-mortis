// Run explicitly with the Docker development stack already started.
import { chromium } from "@playwright/test";
import { stat, utimes, mkdir } from "node:fs/promises";
import assert from "node:assert/strict";

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  let sawUpdate;
  const updated = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("No Docker HMR update received")), 15000);
    sawUpdate = () => { clearTimeout(timeout); resolve(); };
  });
  page.on("websocket", (socket) => socket.on("framereceived", ({ payload }) => {
    const frame = JSON.parse(String(payload));
    if (frame.type === "update" && frame.updates.some((item) => item.path.includes("index.css"))) sawUpdate();
  }));
  await page.goto("http://localhost:5173/");
  await page.getByRole("heading", { name: "Give your reviews a home." }).waitFor();
  assert.equal((await page.request.get("http://localhost:5173/health/ready")).status(), 200);
  const path = new URL("../src/index.css", import.meta.url);
  const original = await stat(path);
  try {
    await utimes(path, original.atime, new Date());
    await updated;
    assert.equal(page.url(), "http://localhost:5173/");
  } finally {
    await utimes(path, original.atime, original.mtime);
  }
  await mkdir(new URL("../../.impeccable/review", import.meta.url), { recursive: true });
  await page.screenshot({ path: "../.impeccable/review/dev-setup-desktop.png", fullPage: true });
  console.log("Docker frontend, same-origin API proxy, ready health, fixed browser URL and CSS HMR passed.");
} finally {
  await browser.close();
}
