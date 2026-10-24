import json

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from code_mortis.api.dependencies import DB
from code_mortis.core.config import settings
from code_mortis.core.security import unseal, valid_signature
from code_mortis.models import (
    AppConfig,
    WebhookDelivery,
)
from code_mortis.reviews import verification
from code_mortis.reviews.service import enqueue

router = APIRouter()


@router.post("/webhooks/github", status_code=202)
async def webhook(request: Request, db: DB) -> dict:
    config = db.get(AppConfig, 1)
    if config is None:
        raise HTTPException(503, "Instance setup is incomplete.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings().max_webhook_bytes:
            raise HTTPException(413, "Webhook payload is too large.")
    if not valid_signature(
        bytes(body), request.headers.get("x-hub-signature-256", ""), unseal(config.webhook_secret)
    ):
        raise HTTPException(401, "Invalid webhook signature.")
    delivery = request.headers.get("x-github-delivery", "")
    event = request.headers.get("x-github-event", "")
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
