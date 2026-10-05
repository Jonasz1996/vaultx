"""End-to-end test: de officiële Bitwarden CLI tegen een echte VaultX-backend.

Start zelf een fake OIDC-provider (Authentik-gedrag, uit backend/tests) en de
VaultX-backend met uvicorn, activeert een kluis met exact de browsercode van de
webinterface (frontend/src/vault/crypto.ts via Node) en doet daarna met `bw`:
login, sync, items en mappen aanmaken, wijzigen, prullenbak, terugzetten,
definitief verwijderen, lock/unlock. Tot slot wordt de gebruiker in VaultX
gedeactiveerd en moet `bw sync` en een nieuwe login falen.

    npm install -g @bitwarden/cli        # of: BW=/pad/naar/bw
    cd backend && python ../e2e/bitwarden_e2e.py

Vereist een lege PostgreSQL-database in VAULTX_DATABASE_URL (standaard die van de tests).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from tests.fake_oidc import FakeOIDCProvider  # noqa: E402

DB = os.environ.get("VAULTX_DATABASE_URL", "postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test")
BW = os.environ.get("BW", "bw")
EMAIL = "Jonas.E2E@Example.com"
PASSWORD = "een-heel-lang-master-password-42"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def self_signed_cert(directory: str) -> tuple[str, str]:
    """De Bitwarden-clients weigeren http://, dus de backend draait met een eigen TLS-certificaat."""
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vaultx-e2e")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = f"{directory}/cert.pem", f"{directory}/key.pem"
    Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    Path(key_path).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return cert_path, key_path


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


