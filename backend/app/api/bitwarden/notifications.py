"""Notificatie-hub voor live synchronisatie (fase 9), zie app/services/vault_push.py.

De apps verbinden rechtstreeks met een WebSocket (SignalR met skipNegotiation) op
/notifications/hub?access_token=<token>. Het token is hetzelfde access token als voor
de API; daarna houdt de verbinding geen databaseverbinding vast.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.api.bitwarden.deps import vault_enabled
from app.api.deps import SettingsDep
from app.core.config import get_settings
from app.core.context import RequestMeta
from app.core.db import get_sessionmaker
from app.services.vault_auth import VaultAuthError, VaultAuthService
from app.services.vault_push import HANDSHAKE_SECONDS, Connection, NotificationHub, parse_handshake

router = APIRouter(prefix="/notifications", tags=["bitwarden-api"])


def _token(ws: WebSocket) -> str:
    authz = ws.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    return ws.query_params.get("access_token", "")


@router.post("/hub/negotiate", summary="SignalR-negotiatie (enkel WebSockets)")
async def negotiate(settings: SettingsDep) -> dict[str, Any]:
    vault_enabled(settings)
    return {
        "connectionId": secrets.token_urlsafe(16),
        "availableTransports": [{"transport": "WebSockets", "transferFormats": ["Text", "Binary"]}],
    }


@router.websocket("/hub")
async def hub(ws: WebSocket) -> None:
    settings = get_settings()
    hub: NotificationHub | None = getattr(ws.app.state, "vault_hub", None)
    if not settings.vault_enabled or hub is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    meta = RequestMeta(
        ip_address=ws.client.host if ws.client else None, user_agent=ws.headers.get("user-agent")
    )
    try:
        async with get_sessionmaker()() as db:
            caller = await VaultAuthService(db, settings).authenticate(_token(ws), meta)
    except VaultAuthError:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive(), HANDSHAKE_SECONDS)
        protocol = parse_handshake(first.get("bytes") or first.get("text") or "")
    except (TimeoutError, ValueError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await ws.send_text('{"error":"Alleen SignalR met messagepack of json, versie 1"}\x1e')
            await ws.close(code=status.WS_1002_PROTOCOL_ERROR)
        return

    async def send(frame: bytes | str) -> None:
        if isinstance(frame, bytes):
            await ws.send_bytes(frame)
        else:
            await ws.send_text(frame)

    if protocol.binary:
        await ws.send_bytes(b"{}\x1e")
    else:
        await ws.send_text("{}\x1e")
    conn = Connection(caller.account.user_id, caller.device.id, protocol, send)
    hub.add(conn)
    pump = asyncio.create_task(conn.pump())
    try:
        while not pump.done():
            receive = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait({receive, pump}, return_when=asyncio.FIRST_COMPLETED)
            if receive not in done:
                receive.cancel()
                break
            message = receive.result()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes") or message.get("text") or ""
            if protocol.is_close(data):
                break
            # Pings en andere berichten van de client hebben geen antwoord nodig.
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.remove(conn)
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump
        with contextlib.suppress(Exception):
            await ws.close()
