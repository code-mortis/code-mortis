import hashlib
import hmac
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from .config import settings


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
        raise ValueError("Use a complete HTTP(S) provider URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider URLs cannot contain credentials, query parameters, or fragments")
    config = settings()
    trusted = {h.strip().lower() for h in config.trusted_endpoint_hosts.split(",") if h.strip()}
    if parsed.hostname.lower() in trusted:
        return url.rstrip("/")
    if parsed.scheme != "https" and not config.allow_private_endpoints:
        raise ValueError("HTTP endpoints require CM_ALLOW_PRIVATE_ENDPOINTS=true")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Provider hostname could not be resolved") from exc
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if address.is_link_local or address.is_unspecified or address.is_multicast:
            raise ValueError("Metadata, link-local, and multicast endpoints are not allowed")
        if not address.is_global and not config.allow_private_endpoints:
            raise ValueError("Private endpoints require CM_ALLOW_PRIVATE_ENDPOINTS=true")
    return url.rstrip("/")


def clean_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[redacted]", value)
    value = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b", "[redacted]", value
    )
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)


def safe_repo_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("Invalid GitHub repository name")
    return value
