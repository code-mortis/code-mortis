"""Short PostgreSQL admission transactions; no provider calls or network-held locks."""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from code_mortis.core.db import now, transaction, uid
from code_mortis.models import Connection, InferenceAdmission

CONNECTION_CEILING = 8


class AdmissionStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectionLease:
    admission_id: str
    connection_id: str

    def heartbeat(self, *, lease_seconds=60):
        return renew_connection(self.admission_id, lease_seconds=lease_seconds)


def validate_limits(slots, run_limit, lease_seconds):
    if any(type(value) is not int or not 1 <= value <= CONNECTION_CEILING for value in (slots, run_limit)):
        raise ValueError("Inference capacity and per-run limits must be whole numbers between 1 and 8")
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("Inference leases must last between 1 and 3600 seconds")


def lock_connection(db, connection_id, *, require_api=True):
    connection = db.scalar(select(Connection).where(Connection.id == connection_id).with_for_update())
    if require_api and (not connection or connection.kind != "api"):
        raise ValueError("Inference requires an existing shared API connection")
    return connection


def admit(db, *, identity, connection_id, run_key, slots=1, run_limit=8, lease_until,
          task_id=None, job_id=None, job_attempt=None):
    """Queue and admit under the same transaction as the caller's stage reservation.

    Caller locks its Job/task/budget first, then this connection. Never take owner
    locks after this lock; that would invert the task-claim lock order.
    """
    validate_limits(slots, run_limit, 60)
    lock_connection(db, connection_id)
    timestamp = now()
    if lease_until <= timestamp:
        return None
    rows = list(db.scalars(select(InferenceAdmission).where(
        InferenceAdmission.connection_id == connection_id,
        InferenceAdmission.status.in_(("active", "queued"))).order_by(
            InferenceAdmission.created_at, InferenceAdmission.id)))
    ticket = next((row for row in rows if row.id == identity), None)
    if not ticket:
        ticket = db.get(InferenceAdmission, identity)
    if ticket and (ticket.connection_id, ticket.run_key, ticket.slots, ticket.run_limit,
                   ticket.task_id, ticket.job_id, ticket.job_attempt) != (
            connection_id, run_key, slots, run_limit, task_id, job_id, job_attempt):
        raise ValueError("An inference ticket cannot change its execution identity")
    if ticket and ticket.status in {"active", "released"}:
        return None
    for row in rows:
        if row.status == "queued" and row.lease_until <= timestamp:
            row.status, row.finished_at = "cancelled", timestamp
    if ticket and ticket.status == "cancelled":
        # Rejoining goes to the back; an abandoned waiter cannot keep its old turn.
        ticket.status, ticket.created_at, ticket.finished_at = "queued", timestamp, None
        if ticket not in rows:
            rows.append(ticket)
    if not ticket:
        ticket = InferenceAdmission(id=identity, connection_id=connection_id, run_key=run_key,
                                    slots=slots, run_limit=run_limit, task_id=task_id, job_id=job_id,
                                    job_attempt=job_attempt, lease_until=lease_until, created_at=timestamp,
                                    status="queued")
        db.add(ticket)
        rows.append(ticket)
    ticket.lease_until = lease_until
    active, limits, heads = {}, {}, {}
    last_grant = dict(db.execute(select(InferenceAdmission.run_key, func.max(InferenceAdmission.admitted_at)).where(
        InferenceAdmission.connection_id == connection_id,
        InferenceAdmission.run_key.in_({row.run_key for row in rows}),
        InferenceAdmission.admitted_at.is_not(None)).group_by(InferenceAdmission.run_key)).all())
    for row in rows:
        if row.status == "active":
            # Expiry fences the caller but cannot prove its network request stopped.
            active[row.run_key] = active.get(row.run_key, 0) + row.slots
            limits[row.run_key] = min(limits.get(row.run_key, row.run_limit), row.run_limit)
        if row.status == "queued":
            current = heads.get(row.run_key)
            if current is None or (row.created_at, row.id) < (current.created_at, current.id):
                heads[row.run_key] = row
    candidates = [row for row in heads.values() if active.get(row.run_key, 0) + row.slots <= min(
        row.run_limit, limits.get(row.run_key, row.run_limit))]
    candidates.sort(key=lambda row: (max(row.created_at, last_grant.get(row.run_key, row.created_at)), row.id))
    # A weighted legacy head drains the connection, rather than starving behind
    # a stream of single-slot tasks. Saturated runs do not block other runs.
    if (not candidates or candidates[0].id != identity
            or sum(active.values()) + slots > CONNECTION_CEILING):
        return None
    ticket.status, ticket.admitted_at = "active", timestamp
    db.flush()
    return ConnectionLease(ticket.id, connection_id)


