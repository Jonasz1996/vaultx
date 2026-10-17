"""phase8 regular accounts with a VaultX password

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-17 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'oidc_issuer', existing_type=sa.String(length=512), nullable=True)
    op.alter_column('users', 'oidc_subject', existing_type=sa.String(length=255), nullable=True)
    op.add_column('users', sa.Column('admin_granted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('password_hash', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('failed_logins', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_users_lower_username', 'users', [sa.text('lower(username)')], unique=False)
    op.create_index('ix_users_lower_email', 'users', [sa.text('lower(email)')], unique=False)
    op.add_column('user_sessions', sa.Column('auth_method', sa.String(length=16), server_default='oidc', nullable=False))


def downgrade() -> None:
    op.drop_column('user_sessions', 'auth_method')
    op.drop_index('ix_users_lower_email', table_name='users')
    op.drop_index('ix_users_lower_username', table_name='users')
    op.drop_column('users', 'locked_until')
    op.drop_column('users', 'failed_logins')
    op.drop_column('users', 'password_changed_at')
    op.drop_column('users', 'password_hash')
    op.drop_column('users', 'admin_granted')
    # Gewone accounts zonder Authentik-identiteit kunnen niet terug naar het oude schema.
    op.execute("DELETE FROM users WHERE oidc_issuer IS NULL")
    op.alter_column('users', 'oidc_subject', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('users', 'oidc_issuer', existing_type=sa.String(length=512), nullable=False)
