"""Fase 10: links, collecties, tags, zoeken en import (Linkwarden, bladwijzers, database)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg
import pytest

from app.cli import import_linkwarden
from app.core.errors import VaultXError
from tests.conftest import TEST_DB


async def _col(c, name: str, parent: str | None = None) -> dict:
    r = await c.post_json("/api/v1/links/collections", {"name": name, "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()


async def _link(c, url: str, **extra) -> dict:
    r = await c.post_json("/api/v1/links", {"url": url, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_links_crud_search_and_isolation(make_client, admin):
    c = await make_client()
    await c.login("jonas")
    homelab = await _col(c, "Homelab")
    docker = await _col(c, "Docker", homelab["id"])

    grafana = await _link(
        c,
        "https://grafana.com/docs/",
        name="Grafana docs",
        description="Dashboards voor Prometheus",
        collection_id=homelab["id"],
        tags=["monitoring", "#Docs", "monitoring", "  "],
    )
    assert grafana["tags"] == ["monitoring", "Docs"] and grafana["archives"] == []
    await _link(
        c, "https://docs.docker.com/compose/", name="Compose", collection_id=docker["id"], tags=["Docs"]
    )
    await _link(c, "https://example.org/los", name="Los", pinned=True)

    r = await c.post_json("/api/v1/links", {"url": "javascript:alert(1)"})
    assert r.status_code == 422
    r = await c.post_json(
        "/api/v1/links", {"url": "https://x.test", "collection_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}
    )
    assert r.status_code == 404

    async def search(**params) -> list[str]:
        r = await c.get("/api/v1/links", params=params)
        assert r.status_code == 200, r.text
        return [i["name"] for i in r.json()["items"]]

    assert await search(sort="name") == ["Compose", "Grafana docs", "Los"]
    assert await search(q="prometheus") == ["Grafana docs"]
    assert await search(q="docs compose") == ["Compose"]
    assert await search(q="monitor") == ["Grafana docs"]  # via de tag
    assert await search(q="100%") == []
    assert await search(tag="Docs", sort="name") == ["Compose", "Grafana docs"]
    assert await search(collection_id=homelab["id"]) == ["Grafana docs"]
    assert await search(unsorted="true") == ["Los"]
    assert await search(pinned="true") == ["Los"]

    cols = {x["name"]: x for x in (await c.get("/api/v1/links/collections")).json()}
    assert cols["Docker"]["parent_id"] == homelab["id"] and cols["Homelab"]["link_count"] == 1

    # Wijzigen: verplaatsen naar "zonder collectie", tags vervangen
    r = await c.patch_json(f"/api/v1/links/{grafana['id']}", {"collection_id": None, "tags": ["ops"]})
    assert r.status_code == 200 and r.json()["collection_id"] is None and r.json()["tags"] == ["ops"]
    # Weglaten van collection_id verplaatst niets
    r = await c.patch_json(f"/api/v1/links/{grafana['id']}", {"name": "Grafana"})
    assert r.json()["collection_id"] is None and r.json()["name"] == "Grafana"

    # Een andere gebruiker ziet en raakt niets
    other = await make_client()
    await other.login("eve")
    assert (await other.get("/api/v1/links")).json()["total"] == 0
    assert (await other.get(f"/api/v1/links/{grafana['id']}")).status_code == 404
    assert (await other.delete_(f"/api/v1/links/collections/{homelab['id']}")).status_code == 404
    assert (
        await other.post_json("/api/v1/links", {"url": "https://x.test", "collection_id": docker["id"]})
    ).status_code == 404

    # Zonder CSRF-header geweigerd
    assert (await c.post("/api/v1/links", json={"url": "https://x.test"})).status_code == 403

    # Collectie verwijderen haalt subcollecties en hun links mee
    assert (await c.delete_(f"/api/v1/links/collections/{homelab['id']}")).status_code == 204
    assert await search(sort="name") == ["Grafana", "Los"]
    assert (await c.get("/api/v1/links/collections")).json() == []

    actions = {e["action"] for e in (await admin.get("/api/v1/audit", params={"limit": 200})).json()["items"]}
    assert {"links.collection.create", "links.link.create", "links.collection.delete"} <= actions


async def test_collection_cannot_move_into_itself(make_client):
    c = await make_client()
    await c.login("jonas")
    a = await _col(c, "A")
    b = await _col(c, "B", a["id"])
    r = await c.patch_json(f"/api/v1/links/collections/{a['id']}", {"parent_id": b["id"]})
    assert r.status_code == 422
    r = await c.patch_json(f"/api/v1/links/collections/{a['id']}", {"parent_id": a["id"]})
    assert r.status_code == 422
    r = await c.patch_json(f"/api/v1/links/collections/{b['id']}", {"parent_id": None, "color": "#ff0000"})
    assert r.status_code == 200 and r.json()["parent_id"] is None and r.json()["color"] == "#ff0000"


async def test_tag_rename_merges(make_client):
    c = await make_client()
    await c.login("jonas")
    one = await _link(c, "https://a.test", tags=["dev"])
    await _link(c, "https://b.test", tags=["dev", "development"])
    tags = {t["name"]: t for t in (await c.get("/api/v1/links/tags")).json()}
    assert tags["dev"]["link_count"] == 2

    r = await c.patch_json(f"/api/v1/links/tags/{tags['dev']['id']}", {"name": "development"})
    assert r.status_code == 200 and r.json()["id"] == tags["development"]["id"]
    tags = {t["name"]: t for t in (await c.get("/api/v1/links/tags")).json()}
    assert list(tags) == ["development"] and tags["development"]["link_count"] == 2

    r = await c.patch_json(f"/api/v1/links/tags/{tags['development']['id']}", {"name": "Ontwikkeling"})
    assert r.json()["name"] == "Ontwikkeling"
    assert (await c.delete_(f"/api/v1/links/tags/{tags['development']['id']}")).status_code == 204
    assert (await c.get(f"/api/v1/links/{one['id']}")).json()["tags"] == []


LINKWARDEN_EXPORT = {
    "name": "Jonas",
    "username": "jonas",
    "email": "jonas@example.com",
    "collections": [
        {
            "id": 1,
            "name": "Homelab",
            "description": "Alles voor thuis",
            "color": "#22c55e",
            "parentId": None,
            "createdAt": "2024-05-01T10:00:00.000Z",
            "rssSubscriptions": [],
            "links": [
                {
                    "id": 10,
                    "name": "Proxmox",
                    "type": "url",
                    "description": "Hypervisor",
                    "url": "https://www.proxmox.com/",
                    "collectionId": 1,
                    "importDate": "2019-03-01T00:00:00.000Z",
                    "createdAt": "2024-05-01T10:00:00.000Z",
                    "tags": [{"id": 5, "name": "virtualisatie", "ownerId": 1}],
                }
            ],
        },
        {
            "id": 2,
            "name": "Backups",
            "description": "",
            "color": "#0ea5e9",
            "parentId": 1,
            "links": [
                {
                    "id": 11,
                    "name": "Restic",
                    "url": "https://restic.net",
                    "description": "",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "tags": [{"name": "backup"}, {"name": "virtualisatie"}],
                },
                {"id": 12, "name": "Factuur.pdf", "type": "pdf", "url": None, "tags": []},
                {"id": 13, "name": "Slecht", "url": "javascript:alert(1)", "tags": []},
            ],
        },
    ],
    "pinnedLinks": [{"id": 11, "url": "https://restic.net"}],
}


async def test_import_linkwarden_export_twice_is_idempotent(make_client, admin):
    c = await make_client()
    await c.login("jonas")
    body = {"format": "linkwarden", "data": json.dumps(LINKWARDEN_EXPORT)}
    r = await c.post_json("/api/v1/links/import", body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert (res["collections_created"], res["links_created"], res["tags_created"], res["skipped"]) == (
        2,
        2,
        2,
        1,
    )
    assert any("zonder URL" in w for w in res["warnings"])

    cols = {x["name"]: x for x in (await c.get("/api/v1/links/collections")).json()}
    assert cols["Backups"]["parent_id"] == cols["Homelab"]["id"]
    assert cols["Homelab"]["color"] == "#22c55e" and cols["Homelab"]["description"] == "Alles voor thuis"
    links = {i["name"]: i for i in (await c.get("/api/v1/links", params={"sort": "name"})).json()["items"]}
    assert links["Restic"]["pinned"] and not links["Proxmox"]["pinned"]
    assert links["Restic"]["tags"] == ["backup", "virtualisatie"]
    assert (
        links["Proxmox"]["created_at"].startswith("2019-03-01") and links["Proxmox"]["source"] == "linkwarden"
    )

    r = await c.post_json("/api/v1/links/import", body)
    res = r.json()
    assert (res["collections_created"], res["links_created"], res["links_updated"]) == (0, 0, 0)
    assert (await c.get("/api/v1/links")).json()["total"] == 2

    entries = (await admin.get("/api/v1/audit", params={"action": "links.import"})).json()["items"]
    assert len(entries) == 2

    r = await c.post_json("/api/v1/links/import", {"format": "linkwarden", "data": "{nope"})
    assert r.status_code == 422 and "JSON" in r.json()["detail"]


BOOKMARKS = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 ADD_DATE="1700000000" PERSONAL_TOOLBAR_FOLDER="true">Favorietenbalk</H3>
    <DL><p>
        <DT><A HREF="https://vaultx.jbogaert.be/" ADD_DATE="1700000001">VaultX</A>
        <DT><H3>Werk &amp; zo</H3>
        <DL><p>
            <DT><A HREF="https://github.com/" ADD_DATE="1700000002000000" TAGS="code,git">GitHub</A>
            <DD>Waar de code staat
        </DL><p>
    </DL><p>
    <DT><A HREF="https://news.ycombinator.com/">HN</A>
</DL><p>
"""


