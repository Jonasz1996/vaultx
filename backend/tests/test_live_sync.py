"""Fase 9: live synchronisatie (notificatie-hub voor de Bitwarden-apps)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.services.vault_push import (
    Connection,
    NotificationHub,
    Protocol,
    PushType,
    parse_handshake,
    read_frames,
)
from tests.test_vault import bearer, enroll, login_item


def test_handshake_and_frames():
    assert parse_handshake('{"protocol":"messagepack","version":1}\x1e').binary
    assert not parse_handshake(b'{"protocol":"json","version":1}\x1e').binary
    for bad in ('{"protocol":"messagepack","version":1}', '{"protocol":"blazorpack","version":1}\x1e'):
        with pytest.raises(ValueError):
            parse_handshake(bad)

    mp = Protocol("messagepack")
    big = {"ContextId": None, "Type": 5, "Payload": {"UserId": "u", "Blob": "x" * 300}}
    frame = mp.invocation(big)
    # Lengte als varint: meer dan 127 bytes, dus twee bytes
    assert isinstance(frame, bytes) and frame[0] & 0x80
    assert len(frame) == ((frame[0] & 0x7F) | (frame[1] << 7)) + 2
    [message] = read_frames(frame)
    assert (
        message[0] == 1 and message[3] == "ReceiveMessage" and message[4][0]["Payload"]["Blob"] == "x" * 300
    )
    assert read_frames(mp.ping()) == [[6]]
    assert mp.is_close(mp.ping() + mp.close()) and not mp.is_close(mp.ping())
    assert not mp.is_close(b"\xff\xff")

    js = Protocol("json")
    assert json.loads(js.ping().rstrip("\x1e")) == {"type": 6}
    assert js.is_close('{"type":6}\x1e{"type":7}\x1e')


@pytest_asyncio.fixture
async def hub():
    h = NotificationHub(get_settings().database_url)
    h.start()
    await asyncio.wait_for(h.listening.wait(), 10)
    yield h
    await h.stop()


def listen(hub: NotificationHub, user_id, device_id, protocol: str = "messagepack") -> Connection:
    async def send(frame):  # pragma: no cover - de pump draait niet in deze tests
        raise AssertionError

    conn = Connection(user_id, device_id, Protocol(protocol), send)
    hub.add(conn)
    return conn


async def received(conn: Connection, count: int = 1) -> list:
    """De meldingen die bij deze verbinding klaarstaan (None = sluiten)."""
    out = []
    for _ in range(count):
        frame = await asyncio.wait_for(conn.queue.get(), 5)
        if frame is None:
            out.append(None)
            continue
        [message] = read_frames(frame)
        out.append(message[4][0])
    return out


async def nothing(conn: Connection) -> bool:
    await asyncio.sleep(0.3)
    return conn.queue.empty()


async def device_ids(c, headers) -> dict[str, str]:
    return {d["identifier"]: d["id"] for d in (await c.get("/api/devices", headers=headers)).json()["data"]}


async def test_changes_reach_other_devices(make_client, hub):
    from uuid import UUID

    c = await make_client()
    account = await enroll(c, "jonas")
    h1 = await bearer(c, account, "laptop")
    await bearer(c, account, "desktop")
    ids = await device_ids(c, h1)
    user_id = UUID((await c.get("/api/v1/me")).json()["id"])
    desktop = listen(hub, user_id, UUID(ids["desktop"]))

    # Iemand anders hoort niets van deze kluis
    other = await enroll(await make_client(), "piet")
    assert other
    stranger = listen(hub, UUID(int=1), UUID(int=2))

    r = await c.post("/api/ciphers", json=login_item(account, "Grafana", "admin", "pw"), headers=h1)
    assert r.status_code == 200
    item = r.json()
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_CIPHER_CREATE
    assert update["ContextId"] == "laptop"  # de laptop negeert zijn eigen melding
    payload = update["Payload"]
    assert payload["Id"] == item["id"] and payload["UserId"] == str(user_id)
    # MessagePack-timestamp (ext -1): de clients maken er een Date van
    assert isinstance(payload["RevisionDate"], datetime)
    assert payload["RevisionDate"].isoformat().startswith(item["revisionDate"][:19])

    body = {**login_item(account, "Grafana", "root", "pw2"), "lastKnownRevisionDate": item["revisionDate"]}
    assert (await c.put(f"/api/ciphers/{item['id']}", json=body, headers=h1)).status_code == 200
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_CIPHER_UPDATE and update["Payload"]["Id"] == item["id"]

    # Een geweigerde wijziging (verouderde kopie) wordt teruggedraaid: geen melding
    stale = {**body, "lastKnownRevisionDate": "2020-01-01T00:00:00Z"}
    assert (await c.put(f"/api/ciphers/{item['id']}", json=stale, headers=h1)).status_code == 400
    assert await nothing(desktop)

    r = await c.post("/api/folders", json={"name": account.enc("Infra")}, headers=h1)
    folder = r.json()
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_FOLDER_CREATE and update["Payload"]["Id"] == folder["id"]
    assert "OrganizationId" not in update["Payload"]

    await c.put(f"/api/ciphers/{item['id']}/delete", headers=h1)
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_CIPHER_UPDATE  # prullenbak = gewijzigd item

    second = (await c.post("/api/ciphers", json=login_item(account, "B", "b", "b"), headers=h1)).json()
    await received(desktop)
    await c.put(
        "/api/ciphers/move", json={"ids": [item["id"], second["id"]], "folderId": folder["id"]}, headers=h1
    )
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_CIPHERS and update["Payload"]["UserId"] == str(user_id)
    assert "Date" in update["Payload"]

    await c.delete(f"/api/ciphers/{second['id']}", headers=h1)
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_LOGIN_DELETE and update["Payload"]["Id"] == second["id"]

    await c.delete(f"/api/folders/{folder['id']}", headers=h1)
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_FOLDER_DELETE

    r = await c.post(
        "/api/ciphers/import", json={"ciphers": [], "folders": [], "folderRelationships": []}, headers=h1
    )
    assert r.status_code == 200
    [update] = await received(desktop)
    assert update["Type"] == PushType.SYNC_VAULT

    assert stranger.queue.empty()


async def test_logout_reaches_the_right_devices(admin, make_client, hub):
    from uuid import UUID

    c = await make_client()
    account = await enroll(c, "jonas")
    await bearer(c, account, "laptop")
    h = await bearer(c, account, "desktop")
    ids = await device_ids(c, h)
    user_id = UUID((await c.get("/api/v1/me")).json()["id"])
    laptop = listen(hub, user_id, UUID(ids["laptop"]), "json")
    desktop = listen(hub, user_id, UUID(ids["desktop"]))

    # Eén apparaat afmelden in VaultX: enkel dat apparaat krijgt LogOut en wordt gesloten
    r = await c.post_json(f"/api/v1/me/vault/devices/{ids['laptop']}/revoke", {})
    assert r.status_code == 200
    frame = await asyncio.wait_for(laptop.queue.get(), 5)
    message = json.loads(frame.rstrip("\x1e"))
    assert message["type"] == 1 and message["target"] == "ReceiveMessage"
    assert (
        message["arguments"][0]["Type"] == PushType.LOG_OUT and message["arguments"][0]["ContextId"] is None
    )
    assert await laptop.queue.get() is None
    assert await nothing(desktop)

    # Gebruiker gedeactiveerd: alle apparaten
    me = (await c.get("/api/v1/me")).json()
    assert (await admin.patch_json(f"/api/v1/users/{me['id']}", {"is_active": False})).status_code == 200
    update, closed = await received(desktop, 2)
    assert update["Type"] == PushType.LOG_OUT and closed is None


async def test_vault_reset_logs_out(make_client, hub):
    from uuid import UUID

    c = await make_client()
    account = await enroll(c, "jonas")
    h = await bearer(c, account, "desktop")
    ids = await device_ids(c, h)
    user_id = UUID((await c.get("/api/v1/me")).json()["id"])
    desktop = listen(hub, user_id, UUID(ids["desktop"]))
    assert (await c.delete_("/api/v1/me/vault")).status_code == 204
    update, closed = await received(desktop, 2)
    assert update["Type"] == PushType.LOG_OUT and closed is None


async def test_slow_client_is_closed_and_hub_limits_connections(hub):
    from uuid import UUID

    from app.services import vault_push

    user, device = UUID(int=7), UUID(int=8)
    conns = [listen(hub, user, device) for _ in range(vault_push.MAX_PER_USER + 1)]
    assert hub.connection_count == vault_push.MAX_PER_USER
    assert conns[0].queue.get_nowait() is None  # de oudste is gesloten

    slow = conns[1]
    msg = json.dumps(
        {"u": str(user), "t": int(PushType.SYNC_VAULT), "r": "2026-10-18T00:00:00+00:00", "c": None}
    )
    for _ in range(vault_push.QUEUE_SIZE + 1):
        hub.dispatch(msg)
    assert slow.queue.qsize() == 1 and slow.queue.get_nowait() is None
    hub.dispatch("geen json")  # wordt genegeerd
    for conn in conns:
        hub.remove(conn)
    assert hub.connection_count == 0


async def test_negotiate_and_config(make_client):
    c = await make_client()
    r = await c.post("/notifications/hub/negotiate")
    assert r.status_code == 200
    assert r.json()["availableTransports"][0]["transport"] == "WebSockets"
    config = (await c.get("/api/config")).json()
    assert config["environment"]["notifications"] == "http://testserver/notifications"
    assert config["push"]["pushTechnology"] == 0


def test_access_token_not_logged(app, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" [accepted]',
            "10.0.0.5:4242",
            "/notifications/hub?access_token=eyJh.geheim.sig",
        )
    assert "geheim" not in caplog.text and "access_token=***" in caplog.text
