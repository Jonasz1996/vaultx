# Automatische login (fase 5)

Het hoofdscenario uit de opdracht: een gebruiker die al bij Authentik is
aangemeld, opent `https://grafana.example.be` en zit meteen in Grafana. Geen
tweede login, geen Grafana-wachtwoord.

VaultX doet dat met methode 4 uit het ontwerp (02b, "Authentik-integratieflows"):
de app wordt zelf een OpenID Connect-client van Authentik. Voor Grafana is dat
de aanbevolen route (02b §3.3): geen gedeeld wachtwoord dat kan lekken, MFA en
het weghalen van gebruikers gelden automatisch, Grafana ziet echte gebruikers,
en VaultX staat niet in het loginpad (Grafana blijft werken als VaultX uitvalt).

## 1. Wat VaultX doet

Op de pagina van een app in de catalogus staat de kaart **Automatische login**.
Na *Automatische login inrichten* → *Voorbeeld bekijken* → *Inrichten*:

1. **In Authentik** maakt VaultX:
   - een OAuth2/OpenID-provider `VaultX login: <domein>` (confidential client,
     PKCE, refresh tokens, ID-tokens getekend met het standaardcertificaat van
     Authentik), met exact één redirect URI;
   - een applicatie `vaultx-login-<domein>`;
   - groepsbindingen voor wie zich mag aanmelden: de leden van de organisatie,
     van één team, of iedereen (dezelfde `vaultx:`-groepconventie als in
     [docs/authentik.md](authentik.md) en fase 4).

   Het client secret kiest VaultX zelf (64 tekens) en bewaart het versleuteld.
2. **In Grafana** (sjabloon *Grafana*), als je een Grafana-beheerder opgeeft:
   VaultX zet de generic OAuth-login via de SSO settings API van Grafana
   (Grafana 11 of nieuwer). Dat werkt meteen, zonder herstart. Geef je geen
   beheerder op, dan toont VaultX de `grafana.ini` en de omgevingsvariabelen om
   zelf in te vullen (*Config tonen*).
3. **Controle**: VaultX opent `/login` van Grafana zoals een bezoeker zonder
   sessie. Grafana hoort door te sturen naar Authentik, met de client ID van
   VaultX en de juiste redirect URI. De uitkomst staat op de kaart; *Opnieuw
   controleren* doet het nog eens (bv. nadat je Grafana zelf herstartte).

Faalt een stap (Authentik weigert, Grafana weigert of neemt de instellingen
niet over), dan verwijdert VaultX wat het in Authentik al aanmaakte. In het
voorbeeld weigert VaultX vooraf als:

- Grafana denkt dat het op een andere URL staat dan de app-URL (`root_url`):
  dan zou Grafana Authentik een verkeerde redirect URI sturen;
- Grafana al een generic OAuth-login aan heeft (VaultX overschrijft die niet);
- de Authentik-groep voor de gekozen toegang niet bestaat;
- de standaardflows of scope mappings van Authentik ontbreken.

**Weghalen** zet de login in Grafana weer uit (daarvoor vraagt VaultX opnieuw
een Grafana-beheerder, anders zou Grafana iedereen naar een provider sturen die
niet meer bestaat) en verwijdert de applicatie en provider in Authentik. Een
app met automatische login kan niet uit de catalogus verwijderd worden zolang
de login er staat.

## 2. Rollen in Grafana

`role_attribute_path` volgt de groepen uit Authentik:

| Wie | Rol |
| --- | --- |
| `vaultx:<org>:owner`, `vaultx:<org>:admin` en de VaultX-beheerders (`VAULTX_OIDC_ADMIN_GROUPS`) | Admin |
| Alle anderen die zich mogen aanmelden | De gekozen standaardrol (Viewer, Editor of Admin) |

Grafana zet de rol bij elke login opnieuw. Team sync is enkel Grafana
Enterprise (onderzoek 03, E2) en zit er dus niet in.

## 3. Instellen

