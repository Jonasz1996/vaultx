# Nginx Proxy Manager koppelen

VaultX leest de proxy hosts uit Nginx Proxy Manager (NPM) en vult daarmee de
**applicatiecatalogus**: welke applicaties er draaien, op welk domein, en hoe
je er binnenkomt (via Authentik of niet). Standaard **leest** VaultX enkel.
Zet je schrijven aan op de koppeling, dan kan een beheerder per host
**Authentik-bescherming laten zetten of weghalen** (sectie 5): VaultX toont eerst
wat het wijzigt, controleert de host achteraf en zet bij een fout de vorige
config terug.

Sinds fase 4 kan VaultX daarbij ook de **Authentik-kant** zelf aanmaken
(provider, applicatie, toegang, outpost; sectie 6), zodat beschermen één stap is.

Getest tegen NPM 2.16.0 en Authentik 2026.8.3 (CI start bij elke push een
echte NPM en een echte Authentik, zie `e2e/npm_e2e.py`, `e2e/npm_write_e2e.py`
en `e2e/authentik_npm_e2e.py`).

## 1. Een NPM-account voor VaultX

Maak in NPM onder *Users* een apart account aan, bijvoorbeeld
`vaultx@jouwdomein.be`:

| Recht | Waarde | Waarom |
| --- | --- | --- |
| Visibility | **All Items** | Met "Created Items Only" ziet VaultX enkel hosts die dit account zelf maakte |
| Proxy Hosts | **View Only**, of **Manage** als VaultX Authentik-bescherming mag zetten | |
| Rest | Hidden | NPM geeft de access list (naam, "Satisfy Any") mee bij de host |

Zo getest op NPM 2.16.0. Met *View Only* weigert NPM elke schrijfpoging, ook
als schrijven in VaultX aanstaat.

Zet **geen tweestapsverificatie** op dit account: VaultX kan de 2FA-code niet
invullen en meldt dat duidelijk. Bescherm het account met een lang willekeurig
wachtwoord. NPM trekt tokens in na een wachtwoordwijziging; pas het wachtwoord
dan ook in VaultX aan.

## 2. Koppeling aanmaken

In VaultX: **NPM → NPM koppelen**, of via de API:

```bash
curl -X POST https://vaultx.jouwdomein.be/api/v1/organizations/$ORG/npm-connections \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "Homelab NPM", "base_url": "http://192.168.1.10:81",
       "identity": "vaultx@jouwdomein.be", "secret": "..."}'
```

- `base_url` is de **beheerpoort** van NPM (standaard 81), niet poort 80/443.
  VaultX moet die kunnen bereiken: draait VaultX in Docker, gebruik dan het
  IP-adres van de host of een gedeeld Docker-netwerk, niet `localhost`.
- Het wachtwoord wordt met AES-256-GCM versleuteld opgeslagen (sleutel
  afgeleid van `VAULTX_SECRET_KEY`) en nooit teruggegeven, ook niet in de auditlog.
- Een koppeling hoort bij één organisatie. Eigenaars en beheerders van die
  organisatie beheren ze; gewone leden zien de koppeling en de catalogus.

Daarna **Nu synchroniseren**. Elke actieve koppeling wordt ook automatisch
ingelezen, standaard elke 15 minuten (`VAULTX_NPM_SYNC_INTERVAL_MINUTES`, 0 = uit).
Met meerdere VaultX-nodes is dat veilig: een databaselock per koppeling
voorkomt dubbel werk.

## 3. Wat VaultX afleidt

Per proxy host:

| Wat | Hoe |
| --- | --- |
| Applicatie en type | Label `vaultx.app` / `vaultx.type`, anders herkend aan het subdomein, de forward host (containernaam) of een kenmerkende poort (Grafana, Home Assistant, Jellyfin, Proxmox, Paperless, Nextcloud, ...). Lukt dat niet, dan wordt het subdomein de naam. |
| Aanmelding | Label `vaultx.auth`, anders **Authentik forward auth** als er een actieve `auth_request` naar de outpost staat (in *Advanced* van de host of van een custom location), anders **NPM access list** als die gekoppeld is, anders *onbekend*. |
| Status | *Beschermd* (via Authentik: forward auth, OIDC, SAML, headers), *Beperkt* (access list of eigen login), *Open* (`vaultx.auth = none`), *Onbekend*, *Offline* (host uit of nginx-fout in NPM), *Verdwenen* (host niet meer in NPM). |

