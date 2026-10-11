# Notities (fase 7)

"Notities" in het menu is een Markdown-kennisbank in je persoonlijke kluis, met
de functies die je van Obsidian kent. Alles is end-to-end versleuteld met de
sleutel van je kluis.

## Versleuteling

- Je kluis moet geactiveerd zijn (Mijn kluis). Notities openen vraagt je master
  password; de browser leidt er je master key uit af, bewijst het wachtwoord aan
  VaultX met dezelfde afgeleide hash als een Bitwarden-app
  (`POST /api/v1/me/vault/unlock`) en krijgt dan de versleutelde user key terug,
  die hij zelf uitpakt. Mislukte pogingen tellen mee met die van de apps: na 10
  fouten is de kluis 15 minuten vergrendeld (`VAULTX_VAULT_MAX_FAILED_LOGINS`,
  `VAULTX_VAULT_LOCKOUT_MINUTES`). Elke poging staat in de auditlog
  (`vault.unlock`).
- De user key blijft enkel in het geheugen van het tabblad, als niet-exporteerbare
  WebCrypto-sleutel. Herladen, "Vergrendelen" of 15 minuten niets doen gooit hem
  weg.
- Per notitie versleutelt de browser titel, map, tekst en bladwijzer samen
  (EncString type 2: AES-256-CBC met HMAC-SHA256, hetzelfde formaat als de
  Bitwarden-items). De server bewaart enkel die blob, een revisienummer en
  tijdstempels. Links, tags, zoeken en de grafiek rekent de browser uit.
- Bijlagen versleutelt de browser met dezelfde sleutel, in het formaat dat
  Bitwarden voor bijlagen gebruikt (één byte type 2, IV, MAC, ciphertext); de
  bestandsnaam is een aparte EncString. De server kent enkel de grootte.
- Bitwarden-apps zien de notities niet; het zijn geen kluisitems.
- Kluis verwijderen (Mijn kluis) verwijdert ook alle notities en bijlagen.

## Wat je kan

