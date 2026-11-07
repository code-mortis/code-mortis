"""Browser-only, bounded PR comparison contracts, independent of graph indexing."""

from typing import Any, Literal

from pydantic import Field, field_validator

from code_mortis.schemas.code_graph import MAX_INDEXED_FILE_BYTES as MAX_FILE_BYTES
from code_mortis.schemas.code_graph import Digest, GitSha, RepositoryPath
from code_mortis.schemas.code_graph_explorer import GraphRead
from code_mortis.schemas.common import StrictModel


class CodeIdentity(StrictModel):
    base_sha: GitSha
    head_sha: GitSha
    merge_base_sha: GitSha


class CodeFileRequest(CodeIdentity):
    path: RepositoryPath


class CodeFileInfo(StrictModel):
    path: RepositoryPath
    previous_path: RepositoryPath | None = None
    change: Literal["added", "modified", "removed", "renamed", "changed", "copied", "unchanged"]
    additions: int | None = Field(default=None, ge=0, le=1_000_000_000)
    deletions: int | None = Field(default=None, ge=0, le=1_000_000_000)


class CodeGroup(StrictModel):
    id: str
    title: str
    description: str
    paths: list[RepositoryPath]


class CodeOverview(CodeIdentity):
    policy_digest: Digest
    files: list[CodeFileInfo]
    total: int
    page: int
    comparison_truncated: bool
    groups: list[CodeGroup]
    summary_run_id: str | None = None
    group: str | None = None


class CodeLine(StrictModel):
    kind: Literal["hunk", "context", "addition", "deletion", "notice"]
    text: str
    old_line: int | None = None
    new_line: int | None = None


class CodeFindingVerification(StrictModel):
    id: str
    verdict: Literal["fixed", "still_present", "uncertain"]
    source_sha: GitSha
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class CodeFinding(StrictModel):
    occurrence_id: str
    finding_id: str
    run_id: str
    source_sha: GitSha | None = None
    current_status: str
    snapshot: dict[str, Any]
    side: Literal["old", "new"] | None = None
    line: int | None = None
    placement: Literal["inline", "outside_diff", "historical", "unknown"]
    verification: CodeFindingVerification | None = None


class CodeFile(CodeIdentity):
    file: CodeFileInfo
    patch_state: Literal["available", "truncated", "unavailable", "too_large", "withheld"]
    lines: list[CodeLine]
    findings: list[CodeFinding]
    findings_truncated: bool
    github_url: str


class CodeNavigation(StrictModel):
    expected: CodeIdentity | None = None
    path: RepositoryPath | None = None
    view: Literal["diff", "dependencies", "definitions", "source"] = "diff"
    revision: Literal["head", "base"] = "head"
    page: int = Field(default=1, ge=1, le=100, strict=True)
    query: str = Field(default="", max_length=256)
    group: str | None = Field(default=None, max_length=64)
    unreviewed: bool = False
    with_findings: bool = False
    reviewed: list[bool] = Field(default_factory=list, max_length=2)
    findings: list[Literal["any", "none", "open", "resolved", "dismissed"]] = Field(default_factory=list, max_length=5)
    offset: int = Field(default=0, ge=0, le=100_000, strict=True)
    start_byte: int = Field(default=0, ge=0, le=MAX_FILE_BYTES, strict=True)
    end_byte: int = Field(default=MAX_FILE_BYTES, ge=0, le=MAX_FILE_BYTES, strict=True)

    @field_validator("reviewed", "findings", mode="before")
    @classmethod
    def accept_single_filter(cls, value):
        if value is None:
            return []
        return [value] if isinstance(value, (str, bool)) else value


class CodeWorkspaceGroup(StrictModel):
    id: str
    title: str
    description: str
    file_indexes: list[int]


class CodeFileState(StrictModel):
    reviewed: bool
    open_count: int = Field(ge=0, le=1000)
    resolved_count: int = Field(ge=0, le=1000)
    dismissed_count: int = Field(ge=0, le=1000)
    complete: bool


class CodeWorkspace(CodeIdentity):
    policy_digest: Digest
    files: list[CodeFileInfo]
    file_states: list[CodeFileState]
    groups: list[CodeWorkspaceGroup]
    summary_run_id: str | None
    comparison_truncated: bool
    metadata_truncated: bool
    path: RepositoryPath | None
    view: Literal["diff", "dependencies", "definitions", "source"]
    revision: Literal["head", "base"]
    graph_enabled: bool
    graph_paused: bool
    diff: CodeFile | None = None
    graph: GraphRead | None = None
