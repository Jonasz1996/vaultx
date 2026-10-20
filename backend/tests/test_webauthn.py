"""Beveiligingssleutels en passkeys (WebAuthn/FIDO2) als tweede stap: kluis en wachtwoordlogin."""

from __future__ import annotations

import json
import time

import pytest

from tests.soft_webauthn import SoftKey
from tests.test_password_login import PW, create, pw_login
from tests.test_vault import enroll, token

VAULT = "/api/v1/me/vault"


async def add_vault_key(c, account, key: SoftKey, name: str = "YubiKey 5C") -> dict:
    r = await c.post_json(
        f"{VAULT}/2fa/security-keys/options", {"master_password_hash": account.password_hash}
    )
    assert r.status_code == 200, r.text
    options = r.json()["options"]
    assert options["rp"] == {"name": "VaultX", "id": "testserver"}
    assert options["authenticatorSelection"]["residentKey"] == "discouraged"
    r = await c.post_json(f"{VAULT}/2fa/security-keys", {"name": name, "credential": key.create(options)})
    assert r.status_code == 201, r.text
    return r.json()


async def test_vault_security_key_with_bitwarden_client(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    status = (await c.get(VAULT)).json()
    assert status["security_keys_available"] is True and status["security_keys_rp_id"] == "testserver"
    assert status["two_factor_enabled"] is False

    # Zonder master password geen registratie
    r = await c.post_json(f"{VAULT}/2fa/security-keys/options", {"master_password_hash": "x" * 44})
    assert r.status_code == 422

    key = SoftKey()
    added = await add_vault_key(c, account, key)
    assert added["key"]["name"] == "YubiKey 5C" and added["recovery_code"]
    status = (await c.get(VAULT)).json()
    assert status["two_factor_enabled"] is True and status["authenticator_enabled"] is False
    assert [k["name"] for k in status["security_keys"]] == ["YubiKey 5C"]

    # Dezelfde sleutel een tweede keer: geweigerd (excludeCredentials en de server zelf)
    r = await c.post_json(
        f"{VAULT}/2fa/security-keys/options", {"master_password_hash": account.password_hash}
    )
    options = r.json()["options"]
    assert [x["id"] for x in options["excludeCredentials"]] == [key.create(options)["id"]]
    r = await c.post_json(
        f"{VAULT}/2fa/security-keys", {"name": "nog eens", "credential": key.create(options)}
    )
    assert r.status_code == 422

    # Bitwarden-client zonder tweede stap: provider 7 met de opties voor de connectorpagina
    r = await token(c, account.email, account.password_hash)
    assert r.status_code == 400
    body = r.json()
    assert body["TwoFactorProviders"] == [7]
    options = body["TwoFactorProviders2"]["7"]
    assert options["rpId"] == "testserver" and options["userVerification"] == "discouraged"
    assert len(options["allowCredentials"]) == 1

    # Antwoord zoals de Bitwarden-connector het als twoFactorToken doorgeeft
    answer = json.dumps(key.get(options, bitwarden=True))
    r = await token(
        c,
        account.email,
        account.password_hash,
        twoFactorToken=answer,
        twoFactorProvider="7",
        twoFactorRemember="1",
    )
    assert r.status_code == 200, r.text
    assert r.json()["TwoFactorToken"]

    # Hetzelfde antwoord nog eens (replay): geweigerd
    r = await token(
        c, account.email, account.password_hash, "dev-2", twoFactorToken=answer, twoFactorProvider="7"
    )
    assert r.status_code == 400 and "TwoFactorProviders2" not in r.json()

    # Een antwoord voor een ander adres (phishing-site) werkt niet
    r = await token(c, account.email, account.password_hash, "dev-3")
    options = r.json()["TwoFactorProviders2"]["7"]
    evil = json.dumps(key.get(options, origin="https://evil.example.com"))
    r = await token(
        c, account.email, account.password_hash, "dev-3", twoFactorToken=evil, twoFactorProvider="7"
    )
    assert r.status_code == 400

    # Een challenge van een andere gebruiker of een vervalste werkt niet
    r = await token(c, account.email, account.password_hash, "dev-4")
    options = r.json()["TwoFactorProviders2"]["7"]
    forged = dict(options, challenge=options["challenge"][:-4] + "AAAA")
    r = await token(
        c,
        account.email,
        account.password_hash,
        "dev-4",
        twoFactorToken=json.dumps(key.get(forged)),
        twoFactorProvider="7",
    )
    assert r.status_code == 400

    # Ook de webkluis (client_id web) krijgt dezelfde vraag; met de sleutel kom je binnen
    r = await token(c, account.email, account.password_hash, "dev-5", client_id="web")
    options = r.json()["TwoFactorProviders2"]["7"]
    r = await token(
        c,
        account.email,
        account.password_hash,
        "dev-5",
        client_id="web",
        twoFactorToken=json.dumps(key.get(options)),
        twoFactorProvider="7",
    )
    assert r.status_code == 200, r.text


async def test_app_and_key_together_and_removal_needs_proof(make_client):
    from tests.test_bitwarden_parity import _enable_2fa

    c = await make_client()
    account = await enroll(c, "jonas")
    key = SoftKey()
    first = await add_vault_key(c, account, key)
    # De authenticator-app erbij: geen nieuwe herstelcode, de eerste blijft gelden
    secret, recovery = await _enable_2fa(c, account)
    assert recovery is None
    r = await token(c, account.email, account.password_hash)
    assert r.json()["TwoFactorProviders"] == [0, 7]

    status = (await c.get(VAULT)).json()
    key_id = status["security_keys"][0]["id"]
    # Weghalen met enkel het master password: geweigerd
    r = await c.post_json(
        f"{VAULT}/2fa/security-keys/{key_id}/remove", {"master_password_hash": account.password_hash}
    )
    assert r.status_code == 422
    # Met de sleutel zelf als bewijs
    options = (await c.post_json(f"{VAULT}/2fa/challenge", {})).json()["options"]
    body = {"master_password_hash": account.password_hash, "security_key": key.get(options)}
    r = await c.post_json(f"{VAULT}/2fa/security-keys/{key_id}/remove", body)
    assert r.status_code == 204, r.text
    status = (await c.get(VAULT)).json()
    assert status["security_keys"] == [] and status["two_factor_enabled"] is True

    # App weghalen met de herstelcode: nu is er niets meer, dus tweestapsverificatie uit
    body = {"master_password_hash": account.password_hash, "code": first["recovery_code"]}
    r = await c.post_json(f"{VAULT}/2fa/authenticator/remove", body)
    assert r.status_code == 204, r.text
    status = (await c.get(VAULT)).json()
    assert status["two_factor_enabled"] is False and status["authenticator_enabled"] is False
    assert (await token(c, account.email, account.password_hash, "dev-9")).status_code == 200
    assert secret


async def test_recovery_code_and_vault_reset_remove_keys(make_client, admin):
    from sqlalchemy import func, select

    from app.core.db import get_sessionmaker
    from app.models import WebAuthnCredential

    async def count() -> int:
        async with get_sessionmaker()() as db:
            return await db.scalar(select(func.count()).select_from(WebAuthnCredential))

    c = await make_client()
    account = await enroll(c, "jonas")
    added = await add_vault_key(c, account, SoftKey())
    r = await token(
        c, account.email, account.password_hash, twoFactorToken=added["recovery_code"], twoFactorProvider="8"
    )
    assert r.status_code == 200, r.text
    assert await count() == 0

    await add_vault_key(c, account, SoftKey())
    me = (await c.get("/api/v1/me")).json()
    r = await admin.delete(f"/api/v1/users/{me['id']}/vault-2fa", headers=admin.mutating_headers())
    assert r.status_code == 204
    assert await count() == 0

    await add_vault_key(c, account, SoftKey())
    r = await c.post_json(f"{VAULT}/reset", {"master_password_hash": account.password_hash})
    assert r.status_code == 204, r.text
    assert await count() == 0


async def test_wrong_key_answers_count_towards_lockout(make_client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "vault_max_failed_logins", 2)
    c = await make_client()
    account = await enroll(c, "jonas")
    key = SoftKey()
    await add_vault_key(c, account, key)
    stranger = SoftKey()
    stranger.credential_id = key.credential_id  # zelfde id, andere sleutel: handtekening klopt niet
    for _ in range(2):
        options = (await token(c, account.email, account.password_hash)).json()["TwoFactorProviders2"]["7"]
        bad = json.dumps(stranger.get(options))
        r = await token(c, account.email, account.password_hash, twoFactorToken=bad, twoFactorProvider="7")
        assert r.status_code == 400
    r = await token(c, account.email, account.password_hash)
    assert "TwoFactorProviders2" not in r.json()  # nu vergrendeld


async def test_no_security_keys_on_an_ip_address(make_client, monkeypatch):
    from app.core.config import get_settings

    c = await make_client()
    account = await enroll(c, "jonas")
    monkeypatch.setattr(get_settings(), "public_url", "https://192.168.10.50")
    assert (await c.get(VAULT)).json()["security_keys_available"] is False
    r = await c.post_json(
        f"{VAULT}/2fa/security-keys/options", {"master_password_hash": account.password_hash}
    )
    assert r.status_code == 422 and "domeinnaam" in r.text


def test_challenge_is_bound_to_user_purpose_and_time(monkeypatch):
    import uuid

    from app.core.config import get_settings
    from app.services import webauthn

    settings = get_settings()
    user = uuid.uuid4()
    ch = webauthn.new_challenge(settings, user, "assert:vault")
    assert webauthn.check_challenge(settings, user, "assert:vault", ch) > 0
    with pytest.raises(webauthn.WebAuthnError):
        webauthn.check_challenge(settings, uuid.uuid4(), "assert:vault", ch)
    with pytest.raises(webauthn.WebAuthnError):
        webauthn.check_challenge(settings, user, "assert:login", ch)
    later = time.time() + webauthn.CHALLENGE_SECONDS + 5
    monkeypatch.setattr(webauthn.time, "time", lambda: later)
    with pytest.raises(webauthn.WebAuthnError):
        webauthn.check_challenge(settings, user, "assert:vault", ch)
    # Oorsprongen: enkel adressen op de domeinnaam
    monkeypatch.setattr(settings, "public_url", "https://vaultx.jbogaert.be")
    monkeypatch.setattr(
        settings, "extra_origins", ["https://192.168.10.50", "https://vaultx.jbogaert.be:8443"]
    )
    assert webauthn.rp_id(settings) == "vaultx.jbogaert.be"
    assert webauthn.expected_origins(settings, "vaultx.jbogaert.be") == [
        "https://vaultx.jbogaert.be",
        "https://vaultx.jbogaert.be:8443",
    ]


# --- Wachtwoordlogin -----------------------------------------------------------------


async def add_login_key(c, key: SoftKey, password: str = PW) -> dict:
    r = await c.post_json("/api/v1/me/security-keys/options", {"password": password})
    assert r.status_code == 200, r.text
    r = await c.post_json(
        "/api/v1/me/security-keys", {"name": "YubiKey", "credential": key.create(r.json()["options"])}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_password_login_with_security_key(admin, make_client):
    await create(admin)
    c = await make_client()
    assert (await pw_login(c, "carol")).status_code == 200
    assert (await c.get("/api/v1/me/security-keys")).json()["has_password"] is True
    # Fout wachtwoord bij het koppelen
    assert (await c.post_json("/api/v1/me/security-keys/options", {"password": "fout"})).status_code == 422
    key = SoftKey()
    added = await add_login_key(c, key)
    assert added["recovery_code"]

    # Nieuwe login: na het wachtwoord nog geen sessie, maar een tweede stap
    c2 = await make_client()
    r = await pw_login(c2, "carol", next="/vault")
    assert r.status_code == 200, r.text
    step = r.json()
    assert step["second_step"] == "security_key" and "redirect_url" not in step
    assert (await c2.get("/api/v1/me")).status_code == 401

    # Fout antwoord, verlopen of vervalst ticket
    bad = {"ticket": step["ticket"] + "x", "security_key": key.get(step["security_key_options"])}
    assert (await c2.post_json("/auth/password/second-step", bad)).status_code == 401
    r = await pw_login(c2, "carol")
    step = r.json()
    ok = {"ticket": step["ticket"], "security_key": key.get(step["security_key_options"]), "next": "/vault"}
    r = await c2.post_json("/auth/password/second-step", ok)
    assert r.status_code == 200, r.text
    assert r.json()["redirect_url"] == "/vault"
    assert (await c2.get("/api/v1/me")).json()["username"] == "carol"
    # Zelfde antwoord nog eens: geweigerd
    c3 = await make_client()
    assert (await c3.post_json("/auth/password/second-step", ok)).status_code == 401

    # Herstelcode: binnen, en de sleutels zijn weg
    c4 = await make_client()
    step = (await pw_login(c4, "carol")).json()
    body = {"ticket": step["ticket"], "recovery_code": added["recovery_code"].lower()}
    assert (await c4.post_json("/auth/password/second-step", body)).status_code == 200
    assert (await c4.get("/api/v1/me/security-keys")).json()["keys"] == []
    c5 = await make_client()
    assert "redirect_url" in (await pw_login(c5, "carol")).json()


async def test_login_key_removal_and_cli_reset(admin, make_client):
    from app import cli
    from app.core.errors import VaultXError

    await create(admin)
    c = await make_client()
    await pw_login(c, "carol")
    key = SoftKey()
    await add_login_key(c, key)
    key_id = (await c.get("/api/v1/me/security-keys")).json()["keys"][0]["id"]
    r = await c.post_json(f"/api/v1/me/security-keys/{key_id}/remove", {"password": PW})
    assert r.status_code == 422
    options = (await c.post_json("/api/v1/me/security-keys/challenge", {})).json()["options"]
    r = await c.post_json(
        f"/api/v1/me/security-keys/{key_id}/remove", {"password": PW, "security_key": key.get(options)}
    )
    assert r.status_code == 204, r.text

    await add_login_key(c, SoftKey())
    assert await cli.reset_login_two_factor("carol") == "carol"
    c2 = await make_client()
    assert "redirect_url" in (await pw_login(c2, "carol")).json()
    with pytest.raises(VaultXError):
        await cli.reset_login_two_factor("carol")


async def test_authentik_account_without_password_cannot_add_login_key(make_client):
    c = await make_client()
    await c.login("jonas")
    assert (await c.get("/api/v1/me/security-keys")).json()["has_password"] is False
    r = await c.post_json("/api/v1/me/security-keys/options", {"password": "wat-dan-ook"})
    assert r.status_code == 422
