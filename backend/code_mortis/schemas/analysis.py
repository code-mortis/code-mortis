"""Persisted execution inputs; no provider execution is enabled by this contract."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from code_mortis.schemas.common import StrictModel

Stage = Literal["analysis", "assessment", "verification", "summary", "security"]


class TaskSpec(StrictModel):
    key: str = Field(min_length=1, max_length=200)
    stage: Stage
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: list[dict[str, Any]]
    dependencies: list[str] = Field(default_factory=list)


class PlanSpec(StrictModel):
    repository_id: int
    run_id: str | None = None
    scan_id: str | None = None
    initiator_id: int | None
    authorization: dict[str, Any]
    trigger: str = Field(min_length=1)
    connection: dict[str, Any]
    revisions: dict[str, str]
    versions: dict[str, str]
    settings: dict[str, Any]
    file_policy: dict[str, Any]
    selected: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    graph: dict[str, Any]
    budgets: dict[Stage, Annotated[int, Field(strict=True, ge=0)] | None]
    deadlines: dict[Stage, datetime] = Field(default_factory=dict)
    tasks: list[TaskSpec]

    @field_validator("connection")
    @classmethod
    def shared_connection(cls, value):
        if value.get("kind") != "api" or not value.get("id"):
            raise ValueError("Plans require an explicitly selected shared API connection")
        if set(value) & {"secret", "api_key", "extra_headers", "credentials"}:
            raise ValueError("Credentials cannot be stored in analysis plans")
        return value

    @model_validator(mode="after")
    def execution_inputs(self):
        if self.run_id and self.scan_id:
            raise ValueError("A plan cannot belong to both a review and a security scan")
        if "head" not in self.revisions or any(len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)
                                     for sha in self.revisions.values()):
            raise ValueError("Plans require exact Git revisions")
        if not {"engine", "planner", "prompt", "rules", "schema", "file_policy"} <= set(self.versions):
            raise ValueError("Plans require all execution contract versions")
        if any(value is not None and (type(value) is not int or value < 0) for value in self.budgets.values()):
            raise ValueError("Stage budgets must be nonnegative whole tokens or unlimited")
        if any(value.tzinfo is None for value in self.deadlines.values()):
            raise ValueError("Stage deadlines must include a time zone")
        keys = {task.key for task in self.tasks}
        if len(keys) != len(self.tasks) or any(task.stage not in self.budgets for task in self.tasks):
            raise ValueError("Tasks require unique keys and an explicit stage budget")
        dependencies = {task.key: set(task.dependencies) for task in self.tasks}
        if any(not values <= keys or key in values for key, values in dependencies.items()):
            raise ValueError("Task dependencies must name other tasks in this plan")
        resolved = set()
        while dependencies:
            ready = {key for key, values in dependencies.items() if values <= resolved}
            if not ready:
                raise ValueError("Task dependencies contain a cycle")
            resolved.update(ready)
            dependencies = {key: values for key, values in dependencies.items() if key not in ready}
        return self
