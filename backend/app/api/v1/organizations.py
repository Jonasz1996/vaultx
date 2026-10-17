from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentPrincipal, DbSession
from app.schemas.common import Page
from app.schemas.organizations import (
    MemberOut,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    OrgMemberCreate,
    OrgMemberUpdate,
    TeamCreate,
    TeamMemberCreate,
    TeamMemberUpdate,
    TeamOut,
    TeamUpdate,
)
from app.services.principal import Principal
from app.services.tenancy import TenancyService

router = APIRouter(prefix="/organizations", tags=["organizations"])


async def _org_out(svc: TenancyService, p: Principal, org) -> OrganizationOut:
    members, teams = await svc.org_counts(org.id)
    return OrganizationOut.model_validate(org).model_copy(
        update={"member_count": members, "team_count": teams, "my_role": p.org_roles.get(org.id)}
    )


# ---------------------------------------------------------------- organisaties


@router.get("", response_model=Page[OrganizationOut])
async def list_organizations(
    p: CurrentPrincipal,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    orgs, total, members, teams = await TenancyService(db).list_orgs(p, limit=limit, offset=offset)
    items = [
        OrganizationOut.model_validate(o).model_copy(
            update={
                "member_count": members.get(o.id, 0),
                "team_count": teams.get(o.id, 0),
                "my_role": p.org_roles.get(o.id),
            }
        )
        for o in orgs
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
async def create_organization(data: OrganizationCreate, p: CurrentPrincipal, db: DbSession):
    svc = TenancyService(db)
    return await _org_out(svc, p, await svc.create_org(p, data))


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(org_id: UUID, p: CurrentPrincipal, db: DbSession):
    svc = TenancyService(db)
    return await _org_out(svc, p, await svc.get_org(p, org_id))


@router.patch("/{org_id}", response_model=OrganizationOut)
async def update_organization(org_id: UUID, data: OrganizationUpdate, p: CurrentPrincipal, db: DbSession):
    svc = TenancyService(db)
    return await _org_out(svc, p, await svc.update_org(p, org_id, data))


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(org_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await TenancyService(db).delete_org(p, org_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- organisatieleden


@router.get("/{org_id}/members", response_model=list[MemberOut])
async def list_org_members(org_id: UUID, p: CurrentPrincipal, db: DbSession):
    return await TenancyService(db).list_members(p, org_id, None)


@router.post("/{org_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_org_member(org_id: UUID, data: OrgMemberCreate, p: CurrentPrincipal, db: DbSession):
    return await TenancyService(db).add_member(p, org_id, None, data.user_id, data.role)


@router.patch("/{org_id}/members/{membership_id}", response_model=MemberOut)
async def update_org_member(
    org_id: UUID, membership_id: UUID, data: OrgMemberUpdate, p: CurrentPrincipal, db: DbSession
):
    return await TenancyService(db).update_member(p, org_id, None, membership_id, data.role)


@router.delete("/{org_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_org_member(
    org_id: UUID, membership_id: UUID, p: CurrentPrincipal, db: DbSession
) -> Response:
    await TenancyService(db).remove_member(p, org_id, None, membership_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- teams


@router.get("/{org_id}/teams", response_model=list[TeamOut])
async def list_teams(org_id: UUID, p: CurrentPrincipal, db: DbSession):
    teams, counts = await TenancyService(db).list_teams(p, org_id)
    return [TeamOut.model_validate(t).model_copy(update={"member_count": counts.get(t.id, 0)}) for t in teams]


@router.post("/{org_id}/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
async def create_team(org_id: UUID, data: TeamCreate, p: CurrentPrincipal, db: DbSession):
    return await TenancyService(db).create_team(p, org_id, data)


@router.get("/{org_id}/teams/{team_id}", response_model=TeamOut)
async def get_team(org_id: UUID, team_id: UUID, p: CurrentPrincipal, db: DbSession):
    svc = TenancyService(db)
    team = await svc.get_team(p, org_id, team_id)
    count = (await svc.teams.member_counts([team.id])).get(team.id, 0)
    return TeamOut.model_validate(team).model_copy(update={"member_count": count})


@router.patch("/{org_id}/teams/{team_id}", response_model=TeamOut)
async def update_team(org_id: UUID, team_id: UUID, data: TeamUpdate, p: CurrentPrincipal, db: DbSession):
    return await TenancyService(db).update_team(p, org_id, team_id, data)


@router.delete("/{org_id}/teams/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(org_id: UUID, team_id: UUID, p: CurrentPrincipal, db: DbSession) -> Response:
    await TenancyService(db).delete_team(p, org_id, team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- teamleden


@router.get("/{org_id}/teams/{team_id}/members", response_model=list[MemberOut])
async def list_team_members(org_id: UUID, team_id: UUID, p: CurrentPrincipal, db: DbSession):
    return await TenancyService(db).list_members(p, org_id, team_id)


@router.post(
    "/{org_id}/teams/{team_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
async def add_team_member(
    org_id: UUID, team_id: UUID, data: TeamMemberCreate, p: CurrentPrincipal, db: DbSession
):
    return await TenancyService(db).add_member(p, org_id, team_id, data.user_id, data.role)


@router.patch("/{org_id}/teams/{team_id}/members/{membership_id}", response_model=MemberOut)
async def update_team_member(
    org_id: UUID,
    team_id: UUID,
    membership_id: UUID,
    data: TeamMemberUpdate,
    p: CurrentPrincipal,
    db: DbSession,
):
    return await TenancyService(db).update_member(p, org_id, team_id, membership_id, data.role)


@router.delete("/{org_id}/teams/{team_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_team_member(
    org_id: UUID, team_id: UUID, membership_id: UUID, p: CurrentPrincipal, db: DbSession
) -> Response:
    await TenancyService(db).remove_member(p, org_id, team_id, membership_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
