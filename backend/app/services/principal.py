"""De ingelogde gebruiker plus zijn rollen, zoals services die nodig hebben."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.context import RequestMeta
from app.models import Membership, OrgRole, TeamRole, User

ORG_MANAGER_ROLES = {OrgRole.owner.value, OrgRole.admin.value}


@dataclass(frozen=True, slots=True)
class Actor:
    """Wie een actie uitvoert, voor de auditlog."""

    type: str  # user | system | idp | anonymous
    user_id: UUID | None = None
    label: str | None = None
    meta: RequestMeta = field(default_factory=RequestMeta)

    @classmethod
    def system(cls, label: str = "system", meta: RequestMeta | None = None) -> Actor:
        return cls(type="system", label=label, meta=meta or RequestMeta())

    @classmethod
    def anonymous(cls, meta: RequestMeta) -> Actor:
        return cls(type="anonymous", meta=meta)

    @classmethod
    def for_user(cls, user: User, meta: RequestMeta) -> Actor:
        return cls(type="user", user_id=user.id, label=user.email or user.username, meta=meta)


@dataclass(slots=True)
class Principal:
    user: User
    auth_method: str  # session | bearer
    meta: RequestMeta
    session_id: UUID | None = None
    org_roles: dict[UUID, str] = field(default_factory=dict)
    team_roles: dict[UUID, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        user: User,
        memberships: list[Membership],
        *,
        auth_method: str,
        meta: RequestMeta,
        session_id: UUID | None = None,
    ) -> Principal:
        org_roles: dict[UUID, str] = {}
        team_roles: dict[UUID, str] = {}
        for m in memberships:
            if m.team_id is None:
                org_roles[m.organization_id] = m.role
            else:
                team_roles[m.team_id] = m.role
        return cls(
            user=user,
            auth_method=auth_method,
            meta=meta,
            session_id=session_id,
            org_roles=org_roles,
            team_roles=team_roles,
        )

    @property
    def actor(self) -> Actor:
        return Actor.for_user(self.user, self.meta)

    @property
    def is_admin(self) -> bool:
        return self.user.is_admin

    def can_view_org(self, org_id: UUID) -> bool:
        return self.is_admin or org_id in self.org_roles

    def can_manage_org(self, org_id: UUID) -> bool:
        return self.is_admin or self.org_roles.get(org_id) in ORG_MANAGER_ROLES

    def is_org_owner(self, org_id: UUID) -> bool:
        return self.is_admin or self.org_roles.get(org_id) == OrgRole.owner.value

    def can_manage_team(self, org_id: UUID, team_id: UUID) -> bool:
        return self.can_manage_org(org_id) or self.team_roles.get(team_id) == TeamRole.maintainer.value

    @property
    def managed_org_ids(self) -> list[UUID]:
        return [org for org, role in self.org_roles.items() if role in ORG_MANAGER_ROLES]
