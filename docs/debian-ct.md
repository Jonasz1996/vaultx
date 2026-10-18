# VaultX op een Debian-container zonder Docker

Voor wie VaultX rechtstreeks op een Debian 13 (Trixie) LXC/CT wil draaien, met
Nginx Proxy Manager ervoor. Voor Docker: zie de README.

**Proxmox LXC:** zet bij de container *Options → Features → Nesting* aan. Zonder
nesting faalt de systemd-hardening van de service met `status=226/NAMESPACE`.
Proxmox-templates hebben geen `sudo`; de commando's hieronder gebruiken daarom
`runuser`.

## 1. Pakketten

```bash
apt update
apt install -y nginx git curl python3 python3-venv postgresql

# Node 22: de frontend (react-router, Mermaid) vraagt Node 22.12 of nieuwer, Debian 13 levert 20
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt install -y nodejs
```

Debian 13 levert PostgreSQL 17; dat werkt. Wil je PostgreSQL 18 (de keuze uit
het ontwerpdossier), gebruik dan de PGDG-repository.

## 2. Database

Controleer eerst de codering met `runuser -u postgres -- psql -l`. Een kale
Proxmox-template heeft geen locale, waardoor de cluster in `SQL_ASCII` staat;
de migratie faalt dan met `TypeError: cannot use a string pattern on a
bytes-like object`. Staat er `SQL_ASCII`, maak de cluster dan opnieuw aan in
UTF-8 (op een verse installatie gaat er niets verloren):

```bash
pg_dropcluster 17 main --stop
pg_createcluster 17 main --locale C.UTF-8 --start
```

Daarna de rol en de database:

```bash
runuser -u postgres -- createuser vaultx --pwprompt
runuser -u postgres -- createdb vaultx --owner vaultx --encoding UTF8 --locale C.UTF-8 --template template0
```

## 3. Code en Python-omgeving

```bash
useradd --system --home /opt/vaultx --shell /usr/sbin/nologin vaultx
git clone https://github.com/Jonasz1996/vaultx.git /opt/vaultx
python3 -m venv /opt/vaultx/venv
/opt/vaultx/venv/bin/pip install -r /opt/vaultx/backend/requirements.txt

cd /opt/vaultx/frontend && npm ci && npm run build   # levert frontend/dist
```

## 4. Configuratie

```bash
mkdir -p /etc/vaultx
cp /opt/vaultx/deploy/debian/vaultx.env.example /etc/vaultx/vaultx.env
chmod 640 /etc/vaultx/vaultx.env && chgrp vaultx /etc/vaultx/vaultx.env
```

Vul in `/etc/vaultx/vaultx.env` minstens in:

```
VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:WACHTWOORD@127.0.0.1:5432/vaultx
VAULTX_PUBLIC_URL=https://vaultx.jouwdomein.be
VAULTX_SECRET_KEY=...            # openssl rand -base64 48
VAULTX_OIDC_ISSUER=https://auth.jouwdomein.be/application/o/vaultx/
VAULTX_OIDC_CLIENT_ID=...
VAULTX_OIDC_CLIENT_SECRET=...
```

De Authentik-kant staat in [authentik.md](authentik.md). Alle andere opties staan
met hun standaardwaarde in `deploy/debian/vaultx.env.example`. Zet je eigen account in
de Authentik-groep `vaultx-admins`, anders ben je na het inloggen geen beheerder.

Het sessiecookie is `Secure`: inloggen werkt enkel via HTTPS (via NPM). Test je
tijdelijk over gewoon http, zet dan `VAULTX_COOKIE_SECURE=false` en
`VAULTX_PUBLIC_URL=http://...`, en draai het terug zodra NPM ervoor staat.

## 5. systemd en nginx

```bash
cp /opt/vaultx/deploy/debian/vaultx.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now vaultx
curl http://127.0.0.1:8000/health

cp /opt/vaultx/deploy/debian/nginx-vaultx.conf /etc/nginx/sites-available/vaultx
ln -s /etc/nginx/sites-available/vaultx /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

De service draait de migraties bij elke start (`ExecStartPre`), luistert enkel
op 127.0.0.1 en is met systemd-hardening afgeschermd.

## 6. Nginx Proxy Manager

Nieuwe Proxy Host:

- Domain: `vaultx.jouwdomein.be`
- Scheme `http`, Forward Hostname = IP van de container, poort `80`
- SSL: Let's Encrypt-certificaat, Force SSL en HSTS aan
- **Websockets Support** aan: live synchronisatie voor de Bitwarden-apps (fase 9)

Zet **geen** Authentik forward-auth (outpost) voor VaultX zelf: VaultX doet de
OIDC-login zelf, en forward-auth zou ook de back-channel logout van Authentik
naar `/auth/backchannel-logout` blokkeren.

## Updaten

```bash
/opt/vaultx/deploy/debian/update.sh                          # huidige branch bijwerken
/opt/vaultx/deploy/debian/update.sh main                     # of: van een fase-branch naar main overstappen
```

Het script maakt eerst een back-up van de database en `/etc/vaultx/vaultx.env`
in `/var/backups/vaultx`, haalt de code op, installeert de Python-pakketten,
bouwt de frontend, vernieuwt de systemd-unit en de nginx-site (de vorige versie
blijft naast het bestand als `.bak-<tijd>`; weigert `nginx -t` de nieuwe, dan
zet het de oude terug), herstart VaultX (dat migreert de database) en wacht
tot `/health/ready` antwoordt. Lukt dat niet, dan toont het de log en hoe je
terugzet (zie [backup.md](backup.md)).

Heb je in `/etc/nginx/sites-available/vaultx` zelf iets aangepast (bv.
`set_real_ip_from` beperkt tot je NPM-host), zet dat dan na het updaten opnieuw
in de nieuwe versie; het oude bestand staat ernaast als `.bak-<tijd>`.

## Back-up

Zie [backup.md](backup.md): een systemd-timer maakt elke nacht een `pg_dump`
en een kopie van de configuratie.
