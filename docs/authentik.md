# Authentik koppelen aan VaultX

VaultX logt in via OpenID Connect (authorization code + PKCE) en leest de
Authentik-groepen om beheerders en lidmaatschappen te bepalen. Deze pagina
beschrijft de provider in Authentik. Een kant-en-klare versie als blueprint
staat in [`docker/authentik/blueprints/vaultx-dev.yaml`](../docker/authentik/blueprints/vaultx-dev.yaml);
die is getest tegen Authentik 2026.8.3.

## Provider (Applications → Providers → OAuth2/OpenID Provider)

| Veld | Waarde |
|---|---|
| Authorization flow | `default-provider-authorization-implicit-consent` (of explicit) |
| Client type | **Confidential** |
| Grant types | **authorization_code** (sinds 2026.x verplicht aan te vinken; zonder dit weigert Authentik met `invalid_request`) |
| Redirect URI (Authorization, strict) | `https://vaultx.jouwdomein.be/auth/callback` |
| Redirect URI (Logout, strict) | `https://vaultx.jouwdomein.be/login?logged_out=1` |
| Signing key | een certificaat, bv. `authentik Self-signed Certificate`. **Verplicht**: VaultX aanvaardt enkel asymmetrisch ondertekende tokens (RS/ES/PS), geen HS256 met het client secret |
| Scopes | `openid`, `email`, `profile` (de standaard `profile`-mapping levert de claim `groups`) |
| Subject mode | standaard (`hashed_user_id`) is goed; VaultX koppelt gebruikers op (issuer, sub) |
| Logout URI | `https://vaultx.jouwdomein.be/auth/backchannel-logout` |
| Logout method | **Back-channel** |
| Include claims in id_token | aan |

Open je VaultX ook op een ander adres (bv. `https://vaultx.lan` of
`http://192.168.0.50`)? Inloggen met een VaultX-wachtwoord werkt daar zonder meer.
Voor Authentik op dat adres zet je het in `VAULTX_EXTRA_ORIGINS` (kommagescheiden) en
voeg je in de provider per adres een extra redirect-URI toe (Authorization, strict)
`https://vaultx.lan/auth/callback` en (Logout, strict) `https://vaultx.lan/login?logged_out=1`.
Op een adres dat niet in die lijst staat, verbergt de loginpagina de Authentik-knop; VaultX
springt nooit vanzelf naar `VAULTX_PUBLIC_URL`. De loginpagina en de wachtwoordlogin hebben
Authentik en NPM niet nodig, dus inloggen via het LAN-IP werkt ook als die plat liggen.

Over http (zonder NPM ervoor, bv. `http://192.168.0.50`) zet VaultX de sessiecookie zonder
Secure-vlag, anders bewaart de browser hem niet. Dat gebeurt enkel voor privé- en loopback-IP's
en voor http-adressen in `VAULTX_EXTRA_ORIGINS`; een domeinnaam over http krijgt altijd een
Secure-cookie. Wachtwoord en sessie gaan dan wel onversleuteld over je LAN.

Maak daarna een **Application** (slug bv. `vaultx`) met deze provider. De
issuer staat op de provider-pagina, bv.
`https://auth.jouwdomein.be/application/o/vaultx/`; neem hem exact over
(met slash) in `VAULTX_OIDC_ISSUER`.

## Groepen

| Authentik-groep | Effect in VaultX |
|---|---|
| `vaultx-admins` (instelbaar met `VAULTX_OIDC_ADMIN_GROUPS`) | instantiebeheerder: ziet alle gebruikers, organisaties en de volledige audit |
| `vaultx:<org>` | lid van organisatie `<org>` |
| `vaultx:<org>:admin` / `:owner` / `:member` | organisatierol |
| `vaultx:<org>/<team>` | lid van team `<team>` (en dus ook van de organisatie) |
| `vaultx:<org>/<team>:maintainer` | teamrol maintainer |

`<org>` en `<team>` zijn de slugs uit VaultX. Groepen voor organisaties of
teams die (nog) niet bestaan, worden genegeerd en verschijnen in de audit als
`user.idp_groups_unmatched`. Bij meerdere groepen voor dezelfde organisatie
wint de hoogste rol.

De synchronisatie gebeurt bij **elke login**. Lidmaatschappen uit groepen
(bron "Authentik" in de UI) kunnen niet in VaultX gewijzigd worden; handmatige
lidmaatschappen (bron "Manueel") laat de synchronisatie altijd ongemoeid.
Uitzetten kan met `VAULTX_OIDC_GROUP_SYNC=false`.

## Meteen ingelogd

