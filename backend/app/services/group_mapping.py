"""Vertaling van Authentik-groepen naar VaultX-rollen en lidmaatschappen.

Conventie (prefix instelbaar via VAULTX_OIDC_GROUP_PREFIX, standaard "vaultx:"):

    vaultx:<org>                    lid van organisatie <org>
    vaultx:<org>:<rol>              organisatierol: owner | admin | member
    vaultx:<org>/<team>             lid van team <team> in <org>
    vaultx:<org>/<team>:maintainer  teamrol: maintainer | member

Teamlidmaatschap impliceert lidmaatschap van de organisatie. Bij meerdere
groepen voor dezelfde organisatie wint de hoogste rol. Instantiebeheerders
komen uit VAULTX_OIDC_ADMIN_GROUPS en staan los van deze conventie.
Er worden geen organisaties of teams aangemaakt: groepen die naar een
onbekende slug verwijzen, worden genegeerd en in de audit vermeld.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import OrgRole, TeamRole

ORG_ROLE_RANK = {OrgRole.member.value: 1, OrgRole.admin.value: 2, OrgRole.owner.value: 3}
TEAM_ROLE_RANK = {TeamRole.member.value: 1, TeamRole.maintainer.value: 2}
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(slots=True)
class DesiredMemberships:
    # org-slug -> rol
    orgs: dict[str, str] = field(default_factory=dict)
    # (org-slug, team-slug) -> rol
    teams: dict[tuple[str, str], str] = field(default_factory=dict)
    # groepen met het prefix die niet geparsed konden worden
    invalid: list[str] = field(default_factory=list)


def _raise_org(desired: DesiredMemberships, org: str, role: str) -> None:
    current = desired.orgs.get(org)
    if current is None or ORG_ROLE_RANK[role] > ORG_ROLE_RANK[current]:
        desired.orgs[org] = role


def parse_groups(groups: list[str], prefix: str) -> DesiredMemberships:
    desired = DesiredMemberships()
    for group in groups:
        if not group.startswith(prefix):
            continue
        body = group[len(prefix) :]
        scope, _, role = body.partition(":")
        org, _, team = scope.partition("/")
        if not SLUG_RE.match(org) or (team and not SLUG_RE.match(team)):
            desired.invalid.append(group)
            continue
        if team:
            role = role or TeamRole.member.value
            if role not in TEAM_ROLE_RANK:
                desired.invalid.append(group)
                continue
            current = desired.teams.get((org, team))
            if current is None or TEAM_ROLE_RANK[role] > TEAM_ROLE_RANK[current]:
                desired.teams[(org, team)] = role
            _raise_org(desired, org, OrgRole.member.value)
        else:
            role = role or OrgRole.member.value
            if role not in ORG_ROLE_RANK:
                desired.invalid.append(group)
                continue
            _raise_org(desired, org, role)
    return desired