class Bw:
    def __init__(self, appdata: str, ca_file: str) -> None:
        self.env = {
            **os.environ,
            "BITWARDENCLI_APPDATA_DIR": appdata,
            "BW_NOINTERACTION": "true",
            "NODE_EXTRA_CA_CERTS": ca_file,
        }
        # Een eventuele uitgaande proxy geldt niet voor de lokale testserver.
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
            self.env.pop(var, None)
        self.session: str | None = None

    def run(self, *args: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if self.session:
            env["BW_SESSION"] = self.session
        r = subprocess.run(
            [BW, *args, "--nointeraction"], env=env, capture_output=True, text=True, input=stdin, timeout=120
        )
        if check and r.returncode != 0:
            raise SystemExit(f"bw {' '.join(args[:2])} faalde ({r.returncode}):\n{r.stdout}\n{r.stderr}")
        return r

    def json(self, *args: str) -> object:
        return json.loads(self.run(*args).stdout)

    def encode(self, obj: object) -> str:
        return self.run("encode", stdin=json.dumps(obj)).stdout.strip()


def login(client: httpx.Client, fake: FakeOIDCProvider, sub: str, email: str, groups: list[str]) -> None:
    fake.next_claims = {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "preferred_username": sub,
        "name": sub.title(),
        "groups": groups,
    }
    r = client.get("/auth/login", params={"next": "/"})
    assert r.status_code == 303, r.text
    r2 = httpx.get(r.headers["location"])
    assert r2.status_code == 302, r2.text
    cb = urlsplit(r2.headers["location"])
    r3 = client.get(f"{cb.path}?{cb.query}")
    assert r3.status_code == 303, r3.text


def main() -> None:
    fake = FakeOIDCProvider()
    fake.start()
    port = free_port()
    base = f"https://127.0.0.1:{port}"
    appdata = tempfile.mkdtemp(prefix="bw-e2e-")
    cert, key = self_signed_cert(appdata)
    env = {
        **os.environ,
        "VAULTX_DATABASE_URL": DB,
        "VAULTX_SECRET_KEY": "e2e-secret-key-e2e-secret-key-0123456789",
        "VAULTX_PUBLIC_URL": base,
        "VAULTX_OIDC_ISSUER": fake.issuer,
        "VAULTX_OIDC_CLIENT_ID": fake.client_id,
        "VAULTX_OIDC_CLIENT_SECRET": fake.client_secret,
        "VAULTX_COOKIE_SECURE": "false",
        "VAULTX_NPM_SYNC_INTERVAL_MINUTES": "0",
        "VAULTX_LOG_LEVEL": "WARNING",
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            os.environ.get("E2E_SERVER_LOG", "warning"),
            "--ssl-certfile",
            cert,
            "--ssl-keyfile",
            key,
        ],
        cwd=BACKEND,
        env=env,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health", verify=cert).status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise SystemExit("backend start niet")

        step("Inloggen in VaultX via (fake) Authentik en kluis activeren met de browsercode")
        user = httpx.Client(base_url=base, verify=cert)
        login(user, fake, "jonas-e2e", EMAIL, [])
        payload = subprocess.run(
            ["node", "--experimental-strip-types", str(ROOT / "e2e" / "vault_enroll.mts"), EMAIL, PASSWORD],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        r = user.post(
            "/api/v1/me/vault",
            content=payload,
            headers={"X-VaultX-CSRF": "1", "Content-Type": "application/json"},
        )
        assert r.status_code == 201, r.text
        email = r.json()["email"]

        bw = Bw(appdata, cert)
        version = bw.run("--version").stdout.strip()
        step(f"Bitwarden CLI {version}: server instellen en inloggen")
        bw.run("config", "server", base)
        bad = bw.run("login", email, "fout-wachtwoord-123", "--raw", check=False)
        assert bad.returncode != 0, "login met fout wachtwoord lukte"
        bw.session = bw.run("login", email, PASSWORD, "--raw").stdout.strip()
        assert bw.session, "geen sessiesleutel"
        status = bw.json("status")
        assert status["status"] == "unlocked" and status["userEmail"] == email, status

        step("Sync, map en login-item aanmaken")
        bw.run("sync")
        folder = bw.json("create", "folder", bw.encode({"name": "Infra"}))
        template = bw.json("get", "template", "item")
        login_tpl = bw.json("get", "template", "item.login")
        uri_tpl = bw.json("get", "template", "item.login.uri")
        item = {
            **template,
            "name": "Grafana",
            "notes": "achter NPM en Authentik",
            "folderId": folder["id"],
            "login": {
                **login_tpl,
                "username": "admin",
                "password": "s3cret-grafana!",
                "totp": None,
                "uris": [{**uri_tpl, "uri": "https://grafana.example.be"}],
            },
        }
        created = bw.json("create", "item", bw.encode(item))
        assert created["name"] == "Grafana"

        note = {
            **template,
            "type": 2,
            "name": "Recovery codes",
            "notes": "1234-5678",
            "secureNote": {"type": 0},
        }
        note.pop("login", None)
        bw.json("create", "item", bw.encode(note))

        step("Server-side controle: VaultX ziet enkel versleutelde data")
        st = user.get("/api/v1/me/vault").json()
        assert st["item_count"] == 2 and st["folder_count"] == 1, st
        assert any(d["type_name"].endswith("CLI") for d in st["devices"]), st["devices"]

        step("Opnieuw syncen vanaf de server en ontsleutelen")
        bw.run("sync", "--force")
        items = bw.json("list", "items", "--search", "Grafana")
        assert len(items) == 1, items
        got = items[0]
        assert got["login"]["username"] == "admin" and got["login"]["password"] == "s3cret-grafana!", got
        assert got["login"]["uris"][0]["uri"] == "https://grafana.example.be"
        assert got["folderId"] == folder["id"]
        assert bw.run("get", "password", "Grafana").stdout.strip() == "s3cret-grafana!"

        step("Item wijzigen")
        got["login"]["password"] = "nieuw-wachtwoord-2"
        got["favorite"] = True
        edited = bw.json("edit", "item", got["id"], bw.encode(got))
        assert edited["login"]["password"] == "nieuw-wachtwoord-2" and edited["favorite"] is True
        bw.run("sync", "--force")
        assert bw.run("get", "password", "Grafana").stdout.strip() == "nieuw-wachtwoord-2"

        step("Lock en unlock met het master password")
        bw.run("lock")
        bw.session = bw.run("unlock", PASSWORD, "--raw").stdout.strip()
        assert bw.run("get", "password", "Grafana").stdout.strip() == "nieuw-wachtwoord-2"

        step("Prullenbak, terugzetten en definitief verwijderen")
        bw.run("delete", "item", got["id"])
        bw.run("sync", "--force")
        trash = bw.json("list", "items", "--trash")
        assert [t["id"] for t in trash] == [got["id"]], trash
        bw.run("restore", "item", got["id"])
        bw.run("sync", "--force")
        assert bw.json("list", "items", "--trash") == []
        bw.run("delete", "item", got["id"], "--permanent")
        bw.run("delete", "folder", folder["id"])
        bw.run("sync", "--force")
        assert [i["name"] for i in bw.json("list", "items")] == ["Recovery codes"]
        folders = bw.json("list", "folders")
        assert [f["id"] for f in folders if f["id"]] == [], folders  # alleen het virtuele "No Folder"

        step("Gebruiker deactiveren in VaultX: de kluis gaat dicht op alle apparaten")
        admin = httpx.Client(base_url=base, verify=cert)
        login(admin, fake, "admin-e2e", "admin-e2e@example.com", ["vaultx-admins"])
        me = user.get("/api/v1/me").json()
        r = admin.patch(
            f"/api/v1/users/{me['id']}", json={"is_active": False}, headers={"X-VaultX-CSRF": "1"}
        )
        assert r.status_code == 200, r.text
        assert bw.run("sync", "--force", check=False).returncode != 0, "sync lukte na deactiveren"
        bw.run("logout", check=False)
        bw.session = None
        again = bw.run("login", email, PASSWORD, "--raw", check=False)
        assert again.returncode != 0, "login lukte na deactiveren"
        print((again.stdout + again.stderr).strip().splitlines()[-1])

        audit = admin.get("/api/v1/audit", params={"action": "vault.", "limit": 100}).json()["items"]
        actions = sorted({(a["action"], a["outcome"]) for a in audit})
        print("auditregels:", actions)
        assert ("vault.login", "failure") in actions and ("vault.login", "denied") in actions
        step(f"OK: Bitwarden CLI {version} werkt tegen VaultX")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(appdata, ignore_errors=True)


if __name__ == "__main__":
    main()
