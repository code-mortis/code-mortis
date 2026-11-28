"""Application-owned workflow guides on the MCP Skills and ordinary tool surfaces."""

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from typing import Any

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.extension import Extension, MethodBinding, ResourceBinding, ToolBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp.types import PaginatedRequestParams, RequestParams, ToolAnnotations

MODERN = frozenset({"2026-07-28"})


@dataclass(frozen=True)
class Guide:
    name: str
    scope: str
    description: str
    text: str

    @property
    def uri(self):
        return f"skill://{self.name}/SKILL.md"

    def entry(self):
        data = self.text.encode()
        return {"uri": self.uri, "frontmatter": {"name": self.name, "description": self.description},
                "resources": [{"uri": self.uri, "size": len(data), "digest": "sha256:" + sha256(data).hexdigest()}]}


def load_guide(name, scope):
    text = files(__package__).joinpath("guides", name, "SKILL.md").read_text(encoding="utf-8")
    description = next(line.removeprefix("description: ") for line in text.splitlines() if line.startswith("description: "))
    return Guide(name, scope, description, text)


GUIDES = tuple(load_guide(name, scope) for name, scope in (
    ("explain-pr-review", "reviews:read"), ("prioritize-security-findings", "security:read"),
    ("trace-change-impact", "code:read")))


def visible_guides():
    token = get_access_token()
    return [guide for guide in GUIDES if token and guide.scope in token.scopes]


def get_workflow_guide(name: str | None = None) -> dict[str, Any]:
    """List permitted Code Mortis workflow guides, or read one by name."""
    guides = visible_guides()
    if name is None:
        return {"guides": [{"name": guide.name, "description": guide.description} for guide in guides]}
    for guide in guides:
        if guide.name == name:
            return {"name": guide.name, "uri": guide.uri, "text": guide.text}
    raise HTTPException(404, "Workflow guide unavailable.")


class SkillParams(RequestParams):
    uri: str


class Skills(Extension):
    identifier = "io.modelcontextprotocol/skills"

    def methods(self):
        return [MethodBinding("skills/list", PaginatedRequestParams, self.list, MODERN),
                MethodBinding("skills/get", SkillParams, self.get, MODERN)]

    def resources(self):
        return [ResourceBinding(TextResource(uri=g.uri, name=g.name, description=g.description,
                                             mime_type="text/markdown", text=g.text)) for g in GUIDES]

    def tools(self):
        return [ToolBinding(get_workflow_guide, kwargs={"annotations": ToolAnnotations(readOnlyHint=True)})]

    async def list(self, ctx, params):
        if params.cursor:
            raise MCPError(code=-32602, message="Invalid skills cursor.")
        return {"skills": [g.entry() for g in visible_guides()], "ttlMs": 0, "cacheScope": "private"}

    async def get(self, ctx, params):
        for guide in visible_guides():
            if guide.uri == params.uri:
                return {"skill": guide.entry(), "ttlMs": 0, "cacheScope": "private"}
        raise MCPError(code=-32602, message="Workflow guide unavailable.")
