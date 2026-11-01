"""merge fork chain with upstream 1.3.1

Revision ID: 9f37f23e2c32
Revises: 51e26899440a, 21042b4645f1
Create Date: 2026-11-01 00:00:00.000000

Joins the two heads that exist after taking upstream 1.3.1:

- ``51e26899440a``: the fork's chain, which already merges the fork approval
  migrations with upstream 1.2.1 and 1.3.0-alpha.3.
- ``21042b4645f1``: the head of upstream 1.3.1 (durable workspace sync
  operations, managed resource side tables, case search vectors and words,
  agent session command, WebAuthn MFA, MCP refresh token revocation).

Schema changes: none. This revision only merges the histories.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "9f37f23e2c32"
down_revision: str | Sequence[str] | None = ("51e26899440a", "21042b4645f1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
