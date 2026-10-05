async def _me(c):
    return (await c.get("/api/v1/me")).json()


async def _org(admin, slug="acme"):
    r = await admin.post_json("/api/v1/organizations", {"slug": slug, "name": slug.title()})
    assert r.status_code == 201, r.text
    return r.json()


async def test_only_admin_creates_organizations(admin, make_client):
    user = await make_client()
    await user.login("user1")
    r = await user.post_json("/api/v1/organizations", {"slug": "x", "name": "X"})
    assert r.status_code == 403
    org = await _org(admin)
    assert org["slug"] == "acme"
    r = await admin.post_json("/api/v1/organizations", {"slug": "acme", "name": "Dup"})
    assert r.status_code == 409


async def test_invalid_slug_rejected(admin):
    r = await admin.post_json("/api/v1/organizations", {"slug": "Not Valid", "name": "x"})
    assert r.status_code == 422


async def test_members_see_only_their_organizations(admin, make_client):
    acme = await _org(admin, "acme")
    await _org(admin, "beta")
    u = await make_client()
    await u.login("u2")
    uid = (await _me(u))["id"]
    r = await admin.post_json(
        f"/api/v1/organizations/{acme['id']}/members", {"user_id": uid, "role": "member"}
    )
    assert r.status_code == 201
    orgs = (await u.get("/api/v1/organizations")).json()
    assert [o["slug"] for o in orgs["items"]] == ["acme"]
    assert orgs["items"][0]["my_role"] == "member"
    # Andere organisatie bestaat "niet" voor deze gebruiker
    all_orgs = (await admin.get("/api/v1/organizations")).json()["items"]
    beta = next(o for o in all_orgs if o["slug"] == "beta")
    assert (await u.get(f"/api/v1/organizations/{beta['id']}")).status_code == 404
    # Gewoon lid mag niets wijzigen
    r = await u.patch_json(f"/api/v1/organizations/{acme['id']}", {"name": "Hacked"})
    assert r.status_code == 403


async def test_org_admin_manages_teams_and_members(admin, make_client):
    acme = await _org(admin)
    oa = await make_client()
    await oa.login("orgadmin")
    oa_id = (await _me(oa))["id"]
    m = await make_client()
    await m.login("member")
    m_id = (await _me(m))["id"]
    base = f"/api/v1/organizations/{acme['id']}"
    await admin.post_json(f"{base}/members", {"user_id": oa_id, "role": "admin"})

    # Org-admin maakt team, voegt lid toe aan org en team
    team = (await oa.post_json(f"{base}/teams", {"slug": "ops", "name": "Ops"})).json()
    r = await oa.post_json(f"{base}/teams/{team['id']}/members", {"user_id": m_id})
    assert r.status_code == 422  # eerst lid van de organisatie
    await oa.post_json(f"{base}/members", {"user_id": m_id, "role": "member"})
    r = await oa.post_json(f"{base}/teams/{team['id']}/members", {"user_id": m_id, "role": "maintainer"})
    assert r.status_code == 201
    assert r.json()["user"]["id"] == m_id

    # Org-admin kan niemand eigenaar maken
    r = await oa.post_json(f"{base}/members", {"user_id": (await _me(admin))["id"], "role": "owner"})
    assert r.status_code == 403

    # Lid verlaat org -> ook teamlidmaatschap weg
    members = (await oa.get(f"{base}/members")).json()
    mm = next(x for x in members if x["user"]["id"] == m_id)
    assert (await oa.delete_(f"{base}/members/{mm['id']}")).status_code == 204
    assert (await oa.get(f"{base}/teams/{team['id']}/members")).json() == []


async def test_last_owner_cannot_be_removed(admin, make_client):
    acme = await _org(admin)
    o = await make_client()
    await o.login("owner")
    oid = (await _me(o))["id"]
    base = f"/api/v1/organizations/{acme['id']}"
    mem = (await admin.post_json(f"{base}/members", {"user_id": oid, "role": "owner"})).json()
    r = await o.patch_json(f"{base}/members/{mem['id']}", {"role": "member"})
    assert r.status_code == 422
    r = await o.delete_(f"{base}/members/{mem['id']}")
    assert r.status_code == 422


