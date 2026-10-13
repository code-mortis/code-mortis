import * as Sentry from "@sentry/react";

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

const dsn = import.meta.env?.VITE_SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || import.meta.env.MODE,
    release: import.meta.env.VITE_SENTRY_RELEASE || undefined,
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
}
