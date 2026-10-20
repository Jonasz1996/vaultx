"""phase10 links

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20 00:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('link_collections',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('color', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['parent_id'], ['link_collections.id'], name=op.f('fk_link_collections_parent_id_link_collections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_link_collections_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_link_collections'))
    )
    op.create_index(op.f('ix_link_collections_parent_id'), 'link_collections', ['parent_id'], unique=False)
    op.create_index(op.f('ix_link_collections_user_id'), 'link_collections', ['user_id'], unique=False)
    op.create_table('link_tags',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_link_tags_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_link_tags')),
    sa.UniqueConstraint('user_id', 'name', name='uq_link_tags_user_name')
    )
    op.create_index(op.f('ix_link_tags_user_id'), 'link_tags', ['user_id'], unique=False)
    op.create_table('links',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('collection_id', sa.UUID(), nullable=True),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('text_content', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['link_collections.id'], name=op.f('fk_links_collection_id_link_collections'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_links_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_links'))
    )
    op.create_index(op.f('ix_links_collection_id'), 'links', ['collection_id'], unique=False)
    op.create_index(op.f('ix_links_user_id'), 'links', ['user_id'], unique=False)
    op.create_table('link_archives',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('link_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['link_id'], ['links.id'], name=op.f('fk_link_archives_link_id_links'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_link_archives')),
    sa.UniqueConstraint('link_id', 'kind', name='uq_link_archives_link_kind')
    )
    op.create_index(op.f('ix_link_archives_link_id'), 'link_archives', ['link_id'], unique=False)
    op.create_table('link_tag_map',
    sa.Column('link_id', sa.UUID(), nullable=False),
    sa.Column('tag_id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['link_id'], ['links.id'], name=op.f('fk_link_tag_map_link_id_links'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id'], ['link_tags.id'], name=op.f('fk_link_tag_map_tag_id_link_tags'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('link_id', 'tag_id', name=op.f('pk_link_tag_map'))
    )
    op.create_index(op.f('ix_link_tag_map_tag_id'), 'link_tag_map', ['tag_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_link_tag_map_tag_id'), table_name='link_tag_map')
    op.drop_table('link_tag_map')
    op.drop_index(op.f('ix_link_archives_link_id'), table_name='link_archives')
    op.drop_table('link_archives')
    op.drop_index(op.f('ix_links_user_id'), table_name='links')
    op.drop_index(op.f('ix_links_collection_id'), table_name='links')
    op.drop_table('links')
    op.drop_index(op.f('ix_link_tags_user_id'), table_name='link_tags')
    op.drop_table('link_tags')
    op.drop_index(op.f('ix_link_collections_user_id'), table_name='link_collections')
    op.drop_index(op.f('ix_link_collections_parent_id'), table_name='link_collections')
    op.drop_table('link_collections')