| Functie | Hoe |
| --- | --- |
| Links | `[[Titel]]`, `[[Map/Titel]]`, `[[Titel#Kop]]`, `[[Titel\|eigen tekst]]`. Een link naar een notitie die nog niet bestaat, maakt haar aan bij een klik. `[[` in de editor geeft suggesties. |
| Ingesloten notitie | `![[Titel]]` of `![[Titel#Kop]]` (twee niveaus diep) |
| Bijlagen | Plak (Ctrl+V) of sleep een afbeelding of bestand in de editor, of gebruik "Bijlage" in de werkbalk. Dat zet `![[naam.png]]` in de tekst. Afbeeldingen worden getoond, andere bestanden worden een 📎-link die ontsleutelt en downloadt. `![[foto.png\|300]]` zet de breedte, `![](foto.png)` werkt ook. Het tabblad "Bijlagen" links toont alles met gebruik en opslag. |
| Catalogus-app | `[[app:Grafana]]` linkt naar de app in de VaultX-catalogus |
| Backlinks | Rechts: wie naar deze notitie linkt, met de regel errond, en niet-gelinkte vermeldingen met "Link maken" |
| Hernoemen | Een titel wijzigen past `[[links]]` in andere notities automatisch aan |
| Tags | `#tag`, geneste tags `#infra/proxmox`, of `tags: [a, b]` in de eigenschappen. Klik op een tag om te zoeken. |
| Eigenschappen | YAML-frontmatter tussen `---`-regels; `aliases` telt mee voor links |
| Opmaak | GitHub-Markdown (tabellen, ~~doorhalen~~, taken), `==markeren==`, `%%commentaar%%`, callouts `> [!note]`, `> [!warning]-` (dichtgeklapt) |
| Taken | `- [ ] taak`; afvinken kan in de leesweergave |
| Geheim | `\|\|pincode\|\|` blijft wazig tot je erop klikt (handig bij schermdelen) |
| Zoeken | Links boven: woorden, `"exacte zin"`, `#tag`, `map:Runbooks`, `-uitsluiten` |
| Snelkiezer | Ctrl+O of Ctrl+K: notitie openen of aanmaken |
| Grafiek | Alle notities en links, optioneel tags en niet-bestaande notities, of enkel rond de huidige notitie. Rechts ook een lokale grafiek. |
| Dagnotitie | "Vandaag" opent of maakt `Dagboek/JJJJ-MM-DD` (met sjabloon `Sjablonen/Dagnotitie` als dat bestaat) |
| Sjablonen | Notities in de map `Sjablonen`; invoegen via de werkbalk. `{{title}}`, `{{date}}`, `{{time}}` worden ingevuld. |
| Bladwijzers | ☆ bij de titel |
| Mappen | Pad bovenaan de notitie, of sleep een notitie naar een map in de boom |
| Versies | "Geschiedenis": VaultX bewaart een versie als je een notitie na minstens 10 minuten opnieuw wijzigt (max. 50 per notitie), ook versleuteld |
| Prullenbak | Verwijderen zet in de prullenbak (menu ⋯), terugzetten of definitief verwijderen |
| Import | Menu ⋯: losse `.md`-bestanden of een hele Obsidian-kluis (map). Andere bestanden (afbeeldingen, pdf's) worden bijlagen met hun pad als naam; `.obsidian` en verborgen mappen worden overgeslagen. |
| Export | Menu ⋯: zip met één `.md`-bestand per notitie in de mappenstructuur, plus de bijlagen. **Niet versleuteld.** |
| Sneltoetsen | Ctrl+S bewaren, Ctrl+E lezen/bewerken, Ctrl+B/I vet/cursief, Tab/Shift+Tab inspringen, Enter zet lijsten voort |

De editor bewaart automatisch (na een seconde stilte). Wijzig je dezelfde notitie
in twee tabbladen, dan krijg je de keuze welke versie blijft.

Externe afbeeldingen worden niet geladen: de Content-Security-Policy van VaultX
staat dat niet toe, zodat een notitie niets naar buiten lekt. Eigen bijlagen wel.

Bijlagen mogen standaard 25 MB per bestand zijn en samen 1 GB per gebruiker
(`VAULTX_VAULT_NOTE_FILE_MAX_MB`, `VAULTX_VAULT_NOTE_STORAGE_MB`). Ze staan
versleuteld in PostgreSQL. De meegeleverde nginx-configuratie laat voor
`/api/v1/me/notes` uploads tot 30 MB toe (de rest van de API blijft op 1 MB);
verhoog je de limiet, pas dan ook `client_max_body_size` daar aan. Werk je een
bestaande installatie bij, kopieer dan ook de nieuwe nginx-configuratie (zie
docs/debian-ct.md, stap 5), anders krijgen grote notities en bijlagen een 413.

## Nog niet (latere fases)

- Notities delen met een team of organisatie (vraagt organisatiesleutels, zie
  "Gedeelde kluizen")
- Canvas, Dataview-achtige queries, Mermaid en wiskunde (KaTeX), voetnoten
- Notities in de Bitwarden-apps of offline op je telefoon
- Ontgrendelen in de browser met Argon2id (enkel PBKDF2 nu; VaultX activeert
  kluizen met PBKDF2)

## API

Alles onder `/api/v1/me/notes`, met de VaultX-sessie (cookie + CSRF-header):

| Methode | Pad | |
| --- | --- | --- |
| GET | `/api/v1/me/notes` | alle notities (versleuteld), ook in de prullenbak |
| POST | `/api/v1/me/notes` | `{content}` |
| POST | `/api/v1/me/notes/import` | `{notes: [{content}]}`, max. 200 per keer |
| PUT | `/api/v1/me/notes/{id}` | `{content, base_revision}`; 409 met `current` als iemand anders intussen bewaarde |
| POST | `/api/v1/me/notes/{id}/trash` en `/restore` | prullenbak |
| DELETE | `/api/v1/me/notes/{id}` | definitief |
| GET | `/api/v1/me/notes/{id}/versions` | eerdere versies (versleuteld) |
| GET | `/api/v1/me/notes/files` | bijlagen (versleutelde namen, groottes, opslag en limieten) |
| POST | `/api/v1/me/notes/files` | body: de versleutelde bytes (`application/octet-stream`), header `X-VaultX-File-Name`: versleutelde naam |
| GET | `/api/v1/me/notes/files/{id}` | versleutelde bytes |
| DELETE | `/api/v1/me/notes/files/{id}` | bijlage verwijderen |

`content` is een EncString type 2 (`2.<iv>|<ciphertext>|<mac>`) van
`{"v":1,"title","folder","body","pinned"}`, max. 2 MB. Opladen en verwijderen
van bijlagen staat in de auditlog (`vault.note.file.upload`, `vault.note.file.delete`).

## Testen

`e2e/notes_e2e.py` doorloopt alles in Chromium en controleert daarna in
PostgreSQL dat geen enkele notitie of versie klare tekst bevat. Zie
[e2e/README.md](../e2e/README.md).
