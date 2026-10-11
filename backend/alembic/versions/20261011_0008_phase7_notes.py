"""phase7 notes

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-11 00:35:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('vault_notes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['vault_accounts.user_id'], name=op.f('fk_vault_notes_user_id_vault_accounts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_notes'))
    )
    op.create_index(op.f('ix_vault_notes_user_id'), 'vault_notes', ['user_id'], unique=False)
    op.create_table('vault_note_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('note_id', sa.UUID(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('saved_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['note_id'], ['vault_notes.id'], name=op.f('fk_vault_note_versions_note_id_vault_notes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_vault_note_versions')),
    sa.UniqueConstraint('note_id', 'revision', name='uq_vault_note_versions_note_revision')
    )
    op.create_index(op.f('ix_vault_note_versions_note_id'), 'vault_note_versions', ['note_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_vault_note_versions_note_id'), table_name='vault_note_versions')
    op.drop_table('vault_note_versions')
    op.drop_index(op.f('ix_vault_notes_user_id'), table_name='vault_notes')
    op.drop_table('vault_notes')
