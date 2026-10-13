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

test("browser errors are sent as sanitized Sentry envelopes", async ({ page }) => {
  test.skip(!process.env.VITE_SENTRY_DSN, "Run against a build with VITE_SENTRY_DSN configured");
  const origin = new URL(process.env.VITE_SENTRY_DSN!).origin;
  const envelopes: string[] = [];
  await page.route(`${origin}/**`, async (route) => {
    envelopes.push(route.request().postData() ?? "");
    await route.fulfill({ status: 200, body: "{}" });
  });
  await page.route("**/api/v1/**", (route) => route.fulfill({ json: { configured: false, public_url: "http://localhost" } }));
  await page.goto("/?code=private-provider-key");
  await page.evaluate(() => {
    setTimeout(() => { throw new Error("private-provider-key"); }, 0);
  });
  await expect.poll(() => envelopes.length).toBe(1);
  expect(envelopes[0]).not.toContain("private-provider-key");
  expect(envelopes[0]).toContain('"service":"frontend"');
  expect(envelopes[0]).toContain('"type":"Error"');
});
