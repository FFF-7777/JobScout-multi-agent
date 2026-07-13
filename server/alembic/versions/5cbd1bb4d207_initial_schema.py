"""initial baseline

Revision ID: 5cbd1bb4d207
Revises:
Create Date: 2026-07-13 14:53:24.762717

This is the baseline migration — it does nothing.
Existing databases are handled by the legacy _migrate_* functions in database.py,
and are stamped at this revision to mark them as Alembic-managed.

Future schema changes will be applied by new Alembic revisions on top of this baseline.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5cbd1bb4d207'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline — no schema changes."""
    pass


def downgrade() -> None:
    """Baseline — cannot downgrade past initial state."""
    pass
