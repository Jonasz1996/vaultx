// TOTP-codes (RFC 6238) en wachtwoorden genereren, volledig in de browser.

export interface TotpParams {
  secret: Uint8Array<ArrayBuffer>;
  digits: number;
  period: number;
  algorithm: "SHA-1" | "SHA-256" | "SHA-512";
  steam: boolean;
}

function base32(input: string): Uint8Array<ArrayBuffer> {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const clean = input.toUpperCase().replace(/[\s=-]/g, "");
  const out: number[] = [];
  let bits = 0;
  let value = 0;
  for (const ch of clean) {
    const idx = alphabet.indexOf(ch);
    if (idx < 0) throw new Error("Ongeldige TOTP-sleutel");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(out);
}

/** Leest een base32-sleutel, een otpauth://-URI of steam://-sleutel zoals Bitwarden. */
export function parseTotp(value: string): TotpParams | null {
  const s = value.trim();
  if (!s) return null;
  try {
    if (/^otpauth:\/\//i.test(s)) {
      const u = new URL(s);
      const p = u.searchParams;
      const alg = (p.get("algorithm") ?? "SHA1").toUpperCase();
      return {
        secret: base32(p.get("secret") ?? ""),
        digits: Math.min(10, Math.max(1, Number(p.get("digits")) || 6)),
        period: Math.max(1, Number(p.get("period")) || 30),
        algorithm: alg === "SHA256" ? "SHA-256" : alg === "SHA512" ? "SHA-512" : "SHA-1",
        steam: false,
      };
    }
    if (/^steam:\/\//i.test(s)) return { secret: base32(s.slice(8)), digits: 5, period: 30, algorithm: "SHA-1", steam: true };
    return { secret: base32(s), digits: 6, period: 30, algorithm: "SHA-1", steam: false };
  } catch {
    return null;
  }
}

const STEAM_CHARS = "23456789BCDFGHJKMNPQRTVWXY";

export async function totpCode(p: TotpParams, now = Date.now()): Promise<string> {
  const counter = Math.floor(now / 1000 / p.period);
  const msg = new Uint8Array(8);
  new DataView(msg.buffer).setUint32(4, counter >>> 0);
  new DataView(msg.buffer).setUint32(0, Math.floor(counter / 2 ** 32));
  const key = await crypto.subtle.importKey("raw", p.secret, { name: "HMAC", hash: p.algorithm }, false, ["sign"]);
  const h = new Uint8Array(await crypto.subtle.sign("HMAC", key, msg));
  const off = h[h.length - 1] & 0xf;
  let bin = ((h[off] & 0x7f) << 24) | (h[off + 1] << 16) | (h[off + 2] << 8) | h[off + 3];
  if (p.steam) {
    let out = "";
    for (let i = 0; i < 5; i++) {
      out += STEAM_CHARS[bin % STEAM_CHARS.length];
      bin = Math.floor(bin / STEAM_CHARS.length);
    }
    return out;
  }
  return String(bin % 10 ** p.digits).padStart(p.digits, "0");
}

export interface GeneratorOptions {
  length: number;
  upper: boolean;
  lower: boolean;
  digits: boolean;
  symbols: boolean;
}

export const GENERATOR_DEFAULTS: GeneratorOptions = { length: 20, upper: true, lower: true, digits: true, symbols: true };

const SETS = {
  upper: "ABCDEFGHJKLMNPQRSTUVWXYZ",
  lower: "abcdefghijkmnopqrstuvwxyz",
  digits: "23456789",
  symbols: "!@#$%^&*-_=+?",
};

/** Uniform willekeurig getal in [0, n) zonder modulo-bias. */
function randomBelow(n: number): number {
  const limit = Math.floor(0x1_0000_0000 / n) * n;
  const buf = new Uint32Array(1);
  for (;;) {
    crypto.getRandomValues(buf);
    if (buf[0] < limit) return buf[0] % n;
  }
}

/** Wachtwoord met minstens één teken uit elke gekozen groep. */
export function generatePassword(o: GeneratorOptions): string {
  const groups = (Object.keys(SETS) as (keyof typeof SETS)[]).filter((k) => o[k]).map((k) => SETS[k]);
  if (!groups.length) groups.push(SETS.lower);
  const length = Math.max(groups.length, Math.min(128, Math.round(o.length) || 20));
  const all = groups.join("");
  const chars = groups.map((g) => g[randomBelow(g.length)]);
  while (chars.length < length) chars.push(all[randomBelow(all.length)]);
  for (let i = chars.length - 1; i > 0; i--) {
    const j = randomBelow(i + 1);
    [chars[i], chars[j]] = [chars[j], chars[i]];
  }
  return chars.join("");
}
