"""Optional error reporting without request, credential or provider payloads."""

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration

from .config import settings


def scrub_event(event, hint):
    # Exception messages can contain arbitrary provider credentials, even with PII disabled.
    for key in ("request", "user", "extra", "breadcrumbs", "message", "logentry"):
        event.pop(key, None)
    for error in event.get("exception", {}).get("values", []):
        error["value"] = "[redacted]"
    return event


def init_sentry(service):
    config = settings()
    if not config.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=config.sentry_dsn,
        environment=config.sentry_environment,
        release=config.sentry_release or None,
        default_integrations=False,
        integrations=[
            CeleryIntegration(propagate_traces=False), ExcepthookIntegration(),
        ],
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_headers": {"request": {"mode": "off"}},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "queues": False,
            "stack_frame_variables": False,
            "frame_context_lines": 0,
        },
        max_breadcrumbs=0,
        traces_sample_rate=0.0,
        server_name="code-mortis",
        before_send=scrub_event,
    )
    sentry_sdk.set_tag("service", service)
