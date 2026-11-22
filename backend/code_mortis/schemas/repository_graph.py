from typing import Annotated, Literal

from pydantic import Field

from code_mortis.schemas.code_graph import GitSha, RepositoryPath, StrictModel

GraphDirection = Literal["both", "incoming", "outgoing"]
GraphDepth = Annotated[int, Field(ge=1, le=2)]


class RepositoryGraphFilters(StrictModel):
    commit_sha: GitSha | None = None
    folder: RepositoryPath | None = None
    language: str | None = Field(default=None, min_length=1, max_length=32)
    focus: RepositoryPath | None = None
    direction: GraphDirection = "both"
    depth: GraphDepth = 1
    hide_unlinked: bool = False


class RepositoryGraphNode(StrictModel):
    id: str
    kind: Literal["file", "folder"]
    path: RepositoryPath
    language: str | None
    incoming: int
    outgoing: int
    file_count: int


class RepositoryGraphEdge(StrictModel):
    source: str
    target: str


class RepositoryGraphView(StrictModel):
    status: Literal["disabled", "unavailable", "ready", "partial"]
    commit_sha: GitSha | None
    observed_sha: GitSha | None
    snapshot_id: str | None
    branch: str | None
    mode: Literal["files", "folders"]
    nodes: list[RepositoryGraphNode]
    edges: list[RepositoryGraphEdge]
    truncated: bool
    total_files: int
    total_links: int


class RepositoryGraphFiles(StrictModel):
    files: list[RepositoryGraphNode]
    truncated: bool