En deze waarschuwingen:

| Code | Betekenis |
| --- | --- |
| `satisfy_any` | Forward auth plus een access list met "Satisfy Any": een toegelaten IP-adres slaat de Authentik-controle over. Authentik raadt dit expliciet af. |
| `custom_root_location` | *Advanced* bevat een eigen `location /`. NPM laat dan zijn standaardlocatie weg, en daarmee de access list en websocket-headers. Gebruik het officiële Authentik-patroon: `auth_request` in een **custom location `/`**. |
| `nginx_offline` | NPM meldt een nginx-fout voor deze host; hij is offline. |
| `forward_auth_not_authentik` | Er staat een `auth_request`, maar niet naar een Authentik-outpost. |
| `no_tls`, `tls_not_forced` | Geen certificaat, of HTTP wordt niet naar HTTPS gestuurd. |
| `label_invalid`, `label_unknown` | Een `vaultx.*`-label met een onbekende sleutel of waarde. |

### Labels

NPM kent geen labels. Zet ze als commentaar in het veld *Advanced* van de
proxy host of van een custom location. nginx negeert commentaar.

```nginx
# vaultx.app = Grafana
# vaultx.auth = oidc          # forward_auth, oidc, saml, header, access_list, app, none
# vaultx.type = grafana
# vaultx.tags = monitoring, ops
# vaultx.description = Dashboards van het team
# vaultx.ignore = true        # niet in de catalogus opnemen
```

Het voorbeeld uit de opdracht, `grafana.domain.be` met `# vaultx.auth = oidc`,
geeft in de catalogus:

```json
{"host": "grafana.domain.be", "app": "Grafana", "auth": "oidc", "status": "protected"}
```

(in de API: `hosts[0].domain_names[0]`, `name`, `auth_method`, `status` op
`GET /api/v1/catalog`).

## 4. Catalogus en sync samen

- Een nieuwe host krijgt automatisch een catalogusitem met *automatisch bijwerken* aan.
  Bij elke sync volgen naam, type, URL, aanmelding, tags en omschrijving dan NPM.
- Pas je een item aan in VaultX, dan gaat *automatisch bijwerken* uit en blijft
  je keuze staan. Je kunt het weer aanzetten op de detailpagina.
- Verwijder je een item, dan worden de bijhorende hosts *genegeerd*, zodat de
  volgende sync het niet terugzet. Op de pagina van de koppeling zet je een host
  weer aan met **Weer opnemen**.
- Verdwijnt een host uit NPM, dan blijft hij in VaultX bewaard als *verdwenen*.
  Komt hij terug (zelfde NPM-id), dan herstelt de koppeling vanzelf.
- Je kunt ook items zonder NPM-host toevoegen (bv. een SaaS-dienst).

Elke sync, elke wijziging en elke geweigerde poging komt in de auditlog van de
organisatie (`npm.sync`, `npm_connection.*`, `npm_host.updated`, `application.*`).

## 5. Authentik-bescherming zetten

