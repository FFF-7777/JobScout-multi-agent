"""add OCR audit and item-run observability

Revision ID: 8f4c2a1d9e73
Revises: 5cbd1bb4d207
Create Date: 2026-07-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8f4c2a1d9e73"
down_revision: Union[str, Sequence[str], None] = "5cbd1bb4d207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("ocr_metadata", sa.JSON(), nullable=True))
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.add_column(sa.Column("ocr_metadata", sa.JSON(), nullable=True))
    with op.batch_alter_table("agent_item_runs") as batch_op:
        batch_op.add_column(sa.Column("phase", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("metadata_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_item_runs") as batch_op:
        batch_op.drop_column("metadata_json")
        batch_op.drop_column("phase")
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.drop_column("ocr_metadata")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("ocr_metadata")