async def test_team_must_belong_to_org_in_url(admin):
    acme = await _org(admin, "acme")
    beta = await _org(admin, "beta")
    team = (
        await admin.post_json(f"/api/v1/organizations/{acme['id']}/teams", {"slug": "t", "name": "T"})
    ).json()
    r = await admin.get(f"/api/v1/organizations/{beta['id']}/teams/{team['id']}")
    assert r.status_code == 404


async def test_idp_group_sync(admin, make_client):
    acme = await _org(admin, "acme")
    base = f"/api/v1/organizations/{acme['id']}"
    team = (await admin.post_json(f"{base}/teams", {"slug": "ops", "name": "Ops"})).json()

    u = await make_client()
    await u.login("synced", groups=["vaultx:acme:admin", "vaultx:acme/ops:maintainer", "vaultx:ghost"])
    me = await _me(u)
    roles = {(x["organization_slug"], x["team_slug"]): (x["role"], x["source"]) for x in me["memberships"]}
    assert roles == {("acme", None): ("admin", "idp"), ("acme", "ops"): ("maintainer", "idp")}

    # IdP-lidmaatschap kan niet manueel gewijzigd worden
    mem = next(x for x in (await admin.get(f"{base}/members")).json() if x["user"]["id"] == me["id"])
    assert (await admin.patch_json(f"{base}/members/{mem['id']}", {"role": "member"})).status_code == 422

    # Groep weg in Authentik -> lidmaatschappen weg bij volgende login
    u2 = await make_client()
    await u2.login("synced", groups=["vaultx:acme"])
    me = await _me(u2)
    roles = {(x["organization_slug"], x["team_slug"]): x["role"] for x in me["memberships"]}
    assert roles == {("acme", None): "member"}
    assert (await admin.get(f"{base}/teams/{team['id']}/members")).json() == []

    # Audit vermeldt onbekende groep
    events = (await admin.get("/api/v1/audit", params={"action": "user.idp_groups_unmatched"})).json()[
        "items"
    ]
    assert events and events[-1]["details"]["unknown"] == ["ghost"]


async def test_manual_memberships_survive_sync(admin, make_client):
    acme = await _org(admin)
    base = f"/api/v1/organizations/{acme['id']}"
    u = await make_client()
    await u.login("manual", groups=[])
    uid = (await _me(u))["id"]
    await admin.post_json(f"{base}/members", {"user_id": uid, "role": "admin"})
    u2 = await make_client()
    await u2.login("manual", groups=[])
    assert [m["role"] for m in (await _me(u2))["memberships"]] == ["admin"]


async def test_delete_organization_cascades(admin):
    acme = await _org(admin)
    base = f"/api/v1/organizations/{acme['id']}"
    await admin.post_json(f"{base}/teams", {"slug": "ops", "name": "Ops"})
    assert (await admin.delete_(base)).status_code == 204
    assert (await admin.get(base)).status_code == 404
    # Audit overleeft de verwijdering
    events = (await admin.get("/api/v1/audit", params={"action": "organization.deleted"})).json()["items"]
    assert len(events) == 2


async def test_user_list_admin_vs_org_admin(admin, make_client):
    acme = await _org(admin)
    oa = await make_client()
    await oa.login("oadm")
    await admin.post_json(
        f"/api/v1/organizations/{acme['id']}/members", {"user_id": (await _me(oa))["id"], "role": "admin"}
    )
    full = (await admin.get("/api/v1/users")).json()["items"]
    assert "idp_groups" in full[0]
    summary = (await oa.get("/api/v1/users", params={"q": "oad"})).json()["items"]
    assert summary and "idp_groups" not in summary[0]
    plain = await make_client()
    await plain.login("plain")
    assert (await plain.get("/api/v1/users")).status_code == 403
