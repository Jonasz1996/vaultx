"""Gebruikers aanmaken/bijwerken vanuit OIDC-claims en groepen synchroniseren."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.context import RequestMeta
from app.core.errors import PermissionDeniedError
from app.models import Membership, MembershipSource, User
from app.repositories import MembershipRepository, OrganizationRepository, TeamRepository, UserRepository
from app.services.audit import AuditService
from app.services.group_mapping import parse_groups
from app.services.principal import Actor

log = logging.getLogger(__name__)


@dataclass(slots=True)
class LoginResult:
    user: User
    created: bool


def _groups_from_claims(claims: dict[str, Any]) -> list[str]:
    groups = claims.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    return sorted({str(g) for g in groups if g})


class IdentityService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.users = UserRepository(db)
        self.orgs = OrganizationRepository(db)
        self.teams = TeamRepository(db)
        self.memberships = MembershipRepository(db)
        self.audit = AuditService(db)

    async def login_from_claims(self, claims: dict[str, Any], meta: RequestMeta) -> LoginResult:
        """Upsert van de gebruiker + groepsynchronisatie. Commit niet."""
        issuer, subject = claims["iss"], str(claims["sub"])
        user = await self.users.by_identity(issuer, subject)
        created = user is None
        if user is None:
            user = self.users.add(User(oidc_issuer=issuer, oidc_subject=subject, idp_groups=[]))

        if not created and not user.is_active:
            await self.audit.record(
                "auth.login",
                Actor.for_user(user, meta),
                outcome="denied",
                target_type="user",
                target_id=user.id,
                details={"reason": "account gedeactiveerd in VaultX"},
            )
            await self.db.commit()
            raise PermissionDeniedError("Dit account is gedeactiveerd in VaultX")

        groups = _groups_from_claims(claims)
        user.email = claims.get("email") or user.email
        user.email_verified = bool(claims.get("email_verified", False))
        user.username = claims.get("preferred_username") or user.username
        user.display_name = claims.get("name") or user.display_name
        was_admin = user.is_admin
        user.is_admin = any(g in self.settings.oidc_admin_groups for g in groups)
        user.idp_groups = groups
        user.last_login_at = datetime.now(UTC)
        await self.db.flush()

        actor = Actor.for_user(user, meta)
        if created:
            await self.audit.record(
                "user.provisioned",
                Actor(type="idp", label=issuer, meta=meta),
                target_type="user",
                target_id=user.id,
                details={"subject": subject, "email": user.email, "is_admin": user.is_admin},
            )
        elif was_admin != user.is_admin:
            await self.audit.record(
                "user.admin_changed",
                Actor(type="idp", label=issuer, meta=meta),
                target_type="user",
                target_id=user.id,
                details={"is_admin": user.is_admin, "source": "idp_groups"},
            )

        if self.settings.oidc_group_sync:
            await self.sync_memberships(user, groups, meta)

        await self.audit.record(
            "auth.login",
            actor,
            target_type="user",
            target_id=user.id,
            details={"groups": groups, "is_admin": user.is_admin},
        )
        return LoginResult(user=user, created=created)

    async def sync_memberships(self, user: User, groups: list[str], meta: RequestMeta) -> None:
        desired = parse_groups(groups, self.settings.oidc_group_prefix)
        orgs = await self.orgs.by_slugs(set(desired.orgs) | {o for o, _ in desired.teams})
        teams = await self.teams.by_org_slug_pairs({(orgs[o].id, t) for o, t in desired.teams if o in orgs})

        # Gewenste toestand: (org_id, team_id|None) -> rol
        want: dict[tuple[UUID, UUID | None], str] = {}
        unmatched: list[str] = []
        for slug, role in desired.orgs.items():
            if slug in orgs:
                want[(orgs[slug].id, None)] = role
            else:
                unmatched.append(slug)
        for (org_slug, team_slug), role in desired.teams.items():
            org = orgs.get(org_slug)
            team = teams.get((org.id, team_slug)) if org else None
            if team is None:
                unmatched.append(f"{org_slug}/{team_slug}")
                continue
            want[(org.id, team.id)] = role

        existing = await self.memberships.for_user(user.id)
        have = {(m.organization_id, m.team_id): m for m in existing}
        # Een handmatig teamlidmaatschap heeft het organisatielidmaatschap nodig:
        # dat laten we dan staan, ook als de groep verdwenen is.
        for (org_id, team_id), m in have.items():
            org_m = have.get((org_id, None))
            if (
                team_id is not None
                and m.source == MembershipSource.manual.value
                and (org_id, None) not in want
                and org_m is not None
                and org_m.source == MembershipSource.idp.value
            ):
                want[(org_id, None)] = org_m.role

        idp_actor = Actor(type="idp", label=self.settings.oidc_issuer, meta=meta)
        changes: dict[UUID, list[dict[str, Any]]] = {}

        for key, m in have.items():
            if m.source != MembershipSource.idp.value:
                continue  # handmatige lidmaatschappen blijven altijd ongemoeid
            if key not in want:
                await self.memberships.delete(m)
                changes.setdefault(key[0], []).append({"op": "removed", "team_id": key[1], "role": m.role})
            elif m.role != want[key]:
                changes.setdefault(key[0], []).append(
                    {"op": "role_changed", "team_id": key[1], "from": m.role, "to": want[key]}
                )
                m.role = want[key]

        for key, role in want.items():
            if key in have:
                continue
            self.memberships.add(
                Membership(
                    user_id=user.id,
                    organization_id=key[0],
                    team_id=key[1],
                    role=role,
                    source=MembershipSource.idp.value,
                )
            )
            changes.setdefault(key[0], []).append({"op": "added", "team_id": key[1], "role": role})

        await self.db.flush()
        for org_id, org_changes in changes.items():
            await self.audit.record(
                "membership.idp_sync",
                idp_actor,
                organization_id=org_id,
                target_type="user",
                target_id=user.id,
                details={"changes": org_changes},
            )
        if unmatched or desired.invalid:
            log.info("Niet-gekoppelde groepen voor %s: %s %s", user.id, unmatched, desired.invalid)
            await self.audit.record(
                "user.idp_groups_unmatched",
                idp_actor,
                target_type="user",
                target_id=user.id,
                details={"unknown": sorted(unmatched), "invalid": desired.invalid},
            )
