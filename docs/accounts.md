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
- Minstens 12 tekens (VAULTX_PASSWORD_MIN_LENGTH), niet gelijk aan de gebruikersnaam of het e-mailadres.
- Na 5 foute wachtwoorden na elkaar (VAULTX_LOCAL_LOGIN_MAX_FAILED) is wachtwoordlogin voor dat
  account 15 minuten vergrendeld (VAULTX_LOCAL_LOGIN_LOCKOUT_MINUTES). Inloggen via Authentik blijft
  werken; een beheerder die het wachtwoord reset, heft de vergrendeling op.
- Een onbekende gebruikersnaam en een fout wachtwoord geven hetzelfde antwoord, even snel.
- Elke login, fout wachtwoord, vergrendeling en wachtwoordwijziging staat in de auditlog.
  Pogingen met een onbekende gebruikersnaam niet (anders kan iedereen de log volschrijven).
- Na een wachtwoordwijziging of -reset vervallen de sessies die met het oude wachtwoord begonnen
  (je eigen huidige sessie blijft). Authentik-sessies blijven.
- Uitloggen uit een wachtwoordsessie gaat niet langs Authentik.
- Er is nog geen tweede factor (TOTP of passkey) voor wachtwoordlogin. Wie dat nodig heeft, logt
  via Authentik in, of zet de wachtwoordlogin uit.

Wachtwoordlogin helemaal uitzetten: `VAULTX_LOCAL_LOGIN_ENABLED=false`. De loginpagina toont dan
enkel Authentik en bestaande wachtwoorden werken niet meer (ze blijven wel bewaard).

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
