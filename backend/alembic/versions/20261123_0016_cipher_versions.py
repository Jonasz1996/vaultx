"""kluisitems: vorige versies bewaren (vault_cipher_versions)

Revision ID: 0016_itemversies
Revises: 0015
Create Date: 2026-11-23 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_itemversies"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_cipher_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("cipher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("revision_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("replaced_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["vault_accounts.user_id"],
            name=op.f("fk_vault_cipher_versions_user_id_vault_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vault_cipher_versions")),
    )
    op.create_index(op.f("ix_vault_cipher_versions_cipher_id"), "vault_cipher_versions", ["cipher_id"])
    op.create_index(op.f("ix_vault_cipher_versions_user_id"), "vault_cipher_versions", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_vault_cipher_versions_user_id"), table_name="vault_cipher_versions")
    op.drop_index(op.f("ix_vault_cipher_versions_cipher_id"), table_name="vault_cipher_versions")
    op.drop_table("vault_cipher_versions")
