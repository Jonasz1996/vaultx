# Lees-API voor je dashboard

Met `GET /api/v1/stats` leest een ander programma, bijvoorbeeld je eigen dashboard,
statistieken uit VaultX: hoeveel kluisitems, op hoeveel toestellen je bent ingelogd,
actieve sessies, apps uit NPM, links en logins van de laatste 24 uur.

Wat de API **niet** geeft: wachtwoorden, namen of URL's van kluisitems, de inhoud van
notities of links. Kluis en notities zijn end-to-end versleuteld; VaultX kent daar zelf
enkel het type en het aantal van, en meer geeft deze API ook niet. Met een API-sleutel kan
je bovendien niets wijzigen en niets anders opvragen dan `/api/v1/stats`.

## 1. Sleutel aanmaken

1. Open in VaultX je eigen account (klik rechtsboven op je naam).
2. Bij **API-sleutels voor je dashboard**: **Sleutel aanmaken**, geef een naam (bv. "Dashboard")
   en kies hoe lang hij geldig is.
3. Kopieer de sleutel (`vxk_...`). Je ziet hem maar één keer; VaultX bewaart enkel een hash.

In de lijst zie je per sleutel wanneer en vanaf welk IP hij laatst gebruikt werd. **Intrekken**
maakt hem meteen ongeldig. Aanmaken en intrekken staan in de auditlog.

Ben je instantiebeheerder, dan krijg je de cijfers van heel VaultX (`"scope": "instance"`,
met een blok `users`). Een gewone gebruiker krijgt enkel zijn eigen cijfers (`"scope": "user"`).

## 2. Uitlezen

```bash
curl -s -H "Authorization: Bearer vxk_..." https://vaultx.jouwdomein.be/api/v1/stats
```

Vanuit een backend (Node, Python, ...) werkt dat altijd. Wil je VaultX rechtstreeks vanuit de
browser van je dashboard aanroepen (fetch in de frontend), zet dan het adres van je dashboard in
`/etc/vaultx/vaultx.env` en herstart VaultX:

```bash
VAULTX_DASHBOARD_ORIGINS=https://dashboard.jouwdomein.be
```

```js
const res = await fetch("https://vaultx.jouwdomein.be/api/v1/stats", {
  headers: { Authorization: `Bearer ${import.meta.env.VITE_VAULTX_API_KEY}` },
});
const stats = await res.json();
console.log(stats.vault.items.total, stats.vault.devices.total);
```

Let op: een sleutel in de frontend kan iedereen die je dashboard opent, uitlezen. Haal de cijfers
liever op in de backend van je dashboard, of maak een sleutel aan op een account zonder
beheerdersrechten als je dashboard publiek bereikbaar is. Cookies gaan nooit mee (CORS zonder
credentials), dus een andere website kan niet met jouw VaultX-sessie lezen.

De lokale nginx laat per IP 60 aanvragen per minuut toe (met wat speling); daarboven komt `429`.
Eén keer per 30 à 60 seconden verversen is ruim genoeg.

## 3. Antwoord

```json
{
  "scope": "instance",
  "version": "0.7.0",
  "generated_at": "2026-11-16T21:50:00Z",
  "users": {
    "total": 3, "active": 3, "admins": 1, "with_authentik": 2, "with_password": 2,
    "logged_in_last_24h": 1, "locked": 0
  },
  "sessions": { "active": 2, "users": 1 },
  "vault": {
    "accounts": 1,
    "with_two_factor": 1,
    "last_change_at": "2026-11-16T20:12:03Z",
    "items": {
      "total": 412, "logins": 380, "notes": 20, "cards": 4, "identities": 3, "ssh_keys": 5,
      "other": 0, "favorites": 12, "archived": 0, "in_trash": 7
    },
    "folders": 14,
    "attachments": { "count": 6, "bytes": 1048576 },
    "devices": {
      "total": 3,
      "active_last_30_days": 2,
      "by_type": { "Edge-extensie": 1, "Android": 1, "Edge": 1 },
      "last_seen_at": "2026-11-16T21:40:00Z"
    }
  },
  "invuller": {
    "devices_with_authentik_unlock": 2, "users_with_authentik_unlock": 1,
    "fills_last_24h": 17, "failed_logins_last_24h": 0
  },
  "notes": { "total": 25, "in_trash": 1, "last_change_at": "2026-11-15T09:00:00Z" },
  "links": { "total": 950, "pinned": 10, "last_added_at": "2026-11-16T18:00:00Z", "collections": 22, "tags": 60 },
  "catalog": {
    "applications": 18,
    "by_status": { "protected": 12, "restricted": 3, "unprotected": 2, "offline": 1 },
    "auto_login": 5,
    "hosts": 21,
    "hosts_online": 20,
    "hosts_with_ssl": 21,
    "npm_connections": [
      { "name": "NPM", "enabled": true, "npm_version": "2.16.0",
        "last_sync_at": "2026-11-16T21:45:00Z", "last_sync_status": "ok" }
    ]
  },
  "activity_last_24h": {
    "events": 140, "logins": 3, "failed_logins": 0, "vault_logins": 4, "failed_vault_logins": 1
  }
}
```

| Veld | Betekenis |
| --- | --- |
| `sessions` | Actieve sessies in de webinterface van VaultX en hoeveel verschillende gebruikers |
| `vault.devices` | Bitwarden-apps, extensies en de webkluis die ingelogd zijn (niet ingetrokken); "actief" = gezien in de laatste 30 dagen |
| `vault.items` | Kluisitems per soort, zonder de prullenbak; `in_trash` apart |
| `catalog.by_status` | Zoals in de catalogus: `protected` (Authentik), `restricted`, `unprotected`, `offline`, `removed`, `unknown` |
| `invuller` | VaultX Invuller: browsers die de kluis openen met Authentik (niet ingetrokken), en invullingen en mislukte logins na invullen in de laatste 24 uur |
| `activity_last_24h` | Uit de auditlog: logins op VaultX en op de kluis, gelukt en mislukt |

Tijdstippen zijn in UTC (ISO 8601). Ook ingelogd in de webinterface werkt `/api/v1/stats`
(zonder sleutel), handig om het antwoord even te bekijken. De volledige beschrijving staat in
de OpenAPI-documentatie op `/api/docs` onder "stats".
