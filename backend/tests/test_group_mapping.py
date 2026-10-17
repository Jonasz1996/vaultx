from app.services.group_mapping import parse_groups


def test_parse_org_and_team_groups():
    d = parse_groups(
        [
            "vaultx:acme",
            "vaultx:acme:admin",
            "vaultx:acme/ops",
            "vaultx:acme/dev:maintainer",
            "vaultx:beta/ops",
            "authentik Admins",
            "vaultx:Bad Slug",
            "vaultx:acme:superuser",
        ],
        "vaultx:",
    )
    assert d.orgs == {"acme": "admin", "beta": "member"}
    assert d.teams == {("acme", "ops"): "member", ("acme", "dev"): "maintainer", ("beta", "ops"): "member"}
    assert d.invalid == ["vaultx:Bad Slug", "vaultx:acme:superuser"]


def test_highest_role_wins_regardless_of_order():
    d = parse_groups(
        ["vaultx:acme:owner", "vaultx:acme", "vaultx:acme/x:maintainer", "vaultx:acme/x"], "vaultx:"
    )
    assert d.orgs == {"acme": "owner"}
    assert d.teams == {("acme", "x"): "maintainer"}
