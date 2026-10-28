from typing import Literal

from pydantic import BaseModel, Field

from code_mortis.schemas.code_graph import MAX_FILE_BYTES, GitSha, RepositoryPath, StrictModel


class ExplorerRevision(StrictModel):
    base_sha: GitSha
    head_sha: GitSha
    revision: Literal["head", "base"] = "head"


class ExplorerQuery(ExplorerRevision):
    operation: Literal["dependencies", "symbols", "source"]
    path: RepositoryPath
    offset: int = Field(default=0, ge=0, le=100_000, strict=True)
    start_byte: int = Field(default=0, ge=0, le=MAX_FILE_BYTES, strict=True)
    end_byte: int = Field(default=MAX_FILE_BYTES, ge=0, le=MAX_FILE_BYTES, strict=True)


class GraphLocation(BaseModel):
    path: str
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int


class GraphDefinition(GraphLocation):
    kind: Literal["symbol"]
    key: str
    name: str
    symbol_kind: str


class GraphRelation(GraphLocation):
    kind: Literal["dependency"]
    target_path: str
    relationship: str
    provenance: str


class GraphSource(GraphLocation):
    kind: Literal["source"]
    blob_sha: str
    text: str


class GraphCursor(BaseModel):
    offset: int
    start_byte: int


class GraphRead(BaseModel):
    repository_id: int
    commit_sha: str
    snapshot_id: str | None
    status: str
    extractor_version: str
    policy_digest: str | None
    capabilities: dict[str, str]
    definition_languages: list[str]
    gaps: list[str]
    truncated: bool
    source_state: str
    items: list[GraphDefinition | GraphRelation | GraphSource]
    next_cursor: GraphCursor | None


class ChangedGraphFile(BaseModel):
    path: str
    change: Literal["added", "modified", "removed"]


class GraphExplorer(BaseModel):
    enabled: bool
    paused: bool = False
    base_sha: str
    head_sha: str
    merge_base_sha: str | None = None
    base: GraphRead | None = None
    head: GraphRead | None = None
    files: list[ChangedGraphFile] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    has_more: bool = False
    comparison_truncated: bool = False
