# VaultX

Self-hosted wachtwoord- en secretsplatform met Authentik als identiteitsbron
en Nginx Proxy Manager-integratie. Deze repository bevat **phase-0**: een dunne
verticale slice die de fundering bewijst, nog zonder kluisfunctionaliteit.

**Wel in phase-0**

- Login/logout via Authentik (OIDC, PKCE, back-channel logout)
- Gebruikers worden automatisch aangemaakt; beheerders en lidmaatschappen volgen de Authentik-groepen
- Multi-tenancy: organisaties, teams, lidmaatschappen met rollen
- Append-only auditlog met een SHA-256-hashketen per organisatie
- Admin UI: dashboard, gebruikers, organisaties, teams, audit
- OpenAPI-documentatie op `/api/docs`
- Docker Compose, en een handleiding voor een Debian-container zonder Docker

**Nog niet**: kluis, encryptie, Bitwarden-compatibiliteit, bijlagen. De
volgende slice is de NPM-connector en de applicatiecatalogus.

## Snel starten met Docker Compose

```bash
cp docker/.env.example docker/.env      # vul database-wachtwoord, secret key en Authentik in
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8080/health
```

Zet Nginx Proxy Manager voor poort 8080. De Authentik-kant staat in
[docs/authentik.md](docs/authentik.md). Zonder Docker: [docs/debian-ct.md](docs/debian-ct.md).

## Structuur

```
backend/
  app/
    api/            routers: health, auth (OIDC), v1 (me, users, organizations, audit)
    core/           config, database, fouten, request-context
    models/         SQLAlchemy 2-modellen
    repositories/   data-toegang, geen businessregels
    schemas/        Pydantic-modellen voor de API
    services/       businesslogica, autorisatie, transacties, audit
    main.py
  alembic/          migraties
  tests/            pytest tegen PostgreSQL + een nep-OIDC-provider over HTTP
frontend/           React 19 + Vite + TanStack Query
docker/             Dockerfiles, Compose, nginx, lokale Authentik met blueprint
deploy/debian/      systemd-unit en nginx-site voor een Debian-container
e2e/                browsertest tegen echte Authentik
docs/               Authentik-koppeling, Debian-installatie
```

De ontwerpdocumenten (`00-kernbeslissingen.md` en verder) staan in de root.

## Ontwikkelen

Backend (Python 3.11+ en een PostgreSQL-database):

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx
alembic upgrade head
uvicorn app.main:app --reload            # met de VAULTX_OIDC_* en VAULTX_SECRET_KEY variabelen gezet

# tests: aparte database, wordt bij elke test leeggemaakt
VAULTX_TEST_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_test pytest
ruff check . && ruff format --check .
```

Frontend:

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxyt /api en /auth naar :8000
npm run build
```

Lokale Authentik met testgebruikers (`alice` is beheerder, `bob` gewoon lid):
zie [e2e/README.md](e2e/README.md).

## Belangrijkste ontwerpkeuzes in phase-0

- **Sessies zitten server-side.** De cookie bevat een willekeurig token; de
  database bewaart enkel de SHA-256 ervan. Zo kan een sessie ingetrokken
  worden (door de gebruiker, een beheerder, deactivatie of Authentik).
- **CSRF**: cookies zijn `HttpOnly`, `SameSite=Lax`, en elke wijzigende
  request met cookie-authenticatie moet de header `X-VaultX-CSRF: 1` dragen.
- **Tokens**: enkel asymmetrisch ondertekende ID- en access tokens, met
  controle op issuer, audience, nonce en vervaldatum. JWKS wordt ververst bij
  een onbekende sleutel.
- **Audit**: elke wijziging schrijft in dezelfde transactie een auditregel.
  Een databasetrigger verbiedt `UPDATE`, `DELETE` en `TRUNCATE` op de
  audittabel; `/api/v1/audit/verify` herberekent de hashketen. Ondertekende
  checkpoints buiten de database (KB-26 in het dossier) volgen later.
- **Tenancy**: een teamlidmaatschap kan via een samengestelde foreign key
  enkel naar een team van dezelfde organisatie wijzen; een organisatie houdt
  altijd minstens één eigenaar.

## Licentie

Nog te bepalen (voorstel in het ontwerpdossier: AGPL-3.0 voor server en apps).
