"""Organisaties, teams en lidmaatschappen."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidOperationError, NotFoundError
from app.models import Membership, MembershipSource, Organization, OrgRole, Team
from app.repositories import MembershipRepository, OrganizationRepository, TeamRepository, UserRepository
from app.schemas.organizations import (
    OrganizationCreate,
    OrganizationUpdate,
    TeamCreate,
    TeamUpdate,
)
from app.services.audit import AuditService
from app.services.principal import Principal


def _diff(obj: object, changes: dict) -> dict:
    return {k: {"from": getattr(obj, k), "to": v} for k, v in changes.items() if getattr(obj, k) != v}


class TenancyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.orgs = OrganizationRepository(db)
        self.teams = TeamRepository(db)
        self.memberships = MembershipRepository(db)
        self.users = UserRepository(db)
        self.audit = AuditService(db)

    # ------------------------------------------------------------ helpers

    async def _org_visible(self, p: Principal, org_id: UUID) -> Organization:
        org = await self.orgs.get(org_id)
        if org is None or not p.can_view_org(org_id):
            raise NotFoundError("Organisatie niet gevonden")
        return org

    async def _team_visible(self, p: Principal, org_id: UUID, team_id: UUID) -> Team:
        await self._org_visible(p, org_id)
        team = await self.teams.in_org(org_id, team_id)
        if team is None:
            raise NotFoundError("Team niet gevonden")
        return team

    async def _commit(self, conflict_message: str) -> None:
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ConflictError(conflict_message) from exc

    # ------------------------------------------------------------ organisaties

    async def list_orgs(self, p: Principal, *, limit: int, offset: int):
        only = None if p.is_admin else list(p.org_roles)
        orgs, total = await self.orgs.list_page(limit=limit, offset=offset, only_ids=only)
        ids = [o.id for o in orgs]
        return orgs, total, await self.orgs.member_counts(ids), await self.orgs.team_counts(ids)

    async def get_org(self, p: Principal, org_id: UUID) -> Organization:
        return await self._org_visible(p, org_id)

    async def org_counts(self, org_id: UUID) -> tuple[int, int]:
        return (
            (await self.orgs.member_counts([org_id])).get(org_id, 0),
            (await self.orgs.team_counts([org_id])).get(org_id, 0),
        )

    async def create_org(self, p: Principal, data: OrganizationCreate) -> Organization:
        if not p.is_admin:
            raise await self.audit.deny(
                "organization.create", p.actor, target_type="organization", target_id=data.slug
            )
        if await self.orgs.by_slug(data.slug):
            raise ConflictError(f"Slug '{data.slug}' is al in gebruik")
        org = self.orgs.add(Organization(**data.model_dump()))
        await self.db.flush()
        await self.audit.record(
            "organization.created",
            p.actor,
            organization_id=org.id,
            target_type="organization",
            target_id=org.id,
            details=data.model_dump(),
        )
        await self._commit(f"Slug '{data.slug}' is al in gebruik")
        return org

    async def update_org(self, p: Principal, org_id: UUID, data: OrganizationUpdate) -> Organization:
        org = await self._org_visible(p, org_id)
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                "organization.update",
                p.actor,
                organization_id=org_id,
                target_type="organization",
                target_id=org_id,
            )
        changes = _diff(org, data.model_dump(exclude_unset=True))
        for field, change in changes.items():
            setattr(org, field, change["to"])
        if changes:
            await self.audit.record(
                "organization.updated",
                p.actor,
                organization_id=org.id,
                target_type="organization",
                target_id=org.id,
                details={"changes": changes},
            )
        await self._commit("Conflict bij bijwerken")
        return org

    async def delete_org(self, p: Principal, org_id: UUID) -> None:
        org = await self._org_visible(p, org_id)
        if not p.is_org_owner(org_id):
            raise await self.audit.deny(
                "organization.delete",
                p.actor,
                organization_id=org_id,
                target_type="organization",
                target_id=org_id,
            )
        # Eerst in de eigen keten (sluitstuk), dan in de instantieketen die blijft bestaan.
        await self.audit.record(
            "organization.deleted",
            p.actor,
            organization_id=org.id,
            target_type="organization",
            target_id=org.id,
        )
        await self.audit.record(
            "organization.deleted",
            p.actor,
            target_type="organization",
            target_id=org.id,
            details={"slug": org.slug, "name": org.name},
        )
        await self.orgs.delete(org)
        await self._commit("Organisatie kon niet verwijderd worden")

    # ------------------------------------------------------------ teams

    async def list_teams(self, p: Principal, org_id: UUID):
        await self._org_visible(p, org_id)
        teams = await self.teams.list_for_org(org_id)
        return teams, await self.teams.member_counts([t.id for t in teams])

    async def get_team(self, p: Principal, org_id: UUID, team_id: UUID) -> Team:
        return await self._team_visible(p, org_id, team_id)

    async def create_team(self, p: Principal, org_id: UUID, data: TeamCreate) -> Team:
        await self._org_visible(p, org_id)
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                "team.create", p.actor, organization_id=org_id, target_type="team", target_id=data.slug
            )
        if await self.teams.by_slug(org_id, data.slug):
            raise ConflictError(f"Team '{data.slug}' bestaat al in deze organisatie")
        team = self.teams.add(Team(organization_id=org_id, **data.model_dump()))
        await self.db.flush()
        await self.audit.record(
            "team.created",
            p.actor,
            organization_id=org_id,
            target_type="team",
            target_id=team.id,
            details=data.model_dump(),
        )
        await self._commit(f"Team '{data.slug}' bestaat al in deze organisatie")
        return team

    async def update_team(self, p: Principal, org_id: UUID, team_id: UUID, data: TeamUpdate) -> Team:
        team = await self._team_visible(p, org_id, team_id)
        if not p.can_manage_team(org_id, team_id):
            raise await self.audit.deny(
                "team.update", p.actor, organization_id=org_id, target_type="team", target_id=team_id
            )
        changes = _diff(team, data.model_dump(exclude_unset=True))
        for field, change in changes.items():
            setattr(team, field, change["to"])
        if changes:
            await self.audit.record(
                "team.updated",
                p.actor,
                organization_id=org_id,
                target_type="team",
                target_id=team.id,
                details={"changes": changes},
            )
        await self._commit("Conflict bij bijwerken")
        return team

    async def delete_team(self, p: Principal, org_id: UUID, team_id: UUID) -> None:
        team = await self._team_visible(p, org_id, team_id)
        if not p.can_manage_org(org_id):
            raise await self.audit.deny(
                "team.delete", p.actor, organization_id=org_id, target_type="team", target_id=team_id
            )
        await self.audit.record(
            "team.deleted",
            p.actor,
            organization_id=org_id,
            target_type="team",
            target_id=team.id,
            details={"slug": team.slug, "name": team.name},
        )
        await self.teams.delete(team)
        await self._commit("Team kon niet verwijderd worden")

    # ------------------------------------------------------------ lidmaatschappen

    async def list_members(self, p: Principal, org_id: UUID, team_id: UUID | None) -> list[Membership]:
        if team_id is None:
            await self._org_visible(p, org_id)
        else:
            await self._team_visible(p, org_id, team_id)
        return await self.memberships.list_scope(org_id, team_id)

    async def add_member(
        self, p: Principal, org_id: UUID, team_id: UUID | None, user_id: UUID, role: str
    ) -> Membership:
        if team_id is None:
            await self._org_visible(p, org_id)
            allowed = p.can_manage_org(org_id)
            # Enkel een eigenaar (of instantiebeheerder) kan iemand eigenaar maken.
            if role == OrgRole.owner.value and not p.is_org_owner(org_id):
                allowed = False
        else:
            await self._team_visible(p, org_id, team_id)
            allowed = p.can_manage_team(org_id, team_id)
        if not allowed:
            raise await self.audit.deny(
                "membership.create",
                p.actor,
                organization_id=org_id,
                target_type="user",
                target_id=user_id,
            )
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("Gebruiker niet gevonden")
        if team_id is not None and await self.memberships.org_membership(org_id, user_id) is None:
            raise InvalidOperationError("Gebruiker moet eerst lid zijn van de organisatie")
        existing = (
            await self.memberships.org_membership(org_id, user_id)
            if team_id is None
            else await self.memberships.team_membership(team_id, user_id)
        )
        if existing:
            raise ConflictError("Gebruiker is al lid")
        m = self.memberships.add(
            Membership(
                user_id=user_id,
                organization_id=org_id,
                team_id=team_id,
                role=role,
                source=MembershipSource.manual.value,
            )
        )
        await self.db.flush()
        await self.audit.record(
            "membership.created",
            p.actor,
            organization_id=org_id,
            target_type="user",
            target_id=user_id,
            details={"team_id": team_id, "role": role, "membership_id": m.id},
        )
        await self._commit("Gebruiker is al lid")
        await self.db.refresh(m, ["user"])
        return m

    async def _member_for_change(
        self, p: Principal, action: str, org_id: UUID, team_id: UUID | None, membership_id: UUID
    ) -> Membership:
        if team_id is None:
            await self._org_visible(p, org_id)
        else:
            await self._team_visible(p, org_id, team_id)
        m = await self.memberships.in_scope(membership_id, org_id, team_id)
        if m is None:
            raise NotFoundError("Lidmaatschap niet gevonden")
        allowed = p.can_manage_org(org_id) if team_id is None else p.can_manage_team(org_id, team_id)
        if team_id is None and m.role == OrgRole.owner.value and not p.is_org_owner(org_id):
            allowed = False
        if not allowed:
            raise await self.audit.deny(
                action, p.actor, organization_id=org_id, target_type="user", target_id=m.user_id
            )
        if m.source == MembershipSource.idp.value:
            raise InvalidOperationError(
                "Dit lidmaatschap komt uit een Authentik-groep; wijzig het in Authentik"
            )
        return m

    async def _guard_last_owner(self, org_id: UUID, m: Membership) -> None:
        if (
            m.team_id is None
            and m.role == OrgRole.owner.value
            and await self.memberships.count_owners(org_id) <= 1
        ):
            raise InvalidOperationError("De laatste eigenaar van een organisatie kan niet weg")

    async def update_member(
        self, p: Principal, org_id: UUID, team_id: UUID | None, membership_id: UUID, role: str
    ) -> Membership:
        m = await self._member_for_change(p, "membership.update", org_id, team_id, membership_id)
        if team_id is None and role == OrgRole.owner.value and not p.is_org_owner(org_id):
            raise await self.audit.deny(
                "membership.update", p.actor, organization_id=org_id, target_type="user", target_id=m.user_id
            )
        if m.role == role:
            return m
        if role != OrgRole.owner.value:
            await self._guard_last_owner(org_id, m)
        old = m.role
        m.role = role
        await self.audit.record(
            "membership.updated",
            p.actor,
            organization_id=org_id,
            target_type="user",
            target_id=m.user_id,
            details={"team_id": team_id, "from": old, "to": role, "source": m.source},
        )
        await self._commit("Conflict bij bijwerken")
        return m

    async def remove_member(
        self, p: Principal, org_id: UUID, team_id: UUID | None, membership_id: UUID
    ) -> None:
        m = await self._member_for_change(p, "membership.delete", org_id, team_id, membership_id)
        await self._guard_last_owner(org_id, m)
        if team_id is None:
            # Wie de organisatie verlaat, verlaat ook al haar teams.
            await self.memberships.delete_user_team_memberships(org_id, m.user_id)
        await self.audit.record(
            "membership.deleted",
            p.actor,
            organization_id=org_id,
            target_type="user",
            target_id=m.user_id,
            details={"team_id": team_id, "role": m.role, "source": m.source},
        )
        await self.memberships.delete(m)
        await self._commit("Lidmaatschap kon niet verwijderd worden")