1. **Serviceaccount in Authentik**: de rechten uit
   [docs/npm.md, sectie 6](npm.md#6-authentik-kant-automatisch-aanmaken), plus:

   | Recht | Waarom |
   | --- | --- |
   | `authentik_providers_oauth2.view_oauth2provider`, `add_oauth2provider`, `delete_oauth2provider` | OIDC-provider zoeken, aanmaken en opruimen |
   | `authentik_providers_oauth2.view_scopemapping` | De standaard scopes (openid, email, profile, offline_access) vinden |
   | `authentik_crypto.view_certificatekeypair` | Het certificaat voor de ID-tokens vinden |

   `change_oauth2provider` is niet nodig: VaultX geeft het client secret zelf
   mee en hoeft het niet terug te lezen.
2. **Op de VaultX-server**:
   - `VAULTX_AUTHENTIK_API_TOKEN` zoals in fase 4;
   - `VAULTX_AUTHENTIK_PUBLIC_URL`: de URL van Authentik zoals browsers en
     Grafana hem gebruiken (komt in de config van de app). Leeg = scheme en
     host van `VAULTX_OIDC_ISSUER`, wat meestal klopt;
   - `VAULTX_AUTHENTIK_SIGNING_KEY`: naam van het certificaat voor de
     ID-tokens (standaard `authentik Self-signed Certificate`).
3. **Grafana**:
   - `root_url` (`GF_SERVER_ROOT_URL`) moet de URL zijn waarop gebruikers
     Grafana openen, bv. `https://grafana.example.be/`;
   - Grafana moet Authentik zelf bereiken op die publieke URL (token- en
     userinfo-URL);
   - elke gebruiker heeft een **e-mailadres in Authentik** nodig: zonder
     e-mailadres weigert Grafana de aanmelding ("Error getting email address");
   - om de login meteen te laten zetten: een Grafana-**serverbeheerder** (bv.
     `admin`). Een serviceaccount-token volstaat niet; de SSO settings API
     antwoordt dan 403. VaultX bewaart die gegevens niet. Het adres dat je
     opgeeft is het interne adres van Grafana (zoals NPM het bereikt, VaultX
     stelt het doel van de NPM-host voor), niet via Authentik forward auth.

Lokaal aanmelden in Grafana blijft mogelijk via
`https://grafana.example.be/login?disableAutoLogin`, bv. met het `admin`-account
als Authentik onbeschikbaar is. Bewaar dat wachtwoord als break-glass.

## 4. Andere apps (sjabloon "Andere app")

Voor elke app die OpenID Connect kent (Portainer, Gitea/Forgejo, Proxmox VE,
Argo CD, ...): geef de redirect URI('s) van de app op. VaultX maakt dezelfde
provider, applicatie en groepsbindingen en toont issuer, discovery-URL,
client ID en secret om in de app in te vullen. VaultX zet niets in de app zelf
en kan niet controleren of de app de instellingen gebruikt: meld je aan om te
testen.

## 5. Samen met "Beschermen met Authentik" (fase 3 en 4)

Staat de host ook achter Authentik forward auth, dan werkt het nog steeds: de
gebruiker heeft al een Authentik-sessie, dus de outpost laat door en Grafana
krijgt meteen een code van Authentik (impliciete toestemming). VaultX kan
Grafana dan niet zelf controleren (zonder sessie stopt de outpost het verzoek);
de kaart zegt dat. Strikt nodig is forward auth dan niet meer: Grafana vraagt
zelf om de aanmelding.

## 6. Beveiliging

- Het client secret staat AES-GCM-versleuteld in `app_logins` (sleutel
  afgeleid van `VAULTX_SECRET_KEY`). *Config tonen* geeft het terug aan
  beheerders van de organisatie en staat in de auditlog
  (`app_login.config_viewed`).
- De redirect URI staat in Authentik op *strict*: een code gaat enkel naar die
  ene URL.
- Leden zien dat er een automatische login is, maar niet het secret, en kunnen
  niets wijzigen. Geweigerde pogingen staan in de auditlog.
- De Grafana-beheerder wordt enkel voor die ene actie gebruikt en niet
  bewaard. VaultX volgt geen doorverwijzingen van Grafana en toont een fout
  als het adres naar Authentik forward auth leidt.
- Over http werkt het, maar codes en tokens gaan dan onversleuteld over het
  netwerk; VaultX waarschuwt in het voorbeeld.

## 7. API

| Methode | Pad | Wat |
| --- | --- | --- |
| GET | `/api/v1/organizations/{org}/applications/{app}/login` | Sjablonen, voorstellen en de huidige login |
| POST | `.../login/preview` | Voorbeeld; wijzigt niets (met `grafana` ook Grafana lezen) |
| POST | `.../login` | Inrichten |
| POST | `.../login/check` | Opnieuw controleren |
| GET | `.../login/config` | Config met client secret (beheerders, in de auditlog) |
| POST | `.../login/remove` | Weghalen (`grafana` nodig als VaultX de login in Grafana zette) |

## 8. Getest

`e2e/autologin_e2e.py` tegen echte Authentik 2026.8.3 en Grafana 13.2.3, met
een serviceaccount met exact de rechten hierboven, en in Chromium: carol
meldt zich aan bij Authentik, opent Grafana en zit er meteen in als Admin;
alice komt via de Authentik-aanmelding binnen als Viewer; eve (geen lid) wordt
door Authentik geweigerd. Ook in CI (job `autologin-e2e`).
