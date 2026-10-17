"""phase9 bitwarden parity: two-step login

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-17 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('vault_accounts', sa.Column('totp_secret', sa.LargeBinary(), nullable=True))
    op.add_column('vault_accounts', sa.Column('totp_pending', sa.LargeBinary(), nullable=True))
    op.add_column('vault_accounts', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.add_column('vault_accounts', sa.Column('recovery_code_hash', sa.String(length=64), nullable=True))
    op.add_column('vault_accounts', sa.Column('two_factor_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vault_devices', sa.Column('remember_token_hash', sa.String(length=64), nullable=True))
    op.add_column('vault_devices', sa.Column('remember_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('vault_devices', 'remember_until')
    op.drop_column('vault_devices', 'remember_token_hash')
    op.drop_column('vault_accounts', 'two_factor_enabled_at')
    op.drop_column('vault_accounts', 'recovery_code_hash')
    op.drop_column('vault_accounts', 'totp_last_step')
    op.drop_column('vault_accounts', 'totp_pending')
    op.drop_column('vault_accounts', 'totp_secret')