Ben je al bij Authentik aangemeld, dan zit je zonder klik in VaultX. Opent een
browser zonder VaultX-sessie een pagina, dan:

1. probeert VaultX eerst **stil** bij Authentik (`prompt=none`). Is de browser
   daar aangemeld, dan komt hij meteen terug en is hij binnen, zonder
   Authentik-scherm;
2. antwoordt Authentik `login_required` (niet aangemeld), dan:
   - met de gewone login aan (`VAULTX_LOCAL_LOGIN_ENABLED=true`, standaard):
     de loginpagina, met het formulier en de knop "Inloggen met Authentik";
   - met `VAULTX_OIDC_AUTO_REDIRECT=true` (standaard aan als de gewone login
     uit staat): meteen het aanmelden bij Authentik, en daarna terug naar de
     pagina die je opende.

Een stille poging die niet lukt, is geen fout en komt niet in de auditlog. De
loginpagina doet hoogstens twee automatische pogingen per minuut, zodat er
nooit een lus tussen VaultX en Authentik ontstaat.

**Noodtoegang:** `https://vaultx.example.be/login?lokaal=1` toont altijd de
gewone loginpagina, zonder door te sturen. Ligt Authentik plat, dan toont de
loginpagina "Authentik is momenteel niet bereikbaar" en werkt de gewone login
gewoon verder. In Authentik zelf hoeft niets te veranderen; dit werkt enkel op
adressen waarop Authentik-login kan (`VAULTX_PUBLIC_URL` en
`VAULTX_EXTRA_ORIGINS`), op andere adressen (bv. het LAN-IP) stuurt VaultX
nooit door.

| Instelling | Standaard | Betekenis |
|---|---|---|
| `VAULTX_OIDC_SILENT_LOGIN` | `true` | eerst stil proberen (prompt=none) |
| `VAULTX_OIDC_AUTO_REDIRECT` | leeg = aan als de gewone login uit staat | zonder Authentik-sessie meteen naar het aanmelden |

## Kluis openen met Authentik (VaultX Invuller)

De extensie [VaultX Invuller](invuller.md) kan de kluis openen zonder master
password zolang je via Authentik bij VaultX aangemeld bent. Dat vraagt:

- een VaultX-sessie die via Authentik begon (niet met het wachtwoord van
  VaultX, niet via een API-sleutel of de Bitwarden-apps);
- **MFA** in die Authentik-login. VaultX leest dat uit de claim `amr` van het
  ID-token (`mfa`, `otp`, `hwk`, `swk` of `sc`). In Authentik: zet een stage
  "Authenticator validation" in de authenticatie-flow en geef de gebruiker een
  authenticator-app of beveiligingssleutel. De standaard-flow doet dat al
  zodra de gebruiker een apparaat heeft;
- een Authentik-login van hoogstens 12 uur geleden (`auth_time`).

| Instelling | Standaard | Betekenis |
|---|---|---|
| `VAULTX_DEVICE_UNLOCK` | `true` | Uit = geen enkel apparaat opent nog met Authentik |
| `VAULTX_DEVICE_UNLOCK_REQUIRE_MFA` | `true` | MFA in Authentik verplicht |
| `VAULTX_DEVICE_UNLOCK_MAX_AUTH_AGE_HOURS` | `12` | Hoe oud de Authentik-login mag zijn |

Per gebruiker staat het aan of uit bij **Mijn kluis → Ontgrendelen met
Authentik**; daar staan ook de apparaten, met "laatst geopend", en kan je ze
intrekken. Uitzetten of je master password wijzigen trekt alle apparaten in.
Elke opening en elke weigering staat in de auditlog.

De sleutel blijft op het apparaat: VaultX bewaart enkel de user key,
versleuteld met een willekeurige sleutel van de extensie. VaultX alleen kan
de kluis dus niet openen, en de extensie alleen ook niet.

## Uitloggen

- Uitloggen in VaultX trekt de VaultX-sessie in en stuurt de browser naar het
  `end_session_endpoint` van Authentik (RP-initiated logout).
- Eindigt de sessie in Authentik (uitloggen, sessie verwijderd door een
  beheerder), dan roept Authentik `/auth/backchannel-logout` aan en trekt
  VaultX de bijhorende sessie in. Authentik moet VaultX daarvoor kunnen
  bereiken op de Logout URI.

## API-clients

Naast de browsersessie aanvaardt de API `Authorization: Bearer <access token>`
met een access token van dezelfde provider (audience = client ID, of
`VAULTX_OIDC_API_AUDIENCE`). De gebruiker moet minstens één keer via de
browser ingelogd hebben. Service accounts (client credentials) volgen in een
latere fase.
