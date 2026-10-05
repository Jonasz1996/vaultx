# App publiceren (fase 6)

Met **App publiceren** zet een beheerder van een organisatie een nieuwe app in
één stap online. VaultX:

1. maakt in Nginx Proxy Manager (NPM) een nieuwe proxy host aan: domein, de app
   erachter (schema, host, poort), een certificaat dat al in NPM staat, TLS
   afdwingen, HSTS, HTTP/2, websockets en "Block Common Exploits";
2. beschermt hem met Authentik, met dezelfde config en dezelfde Authentik-kant als
   **Beschermen met Authentik** ([npm.md](npm.md), secties 5 en 6): proxy provider,
   applicatie, groepsbinding voor de organisatie of een team, outpost;
3. zet het CSS-thema en de beveiligingsheaders in de app;
4. leest de host in en zet de app in de **catalogus**.

Daarna kan dezelfde beheerder de app ook weer **depubliceren**: VaultX verwijdert
de host uit NPM, ruimt in Authentik op wat het aanmaakte en haalt de app uit de
catalogus. VaultX verwijdert enkel hosts die het zelf publiceerde.

VaultX praat daarvoor enkel met NPM en Authentik, niet met de app zelf.

Getest tegen NPM 2.16.0 en Authentik 2026.8.3, met aanmelden in een echte
browser (`e2e/publish_e2e.py`, ook in CI).

## Vooraf

- Een NPM-koppeling met **VaultX mag wijzigen** aan. Het NPM-account heeft
  *Proxy Hosts: Manage* nodig, en *Certificates: View* om een certificaat te
  kunnen kiezen ([npm.md](npm.md), sectie 1).
- De **Authentik-outpost-URL** op de koppeling (hoe nginx in NPM de outpost
  bereikt) en, om de Authentik-kant automatisch te laten aanmaken, een **outpost**
  ([npm.md](npm.md), sectie 6). Zonder outpost maakt VaultX in Authentik niets aan;
  dan moet er al een provider zijn die het domein dekt, bv. een domain-level
  provider.
- Een **certificaat in NPM** dat het domein dekt, bv. een wildcard
  `*.example.be`. VaultX vraagt zelf geen Let's Encrypt-certificaten aan.
- Optioneel een **CSS-thema-sjabloon** op de koppeling, bv.
  `https://css.example.be/{app}.css`. `{app}` wordt de naam van de app in kleine
  letters met streepjes (*Proxmox VE* wordt `proxmox-ve`). Het thema blijft per
  app aan te passen in het formulier.

## In de UI

**NPM → koppeling → App publiceren.** Vul naam, domein en de app erachter in.
VaultX kiest zelf een certificaat dat het domein dekt (✓ in de lijst) en vult
het thema in volgens het sjabloon. **Voorbeeld** toont eerst de controles, de
stappen in Authentik, NPM en de catalogus, en de volledige config van de nieuwe
host. Pas met **Publiceren** verandert er iets.

Een gepubliceerde host krijgt in de lijst met hosts het label *gepubliceerd door
VaultX* en een knop **Depubliceren**. Elke publicatie staat in het journaal
*Wijzigingen door VaultX* en in de auditlog (`npm_host.publish`,
`npm_host.unpublish`).

## Wat VaultX in NPM aanmaakt

Voor `pve.example.be` naar `https://192.168.0.10:8006`, met wildcardcertificaat,
outpost `http://192.168.0.244:9000` en thema `https://css.example.be/proxmox.css`:

**Advanced van de host**

```nginx
# Gepubliceerd door VaultX
# vaultx.app = Proxmox VE
# >>> vaultx:authentik (beheerd door VaultX, niet met de hand wijzigen)
proxy_buffers 8 16k;
proxy_buffer_size 32k;
port_in_redirect off;

location /outpost.goauthentik.io {
    proxy_pass              http://192.168.0.244:9000/outpost.goauthentik.io;
    proxy_set_header        Host $host;
    proxy_set_header        X-Original-URL $scheme://$http_host$request_uri;
    add_header              Set-Cookie $auth_cookie;
    auth_request_set        $auth_cookie $upstream_http_set_cookie;
    proxy_pass_request_body off;
    proxy_set_header        Content-Length "";
}

location @goauthentik_proxy_signin {
    internal;
    add_header Set-Cookie $auth_cookie;
    return 302 /outpost.goauthentik.io/start?rd=$scheme://$http_host$request_uri;
}
# <<< vaultx:authentik
```

**Custom location `/`** naar de app, met in zijn Advanced:

```nginx
# >>> vaultx:authentik (beheerd door VaultX, niet met de hand wijzigen)
# vaultx:created-location
auth_request     /outpost.goauthentik.io/auth/nginx;
error_page       401 = @goauthentik_proxy_signin;
auth_request_set $auth_cookie $upstream_http_set_cookie;
add_header       Set-Cookie $auth_cookie;
auth_request_set $authentik_username $upstream_http_x_authentik_username;
...                                   (ook groups, entitlements, email, name, uid)
proxy_set_header X-authentik-username $authentik_username;
...
# <<< vaultx:authentik
# >>> vaultx:app (thema en headers, ingesteld bij publiceren)
sub_filter '</head>' '<link rel="stylesheet" type="text/css" href="https://css.example.be/proxmox.css"></head>';
sub_filter_once on;
proxy_set_header Accept-Encoding "";
add_header X-Xss-Protection "1; mode=block" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "no-referrer";
add_header Content-Security-Policy "frame-ancestors 'self'";
# <<< vaultx:app
```