async def test_import_browser_bookmarks_and_export_roundtrip(make_client):
    c = await make_client()
    await c.login("jonas")
    r = await c.post_json("/api/v1/links/import", {"format": "bookmarks", "data": BOOKMARKS})
    assert r.status_code == 200, r.text
    assert (r.json()["collections_created"], r.json()["links_created"]) == (3, 3)

    cols = {x["name"]: x for x in (await c.get("/api/v1/links/collections")).json()}
    root = cols["Geïmporteerde bladwijzers"]
    assert cols["Favorietenbalk"]["parent_id"] == root["id"]
    assert cols["Werk & zo"]["parent_id"] == cols["Favorietenbalk"]["id"]
    links = {i["name"]: i for i in (await c.get("/api/v1/links")).json()["items"]}
    assert links["GitHub"]["collection_id"] == cols["Werk & zo"]["id"]
    assert (
        links["GitHub"]["tags"] == ["code", "git"] and links["GitHub"]["description"] == "Waar de code staat"
    )
    assert links["GitHub"]["created_at"].startswith("2023-11-14")
    assert links["HN"]["collection_id"] == root["id"]

    # De eigen export importeert een andere gebruiker opnieuw, met dezelfde structuur.
    r = await c.get("/api/v1/links/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    other = await make_client()
    await other.login("carol")
    r = await other.post_json("/api/v1/links/import", {"format": "linkwarden", "data": r.text})
    assert r.status_code == 200, r.text
    assert (r.json()["collections_created"], r.json()["links_created"]) == (3, 3)
    cols2 = {x["name"]: x for x in (await other.get("/api/v1/links/collections")).json()}
    assert cols2["Werk & zo"]["parent_id"] == cols2["Favorietenbalk"]["id"]


# Een nagebootste Linkwarden-database (Prisma-schema van Linkwarden 2.x) in een eigen schema.

LW_SCHEMA = """
DROP SCHEMA IF EXISTS lw_test CASCADE;
CREATE SCHEMA lw_test;
SET search_path = lw_test;
CREATE TABLE "User" (id serial PRIMARY KEY, name text, username text, email text, password text);
CREATE TABLE "Collection" (
    id serial PRIMARY KEY, name text NOT NULL, description text NOT NULL DEFAULT '',
    color text NOT NULL DEFAULT '#0ea5e9', "parentId" int, "isPublic" bool DEFAULT false,
    "ownerId" int NOT NULL, "createdAt" timestamp(3) NOT NULL DEFAULT now()
);
CREATE TABLE "Link" (
    id serial PRIMARY KEY, name text NOT NULL DEFAULT '', type text NOT NULL DEFAULT 'url',
    description text NOT NULL DEFAULT '', "collectionId" int NOT NULL, url text, "textContent" text,
    preview text, image text, pdf text, readable text, monolith text, "importDate" timestamp(3),
    "createdAt" timestamp(3) NOT NULL DEFAULT now()
);
CREATE TABLE "Tag" (id serial PRIMARY KEY, name text NOT NULL, "ownerId" int NOT NULL);
CREATE TABLE "_LinkToTag" ("A" int NOT NULL, "B" int NOT NULL);
CREATE TABLE "_PinnedLinks" ("A" int NOT NULL, "B" int NOT NULL);
INSERT INTO "User" (id, name, username, email) VALUES (1, 'Jonas', 'jonas', 'jonas@example.com'),
    (2, 'Gast', 'gast', 'gast@example.com');
INSERT INTO "Collection" (id, name, color, "parentId", "ownerId") VALUES
    (1, 'Homelab', '#22c55e', NULL, 1), (2, 'Docker', '#0ea5e9', 1, 1), (3, 'Van gast', '#0ea5e9', NULL, 2);
INSERT INTO "Link" (id, name, "collectionId", url, "textContent", image, pdf, readable, monolith, preview,
    "createdAt") VALUES
    (1, 'Portainer', 2, 'https://www.portainer.io/', 'Container beheer in de browser',
     'archives/2/1.jpeg', 'archives/2/1.pdf', 'archives/2/1_readability.json', 'archives/2/1.html',
     'archives/preview/2/1.jpeg', '2023-01-02 03:04:05'),
    (2, 'Kapot', 1, 'https://gone.example/', NULL, 'unavailable', 'archives/1/2.pdf', 'pending', NULL, NULL,
     '2023-02-02 00:00:00'),
    (3, 'Andermans link', 3, 'https://gast.example/', NULL, NULL, NULL, NULL, NULL, NULL, now());
INSERT INTO "Tag" (id, name, "ownerId") VALUES (1, 'docker', 1), (2, 'ui', 1);
INSERT INTO "_LinkToTag" VALUES (1, 1), (1, 2);
INSERT INTO "_PinnedLinks" VALUES (1, 1);
"""


@pytest.fixture
def linkwarden_db() -> str:
    dsn = TEST_DB.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(LW_SCHEMA)
    yield dsn + "?options=-csearch_path%3Dlw_test"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS lw_test CASCADE")


def _args(**kw) -> argparse.Namespace:
    base = {"dsn": None, "file": None, "to": None, "source_user": None, "files": None}
    return argparse.Namespace(**{**base, "list_users": False, "dry_run": False, **kw})


async def test_import_from_linkwarden_database_with_archives(
    make_client, linkwarden_db, tmp_path: Path, capsys
):
    c = await make_client()
    await c.login("jonas")

    data = tmp_path / "data"
    (data / "archives/2").mkdir(parents=True)
    (data / "archives/preview/2").mkdir(parents=True)
    (data / "archives/2/1.jpeg").write_bytes(b"\xff\xd8\xff screenshot")
    (data / "archives/2/1.pdf").write_bytes(b"%PDF-1.7 pdf")
    readable = {
        "title": "Portainer",
        "content": "<p>Leesbaar <script>x()</script></p>",
        "textContent": "Leesbaar",
    }
    (data / "archives/2/1_readability.json").write_text(json.dumps(readable))
    (data / "archives/2/1.html").write_text("<html><body><script>alert(1)</script>Pagina</body></html>")
    (data / "archives/preview/2/1.jpeg").write_bytes(b"\xff\xd8\xff preview")
    # archives/1/2.pdf ontbreekt bewust

    # Twee Linkwarden-gebruikers met collecties: zonder --from wordt er niet gegokt.
    assert await import_linkwarden(_args(dsn=linkwarden_db, list_users=True)) == 0
    assert "jonas" in capsys.readouterr().out
    with pytest.raises(VaultXError, match="--from"):
        await import_linkwarden(_args(dsn=linkwarden_db, to="jonas"))

    assert await import_linkwarden(_args(dsn=linkwarden_db, source_user="jonas", dry_run=True)) == 0
    assert "2 collecties en 2 links gelezen" in capsys.readouterr().out

    rc = await import_linkwarden(
        _args(dsn=linkwarden_db, source_user="jonas", to="jonas", files=str(tmp_path))
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "2 collecties en 2 links aangemaakt" in out and "5 archieven" in out
    assert "1 archiefbestanden staan in de database maar niet" in out

    cols = {x["name"]: x for x in (await c.get("/api/v1/links/collections")).json()}
    assert set(cols) == {"Homelab", "Docker"} and cols["Docker"]["parent_id"] == cols["Homelab"]["id"]
    links = {i["name"]: i for i in (await c.get("/api/v1/links")).json()["items"]}
    portainer = links["Portainer"]
    assert portainer["pinned"] and portainer["tags"] == ["docker", "ui"]
    assert portainer["created_at"].startswith("2023-01-02")
    assert sorted(a["kind"] for a in portainer["archives"]) == [
        "page",
        "pdf",
        "preview",
        "readable",
        "screenshot",
    ]
    assert links["Kapot"]["archives"] == []

    # Zoeken in de gearchiveerde tekst
    r = await c.get("/api/v1/links", params={"q": "browser"})
    assert [i["name"] for i in r.json()["items"]] == ["Portainer"]

    base = f"/api/v1/links/{portainer['id']}/archives"
    r = await c.get(f"{base}/screenshot")
    assert (
        r.status_code == 200
        and r.headers["content-type"] == "image/jpeg"
        and r.content.endswith(b"screenshot")
    )
    r = await c.get(f"{base}/page")
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"].startswith("sandbox;")
    r = await c.get(f"{base}/pdf", params={"download": "true"})
    assert r.headers["content-disposition"].startswith("attachment") and r.content.startswith(b"%PDF")
    assert (await c.get(f"/api/v1/links/{links['Kapot']['id']}/archives/pdf")).status_code == 404
    assert (await c.get(f"{base}/evil")).status_code == 422

    other = await make_client()
    await other.login("eve")
    assert (await other.get(f"{base}/pdf")).status_code == 404

    # Nog eens, nu met het ontbrekende bestand erbij: enkel dat archief komt erbij.
    (data / "archives/1").mkdir()
    (data / "archives/1/2.pdf").write_bytes(b"%PDF-1.4 later")
    assert (
        await import_linkwarden(_args(dsn=linkwarden_db, source_user="1", to="jonas", files=str(data))) == 0
    )
    out = capsys.readouterr().out
    assert "0 links aangemaakt" in out and "1 bestaande links aangevuld" in out and "1 archieven" in out


async def test_import_cli_rejects_non_linkwarden_database(linkwarden_db):
    wrong = linkwarden_db.replace("lw_test", "public")
    with pytest.raises(VaultXError, match="geen Linkwarden-database"):
        await import_linkwarden(_args(dsn=wrong, to="jonas"))


async def test_archive_with_wrong_type_is_only_a_download(make_client):
    import uuid

    from app.core.db import get_sessionmaker
    from app.models import LinkArchive

    c = await make_client()
    await c.login("jonas")
    link = await _link(c, "https://example.com/")
    html = b"<script>alert(document.cookie)</script>"
    async with get_sessionmaker()() as db:
        db.add(
            LinkArchive(
                link_id=uuid.UUID(link["id"]),
                kind="screenshot",
                media_type="text/html",
                size=len(html),
                data=html,
            )
        )
        await db.commit()
    r = await c.get(f"/api/v1/links/{link['id']}/archives/screenshot")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in r.headers["content-security-policy"]


async def test_malformed_import_is_a_clear_error(make_client):
    c = await make_client()
    await c.login("jonas")
    bad = json.dumps({"format": "vaultx-links", "collections": [{"name": "zonder id"}]})
    r = await c.post_json("/api/v1/links/import", {"format": "linkwarden", "data": bad})
    assert r.status_code == 422
    assert "verwachte vorm" in r.json()["detail"]
