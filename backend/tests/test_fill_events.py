"""VaultX Invuller meldt wat ze invulde (id en host); Mijn kluis en /api/v1/stats tonen het."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from tests.test_vault import bearer, enroll, login_item


async def _item(c, account, bw, name: str) -> str:
    r = await c.post("/api/ciphers", json=login_item(account, name, "jonas", "geheim"), headers=bw)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_fill_events_insights_and_stats(make_client):
    c = await make_client()
    account = await enroll(c, "jonas")
    bw = await bearer(c, account)
    grafana = await _item(c, account, bw, "Grafana")
    proxmox = await _item(c, account, bw, "Proxmox")

    # Iemand anders: zijn items tellen niet mee bij jonas, en jonas kan er niets voor melden.
    other_c = await make_client()
    other = await enroll(other_c, "ander")
    other_bw = await bearer(other_c, other)
    foreign = await _item(other_c, other, other_bw, "Ander")

    old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    events = [
        {"item_id": grafana, "host": "grafana.example.com", "kind": "fill"},
        {"item_id": grafana, "host": "grafana.example.com", "kind": "fill"},
        {"item_id": proxmox, "host": "pve.lan:8006", "kind": "fill"},
        {"item_id": proxmox, "host": "pve.lan:8006", "kind": "failed"},
        {"item_id": foreign, "host": "x.example.com", "kind": "fill"},  # niet van jonas
        {"item_id": str(uuid.uuid4()), "host": "x.example.com", "kind": "fill"},  # bestaat niet
        {"item_id": grafana, "host": "grafana.example.com", "kind": "fill", "at": old},  # te oud
    ]
    r = await c.post("/api/vaultx/fill-events", json={"events": events}, headers=bw)
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": 4}

    # Enkel met een kluis-token (de extensie), niet zonder.
    anon = await make_client()
    r = await anon.post("/api/vaultx/fill-events", json={"events": events[:1]})
    assert r.status_code == 401

    # Geen URL, pad of inhoud: enkel een host.
    for bad in ("https://grafana.example.com/login", "grafana.example.com/login?x=1", "a b", ""):
        r = await c.post(
            "/api/vaultx/fill-events",
            json={"events": [{"item_id": grafana, "host": bad, "kind": "fill"}]},
            headers=bw,
        )
        assert r.status_code == 400, bad
    r = await c.post(
        "/api/vaultx/fill-events",
        json={"events": [{"item_id": grafana, "host": "a.b", "kind": "password"}]},
        headers=bw,
    )
    assert r.status_code == 400

    r = await c.get("/api/v1/vault/fill-insights")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["days"] == 30 and d["fills"] == 3 and d["failures"] == 1
    # Mislukte logins eerst.
    assert [(i["item_id"], i["host"], i["fills"], i["failures"]) for i in d["items"]] == [
        (proxmox, "pve.lan:8006", 1, 1),
        (grafana, "grafana.example.com", 2, 0),
    ]
    assert d["items"][0]["last_failure_at"] and d["items"][1]["last_failure_at"] is None

    # De ander ziet niets van jonas.
    r = await other_c.get("/api/v1/vault/fill-insights")
    assert r.json()["items"] == []

    r = await c.get("/api/v1/stats")
    assert r.status_code == 200, r.text
    assert r.json()["invuller"] == {
        "devices_with_authentik_unlock": 0,
        "users_with_authentik_unlock": 0,
        "fills_last_24h": 3,
        "failed_logins_last_24h": 1,
    }
