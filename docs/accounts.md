# Gewone accounts (inloggen zonder Authentik)

Naast "Inloggen met Authentik" kan je in VaultX inloggen met een gebruikersnaam (of e-mailadres)
en een VaultX-wachtwoord. Handig voor iemand zonder Authentik-account, en als noodtoegang wanneer
Authentik even niet bereikbaar is.

## Hoe het werkt

- **Geen open registratie.** Een instantiebeheerder maakt gewone accounts aan via *Gebruikers →
  Account aanmaken* (gebruikersnaam, e-mail, weergavenaam, wachtwoord, optioneel beheerder).
- **Een Authentik-account kan er ook een wachtwoord bij krijgen.** Op de pagina van de gebruiker:
  *Toegang → Wachtwoord instellen*. Het blijft hetzelfde account, met dezelfde kluis, notities,
  lidmaatschappen en rechten; je kan dan via Authentik of met het wachtwoord inloggen.
  Een beheerder kan dat ook voor zichzelf doen (klik op je naam linksonder).
- **Beheerdersrechten** komen uit de Authentik-groep `vaultx-admins` (VAULTX_OIDC_ADMIN_GROUPS) of
  worden in VaultX toegekend (*Beheerder maken in VaultX*). Een toekenning in VaultX blijft staan
  na een Authentik-login; je eigen rechten intrekken kan niet.
- **Wachtwoord wijzigen**: wie een wachtwoord heeft, wijzigt het zelf via zijn eigen pagina (met
  het huidige wachtwoord). Een beheerder kan elk wachtwoord resetten, of bij een Authentik-account
  de wachtwoordlogin weer weghalen. Bij een gewoon account zonder Authentik kan dat niet; deactiveer
  het dan.
- **Kluis en Bitwarden-apps** werken voor gewone accounts op dezelfde manier: activeer de kluis op
  *Mijn kluis*; het e-mailadres van het account wordt het kluisadres. Het VaultX-wachtwoord is
  **niet** je master password: kies voor de kluis een ander wachtwoord. Het master password
  verlaat je browser nooit, het VaultX-wachtwoord wel (over HTTPS naar de server).

## Beveiliging

- Wachtwoorden staan als Argon2id-hash in de database.
- Geen eisen aan het wachtwoord (lengte, tekens of gebruikersnaam); het mag alleen niet leeg zijn.
- Na 5 foute wachtwoorden na elkaar (VAULTX_LOCAL_LOGIN_MAX_FAILED) is wachtwoordlogin voor dat
  account 15 minuten vergrendeld (VAULTX_LOCAL_LOGIN_LOCKOUT_MINUTES). Inloggen via Authentik blijft
  werken; een beheerder die het wachtwoord reset, heft de vergrendeling op.
- Een onbekende gebruikersnaam en een fout wachtwoord geven hetzelfde antwoord, even snel.
- Elke login, fout wachtwoord, vergrendeling en wachtwoordwijziging staat in de auditlog.
  Pogingen met een onbekende gebruikersnaam niet (anders kan iedereen de log volschrijven).
- Na een wachtwoordwijziging of -reset vervallen de sessies die met het oude wachtwoord begonnen
  (je eigen huidige sessie blijft). Authentik-sessies blijven.
- Uitloggen uit een wachtwoordsessie gaat niet langs Authentik.
- Tweede stap: koppel een YubiKey, andere FIDO2-sleutel of passkey op je eigen gebruikerspagina
  (klik op je naam linksonder → *Beveiligingssleutels voor het wachtwoordlogin*). Daarna vraagt
  de loginpagina na je wachtwoord om de sleutel. Zie hieronder.

## Beveiligingssleutel na het wachtwoord

Op je eigen gebruikerspagina: *Sleutel of passkey toevoegen*, je wachtwoord invullen en de sleutel
aanraken. Bij de eerste sleutel krijg je eenmalig een **herstelcode**; bewaar die buiten VaultX.

- Inloggen: gebruikersnaam en wachtwoord, daarna vraagt de browser om de sleutel. Pas dan is er
  een sessie; zonder sleutel blijft het bij "wachtwoord klopt". Een fout antwoord telt mee voor
  dezelfde vergrendeling als een fout wachtwoord.
- Werkt enkel op de domeinnaam van VaultX (de host van `VAULTX_PUBLIC_URL`, of
  `VAULTX_WEBAUTHN_RP_ID`). Op het IP-adres van de CT kan de browser de sleutel niet gebruiken;
  daar log je in met de herstelcode (die zet de sleutels voor je login uit) of je gaat naar de
  domeinnaam.
- Weghalen: wachtwoord plus een gekoppelde sleutel of de herstelcode.
- Sleutel én herstelcode kwijt: op de CT als root
  `/opt/vaultx/venv/bin/python -m app.cli reset-login-2fa <gebruikersnaam>` (zie hieronder voor de
  omgeving). Het wachtwoord blijft nodig.
