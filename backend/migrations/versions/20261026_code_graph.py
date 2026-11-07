"""Add revision-scoped graph storage without activating indexing."""

import sqlalchemy as sa
from alembic import op

revision = "20261026_code_graph"
down_revision = "20261025_api_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "code_graph_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("extractor_version", sa.String(128), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("repository_id", "commit_sha", "extractor_version", "policy_digest",
                            name="uq_code_graph_snapshot_identity"),
        sa.CheckConstraint("commit_sha ~ '^[a-f0-9]{40}$'", name="ck_code_graph_snapshot_commit"),
        sa.CheckConstraint("policy_digest ~ '^[a-f0-9]{64}$'", name="ck_code_graph_snapshot_policy"),
        sa.CheckConstraint("length(extractor_version) > 0", name="ck_code_graph_snapshot_extractor"),
        sa.CheckConstraint("status IN ('queued', 'running', 'ready', 'partial', 'failed', 'cancelled')",
                           name="ck_code_graph_snapshot_status"),
    )
    op.create_table(
        "code_graph_files",
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("code_graph_snapshots.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column("blob_sha", sa.String(40), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(32)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("line_count", sa.Integer()),
        sa.Column("unresolved_calls", sa.Integer()),
        sa.Column("reason", sa.String(50)),
        sa.CheckConstraint("octet_length(path) BETWEEN 1 AND 2000", name="ck_code_graph_file_path"),
        sa.CheckConstraint("blob_sha ~ '^[a-f0-9]{40}$'", name="ck_code_graph_file_blob"),
        sa.CheckConstraint("size_bytes >= 0 AND (line_count IS NULL OR line_count >= 0) "
                           "AND (unresolved_calls IS NULL OR unresolved_calls >= 0)", name="ck_code_graph_file_counts"),
        sa.CheckConstraint("status IN ('parsed', 'partial', 'unsupported', 'excluded', 'failed')",
                           name="ck_code_graph_file_status"),
        sa.CheckConstraint("status NOT IN ('parsed', 'partial') OR (language IS NOT NULL AND line_count IS NOT NULL)",
                           name="ck_code_graph_file_parsed"),
    )
    op.create_table(
        "code_graph_symbols",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("start_byte", sa.Integer(), nullable=False),
        sa.Column("end_byte", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id", "file_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                                ondelete="CASCADE", name="fk_code_graph_symbol_file"),
        sa.UniqueConstraint("snapshot_id", "file_path", "start_byte", "end_byte", "kind", name="uq_code_graph_symbol_location"),
        sa.CheckConstraint("key ~ '^[a-f0-9]{64}$'", name="ck_code_graph_symbol_key"),
        sa.CheckConstraint("start_byte >= 0 AND end_byte > start_byte AND start_line >= 1 AND end_line >= start_line",
                           name="ck_code_graph_symbol_range"),
    )
    op.create_index("ix_code_graph_symbols_name", "code_graph_symbols", ["snapshot_id", "name"])
    op.create_table(
        "code_graph_imports",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("file_path", sa.Text(), primary_key=True),
        sa.Column("start_byte", sa.Integer(), primary_key=True),
        sa.Column("end_byte", sa.Integer(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("specifier", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text()),
        sa.Column("provenance", sa.String(20), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id", "file_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                                ondelete="CASCADE", name="fk_code_graph_import_source"),
        sa.ForeignKeyConstraint(["snapshot_id", "target_path"], ["code_graph_files.snapshot_id", "code_graph_files.path"],
                                ondelete="CASCADE", name="fk_code_graph_import_target"),
        sa.CheckConstraint("start_byte >= 0 AND end_byte > start_byte AND start_line >= 1 AND end_line >= start_line",
                           name="ck_code_graph_import_range"),
        sa.CheckConstraint("(target_path IS NULL AND provenance = 'unresolved') OR "
                           "(target_path IS NOT NULL AND provenance = 'relative_path')", name="ck_code_graph_import_resolution"),
    )
    op.create_index("ix_code_graph_imports_target", "code_graph_imports", ["snapshot_id", "target_path"])


def downgrade():
    for table in ("code_graph_imports", "code_graph_symbols", "code_graph_files", "code_graph_snapshots"):
        op.drop_table(table)
