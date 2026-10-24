from typing import Literal

from sqlalchemy import case, func

FindingOrdering = Literal["created_at", "-created_at", "updated_at", "-updated_at", "severity", "-severity", "path", "-path"]
CandidateOrdering = Literal["position", "-position", "created_at", "-created_at", "finished_at", "-finished_at", "severity", "-severity", "path", "-path"]
SnapshotOrdering = Literal["position", "-position", "severity", "-severity", "path", "-path"]
RunFindingOrdering = Literal["severity", "-severity", "path", "-path"]
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def list_order(ordering, columns, *ties):
    field = ordering.removeprefix("-")
    column = columns[field]
    if field == "severity":
        column = case(SEVERITY_RANK, value=column, else_=None)
    elif field == "path":
        column = func.lower(column)
    order = column.desc() if ordering.startswith("-") else column.asc()
    return order.nulls_last(), *ties


def sort_snapshots(rows, ordering, snapshot=lambda row: row):
    field = ordering.removeprefix("-")
    descending = ordering.startswith("-")
    if field == "position":
        return list(reversed(rows)) if descending else rows
    key = (lambda row: SEVERITY_RANK.get(snapshot(row).get("severity"), 0)) if field == "severity" else (
        lambda row: str(snapshot(row).get("path") or "").casefold()
    )
    result = sorted(rows, key=key, reverse=descending)
    if field == "severity":
        result.sort(key=lambda row: snapshot(row).get("severity") not in SEVERITY_RANK)
    return result
