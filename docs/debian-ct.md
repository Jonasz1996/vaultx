# VaultX op een Debian-container zonder Docker

Voor wie VaultX rechtstreeks op een Debian 13 (Trixie) LXC/CT wil draaien, met
Nginx Proxy Manager ervoor. Voor Docker: zie de README.

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

Proxmox-templates hebben vaak geen locale. Maakt PostgreSQL dan een
`SQL_ASCII`-database, dan faalt de eerste migratie ("cannot use a string pattern
on a bytes-like object"). Maak de cluster dan opnieuw aan met UTF-8 (wist alle
databases in die cluster):

```bash
pg_dropcluster 17 main --stop
pg_createcluster 17 main --locale C.UTF-8 --start
```

## 2. Database

```bash
sudo -u postgres createuser vaultx --pwprompt
sudo -u postgres createdb vaultx --owner vaultx
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
met hun standaardwaarde in `deploy/debian/vaultx.env.example`.

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

Zet **geen** Authentik forward-auth (outpost) voor VaultX zelf: VaultX doet de
OIDC-login zelf, en forward-auth zou ook de back-channel logout van Authentik
naar `/auth/backchannel-logout` blokkeren.

## Updaten

```bash
/opt/vaultx/deploy/debian/update.sh                          # huidige branch bijwerken
/opt/vaultx/deploy/debian/update.sh phase-7/notes-ts9df7     # of: naar een andere branch overstappen
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

Stap niet over naar `main` zolang daar enkel het ontwerpdossier staat: de code
zit voorlopig op de fase-branches.

## Back-up

Zie [backup.md](backup.md): een systemd-timer maakt elke nacht een `pg_dump`
en een kopie van de configuratie.
