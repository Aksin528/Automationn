"""merge fork chain with upstream 1.3.0-alpha.3

Revision ID: 51e26899440a
Revises: 261af2786dda, b7d41c9e2a6f
Create Date: 2026-10-23 00:00:00.000000

Joins the two heads that exist after taking upstream 1.3.0-alpha.3:

- ``261af2786dda``: the fork's chain, which already merges the fork approval
  migrations with upstream 1.2.1.
- ``b7d41c9e2a6f``: the head of upstream 1.3.0-alpha.3 (agent scopes dropped from
  the org member role, case text search indexes, agent tunnels).

Schema changes: none. This revision only merges the histories.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "51e26899440a"
down_revision: str | Sequence[str] | None = ("261af2786dda", "b7d41c9e2a6f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
