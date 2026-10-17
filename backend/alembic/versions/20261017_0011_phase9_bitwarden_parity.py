"""phase9 bitwarden parity: two-step login and attachments

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
    op.create_table(
        'vault_attachments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('cipher_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('file_name', sa.Text(), nullable=False),
        sa.Column('key', sa.Text(), nullable=True),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['cipher_id'], ['vault_ciphers.id'], name=op.f('fk_vault_attachments_cipher_id_vault_ciphers'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['vault_accounts.user_id'], name=op.f('fk_vault_attachments_user_id_vault_accounts'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_attachments')),
    )
    op.create_index(op.f('ix_vault_attachments_cipher_id'), 'vault_attachments', ['cipher_id'], unique=False)
    op.create_index(op.f('ix_vault_attachments_user_id'), 'vault_attachments', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_vault_attachments_user_id'), table_name='vault_attachments')
    op.drop_index(op.f('ix_vault_attachments_cipher_id'), table_name='vault_attachments')
    op.drop_table('vault_attachments')
    op.drop_column('vault_devices', 'remember_until')
    op.drop_column('vault_devices', 'remember_token_hash')
    op.drop_column('vault_accounts', 'two_factor_enabled_at')
    op.drop_column('vault_accounts', 'recovery_code_hash')
    op.drop_column('vault_accounts', 'totp_last_step')
    op.drop_column('vault_accounts', 'totp_pending')
    op.drop_column('vault_accounts', 'totp_secret')
