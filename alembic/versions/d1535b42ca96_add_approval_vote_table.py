"""add approval_vote table

Revision ID: d1535b42ca96
Revises: a9c1d4e7f2b3
Create Date: 2026-08-24 11:30:00.000000

Hand-trimmed from an autogenerate run: this database has pre-existing drift
(custom soc_cases/case_evidence/ioc_history/ai_decisions/... tables added
outside of Alembic) that autogenerate wants to drop. This migration touches
only the new `approval_vote` table and nothing else.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1535b42ca96"
down_revision: str | None = "a9c1d4e7f2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_vote",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("interaction_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("surrogate_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["interaction_id"],
            ["interaction.id"],
            name=op.f("fk_approval_vote_interaction_id_interaction"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_approval_vote_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspace.id"],
            name=op.f("fk_approval_vote_workspace_id_workspace"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("surrogate_id", name=op.f("pk_approval_vote")),
        sa.UniqueConstraint(
            "workspace_id",
            "interaction_id",
            "user_id",
            name="uq_approval_vote_workspace_interaction_user",
        ),
    )
    op.create_index(
        op.f("ix_approval_vote_id"), "approval_vote", ["id"], unique=True
    )
    op.create_index(
        op.f("ix_approval_vote_interaction_id"),
        "approval_vote",
        ["interaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_approval_vote_user_id"),
        "approval_vote",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_vote_user_id"), table_name="approval_vote")
    op.drop_index(
        op.f("ix_approval_vote_interaction_id"), table_name="approval_vote"
    )
    op.drop_index(op.f("ix_approval_vote_id"), table_name="approval_vote")
    op.drop_table("approval_vote")
