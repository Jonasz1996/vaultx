# VaultX — 08 GitHub-milestones en issues

Status: voorstel ter review door Jonas · Datum: 2026-10-04 · Versie 0.1
Bindende bron: `00-kernbeslissingen.md` (Deel E bepaalt de releasescope). Fasering: `07-implementatieroadmap.md`. Repositorystructuur: `06-repositorystructuur.md`.

Machineleesbare versies:
- `08-issues.json` — 163 objecten (148 issues voor MVP en v1.0 + 15 enterprise-epics) met `id`, `title`, `body` (markdown met acceptatiecriteria), `labels`, `milestone`, `depends_on`.
- `08-milestones.json` — 14 milestones met `key`, `title`, `release`, `description` (incl. exit-criteria).

Validatie: beide bestanden zijn met `python3 -m json.tool` en een controlescript gevalideerd (zie §7).

---

## 1. Principes voor issues die Claude Code kan oppakken

1. **Eén issue = één PR**, doel < ~400 regels diff (excl. gegenereerde bestanden en fixtures). Te groot? Opsplitsen vóór start.
2. **Acceptatiecriteria zijn toetsbaar** en worden elk door een test gedekt. Elk issue heeft impliciet het extra criterium "tests voor elk criterium; `just lint && just test` groen" (in de JSON-body expliciet opgenomen).
3. **Afhankelijkheden zijn expliciet** (`depends_on`). Een issue gaat pas naar *Ready* als alle afhankelijkheden *Done* zijn.
4. **Compat-issues bevatten fixtures**: een opgenomen request/response (tools/compat-recorder) of verwijzing naar de Vaultwarden-referentie. Zonder fixture is een compat-issue niet *Ready* (07 §6).
5. **Label `agent:ready`**: Claude Code mag het issue zelfstandig implementeren; een mens reviewt de PR. **Label `agent:assist`**: (alle `security:crypto-review`-issues) een mens leidt ontwerp en review; Claude Code helpt met tests, testvectoren, lijmcode. Protocolbeschrijving in `docs/security` gaat vóór de implementatie-PR.
6. **Verwijzingen naar KB-xx** in de beschrijving; afwijken vereist een ADR in dezelfde PR.

Voorbeeldprompt voor een Claude Code-sessie per issue:
> Pak VX-025 op. Lees het issue, CLAUDE.md en de vermelde KB's. Schrijf eerst de golden-fixture- en autorisatietests, dan de implementatie. Houd de diff klein; meld als het issue te groot blijkt in plaats van door te bouwen.

---

## 2. Labels

Beheerd in `.github/labels.yml`, gesynchroniseerd door een workflow (06 §4.7).

| Groep | Labels | Kleur (indicatief) | Betekenis |
|---|---|---|---|
| **type** | `type:feature`, `type:bug`, `type:chore`, `type:build`, `type:test`, `type:docs`, `type:security`, `type:spike`, `type:epic` | blauw-tinten | soort werk; `spike` = tijdgebonden onderzoek met notitie als resultaat |
| **area** | `area:server`, `area:crypto`, `area:compat`, `area:identity`, `area:authentik`, `area:vault`, `area:org`, `area:policy`, `area:audit`, `area:notify`, `area:jobs`, `area:storage`, `area:secrets`, `area:gateway`, `area:connectors`, `area:cli`, `area:web`, `area:admin`, `area:extension`, `area:android`, `area:ios`, `area:deploy`, `area:helm`, `area:ci`, `area:docs` | groen-tinten | komt overeen met crate/pakket en conventional-commit-scope |
| **priority** | `priority:p0` (blokkeert milestone-exit), `priority:p1` (hoort in milestone), `priority:p2` (mag schuiven naar volgende milestone), `priority:p3` (nice-to-have) | rood → grijs | |
| **release** | `release:mvp`, `release:v1.0`, `release:enterprise` | paars | scope volgens Deel E |
| **security** | `security:crypto-review` (verplicht 2 menselijke reviewers, CODEOWNERS), `security:sensitive` (raakt authN/authZ/geheimen; extra reviewaandacht), `security:threat-model` (vereist update van 03-threat-model), `security:advisory` (automatisch door cargo-audit/Renovate) | oranje | |
| **agent** | `agent:ready`, `agent:assist` | geel | zie §1 |
| **status-hulp** | `needs-fixture`, `needs-adr`, `blocked-external`, `good-first-issue` | grijs | |

---

## 3. GitHub Projects v2-board "VaultX Roadmap"

### 3.1 Velden

| Veld | Type | Waarden |
|---|---|---|
| **Status** | single select | `Backlog` → `Ready` → `In progress` → `In review` → `Done`; plus `Blocked` |
| **Release** | single select | `MVP`, `v1.0`, `Enterprise` (gespiegeld uit label; automatisering via workflow of handmatig) |
| **Area** | single select | dezelfde waarden als `area:*` |
| **Priority** | single select | `P0`, `P1`, `P2`, `P3` |
| **Estimate** | number | ideale dagen (1, 2, 3, 5, 8); > 5 = opsplitsen |
| **Iteration** | iteration | 2 weken, start op maandag |
| **Milestone** | (ingebouwd) | M0 … M12, E |
| **Depends on** | text | VX-ID's (tot GitHub's issue-dependencies "blocked by" overal beschikbaar is; daarna die relatie gebruiken — verifiëren) |
| **Agent** | single select | `Claude Code`, `Mens`, `Samen` |

### 3.2 Views

| View | Type | Filter / groepering | Doel |
|---|---|---|---|
| **Board** | board | groep: Status; filter: huidige Iteration | dagelijkse stand-up |
| **Roadmap** | roadmap | groep: Milestone; datums: Iteration | planning t.o.v. 07 |
| **Backlog per milestone** | table | groep: Milestone; sort: Priority, ID | refinement |
| **Ready voor Claude** | table | `Status = Ready` AND label `agent:ready`; sort: Priority | werkvoorraad voor agent-sessies |
| **Security review** | table | label `security:*`; Status ≠ Done | reviewers zien wat hun aandacht vraagt |
| **Blocked** | table | Status = Blocked of label `blocked-external` | afhankelijkheden opvolgen |
| **Per area** | board | groep: Area | eigenaarschap per gebied |

### 3.3 Automatisering
- Ingebouwde workflows: item toegevoegd → `Backlog`; PR geopend met `Closes #n` → `In review`; issue gesloten → `Done`.
- Workflow `project-ready.yml` (later): zet een issue op `Ready` zodra alle `depends_on` gesloten zijn.
- Milestones sluiten pas als alle `priority:p0`-issues gesloten zijn en de exit-criteria in de milestone-beschrijving afgevinkt (handmatige check).

---

## 4. Milestones

| Key | Titel | Release | Kern-exit-criteria | Issues |
|---|---|---|---|---|
| M0 | Fundament & walking skeleton (v0.1) | MVP | `bw login`+`bw sync` in CI; leases met fencing; geen secrets in logs | VX-001…018 |
| M1 | Kluis-kern (v0.2) | MVP | alle Bitwarden-clients voor persoonlijke kluis; sync 5k items < 1 s | VX-020…036 |
| M2 | Compat-uitbreiding (v0.3–v0.4) | MVP | orgs/collecties, bijlagen, Sends, 2FA, multi-node notificaties | VX-040…055 |
| M3 | Authentik, audit & admin (v0.5–v0.6) | MVP | SSO+SCIM+lifecycle, `audit verify`, adminconsole | VX-060…075 |
| M4 | HA, deployment & MVP-release (v0.9) | MVP | chaos, restore, upgrade, k6, Helm basis | VX-080…093 |
| M5 | Identiteit & sleutelbeheer (v0.10) | v1.0 | TDE, approvals, PRF, emergency access, LDAP/OIDC/SAML | VX-100…108 |
| M6 | Organisatiemodel & policy (v0.11) | v1.0 | Cedar overal, workspaces, SIEM, OpenAPI | VX-110…119 |
| M7 | VaultX-webapp (v0.12) | v1.0 | pariteit, CSP/SRI, WCAG | VX-120…129 |
| M8 | App-catalogus & proxy-connectors (v0.13) | v1.0 | NPM + Traefik, Grafana-scenario | VX-130…139 |
| M9 | VaultX Connect & Access Gateway (v0.14) | v1.0 | extensie in stores, gateway geïsoleerd | VX-140…148 |
| M10 | Android (v0.15) | v1.0 | autofill, passkeys, offline | VX-150…156 |
| M11 | Machine-secrets & Key Connector (v0.16) | v1.0 | `vx run`, KEK-rotatie, Key Connector opt-in | VX-160…165 |
| M12 | Kubernetes, DR & v1.0-release (v1.0) | v1.0 | zero-downtime, DR, externe audit | VX-170…175 |
| E | Enterprise (v2.x) | Enterprise | per epic | VX-E01…E15 |

De volledige beschrijving en exit-criteria per milestone staan in `08-milestones.json` (veld `description`) en in 07 §3. ID-reeksen hebben bewust gaten (VX-019, VX-037…039 enz.) zodat later toegevoegde issues bij hun milestone kunnen blijven.

---

## 5. Import met `gh` (indicatief)

```bash
# 1. Labels
gh label create "type:feature" --color 1f6feb --force   # … per label uit §2 (of via labels.yml-workflow)

# 2. Milestones
jq -c '.[]' 08-milestones.json | while read -r m; do
  gh api repos/$REPO/milestones -f title="$(jq -r .title <<<"$m")" -f description="$(jq -r .description <<<"$m")"
done

# 3. Issues in volgorde (afhankelijkheden eerst), mapping VX-ID → issuenummer bijhouden
jq -c '.[]' 08-issues.json | while read -r i; do
  url=$(gh issue create --repo $REPO \
        --title "$(jq -r '.id + " " + .title' <<<"$i")" \
        --body "$(jq -r .body <<<"$i")" \
        --milestone "$(jq -r .milestone <<<"$i")" \
        $(jq -r '.labels[] | "--label \(.)"' <<<"$i"))
  echo "$(jq -r .id <<<"$i") ${url##*/}" >> idmap.txt
done
# 4. Tweede pass: "- VX-nnn" in bodies vervangen door "#<nummer>" (gh issue edit) en/of
#    "blocked by"-relaties zetten; issues toevoegen aan het project (gh project item-add).
```

Labels moeten bestaan vóór stap 3 (anders faalt `gh issue create`). De volgorde in de JSON is al topologisch per milestone; afhankelijkheden wijzen nooit naar een latere milestone (gecontroleerd door het generatiescript).

---

## 6. Issue-backlog

Notatie: per issue titel, labels, afhankelijkheden, beschrijving en acceptatiecriteria. Het standaardcriterium "tests voor elk criterium; `just lint && just test` groen" geldt voor alle issues en wordt hier niet herhaald.

Aantallen: **MVP 81 issues** (M0–M4), **v1.0 67 issues** (M5–M12), **15 enterprise-epics**.

### M0 · Fundament & walking skeleton (v0.1) — 18 issues

