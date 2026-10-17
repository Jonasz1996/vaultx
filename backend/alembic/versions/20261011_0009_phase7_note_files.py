"""phase7 note attachments

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-11 01:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('vault_note_files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['vault_accounts.user_id'], name=op.f('fk_vault_note_files_user_id_vault_accounts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_note_files'))
    )
    op.create_index(op.f('ix_vault_note_files_user_id'), 'vault_note_files', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_vault_note_files_user_id'), table_name='vault_note_files')
    op.drop_table('vault_note_files')
