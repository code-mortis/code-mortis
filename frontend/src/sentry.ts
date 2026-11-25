import * as Sentry from "@sentry/react";
import type { Schema } from "./api";

export function scrubEvent(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  // Error messages can contain credentials or private API responses.
  for (const key of ["request", "user", "extra", "breadcrumbs", "message", "logentry"] as const) {
    delete event[key];
  }
  delete event.transaction;
  for (const error of event.exception?.values ?? []) {
    error.value = "[redacted]";
  }
  return event;
}

export async function initBrowserSentry() {
  try {
    const response = await fetch("/api/browser-sentry", { credentials: "omit" });
    if (!response.ok) return;
    const config = await response.json() as Schema["BrowserSentryConfig"] | null;
    if (!config?.dsn) return;
    Sentry.init({
      dsn: config.dsn,
      environment: config.environment,
      release: config.release || undefined,
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
        stackFrameVariables: false,
        frameContextLines: 0,
      },
      defaultIntegrations: false,
      integrations: [
        Sentry.globalHandlersIntegration(),
        Sentry.dedupeIntegration(),
      ],
      maxBreadcrumbs: 0,
      beforeSend: scrubEvent,
      initialScope: { tags: { service: "frontend" } },
    });
  } catch {
    // Optional telemetry must not delay or interrupt app startup.
  }
}