#### VX-001 · Monorepo-skelet: Cargo-workspace, pnpm-workspace, justfile, toolchain

Labels: `type:chore` `area:ci` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: —

Maak de rootstructuur volgens 06 §2: `Cargo.toml` (workspace, `[workspace.dependencies]`, `[workspace.lints]`), `rust-toolchain.toml`, `pnpm-workspace.yaml`, `justfile` met recepten `bootstrap, build, test, lint, fmt`, lege crate `vaultx-core`, REUSE-licentiebestanden.

- [ ] `just bootstrap && just build && just test` slaagt op schone checkout (Linux, macOS)
- [ ] `[workspace.lints]` bevat `unsafe_code = "forbid"` en `clippy::unwrap_used = "deny"` (niet-test)
- [ ] `reuse lint` slaagt
- [ ] README beschrijft de drie commando's

#### VX-002 · CI-workflow Rust: fmt, clippy, nextest, cargo-deny, cargo-audit

Labels: `type:chore` `area:ci` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

GitHub Actions `ci-rust.yml` met caching. `deny.toml` met licentie-allowlist compatibel met AGPL-3.0/Apache-2.0 en alleen crates.io als bron.

- [ ] Workflow draait op PR en push naar main, met padfilter op `crates/**`, `Cargo.*`, `migrations/**`
- [ ] Stappen: `cargo fmt --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo nextest run`, `cargo deny check`
- [ ] Nightly job draait `cargo audit` en opent bij bevindingen een issue met label `security:advisory`
- [ ] Mediane looptijd < 10 min met warme cache

#### VX-003 · CI-workflow TypeScript: pnpm, ESLint, Prettier, tsc, Vitest

Labels: `type:chore` `area:ci` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

`ci-web.yml` voor `web/*`, `extension`, `packages/*`. Gedeelde config in `packages/config-eslint` en `packages/config-tsconfig` (typescript-eslint strict-type-checked, no-unsanitized).

- [ ] `pnpm install --frozen-lockfile` + `pnpm -r lint typecheck test build` in CI
- [ ] Een bewust toegevoegde floating promise laat lint falen (test in PR beschreven)
- [ ] Padfilter zodat Rust-only PR's deze workflow overslaan

#### VX-004 · CLAUDE.md, CONTRIBUTING, CODEOWNERS, PR- en issue-templates

Labels: `type:docs` `area:docs` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

Neem de CLAUDE.md-inhoud uit 06 §6 op, plus gebieds-CLAUDE.md voor `crates/vaultx-crypto`. PR-template met checklist (tests, migratie, security-impact, compat-impact). Issue-templates bug/feature/compat-report; security-meldingen verwijzen naar SECURITY.md.

- [ ] CLAUDE.md bevat secties Bouwen/testen, Crypto-regels, Logging, Wat je niet doet
- [ ] CODEOWNERS dekt crypto-, keys-, gateway-, migratie- en deploy-paden
- [ ] SECURITY.md beschrijft privé melden (GitHub private vulnerability reporting)
- [ ] Labels uit `.github/labels.yml` worden door `labels.yml`-workflow gesynchroniseerd

#### VX-005 · ADR-proces en ADR-0001…0030 als verwijzingen naar KB-01…KB-30

Labels: `type:docs` `area:docs` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

MADR-template in `docs/adr/`. Eén ADR per KB met status `accepted`, context in één alinea en link naar 00-kernbeslissingen. Ontwerpdossier 00–10 naar `docs/architecture/`.

- [ ] `docs/adr/0000-template.md` aanwezig
- [ ] 30 ADR-bestanden met juiste KB-verwijzing
- [ ] Index `docs/adr/README.md` gegenereerd door `just adr-index`

#### VX-006 · vaultx-server: axum-skelet, --roles, /alive en /ready, graceful shutdown

Labels: `type:feature` `area:server` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

Binary `vaultx` met subcommando's `serve` en `migrate`. `--roles` accepteert `api,notifications,worker,gateway` (KB-24); onbekende rol = startfout. `vaultx-config` laadt TOML + env (`VAULTX_*`) en `*_FILE`-secrets.

- [ ] `vaultx serve --roles=api` start en `/alive` geeft 200
- [ ] `/ready` geeft 503 zolang DB-migraties niet op de verwachte versie staan
- [ ] SIGTERM: stopt met nieuwe requests, wacht max. `shutdown_grace` op lopende requests (test)
- [ ] Configvalidatiefouten noemen de sleutel maar nooit de waarde

#### VX-007 · vaultx-telemetry: JSON-logs, OpenTelemetry, Prometheus, redactielaag

Labels: `type:feature` `area:server` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-006

tracing-subscriber JSON, OTLP-export optioneel, `/metrics`. `Secret<T>` in `vaultx-core` met geredigeerde Debug/Display. Redactielaag maskeert velden met namen als password, hash, token, key, secret, authorization, cookie (KB-23).

- [ ] Canary-test: request met wachtwoord `CANARY-…` levert geen canary in logoutput, ook niet bij fout
- [ ] `Secret<T>` implementeert geen `Clone` zonder expliciete `expose`
- [ ] HTTP-metrics per route (zonder path-parameters als label)
- [ ] Trace-id in elke logregel en in `X-Request-Id`-respons

#### VX-008 · vaultx-storage: Postgres-pool, sqlx-migraties, offline mode, testcontainers-harnas

Labels: `type:feature` `area:storage` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-006

sqlx met PostgreSQL 17, `migrations/` alleen voorwaarts, `.sqlx/` ingecheckt, `vaultx migrate`. `vaultx-testkit` start Postgres via testcontainers en levert per test een schone database.

- [ ] `cargo sqlx prepare --check` in CI
- [ ] Integratietest draait migraties op lege DB en verifieert schema-versie
- [ ] `vaultx migrate` is idempotent en neemt een advisory lock zodat twee gelijktijdige runs serialiseren
- [ ] Testharnas maakt per test een aparte database (parallel veilig)

#### VX-009 · vaultx-jobs: Postgres-jobqueue met FOR UPDATE SKIP LOCKED

Labels: `type:feature` `area:jobs` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-008

Tabel `jobs` (type, payload jsonb, run_at, attempts, locked_until). Worker-rol pollt met `SKIP LOCKED`, exponentiële backoff, dead-letter na N pogingen (KB-19). Cron-planner voor periodieke taken.

- [ ] Twee workers verwerken 1 000 jobs zonder dubbele uitvoering (test)
- [ ] Gefaalde job wordt herhaald met backoff en belandt na max. pogingen in `dead`
- [ ] Gecrashte worker: job wordt na `locked_until` door een ander opgepakt
- [ ] Metrics: queue-diepte en verwerkingstijd per type

#### VX-010 · Postgres-leases met fencing tokens (KB-06)

Labels: `type:feature` `area:jobs` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-008

Tabel `leases(name, holder, token bigint, expires_at)`; acquire/renew/release; monotone fencing tokens. Schrijfoperaties onder lease controleren het token in dezelfde transactie. `pg_advisory_xact_lock` helper voor korte secties.

- [ ] Acquire door tweede houder faalt zolang lease geldig is
- [ ] Na verloop krijgt nieuwe houder een hoger token; schrijfpoging met oud token faalt (test)
- [ ] Renew door niet-houder faalt
- [ ] Documentatie in `docs/architecture` met sequentiediagram

#### VX-011 · vaultx-crypto: EncString type 2 parse/serialize en AES-CBC-HMAC met testvectoren

Labels: `type:feature` `area:crypto` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-001

Crate zonder I/O. Parseer en serialiseer Bitwarden EncString `2.iv|ct|mac`, encrypt/decrypt met AES-256-CBC + HMAC-SHA256 (encrypt-then-MAC, constant-time vergelijking). Alleen RustCrypto-crates. `zeroize` op sleutels.

- [ ] Testvectoren uit Bitwarden-SDK/Vaultwarden (bron vermeld) slagen
- [ ] MAC-fout → foutwaarde, nooit panic; vergelijking via `subtle`
- [ ] Fuzz-target voor de parser (cargo-fuzz) draait 10 min zonder crash
- [ ] Sleuteltypes implementeren geen `Debug`/`Clone`

#### VX-012 · vaultx-crypto: KDF's (PBKDF2-SHA256, Argon2id) en master-password-hash-afleiding

Labels: `type:feature` `area:crypto` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-011

Bitwarden-afleiding: master key = KDF(password, email), master password hash = PBKDF2(master key, password, 1). Server-side opslag-hash van de client-hash met Argon2id (eigen parameters). Validatie van KDF-parameters tegen minima (KB-10).

- [ ] Testvectoren voor PBKDF2 (600 000 it.) en Argon2id (64 MiB, t=3, p=4) slagen
- [ ] `validate_kdf()` weigert waarden onder de minima met specifieke fout
- [ ] Server-opslaghash verifieert in constante tijd
- [ ] Benchmark gedocumenteerd (criterion)

#### VX-013 · Docker-image (distroless, non-root, multi-arch) en compose/single

Labels: `type:build` `area:deploy` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-006, VX-008

`deploy/docker/Dockerfile` multi-stage met cargo-chef; runtime distroless/static, UID ≠ 0, read-only rootfs. `deploy/compose/single/compose.yaml` met vaultx + PostgreSQL 17 + volume voor bijlagen.

- [ ] Image < 60 MB, amd64 + arm64
- [ ] `docker compose up` → `/ready` 200 binnen 30 s
- [ ] Container draait met `read_only: true` en `cap_drop: [ALL]`
- [ ] Trivy: geen high/critical

#### VX-014 · Bitwarden-compat: prelogin en registratie (minimaal)

Labels: `type:feature` `area:compat` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-008, VX-012

Crate `vaultx-compat-bitwarden` + `vaultx-identity`. `POST /identity/accounts/prelogin` en registratie zoals de gepinde `bw`-versie die gebruikt (verifiëren: huidige registratieflow met e-mailverificatie). Opslag van user (keys, KDF-config, opslaghash).

- [ ] Golden-fixtures (opgenomen requests) voor prelogin en registratie slagen
- [ ] Prelogin voor onbekend e-mailadres geeft standaard-KDF terug (geen user enumeration)
- [ ] Registratie met KDF onder minima geeft 400
- [ ] Migratie voor `users` en `devices`

#### VX-015 · Bitwarden-compat: token-endpoint password grant (minimaal) en lege sync

Labels: `type:feature` `area:compat` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-014, VX-007

`POST /identity/connect/token` (grant_type=password) met device-registratie; access token (JWT, EdDSA) en refresh token. `GET /api/sync` met profiel en lege lijsten. `GET /api/config` met minimale velden.

- [ ] Golden-fixture-tests voor token, sync en config
- [ ] Fout wachtwoord → 400 met Bitwarden-foutvorm, zonder onderscheid tussen onbekende gebruiker en fout wachtwoord
- [ ] JWT bevat geen e-mail of andere PII buiten wat clients vereisen (gedocumenteerd)
- [ ] Signeersleutel uit config (`*_FILE`), nooit gelogd

#### VX-016 · Walking skeleton E2E: Bitwarden CLI login + sync tegen compose-single

