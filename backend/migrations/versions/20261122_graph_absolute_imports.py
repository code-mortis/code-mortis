"""Allow absolute repository-local import facts."""

from alembic import op

revision = "20261122_graph_absolute_imports"
down_revision = "20261122_scoped_review_rules"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_code_graph_import_resolution", "code_graph_imports", type_="check")
    op.create_check_constraint(
        "ck_code_graph_import_resolution", "code_graph_imports",
        "(target_path IS NULL AND provenance = 'unresolved') OR "
        "(target_path IS NOT NULL AND provenance IN ('relative_path', 'absolute_path'))",
    )


def downgrade():
    op.drop_constraint("ck_code_graph_import_resolution", "code_graph_imports", type_="check")
    # PostgreSQL rejects a downgrade with absolute facts rather than discarding saved import history.
    op.create_check_constraint(
        "ck_code_graph_import_resolution", "code_graph_imports",
        "(target_path IS NULL AND provenance = 'unresolved') OR "
        "(target_path IS NOT NULL AND provenance = 'relative_path')",
    )
