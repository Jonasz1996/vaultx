"""device unlock: open the vault with Authentik on a device (VaultX Invuller)

Revision ID: 0017
Revises: 0016_herstel
Create Date: 2026-11-17 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0017'
down_revision: str | None = '0016_herstel'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'vault_device_unlock',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('device_identifier', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('blob', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_ip', sa.String(length=64), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['vault_accounts.user_id'],
            name=op.f('fk_vault_device_unlock_user_id_vault_accounts'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_device_unlock')),
    )
    op.create_index(op.f('ix_vault_device_unlock_user_id'), 'vault_device_unlock', ['user_id'], unique=False)
    op.create_index(
        'uq_vault_device_unlock_active',
        'vault_device_unlock',
        ['user_id', 'device_identifier'],
        unique=True,
        postgresql_where=sa.text('revoked_at IS NULL'),
    )
    op.add_column('user_sessions', sa.Column('mfa', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('user_sessions', sa.Column('idp_auth_time', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'vault_accounts', sa.Column('device_unlock_enabled', sa.Boolean(), server_default='true', nullable=False)
    )


def downgrade() -> None:
    op.drop_column('vault_accounts', 'device_unlock_enabled')
    op.drop_column('user_sessions', 'idp_auth_time')
    op.drop_column('user_sessions', 'mfa')
    op.drop_index('uq_vault_device_unlock_active', table_name='vault_device_unlock', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index(op.f('ix_vault_device_unlock_user_id'), table_name='vault_device_unlock')
    op.drop_table('vault_device_unlock')
