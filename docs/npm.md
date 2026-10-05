# Nginx Proxy Manager koppelen

VaultX leest de proxy hosts uit Nginx Proxy Manager (NPM) en vult daarmee de
**applicatiecatalogus**: welke applicaties er draaien, op welk domein, en hoe
je er binnenkomt (via Authentik of niet). In deze fase **leest** VaultX enkel;
er wordt niets in NPM gewijzigd.

Getest tegen NPM 2.16.0 (CI start bij elke push een echte NPM, zie
`e2e/npm_e2e.py`).

## 1. Een NPM-account voor VaultX

Maak in NPM onder *Users* een apart account aan, bijvoorbeeld
`vaultx@jouwdomein.be`:

| Recht | Waarde | Waarom |
| --- | --- | --- |
| Visibility | **All Items** | Met "Created Items Only" ziet VaultX enkel hosts die dit account zelf maakte |
| Proxy Hosts | View Only | VaultX leest enkel |
| Rest | Hidden | NPM geeft de access list (naam, "Satisfy Any") mee bij de host |

Zo getest op NPM 2.16.0.

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

Volledige schema's op `/api/docs`.

## Nog niet

Schrijven naar NPM (forward-auth-config uitrollen, access lists genereren)
komt later. Een mislukte schrijfactie zet een host in NPM meteen offline (NPM
verwijdert de config bij een nginx-fout), dus dat vraagt eerst een lokale
`nginx -t`-controle en automatisch terugzetten.