def release_in_transaction(db, admission_id, *, queued_only=False):
    ticket = db.get(InferenceAdmission, admission_id)
    if not ticket:
        return False
    if not lock_connection(db, ticket.connection_id, require_api=False):
        return False
    db.refresh(ticket)
    if queued_only and ticket.status != "queued":
        return False
    if ticket.status not in {"active", "queued"}:
        return False
    ticket.status, ticket.finished_at = "released" if ticket.status == "active" else "cancelled", now()
    return True


def release_connection(admission_id: str) -> bool:
    """Release only after transport termination is established, including stale owners.

    This is also the explicit worker-loss recovery primitive: the operator must
    establish process/request termination before invoking it. Token charges remain.
    """
    with transaction() as db:
        return release_in_transaction(db, admission_id)


def renew_in_transaction(db, admission_id, lease_until):
    ticket = db.get(InferenceAdmission, admission_id)
    if not ticket:
        return False
    connection = lock_connection(db, ticket.connection_id, require_api=False)
    if not connection or connection.kind != "api":
        return False
    db.refresh(ticket)
    timestamp = now()
    if ticket.status != "active" or ticket.lease_until <= timestamp or lease_until <= timestamp:
        return False
    ticket.lease_until = lease_until
    return True


def renew_connection(admission_id: str, *, lease_seconds=60) -> bool:
    validate_limits(1, 8, lease_seconds)
    with transaction() as db:
        return renew_in_transaction(db, admission_id, now() + timedelta(seconds=lease_seconds))


def cancel_waiter(identity):
    with transaction() as db:
        ticket = db.get(InferenceAdmission, identity)
        if ticket and ticket.status == "queued":
            release_in_transaction(db, identity, queued_only=True)


def cancel_task_waiter(task_id: str, job_id: str, job_attempt: int) -> int:
    """Abandon only queued turns, never reclaim a potentially running request."""
    with transaction() as db:
        tickets = list(db.scalars(select(InferenceAdmission).where(
            InferenceAdmission.task_id == task_id, InferenceAdmission.job_id == job_id,
            InferenceAdmission.job_attempt == job_attempt, InferenceAdmission.status == "queued")))
        return sum(release_in_transaction(db, ticket.id, queued_only=True) for ticket in tickets)


@contextmanager
def reserve_connection(connection_id: str, run_key: str, *, stopped=lambda: False,
                       wait_seconds=60, lease_seconds=60, deadline_at: datetime | None = None):
    """Reserve all eight slots while a connection probe is in flight.

    The context must enclose every request. A deadline or lost heartbeat does
    not automatically reclaim capacity from a potentially live request.
    """
    validate_limits(CONNECTION_CEILING, CONNECTION_CEILING, lease_seconds)
    if not isinstance(run_key, str) or not 1 <= len(run_key) <= 100:
        raise ValueError("A stable parent run identity is required")
    if not isinstance(wait_seconds, (int, float)) or not 0 <= wait_seconds <= 3600:
        raise ValueError("Admission waits must be bounded between 0 and 3600 seconds")
    if deadline_at is not None and deadline_at.tzinfo is None:
        raise ValueError("Inference deadlines must include a time zone")
    identity, until = uid(), time.monotonic() + wait_seconds
    try:
        while True:
            timestamp = now()
            if stopped() or deadline_at and deadline_at <= timestamp:
                raise AdmissionStopped("Inference admission stopped")
            expires = min(timestamp + timedelta(seconds=lease_seconds), deadline_at) if deadline_at else (
                timestamp + timedelta(seconds=lease_seconds))
            with transaction() as db:
                lease = admit(db, identity=identity, connection_id=connection_id, run_key=run_key,
                              slots=CONNECTION_CEILING, run_limit=CONNECTION_CEILING, lease_until=expires)
            if lease:
                if stopped():
                    raise AdmissionStopped("Inference admission stopped")
                yield lease
                return
            if time.monotonic() >= until:
                raise TimeoutError("Inference connection admission timed out")
            time.sleep(min(0.1, max(0, until - time.monotonic())))
    finally:
        # The caller promises every request has stopped before exit.
        release_connection(identity)
