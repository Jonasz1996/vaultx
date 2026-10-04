# VaultX — Kernbeslissingen en uitgedaagde aannames

Status: voorstel ter review door Jonas · Datum: 2026-10-04 · Versie 0.1

Dit document is de bron van waarheid voor alle andere VaultX-documenten (01 t/m 10). Waar een ander document hiervan afwijkt, geldt dit document. Elke beslissing heeft een ID (`KB-xx`) zodat roadmap, issues en ADR's ernaar kunnen verwijzen.

---

## Deel A — Uitgedaagde aannames

De opdracht bevat een aantal aannames die elkaar tegenspreken of technisch niet houdbaar zijn. Die moeten vóór het ontwerp expliciet worden opgelost.

### A1. "Zero-knowledge E2E-kluis" en "server logt automatisch in" sluiten elkaar uit

Bitwarden-compatibiliteit betekent: de server ziet nooit plaintext; alleen de client kan ontsleutelen (master password → KDF → master key → user key). Methodes 2 (headers), 6 (proxy-sessiedelegatie) en 7 (credential replay) vereisen dat *een serverproces* het wachtwoord in plaintext kent.

**Oplossing (KB-01):** twee strikt gescheiden geheimklassen.

| Klasse | Wie kan ontsleutelen | Gebruik |
|---|---|---|
| **Persoonlijke/organisatiekluis (E2E)** | Alleen clients van gemachtigde gebruikers | Standaard voor alles; Bitwarden-compatibel |
| **Gedelegeerde secrets** | Alleen de geïsoleerde **VaultX Access Gateway** (eigen X25519-sleutelpaar, idealiter in HSM/TPM) | Opt-in per item, door een gebruiker of org-admin expliciet "gedeeld met gateway" — de client versleutelt het item opnieuw naar de publieke sleutel van de gateway |
| **Infrastructuur-secrets** | Server-side met envelope encryption (KEK in KMS/HSM/Shamir-unseal) | Dynamische secrets, service accounts, rotatie, SSH-CA, K8s |

De API-nodes kunnen gedelegeerde secrets *nooit* ontsleutelen. Alleen de gateway kan dat, in geheugen, per request, met audit. Dit houdt de E2E-belofte eerlijk: wat niet gedelegeerd is, blijft zero-knowledge.

### A2. "Authentik-sessie = kluis open" is cryptografisch onmogelijk zonder extra ontwerp

SSO bewijst *wie* je bent, maar levert geen ontsleutelsleutel op. Bitwarden lost dit op met *Trusted Device Encryption* en *Key Connector*.

