# Links (fase 10)

"Links" in het menu is een bladwijzerbeheerder zoals [Linkwarden](https://linkwarden.app):
je bewaart links met een titel, beschrijving en tags in collecties, en VaultX houdt
gearchiveerde versies bij (screenshot, pdf, leesbare versie, volledige pagina).

Dit is het eerste deel. Wat er nog komt staat onderaan.

## Wat er al is

- **Collecties**, genest zo diep als je wil, met een naam, beschrijving en kleur. Een
  collectie verplaatsen kan via "Collectie bewerken" (niet in zichzelf of een eigen
  subcollectie). Een collectie verwijderen haalt haar subcollecties en hun links mee
  weg, net als in Linkwarden; VaultX vraagt eerst bevestiging met het aantal links.
- **Links** met URL, titel, beschrijving, collectie (of "Zonder collectie") en tags.
  Een ster pint een link vast; "Vastgepind" toont ze allemaal.
- **Tags**: klik een tag om te filteren. Onder "Tags > beheren" hernoem je of verwijder
  je een tag; hernoemen naar een bestaande naam voegt beide samen.
- **Zoeken** in titel, URL, beschrijving, tags en de tekst van gearchiveerde pagina's.
  Meerdere woorden moeten allemaal voorkomen. Sorteren op datum of naam.
- **Archieven** uit Linkwarden: screenshot (in een venster), leesbare versie (gesaneerd,
  zonder scripts of afbeeldingen van buitenaf), pdf en volledige pagina (in een nieuw
  tabblad). De volledige pagina komt met `Content-Security-Policy: sandbox` en laadt
  niets van buitenaf: scripts en formulieren erin doen niets.
- **Export** van alle collecties en links als JSON (zonder archieven). Die export kan je
  ook weer importeren, bv. bij een andere gebruiker.

Links zijn niet end-to-end versleuteld zoals de kluis en de notities: archiveren, zoeken
in de tekst, RSS en publieke deellinks (volgende delen) werken alleen als de server de
URL en de tekst kan lezen. Ze horen bij je VaultX-account, niet bij je kluis, en zijn
enkel voor jou zichtbaar. Aanmaken, verwijderen, collecties, tags en imports staan in de
auditlog (`links.*`).

## Importeren

Opnieuw importeren is veilig. Een collectie met dezelfde naam op dezelfde plaats wordt
hergebruikt; een link met dezelfde URL in dezelfde collectie wordt aangevuld (tags,
vastgepind, beschrijving, ontbrekende archieven) in plaats van verdubbeld. Je kan dus
eerst de JSON-export importeren en later nog eens de database met de bestanden.

### Linkwarden draait nog: de export

In Linkwarden: Instellingen > Data > Export Data. In VaultX: Links > Importeren >
Linkwarden-export. Collecties met hun nesting, links, tags, vastgepinde links en de
originele datums komen mee. Gearchiveerde bestanden zitten niet in die export; daarvoor
is de database-import hieronder. Opgeladen bestanden zonder URL (pdf/afbeelding als
link) worden overgeslagen en gemeld.

### Bladwijzers van je browser

Edge: Favorieten > ... > Favorieten exporteren (Chrome en Firefox hebben dezelfde
HTML-export). Links > Importeren > Browserbladwijzers. Alles komt onder de collectie
"Geïmporteerde bladwijzers", met je mappen als subcollecties. Tags (Firefox) en
beschrijvingen komen mee.

### Rechtstreeks uit de Linkwarden-database

Als Linkwarden niet meer draait maar de PostgreSQL-database er nog is. VaultX leest
enkel (read-only verbinding) en neemt collecties met nesting, links, tags, vastgepinde
links, datums en de opgeslagen paginatekst mee. Met `--files` ook de archieven.

Op de VaultX-server, als root:

```sh
set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend
export PGPASSWORD='wachtwoord-van-de-linkwarden-db'
DSN=postgresql://linkwarden@192.168.10.193:5433/linkwarden

# Welke Linkwarden-gebruikers zijn er?
/opt/vaultx/venv/bin/python -m app.cli import-linkwarden --dsn $DSN --list-users
# Enkel lezen en tellen
/opt/vaultx/venv/bin/python -m app.cli import-linkwarden --dsn $DSN --from jonas --dry-run
# Importeren naar je VaultX-account (gebruikersnaam of e-mail)
/opt/vaultx/venv/bin/python -m app.cli import-linkwarden --dsn $DSN --from jonas --to jonas \
  --files /pad/naar/linkwarden/data
```

- `--from` is de Linkwarden-gebruiker (id, gebruikersnaam of e-mail); weglaten mag als er
  maar één gebruiker met collecties is.
- `--files` is de map die Linkwarden als `STORAGE_FOLDER` gebruikt (standaard `data`, in
  Docker meestal het volume op `/data/data`). Kopieer die map eerst naar de VaultX-server
  (bv. `rsync -a oude-host:/pad/linkwarden/data/ /root/linkwarden-data/`). VaultX zoekt
  de bestanden op het pad dat in de database staat (`archives/<collectie>/<link>.pdf`,
  ...) en meldt hoeveel er ontbreken. Linkwarden met S3-opslag: download de bucket eerst
  naar een map.
- Heb je enkel een dump: zet die eerst terug in een tijdelijke database naast die van
  VaultX, met de VaultX-databasegebruiker als eigenaar, en wijs `--dsn` daarnaar:

  ```sh
  sudo -u postgres createdb -O vaultx linkwarden_oud
  sudo -u postgres pg_restore --no-owner --role=vaultx -d linkwarden_oud linkwarden.dump
  # (een .sql-dump: sudo -u postgres psql -d linkwarden_oud -f linkwarden.sql)
  /opt/vaultx/venv/bin/python -m app.cli import-linkwarden \
    --dsn "${VAULTX_DATABASE_URL%/*}/linkwarden_oud" --from jonas --to jonas --files ...
  sudo -u postgres dropdb linkwarden_oud   # achteraf
  ```
- Ook een JSON-export of bladwijzerbestand kan via de commandolijn:
  `--file export.json` of `--file bladwijzers.html` in plaats van `--dsn`.

Archieven staan in PostgreSQL (tabel `link_archives`), dus ook in de nachtelijke back-up.
Een archief mag hoogstens 100 MB zijn. De import via de webinterface mag tot 50 MB groot
zijn (nginx); staat NPM ervoor met een kleinere `client_max_body_size`, gebruik dan de
commandolijn.

## Wat nog komt

Zie `vaultx/fase-10/linkwarden-vergelijking.md` in het project:

2. VaultX archiveert zelf (headless Chromium): screenshot, pdf, volledige pagina en
   leesbare versie bij het opslaan, opnieuw archiveren, volledige-tekst-zoeken,
   voorbeeldafbeeldingen, bestanden opladen als link.
3. Delen: collecties met andere VaultX-gebruikers en teams, publieke alleen-lezen pagina,
   RSS-abonnementen die links in een collectie zetten.
4. Markeringen in de leesbare versie, bulk bewerken, een bladwijzer "Opslaan in VaultX".

VaultX bouwt geen eigen browserextensie. Bekeken wordt nog of de officiële
Linkwarden-extensie met VaultX kan praten, zoals de Bitwarden-apps nu met de kluis.

## API

Alles onder `/api/v1/links` (sessie of Authentik-token, zoals de rest van de API):
`GET/POST /collections`, `PATCH/DELETE /collections/{id}`, `GET /tags`,
`PATCH/DELETE /tags/{id}`, `GET /` (zoeken: `q`, `collection_id`, `unsorted`, `tag`,
`pinned`, `sort`, `limit`, `offset`), `POST /`, `GET/PATCH/DELETE /{id}`,
`GET /{id}/archives/{screenshot|pdf|readable|page|preview}`, `POST /import`,
`GET /export`. Zie `/api/docs`.