Op de pagina van een koppeling staat bij elke host zonder Authentik de knop
**Beschermen met Authentik**, en bij hosts die VaultX beschermde **Bescherming
weghalen**. VaultX zet dan het [officiële Authentik-patroon voor
NPM](https://docs.goauthentik.io/add-secure-apps/providers/proxy/server_nginx/)
in de host.

### Vooraf

1. **In Authentik**: een *Proxy Provider* in forward-auth-modus voor het domein
   (*single application* met de externe URL van de host, of *domain level*),
   met een applicatie, en toegewezen aan een outpost. Kies je op de koppeling
   een outpost, dan maakt VaultX dat zelf aan (sectie 6). Zonder provider
   antwoordt de outpost niet met een aanmelding; de controle achteraf faalt dan
   en VaultX zet terug.
2. **Op de koppeling** (*Bewerken*):
   - **VaultX mag proxy hosts in deze NPM wijzigen** aanzetten (standaard uit), en
     het NPM-account *Proxy Hosts: Manage* geven.
   - **Authentik-outpost, gezien vanuit NPM**: het adres waarop nginx in de
     NPM-container de outpost bereikt, zonder pad. Voor de ingebouwde outpost is
     dat de Authentik-server, bv. `http://authentik-server:9000` op een gedeeld
     Docker-netwerk of `http://192.168.1.20:9000`.
   - **Controleadres van NPM** en de poorten: waar VaultX NPM's proxypoorten
     (80/443) bereikt om een host te controleren. Leeg = de host van de
     beheer-URL. VaultX stuurt de domeinnaam mee als Host-header en SNI, dus DNS
     hoeft niet naar NPM te wijzen.

### Wat VaultX wijzigt

- In *Advanced* van de host, achter wat er al staat: `proxy_buffers`,
  `proxy_buffer_size` en `port_in_redirect off` (enkel als ze er nog niet
  staan), `location /outpost.goauthentik.io` en `location @goauthentik_proxy_signin`.
- In elke custom location vooraan: `auth_request` naar de outpost en de
  `X-authentik-*`-headers (username, groups, entitlements, email, name, uid)
  naar de applicatie. Heeft de host geen custom location `/`, dan maakt VaultX
  er een aan met dezelfde forward host, poort en scheme. NPM neemt daarin zelf
  de access list en websocket-instellingen van de host over.
- Alles staat tussen
  `# >>> vaultx:authentik (beheerd door VaultX, niet met de hand wijzigen)` en
  `# <<< vaultx:authentik`. Een location die VaultX aanmaakte, draagt
  `# vaultx:created-location`. **Bescherming weghalen** haalt precies die blokken
  en locations weer weg; wat je zelf schreef, blijft staan.
- VaultX zet nooit een eigen `location /` in *Advanced*: dan laat NPM zijn
  standaardlocatie weg, en daarmee de access list.

### Controles vooraf

VaultX toont eerst een voorbeeld: de stappen, de config ervoor en erna, en wat
het tegenhoudt. Het weigert als:

| Code | Waarom |
| --- | --- |
| `write_disabled` | Schrijven staat uit op de koppeling. |
| `no_outpost_url` | Geen outpost-URL ingesteld. |
| `already_protected`, `already_managed` | De host heeft al Authentik (met de hand of door VaultX). |
| `foreign_auth_request` | Er staat al een `auth_request` naar iets anders; nginx staat er één per location toe. |
| `outpost_location_exists`, `custom_root_location` | *Advanced* heeft al een outpost-location of een eigen `location /`. |
| `satisfy_any` | Access list op "Satisfy Any": een toegelaten IP-adres zou Authentik overslaan. |
| `syntax`, `location_invalid` | Onevenwichtige accolades of een location zonder pad in de bestaande config. |
| `host_disabled`, `nginx_offline` | De host staat uit of is nu al offline in NPM. |

En het waarschuwt (`no_tls`, `tls_not_forced`, `app_has_login` voor een app
die zelf al via OIDC/SAML aanmeldt, `no_probe` voor een host met enkel
wildcard-domeinen, `block_exploits_http` als *Block Common Exploits* aanstaat op
een host die over http bereikbaar is: NPM weigert dan de aanmeldredirect
`?rd=http://…` met 403, gezien tegen NPM 2.16.0).

### Uitvoeren, controleren, terugzetten

1. VaultX leest de host opnieuw. Wijzigde hij in NPM sinds het voorbeeld, dan
   stopt het.
2. Het spreekt de host aan zonder sessie (controle vooraf). Lukt dat niet, dan
   stopt het, tenzij je *Controleren* uitvinkt.
3. Het schrijft de nieuwe config naar NPM. NPM test die met `nginx -t`; bij een
   fout zet NPM de host offline en zet VaultX meteen de vorige config terug.
4. Controle achteraf: na beschermen moet een bezoeker zonder sessie een
   doorverwijzing naar `/outpost.goauthentik.io/start` krijgen. Een 5xx of iets
   anders betekent terugzetten. Na weghalen mag er geen nieuwe 5xx zijn.
5. Lukt ook het terugzetten niet, dan staat de vorige config in het journaal,
   om met de hand terug te zetten.

Per host loopt er maar één wijziging tegelijk, ook met meerdere VaultX-nodes.
Een wijziging die langer dan 10 minuten op *bezig* blijft staan (bv. VaultX
herstartte), wordt *onderbroken*; kijk die host dan na in NPM.

Elke poging staat in het journaal **Wijzigingen door VaultX** op de pagina van
de koppeling (enkel voor beheerders: het bevat de volledige config) en in de
auditlog als `npm_host.protect` / `npm_host.unprotect`, met uitkomst *success*,
*failure* of *denied*.

Grenzen:

- Wie in NPM dezelfde host wijzigt tussen stap 1 en 3, wordt overschreven:
  NPM kent geen voorwaardelijke update. Dat venster duurt zo lang als de
  controle vooraf, meestal minder dan een seconde.
- Kan VaultX de host niet aanspreken (enkel wildcard-domeinen, of NPM
  onbereikbaar op het controleadres), dan kan je uitvoeren zonder controle.
  VaultX zet dan enkel terug als nginx de config weigert.

## 6. Authentik-kant automatisch aanmaken

Kies je op de koppeling bij **Authentik-kant automatisch aanmaken** een
outpost, dan doet **Beschermen met Authentik** ook dit in Authentik, vóór VaultX
NPM wijzigt:

1. een **proxy provider** `VaultX: <domein>` in modus *Forward auth (single
   application)*, met als externe URL `https://<domein>` (of `http://` als de
   host geen certificaat heeft) en de standaardflows van Authentik;
2. een **applicatie** met de naam uit de catalogus en slug `vaultx-<domein>`;
3. de **toegang** die je in het voorbeeld kiest (zie hieronder);
4. de provider op de gekozen **outpost** zetten.

Daarna zet VaultX de config in NPM en controleert het met de echte outpost: een
bezoeker zonder sessie moet naar Authentik gaan. Een outpost laadt een nieuwe
provider pas na enkele seconden (tegen Authentik 2026.8.3 gemeten: 7 tot 11
seconden); VaultX probeert daarom tot 30 seconden. Faalt de controle, of een
stap in Authentik, dan draait VaultX beide kanten terug: de vorige config in
NPM en wat het in Authentik aanmaakte. **Bescherming weghalen** ruimt na de
wijziging in NPM op wat VaultX in Authentik aanmaakte; wat er al stond, blijft.

### Toegang

| Keuze | Gebonden Authentik-groepen |
| --- | --- |
| Leden van deze organisatie (standaard) | `vaultx:<org>`, `vaultx:<org>:<rol>` en `vaultx:<org>/<team>[:<rol>]` |
| Leden van team *X* | `vaultx:<org>/<x>` en `vaultx:<org>/<x>:<rol>` |
| Alle Authentik-gebruikers | geen binding (iedereen met een account) |

Het prefix `vaultx:` volgt `VAULTX_OIDC_GROUP_PREFIX`, dezelfde conventie als
voor de lidmaatschappen (docs/authentik.md). Bestaat er geen enkele passende
groep, dan weigert VaultX: maak de groep aan, of kies *Alle
Authentik-gebruikers*. Wie geen lid is, krijgt na de aanmelding van Authentik
"Permission denied".

### Bestaande providers

- Er is al een provider met dezelfde externe host: VaultX gebruikt die. Heeft
  hij nog geen applicatie, dan maakt VaultX er een; staat hij niet op de
  outpost, dan zet VaultX hem erop. Bij weghalen draait VaultX enkel die
  stappen terug; de provider zelf blijft.
- Een *domain level*-provider op de outpost dekt het domein al (cookie domain):
  VaultX maakt niets aan.
- Een provider in *proxy*-modus voor dat domein: VaultX weigert.

### Instellen

1. **Serviceaccount in Authentik.** Maak een rol met enkel deze globale
   rechten, een groep met die rol en een serviceaccount in die groep, en een
   API-token (*Directory → Tokens*, intent *API*) voor dat account:

   | Recht | Waarom |
   | --- | --- |
   | `authentik_providers_proxy.view_proxyprovider`, `add_proxyprovider`, `delete_proxyprovider` | Providers zoeken, aanmaken en opruimen |
   | `authentik_core.view_application`, `add_application`, `delete_application` | Applicatie aanmaken en opruimen |
   | `authentik_core.view_group` | Groepen voor de toegang vinden |
   | `authentik_policies.add_policybinding` | Groep aan de applicatie binden |
   | `authentik_outposts.view_outpost`, `change_outpost` | Provider op de outpost zetten |
   | `authentik_flows.view_flow` | De standaardflows vinden |

   Precies deze lijst is getest (de e2e-test maakt zo'n account). Gebruik geen
   superuser-token: daarmee kan VaultX alles in Authentik.
2. **Op de VaultX-server**: `VAULTX_AUTHENTIK_API_TOKEN=<token>` en, als
   Authentik niet op de host van `VAULTX_OIDC_ISSUER` draait,
   `VAULTX_AUTHENTIK_API_URL`. Andere flows kies je met
   `VAULTX_AUTHENTIK_AUTHORIZATION_FLOW` en `VAULTX_AUTHENTIK_INVALIDATION_FLOW`
   (slugs).
3. **Bij de outpost in Authentik**: vul `authentik_host` in met de publieke URL
   van Authentik (bij de ingebouwde outpost staat die leeg). Daarheen stuurt de
   outpost een browser om zich aan te melden. VaultX waarschuwt als die leeg is.
4. **Op de koppeling** (*Bewerken*): kies de outpost. De lijst komt live uit
   Authentik.

Grenzen:

- De provider dekt één domein. Heeft de host er meerdere, dan waarschuwt
  VaultX: op de andere domeinen geeft de outpost een fout.
- De providerlijst van een outpost wordt gelezen en meteen teruggeschreven;
  wie op hetzelfde moment in Authentik dezelfde outpost wijzigt, kan
  overschreven worden.
- Verwijder je de NPM-koppeling, dan blijven de Authentik-objecten staan. Haal
  eerst de bescherming weg als je ze kwijt wil.
- De auditlog (`npm_host.protect` / `npm_host.unprotect`) en het journaal tonen
  per wijziging welke provider, applicatie en groepen VaultX aanmaakte,
  terugdraaide of opruimde.

## API

| Methode | Pad | |
| --- | --- | --- |
| GET | `/api/v1/catalog?organization_id=&q=&status=` | Catalogus over alle zichtbare organisaties |
| POST, GET, PATCH, DELETE | `/api/v1/organizations/{org}/applications[/{id}]` | Catalogusitems |
| GET, POST | `/api/v1/organizations/{org}/npm-connections` | Koppelingen |
| GET, PATCH, DELETE | `/api/v1/organizations/{org}/npm-connections/{id}` | |
| POST | `/api/v1/organizations/{org}/npm-connections/{id}/sync` | Nu inlezen (502 als NPM faalt) |
| GET | `/api/v1/organizations/{org}/npm-connections/{id}/hosts` | Ontdekte hosts |
| PATCH | `/api/v1/organizations/{org}/npm-connections/{id}/hosts/{host}` | `{"ignored": true}` |
| GET | `/api/v1/organizations/{org}/npm-connections/{id}/hosts/{host}/protection?action=protect` | Voorbeeld: controles, stappen, config ervoor en erna |
| POST | `/api/v1/organizations/{org}/npm-connections/{id}/hosts/{host}/protection` | `{"action": "protect", "expected_modified_on": "...", "verify": true}`; antwoordt met de wijziging en haar status (`applied`, `rolled_back`, `rollback_failed`, `refused`) |
| GET | `/api/v1/organizations/{org}/npm-connections/{id}/changes?host_id=` | Journaal |
| GET | `/api/v1/organizations/{org}/authentik/outposts` | Proxy-outposts in Authentik, om op een koppeling te kiezen |

Fase 4: de koppeling heeft `authentik_outpost_pk` (leeg = niets aanmaken in
Authentik). Het voorbeeld en het uitvoeren nemen `access`: `organization`
(standaard), `team:<slug>` of `all`; het voorbeeld geeft in `authentik` wat
VaultX in Authentik doet, het journaal in `authentik` wat het deed.

Volledige schema's op `/api/docs`.

## Nog niet

- Access lists genereren, nieuwe proxy hosts aanmaken, meerdere hosts tegelijk
  beschermen.
- De toegang van een al beschermde host wijzigen (nu: weghalen en opnieuw
  beschermen), en OIDC-providers in plaats van forward auth aanmaken.
