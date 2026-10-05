"""phase6 app publish

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-05 21:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('npm_connections', sa.Column('theme_css_template', sa.String(length=2048), nullable=True))
    op.add_column('npm_hosts', sa.Column('vaultx_published', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.drop_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', type_='check')
    op.create_check_constraint(
        op.f('ck_npm_changes_action_valid'),
        'npm_changes',
        "action IN ('protect','unprotect','publish','unpublish')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM npm_changes WHERE action IN ('publish','unpublish')")
    op.drop_constraint(op.f('ck_npm_changes_action_valid'), 'npm_changes', type_='check')
    op.create_check_constraint(
        op.f('ck_npm_changes_action_valid'), 'npm_changes', "action IN ('protect','unprotect')"
    )
    op.drop_column('npm_hosts', 'vaultx_published')
    op.drop_column('npm_connections', 'theme_css_template')
