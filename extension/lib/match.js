// URL-regels van VaultX Invuller: wanneer mag een item op een pagina ingevuld worden?
//
// Strenger dan Bitwarden, met opzet:
//  * exact dezelfde host en poort als een URI van het item (geen andere subdomeinen, geen
//    "basisdomein"-match): login.example.com vult niet in op evil.example.com;
//  * het schema: een https-item nooit op een http-pagina; een http-item mag wel op https;
//  * een URI zonder schema geldt als https;
//  * Bitwarden-matchtypes: 5 (nooit) telt niet mee, 2 (begint met) en 3 (exact) gelden bovenop
//    de host-regel, 4 (reguliere expressie) ook, maar enkel samen met dezelfde host.
//
// Gedeeld door de achtergrond (importScripts) en de tests in Node (require).
(function (root) {
  "use strict";

  const DEFAULT_PORTS = { "http:": "80", "https:": "443" };
  const MATCH = { DOMAIN: 0, HOST: 1, STARTS_WITH: 2, EXACT: 3, REGEX: 4, NEVER: 5 };

  /** URL ontleden; zonder schema geldt https. Enkel http(s). */
  function parse(raw) {
    if (typeof raw !== "string") return null;
    let s = raw.trim();
    if (!s) return null;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(s)) s = "https://" + s;
    let u;
    try {
      u = new URL(s);
    } catch {
      return null;
    }
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    if (!u.hostname) return null;
    return {
      url: u,
      scheme: u.protocol,
      host: u.hostname.toLowerCase().replace(/\.$/, ""),
      port: u.port || DEFAULT_PORTS[u.protocol],
      explicitPort: Boolean(u.port),
    };
  }

  /** Mag dit item (één URI) op deze pagina? */
  function uriMatches(itemUri, matchType, pageUrl) {
    if (matchType === MATCH.NEVER) return false;
    const item = parse(itemUri);
    const page = parse(pageUrl);
    if (!item || !page) return false;
    if (item.host !== page.host) return false;
    if (item.scheme === "https:" && page.scheme !== "https:") return false;
    // Een http-item op dezelfde site over https: beide op hun standaardpoort is dezelfde site.
    const upgraded = item.scheme === "http:" && page.scheme === "https:" && !item.explicitPort && !page.explicitPort;
    if (item.port !== page.port && !upgraded) return false;
    if (matchType === MATCH.STARTS_WITH) return page.url.href.startsWith(item.url.href);
    if (matchType === MATCH.EXACT) return page.url.href === item.url.href;
    if (matchType === MATCH.REGEX) {
      try {
        return new RegExp(itemUri).test(pageUrl);
      } catch {
        return false;
      }
    }
    return true;
  }

  /** Items (ontsleuteld, met login.uris) die op deze pagina passen. */
  function matchingItems(items, pageUrl) {
    return items.filter((item) =>
      (item.uris || []).some((u) => uriMatches(u.uri, typeof u.match === "number" ? u.match : null, pageUrl)),
    );
  }

  /** Origin (schema://host:poort) van een URL, of null. */
  function originOf(url) {
    const p = parse(url);
    return p ? `${p.scheme}//${p.host}:${p.port}` : null;
  }

  const api = { parse, uriMatches, matchingItems, originOf, MATCH };
  root.VXMatch = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
