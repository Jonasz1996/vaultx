"""npm beheer: journal entries for every NPM object type (object_type) and new actions

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-22 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_ACTIONS = "action IN ('protect','unprotect','publish','unpublish')"
NEW_ACTIONS = (
    "action IN ('protect','unprotect','publish','unpublish','create','update','delete','enable',"
    "'disable','renew','upload')"
)
OBJECT_TYPES = (
    "object_type IN ('proxy_host','redirection_host','dead_host','stream','access_list',"
    "'certificate')"
)


def upgrade() -> None:
    op.add_column(
        'npm_changes',
        sa.Column('object_type', sa.String(length=24), server_default='proxy_host', nullable=False),
    )
    op.create_check_constraint(op.f('ck_npm_changes_object_type_valid'), 'npm_changes', OBJECT_TYPES)
    op.drop_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', type_='check')
    op.create_check_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', NEW_ACTIONS)
    op.drop_index('uq_npm_changes_running_host', table_name='npm_changes', postgresql_where=sa.text("status = 'running'"))
    op.create_index(
        'uq_npm_changes_running_host',
        'npm_changes',
        ['connection_id', 'object_type', 'npm_id'],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM npm_changes WHERE object_type <> 'proxy_host' OR NOT (" + OLD_ACTIONS + ")")
    op.drop_index('uq_npm_changes_running_host', table_name='npm_changes', postgresql_where=sa.text("status = 'running'"))
    op.create_index(
        'uq_npm_changes_running_host',
        'npm_changes',
        ['connection_id', 'npm_id'],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', type_='check')
    op.create_check_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', OLD_ACTIONS)
    op.drop_constraint(op.f('ck_npm_changes_object_type_valid'), 'npm_changes', type_='check')
    op.drop_column('npm_changes', 'object_type')
