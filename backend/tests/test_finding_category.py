"""Public category bounds match persistence while existing finding history survives upgrades."""

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect, select

from code_mortis.core.db import engine, transaction
from code_mortis.models import Finding, Occurrence, ReviewRun
from code_mortis.reviews.service import save_analysis
from code_mortis.schemas.common import ReviewComment


def test_category_upgrade_preserves_history_and_saves_public_maximum(seed):
    config = Config("alembic.ini")
    with transaction() as db:
        db.add(ReviewRun(id="category-run", pr_id="pr", actor_id=7, trigger="manual", dedup_key="category-run",
                         base_sha="b" * 40, head_sha="a" * 40, status="running",
                         config={"analysis_engine": "mortis"}, progress={"execution": "category-job:1"}))
        db.flush()
        db.add(Finding(id="historical-finding", pr_id="pr", fingerprint="a" * 64, path="existing.py",
                       content="Historical finding", category="h" * 30, start_line=1, end_line=1,
                       last_seen_run_id="category-run"))
    try:
        command.downgrade(config, "20261107_inference_admission")
    finally:
        command.upgrade(config, "head")
    with transaction() as db:
        historical = db.get(Finding, "historical-finding")
        assert (historical.fingerprint, historical.path, historical.content, historical.category, historical.status) == (
            "a" * 64, "existing.py", "Historical finding", "h" * 30, "open")
    category_column = next(column for column in inspect(engine()).get_columns("findings") if column["name"] == "category")
    assert category_column["type"].length == Finding.__table__.c.category.type.length == 50
    comment = ReviewComment(path="ports.py", content="Missing port validation", start_line=1, end_line=1,
                            category="c" * 50, existing_code="return int(raw)").model_dump()
    with pytest.raises(ValidationError):
        ReviewComment.model_validate({**comment, "category": "c" * 51})
    save_analysis("category-run", "category-job:1", "completed", {}, [comment])
    with transaction() as db:
        finding = db.scalar(select(Finding).where(Finding.path == "ports.py"))
        assert finding.category == "c" * 50
        occurrence = db.scalar(select(Occurrence).where(Occurrence.finding_id == finding.id))
        assert occurrence.snapshot["category"] == finding.category
        assert db.get(Finding, "historical-finding").category == "h" * 30
