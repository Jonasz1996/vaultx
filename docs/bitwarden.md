# Kluis voor Bitwarden®-clients (fase 2, uitgebreid in fase 9)

VaultX heeft een persoonlijke wachtwoordkluis die werkt met de officiële
Bitwarden-apps, -browserextensies en -CLI. VaultX is niet verbonden met
Bitwarden, Inc.; het spreekt alleen hetzelfde protocol.

Wat werkt: inloggen, synchroniseren, items en mappen, importeren, bijlagen,
websitepictogrammen, tweestapsverificatie met een authenticator-app en het master
password (of de sleutelafleiding) wijzigen. Organisaties, delen, Sends, live sync
en noodtoegang komen later (zie onderaan).

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

## Importeren (fase 9)

Importeren werkt vanuit de apps zoals bij Bitwarden: in de browserextensie of
desktopapp via *Importeren*, of met de CLI:

```bash
bw import bitwardenjson export.json      # bv. een export uit Vaultwarden
bw import lastpasscsv lastpass.csv       # elk formaat dat de Bitwarden-apps kennen
```

De app versleutelt alles vóór het verzenden; VaultX bewaart de items en mappen in
één keer (alles of niets). Een import van een paar duizend items gaat in één verzoek;
nginx laat daarvoor tot 110 MB door op `/api/ciphers/import`.

## Websitepictogrammen (fase 9)

De apps tonen naast elk item het pictogram van de website. VaultX haalt het zelf
op (`/icons/<domein>/icon.png`), dus geen externe dienst ziet welke sites in je
kluis staan. Ter bescherming van je netwerk:

- privé-adressen (192.168.x.x, 10.x, ...) enkel voor domeinen die VaultX kent uit
  je NPM-koppelingen of de catalogus, zodat je eigen apps toch een pictogram krijgen;
- het adres wordt gecontroleerd vlak voor de verbinding, ook na een redirect;
- geen SVG, maximaal 256 kB, 5 seconden; resultaten worden een week bewaard.

`VAULTX_VAULT_ICONS=false` zet dit uit (de apps tonen dan een standaardicoon en
VaultX maakt geen uitgaande verbindingen). De backend heeft wel uitgaande toegang
op poort 80/443 nodig.

## Bijlagen (fase 9)

Bijlagen toevoegen, openen en verwijderen werkt in alle apps en in de CLI
(`bw create attachment --file ... --itemid ...`). De app versleutelt het bestand
en de naam; VaultX bewaart de versleutelde blob in PostgreSQL (dus mee in de
nachtelijke back-up). Downloaden gebeurt via een link met een kort geldig,
ondertekend token, omdat de apps daar geen login meesturen.

| Variabele | Standaard | |
|---|---|---|
| `VAULTX_VAULT_ATTACHMENT_MAX_MB` | `100` | Maximum per bestand (Bitwarden: 500) |
| `VAULTX_VAULT_ATTACHMENT_STORAGE_MB` | `2048` | Totaal per gebruiker |

Wie een eigen nginx-site gebruikt, moet de location voor
`^/api/ciphers/(import$|[^/]+/attachment)` met `client_max_body_size 110m`
overnemen uit `deploy/debian/nginx-vaultx.conf` (`update.sh` doet dat zelf).

## Tweestapsverificatie (fase 9)

Op **Mijn kluis → Tweestapsverificatie**: master password invullen, de QR-code
scannen met een authenticator-app (Aegis, 2FAS, Google Authenticator, Bitwarden
Authenticator, ...) en een eerste code invullen. Je krijgt dan eenmalig een
**herstelcode**: bewaar die buiten de kluis.

Daarna vragen de Bitwarden-apps na het master password een code. Met *Onthoud mij*
slaat een apparaat dat 30 dagen over. De CLI: `bw login <e-mail> --method 0 --code 123456`.

- Een code werkt maar één keer; foute codes tellen mee voor de vergrendeling
  (10 fouten = 15 minuten dicht, samen met foute master passwords).
- Het geheim staat versleuteld met `VAULTX_SECRET_KEY` in de database.
- Telefoon kwijt: log in een app in met de herstelcode als code (dat zet
  tweestapsverificatie uit), of zet ze uit op Mijn kluis met master password en
  herstelcode.
- Telefoon én herstelcode kwijt: een beheerder zet het uit met
  `DELETE /api/v1/users/{id}/vault-2fa`, of op de CT als root:
  `set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend` en
  `/opt/vaultx/venv/bin/python -m app.cli reset-vault-2fa jij@jouwdomein.be`.
  Het master password blijft altijd nodig.

Dit geldt voor de Bitwarden-apps, extensies en CLI. In de VaultX-webinterface zelf
beschermt Authentik je login (en voor gewone accounts het VaultX-wachtwoord).

## Master password wijzigen (fase 9)

Op **Mijn kluis → Master password wijzigen**. Je browser pakt je sleutel opnieuw in
met het nieuwe wachtwoord; je items, mappen, bijlagen en notities blijven gewoon
leesbaar. Je kan er ook overstappen van PBKDF2 naar **Argon2id** (3 iteraties,
64 MiB, 4 threads, zoals de Bitwarden-standaard), wat kraken van een gelekte hash
veel duurder maakt. Na de wijziging worden alle apparaten afgemeld; log daar
opnieuw in met het nieuwe wachtwoord.

Argon2id draait in de browser als WebAssembly; daarom staat `'wasm-unsafe-eval'`
in de Content-Security-Policy (dat laat geen JavaScript-`eval` toe).

## Master password vergeten

Niemand kan het herstellen, ook een beheerder niet. Op **Mijn kluis** kan je de
kluis verwijderen en opnieuw activeren; alle items gaan dan verloren en alle
apparaten worden afgemeld.

## Bekende beperkingen van deze versie

Bewust nog niet gebouwd, in deze volgorde voorgesteld:

1. **Live sync** (notifications-hub): nu synchroniseren de apps periodiek of bij openen.
2. **Send** (tekst of bestand tijdelijk delen via een link).
3. **Organisaties en collecties** (gedeelde kluizen per VaultX-organisatie en -team),
   daarna **noodtoegang**.
4. **API-sleutel** voor de CLI (`bw login --apikey`), sleutelrotatie van de user key.
5. Tweestapsverificatie via e-mail, WebAuthn/YubiKey of Duo.
6. Bitwarden-**v2-accounts** (COSE, signing keys, security state): het datamodel
   heeft er al een kolom voor, de endpoints nog niet.

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
terugzetten, verwijderen, verplaatsen, ook in bulk), `/api/ciphers/import`,
`/api/ciphers/{id}/attachment/...` (v2, upload, download, renew, verwijderen),
`/api/folders` en `/icons/{domein}/icon.png`.

VaultX-eigen API voor de webinterface: `GET/POST/DELETE /api/v1/me/vault`,
`POST /api/v1/me/vault/unlock`, `POST /api/v1/me/vault/password`,
`POST /api/v1/me/vault/2fa/{setup,enable,disable}`,
`POST /api/v1/me/vault/devices/{id}/revoke` en (beheerder)
`DELETE /api/v1/users/{id}/vault-2fa`.
