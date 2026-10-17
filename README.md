# VaultX

Self-hosted wachtwoord- en secretsplatform met Authentik als identiteitsbron
en Nginx Proxy Manager-integratie. Deze repository bevat **phase-0** (de
fundering), **fase 1** (NPM-connector en applicatiecatalogus), **fase 2**
(een persoonlijke kluis voor de officiële Bitwarden®-clients) en **fase 3**
(Authentik-bescherming zetten in NPM).

**Fase 3**

- Per proxy host met één knop Authentik forward auth zetten of weghalen, volgens het officiële Authentik-patroon voor NPM
- Eerst een voorbeeld: wat VaultX wijzigt, de config ervoor en erna, en wat het tegenhoudt (bv. "Satisfy Any", een eigen `location /`)
- Na het schrijven controleert VaultX de host zelf; weigert nginx de config of verwijst de host niet naar Authentik, dan zet VaultX de vorige config terug
- Journaal van elke wijziging, en auditlog; schrijven staat standaard uit per koppeling
- Getest tegen een echte NPM 2.16.0 (ook in CI). Zie [docs/npm.md](docs/npm.md#5-authentik-bescherming-zetten)

**Fase 2**

- Persoonlijke kluis per VaultX-gebruiker, te gebruiken met de officiële Bitwarden-apps, -extensies en -CLI
- Activeren in VaultX zelf (na Authentik-login); de browser leidt de sleutels af, de server ziet nooit het master password
- Login, sync, items (alle types) en mappen; prullenbak; apparaten afmelden
- Wie in VaultX gedeactiveerd wordt, verliest meteen de toegang tot zijn kluis op alle apparaten
- Getest met Bitwarden CLI 2026.9.1 (ook in CI). Zie [docs/bitwarden.md](docs/bitwarden.md)
- VaultX is niet verbonden met Bitwarden, Inc.

**Fase 1**

- NPM-connector: leest alle proxy hosts uit Nginx Proxy Manager, periodiek en op verzoek
- Herkent Authentik forward auth, access lists, bekende applicaties en `vaultx.*`-labels in *Advanced*
- Applicatiecatalogus met status per applicatie (beschermd via Authentik, beperkt, open, offline, ...)
  en waarschuwingen zoals "Satisfy Any omzeilt Authentik"
- Zie [docs/npm.md](docs/npm.md)

**Phase-0**

- Login/logout via Authentik (OIDC, PKCE, back-channel logout)
- Gebruikers worden automatisch aangemaakt; beheerders en lidmaatschappen volgen de Authentik-groepen
- Multi-tenancy: organisaties, teams, lidmaatschappen met rollen
- Append-only auditlog met een SHA-256-hashketen per organisatie
- Admin UI: dashboard, gebruikers, organisaties, teams, audit
- OpenAPI-documentatie op `/api/docs`
- Docker Compose, en een handleiding voor een Debian-container zonder Docker

**Nog niet**: gedeelde kluizen (organisaties en collecties), bijlagen, Sends,
tweestapsverificatie voor de kluis, master password wijzigen, en de Authentik-kant
(provider en applicatie) automatisch aanmaken.

## Snel starten met Docker Compose

```bash
cp docker/.env.example docker/.env      # vul database-wachtwoord, secret key en Authentik in
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8080/health
```

Zet Nginx Proxy Manager voor poort 8080. De Authentik-kant staat in
[docs/authentik.md](docs/authentik.md), de NPM-koppeling in [docs/npm.md](docs/npm.md). Zonder Docker: [docs/debian-ct.md](docs/debian-ct.md).

## Structuur

```
backend/
  app/
    api/            routers: health, auth (OIDC), v1 (me, vault, users, organizations, audit, catalog, npm),
                    bitwarden (/identity en /api voor Bitwarden-clients)
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
e2e/                browsertest tegen echte Authentik, connectortest tegen echte NPM,
                    Bitwarden CLI tegen de kluis
docs/               Authentik-koppeling, NPM-koppeling, Bitwarden-clients, Debian-installatie
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
