"""Live synchronisatie voor de Bitwarden-apps (fase 9): de notificatie-hub.

De desktop-app, de browserextensie en de webkluis houden een WebSocket open naar
/notifications/hub (ASP.NET SignalR, MessagePack-protocol). Verandert er iets in de
kluis, dan krijgen de andere apparaten meteen een korte melding: welk item of welke
map en de revisiedatum, nooit inhoud. De app haalt het item daarna zelf op met zijn
eigen token, of doet een sync.

Meldingen gaan via PostgreSQL NOTIFY in dezelfde transactie als de wijziging: ze
vertrekken pas na de commit (en nooit bij een rollback), en elke VaultX-instantie
die LISTEN doet krijgt ze, ook met meerdere workers of meerdere nodes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any
from uuid import UUID

import msgpack
import psycopg
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

CHANNEL = "vaultx_vault_push"
RECORD_SEPARATOR = "\x1e"
PING_SECONDS = 15.0
HANDSHAKE_SECONDS = 10.0
QUEUE_SIZE = 100
MAX_PER_USER = 20


class PushType(IntEnum):
    """Meldingstypes zoals de Bitwarden-clients ze kennen (NotificationType)."""

    SYNC_CIPHER_UPDATE = 0
    SYNC_CIPHER_CREATE = 1
    SYNC_LOGIN_DELETE = 2
    SYNC_FOLDER_DELETE = 3
    SYNC_CIPHERS = 4
    SYNC_VAULT = 5
    SYNC_FOLDER_CREATE = 7
    SYNC_FOLDER_UPDATE = 8
    SYNC_SETTINGS = 10
    LOG_OUT = 11


CIPHER_TYPES = {PushType.SYNC_CIPHER_UPDATE, PushType.SYNC_CIPHER_CREATE, PushType.SYNC_LOGIN_DELETE}
FOLDER_TYPES = {PushType.SYNC_FOLDER_DELETE, PushType.SYNC_FOLDER_CREATE, PushType.SYNC_FOLDER_UPDATE}


async def publish(
    db: AsyncSession,
    user_id: UUID,
    type_: PushType,
    *,
    item_id: UUID | None = None,
    revision: datetime | None = None,
    context: str | None = None,
    device_id: UUID | None = None,
) -> None:
    """Zet een melding klaar; PostgreSQL verstuurt ze bij de commit van deze transactie.

    `context` is de identifier van het apparaat dat de wijziging deed: dat apparaat
    negeert de melding. `device_id` beperkt de melding tot één apparaat (afmelden).
    """
    msg = {
        "u": str(user_id),
        "t": int(type_),
        "i": str(item_id) if item_id else None,
        "r": (revision or datetime.now(UTC)).isoformat(),
        "c": context,
        "d": str(device_id) if device_id else None,
    }
    await db.execute(text("SELECT pg_notify(:channel, :msg)"), {"channel": CHANNEL, "msg": json.dumps(msg)})


# --- SignalR-protocol -------------------------------------------------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_frames(data: bytes) -> list[Any]:
    """Splitst een binair SignalR-bericht (lengte als varint + MessagePack) in berichten."""
    messages: list[Any] = []
    pos = 0
    while pos < len(data):
        length, shift = 0, 0
        while True:
            if pos >= len(data) or shift > 28:
                raise ValueError("ongeldige lengte")
            byte = data[pos]
            pos += 1
            length |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        if pos + length > len(data):
            raise ValueError("onvolledig bericht")
        messages.append(msgpack.unpackb(data[pos : pos + length], raw=False, timestamp=3))
        pos += length
    return messages


@dataclass(slots=True, frozen=True)
class Protocol:
    name: str

    @property
    def binary(self) -> bool:
        return self.name == "messagepack"

    def encode(self, message: list[Any], as_json: dict[str, Any]) -> bytes | str:
        if self.binary:
            body = msgpack.packb(message, datetime=True)
            return _varint(len(body)) + body
        return json.dumps(as_json, default=_json_default) + RECORD_SEPARATOR

    def ping(self) -> bytes | str:
        return self.encode([6], {"type": 6})

    def close(self) -> bytes | str:
        return self.encode([7, None, False], {"type": 7, "allowReconnect": False})

    def invocation(self, update: dict[str, Any]) -> bytes | str:
        return self.encode(
            [1, {}, None, "ReceiveMessage", [update]],
            {"type": 1, "target": "ReceiveMessage", "arguments": [update]},
        )

    def is_close(self, data: bytes | str) -> bool:
        try:
            if isinstance(data, bytes):
                return any(isinstance(m, list) and m and m[0] == 7 for m in read_frames(data))
            return any(
                json.loads(part).get("type") == 7 for part in data.split(RECORD_SEPARATOR) if part.strip()
            )
        except (ValueError, AttributeError, msgpack.ExtraData, msgpack.FormatError, msgpack.StackError):
            return False


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(type(value).__name__)


def parse_handshake(data: bytes | str) -> Protocol:
    """`{"protocol":"messagepack","version":1}` gevolgd door het scheidingsteken 0x1e."""
    raw = data.decode() if isinstance(data, bytes) else data
    if RECORD_SEPARATOR not in raw:
        raise ValueError("handshake zonder scheidingsteken")
    request = json.loads(raw.split(RECORD_SEPARATOR, 1)[0])
    name = request.get("protocol")
    if name not in ("messagepack", "json") or request.get("version") != 1:
        raise ValueError(f"protocol {name!r} versie {request.get('version')!r} niet ondersteund")
    return Protocol(name)


def update_json(msg: dict[str, Any]) -> dict[str, Any]:
    """Melding uit de database naar het formaat van de Bitwarden-server (PushNotificationData)."""
    type_ = PushType(msg["t"])
    when = datetime.fromisoformat(msg["r"])
    payload: dict[str, Any]
    if type_ in CIPHER_TYPES:
        payload = {
            "Id": msg["i"],
            "UserId": msg["u"],
            "OrganizationId": None,
            "CollectionIds": None,
            "RevisionDate": when,
        }
    elif type_ in FOLDER_TYPES:
        payload = {"Id": msg["i"], "UserId": msg["u"], "RevisionDate": when}
    else:
        payload = {"UserId": msg["u"], "Date": when}
    return {"ContextId": msg["c"], "Type": int(type_), "Payload": payload}


# --- Verbindingen -----------------------------------------------------------------------


@dataclass(eq=False)
class Connection:
    """Eén open WebSocket van één apparaat. `send` en `close` komen van de WebSocket-route."""

    user_id: UUID
    device_id: UUID
    protocol: Protocol
    send: Callable[[bytes | str], Awaitable[None]]
    queue: asyncio.Queue[bytes | str | None] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))

    def offer(self, frame: bytes | str | None) -> None:
        try:
            self.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Een client die niet meer leest, krijgt geen meldingen meer: sluiten. Bij het
            # opnieuw verbinden doet de app zelf een sync.
            with contextlib.suppress(asyncio.QueueEmpty):
                while True:
                    self.queue.get_nowait()
            self.queue.put_nowait(None)

    async def pump(self) -> None:
        """Stuurt meldingen door en pingt elke 15 s (anders verbreekt SignalR na 30 s)."""
        while True:
            try:
                frame = await asyncio.wait_for(self.queue.get(), PING_SECONDS)
            except TimeoutError:
                frame = self.protocol.ping()
            if frame is None:
                with contextlib.suppress(Exception):
                    await self.send(self.protocol.close())
                return
            await self.send(frame)


class NotificationHub:
    """Houdt de open verbindingen per gebruiker bij en luistert op PostgreSQL."""

    def __init__(self, database_url: str) -> None:
        self._conninfo = (
            make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        )
        self._conns: dict[UUID, list[Connection]] = {}
        self._task: asyncio.Task[None] | None = None
        self.listening = asyncio.Event()

    @property
    def connection_count(self) -> int:
        return sum(len(v) for v in self._conns.values())

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._listen(), name="vault-push-listener")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for conns in list(self._conns.values()):
            for conn in conns:
                conn.offer(None)

    def add(self, conn: Connection) -> None:
        conns = self._conns.setdefault(conn.user_id, [])
        conns.append(conn)
        while len(conns) > MAX_PER_USER:
            conns.pop(0).offer(None)

    def remove(self, conn: Connection) -> None:
        conns = self._conns.get(conn.user_id, [])
        if conn in conns:
            conns.remove(conn)
        if not conns:
            self._conns.pop(conn.user_id, None)

    def dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
            user_id = UUID(msg["u"])
            update = update_json(msg)
        except (ValueError, KeyError, TypeError):
            log.warning("Ongeldige kluismelding genegeerd")
            return
        device = msg.get("d")
        for conn in list(self._conns.get(user_id, [])):
            if device and str(conn.device_id) != device:
                continue
            conn.offer(conn.protocol.invocation(update))
            if update["Type"] == PushType.LOG_OUT:
                conn.offer(None)

    def _resync_all(self) -> None:
        """Na een onderbroken LISTEN kunnen meldingen gemist zijn: laat elke app syncen."""
        now = datetime.now(UTC).isoformat()
        for user_id in list(self._conns):
            self.dispatch(json.dumps({"u": str(user_id), "t": int(PushType.SYNC_VAULT), "r": now, "c": None}))

    async def _listen(self) -> None:
        delay = 1.0
        first = True
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True) as db:
                    await db.execute(f"LISTEN {CHANNEL}")
                    if not first:
                        self._resync_all()
                    first, delay = False, 1.0
                    self.listening.set()
                    async for notify in db.notifies():
                        self.dispatch(notify.payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - de hub moet blijven proberen
                log.warning("Live sync: PostgreSQL-verbinding weg (%s), opnieuw over %.0f s", exc, delay)
            self.listening.clear()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
