"""merge fork approval chain with upstream 1.2.1

Revision ID: 261af2786dda
Revises: 1b7b004b3c68, 3b8f2c1d9e7a
Create Date: 2026-10-21 00:00:00.000000

Joins the two heads that exist after taking upstream 1.2.1:

- ``1b7b004b3c68``: the fork's own chain (pinned version ids, approval vote
  table, approval case event types, ``approval.case_id``). Production was already
  upgraded to this revision, so those migrations are kept as they are instead of
  being re-parented onto the upstream chain.
- ``3b8f2c1d9e7a``: the head of upstream 1.2.1.

Schema changes: none. This revision only merges the histories.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "261af2786dda"
down_revision: str | Sequence[str] | None = ("1b7b004b3c68", "3b8f2c1d9e7a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
