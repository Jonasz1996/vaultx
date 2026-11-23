"""account reset: reset requests with a code, temporary password changed at the first login

Revision ID: 0016_herstel
Revises: 0015
Create Date: 2026-11-23 19:00:00.000000

De revisie heet bewust niet '0016': andere openstaande werkstukken nummeren ook na 0015.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016_herstel'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'account_reset_requests',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('login', sa.String(length=320), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_account_reset_requests')),
        sa.UniqueConstraint('code_hash', name=op.f('uq_account_reset_requests_code_hash')),
    )
    op.create_index(
        op.f('ix_account_reset_requests_login'), 'account_reset_requests', ['login'], unique=False
    )
    op.add_column(
        'users',
        sa.Column('password_temporary_until', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('users', 'password_temporary_until')
    op.drop_index(op.f('ix_account_reset_requests_login'), table_name='account_reset_requests')
    op.drop_table('account_reset_requests')
