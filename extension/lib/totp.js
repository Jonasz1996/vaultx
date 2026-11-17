// TOTP volgens RFC 6238 (HOTP RFC 4226), zoals de Bitwarden-clients: een otpauth://-URI of een
// los base32-geheim. Enkel WebCrypto, zodat het ook in Node getest kan worden.
(function (root) {
  "use strict";

  const ALGOS = { SHA1: "SHA-1", SHA256: "SHA-256", SHA512: "SHA-512" };

  function base32Decode(input) {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
    const clean = input.toUpperCase().replace(/[\s=-]/g, "");
    if (!clean || /[^A-Z2-7]/.test(clean)) throw new Error("Ongeldig TOTP-geheim");
    let bits = 0;
    let value = 0;
    const out = [];
    for (const ch of clean) {
      value = (value << 5) | alphabet.indexOf(ch);
      bits += 5;
      if (bits >= 8) {
        out.push((value >>> (bits - 8)) & 0xff);
        bits -= 8;
      }
    }
    return new Uint8Array(out);
  }

  /** otpauth://totp/...?secret=...&digits=&period=&algorithm= of een los geheim. */
  function parseTotp(raw) {
    if (typeof raw !== "string" || !raw.trim()) return null;
    const s = raw.trim();
    const cfg = { secret: s, digits: 6, period: 30, algorithm: "SHA1" };
    if (/^otpauth:\/\//i.test(s)) {
      let u;
      try {
        u = new URL(s);
      } catch {
        return null;
      }
      if (u.host.toLowerCase() !== "totp") return null; // hotp heeft een teller, niet voor invullen
      const q = u.searchParams;
      cfg.secret = q.get("secret") || "";
      const digits = Number(q.get("digits") || 6);
      const period = Number(q.get("period") || 30);
      const algorithm = (q.get("algorithm") || "SHA1").toUpperCase();
      if (!Number.isInteger(digits) || digits < 6 || digits > 10) return null;
      if (!Number.isInteger(period) || period < 1 || period > 600) return null;
      if (!ALGOS[algorithm]) return null;
      Object.assign(cfg, { digits, period, algorithm });
    } else if (/^steam:\/\//i.test(s)) {
      return null; // Steam Guard gebruikt letters, geen gewone TOTP
    }
    try {
      cfg.key = base32Decode(cfg.secret);
    } catch {
      return null;
    }
    return cfg;
  }

  async function hotp(key, counter, digits, algorithm) {
    const msg = new Uint8Array(8);
    let c = counter;
    for (let i = 7; i >= 0; i--) {
      msg[i] = c & 0xff;
      c = Math.floor(c / 256);
    }
    const subtle = globalThis.crypto.subtle;
    const k = await subtle.importKey("raw", key, { name: "HMAC", hash: ALGOS[algorithm] }, false, ["sign"]);
    const h = new Uint8Array(await subtle.sign("HMAC", k, msg));
    const o = h[h.length - 1] & 0x0f;
    const bin = ((h[o] & 0x7f) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
    return String(bin % 10 ** digits).padStart(digits, "0");
  }

  /** De huidige code en hoeveel seconden ze nog geldt, of null als het geen TOTP is. */
  async function totpCode(raw, now = Date.now()) {
    const cfg = parseTotp(raw);
    if (!cfg) return null;
    const step = Math.floor(now / 1000 / cfg.period);
    const code = await hotp(cfg.key, step, cfg.digits, cfg.algorithm);
    const remaining = cfg.period - (Math.floor(now / 1000) % cfg.period);
    return { code, remaining, digits: cfg.digits };
  }

  const api = { base32Decode, parseTotp, hotp, totpCode };
  root.VXTotp = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
