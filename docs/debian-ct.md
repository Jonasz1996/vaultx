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
apt install -y nginx git curl python3 python3-venv postgresql nodejs npm
```

Debian 13 levert PostgreSQL 17 en Node 20.19; beide werken. `npm ci` geeft op
Node 20 een `EBADENGINE`-waarschuwing (react-router vraagt Node 22+), maar de
build slaagt; je kan die waarschuwing negeren of Node 22 via NodeSource installeren. Wil je PostgreSQL 18
(de keuze uit het ontwerpdossier), gebruik dan de PGDG-repository.

## 2. Database

```bash
runuser -u postgres -- createuser vaultx --pwprompt
runuser -u postgres -- createdb vaultx --owner vaultx
```

## 3. Code en Python-omgeving

```bash
useradd --system --home /opt/vaultx --shell /usr/sbin/nologin vaultx
# Zolang phase-0 nog niet gemerged is, staat de code op de branch phase-0/skeleton.
git clone -b phase-0/skeleton https://github.com/Jonasz1996/vaultx.git /opt/vaultx
python3 -m venv /opt/vaultx/venv
/opt/vaultx/venv/bin/pip install -r /opt/vaultx/backend/requirements.txt

cd /opt/vaultx/frontend && npm ci && npm run build   # levert frontend/dist
```

## 4. Configuratie

```bash
mkdir -p /etc/vaultx
cp /opt/vaultx/docker/.env.example /etc/vaultx/vaultx.env
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

De Authentik-kant staat in [authentik.md](authentik.md). Zet je eigen account in
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

Zet **geen** Authentik forward-auth (outpost) voor VaultX zelf: VaultX doet de
OIDC-login zelf, en forward-auth zou ook de back-channel logout van Authentik
naar `/auth/backchannel-logout` blokkeren.

## Updaten

```bash
cd /opt/vaultx && git pull
/opt/vaultx/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm ci && npm run build)
systemctl restart vaultx     # migreert automatisch
```
