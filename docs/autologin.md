# Automatische login (fase 5)

Het hoofdscenario uit de opdracht: een gebruiker die al bij Authentik is
aangemeld, opent een app achter NPM (bv. `https://grafana.example.be`) en zit er
meteen in. Geen tweede login, geen wachtwoord voor de app.

VaultX doet dat met methode 4 uit het ontwerp (02b, "Authentik-integratieflows"):
de app wordt zelf een OpenID Connect-client van Authentik. Geen gedeeld
wachtwoord dat kan lekken, MFA en het weghalen van gebruikers gelden
automatisch, de app ziet echte gebruikers, en VaultX staat niet in het
loginpad (de app blijft werken als VaultX uitvalt).

VaultX koppelt daarvoor enkel met **Authentik** (en met NPM, waar de app en haar
URL vandaan komen). Het praat niet met de app zelf: issuer, client ID en secret
vul je in de app in.

## 1. Wat VaultX doet

Op de pagina van een app in de catalogus staat de kaart **Automatische login**.
Na *Automatische login inrichten* → *Voorbeeld bekijken* → *Inrichten*:

1. **In Authentik** maakt VaultX:
   - een OAuth2/OpenID-provider `VaultX login: <domein>` (confidential client,
     refresh tokens, ID-tokens getekend met het standaardcertificaat van
     Authentik), met exact de redirect URI's die je opgaf;
   - een applicatie `vaultx-login-<domein>`, met de app-URL als startpagina;
   - groepsbindingen voor wie zich mag aanmelden: de leden van de organisatie,
     van één team, of iedereen (dezelfde `vaultx:`-groepconventie als in
     [docs/authentik.md](authentik.md) en fase 4).

   Het client secret kiest VaultX zelf (64 tekens) en bewaart het versleuteld.
2. **Instellingen tonen** geeft wat je in de app invult: issuer, discovery-URL,
   client ID, client secret, scopes, authorization-, token-, userinfo- en
   uitlog-URL. Meestal volstaan de discovery-URL (of issuer), client ID en
   secret.
3. Je zet de login aan in de app en meldt je aan om te testen. Laat de app
   automatisch naar Authentik doorsturen (veel apps kennen een optie als
   "auto login"), dan komt wie al bij Authentik is aangemeld meteen binnen.

Faalt een stap in Authentik, dan verwijdert VaultX wat het al aanmaakte. In het
voorbeeld weigert VaultX vooraf als:

- er geen geldige redirect URI is (absolute http(s)-URL);
- de app geen geldige URL heeft;
- de Authentik-groep voor de gekozen toegang niet bestaat;
- de standaardflows of scope mappings van Authentik ontbreken;
- de app al een automatische login heeft.

**Weghalen** verwijdert de applicatie en provider in Authentik. Zet de login
daarna ook in de app zelf uit. Een app met automatische login kan niet uit de
catalogus verwijderd worden zolang de login er staat.

## 2. Redirect URI

De redirect URI is het adres waarop de app de code van Authentik ontvangt. Ze
staat in de documentatie van de app, bij OpenID Connect of OAuth, en Authentik
aanvaardt enkel exact die URI. Voorbeelden en volledige handleidingen per app
staan bij de [Authentik-integraties](https://integrations.goauthentik.io/);
voor Grafana is het `https://grafana.example.be/login/generic_oauth`.

Wat de app met de gebruiker doet (rollen, nieuwe accounts) stel je in de app in.
Authentik stuurt `preferred_username`, `email`, `name` en `groups` mee; met de
`vaultx:`-groepen kan de app rollen toekennen.

## 3. Instellen

1. **Serviceaccount in Authentik**: de rechten uit
   [docs/npm.md, sectie 6](npm.md#6-authentik-kant-automatisch-aanmaken), plus:

   | Recht | Waarom |
   | --- | --- |
   | `authentik_providers_oauth2.view_oauth2provider`, `add_oauth2provider`, `delete_oauth2provider` | OIDC-provider zoeken, aanmaken en opruimen |
   | `authentik_providers_oauth2.view_scopemapping` | De standaard scopes (openid, email, profile, offline_access) vinden |
   | `authentik_crypto.view_certificatekeypair` | Het certificaat voor de ID-tokens vinden |

   `change_oauth2provider` is niet nodig: VaultX geeft het client secret zelf
   mee en hoeft het niet terug te lezen (Authentik toont het secret niet aan
   een account zonder dat recht).
2. **Op de VaultX-server**:
   - `VAULTX_AUTHENTIK_API_TOKEN` zoals in fase 4;
   - `VAULTX_AUTHENTIK_PUBLIC_URL`: de URL van Authentik zoals browsers en apps
     hem gebruiken (komt in de instellingen voor de app). Leeg = scheme en host
     van `VAULTX_OIDC_ISSUER`, wat meestal klopt;
   - `VAULTX_AUTHENTIK_SIGNING_KEY`: naam van het certificaat voor de
     ID-tokens (standaard `authentik Self-signed Certificate`).
3. **De app** moet Authentik zelf bereiken op die publieke URL (token- en
   userinfo-URL). Sommige apps weigeren een gebruiker zonder e-mailadres: geef
   gebruikers een e-mailadres in Authentik.

## 4. Samen met "Beschermen met Authentik" (fase 3 en 4)

Staat de host ook achter Authentik forward auth, dan werkt het nog steeds: de
gebruiker heeft al een Authentik-sessie, dus de outpost laat door en de app
krijgt meteen een code van Authentik (impliciete toestemming). Strikt nodig is
forward auth dan niet meer: de app vraagt zelf om de aanmelding.

## 5. Beveiliging

- Het client secret staat AES-GCM-versleuteld in `app_logins` (sleutel
  afgeleid van `VAULTX_SECRET_KEY`). *Instellingen tonen* geeft het terug aan
  beheerders van de organisatie en staat in de auditlog
  (`app_login.config_viewed`).
- De redirect URI's staan in Authentik op *strict*: een code gaat enkel naar
  die URL's.
- Leden zien dat er een automatische login is, maar niet het secret, en kunnen
  niets wijzigen. Geweigerde pogingen staan in de auditlog.
- Over http werkt het, maar codes en tokens gaan dan onversleuteld over het
  netwerk; VaultX waarschuwt in het voorbeeld.

## 6. API

| Methode | Pad | Wat |
| --- | --- | --- |
| GET | `/api/v1/organizations/{org}/applications/{app}/login` | Toestand, voorgestelde app-URL en de huidige login |
| POST | `.../login/preview` | Voorbeeld; wijzigt niets |
| POST | `.../login` | Inrichten (`redirect_uris`, `access`, optioneel `app_url`) |
| GET | `.../login/config` | Instellingen met client secret (beheerders, in de auditlog) |
| POST | `.../login/remove` | Weghalen (opruimen in Authentik) |

## 7. Getest

`e2e/autologin_e2e.py` tegen echte Authentik 2026.8.3, met een serviceaccount
met exact de rechten hierboven. De test speelt zelf de app: een kleine
OIDC-client die bezoekers zonder sessie meteen naar Authentik stuurt en het
ID-token controleert (handtekening RS256, issuer, audience). In Chromium: carol
meldt zich aan bij Authentik, opent de app en zit er meteen in, zonder
aanmeldstap; alice komt via de Authentik-aanmelding binnen; eve (geen lid) wordt
door Authentik geweigerd. Ook in CI (job `autologin-e2e`).
