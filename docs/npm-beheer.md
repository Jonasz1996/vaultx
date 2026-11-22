# NPM volledig beheren vanuit VaultX

Op **NPM > (koppeling) > NPM beheren** beheer je alles in Nginx Proxy Manager zonder de NPM-interface te openen:

| Tabblad | Wat kan |
|---|---|
| Proxy hosts | aanmaken, bewerken (domeinen, doel, websockets, block exploits, cache, access list, TLS, custom locations, Advanced), aan/uit, verwijderen |
| Redirects | redirection hosts met HTTP-code, schema, doeldomein, pad behouden, TLS en Advanced |
| 404-hosts | domeinen die een 404 geven, met TLS en Advanced |
| Streams | TCP/UDP-doorsturen van een poort naar een host |
| Access lists | gebruikers (basic auth) en adressen, Satisfy Any, auth doorgeven |
| Certificaten | Let's Encrypt aanvragen (HTTP- of DNS-challenge), eigen certificaat opladen, vernieuwen, nieuw bestand opladen, verwijderen |
| Journaal | elke wijziging met resultaat, controle, wat er veranderde en de volledige config ervoor; een verwijderd object kun je opnieuw aanmaken |

## Wat VaultX vereist

- Op de koppeling staat **VaultX mag wijzigen** aan.
- Het NPM-account heeft *Manage* op proxy hosts, redirection hosts, 404-hosts, streams, access lists en certificaten.
- Enkel eigenaars en beheerders van de organisatie zien en gebruiken dit scherm (de Advanced-config kan geheimen bevatten).

## Hoe een wijziging verloopt

1. **Voorbeeld.** VaultX leest alles live uit NPM en toont wat het zou doen, welke velden veranderen en de controles.
2. **Controles vooraf.** Geweigerd wordt onder meer: een domein dat al bij een andere host hoort, een certificaat of access list die niet bestaat, accolades die niet kloppen, een stream op een poort die al bezet is of op 80/81/443, een certificaat of access list verwijderen die nog in gebruik is (NPM zou dat toelaten en hosts stil openzetten of laten wijzen naar een onbestaand certificaat), Satisfy Any op een access list van een host met Authentik forward auth, en hosts verwijderen die VaultX publiceerde of met Authentik beschermde (daarvoor zijn *Depubliceren* en *Bescherming weghalen*).
3. **Schrijven via de NPM-API** en meteen nakijken of nginx de config aanvaardde (`meta.nginx_online`).
4. **Controle van buitenaf.** VaultX spreekt de host aan zoals een bezoeker (via het controleadres van de koppeling): een proxy host mag geen 5xx geven als dat ervoor niet zo was, een redirect moet de juiste code en het doel teruggeven, een 404-host een 404.
5. **Terugzetten** bij een fout: de vorige waarden terug, een nieuw object weer weg, aanzetten ongedaan.

VaultX staat nooit in het aanvraagpad: het schrijft enkel via de NPM-API.

## Je Advanced-config blijft heel

- Blokken tussen `# >>> vaultx:<naam>` en `# <<< vaultx:<naam>` (Authentik-bescherming, app publiceren) moeten byte voor byte blijven staan; wie ze wijzigt, krijgt een weigering.
- Verdwijnen er regels met `auth_request`, de Authentik-outpost, `sub_filter` (CSS-thema) of `add_header` (security headers), dan weigert VaultX tenzij je **Beschermde regels mogen weg** aanvinkt.
- Bewerk je iets anders aan een host, dan stuurt VaultX enkel de gewijzigde velden: de Advanced-config raakt het niet aan.
- Een eigen `location /` in Advanced vervangt de location van NPM. VaultX waarschuwt dan dat een access list op die host niet geldt voor `/`.

## Wachtwoorden en sleutels

- Access lists: een leeg wachtwoord bij een bestaande gebruiker laat het oude wachtwoord staan (zoals in NPM). Wachtwoorden komen niet in het journaal of de auditlog.
- Certificaten: de private key en de DNS-gegevens gaan rechtstreeks naar NPM. VaultX toont nooit de `meta` van een certificaat (daarin geeft NPM de sleutel terug) en bewaart ze niet.
- Let's Encrypt kan tot vijf minuten duren; de HTTP-challenge werkt enkel als het domein van op het internet op poort 80 bij deze NPM uitkomt.

## API

`/api/v1/organizations/{org}/npm-connections/{conn}/manage`

- `GET` overzicht van alle objecten; `GET /dns-providers`; `GET /changes?kind=`.
- `POST /{kind}/preview` en `POST /{kind}/apply` met `{"action", "npm_id", "data", "expected_modified_on", "confirm_removals", "verify"}`; `kind` is `proxy_host`, `redirection_host`, `dead_host`, `stream`, `access_list` of `certificate`, `action` is `create`, `update`, `delete`, `enable`, `disable`, `renew` of `upload`.

Getest tegen een echte NPM 2.16 met `e2e/npm_manage_e2e.py` (CI-job `npm-e2e`).
