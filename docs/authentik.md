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