- Via Authentik inloggen vraagt deze stap niet: stel daarvoor in Authentik zelf een WebAuthn-
  stage in (dan vraagt Authentik je YubiKey). De sleutels van je kluis stel je apart in op Mijn
  kluis.

Wachtwoordlogin helemaal uitzetten: `VAULTX_LOCAL_LOGIN_ENABLED=false`. De loginpagina toont dan
enkel Authentik en bestaande wachtwoorden werken niet meer (ze blijven wel bewaard).

## Accountherstel: kan niet meer inloggen

Wachtwoord vergeten, of je beveiligingssleutel én herstelcode kwijt? Dan reset een beheerder je
account met één commando op de server. Er gaat geen e-mail uit.

**Voor de gebruiker.** Op de gewone loginpagina (met automatisch doorsturen naar Authentik:
`/login?lokaal=1`, of meteen `/login?herstel=1`) staat *Wachtwoord vergeten of kan je niet
inloggen?*. Daar vul je je inlog-e-mailadres in en krijg je het commando dat je beheerder moet
uitvoeren. Die pagina vraagt de server niets: de tekst is dus altijd dezelfde, of het account nu
bestaat of niet, en niemand kan er e-mailadressen mee uitproberen.

**Voor de beheerder**, op de VaultX-server (bij een ClusterForge-cluster op één van de nodes) als root:

```bash
set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend && \
  /opt/vaultx/venv/bin/python -m app.cli reset-account jij@jouwdomein.be
```

Dat doet in één keer:

- een **tijdelijk wachtwoord** maken en tonen (vier groepen van vijf tekens). Het werkt
  **één uur** (`--minutes 30` voor korter, hoogstens 7 dagen) en geeft zelf geen toegang: bij de
  eerste login moet de gebruiker meteen een eigen wachtwoord kiezen, pas daarna is er een sessie.
  Verlopen? Dan voer je het commando opnieuw uit;
- de **MFA van het VaultX-login** weghalen: beveiligingssleutels, passkeys en herstelcode;
- de **tweestapsverificatie van de kluis** uitzetten (authenticator-app/TOTP, sleutels en
  herstelcode voor de Bitwarden-apps), tenzij je `--keep-vault-2fa` meegeeft;
- **alle VaultX-sessies** van de gebruiker intrekken, ook die via Authentik, en een
  vergrendeling na foute wachtwoorden opheffen (een gedeactiveerd account wordt weer actief);
- alles in de **auditlog** zetten (`user.account_reset`, met wat er gereset is; daarna
  `user.password_change` als de gebruiker een eigen wachtwoord kiest).

Zelf het tijdelijke wachtwoord kiezen kan met `--password-stdin`. Geef het tijdelijke wachtwoord
persoonlijk door. Na het nieuwe wachtwoord komt de gebruiker op zijn eigen pagina, met de vraag
om zijn beveiligingssleutel en de tweestapsverificatie van zijn kluis opnieuw in te stellen.

**Wat niet gereset wordt: de kluis.** VaultX heeft twee lagen:

| Laag | Wachtwoord | Reset door beheerder? |
| --- | --- | --- |
| VaultX-login (deze pagina's, fase 8) | VaultX-wachtwoord, op de server als Argon2id-hash | Ja, met `reset-account` |
| Kluis (Mijn kluis, Bitwarden-apps) | master password, verlaat de browser nooit | **Nee** |

De kluis is in je browser versleuteld met een sleutel uit je master password; de server kent die
sleutel niet. Een beheerder kan het master password dus niet resetten zonder dat de inhoud
verloren gaat, en `reset-account` raakt het nooit aan. Wie zijn master password kwijt is, kan
enkel de kluis opnieuw aanmaken, zonder de oude items.

Wie via Authentik inlogt, herstelt zijn Authentik-wachtwoord en -MFA in Authentik. `reset-account`
geeft zo'n account wel een tijdelijk VaultX-wachtwoord, als noodtoegang naast Authentik.

## Noodtoegang vanaf de commandolijn

Ligt Authentik plat en heeft niemand een wachtwoord, dan kan je op de server zelf een account
aanmaken of een wachtwoord zetten. Op de Debian-container, als root:

```bash
set -a; . /etc/vaultx/vaultx.env; set +a
cd /opt/vaultx/backend
# Wachtwoord van een bestaand account (gebruikersnaam of e-mail, ook een Authentik-account):
/opt/vaultx/venv/bin/python -m app.cli set-password jonas
# Of een nieuw gewoon account dat beheerder is:
/opt/vaultx/venv/bin/python -m app.cli create-user --username noodbeheer --email nood@jouwdomein.be --admin
```

Het wachtwoord wordt twee keer gevraagd (zonder echo). `set-password` activeert het account ook
weer als het gedeactiveerd was. In Docker: `docker compose exec backend python -m app.cli ...`.
