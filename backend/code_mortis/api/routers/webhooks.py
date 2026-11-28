import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from code_mortis.core.config import settings
from code_mortis.core.db import transaction
from code_mortis.core.security import unseal, valid_signature
from code_mortis.models import (
    AppConfig,
    WebhookDelivery,
)
from code_mortis.reviews import verification
from code_mortis.reviews.service import enqueue

router = APIRouter()


@router.post("/webhooks/github", status_code=202)
async def webhook(request: Request) -> dict:
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > settings().max_webhook_bytes:
                raise HTTPException(413, "Webhook payload is too large.")
            body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, "Webhook delivery disconnected before the body was received.") from None
    return await run_in_threadpool(
        accept_webhook, bytes(body), request.headers.get("x-hub-signature-256", ""),
        request.headers.get("x-github-delivery", ""), request.headers.get("x-github-event", ""),
    )


def accept_webhook(body: bytes, signature: str, delivery: str, event: str) -> dict:
    with transaction() as db:
        config = db.get(AppConfig, 1)
        if config is None:
            raise HTTPException(503, "Instance setup is incomplete.")
        if not valid_signature(body, signature, unseal(config.webhook_secret)):
            raise HTTPException(401, "Invalid webhook signature.")
        if not delivery or len(delivery) > 100 or not event or len(event) > 100:
            raise HTTPException(400, "Missing webhook delivery headers.")
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(400, "Invalid webhook payload.") from exc
        try:
            with db.begin_nested():
                db.add(WebhookDelivery(id=delivery, event=event))
                db.flush()
                verification.thread_event(db, event, data, delivery)
                if event != "ping":
                    enqueue(db, "webhook", {"event": event, "body": data, "delivery": delivery})
        except IntegrityError:
            return {"accepted": True, "duplicate": True}
        return {"accepted": True, "duplicate": False}
