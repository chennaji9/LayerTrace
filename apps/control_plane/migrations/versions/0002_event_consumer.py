"""Durable event receipts and diagnosis input signatures.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("event_receipts", sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
                    sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False))
    op.add_column("diagnoses", sa.Column("input_signature", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("diagnoses", "input_signature")
    op.drop_table("event_receipts")
