import { test, expect } from "@playwright/test";
import type { ErrorEvent } from "@sentry/react";
import { scrubEvent } from "../src/sentry";

test("Sentry keeps error stacks and removes private data", () => {
  const secret = "private-provider-key";
  const event: ErrorEvent = {
    type: undefined,
    request: { url: `https://example.com/callback?code=${secret}`, headers: { Authorization: secret } },
    user: { email: secret },
    extra: { api_key: secret },
    breadcrumbs: [{ message: secret }],
    message: secret,
    logentry: { message: secret },
    transaction: `/callback?code=${secret}`,
    exception: { values: [{ type: "Error", value: secret, stacktrace: { frames: [{ filename: "app.js", lineno: 42 }] } }] },
    tags: { service: "frontend" },
  };
  const clean = scrubEvent(event);
  expect(JSON.stringify(clean)).not.toContain(secret);
  expect(clean.exception?.values?.[0].stacktrace?.frames?.[0].lineno).toBe(42);
  expect(clean.tags?.service).toBe("frontend");
});

test("Sentry accepts errors without an exception payload", () => {
  expect(scrubEvent({ type: undefined, event_id: "id" })).toEqual({ event_id: "id" });
});

test("runtime Sentry settings send sanitized browser errors", async ({ page }) => {
  const origin = "https://sentry.example.com";
  const envelopes: string[] = [];
  await page.route(`${origin}/**`, async (route) => {
    envelopes.push(route.request().postData() ?? "");
    await route.fulfill({ status: 200, body: "{}" });
  });
  await page.route("**/api/browser-sentry", (route) => route.fulfill({
    json: { dsn: "https://public@sentry.example.com/1", environment: "staging", release: "candidate" },
  }));
  await page.route("**/api/v1/setup/status", (route) => route.fulfill({ json: { configured: false, public_url: "http://localhost" } }));
  const config = page.waitForResponse("**/api/browser-sentry");
  await page.goto("/?code=private-provider-key");
  await config;
  await expect(page.getByRole("button", { name: "Register GitHub App" })).toBeVisible();
  await page.evaluate(() => {
    setTimeout(() => { throw new Error("private-provider-key"); }, 0);
  });
  await expect.poll(() => envelopes.length).toBe(1);
  expect(envelopes[0]).not.toContain("private-provider-key");
  expect(envelopes[0]).toContain('"environment":"staging"');
  expect(envelopes[0]).toContain('"release":"candidate"');
  expect(envelopes[0]).toContain('"service":"frontend"');
  expect(envelopes[0]).toContain('"type":"Error"');
});

test("app setup loads while optional Sentry settings are pending", async ({ page }) => {
  let finishRequest!: () => void;
  const pending = new Promise<void>((resolve) => { finishRequest = resolve; });
  await page.route("**/api/browser-sentry", async (route) => {
    await pending;
    await route.fulfill({ status: 503 });
  });
  await page.route("**/api/v1/setup/status", (route) => route.fulfill({ json: { configured: false, public_url: "http://localhost" } }));
  const config = page.waitForRequest("**/api/browser-sentry");
  try {
    await page.goto("/");
    await config;
    await expect(page.getByRole("button", { name: "Register GitHub App" })).toBeVisible();
  } finally {
    finishRequest();
  }
});
