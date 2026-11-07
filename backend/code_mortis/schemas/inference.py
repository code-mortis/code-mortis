"""Internal, provider-neutral inference contracts; never contain credentials."""

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from code_mortis.schemas.common import StrictModel


class Tool(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = ""
    parameters: dict[str, Any]


class ToolCall(StrictModel):
    id: str = Field(min_length=1, max_length=500)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]


class Message(StrictModel):
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    provider_data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_role(self):
        if (self.role == "tool") != bool(self.tool_call_id):
            raise ValueError("Only tool messages require a tool call ID")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages may request tools")
        if self.provider_data and self.role != "assistant":
            raise ValueError("Only assistant messages may retain provider reasoning state")
        return self


class Request(StrictModel):
    messages: list[Message] = Field(min_length=1)
    instructions: str = ""
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: str = "auto"
    output_schema: dict[str, Any] | None = None
    max_output_tokens: int = Field(strict=True, ge=1, le=1_000_000)
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    session_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=200,
                            pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def tool_history(self):
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Tool names must be unique")
        if self.tool_choice not in {"auto", "required", *(tool.name for tool in self.tools)}:
            raise ValueError("Tool choice must be auto, required, or a declared tool name")
        if self.tool_choice != "auto" and not self.tools:
            raise ValueError("Tool choice requires declared tools")
        pending, seen = set(), set()
        for message in self.messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("Tool results must answer a pending tool call exactly once")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError("Answer all pending tool calls before continuing")
                for call in message.tool_calls:
                    if call.id in seen:
                        raise ValueError("Tool call IDs must be unique")
                    pending.add(call.id)
                    seen.add(call.id)
        if pending:
            raise ValueError("Answer all pending tool calls before sending a request")
        return self


class Usage(StrictModel):
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)
    total_tokens: int | None = Field(default=None, strict=True, ge=0)
    cache_read_tokens: int | None = Field(default=None, strict=True, ge=0)
    cache_write_tokens: int | None = Field(default=None, strict=True, ge=0)
    reasoning_tokens: int | None = Field(default=None, strict=True, ge=0)


class Response(StrictModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    request_id: str | None = None
    finish_reason: Literal["complete", "tool_calls"] = "complete"
    output: Any | None = None
    provider_data: dict[str, Any] = Field(default_factory=dict)