Labels: `type:test` `area:compat` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-013, VX-015

`tests/compat/cli-smoke.sh`: start compose-single, `bw config server`, registreer testaccount, `bw login`, `bw sync`, `bw status`. Workflow `compat.yml` draait dit op elke PR met gepinde `bw`-versie.

- [ ] Script slaagt lokaal (`just compat`) en in CI
- [ ] `bw`-versie gepind in één bestand (`tests/compat/versions.toml`)
- [ ] Bij falen worden serverlogs als artefact bewaard

#### VX-017 · Supply chain: SBOM, cosign-ondertekening, release-please-configuratie

Labels: `type:chore` `area:ci` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-002, VX-013

release-please in manifest-modus (06 §7.4), `release.yml` bouwt images, ondertekent met cosign (keyless), maakt CycloneDX-SBOM en SLSA-provenance.

- [ ] Een testrelease (`v0.1.0-rc.1`) levert ondertekend image + SBOM op GHCR
- [ ] `cosign verify` met identiteit van de workflow slaagt (gedocumenteerd)
- [ ] Conventional-commit-check op PR-titels

#### VX-018 · Lokale ontwikkelomgeving tools/dev-env

Labels: `type:chore` `area:ci` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-001

Compose met Postgres 17, Valkey 8, Garage (1 node), Authentik, NPM, Mailpit voor `just dev`.

- [ ] `just dev` start alle services en print URL's/inloggegevens
- [ ] Seed-script maakt Authentik-testgebruikers en een Garage-bucket
- [ ] Documentatie in `tools/dev-env/README.md`


### M1 · Kluis-kern (v0.2) — 17 issues

#### VX-020 · Domeinmodel in vaultx-core: User, Device, Cipher, Folder, ID's

Labels: `type:feature` `area:server` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-011

Types met UUIDv7-ID's, `EncString` als opaque waarde, `CipherType`-enum (login, secure note, card, identity, ssh key), revisiedata. Geen HTTP-/SQL-afhankelijkheden.

- [ ] Serde-roundtrip-tests voor alle types
- [ ] `EncString` valideert formaat maar kan niet ontsleutelen (geen sleuteltoegang)
- [ ] cargo-deny-ban: vaultx-core hangt niet af van axum/sqlx

#### VX-021 · Volledige accountregistratie inclusief sleutels en e-mailverificatie

Labels: `type:feature` `area:identity` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-014, VX-009

Opslag van protected user key, publieke sleutel en versleutelde private key; e-mailverificatie volgens huidige clientflow (verifiëren); configurabele open/gesloten registratie en domein-allowlist.

- [ ] Registratie gesloten → 403 met Bitwarden-foutvorm
- [ ] Domein-allowlist afgedwongen
- [ ] Verificatiemail via job (VX-047 mag stub zijn)
- [ ] Golden-fixtures voor extensie en CLI

#### VX-022 · Token-endpoint: refresh-tokenrotatie en reuse-detectie

Labels: `type:feature` `area:identity` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-015

Refresh tokens gehasht opgeslagen per tokenfamilie; elke refresh roteert; hergebruik van een oud token revoceert de familie en logt een security-event (KB-25). Access token-levensduur configureerbaar (5–15 min).

- [ ] Refresh roteert en oude token wordt ongeldig
- [ ] Reuse → hele familie ingetrokken + audit-/log-event
- [ ] Tokens alleen als hash in DB
- [ ] Klok-abstractie gebruikt in tests

#### VX-023 · JWT-signeersleutelbeheer en rotatie (EdDSA)

Labels: `type:feature` `area:identity` `priority:p1` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-015

Meerdere actieve verificatiesleutels (kid), één signeersleutel; rotatie zonder uitloggen. Opslag via `vaultx-keys` (bestand of envelope-encrypted in DB).

- [ ] Tokens getekend met oude sleutel blijven geldig tot hun expiry
- [ ] `kid` in header
- [ ] Rotatiecommando `vaultx admin rotate-signing-key`
- [ ] Geen sleutelmateriaal in logs (canary)

#### VX-024 · Rate limiting voor login en gevoelige endpoints

Labels: `type:feature` `area:identity` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-015

Token bucket per IP en per account in Valkey met in-memory fallback (KB-18: verlies acceptabel). Configureerbare limieten; `Retry-After`.

- [ ] Na N mislukte logins → 429 met Retry-After
- [ ] Werkt zonder Valkey (single) met in-memory store
- [ ] Limieten per endpoint configureerbaar
- [ ] Test met Valkey via testcontainers

#### VX-025 · Ciphers: schema en CRUD voor alle MVP-itemtypes

Labels: `type:feature` `area:vault` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-020, VX-015

