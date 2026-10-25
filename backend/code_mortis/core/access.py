"""Transport-neutral effective repository selection.

A request is restricted to the intersection of every repository restriction
attached to its credential: personal API token selection and MCP grant
selection. Neither transport can widen the other. Returns None when the
credential carries no restriction.
"""


def effective_repository_ids(user) -> frozenset[int] | None:
    selected: set[int] | None = None
    for attribute in ("api_token_repository_ids", "mcp_repository_ids"):
        restricted = getattr(user, attribute, None)
        if restricted is not None:
            restricted = frozenset(restricted)
            selected = restricted if selected is None else selected & restricted
    return frozenset(selected) if selected is not None else None


def client_ip(request) -> str:
    host = request.client.host if request.client else "unknown"
    return host or "unknown"