**Oplossing (KB-02):** Authentik authenticeert, het apparaat ontsleutelt.
- Elk apparaat krijgt een **device key** (in TPM/Secure Enclave/Android Keystore/WebCrypto non-extractable) die de user key omhult.
- Nieuw apparaat: goedkeuring vanaf een bestaand vertrouwd apparaat, of door een org-admin via *admin recovery* (org-publieke sleutel), of master password.
- Ontgrendelen op een vertrouwd apparaat: biometrie/PIN/**passkey met WebAuthn PRF-extensie**.
- **Key Connector** (server houdt sleutels) wordt aangeboden als expliciet gemarkeerde *lagere beveiligingsmodus* voor organisaties die echte "SSO = alles open" willen; dan is een gecompromitteerde Authentik wél fataal (zie threat model).

Resultaat: met een actieve Authentik-sessie *en* een vertrouwd apparaat is de ervaring vrijwel frictieloos, zonder dat Authentik ooit sleutels kan vrijgeven.

### A3. Voor de meeste interne apps is "VaultX logt in" niet het juiste antwoord

Grafana, Gitea/Forgejo, Portainer, Proxmox, Nextcloud, ArgoCD, Jellyfin enz. ondersteunen OIDC, SAML of proxy-header-auth. Een wachtwoord laten injecteren voor een app die native SSO kan, is slechtere beveiliging en meer complexiteit.

**Oplossing (KB-03):** een **login-methodeladder**. VaultX' app-catalogus kiest per applicatie de sterkste beschikbare methode:

1. **Native SSO via Authentik** (OIDC/SAML/proxy-header). VaultX detecteert, adviseert en kan de Authentik-provider/applicatie en NPM-config genereren. Geen wachtwoord nodig.
2. **Extensie-geassisteerde login** (methode 5) met Authentik-gestuurde ontgrendeling. Standaard voor apps zonder SSO.
3. **Gateway-gedelegeerde login** (methodes 6/7, via de Access Gateway) voor legacy apps zonder SSO en zonder extensie (kiosk, gedeelde accounts). Opt-in, isolatie, audit.
4. **Just-in-time secret delivery** (methode 8) voor machines, CLI's, CI/CD en infra.

### A4. Nginx Proxy Manager heeft géén pluginsysteem

NPM is een Node.js-app zonder extensiepunten. Een "NPM-plugin" bouwen betekent NPM forken, wat onhoudbaar is.

**Oplossing (KB-04):** VaultX bouwt een **Proxy Connector-framework** aan de VaultX-kant:
- De NPM-connector gebruikt NPM's REST API (`/api/nginx/proxy-hosts`, token via `/api/tokens`) om proxy hosts te ontdekken en schrijft per host een gegenereerd blok in het veld *Custom Nginx Configuration* (`advanced_config`), afgebakend met markers zodat handmatige config behouden blijft.
- Dat blok voegt `auth_request` naar de Authentik-outpost en/of de VaultX Access Gateway toe.
- Hetzelfde connectorcontract wordt geïmplementeerd voor **Traefik** (forwardAuth/labels), **Caddy** (forward_auth), plain **nginx** (include-bestanden) en **Kubernetes Ingress/Gateway API**. NPM is de eerste, niet de enige.
- Risico: NPM's API is niet formeel geversioneerd. Mitigatie: contracttests per NPM-versie, read-only modus, dry-run met diff.

### A5. MinIO is geen veilige standaardkeuze meer

MinIO heeft in 2025 de community-editie sterk ingeperkt (beheerconsole verwijderd, distributie van community-binaries/images gestopt, repository in onderhoudsmodus). *Verifieer de actuele status vóór implementatie.*

**Oplossing (KB-05):** VaultX praat uitsluitend **S3-API** (plus een lokale bestandssysteem-backend voor 1 node). Referentie-implementaties: **Garage** (lichtgewicht, geo-gedistribueerd, AGPL) of **SeaweedFS** (Apache-2.0) voor self-hosted HA; elke S3-compatibele dienst (bestaande MinIO, Ceph RGW, AWS S3, Hetzner, Wasabi) werkt. Bijlagen zijn client-side versleuteld, dus de opslag hoeft niet vertrouwd te worden.

### A6. Redis/Valkey is niet geschikt voor correcte distributed locking

Redlock biedt geen veiligheidsgarantie bij klokverschuiving, GC-pauzes en failover. Voor een wachtwoordkluis (sleutelrotatie, organisatie-sleutelwissels, migraties) is een dubbele lock-houder onacceptabel.

**Oplossing (KB-06):** locking via **PostgreSQL** (lease-tabel met **fencing tokens** en `pg_advisory_xact_lock` voor korte kritieke secties). Valkey wordt alleen gebruikt voor zaken waar verlies acceptabel is: rate limiting, kortlevende challenges, pub/sub voor push-notificaties, cache.

### A7. "Next.js" voegt aanvalsoppervlak toe zonder voordeel voor een E2E-kluis

Server-side rendering heeft geen zin als de server de data niet mag zien. Een Node-server in het pad van de kluis-UI is extra aanvalsoppervlak.

**Oplossing (KB-07):** **React + TypeScript + Vite** als statische SPA, geserveerd door de Rust-backend met strikte CSP, SRI en zonder third-party scripts. Next.js eventueel alleen als statische export voor de documentatiesite (of Astro/Docusaurus).

### A8. Alles zelf bouwen is niet realistisch

Vaultwarden is jaren werk van een ervaren community en dekt slechts een deel van deze scope. HashiCorp Vault/OpenBao dekt dynamische secrets al uitstekend.

**Oplossing (KB-08):**
- **Clients:** in MVP de bestaande Bitwarden-clients gebruiken (extensies, desktop, mobiel, CLI en de Bitwarden-webvault via een open-source build zoals Vaultwarden dat doet). Eigen clients komen later, alleen waar ze echte meerwaarde hebben.
- **Dynamische secrets:** een *Secrets Engine*-pluginmodel met enkele ingebouwde engines (SSH-CA, PostgreSQL, Kubernetes TokenRequest) **en** een OpenBao-backend-engine, zodat organisaties met OpenBao/Vault dat niet hoeven te vervangen.
- **Nieuwe codebase in Rust** in plaats van een Vaultwarden-fork: Vaultwarden is single-node georiënteerd (lokale websocket-hub, SQLite-first, Rocket/Diesel). Vaultwarden (AGPL-3.0) is wel de referentiespecificatie voor de Bitwarden-API, en code mag onder AGPL met bronvermelding worden hergebruikt.

### A9. De Bitwarden-API is een bewegend, ongedocumenteerd doel

De Bitwarden-clients worden maandelijks uitgebracht en wijzigen regelmatig API-velden, crypto-types en feature flags (`/api/config`).

**Oplossing (KB-09):** compat-laag als **aparte module** (`vaultx-compat-bitwarden`) bovenop het eigen domeinmodel, met:
- een expliciete *compatibiliteitsmatrix* (ondersteunde clientversies),
- **contracttests** tegen opgenomen client-verkeer en tegen de officiële Bitwarden CLI in CI,
- een maandelijkse "compat watch"-taak die nieuwe clientreleases test.
De eigen VaultX-API (`/v1/...`) is de primaire, gedocumenteerde, stabiele API.

### A10. Het Bitwarden-cryptomodel heeft bekende zwaktes tegenover een kwaadwillende server

Bekend uit publiek onderzoek: KDF-parameters worden niet geauthenticeerd (server kan zwakkere KDF opdringen), AES-CBC+HMAC zonder binding aan itemcontext, publieke sleutels van organisaties/leden worden niet geverifieerd (sleutelinjectie bij delen/noodtoegang).

**Oplossing (KB-10):**
- Eigen VaultX-clients dwingen **KDF-minima** af (Argon2id m≥64 MiB, t≥3, p≥4) en weigeren downgrades.
- **Key transparency light:** publieke sleutels van gebruikers en organisaties worden vastgelegd in een append-only, ondertekende log; eigen clients verifiëren fingerprints en tonen een waarschuwing bij wijziging. Bitwarden-clients profiteren hier niet van (gedocumenteerde beperking).
- **Crypto v2** voor accounts die uitsluitend eigen clients gebruiken: XChaCha20-Poly1305 met associated data (item-ID, veldnaam). Bitwarden-compatibele accounts blijven op type 2 (AES-256-CBC-HMAC-SHA256).

### A11. "1, 3 en 5 nodes" betekent niet "alle componenten op elke node"

Quorum-systemen (etcd, Patroni, Valkey Sentinel, Garage) hebben elk hun eigen regels. Een 5-node cluster met 5 Postgres-replica's is verspilling.

**Oplossing (KB-11):** applicatienodes schalen horizontaal en onafhankelijk; stateful componenten hebben vaste profielen (zie KB-20).

---

## Deel B — Definitieve technologiekeuzes

| ID | Onderdeel | Keuze | Belangrijkste reden | Afgewezen |
|---|---|---|---|---|
| KB-12 | Backend | **Rust** (tokio, axum, sqlx, tower) | Geheugenveiligheid zonder GC, `zeroize` voor sleutelmateriaal, één taal met de gedeelde crypto-core, Vaultwarden als Rust-prior-art | Go: sneller ontwikkelen en sterk K8s-ecosysteem, maar GC kan geheimen niet betrouwbaar wissen en er zou een tweede taal naast de Rust-crypto-core nodig zijn |
| KB-13 | Crypto-core | **Rust-crate `vaultx-crypto`**, gecompileerd naar WASM (web/extensie) en via **UniFFI** naar Kotlin/Swift | Eén geauditeerde implementatie voor alle clients (zelfde aanpak als Bitwarden's eigen SDK) | Losse implementaties per platform |
| KB-07 | Web-UI | **React 19 + TypeScript + Vite**, TanStack Router/Query, Radix UI/shadcn, Tailwind | Statische SPA, strikte CSP | Next.js (zie A7) |
| KB-14 | Android | **Kotlin + Jetpack Compose**, Credential Manager (passkeys, Android 14+ provider), Autofill Framework, BiometricPrompt + Keystore/StrongBox, Rust-core via UniFFI | Native API's zijn verplicht voor autofill en passkeys | Flutter/React Native: zwakkere toegang tot Autofill en Credential Provider |
| KB-15 | iOS (fase 2) | Swift + SwiftUI, AuthenticationServices (credential provider, passkeys), dezelfde Rust-core | | |
| KB-16 | Database | **PostgreSQL 17** | Transacties, RLS, advisory locks, LISTEN/NOTIFY, volwassen HA | MySQL, SQLite (alleen voor dev/tests niet ondersteund: één dialect houdt het eenvoudig) |
| KB-17 | DB-HA | **CloudNativePG** op Kubernetes; **Patroni + etcd** op VM's/Docker | CNPG is de standaard Postgres-operator op K8s; Patroni de standaard daarbuiten | Patroni binnen K8s (werkt, maar CNPG integreert beter) |
| KB-18 | Cache/pub-sub | **Valkey 8** (BSD-3), Sentinel voor HA | Echte open-source licentie, drop-in Redis-protocol | Redis (licentiewissels sinds 2024; Redis 8 heeft weer AGPL, maar Valkey heeft de bredere community-governance). Redis blijft protocolcompatibel bruikbaar |
| KB-05 | Object storage | **S3-API**; referentie **Garage**, alternatief SeaweedFS; lokale FS voor 1 node | Zie A5 | MinIO als verplichte component |
| KB-19 | Messaging | **Geen NATS in MVP/v1.** Jobs via Postgres-queue (`FOR UPDATE SKIP LOCKED`), fan-out via Valkey pub/sub. NATS JetStream is een optionele enterprise-component voor event-streaming (SIEM, multi-regio) | Elke extra stateful component kost operationele complexiteit | NATS als kerncomponent |
| KB-06 | Locking | PostgreSQL-leases met fencing tokens | Zie A6 | Redlock |
| KB-21 | Licentie | **AGPL-3.0** voor server en eigen clients; Apache-2.0 voor SDK's/connectors | Beschermt self-hosted open-source, compatibel met Vaultwarden-hergebruik | MIT (laat closed SaaS-forks toe) |
| KB-22 | Policy engine | **Cedar** (Rust-native, formeel geverifieerde semantiek) ingebed in de server | Rust-native, snel, analyseerbaar | OPA/Rego (extra sidecar, Go) — wel optioneel als externe PDP |
| KB-23 | Observability | OpenTelemetry (traces, metrics), Prometheus-endpoints, gestructureerde JSON-logs; nooit secrets of ciphertext in logs | | |

---

## Deel C — Architectuurbeslissingen

**KB-20 Clusterprofielen**

| Profiel | App-nodes | PostgreSQL | Valkey | Object storage | Doel |
|---|---|---|---|---|---|
| `single` | 1 (alle rollen) | 1 instantie | optioneel (in-memory fallback) | lokale FS | homelab, kleine teams |
| `ha-3` | 3 | 1 primary + 2 replica's (Patroni/CNPG, synchrone replicatie naar ≥1) | 3 (1 primary + 2 replica's + 3 Sentinels) | Garage 3 nodes, replicatiefactor 3 | productie MKB |
| `ha-5` | 5+ (horizontaal) | 1 primary + 2 replica's (+ optioneel async DR-replica in tweede site) | 3 + Sentinels | Garage 3–5 nodes | enterprise |

**KB-24 Eén binary, meerdere rollen:** `vaultx serve --roles=api,notifications,worker,gateway`. In `single` draaien alle rollen in één proces; in HA worden ze als aparte deployments geschaald. De **gateway** draait altijd in een eigen trust zone (eigen proces, eigen netwerkpolicy, eigen sleutel) zodra gedelegeerde secrets actief zijn.

**KB-25 Stateless app-nodes:** geen lokale state behalve cache. Sessies zijn kortlevende access tokens (JWT, EdDSA, 5–15 min) + roterende refresh tokens in PostgreSQL. Websocket/SignalR-verbindingen worden per node bijgehouden, notificaties via Valkey pub/sub verdeeld.

**KB-26 Auditlog met tamperdetectie:** append-only tabel, elke entry bevat `prev_hash` (SHA-256 hashketen per tenant), periodiek een **ondertekend checkpoint** (Ed25519, sleutel buiten de database) en optionele export naar WORM-opslag (S3 Object Lock) of SIEM. De database-rol van de app heeft geen `UPDATE/DELETE` op de audittabel.

**KB-27 Sleutelhiërarchie server-side:** Root KEK (KMS/HSM/PKCS#11, of Shamir-unseal à la OpenBao in self-hosted modus) → per-tenant DEK's → velden. Rotatie van KEK zonder herversleuteling van data (alleen DEK's herwrappen).

**KB-28 Multi-tenancy:** `Instance → Organization → Workspace → Collection/Folder → Item`. Teams en rollen hangen aan Organization. Persoonlijke kluis is een impliciete persoonlijke workspace. RBAC via vaste rollen + Cedar-policies voor fijnmazige regels.

**KB-29 Authentik-trustmodel (samenvatting):**
- Authentik is **identity provider en lifecyclebron**, nooit sleutelbeheerder (behalve in expliciete Key Connector-modus).
- Integratiekanalen: OIDC (login, met PKCE), **SCIM 2.0** (Authentik SCIM-provider pusht gebruikers en groepen naar VaultX), **Authentik API** (applicatie-catalogus, outposts, providers lezen; optioneel providers aanmaken met een beperkt service-account-token), **notification webhooks** (events zoals logout, wachtwoordreset, gebruiker uitgeschakeld), OIDC back-channel logout waar de Authentik-versie dat ondersteunt, anders token-introspectie bij elke refresh.
- Groepsmapping: Authentik-groepen → VaultX organisaties/teams/rollen via declaratieve mapping (claims of SCIM-groepen).
- Gedelegeerde autorisatie: Authentik-flows kunnen "step-up" afdwingen (bv. MFA vóór toegang tot gevoelige collecties) via `acr`/`amr`-claims die VaultX-policies controleren.

**KB-30 Bitwarden-compat scope:** identity (`/identity/connect/token`, prelogin), sync, ciphers, folders, organisaties/collecties, attachments, Sends, emergency access, notificaties (`/notifications/hub`, SignalR/MessagePack), icons, devices, 2FA (TOTP, WebAuthn, e-mail), SSO (OIDC-flow zoals de Bitwarden-clients die verwachten). Nieuwe VaultX-itemtypes (API-sleutels, certificaten, dynamische secrets) verschijnen in Bitwarden-clients als **secure note met gestructureerde custom fields** (alleen-lezen projectie).

---

## Deel D — Open vragen voor Jonas

1. **Licentie:** AGPL-3.0 voorgesteld. Akkoord, of liever Apache-2.0 (laat commerciële closed forks toe)?
2. **Doelgroep v1.0:** homelab/MKB (single + ha-3) als eerste prioriteit, of meteen enterprise (ha-5, multi-regio)? Voorgesteld: homelab/MKB eerst.
3. **Key Connector-modus** ("Authentik-login = kluis open, server houdt sleutels"): aanbieden als opt-in, of helemaal niet? Voorgesteld: opt-in vanaf v1.0, met duidelijke waarschuwing.
4. **Repository:** welke GitHub-repository (nieuw, bv. `Jonasz1996/vaultx`) voor fase 10?

---

## Deel E — Releasescope (bindend voor PRD, roadmap en issues)

**MVP (v0.x, "Bitwarden-compatibele HA-kluis met Authentik"):**
- Rust-server, rollen api/notifications/worker in één binary; profielen `single` en `ha-3`.
- Bitwarden-compat: registratie/login (master password, Argon2id/PBKDF2 KDF), sync, ciphers (login, notitie, kaart, identiteit, SSH-sleutel), mappen, bijlagen (S3/FS), TOTP-codes in items, 2FA (TOTP, WebAuthn), organisaties + collecties (basis), Sends, device-beheer, notificaties over meerdere nodes.
- Onbekende/nieuwe cipher-velden (o.a. `fido2Credentials` voor passkeys) worden ongewijzigd bewaard en teruggegeven, zodat passkeys die Bitwarden-clients aanmaken niet verloren gaan; volledige passkey-ondersteuning volgt in v1.0. Functies die pas later komen (bv. emergency access) geven een nette 'uitgeschakeld'-respons via `/api/config`.
- Mobiele push: Bitwarden-clients via websockets wanneer actief; push via de Bitwarden-relay (vergt registratie bij Bitwarden, verifiëren) is opt-in en niet in de MVP; eigen push via de VaultX-app in v1.0.
- Bitwarden-clients (extensies, Android/iOS, desktop, CLI) + Bitwarden-webvault-build als UI; eigen minimale **adminconsole** (React).
- Authentik: OIDC-login (SSO) met master password-unlock, SCIM 2.0-provisioning, groep→org/rol-mapping.
- Auditlog met hashketen en ondertekende checkpoints; Postgres-leases; Postgres-jobqueue.
- Docker Compose (single, ha-3) en Helm-chart (basis); back-up en restore gedocumenteerd en getest.

**v1.0 ("VaultX-onderscheidend"):**
- Trusted Device Encryption + passkey/PRF-unlock (KB-02); emergency access; passkey-opslag; LDAP en generieke OIDC/SAML; RBAC + Cedar-policy engine; workspaces, teams, tags; vervallende credentials; uitgebreide audit + SIEM-export.
- Eigen React-webapp (vervangt Bitwarden-webvault).
- **App-catalogus** + login-methodeladder (KB-03); **Proxy Connector-framework** met NPM-connector (detectie, domein→secret mapping, gegenereerde auth_request-config, dry-run) en Traefik-connector.
- **VaultX Connect** browserextensie (Chrome/Edge/Firefox, Manifest V3) als companion: Authentik-sessiebewuste unlock, catalogus-gestuurde autofill, gateway-login.
- **Access Gateway** (methodes 6/7) voor gedelegeerde secrets, opt-in.
- Android-app (Kotlin) met Autofill, Credential Manager-passkeys, biometrie, offline cache.
- Service accounts + API-tokens + JIT secret delivery (CLI/SDK, methode 8); Key Connector opt-in.
- Kubernetes: Helm + CNPG + Valkey Sentinel + Garage, ha-5-profiel, DR-runbooks, zero-downtime upgrades.

**Enterprise (v2.x):**
- Dynamische secrets (Secrets Engines: SSH-CA, PostgreSQL, Kubernetes, OpenBao-backend); wachtwoordrotatie-connectors; secret discovery (Git-repo's, K8s, CI-variabelen); HSM/KMS/PKCS#11; multi-regio DR; NATS-eventstreaming; key transparency; crypto v2; iOS-app; AI-assistent (metadata-only, lokaal LLM optioneel, client-side hygiene-analyse); compliance-rapportage.

---

## Deel F — Aanscherpingen na review van fase 1–9 (2026-10-04)

Bij het uitwerken van de documenten kwamen spanningen met deel A–E naar boven (elk document heeft onderaan een sectie "Opmerkingen bij kernbeslissingen"). De technische aanscherpingen hieronder zijn **overgenomen** en gaan voor op tegenstrijdige passages in 01–09. Wijzigingen die de productscope raken staan apart als **voorstel** en wachten op Jonas.

### F1. Overgenomen technische aanscherpingen

| ID | Wijzigt | Aanscherping |
|---|---|---|
| KB-01a | KB-01 / A1 | Een plaintext-wachtwoord server-side is nodig voor methodes **1, 6 en 7**. Methode 2 (pure header-auth) heeft geen wachtwoord nodig: de app vertrouwt een identiteitsheader. Methode 1 hoort in trede 3 van de ladder (KB-03). Gedelegeerde secrets moeten opnieuw worden versleuteld zodra het bronitem wijzigt (client-side trigger + "verouderd"-status). |
| KB-04a | KB-04 | nginx staat één `auth_request` per location toe. De Access Gateway roept daarom zelf de Authentik-outpost aan (geketende validatie). **Prototype verplicht vóór planning van M8/M9.** |
| KB-05a | KB-05 | Bitwarden-clients uploaden bijlagen via de API-nodes (streaming naar S3), niet via presigned URL's; presigned URL's alleen voor eigen clients. Garage heeft (verifiëren) geen S3 Object Lock: WORM-doel voor audit is een andere S3-dienst of SIEM. |
| KB-06a | KB-06 | Achter PgBouncer alleen transactie-advisory locks; `LISTEN` gebruikt een eigen, niet-gepoolde verbinding. Beveiligingskritische challenges (WebAuthn, OTP, device-goedkeuring) staan in PostgreSQL, niet alleen in Valkey. |
| KB-10a | KB-10 | Crypto v2 sluit aan bij het v2-formaat dat Bitwarden zelf ontwikkelt (COSE/XChaCha20-Poly1305, verifiëren) in plaats van een eigen formaat. **Lichte key-pinning** (fingerprints van org-/ledensleutels lokaal vastgelegd) al in v1.0; volledige key transparency in Enterprise. De MVP-documentatie vermeldt expliciet dat een kwaadwillende server- of DB-beheerder met alleen Bitwarden-clients de E2E-garantie actief kan aanvallen (sleutelinjectie, KDF-downgrade). |
| KB-16a | KB-16 | Geen SQLite, ook niet voor dev/tests (testcontainers met PostgreSQL). UUIDv7 wordt in de applicatie gegenereerd. |
| KB-17a | KB-17 | Patroni (alleen buiten K8s) vereist een **etcd-cluster van 3 nodes**; VaultX levert een eigen Patroni-hulpimage (onderhoudslast geaccepteerd). Synchrone replicatie is quorum-gebaseerd (`ANY 1`); niet-strikt in `ha-3`, strikt in `ha-5`. |
| KB-18a | KB-18/KB-20 | Valkey Sentinel is optioneel in `ha-3` (Valkey bevat alleen verliesbare data), standaard aan in `ha-5`. |
| KB-21a | KB-21 | `vaultx-crypto` (en WASM/FFI-bindings) krijgt **Apache-2.0**, anders zijn Apache-SDK's in de praktijk AGPL. Server en apps blijven AGPL-3.0. |
| KB-24a | KB-24 | De gateway wordt als aparte `gateway-only`-build uitgeleverd (ontsleutelcode niet op API-nodes) en krijgt geen databaserol; hij haalt gedelegeerde secrets via een smalle mTLS-API. |
| KB-26a | KB-26 | De signing key voor audit-checkpoints staat in een ander beheerdomein (KMS/HSM of offline), en checkpoints worden extern gekopieerd (SIEM/WORM). Retentie via een aparte onderhoudsrol die alleen partities mag droppen. |
| KB-27a | KB-27 | **Auto-unseal via KMS/TPM is standaard** voor `ha-*`; Shamir-unseal alleen voor `single`. Sleutelmateriaal (KEK) leeft in een aparte `keyservice`-rol, niet in elke API-node. De pepper staat apart offline in escrow. |
| KB-28a | KB-28/KB-30 | Workspaces worden in Bitwarden-clients getoond via een extra, door de eigen client versleuteld `compat_name` (`Workspace/Collectie`). Nieuwe itemtypes krijgen hun `bw_projection` client-side; Bitwarden-clients kunnen die alleen lezen. |
| KB-30a | KB-30 | Emergency access is v1.0 (deel E gaat voor); MVP geeft een nette uitgeschakeld-respons. Teamtabellen bestaan al in MVP (SCIM-groepen). `/api/config` meldt een Bitwarden-serverversie; de compatibiliteitsmatrix is een releasedeliverable. Websocket/SignalR-verbindingen sluiten bij tokenverloop of intrekking. |
| KB-31 | nieuw | VaultX treedt op als **RFC 8693 token-exchange-service** met een eigen signing key (aparte sleutel, korte levensduur, audience-gebonden, gelogd). |
| KB-32 | nieuw | Een via SCIM aangemaakte gebruiker kan org-items pas ontsleutelen na *bevestiging* (org-sleutel naar zijn publieke sleutel versleuteld). Standaard: bevestiging door een admin-client; automatische bevestiging alleen via een vertrouwd admin-apparaat met key-pinning (v1.0). |
| KB-33 | nieuw | Service accounts krijgen een eigen sleutelpaar; toegang tot E2E-collecties = collectiesleutel versleuteld naar dat sleutelpaar (v1.0). |
| KB-34 | nieuw | De webapp wordt door de eigen extensie gecontroleerd tegen een ondertekend release-manifest (v1.0), zodat een gecompromitteerde app-node geen gewijzigde webassets kan serveren zonder detectie. |
| KB-35 | nieuw | De geautomatiseerde restore-test voor Compose hoort al in de MVP. Een **externe security-audit** is een voorwaarde voor v1.0. |

### F2. Voorstellen die op Jonas wachten (productscope)

1. **Methode 6 (reverse proxy-sessiedelegatie, XL)** uit v1.0 halen naar Enterprise; v1.0 levert gateway-login via methode 7.
2. **Android en/of Key Connector naar v1.1** als het team uit 2 ontwikkelaars bestaat (zie 07 §4.2).
3. **Four-eyes-goedkeuring** voor gevoelige admin-acties (Key Connector inschakelen, admin recovery, policywijzigingen) in v1.0 in plaats van Enterprise.
4. **Mobiele push**: Bitwarden-apps gebruiken (verifiëren) Bitwarden's push-relay; voorstel: opt-in, standaard uit, als bekende beperking gedocumenteerd.