Tabel `ciphers` (data jsonb met EncStrings, type, folder, favorite, reprompt, revisiondate, deleted_at). Endpoints `/api/ciphers` (create, get, update, delete, bulk). Itemtypes: login (incl. TOTP-veld, URI's), secure note, card, identity, SSH-sleutel.

- [ ] Golden-fixtures per itemtype (extensie en CLI)
- [ ] Autorisatietest: andere gebruiker krijgt 404
- [ ] Onbekende velden van nieuwere clients worden behouden (forward-compat test)
- [ ] Revisiedatum wordt bij elke wijziging bijgewerkt

#### VX-026 · Folders CRUD

Labels: `type:feature` `area:vault` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-025

`/api/folders` met versleutelde naam; verwijderen ontkoppelt ciphers.

- [ ] Golden-fixtures
- [ ] Verwijderen map → ciphers behouden met folder null
- [ ] Autorisatietests

#### VX-027 · Volledige sync-endpoint

Labels: `type:feature` `area:compat` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-025, VX-026

`GET /api/sync` met profiel, mappen, ciphers, collecties (leeg), policies (leeg), sends (leeg), domains. Efficiënte query (geen N+1).

- [ ] Golden-fixture tegen `bw sync` en extensie
- [ ] 5 000 items < 1 s p95 (bench in `tests/load` of criterion)
- [ ] `excludeDomains`-parameter ondersteund

#### VX-028 · Conflictafhandeling via lastKnownRevisionDate

Labels: `type:feature` `area:vault` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-025

Update met verouderde `lastKnownRevisionDate` wordt geweigerd zoals Bitwarden-clients verwachten.

- [ ] Gelijktijdige update-test: tweede krijgt de verwachte fout
- [ ] Clients tonen 'item is gewijzigd' (handmatige check gedocumenteerd)

#### VX-029 · Soft delete, restore en purge-job voor prullenbak

Labels: `type:feature` `area:vault` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-025, VX-009

`PUT /api/ciphers/{id}/delete`, `/restore`, permanente delete; job purge na configureerbaar aantal dagen.

- [ ] Soft-deleted items verschijnen in sync met deletedDate
- [ ] Purge-job verwijdert na N dagen (klok-test)
- [ ] Bulk-varianten ondersteund

#### VX-030 · Master password- en KDF-wijziging, user-key-rotatie

Labels: `type:feature` `area:identity` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-022, VX-025

Endpoints voor wachtwoordwijziging, KDF-wijziging en sleutelrotatie (alle ciphers/mappen opnieuw versleuteld door client, atomair opgeslagen). Na wijziging alle andere sessies intrekken.

- [ ] Rotatie is één transactie: alles of niets (test met fout halverwege)
- [ ] KDF-wijziging naar onder minimum geweigerd
- [ ] Alle refresh tokens behalve huidig apparaat ingetrokken
- [ ] Golden-fixtures

#### VX-031 · /api/config en compatibiliteitsmatrix als data

Labels: `type:feature` `area:compat` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-015

Server-versie en feature flags die clients lezen; matrix in `docs/compat/matrix.toml` gebruikt door tests en docs (KB-09).

- [ ] Config bevat versie en flags uit matrix
- [ ] Matrix wordt in docs gerenderd
- [ ] Test faalt als een flag in code niet in de matrix staat

#### VX-032 · Devices-API: lijst, known-device, deactiveren

Labels: `type:feature` `area:identity` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-022

Device-records met type, naam, laatste activiteit; `GET /api/devices`, known-device-check, deactiveren trekt tokens in.

- [ ] Deactiveren → refresh token van dat apparaat ongeldig
- [ ] Golden-fixtures
- [ ] Nieuwe-apparaatmelding via job (mail-stub)

#### VX-033 · Compat-contractharnas: opgenomen verkeer als golden fixtures

Labels: `type:test` `area:compat` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-016

`tools/compat-recorder` (mitmproxy-addon) neemt clientverkeer op en anonimiseert e-mail, tokens, ciphertext. Test-runner speelt requests af en vergelijkt responsvorm (sleutels, types), niet waarden.

- [ ] Recorder vervangt alle tokens/EncStrings door placeholders (test)
- [ ] Runner meldt ontbrekende/extra velden per endpoint
- [ ] Fixtures voor extensie, desktop en CLI van gepinde versies

#### VX-034 · Compat-watch: maandelijkse test tegen nieuwe Bitwarden-clientreleases

Labels: `type:chore` `area:compat` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-033, VX-031

Workflow die nieuwe releases van `bw` en de webvault detecteert, de compat-suite draait en bij falen een issue opent met label `area:compat`.

- [ ] Workflow maandelijks + handmatig startbaar
- [ ] Issue bevat diff van falende velden
- [ ] Matrix-update via PR

#### VX-035 · Bitwarden-webvault-build serveren met strikte CSP

Labels: `type:feature` `area:compat` `priority:p1` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-027

Externe repo `vaultx-web-vault-build` levert een tarball (06 §1.3). Server serveert statische bestanden onder `/` met CSP, HSTS, `X-Content-Type-Options`, Referrer-Policy, cache-headers.

- [ ] Webvault laadt en kan inloggen/sync (Playwright-smoke)
- [ ] Security-headers aanwezig (test)
- [ ] Versie van webvault zichtbaar in /api/config
- [ ] Licentie- en bronvermelding in NOTICE

#### VX-036 · Autorisatietestsuite per endpoint

Labels: `type:test` `area:server` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-025

Generieke test die per geregistreerde route controleert: zonder token 401, met token van andere gebruiker geen toegang tot andermans resources.

- [ ] Route-registry levert lijst van alle routes
- [ ] Nieuwe route zonder testcase laat CI falen
- [ ] Dekt compat- en /v1-routes


### M2 · Compat-uitbreiding (v0.3–v0.4) — 16 issues

#### VX-040 · Organisaties: aanmaken, org-sleutels, ophalen

Labels: `type:feature` `area:org` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-027

Crate `vaultx-org`. Org met versleutelde org-sleutel per lid (RSA-OAEP met publieke sleutel van het lid). Endpoints zoals clients ze gebruiken. Documenteer KB-10-beperking (geen verificatie van publieke sleutels in Bitwarden-clients).

- [ ] Golden-fixtures voor org aanmaken via webvault
- [ ] Org-sleutel nooit in plaintext opgeslagen (schema-review)
- [ ] Beperking gedocumenteerd in docs/security

#### VX-044 · Object storage-trait met FS- en S3-backend

Labels: `type:feature` `area:storage` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-008

Trait in `vaultx-storage` (put/get/delete/presign). Backends: lokaal FS en S3 (Garage-referentie, KB-05). Keuze `object_store` vs `aws-sdk-s3` vastleggen in ADR.

- [ ] Gedeelde testsuite draait tegen FS en Garage (testcontainers)
- [ ] Presigned URL's met korte TTL
- [ ] ADR geschreven

#### VX-045 · Bijlagen: v2-uploadflow, download, verwijderen

Labels: `type:feature` `area:vault` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-044, VX-025

Bijlage-metadata in DB, blob in object storage; uploadflow zoals huidige clients (verifiëren: direct upload of via server). Max-grootte configureerbaar; quotum per gebruiker/org.

- [ ] 100 MB upload/download via S3 en FS
- [ ] Quotum overschreden → nette fout
- [ ] Verwijderen item verwijdert blobs (job)
- [ ] Golden-fixtures

#### VX-046 · Sends (tekst en bestand)

Labels: `type:feature` `area:vault` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-044, VX-029

Sends met expiry, deletion date, max access count, optioneel wachtwoord (gehasht), verbergen e-mail. Publieke access-endpoints. Purge-job.

- [ ] Send met wachtwoord en max 3 toegangen: vierde faalt
- [ ] Bestands-Send via object storage
- [ ] Purge na deletion date
- [ ] Golden-fixtures web en CLI

#### VX-047 · Mailservice: SMTP, templates, verzending via jobqueue

Labels: `type:feature` `area:notify` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-009

`vaultx-notify::mail` met lettre, HTML+tekst-templates (nl/en), verzending als job met retries. Mailpit in dev/tests.

- [ ] Templates voor verificatie, uitnodiging, nieuw apparaat, 2FA-code
- [ ] Mail-fout → retry; geen secrets in maillogs
- [ ] Integratietest met Mailpit

#### VX-041 · Lidmaatschap: uitnodigen, accepteren, bevestigen

Labels: `type:feature` `area:org` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-040, VX-047

Uitnodiging via mail-token, accepteren door gebruiker, bevestigen door admin (die org-sleutel versleutelt naar publieke sleutel lid). Statussen invited/accepted/confirmed.

- [ ] Volledige flow e2e met twee testaccounts via `bw`/webvault
- [ ] Token eenmalig en verlopend
- [ ] Alleen admin/owner kan bevestigen
- [ ] Audit-hook aangeroepen (stub tot VX-071)

#### VX-042 · Collecties en basisrollen (owner, admin, manager, user)

Labels: `type:feature` `area:org` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-041

Collecties met per-lid/per-groep toegang (read-only, hide-passwords, manage). Rollen zoals Bitwarden-basisrollen.

- [ ] Autorisatiematrix-test per rol × actie
- [ ] Sync levert alleen toegankelijke collecties
- [ ] Golden-fixtures

#### VX-043 · Items delen en verplaatsen naar organisatie

Labels: `type:feature` `area:vault` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-042, VX-025

Share-endpoint: client hervesleutelt item met org-sleutel, server verplaatst atomair en koppelt collecties.

- [ ] Share van item met bijlage werkt (na VX-45)
- [ ] Atomair: bij fout blijft item persoonlijk
- [ ] Golden-fixtures

#### VX-048 · 2FA: TOTP en recovery code

Labels: `type:feature` `area:identity` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-022

Setup/verify/disable TOTP (secret envelope-encrypted server-side), recovery code eenmalig. Login-flow met 2FA-challenge zoals clients verwachten.

- [ ] Login vraagt 2FA en accepteert geldige code (±1 stap)
- [ ] Replay van dezelfde code binnen venster geweigerd
- [ ] Recovery code schakelt 2FA uit en is daarna ongeldig
- [ ] Golden-fixtures

#### VX-049 · 2FA: WebAuthn

Labels: `type:feature` `area:identity` `priority:p1` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-048

Registratie en verificatie met webauthn-rs; meerdere sleutels; Bitwarden-wireformaat.

- [ ] Werkt in Bitwarden-browserextensie (handmatig + Playwright met virtuele authenticator)
- [ ] Sign-count-regressie gelogd
- [ ] Golden-fixtures

#### VX-050 · 2FA: e-mailcode

Labels: `type:feature` `area:identity` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-047, VX-048

E-mail als tweede factor met kortlevende code in Valkey/DB, rate-limited.

- [ ] Code 6 cijfers, TTL 10 min, eenmalig
- [ ] Rate limit op versturen
- [ ] Golden-fixtures

#### VX-051 · Notificatiehub: SignalR/MessagePack over websocket

Labels: `type:feature` `area:notify` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-027

`/notifications/hub` met SignalR-handshake, MessagePack-protocol, keepalive; per node bijgehouden verbindingen; berichten bij cipher/folder/sync-wijzigingen en logout.

- [ ] Bitwarden-extensie ontvangt sync-notificatie na wijziging via CLI
- [ ] Keepalive/timeout volgens SignalR-spec
- [ ] Verbinding geauthenticeerd met access token; verlopen token sluit verbinding

#### VX-052 · Notificatie-fan-out tussen nodes via Valkey pub/sub

Labels: `type:feature` `area:notify` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-051

Publicatie per gebruiker/org-kanaal; elke node levert aan lokale verbindingen (KB-25). Zonder Valkey (single) in-process.

- [ ] Test met twee servers + Valkey: wijziging via node A bereikt client op node B < 2 s
- [ ] Valkey-uitval: geen crash, degradatie gelogd
- [ ] Metrics: verbindingen per node

#### VX-053 · Mobiele push via relay (onderzoek + implementatie)

Labels: `type:spike` `area:notify` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-052

Onderzoek of en hoe Bitwarden-mobiele apps push ontvangen bij self-hosting (push-relay met installation id/key, verifiëren). Implementeer indien haalbaar, anders documenteer beperking.

- [ ] Onderzoeksnotitie in docs/compat
- [ ] Bij implementatie: opt-in config, geen kluisdata in pushpayload
- [ ] Beperking gedocumenteerd indien niet haalbaar

#### VX-054 · Icons-service met SSRF-bescherming

Labels: `type:feature` `area:vault` `priority:p2` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-006

`/icons/{domain}/icon.png` met cache, DNS-resolutie gecontroleerd tegen deny-lijst (RFC 1918, loopback, link-local, metadata 169.254.169.254, IPv6-equivalenten), timeouts, max-grootte. Uit te schakelen.

- [ ] Interne adressen en DNS-rebinding geweigerd (tests)
- [ ] Max 1 MB, timeout 5 s
- [ ] Configuratie `icons.enabled=false` geeft 404

#### VX-055 · Chaos-vroegtest: notificaties en sessies bij node-uitval

Labels: `type:test` `area:deploy` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-052

Vroege versie van chaos-test (R2 uit 07): twee app-nodes achter HAProxy, kill één node, clients reconnecten.

- [ ] Script in tests/chaos draait in nightly
- [ ] Geen verloren writes, clients reconnecten < 10 s


### M3 · Authentik, audit & admin (v0.5–v0.6) — 16 issues

#### VX-060 · OIDC-client tegen Authentik (PKCE, discovery, JWKS)

Labels: `type:feature` `area:authentik` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-022

`vaultx-identity::oidc` met openidconnect-crate; discovery, JWKS-caching met rotatie, PKCE, nonce/state, validatie van iss/aud/exp (KB-29).

- [ ] E2E tegen Authentik-container: authorization code + PKCE
- [ ] Ongeldige iss/aud/nonce geweigerd (tests)
- [ ] JWKS-rotatie zonder herstart

#### VX-061 · Bitwarden-SSO-flow met master-password-unlock

Labels: `type:feature` `area:compat` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-060, VX-042

Endpoints en flow zoals Bitwarden-clients SSO uitvoeren (org identifier, prevalidate, authorize/callback, token met sso-grant; verifiëren tegen huidige clients). Na SSO ontgrendelt de gebruiker met master password (KB-02: Authentik geeft geen sleutels).

- [ ] E2E: SSO-login via Bitwarden-extensie tegen Authentik
- [ ] Master password blijft vereist voor unlock
- [ ] Golden-fixtures van opgenomen SSO-verkeer

#### VX-062 · SSO: account-linking en JIT-provisioning

Labels: `type:feature` `area:authentik` `priority:p1` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-061

Koppeling op `sub` + issuer (niet alleen e-mail); JIT-aanmaak configureerbaar; bestaande lokale accounts alleen koppelen na wachtwoordbevestiging.

- [ ] Koppeling op e-mail alleen niet mogelijk zonder bevestiging (test tegen account-overname)
- [ ] JIT uit → onbekende gebruiker krijgt nette fout
- [ ] Audit-event bij koppeling

#### VX-063 · SCIM 2.0: /Users-endpoint

Labels: `type:feature` `area:authentik` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-041

RFC 7643/7644 Users: create, get, list met filter (`userName eq`), replace, patch, delete (= uitschakelen). Bearer-token per org. Getest tegen Authentik SCIM-provider.

- [ ] SCIM-compliance-tests (subset) groen
- [ ] Authentik-push van 500 gebruikers idempotent
- [ ] Uitgeschakelde gebruiker: tokens ingetrokken
- [ ] Filterparser gefuzzed

#### VX-064 · SCIM 2.0: /Groups-endpoint

Labels: `type:feature` `area:authentik` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-063

Groups met members, PATCH add/remove members; groepen worden VaultX-groepen binnen de org.

- [ ] 50 groepen met lidmaatschap correct na push
- [ ] PATCH-varianten van Authentik ondersteund (verifiëren per versie)
- [ ] Groep verwijderen ontkoppelt collecties

#### VX-065 · Declaratieve groep→org/rol-mapping

Labels: `type:feature` `area:authentik` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-064, VX-042

Config (YAML/API) die Authentik-groepen of claims mapt naar organisaties, rollen en collectietoegang (KB-29). Dry-run toont effect.

- [ ] Mapping-wijziging met dry-run-diff
- [ ] Conflicterende regels → validatiefout
- [ ] Toepassing via job, idempotent

#### VX-066 · Authentik-webhookontvanger voor lifecycle-events

Labels: `type:feature` `area:authentik` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-060, VX-052

Endpoint voor Authentik notification webhooks (user disabled, password changed, logout). Authenticiteit: gedeeld geheim + optionele IP-allowlist + verificatie van de gebeurtenis via Authentik API (webhook-ondertekening verifiëren).

- [ ] Gebruiker uitgeschakeld → tokens ingetrokken en notificatie-logout < 60 s
- [ ] Webhook zonder geldig geheim → 401 en audit
- [ ] Event-replay idempotent

#### VX-067 · Back-channel logout of introspectie-fallback

Labels: `type:feature` `area:authentik` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-066

OIDC back-channel logout indien Authentik-versie het ondersteunt (verifiëren); anders bij elke refresh introspectie/userinfo-check met caching.

- [ ] Logout in Authentik beëindigt VaultX-sessie bij volgende refresh
- [ ] Configurabel interval
- [ ] Test met beide modi

#### VX-068 · Authentik-blueprints in deploy/authentik-blueprints

Labels: `type:build` `area:authentik` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-061, VX-063, VX-066

Blueprints voor OIDC-provider, applicatie, SCIM-provider, property mappings (groepen-claim), notification transport naar VaultX-webhook.

- [ ] Import in schone Authentik maakt werkende integratie (e2e)
- [ ] Placeholders voor URL's/geheimen gedocumenteerd
- [ ] Getest tegen gepinde Authentik-versie

#### VX-069 · Auditlog: schema, hashketen per tenant, DB-rol zonder UPDATE/DELETE

Labels: `type:feature` `area:audit` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-010

Crate `vaultx-audit`. Tabel met `seq`, `prev_hash`, `hash`, event, actor, target, ip, user-agent; append onder `pg_advisory_xact_lock` per tenant; app-rol heeft alleen INSERT/SELECT (KB-26).

- [ ] Gelijktijdige appends leveren een geldige keten (test)
- [ ] UPDATE/DELETE door app-rol faalt (test)
- [ ] Geen secrets of ciphertext in events (schema-review)

#### VX-070 · Audit-eventcatalogus en emissie in alle domeinen

Labels: `type:feature` `area:audit` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-069

Lijst van events (login ok/fail, 2FA, apparaat, item CRUD, delen, org-lidmaatschap, SCIM, admin-acties, policy, export). Emissie in dezelfde transactie als de actie.

- [ ] Catalogus in docs met event-ID's
- [ ] Test: elke mutatie-route emitteert ≥ 1 event
- [ ] Bitwarden event-log-endpoints voor orgs (basis)

#### VX-071 · Ondertekende checkpoints en `vaultx audit verify`

Labels: `type:feature` `area:audit` `priority:p0` `release:mvp` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-070

Periodieke job tekent (tenant, seq, hash) met Ed25519; sleutel buiten DB via `vaultx-keys` (bestand/KMS). CLI verifieert keten en checkpoints, ook offline tegen een export.

- [ ] Gewijzigde rij → verify meldt eerste breukpunt
- [ ] Verwijderde rij → gedetecteerd
- [ ] Checkpointsleutelrotatie ondersteund
- [ ] Exportformaat gedocumenteerd

#### VX-072 · Admin-API en adminrollen met step-up

Labels: `type:feature` `area:server` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-060, VX-070

`/v1/admin/*` voor instance-admins; authenticatie via OIDC met vereiste `acr`/MFA (step-up, KB-29) of lokaal admin-account met WebAuthn. Admins kunnen geen kluisdata ontsleutelen.

- [ ] Admin zonder MFA-claim → 403
- [ ] Alle admin-acties geauditeerd
- [ ] OpenAPI-fragment in docs/api/openapi.yaml

#### VX-073 · Adminconsole-skelet (React/Vite) in web/app met OIDC-login

Labels: `type:feature` `area:admin` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-072, VX-003

`web/app` met route-tree `/admin/*`, TanStack Router/Query, shadcn/Tailwind, OIDC-login (PKCE), API-client uit openapi. Geserveerd door de server onder `/admin` met CSP en SRI.

- [ ] Login via Authentik werkt (Playwright)
- [ ] CSP zonder unsafe-inline; SRI op assets
- [ ] Bundle-budget gecontroleerd in CI

#### VX-074 · Adminconsole: gebruikers, organisaties, apparaten, audit-viewer

Labels: `type:feature` `area:admin` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-073

Lijsten met zoeken/paginering; gebruiker uitschakelen, sessies intrekken; audit-viewer met filters en verify-status.

- [ ] Playwright-scenario's per scherm
- [ ] Acties vragen bevestiging en zijn geauditeerd
- [ ] Toegankelijkheid: axe zonder serious issues

#### VX-075 · Adminconsole: SCIM- en mapping-status

Labels: `type:feature` `area:admin` `priority:p2` `release:mvp` `agent:ready` · Afhankelijk van: VX-074, VX-065

Overzicht SCIM-tokens, laatste sync, fouten; mapping-dry-run vanuit UI.

- [ ] SCIM-token aanmaken/intrekken (token één keer getoond)
- [ ] Dry-run-diff zichtbaar
- [ ] Playwright-test


### M4 · HA, deployment & MVP-release (v0.9) — 14 issues

#### VX-080 · compose/ha-3: Patroni+etcd, Valkey+Sentinel, Garage, 3 app-nodes, HAProxy

Labels: `type:build` `area:deploy` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-013, VX-052, VX-044

`deploy/compose/ha-3/` met per-host compose-bestanden en een all-in-one testvariant. Synchrone replicatie naar ≥ 1 replica (KB-17, KB-20).

- [ ] All-in-one variant start en `/ready` op 3 nodes
- [ ] Patroni-failover werkt (handmatige test gedocumenteerd)
- [ ] Documentatie voor 3 hosts

#### VX-081 · Valkey Sentinel-ondersteuning en degradatiegedrag

Labels: `type:feature` `area:storage` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-080

Client ontdekt primary via Sentinel; bij uitval valt rate limiting terug op lokaal, pub/sub reconnect.

- [ ] Failover-test: geen 5xx buiten reconnect-venster
- [ ] Metrics/logs bij degradatie
- [ ] Config-voorbeelden

#### VX-082 · Readiness/liveness, graceful shutdown en websocket-drain

Labels: `type:feature` `area:server` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-080

`/ready` toetst DB, Valkey (optioneel), object storage; bij shutdown eerst unready, dan websockets sluiten met reconnect-hint.

- [ ] Rolling restart in ha-3 zonder mislukte HTTP-requests (k6)
- [ ] Clients reconnecten naar andere node
- [ ] Documentatie van semantiek

#### VX-083 · Migratiestrategie: expand/contract, migratie onder lease

Labels: `type:chore` `area:storage` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-010, VX-008

Richtlijn + CI-check: migraties compatibel met N-1. `vaultx migrate` neemt lease; app-nodes weigeren te starten bij te nieuw schema.

- [ ] CI draait testsuite van vorige release tegen nieuw schema
- [ ] Gelijktijdige migrate-runs veilig
- [ ] Richtlijn in docs/runbooks

#### VX-084 · Helm-chart basis (externe Postgres/Valkey/S3)

Labels: `type:build` `area:helm` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-082

`deploy/helm/vaultx` met deployments per rol, Service, Ingress, ConfigMap/Secret, values.schema.json; afhankelijkheden extern.

- [ ] `helm lint` en `helm template` in CI
- [ ] Installatie op kind met externe Postgres slaagt (CI)
- [ ] Rollen apart schaalbaar

#### VX-085 · Back-up: PostgreSQL (pgBackRest/WAL-G), object storage, sleutels

Labels: `type:feature` `area:deploy` `priority:p0` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-080

Keuze pgBackRest vs WAL-G in ADR; PITR; bucket-replicatie of -back-up; back-up van signeer-/checkpointsleutels gescheiden van data.

- [ ] Runbook back-up voor single en ha-3
- [ ] PITR gedemonstreerd
- [ ] Sleutelback-up apart en versleuteld

#### VX-086 · Geautomatiseerde restore-test (nightly)

Labels: `type:test` `area:deploy` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-085, VX-071

Nightly: back-up maken, nieuwe stack restoren, kluis via `bw` lezen, `audit verify`.

- [ ] Nightly groen
- [ ] Faalmelding opent issue
- [ ] RTO gemeten en gerapporteerd

#### VX-087 · k6-loadtest-baseline

Labels: `type:test` `area:deploy` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-080

Scenario's login-storm, sync grote kluis, websocket-fan-out in `tests/load`. Doelen vastleggen na eerste meting.

- [ ] Scripts + README
- [ ] Baseline-resultaten in docs
- [ ] Nightly-run met trendgrafiek als artefact

#### VX-088 · Chaos-suite ha-3: primary-kill, partitie, Valkey-failover

Labels: `type:test` `area:deploy` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-080, VX-055

Toxiproxy/Pumba-scenario's met k6-verkeer; verificatie dat bevestigde writes niet verloren gaan.

- [ ] Primary-kill: 0 verloren bevestigde writes, RTO < 60 s
- [ ] Valkey-failover: alleen rate-limit-state verloren
- [ ] Node-kill tijdens lease-houderschap: geen dubbele houder

#### VX-089 · TLS en security headers, CSP-review

Labels: `type:chore` `area:server` `priority:p1` `release:mvp` `security:sensitive` `agent:ready` · Afhankelijk van: VX-035

TLS-termination-richtlijnen (proxy vs ingebouwd rustls), HSTS, CSP, Permissions-Policy, trusted-proxy-config voor X-Forwarded-For.

- [ ] Headers-test op alle statische en API-routes
- [ ] X-Forwarded-For alleen van geconfigureerde proxies vertrouwd (test)
- [ ] Documentatie

#### VX-090 · Upgrade/rollback-runbook en geautomatiseerde upgradetest

Labels: `type:test` `area:deploy` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-083, VX-082

Test: deploy vorige release, data seeden, rolling upgrade naar huidige, verifiëren; rollback-procedure (app terug, schema blijft dankzij expand/contract).

- [ ] CI-job upgrade N-1 → N in compose-ha-3
- [ ] Runbook rollback getest
- [ ] Release-checklist verwijst ernaar

#### VX-091 · Interne securityreview MVP en fuzzing

Labels: `type:security` `area:server` `priority:p0` `release:mvp` `security:threat-model` `agent:ready` · Afhankelijk van: VX-071, VX-063, VX-051

Doorloop threat model (03) als checklist, fuzz-targets voor EncString, JWT, SCIM-filter, SignalR-frames; cargo-audit schoon.

- [ ] Checklist met bevindingen als issues
- [ ] Fuzzers draaien nightly 30 min
- [ ] Geen open high/critical

#### VX-092 · Documentatie: installatie single/ha-3, Authentik-setup, compat-matrix

Labels: `type:docs` `area:docs` `priority:p1` `release:mvp` `agent:ready` · Afhankelijk van: VX-068, VX-080

Docs-site (`web/docs-site`) met installatie, configuratiereferentie (gegenereerd uit `vaultx-config`), Authentik-handleiding, bekende beperkingen.

- [ ] Docs-site bouwt in CI
- [ ] Configreferentie gegenereerd
- [ ] Beperkingen (push, KB-10) expliciet

#### VX-093 · MVP-release v0.9.0

Labels: `type:chore` `area:ci` `priority:p0` `release:mvp` `agent:ready` · Afhankelijk van: VX-086, VX-088, VX-090, VX-091, VX-092

Release-checklist: alle M0–M4-exit-criteria, RC één week in nightly, release notes, ondertekende artefacten.

- [ ] Checklist volledig afgevinkt
- [ ] v0.9.0 getagd met SBOM en handtekening
- [ ] Aankondiging met beperkingen


### M5 · Identiteit & sleutelbeheer (v0.10) — 9 issues

#### VX-100 · Device keys en Trusted Device Encryption (server-side)

Labels: `type:feature` `area:identity` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-093

Opslag van device-gebonden versleutelde user keys en publieke device keys; API's voor TDE zoals Bitwarden-clients verwachten waar compatibel (verifiëren) en via /v1 voor eigen clients (KB-02).

- [ ] Protocolbeschrijving in docs/security vóór implementatie (review)
- [ ] Server bewaart alleen versleutelde sleutels
- [ ] Testvectoren voor wrapping

#### VX-101 · Device-approval: via bestaand apparaat en admin recovery

Labels: `type:feature` `area:identity` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-100

Aanvraag nieuw apparaat → goedkeuring door vertrouwd apparaat (versleutelt user key naar nieuw device key) of org-admin via admin-recovery-sleutel. Fingerprint-weergave aan beide kanten.

- [ ] E2E: nieuw apparaat goedgekeurd zonder master password
- [ ] Fingerprint-phrase getoond en gelijk
- [ ] Verlopen aanvragen na 15 min
- [ ] Volledig geauditeerd

#### VX-102 · Passkey-login met WebAuthn PRF-unlock

Labels: `type:feature` `area:identity` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-100

Registratie van passkey met PRF; user key versleuteld met PRF-afgeleide sleutel opgeslagen; login + unlock in één ceremonie.

- [ ] Chromium-e2e met virtuele authenticator (PRF)
- [ ] Zonder PRF-ondersteuning nette terugval
- [ ] Intrekken passkey verwijdert gewrapte sleutel

#### VX-103 · Emergency access

Labels: `type:feature` `area:vault` `priority:p1` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-093

Uitnodigen vertrouwd contact, wachttijd, view of takeover, weigeren tijdens wachttijd; compat met Bitwarden-clients.

- [ ] Wachttijd afgedwongen (klok-test)
- [ ] Grantor kan weigeren
- [ ] Takeover reset wachtwoord en trekt sessies in
- [ ] Golden-fixtures

#### VX-104 · Passkey-opslag in items (fido2Credentials)

Labels: `type:feature` `area:vault` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-093

Opslag en sync van passkeys in login-items zoals Bitwarden-clients doen.

- [ ] Bitwarden-extensie kan passkey opslaan en gebruiken
- [ ] Sync-fixtures
- [ ] Export/import behoudt passkeys (client-side)

#### VX-105 · LDAP-authenticatie en -synchronisatie

Labels: `type:feature` `area:identity` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-093

LDAP-bind voor login (met master password-unlock) en periodieke sync van gebruikers/groepen als alternatief voor SCIM.

- [ ] E2E tegen OpenLDAP-container
- [ ] StartTLS/LDAPS verplicht standaard
- [ ] Groepsmapping hergebruikt VX-065

#### VX-106 · Generieke OIDC-providers (naast Authentik)

Labels: `type:feature` `area:identity` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-093

Meerdere IdP's per instance/org; getest met Keycloak en een generieke provider.

- [ ] Config per org
- [ ] E2E met Keycloak-container
- [ ] Claim-mapping configureerbaar

#### VX-107 · SAML 2.0 SP

Labels: `type:feature` `area:identity` `priority:p2` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-106

SAML-SP (crate-keuze in ADR, verifiëren volwassenheid `samael`), signature-validatie, metadata-endpoint.

- [ ] E2E met Authentik als SAML-IdP
- [ ] Ongetekende/onjuist getekende assertion geweigerd
- [ ] XML-parser gefuzzed

#### VX-108 · Organisatie-policies (basis): 2FA verplicht, master-password-eisen, SSO verplicht

Labels: `type:feature` `area:org` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-093

Bitwarden-compatibele org-policies die clients afdwingen; server dwingt af waar mogelijk.

- [ ] Policies in sync
- [ ] Server weigert login zonder 2FA bij 2FA-policy
- [ ] Golden-fixtures


### M6 · Organisatiemodel & policy (v0.11) — 10 issues

#### VX-110 · vaultx-policy: Cedar-schema, entities en actions

Labels: `type:feature` `area:policy` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-093

Cedar-schema voor principals (User, Group, ServiceAccount, Device), resources (Organization, Workspace, Collection, Item, Secret) en actions. Policy-opslag en evaluatie (KB-22).

- [ ] Schema gevalideerd in CI
- [ ] Evaluatie-API met context (acr, amr, ip, device_trust)
- [ ] Benchmark p99 < 1 ms

#### VX-111 · Vaste rollen als Cedar-policies en autorisatiemiddleware /v1

Labels: `type:feature` `area:policy` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-110, VX-036

Ingebouwde policies voor owner/admin/manager/user/custom; middleware die elke /v1-route via de engine autoriseert. Bitwarden-compat-autorisatie gebruikt dezelfde beslissingen.

- [ ] Route zonder autorisatie-annotatie laat CI falen
- [ ] Regressiesuite Bitwarden-rollen groen
- [ ] Deny-by-default

#### VX-112 · Policy-API: CRUD, validatie, dry-run

Labels: `type:feature` `area:policy` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-111

/v1/policies met schema-validatie bij opslaan en dry-run ('wie kan X') tegen huidige data.

- [ ] Ongeldige policy → 422 met Cedar-fout
- [ ] Dry-run toont effect zonder te activeren
- [ ] Geauditeerd

#### VX-113 · Step-up via acr/amr-claims in policies

Labels: `type:feature` `area:policy` `priority:p1` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-111, VX-060

Context bevat acr/amr uit IdP-token; policies kunnen MFA eisen voor collecties; client krijgt step-up-fout met hint.

- [ ] Toegang zonder MFA-claim geweigerd met specifieke fout
- [ ] Na step-up toegestaan
- [ ] E2E met Authentik-flow

#### VX-114 · Workspaces (KB-28): model en API

Labels: `type:feature` `area:org` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-111

Workspace tussen Organization en Collection; persoonlijke kluis als impliciete workspace; migratie bestaande collecties naar default-workspace.

- [ ] Migratie expand/contract
- [ ] /v1-endpoints + OpenAPI
- [ ] Bitwarden-clients zien collecties ongewijzigd

#### VX-115 · Teams

Labels: `type:feature` `area:org` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-114

Teams als groepering met rollen, los van SCIM-groepen of eraan gekoppeld.

- [ ] CRUD + lidmaatschap
- [ ] Teams bruikbaar als principal in Cedar
- [ ] Geauditeerd

#### VX-116 · Tags op items

Labels: `type:feature` `area:vault` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-114

Tags als versleutelde labels (client-side) met server-side opaque ID's; filter in /v1.

- [ ] Tag-namen nooit plaintext op server
- [ ] Filter op tag-ID
- [ ] Bitwarden-clients onaangetast

#### VX-117 · Vervallende credentials en verloopnotificaties

Labels: `type:feature` `area:vault` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-110, VX-047

Velden `expires_at` en `rotate_after` (metadata, niet versleuteld — bewuste afweging gedocumenteerd); job stuurt herinneringen; policy kan toegang na expiry blokkeren.

- [ ] Job stuurt mail X dagen vooraf
- [ ] Afweging metadata-lek in docs/security
- [ ] Policy-voorbeeld 'deny after expiry'

#### VX-118 · SIEM-export en WORM-archivering van audit

Labels: `type:feature` `area:audit` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-071

Exporters: JSON over HTTPS (batch), syslog (RFC 5424 over TLS), S3 met Object Lock (KB-26). Cursor per exporter, at-least-once.

- [ ] Export hervat na uitval zonder gaten
- [ ] Object Lock-modus configureerbaar
- [ ] Test tegen Garage/SeaweedFS (Object Lock-ondersteuning verifiëren)

#### VX-119 · OpenAPI /v1: design-first spec, CI-diff, gegenereerde clients

Labels: `type:chore` `area:server` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-111

`docs/api/openapi.yaml` als bron; utoipa-output vergeleken in CI; oasdiff blokkeert breaking changes zonder major-label; generatie `packages/api-client` en Rust-SDK-types.

- [ ] CI faalt bij afwijking implementatie ↔ spec
- [ ] Breaking change detectie
- [ ] Gegenereerde clients in CI gebouwd


### M7 · VaultX-webapp (v0.12) — 10 issues

#### VX-120 · vaultx-crypto-wasm en @vaultx/crypto-wasm

Labels: `type:feature` `area:crypto` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-012

wasm-bindgen-façade, build via `just wasm`, npm-wrapper met typings, SRI-hash, size budget.

- [ ] Zelfde testvectoren slagen in Node/Vitest
- [ ] WASM < 1 MB gz
- [ ] Geen JS-crypto behalve WebCrypto voor RNG/opslag

#### VX-121 · packages/vault-state: sync-engine, versleutelde cache, lock-statusmachine

Labels: `type:feature` `area:web` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-120, VX-119

Gedeeld door webapp en extensie: sync, IndexedDB-cache (versleuteld), lock/unlock/timeout-states, geheugenhygiëne.

- [ ] Statusmachine met unit tests voor alle transities
- [ ] Na lock geen sleutels in JS-objecten (test met heap-snapshot-heuristiek, best effort)
- [ ] Offline lezen uit cache

#### VX-122 · Webapp-shell: routing, login (wachtwoord, SSO, passkey), unlock, autolock

Labels: `type:feature` `area:web` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-121, VX-102, VX-073

Uitbreiding van `web/app` met kluisroutes, loginvarianten en instelbare autolock.

- [ ] Playwright: alle drie loginvarianten
- [ ] Autolock na inactiviteit
- [ ] CSP en SRI blijven intact

#### VX-123 · Kluisweergave en bewerken van alle itemtypes

Labels: `type:feature` `area:web` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-122

Lijst, zoeken (client-side), detail, aanmaken/bewerken voor login, notitie, kaart, identiteit, SSH-sleutel, passkey-weergave, TOTP-codes, wachtwoordgenerator.

- [ ] Playwright per itemtype
- [ ] Clipboard wordt na 30 s gewist (configureerbaar)
- [ ] Wachtwoordgenerator gebruikt CSPRNG via WASM

#### VX-124 · Organisaties, collecties en delen in de webapp

Labels: `type:feature` `area:web` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-123

Org-beheer, uitnodigen/bevestigen met fingerprint-weergave, collecties, toegang.

- [ ] Playwright: org aanmaken, lid uitnodigen, bevestigen, item delen
- [ ] Fingerprint-phrase getoond bij bevestigen

#### VX-125 · Sends en bijlagen in de webapp

Labels: `type:feature` `area:web` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-123

Send aanmaken/beheren, publieke Send-ontvangstpagina, bijlagen up/download met client-side versleuteling.

- [ ] Playwright voor Send en bijlage
- [ ] Ontvangstpagina zonder login, sleutel in URL-fragment

#### VX-126 · Instellingen: 2FA, apparaten, passkeys, emergency access, sessies

Labels: `type:feature` `area:web` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-123, VX-101, VX-103

Account- en beveiligingsinstellingen inclusief device-approvals.

- [ ] Playwright per instelling
- [ ] Device-approval met fingerprint
- [ ] Sessies intrekken

#### VX-127 · Import client-side: Bitwarden JSON/CSV, KeePass XML, 1Password 1PUX

Labels: `type:feature` `area:web` `priority:p2` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-123

Parsers draaien in de browser; niets onversleuteld naar server; foutrapport per item.

- [ ] Fixtures per formaat
- [ ] Grote import (10 000 items) zonder UI-freeze (worker)
- [ ] Export versleuteld en onversleuteld met waarschuwing

#### VX-128 · Toegankelijkheid en i18n (nl, en)

Labels: `type:feature` `area:web` `priority:p2` `release:v1.0` `agent:ready` · Afhankelijk van: VX-123

`packages/i18n`, axe in Playwright, toetsenbordnavigatie.

- [ ] Geen hardcoded strings (lintregel)
- [ ] axe: geen serious/critical
- [ ] nl en en volledig

#### VX-129 · Bitwarden-webvault uitfaseren en server serveert eigen webapp

Labels: `type:chore` `area:web` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-124, VX-125, VX-126

Config-schakelaar; standaard eigen webapp; webvault-build optioneel tot v1.1.

- [ ] Pariteitschecklist afgevinkt
- [ ] Migratie-instructies in docs
- [ ] Webvault nog aan te zetten


### M8 · App-catalogus & proxy-connectors (v0.13) — 10 issues

#### VX-130 · App-catalogus: datamodel en /v1-API

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-119

Applicatie (naam, URL's/hostnames, bron: handmatig/Authentik/NPM), login-methode, gekoppelde items (per referentie, geen plaintext), eigenaar.

- [ ] CRUD + OpenAPI
- [ ] Policy-geautoriseerd
- [ ] Koppeling item ↔ app zonder ontsleuteling

#### VX-131 · Authentik API-client: applicaties, providers, outposts lezen

Labels: `type:feature` `area:authentik` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-130

Read-only service-account-token; synchroniseert Authentik-applicaties naar catalogus met providertype (OIDC/SAML/proxy).

- [ ] Integratietest tegen Authentik-container
- [ ] Token-scope minimaal gedocumenteerd
- [ ] Periodieke sync via job

#### VX-132 · Login-methodeladder: evaluator en advies

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-130, VX-131

Regels die per app de sterkste methode adviseren (KB-03): native SSO > extensie > gateway > JIT; uitleg per advies.

- [ ] Grafana-fixture → advies 'native SSO via Authentik'
- [ ] Legacy-app zonder SSO → 'extensie'
- [ ] Advies met reden in API

#### VX-133 · Proxy Connector-contract (trait) en testkit

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-130

Crate `vaultx-connectors`: discover, plan (diff), apply, rollback, health; golden-file-testkit (KB-04).

- [ ] Contract gedocumenteerd
- [ ] Testkit met fake-proxy
- [ ] Dry-run is standaard

#### VX-134 · NPM-connector: authenticatie en read-only discovery

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-133

Token via `/api/tokens`, lijst proxy hosts via `/api/nginx/proxy-hosts`, mapping naar catalogus.

- [ ] Contracttests tegen ≥ 2 gepinde NPM-versies
- [ ] Read-only modus standaard
- [ ] Credentials NPM als infra-secret opgeslagen

#### VX-135 · NPM-connector: markerblok in advanced_config en dry-run-diff

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-134

Genereer auth_request-blok (Authentik-outpost en/of gateway) tussen markers `# BEGIN VAULTX …`/`# END VAULTX`; handmatige config buiten markers ongewijzigd. Golden files in `deploy/npm-snippets`.

- [ ] Byte-identiek behoud buiten markers (property test)
- [ ] Diff-weergave in API
- [ ] Parser gefuzzed

#### VX-136 · NPM-connector: apply met concurrency-controle en automatische rollback

Labels: `type:feature` `area:connectors` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-135, VX-010

Apply onder lease; vergelijk modified-tijd vóór schrijven; health-check na apply, rollback bij falen.

- [ ] Gelijktijdige wijziging in NPM → apply afgebroken
- [ ] Gefaalde health-check → vorige config hersteld
- [ ] Geauditeerd

#### VX-137 · Traefik-connector (forwardAuth)

Labels: `type:feature` `area:connectors` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-133

Genereer forwardAuth-middleware via file provider of label-snippets; discovery via Traefik API (read-only).

- [ ] E2E met Traefik-container
- [ ] Dry-run-diff
- [ ] Docs-voorbeeld

#### VX-138 · Domein→secret-mapping en catalogus-UI

Labels: `type:feature` `area:web` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-132, VX-136, VX-123

UI voor catalogus, ladderadvies, koppelen van items, connector-plannen bekijken en toepassen.

- [ ] Playwright: Grafana-scenario end-to-end
- [ ] Apply vereist bevestiging en toont diff

#### VX-139 · Optioneel: Authentik-provider/applicatie genereren

Labels: `type:feature` `area:authentik` `priority:p2` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-132

Met apart, beperkt token Authentik-provider en -applicatie aanmaken voor een app uit de catalogus (opt-in).

- [ ] Standaard uit
- [ ] Dry-run toont te maken objecten
- [ ] Rollback verwijdert aangemaakte objecten


### M9 · VaultX Connect & Access Gateway (v0.14) — 9 issues

#### VX-140 · Extensie-skelet (WXT, MV3) voor Chrome, Edge, Firefox

Labels: `type:feature` `area:extension` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-121

`extension/` met WXT gepind, manifest-snapshots per browser, minimale permissies, CSP.

- [ ] Builds voor 3 browsers in CI
- [ ] Manifest-snapshottest faalt bij permissiewijziging
- [ ] Playwright laadt extensie in Chromium

#### VX-141 · Extensie: login, unlock met device key, Authentik-sessiebewustzijn

Labels: `type:feature` `area:extension` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-140, VX-101, VX-102

Unlock via device key/PRF; detectie van actieve Authentik-sessie (via VaultX-token-refresh met SSO, niet via cookies lezen) om re-auth te vermijden.

- [ ] Unlock zonder master password op vertrouwd apparaat
- [ ] Geen toegang tot Authentik-cookies (permissies)
- [ ] Lock bij browser-afsluiten configureerbaar

#### VX-142 · Extensie: catalogusgestuurde autofill

Labels: `type:feature` `area:extension` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-141, VX-130

Matching op catalogus-hostnames (exact/eTLD+1-regels), invul-UI in geïsoleerde iframe, geen autofill in cross-origin iframes zonder bevestiging.

- [ ] Playwright: invullen op testapp
- [ ] Phishing-domein (look-alike) krijgt geen suggestie
- [ ] Content script minimaal en zonder externe code

#### VX-143 · Extensie: store-packaging en signeerpijplijn

Labels: `type:chore` `area:extension` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-142

`wxt zip` per browser, upload naar stores via CI (handmatige goedkeuring), AMO-bronnenpakket.

- [ ] Bèta gepubliceerd in drie stores
- [ ] Reproduceerbare build gedocumenteerd

#### VX-144 · Access Gateway: apart proces, X25519-sleutel, sleutelopslag

Labels: `type:feature` `area:gateway` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-093

Crate `vaultx-gateway`, aparte build `gateway-only` (06 §10). Sleutel in bestand (versleuteld) of PKCS#11/TPM (optioneel). Publieke sleutel gepubliceerd en ondertekend.

- [ ] API-build bevat geen gateway-ontsleutelcode (build-test)
- [ ] Sleutel nooit in logs of DB in plaintext
- [ ] Threat-model-sectie bijgewerkt

#### VX-145 · 'Deel met gateway': client herversleutelt naar gateway-sleutel

Labels: `type:feature` `area:gateway` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-144, VX-123

/v1-API en UI waarmee een gebruiker/org-admin een item expliciet delegeert; client versleutelt opnieuw naar de gateway-publieke sleutel (KB-01). Intrekken verwijdert de kopie.

- [ ] Alleen expliciete opt-in per item
- [ ] Intrekken binnen 1 s effectief
- [ ] Geauditeerd met wie/wat/wanneer

#### VX-146 · Gateway methode 6: sessiedelegatie via auth_request

Labels: `type:feature` `area:gateway` `priority:p1` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-145, VX-135

auth_request-endpoint dat Authentik-identiteit (via outpost-headers of eigen OIDC) koppelt aan gedelegeerde credential en upstream-sessie opzet.

- [ ] E2E: legacy-app achter NPM opent ingelogd
- [ ] Headers van niet-vertrouwde bronnen genegeerd
- [ ] Sessie gebonden aan gebruiker en verloopt

#### VX-147 · Gateway methode 7: credential replay met allowlist

Labels: `type:feature` `area:gateway` `priority:p1` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-146

Form-post-replay naar expliciet geconfigureerde login-URL's; CSRF-token-ophaling; nooit naar niet-allowlisted hosts.

- [ ] E2E met testapp
- [ ] Niet-allowlisted host → geweigerd
- [ ] Plaintext alleen in geheugen, zeroized na gebruik

#### VX-148 · Gateway: audit, NetworkPolicies, threat-model-review

Labels: `type:security` `area:gateway` `priority:p0` `release:v1.0` `security:threat-model` `agent:ready` · Afhankelijk van: VX-147

Audit per gebruik; deploy-artefacten met eigen netwerkpolicy; review van 03-threat-model voor gateway.

- [ ] Elk gebruik geauditeerd met gebruiker, app, methode
- [ ] NetworkPolicy alleen proxy → gateway → app
- [ ] Reviewverslag in docs/security


### M10 · Android (v0.15) — 7 issues

#### VX-150 · UniFFI-bindings voor vaultx-crypto/-sdk en Android-AAR

Labels: `type:feature` `area:android` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-120

`vaultx-crypto-ffi` met UniFFI; `cargo ndk` voor arm64-v8a, armeabi-v7a, x86_64; AAR in `android/core/crypto`.

- [ ] Testvectoren slagen in Android-unit tests
- [ ] CI bouwt AAR met cache
- [ ] Geen sleutels als Kotlin-String

#### VX-151 · Android-projectskelet, multi-module, CI

Labels: `type:chore` `area:android` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-150

Structuur uit 06 §4.3, version catalog, ktlint/detekt, `ci-android.yml` met padfilter.

- [ ] `./gradlew check` groen in CI
- [ ] Modules app/core/feature aanwezig
- [ ] Baseline profile-module

#### VX-152 · Android: login, unlock (biometrie + Keystore), sync, versleutelde offline cache

Labels: `type:feature` `area:android` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-151, VX-101

Login (wachtwoord, SSO, device approval), biometrische unlock met StrongBox waar beschikbaar, Room-cache met versleutelde velden.

- [ ] Offline openen werkt
- [ ] Biometrie-key ongeldig bij nieuwe vingerafdruk
- [ ] Instrumented tests op Gradle Managed Device

#### VX-153 · Android AutofillService

Labels: `type:feature` `area:android` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-152

Autofill Framework met inline suggesties (IME), app-ID ↔ domein-koppeling (Digital Asset Links), browserondersteuning.

- [ ] Testmatrix top-20 apps + Chrome/Firefox
- [ ] Geen autofill bij niet-geverifieerde app ↔ domein zonder bevestiging
- [ ] Instrumented tests

#### VX-154 · Android Credential Manager-provider (passkeys)

Labels: `type:feature` `area:android` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-152, VX-104

Credential Provider voor Android 14+: passkeys aanmaken/gebruiken, wachtwoorden aanbieden.

- [ ] Passkey aanmaken en inloggen op webauthn-testsite
- [ ] Sync met webapp/extensie
- [ ] Android < 14: nette terugval

#### VX-155 · Android: item-bewerking, TOTP, bijlagen, generator

Labels: `type:feature` `area:android` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-152

Compose-schermen voor alle itemtypes, TOTP-weergave, bijlagen downloaden/openen.

- [ ] Compose UI-tests
- [ ] Screenshots voorkomen (FLAG_SECURE) op gevoelige schermen

#### VX-156 · Android-releasepijplijn

Labels: `type:chore` `area:android` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-153, VX-154

Signing via CI-secrets, Play internal track, reproduceerbare build-onderzoek voor F-Droid.

- [ ] Interne bèta live
- [ ] Signing-keys niet in repo
- [ ] F-Droid-haalbaarheid gedocumenteerd


### M11 · Machine-secrets & Key Connector (v0.16) — 6 issues

#### VX-160 · Infra-secrets: envelope-encryptie met root KEK, Shamir-unseal, per-tenant DEK's

Labels: `type:feature` `area:secrets` `priority:p0` `release:v1.0` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-093

`vaultx-keys`: KEK-providers (Shamir-unseal, bestand), DEK's per tenant, KEK-rotatie door herwrappen (KB-27). Cluster blijft 'sealed' tot unseal.

- [ ] Unseal met k-van-n shares (test)
- [ ] KEK-rotatie zonder data-herversleuteling
- [ ] Sealed node → /ready 503 met reden

#### VX-161 · Service accounts en scoped API-tokens

Labels: `type:feature` `area:secrets` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-111, VX-160

Service accounts per org/workspace; tokens met scope, expiry, IP-restrictie; alleen hash opgeslagen.

- [ ] Token één keer getoond
- [ ] Scope via Cedar afgedwongen
- [ ] Intrekken direct effectief

#### VX-162 · Machine-secrets en JIT-delivery-API (methode 8)

Labels: `type:feature` `area:secrets` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-161

Projecten en secrets voor machines; ophalen via token met korte TTL-leveringen en audit; client-side ontsleuteling voor E2E-secrets of server-side voor infra-klasse (KB-01).

- [ ] Beide geheimklassen gescheiden in schema en API
- [ ] Elke levering geauditeerd
- [ ] OpenAPI bijgewerkt

#### VX-163 · vx-CLI: login, get, run -- met env-injectie

Labels: `type:feature` `area:cli` `priority:p0` `release:v1.0` `security:sensitive` `agent:ready` · Afhankelijk van: VX-162

`vaultx-cli` op basis van `vaultx-sdk`: device flow login, `vx get`, `vx run -- cmd` (env alleen voor kindproces).

- [ ] Geen secrets op schijf of in shellhistorie
- [ ] Exit-code kindproces doorgegeven
- [ ] Binaries voor Linux/macOS/Windows

#### VX-164 · vaultx-sdk (Rust) en TS-SDK

Labels: `type:feature` `area:cli` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-163

Publieke SDK's onder Apache-2.0 (zie 06 §10.1), voorbeelden voor CI (GitHub Actions-action).

- [ ] Gepubliceerd op crates.io/npm (pre-release)
- [ ] Voorbeeld GitHub Action
- [ ] Docs

#### VX-165 · Key Connector opt-in modus

Labels: `type:feature` `area:identity` `priority:p1` `release:v1.0` `security:crypto-review` `security:threat-model` `agent:assist` · Afhankelijk van: VX-160, VX-061

Expliciete lagere-beveiligingsmodus (KB-02, D3): server houdt sleutels voor SSO-only orgs; alleen door instance-admin te activeren, zichtbaar voor alle leden, threat model bijgewerkt.

- [ ] Activering vereist instance-admin + bevestiging
- [ ] Leden zien banner/indicator
- [ ] Threat-model-sectie en waarschuwing in docs


### M12 · Kubernetes, DR & v1.0-release (v1.0) — 6 issues

#### VX-170 · Helm volledig: CNPG, Valkey Sentinel, Garage, PDB's, NetworkPolicies, HPA

Labels: `type:build` `area:helm` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-084, VX-144

Subcharts/CR's voor CNPG `Cluster`, Valkey met Sentinel, Garage; ServiceMonitor; aparte gateway-deployment.

- [ ] Installatie ha-3 op kind in CI
- [ ] NetworkPolicies: default deny + expliciete paden
- [ ] values.schema.json volledig

#### VX-171 · ha-5-profiel en async DR-replica

Labels: `type:build` `area:helm` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-170

values-ha-5.yaml; CNPG replica cluster in tweede site; runbook promotie.

- [ ] DR-promotie getest op twee kind-clusters
- [ ] RPO/RTO gemeten

#### VX-172 · Zero-downtime-upgradetest in CI (kind)

Labels: `type:test` `area:helm` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-170, VX-090

Helm upgrade vorige → huidige onder k6-verkeer.

- [ ] 0 mislukte requests
- [ ] Migratie onder lease
- [ ] Rapport als artefact

#### VX-173 · DR-runbooks en game day

Labels: `type:docs` `area:deploy` `priority:p1` `release:v1.0` `agent:ready` · Afhankelijk van: VX-171

Runbooks: site-uitval, corrupte back-up, sleutelverlies, unseal-quorum kwijt; game day met verslag.

- [ ] Runbooks in docs/runbooks
- [ ] Game-day-verslag met acties

#### VX-174 · Externe security-audit en remediatie

Labels: `type:security` `area:server` `priority:p0` `release:v1.0` `security:threat-model` `security:crypto-review` `agent:assist` · Afhankelijk van: VX-148, VX-165, VX-152

Audit door externe partij (pentest + crypto-review van TDE, gateway, Key Connector, audit-keten).

- [ ] Rapport ontvangen
- [ ] Alle critical/high opgelost en hertest
- [ ] Publieke samenvatting

#### VX-175 · v1.0-release: docs-site, compat-matrix, release notes

Labels: `type:chore` `area:ci` `priority:p0` `release:v1.0` `agent:ready` · Afhankelijk van: VX-172, VX-173, VX-174, VX-156, VX-143

Releasechecklist M5–M12, release-branch `release/v1.0`, ondersteuningsbeleid.

- [ ] Checklist afgevinkt
- [ ] v1.0.0 getagd en ondertekend
- [ ] Ondersteuningsbeleid gepubliceerd


### E · Enterprise (v2.x) — 15 epics

- **VX-E01 [Epic] Secrets Engines-framework + SSH-CA-engine** — Engine-trait, leases/revocatie; SSH-CA met korte certificaten (KB-08).
- **VX-E02 [Epic] PostgreSQL- en Kubernetes-dynamische-secrets-engines** — Tijdelijke DB-rollen, K8s TokenRequest.
- **VX-E03 [Epic] OpenBao/Vault-backend-engine** — Integratie met bestaande OpenBao/Vault in plaats van vervanging.
- **VX-E04 [Epic] Wachtwoordrotatie-connectors** — Rotatie voor ondersteunde apps (LDAP, PostgreSQL, Linux, Windows/AD).
- **VX-E05 [Epic] Secret discovery** — Scan van Git-repo's, K8s-secrets, CI-variabelen; rapportage zonder secrets op te slaan.
- **VX-E06 [Epic] HSM/KMS/PKCS#11 voor root KEK en signeersleutels** — AWS/GCP/Azure KMS, PKCS#11 (KB-27).
- **VX-E07 [Epic] Multi-regio DR** — Actief/passief over regio's, failover-runbooks.
- **VX-E08 [Epic] NATS JetStream-eventstreaming** — Optionele component voor SIEM/multi-regio (KB-19).
- **VX-E09 [Epic] Key transparency** — Append-only ondertekende log van publieke sleutels, verificatie in eigen clients (KB-10).
- **VX-E10 [Epic] Crypto v2 (XChaCha20-Poly1305 met AD)** — Voor accounts met uitsluitend eigen clients; migratiepad (KB-10).
- **VX-E11 [Epic] iOS-app** — Swift/SwiftUI, credential provider, passkeys, dezelfde Rust-core (KB-15).
- **VX-E12 [Epic] AI-assistent (metadata-only)** — Client-side hygiëne-analyse, optioneel lokaal LLM; nooit plaintext naar server.
- **VX-E13 [Epic] Compliance-rapportage** — Rapporten (toegang, hygiëne, audit) voor ISO 27001/NIS2-ondersteuning.
- **VX-E14 [Epic] Extra proxy-connectors: Caddy, plain nginx, Kubernetes Ingress/Gateway API** — Implementaties van het connectorcontract (KB-04).
- **VX-E15 [Epic] Externe PDP (OPA) als alternatief voor ingebedde Cedar** — Optionele integratie (KB-22).
---

## 7. Validatie van de JSON-bestanden

Uitgevoerd op 2026-10-04 met python3:

- `python3 -m json.tool 08-issues.json` en `python3 -m json.tool 08-milestones.json`: **geldig**.
- Controlescript: 163 issues, 14 milestones; alle ID's uniek; elk object heeft exact de velden `id, title, body, labels, milestone, depends_on`; elke `milestone` bestaat in `08-milestones.json`; elke `depends_on` verwijst naar een bestaand ID; geen cycli (`graphlib.TopologicalSorter`); in de array staat elke afhankelijkheid vóór het issue dat ervan afhangt; geen afhankelijkheid wijst naar een latere milestone; elke body bevat ≥ 1 acceptatiecriterium (`- [ ]`). **Resultaat: ok.**

## 8. Opmerkingen bij kernbeslissingen

1. **Deel E en teamgrootte**: de v1.0-backlog (67 issues, waarvan veel `p0`) is groot voor 2–4 ontwikkelaars (zie 07 §7.1). M10 (Android) en VX-165 (Key Connector) zijn de natuurlijke kandidaten om naar v1.1 te verschuiven als dat nodig blijkt; dat vraagt een expliciet besluit van Jonas, deze backlog volgt Deel E.
2. **Mobiele push (VX-053)** is als `spike` opgenomen omdat Deel E "notificaties over meerdere nodes" in MVP noemt, terwijl push naar Bitwarden-mobiele apps waarschijnlijk van een externe relay afhangt (verifiëren).
3. **Licentie SDK vs. crypto-core (VX-164)**: zie 06 §10.1; het issue gaat uit van Apache-2.0 voor de SDK, wat pas klopt als `vaultx-crypto` ook permissief gelicenseerd wordt.
4. **Aparte gateway-build (VX-144)** volgt de verfijning van KB-24 uit 06 §10.2.
5. **Externe audit (VX-174)** staat niet expliciet in Deel E, maar is opgenomen als v1.0-voorwaarde (07 §7.3).