Waarom een custom location `/` en geen `location / { ... }` in Advanced: met een
eigen `location /` in Advanced schakelt NPM zijn eigen location uit, en dan doen
access lists en de schakelaars voor websockets en HSTS niets meer. In de custom
location zet NPM zelf `proxy_pass`, de `Host`- en `X-Forwarded-*`-headers,
websockets en HSTS. Dezelfde headers nog eens zetten zou ze dubbel naar de app
of de browser sturen. HSTS komt daarom van de schakelaar in NPM, zonder
`includeSubDomains`.

`proxy_set_header Accept-Encoding ""` is nodig voor het thema: anders stuurt de
app gecomprimeerde HTML terug en vindt `sub_filter` de `</head>` niet.

## Controles

Het voorbeeld weigert (niets gewijzigd) als:

- het domein al op een proxy host in NPM staat, of geen geldige domeinnaam is;
- het gekozen certificaat het domein niet dekt (een wildcard dekt één niveau),
  niet meer bestaat of verlopen is;
- de Authentik-kant niet kan: geen groep `vaultx:<organisatie>` (of
  `vaultx:<organisatie>/<team>`) in Authentik, een provider in proxy-modus voor
  dat domein, de outpost bestaat niet meer (zie [npm.md](npm.md), sectie 6);
- schrijven uit staat op de koppeling, of de outpost-URL ontbreekt.

Het waarschuwt als VaultX de app zelf niet bereikt (VaultX probeert een TCP-
verbinding; NPM zit soms in een ander netwerk en kan het wel), als het
certificaat binnen 14 dagen verloopt, als er geen TLS is, of als de app zonder
Authentik online komt.

## Uitvoeren, controleren, terugdraaien

1. Eerst de Authentik-kant, zodat de host vanaf de eerste seconde beschermd
   online komt. Mislukt een stap in Authentik, dan draait VaultX terug wat het
   al aanmaakte en verandert NPM niet.
2. De host aanmaken in NPM, opnieuw lezen en nakijken dat nginx de config
   aanvaardde (`meta.nginx_online`).
3. De host aanspreken zoals een bezoeker zonder sessie, via de proxypoort van
   NPM (het controleadres van de koppeling): met Authentik hoort dat een
   doorverwijzing naar Authentik te geven. Een nieuwe provider laadt de outpost
   na enkele seconden; VaultX probeert tot 30 seconden.
4. Mislukt 2 of 3, dan verwijdert VaultX de nieuwe host weer uit NPM en draait
   het de Authentik-kant terug. Lukt verwijderen niet, dan staat de status op
   *Terugzetten mislukt* en zegt het journaal welke host met de hand weg moet.
5. Lukt alles, dan leest VaultX de host in, zet hij in de catalogus en onthoudt
   VaultX dat het hem publiceerde.

Er loopt per NPM-koppeling één publicatie tegelijk, ook over meerdere
VaultX-nodes heen.

## Depubliceren

Enkel voor hosts die VaultX publiceerde, en niet zolang de app een automatische
login heeft ([autologin.md](autologin.md); haal die eerst weg). VaultX
bewaart de volledige host in het journaal, verwijdert hem uit NPM, ruimt in
Authentik de provider en applicatie op die het aanmaakte, en haalt de app uit de
catalogus. Mislukt opruimen in Authentik, dan zegt het journaal wat er nog met
de hand weg moet.

**Bescherming weghalen** op een gepubliceerde host kan ook: dan blijft de app
online zonder Authentik, met thema en headers.

## API

| Methode | Pad | |
| --- | --- | --- |
| GET | `/api/v1/organizations/{org}/npm-connections/{id}/certificates` | Certificaten in NPM, zonder sleutels |
| POST | `/api/v1/organizations/{org}/npm-connections/{id}/publish/preview` | Voorbeeld: controles, stappen, de nieuwe host |
| POST | `/api/v1/organizations/{org}/npm-connections/{id}/publish` | Publiceren; antwoordt met de wijziging (`applied`, `rolled_back`, `rollback_failed`, `refused`) |
| GET | `/api/v1/organizations/{org}/npm-connections/{id}/hosts/{host}/unpublish` | Voorbeeld van depubliceren |
| POST | `/api/v1/organizations/{org}/npm-connections/{id}/hosts/{host}/unpublish` | Depubliceren |

Body van `publish` (en `publish/preview`):

```json
{
  "name": "Proxmox VE",
  "domain": "pve.example.be",
  "forward_scheme": "https",
  "forward_host": "192.168.0.10",
  "forward_port": 8006,
  "certificate_id": 3,
  "ssl_forced": true,
  "websockets": true,
  "block_exploits": true,
  "security_headers": true,
  "theme_css_url": "https://css.example.be/proxmox.css",
  "description": null,
  "protect": true,
  "access": "organization",
  "verify": true
}
```

Op de koppeling: `theme_css_template` (leeg = geen thema).

## Nog niet

- Let's Encrypt-certificaten aanvragen vanuit VaultX.
- Meerdere domeinen per app, access lists, eigen custom locations.
- Een gepubliceerde app wijzigen (upstream, thema): nu depubliceren en opnieuw
  publiceren, of in NPM zelf.
