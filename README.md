# VaultX

Self-hosted wachtwoord- en secretsplatform met Authentik als identiteitsbron
en Nginx Proxy Manager-integratie. Deze repository bevat **phase-0** (de
fundering), **fase 1** (NPM-connector en applicatiecatalogus), **fase 2**
(een persoonlijke kluis voor de officiële Bitwarden®-clients), **fase 3**
(Authentik-bescherming zetten in NPM), **fase 4** (ook de Authentik-kant
automatisch aanmaken), **fase 5** (automatische login: wie al bij Authentik
is aangemeld, komt meteen in de app), **fase 6** (app publiceren), **fase 7**
(versleutelde notities zoals in Obsidian), **fase 8** (inloggen met een
gewoon account, naast Authentik) en **fase 9** (wat de Bitwarden-apps nog
misten tegenover Bitwarden en Vaultwarden).

**Fase 9**

- Importeren vanuit de Bitwarden-apps en `bw import` (bv. je Vaultwarden-export), met mappen
- Bijlagen bij items (tot 100 MB per bestand), versleuteld door de app
- Websitepictogrammen, opgehaald door VaultX zelf; privé-adressen enkel voor je eigen apps uit NPM en de catalogus
- Tweestapsverificatie voor de apps met een authenticator-app, herstelcode en "onthoud mij"
- Master password wijzigen op Mijn kluis, ook overstappen naar Argon2id; alle apparaten worden afgemeld
- Alles getest met de echte Bitwarden CLI 2026.9.1 in CI. Zie [docs/bitwarden.md](docs/bitwarden.md)

**Fase 8**

- Inloggen met gebruikersnaam (of e-mail) en een VaultX-wachtwoord, naast "Inloggen met Authentik"
- Een beheerder maakt gewone accounts aan (geen open registratie), kan ze beheerder maken, en kan ook een Authentik-account een wachtwoord geven: hetzelfde account en dezelfde kluis, met beide logins
- Argon2id, tijdelijke vergrendeling na 5 foute wachtwoorden, alles in de auditlog; noodtoegang vanaf de commandolijn als Authentik plat ligt
- Getest in Chromium (ook in CI). Zie [docs/accounts.md](docs/accounts.md)

**Fase 7**

- "Notities" in het menu: Markdown-notities met [[wikilinks]], backlinks, niet-gelinkte vermeldingen, #tags (ook genest), eigenschappen (frontmatter), callouts, taken, voetnoten, wiskunde, Mermaid-diagrammen, code-kleuren, ingesloten notities, canvassen (JSON Canvas zoals Obsidian), een grafiek, dagnotities, sjablonen, bladwijzers, snelkiezer (Ctrl+O) en zoeken
- End-to-end versleuteld met de sleutel van je kluis: de browser ontgrendelt met je master password, de server ziet enkel versleutelde tekst; automatisch vergrendelen na 15 minuten
- Voor deze kluis: [[app:Naam]] linkt naar een app uit de catalogus, ||geheim|| blijft wazig tot je klikt, versiegeschiedenis, prullenbak, import van een Obsidian-kluis en export als zip
- Bijlagen: plak of sleep een afbeelding of bestand in een notitie (`![[foto.png]]`); inhoud en naam worden in de browser versleuteld (max. 25 MB per bestand, 1 GB per gebruiker)
- Getest in Chromium (ook in CI), inclusief controle dat PostgreSQL geen klare tekst bevat. Zie [docs/notes.md](docs/notes.md)

**Fase 5**

- Per app in de catalogus "Automatische login inrichten": VaultX maakt in Authentik een OIDC-provider, applicatie en groepsbindingen voor de app
- VaultX koppelt enkel met Authentik en NPM, niet met de app zelf: het toont issuer, client ID en secret om in de app in te vullen (elke app die OpenID Connect kent)
- Voorbeeld vooraf, terugdraaien bij een fout, weghalen ruimt Authentik op; client secret versleuteld, opvragen staat in de auditlog
- Getest tegen echte Authentik 2026.8.3 in de browser, met een test-app als OIDC-client: al aangemeld bij Authentik = meteen in de app (ook in CI). Zie [docs/autologin.md](docs/autologin.md)

**Fase 4**

- "Beschermen met Authentik" in één stap: VaultX maakt ook de proxy provider (forward auth), de applicatie en de outpost-toewijzing in Authentik aan
- Toegang per host: leden van de organisatie, van één team, of alle Authentik-gebruikers, via de bestaande `vaultx:`-groepconventie
- Bestaande providers voor het domein worden hergebruikt; faalt iets, dan draait VaultX zowel NPM als Authentik terug; weghalen ruimt op wat VaultX aanmaakte
- Werkt met een Authentik-serviceaccount met beperkte rechten, geen superuser-token
- Getest tegen echte Authentik 2026.8.3 en NPM 2.16.0, met aanmelden in de browser (ook in CI). Zie [docs/npm.md](docs/npm.md#6-authentik-kant-automatisch-aanmaken)

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

**Nog niet**: gedeelde kluizen (organisaties en collecties), bijlagen in de
Bitwarden-apps (bijlagen in notities bestaan wel), Sends, tweestapsverificatie
voor de kluis en master password wijzigen.

## Snel starten met Docker Compose

```bash
cp docker/.env.example docker/.env      # vul database-wachtwoord, secret key en Authentik in
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8080/health
```

Zet Nginx Proxy Manager voor poort 8080. De Authentik-kant staat in
[docs/authentik.md](docs/authentik.md), de NPM-koppeling in [docs/npm.md](docs/npm.md). Zonder Docker: [docs/debian-ct.md](docs/debian-ct.md). Back-up en terugzetten: [docs/backup.md](docs/backup.md).

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
deploy/debian/      systemd-unit, nginx-site, update- en back-upscript voor een Debian-container
e2e/                browsertest tegen echte Authentik, connectortest tegen echte NPM,
                    Bitwarden CLI tegen de kluis
docs/               Authentik, NPM, automatische login, app publiceren, Bitwarden-clients,
                    notities, Debian-installatie, back-up
```

De ontwerpdocumenten (`00-kernbeslissingen.md` en verder) staan in de root.

## Ontwikkelen

Backend (Python 3.13, zoals CI en de Docker-image, en een PostgreSQL-database):

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
