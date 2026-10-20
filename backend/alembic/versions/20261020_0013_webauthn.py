"""webauthn: security keys and passkeys as second step

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-20 17:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'webauthn_credentials',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('credential_id', sa.LargeBinary(), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.BigInteger(), nullable=False),
        sa.Column('transports', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('aaguid', sa.String(length=36), nullable=True),
        sa.Column('backed_up', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_challenge_ms', sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_webauthn_credentials_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_webauthn_credentials')),
        sa.UniqueConstraint('user_id', 'scope', 'credential_id', name='uq_webauthn_credentials_user_scope_cred'),
    )
    op.create_index(op.f('ix_webauthn_credentials_user_id'), 'webauthn_credentials', ['user_id'], unique=False)
    op.add_column('users', sa.Column('login_recovery_code_hash', sa.String(length=64), nullable=True))
    # Tweestapsverificatie van de kluis hangt voortaan aan two_factor_enabled_at (app of sleutel).
    op.execute(
        "UPDATE vault_accounts SET two_factor_enabled_at = now() "
        "WHERE totp_secret IS NOT NULL AND two_factor_enabled_at IS NULL"
    )


def downgrade() -> None:
    # Kluizen met enkel sleutels (zonder app) hebben na een downgrade geen tweede stap meer.
    op.execute("UPDATE vault_accounts SET two_factor_enabled_at = NULL WHERE totp_secret IS NULL")
    op.drop_column('users', 'login_recovery_code_hash')
    op.drop_index(op.f('ix_webauthn_credentials_user_id'), table_name='webauthn_credentials')
    op.drop_table('webauthn_credentials')
