// Crypto in het Bitwarden-formaat voor VaultX Invuller (dezelfde regels als
// frontend/src/vault/crypto.ts van de webkluis):
//
//   master key = PBKDF2-SHA256(master password, salt, iteraties)  of
//                Argon2id(master password, SHA-256(salt), t, m, p)   (hash-wasm, globalThis.hashwasm)
//   login-hash = PBKDF2-SHA256(master key, master password, 1)       -> naar de server
//   stretched  = HKDF-Expand(master key, "enc") || HKDF-Expand(master key, "mac")
//   user key   = 64 bytes (32 AES + 32 HMAC), versleuteld met de stretched key (EncString type 2)
//
// EncString type 2 = AES-256-CBC + HMAC-SHA256 over iv||ciphertext. Een item kan een eigen
// itemsleutel hebben (cipher.key, versleuteld met de user key).
(function (root) {
  "use strict";

  const PBKDF2_MIN = 600000;
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const subtle = () => globalThis.crypto.subtle;

  function toB64(bytes) {
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin);
  }

  function fromB64(s) {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function concat(...parts) {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let o = 0;
    for (const p of parts) {
      out.set(p, o);
      o += p.length;
    }
    return out;
  }

  async function pbkdf2(password, salt, iterations) {
    const key = await subtle().importKey("raw", password, "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await subtle().deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256));
  }

  async function hmac(key, data) {
    const k = await subtle().importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return new Uint8Array(await subtle().sign("HMAC", k, data));
  }

  function sameBytes(a, b) {
    if (a.length !== b.length) return false;
    let d = 0;
    for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
    return d === 0;
  }

  /**
   * Master key afleiden. Ondergrenzen worden hier gecontroleerd: een (gecompromitteerde) server
   * die 1 iteratie terugstuurt, zou anders een login-hash krijgen die meteen te kraken is.
   */
  async function masterKey(params, password) {
    const pw = enc.encode(password);
    const salt = enc.encode(params.salt);
    if (params.kdf === 0) {
      if (!Number.isInteger(params.iterations) || params.iterations < PBKDF2_MIN)
        throw new Error("De server vraagt te weinig PBKDF2-iteraties; geweigerd.");
      return pbkdf2(pw, salt, params.iterations);
    }
    if (params.kdf === 1) {
      const { iterations, memory, parallelism } = params;
      if (!Number.isInteger(iterations) || iterations < 2 || !(memory >= 16) || !(parallelism >= 1))
        throw new Error("De server vraagt te zwakke Argon2id-instellingen; geweigerd.");
      const hw = globalThis.hashwasm;
      if (!hw || !hw.argon2id) throw new Error("Argon2id is niet geladen");
      const out = await hw.argon2id({
        password: pw,
        salt: new Uint8Array(await subtle().digest("SHA-256", salt)),
        iterations,
        memorySize: memory * 1024,
        parallelism,
        hashLength: 32,
        outputType: "binary",
      });
      return new Uint8Array(out);
    }
    throw new Error("Onbekende sleutelafleiding");
  }

  async function loginHash(mkey, password) {
    return toB64(await pbkdf2(mkey, enc.encode(password), 1));
  }

  async function stretch(mkey) {
    const e = await hmac(mkey, concat(enc.encode("enc"), new Uint8Array([1])));
    const m = await hmac(mkey, concat(enc.encode("mac"), new Uint8Array([1])));
    return concat(e, m);
  }

  function parseEnc(encString) {
    const m = /^2\.([^|]+)\|([^|]+)\|([^|]+)$/.exec(encString || "");
    if (!m) throw new Error("Onbekend versleutelingsformaat");
    return [fromB64(m[1]), fromB64(m[2]), fromB64(m[3])];
  }

  /** EncString type 2 ontsleutelen met een sleutel van 64 bytes; gooit bij een foute MAC. */
  async function decryptBytes(encString, key) {
    const [iv, ct, mac] = parseEnc(encString);
    if (!sameBytes(await hmac(key.slice(32, 64), concat(iv, ct)), mac)) throw new Error("Verkeerde sleutel");
    const aes = await subtle().importKey("raw", key.slice(0, 32), "AES-CBC", false, ["decrypt"]);
    return new Uint8Array(await subtle().decrypt({ name: "AES-CBC", iv }, aes, ct));
  }

  async function encryptBytes(plain, key) {
    const iv = globalThis.crypto.getRandomValues(new Uint8Array(16));
    const aes = await subtle().importKey("raw", key.slice(0, 32), "AES-CBC", false, ["encrypt"]);
    const ct = new Uint8Array(await subtle().encrypt({ name: "AES-CBC", iv }, aes, plain));
    const mac = await hmac(key.slice(32, 64), concat(iv, ct));
    return `2.${toB64(iv)}|${toB64(ct)}|${toB64(mac)}`;
  }

  async function decryptString(encString, key) {
    return dec.decode(await decryptBytes(encString, key));
  }

  /** De user key (64 bytes) uitpakken met de master key. */
  async function openUserKey(mkey, encUserKey) {
    const raw = await decryptBytes(encUserKey, await stretch(mkey));
    if (raw.length !== 64) throw new Error("Onverwachte user key");
    return raw;
  }

  const FALSE = new Set(["nee", "no", "false", "0", "uit", "off"]);

  /**
   * Een login-item ontsleutelen tot wat de invuller nodig heeft. Andere types en items in de
   * prullenbak geven null. Velden die niet te ontsleutelen zijn, worden leeg.
   */
  async function decryptLogin(raw, userKey) {
    if (!raw || raw.type !== 1 || raw.deletedDate) return null;
    let key = userKey;
    if (raw.key) key = await decryptBytes(raw.key, userKey);
    const d = async (v) => {
      if (!v) return "";
      try {
        return await decryptString(v, key);
      } catch {
        return "";
      }
    };
    const login = raw.login || {};
    const fields = [];
    for (const f of raw.fields || []) fields.push({ name: await d(f.name), value: await d(f.value) });
    const flag = (name) => {
      const f = fields.find((x) => x.name.trim().toLowerCase() === name);
      return f ? !FALSE.has(f.value.trim().toLowerCase()) : true;
    };
    const uris = [];
    for (const u of login.uris || []) uris.push({ uri: await d(u.uri), match: typeof u.match === "number" ? u.match : null });
    return {
      id: raw.id,
      name: await d(raw.name),
      username: await d(login.username),
      password: await d(login.password),
      totp: await d(login.totp),
      uris,
      reprompt: raw.reprompt === 1,
      // Eigen velden: "vx:invullen = nee" nooit automatisch, "vx:enter = nee" wel invullen, niet versturen.
      autofill: flag("vx:invullen"),
      submit: flag("vx:enter"),
      revisionDate: raw.revisionDate || null,
    };
  }

  const api = {
    toB64,
    fromB64,
    masterKey,
    loginHash,
    stretch,
    decryptBytes,
    encryptBytes,
    decryptString,
    openUserKey,
    decryptLogin,
    PBKDF2_MIN,
  };
  root.VXBw = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : self);
