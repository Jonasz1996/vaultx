# VaultX Invuller (browserextensie)

VaultX Invuller vult je logins uit je VaultX-kluis automatisch in, zoals een
mens: gebruikersnaam, wachtwoord en eventueel de tweestapscode (TOTP), en dan
Enter. Niet via SAML of OAuth in de apps: in de apps en in Nginx Proxy Manager
verandert niets.

Werkt in Chrome, Edge en Firefox (Manifest V3). De code staat in `extension/`.

## Installeren

Een Web Store is niet nodig.

**Chrome en Edge**

1. Haal de map `extension/` uit de repository (of pak `vaultx-invuller.zip`
   uit de CI-artefacten van een build uit).
2. Open `chrome://extensions` (Edge: `edge://extensions`) en zet
   **Ontwikkelaarsmodus** aan.
3. **Uitgepakte extensie laden** en kies de map `extension/`.
4. Maak de extensie vast in de werkbalk (puzzelstukje → punaise).

**Firefox** (128 of nieuwer)

- Even proberen: `about:debugging#/runtime/this-firefox` → **Tijdelijke
  add-on laden** → kies `extension/manifest.json`. Blijft tot je Firefox sluit.
- Blijvend: Firefox installeert enkel ondertekende add-ons. Laat de zip
  "unlisted" ondertekenen op addons.mozilla.org (gratis, niet openbaar), of
  gebruik Firefox Developer Edition/ESR met
  `xpinstall.signatures.required = false`.
- Firefox vraagt de toegang tot websites apart: Add-ons → VaultX Invuller →
  Machtigingen → **Toegang tot je gegevens voor alle websites** aan.

## Instellen

Klik op het VaultX-icoon en vul in:

- **Adres van VaultX**, bv. `https://vaultx.jbogaert.be` (of het LAN-adres);
- je **e-mail** en **master password** van de kluis (dezelfde als in de
  Bitwarden-apps);
- staat tweestapsverificatie aan op je kluis, dan vraagt de extensie de code
  uit je authenticator-app (of je herstelcode). Een beveiligingssleutel kan in
  de extensie niet: gebruik dan de herstelcode of zet ook een
  authenticator-app aan.

De extensie verschijnt in VaultX bij **Mijn kluis → Apps, apparaten en beveiliging** als
"VaultX Invuller (Chrome)" en kan daar afgemeld worden.

## Wat er gebeurt

- **Synchroniseren** bij het aanmelden, bij het openen van de browser, elke 5
  minuten en meteen bij een wijziging (live, zoals de Bitwarden-apps). De
  kluis blijft versleuteld opgeslagen in de extensie.
- **Ontgrendeld** blijft de kluis tot je de browser sluit, op
  **Vergrendelen** klikt, of na 12 uur. Daarna vraagt de extensie je master
  password. Ontgrendelen kan ook als VaultX onbereikbaar is: de versleutelde
  kluis staat lokaal.
- **Invullen** op een pagina met een loginformulier:
  - gebruikersnaam en wachtwoord, dan Enter;
  - een login in twee stappen (eerst gebruikersnaam, dan wachtwoord): elke
    stap apart, telkens met Enter;
  - een stap met een eenmalige code: de TOTP-code uit het item, dan Enter;
  - één passend item: automatisch. Meerdere: een klein keuzemenu onder het
    veld (ook met het toetsenbord: pijl omlaag, Enter);
  - in het popupvenster staat bij elke passende login een knop **Invullen**.
- Wat je zelf al typte, wordt niet overschreven.

### Per item

Voeg in het item (webkluis of Bitwarden-app) een eigen veld toe:

| Veld | Waarde | Effect |
|---|---|---|
| `vx:enter` | `nee` | wel invullen, niet zelf op Enter drukken |
| `vx:invullen` | `nee` | nooit automatisch; enkel via het keuzemenu of het popupvenster |

Items met **Master password opnieuw vragen** vult de extensie nooit in.

## Veiligheidsregels

- Enkel op **exact dezelfde host en poort** als een adres (URI) van het item.
  `grafana.example.com` vult niet in op `evil.example.com`, `example.com` of
  `grafana.example.com.evil.test`. Een adres zonder `https://` of `http://`
  geldt als https.
- Een https-item nooit op een http-pagina.
- Nooit in een iframe van een ander domein, en nooit op een pagina die zelf in
  een vreemd iframe zit. Dat controleert de achtergrond van de extensie met
  de adressen die de browser zelf geeft, niet met wat de pagina zegt.
- Hoogstens één automatische verzending per site en stap per 30 seconden.
  Komt hetzelfde formulier terug (fout wachtwoord), dan probeert de extensie
  het niet opnieuw en toont ze "wachtwoord klopt niet meer?".
- De pagina krijgt enkel de gegevens van het ene item dat ingevuld wordt. Het
  keuzemenu en de melding staan in een gesloten shadow root.

## Als iets plat ligt

| Wat ligt plat | Wat er gebeurt |
|---|---|
| VaultX | De extensie werkt met de laatst gesynchroniseerde kluis. Ontgrendelen met je master password. |
| Authentik | Geen invloed op de extensie. |
| Een certificaat, een node, NPM | De apps werken zoals altijd; je kan altijd zelf inloggen. De extensie zit niet in het pad van een aanvraag. |
| De extensie zelf (uitgezet) | Niets verandert aan de apps; je logt in zoals vroeger. |

## Testen

```bash
node --test extension/test/*.test.js        # URL-regels, TOTP (RFC 6238), crypto
cd backend && VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \
  python ../e2e/invuller_e2e.py ../invuller-shots   # de extensie in Chromium
```

Argon2id komt van [hash-wasm](https://github.com/Daninet/hash-wasm) (MIT),
`extension/lib/vendor/`.
