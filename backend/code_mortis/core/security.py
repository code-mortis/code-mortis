import hashlib
import hmac
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from code_mortis.core.config import settings


def seal(value: str) -> str:
    return Fernet(settings().encryption_key.encode()).encrypt(value.encode()).decode()


def unseal(value: str) -> str:
    return Fernet(settings().encryption_key.encode()).decrypt(value.encode()).decode() if value else ""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def valid_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def safe_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Enter a complete provider URL starting with https:// or http://.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider URLs cannot contain credentials, query parameters, or fragments")
    config = settings()
    trusted = {h.strip().lower() for h in config.trusted_endpoint_hosts.split(",") if h.strip()}
    if parsed.hostname.lower() in trusted:
        return url.rstrip("/")
    if parsed.scheme != "https" and not config.allow_private_endpoints:
        raise ValueError("This server does not allow unencrypted HTTP provider URLs. Use HTTPS or ask the administrator to review CM_ALLOW_PRIVATE_ENDPOINTS for a trusted local provider.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("The provider address could not be found. Check the URL and ask the administrator to check server DNS if it is correct.") from exc
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError("This provider URL points to a protected system or local-network address. Choose a different provider URL.")
        if not address.is_global and not config.allow_private_endpoints:
            raise ValueError("This server does not allow private-network provider addresses. Use an allowed provider or ask the administrator to review CM_ALLOW_PRIVATE_ENDPOINTS for your trusted provider.")
    return url.rstrip("/")


def clean_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", value)
    value = re.sub(r"cm_pat_[A-Za-z0-9_-]{43}", "[redacted]", value)
    value = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b", "[redacted]", value
    )
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)


def safe_repo_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Enter a GitHub repository in owner/repository format.")
    return value


def redact_saved_credentials(db, text, repository_ids=None):
    return saved_credential_redactor(db, repository_ids)(text)


def saved_credential_redactor(db, repository_ids=None):
    from sqlalchemy import select

    from code_mortis.models import Connection, SecurityFinding
    from code_mortis.security_scans import engine as security_engine

    credentials = []
    for connection in db.scalars(select(Connection)):
        if connection.secret:
            saved = json.loads(unseal(connection.secret))
            credentials.extend([saved.get("api_key", ""), *saved.get("extra_headers", {}).values()])
    query = select(SecurityFinding.evidence).where(SecurityFinding.kind == "secret")
    if repository_ids is not None:
        query = query.where(SecurityFinding.repository_id.in_(repository_ids))
    evidence = list(db.scalars(query))
    return lambda text: clean_text(security_engine.redact_secrets(text, credentials, evidence))
