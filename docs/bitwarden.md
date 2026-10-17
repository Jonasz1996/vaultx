# Kluis voor Bitwarden®-clients (fase 2)

VaultX heeft een persoonlijke wachtwoordkluis die werkt met de officiële
Bitwarden-apps, -browserextensies en -CLI. VaultX is niet verbonden met
Bitwarden, Inc.; het spreekt alleen hetzelfde protocol.

Dit is de dunste bruikbare versie: inloggen, synchroniseren en items en mappen
beheren in een persoonlijke kluis. Organisaties, delen, bijlagen, Sends,
tweestapsverificatie en noodtoegang komen later (zie onderaan).

## Hoe het werkt

1. Je logt in op VaultX via Authentik en opent **Mijn kluis**.
2. Je kiest daar een master password. Je **browser** leidt daar de sleutels uit
   af (PBKDF2-SHA256, 600.000 iteraties, salt = je e-mailadres) en stuurt alleen
   versleutelde sleutels en een afgeleide hash naar VaultX. Het master password
   zelf verlaat je browser nooit.
3. In een Bitwarden-app kies je *Self-hosted*, vult de VaultX-URL in en logt in
   met je e-mailadres en master password.

Een kluis hoort altijd bij een VaultX-gebruiker, en dus bij een Authentik-
identiteit. Een kluis aanmaken vanuit de Bitwarden-apps ("Account aanmaken")
kan niet; de server verwijst dan naar VaultX.

### Authentik blijft de baas

Bij elk access token en elke verversing controleert VaultX of de gebruiker nog
actief is. Wordt iemand in VaultX gedeactiveerd, dan werken zijn apps meteen
niet meer: sync faalt, verversen faalt en opnieuw inloggen wordt geweigerd. Dat
staat ook in de auditlog (`vault.login` met uitkomst `denied`).

## Een app verbinden

HTTPS is verplicht: de Bitwarden-clients weigeren een `http://`-server. Achter
Nginx Proxy Manager is dat al zo.

| Client | Instelling |
|---|---|
| Browserextensie, desktop, mobiel | Bij inloggen: *Ingelogd op* → **Self-hosted** → server-URL `https://vaultx.jouwdomein.be` |
| CLI | `bw config server https://vaultx.jouwdomein.be` en `bw login jij@jouwdomein.be` |

Naast `/api` moet ook `/identity` naar de backend gaan. De meegeleverde
nginx-configuraties (`docker/nginx/default.conf.template` en
`deploy/debian/nginx-vaultx.conf`) doen dat. Wie al een eigen nginx-site heeft,
moet de regel `location ~ ^/(api|auth|health|identity)(/|$)` overnemen.

Getest met: **Bitwarden CLI 2026.9.1** (volledige flow in `e2e/bitwarden_e2e.py`,
draait ook in CI). De browserextensies en apps gebruiken dezelfde clientcode
en hetzelfde protocol, maar zijn nog niet automatisch getest.

## Wat de server ziet en bewaart

| Gegeven | Hoe opgeslagen |
|---|---|
| Master password | Nooit ontvangen |
| Login-hash van de client | Nog eens gehasht: PBKDF2-SHA256, 600.000 iteraties, eigen salt |
| User key, private key | Versleuteld door de client (EncString type 2), onleesbaar voor VaultX |
| Items en mappen | Versleuteld door de client; VaultX bewaart ze ongewijzigd |
| Itemtype, map, favoriet, prullenbak, datums | Leesbaar (nodig om te synchroniseren) |

Verder:

- Access tokens zijn JWT's (HS256, sleutel afgeleid van `VAULTX_SECRET_KEY`),
  standaard 60 minuten geldig. Refresh tokens staan alleen als SHA-256 in de
  database, per apparaat. Een apparaat dat 30 dagen niets van zich liet horen,
  moet opnieuw inloggen.
- Na 10 foute master passwords op rij wordt de kluis 15 minuten vergrendeld.
- Een onbekend e-mailadres krijgt bij prelogin dezelfde antwoorden en dezelfde
  rekentijd als een bekend adres.
- Elke login (ook mislukte) en elke wijziging aan items en mappen komt in de
  auditlog, zonder inhoud.

Instellingen (omgevingsvariabelen):

| Variabele | Standaard | |
|---|---|---|
| `VAULTX_VAULT_ENABLED` | `true` | `false` zet `/identity` en de Bitwarden-`/api` uit |
| `VAULTX_VAULT_ACCESS_TOKEN_MINUTES` | `60` | Levensduur access token |
| `VAULTX_VAULT_DEVICE_IDLE_DAYS` | `30` | Daarna opnieuw inloggen |
| `VAULTX_VAULT_MAX_FAILED_LOGINS` | `10` | Foute pogingen voor vergrendeling |
| `VAULTX_VAULT_LOCKOUT_MINUTES` | `15` | Duur van de vergrendeling |

## Master password vergeten

Niemand kan het herstellen, ook een beheerder niet. Op **Mijn kluis** kan je de
kluis verwijderen en opnieuw activeren; alle items gaan dan verloren en alle
apparaten worden afgemeld.

## Bekende beperkingen van deze versie

Bewust nog niet gebouwd, in deze volgorde voorgesteld:

1. **Organisaties en collecties** (gedeelde kluizen per VaultX-organisatie en -team).
2. **Master password of KDF wijzigen**, sleutelrotatie.
3. **Bijlagen** en **Sends**.
4. **Tweestapsverificatie** voor de kluis, en **live sync** (notifications-hub;
   nu synchroniseren de apps periodiek of bij openen).
5. Bitwarden-**v2-accounts** (COSE, signing keys, security state): het datamodel
   heeft er al een kolom voor, de endpoints nog niet.
6. **Websitepictogrammen** (`/icons`): de apps tonen nu een standaardicoon.

## Compatibiliteit bijhouden

De Bitwarden-clients veranderen maandelijks. VaultX meldt in `/api/config`
serverversie `2026.6.0`; de clients zetten op basis daarvan functies aan.
Werkwijze bij een nieuwe clientversie: de versie in de CI-job `bitwarden-e2e`
verhogen, `e2e/bitwarden_e2e.py` draaien met `E2E_SERVER_LOG=info`, en in de
serverlog kijken naar 404's op endpoints die de nieuwe client verwacht.
Zo is bijvoorbeeld `POST /api/accounts/key-management/user-key-id` ontdekt: CLI
2026.9.1 weigert in te loggen zonder dat endpoint.

## Endpoints

`/identity/accounts/prelogin/password` (en het oude `/prelogin`),
`/identity/connect/token` (`password` en `refresh_token`),
`/api/config`, `/api/sync`, `/api/accounts/profile`, `/api/accounts/revision-date`,
`/api/accounts/key-management/user-key-id`, `/api/devices`, `/api/devices/knowndevice`,
`/api/ciphers` (aanmaken, lezen, wijzigen, gedeeltelijk wijzigen, prullenbak,
terugzetten, verwijderen, verplaatsen, ook in bulk) en `/api/folders`.

VaultX-eigen API voor de webinterface: `GET/POST/DELETE /api/v1/me/vault` en
`POST /api/v1/me/vault/devices/{id}/revoke`.
