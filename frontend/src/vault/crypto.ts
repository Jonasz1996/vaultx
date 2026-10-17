// Sleutels voor een nieuwe kluis, afgeleid in de browser (WebCrypto), in het formaat van de
// Bitwarden-clients. Het master password en de onversleutelde sleutels verlaten de browser nooit;
// VaultX krijgt enkel de versleutelde user key, de versleutelde private key, de publieke sleutel
// en een afgeleide hash om logins te controleren.
//
//   master key  = PBKDF2-SHA256(master password, salt = e-mailadres, iteraties)
//   login-hash  = PBKDF2-SHA256(master key, master password, 1)            -> naar de server
//   stretched   = HKDF-Expand(master key, "enc") || HKDF-Expand(master key, "mac")
//   user key    = 64 willekeurige bytes (32 enc + 32 mac), versleuteld met de stretched key
//   RSA-2048    = sleutelpaar; private key (PKCS#8) versleuteld met de user key
//
// Versleuteling is EncString type 2: AES-256-CBC + HMAC-SHA256 over iv||ciphertext.
//
// Dit bestand gebruikt alleen globalThis.crypto, zodat de e2e-test het ook in Node kan draaien.

export const PBKDF2_ITERATIONS = 600_000;

export interface VaultEnrollPayload {
  salt: string;
  kdf: 0;
  kdf_iterations: number;
  master_password_hash: string;
  user_key: string;
  public_key: string;
  private_key: string;
}

const subtle = () => {
  const s = globalThis.crypto?.subtle;
  if (!s) throw new Error("WebCrypto is niet beschikbaar. Open VaultX via HTTPS.");
  return s;
};

const enc = new TextEncoder();

function toB64(data: ArrayBuffer | Uint8Array): string {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function concat(...parts: Uint8Array[]): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let offset = 0;
  for (const p of parts) {
    out.set(p, offset);
    offset += p.length;
  }
  return out;
}

async function pbkdf2(password: Uint8Array<ArrayBuffer>, salt: Uint8Array<ArrayBuffer>, iterations: number): Promise<Uint8Array<ArrayBuffer>> {
  const key = await subtle().importKey("raw", password, "PBKDF2", false, ["deriveBits"]);
  const bits = await subtle().deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
  return new Uint8Array(bits);
}

async function hmacSha256(key: Uint8Array<ArrayBuffer>, data: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const k = await subtle().importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await subtle().sign("HMAC", k, data));
}

/** HKDF-Expand voor één blok van 32 bytes (Bitwarden doet geen extract-stap). */
async function hkdfExpand32(prk: Uint8Array<ArrayBuffer>, info: string): Promise<Uint8Array<ArrayBuffer>> {
  return hmacSha256(prk, concat(enc.encode(info), new Uint8Array([1])));
}

/** EncString type 2. key = 64 bytes: eerste helft AES-sleutel, tweede helft MAC-sleutel. */
async function encryptType2(plain: Uint8Array<ArrayBuffer>, key: Uint8Array<ArrayBuffer>): Promise<string> {
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const aes = await subtle().importKey("raw", key.slice(0, 32), "AES-CBC", false, ["encrypt"]);
  const ct = new Uint8Array(await subtle().encrypt({ name: "AES-CBC", iv }, aes, plain));
  const mac = await hmacSha256(key.slice(32, 64), concat(iv, ct));
  return `2.${toB64(iv)}|${toB64(ct)}|${toB64(mac)}`;
}

function fromB64(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** Ontsleutelt een EncString type 2 met een sleutel van 64 bytes; gooit bij een foute MAC. */
async function decryptType2(encString: string, key: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const m = /^2\.([^|]+)\|([^|]+)\|([^|]+)$/.exec(encString);
  if (!m) throw new Error("Onbekend versleutelingsformaat");
  const [iv, ct, mac] = [fromB64(m[1]), fromB64(m[2]), fromB64(m[3])];
  if (!sameBytes(await hmacSha256(key.slice(32, 64), concat(iv, ct)), mac)) throw new Error("Verkeerde sleutel");
  const aes = await subtle().importKey("raw", key.slice(0, 32), "AES-CBC", false, ["decrypt"]);
  return new Uint8Array(await subtle().decrypt({ name: "AES-CBC", iv }, aes, ct));
}

/**
 * De user key van een ontgrendelde kluis, als niet-exporteerbare WebCrypto-sleutels.
 * Zo kan zelfs code in dezelfde pagina de ruwe sleutel niet meer uitlezen.
 */
export interface UserKey {
  aes: CryptoKey;
  mac: CryptoKey;
}

export interface UnlockParams {
  salt: string;
  kdf: number;
  iterations: number;
  /** Argon2id: geheugen in MiB en parallelisme. */
  memory?: number | null;
  parallelism?: number | null;
}

/** Bitwarden-standaard voor Argon2id. */
export const ARGON2_DEFAULTS = { iterations: 3, memory: 64, parallelism: 4 };

/**
 * Master key afleiden zoals de Bitwarden-clients.
 *   PBKDF2:   PBKDF2-SHA256(wachtwoord, salt, iteraties)
 *   Argon2id: Argon2id(wachtwoord, SHA-256(salt), iteraties, geheugen in KiB, parallelisme), 32 bytes
 * De ondergrenzen worden hier gecontroleerd: een (gecompromitteerde) server die 1 iteratie
 * terugstuurt, zou anders een login-hash krijgen die offline in een oogwenk te kraken is.
 */
async function deriveMasterKey(params: UnlockParams, password: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  const salt = enc.encode(params.salt);
  if (params.kdf === 0) {
    if (!Number.isInteger(params.iterations) || params.iterations < PBKDF2_ITERATIONS)
      throw new Error("De server vraagt te weinig PBKDF2-iteraties; ontgrendelen geweigerd.");
    return pbkdf2(password, salt, params.iterations);
  }
  if (params.kdf === 1) {
    const { iterations } = params;
    const memory = params.memory ?? 0;
    const parallelism = params.parallelism ?? 0;
    if (!Number.isInteger(iterations) || iterations < 2 || memory < 16 || parallelism < 1)
      throw new Error("De server vraagt te zwakke Argon2id-instellingen; ontgrendelen geweigerd.");
    const { argon2id } = await import("hash-wasm");
    const out = await argon2id({
      password,
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

/** Stap 1 van ontgrendelen: master key en login-hash afleiden (de hash gaat naar de server). */
export async function deriveLogin(params: UnlockParams, masterPassword: string) {
  const password = enc.encode(masterPassword);
  const masterKey = await deriveMasterKey(params, password);
  const loginHash = toB64(await pbkdf2(masterKey, password, 1));
  return { masterKey, loginHash };
}

async function stretchKey(masterKey: Uint8Array<ArrayBuffer>): Promise<Uint8Array<ArrayBuffer>> {
  return concat(await hkdfExpand32(masterKey, "enc"), await hkdfExpand32(masterKey, "mac"));
}

export interface PasswordChangePayload {
  current_master_password_hash: string;
  kdf: number;
  kdf_iterations: number;
  kdf_memory: number | null;
  kdf_parallelism: number | null;
  master_password_hash: string;
  user_key: string;
}

/**
 * Master password en/of sleutelafleiding wijzigen. De user key zelf blijft dezelfde (dus
 * alle items, mappen en notities blijven leesbaar); hij wordt enkel opnieuw ingepakt met de
 * nieuwe master key. `current` komt van deriveLogin met het huidige wachtwoord, `encUserKey`
 * van de server na controle van die login-hash.
 */
export async function changeMasterPassword(
  current: { masterKey: Uint8Array<ArrayBuffer>; loginHash: string },
  encUserKey: string,
  salt: string,
  newPassword: string,
  next: Omit<UnlockParams, "salt">,
): Promise<PasswordChangePayload> {
  const rawUserKey = await decryptType2(encUserKey, await stretchKey(current.masterKey));
  try {
    if (rawUserKey.length !== 64) throw new Error("Onverwachte user key");
    const fresh = await deriveLogin({ ...next, salt }, newPassword);
    return {
      current_master_password_hash: current.loginHash,
      kdf: next.kdf,
      kdf_iterations: next.iterations,
      kdf_memory: next.kdf === 1 ? (next.memory ?? null) : null,
      kdf_parallelism: next.kdf === 1 ? (next.parallelism ?? null) : null,
      master_password_hash: fresh.loginHash,
      user_key: await encryptType2(rawUserKey, await stretchKey(fresh.masterKey)),
    };
  } finally {
    rawUserKey.fill(0);
  }
}

/** Stap 2: de user key uitpakken die de server na controle van de login-hash teruggeeft. */
export async function openUserKey(masterKey: Uint8Array<ArrayBuffer>, encUserKey: string): Promise<UserKey> {
  const raw = await decryptType2(encUserKey, await stretchKey(masterKey));
  if (raw.length !== 64) throw new Error("Onverwachte user key");
  try {
    return {
      aes: await subtle().importKey("raw", raw.slice(0, 32), "AES-CBC", false, ["encrypt", "decrypt"]),
      mac: await subtle().importKey("raw", raw.slice(32, 64), { name: "HMAC", hash: "SHA-256" }, false, [
        "sign",
        "verify",
      ]),
    };
  } finally {
    raw.fill(0);
  }
}

/** Tekst versleutelen met de user key (EncString type 2, leesbaar voor de Bitwarden-SDK). */
export async function encryptText(text: string, key: UserKey): Promise<string> {
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const ct = new Uint8Array(await subtle().encrypt({ name: "AES-CBC", iv }, key.aes, enc.encode(text)));
  const mac = new Uint8Array(await subtle().sign("HMAC", key.mac, concat(iv, ct)));
  return `2.${toB64(iv)}|${toB64(ct)}|${toB64(mac)}`;
}

export async function decryptText(encString: string, key: UserKey): Promise<string> {
  const m = /^2\.([^|]+)\|([^|]+)\|([^|]+)$/.exec(encString);
  if (!m) throw new Error("Onbekend versleutelingsformaat");
  const [iv, ct, mac] = [fromB64(m[1]), fromB64(m[2]), fromB64(m[3])];
  if (!(await subtle().verify("HMAC", key.mac, mac, concat(iv, ct)))) throw new Error("Notitie is beschadigd of gewijzigd");
  return new TextDecoder().decode(await subtle().decrypt({ name: "AES-CBC", iv }, key.aes, ct));
}

/** Normalisatie zoals de clients: e-mailadres zonder spaties, kleine letters. */
/** Bestand versleutelen als EncArrayBuffer (zoals Bitwarden-bijlagen): 2 | iv | mac | ct. */
export async function encryptBytes(data: Uint8Array<ArrayBuffer>, key: UserKey): Promise<Uint8Array<ArrayBuffer>> {
  const iv = globalThis.crypto.getRandomValues(new Uint8Array(16));
  const ct = new Uint8Array(await subtle().encrypt({ name: "AES-CBC", iv }, key.aes, data));
  const mac = new Uint8Array(await subtle().sign("HMAC", key.mac, concat(iv, ct)));
  return concat(new Uint8Array([2]), iv, mac, ct);
}

export async function decryptBytes(buf: Uint8Array<ArrayBuffer>, key: UserKey): Promise<Uint8Array<ArrayBuffer>> {
  if (buf.length < 65 || buf[0] !== 2) throw new Error("Onbekend versleutelingsformaat");
  const iv = buf.slice(1, 17);
  const mac = buf.slice(17, 49);
  const ct = buf.slice(49);
  if (!(await subtle().verify("HMAC", key.mac, mac, concat(iv, ct)))) throw new Error("Bijlage is beschadigd of gewijzigd");
  return new Uint8Array(await subtle().decrypt({ name: "AES-CBC", iv }, key.aes, ct));
}

export function normalizeSalt(email: string): string {
  return email.trim().toLowerCase();
}

export async function createVaultKeys(
  email: string,
  masterPassword: string,
  iterations = PBKDF2_ITERATIONS,
): Promise<VaultEnrollPayload> {
  const salt = normalizeSalt(email);
  const password = enc.encode(masterPassword);
  const masterKey = await pbkdf2(password, enc.encode(salt), iterations);
  const loginHash = await pbkdf2(masterKey, password, 1);
  const stretched = concat(await hkdfExpand32(masterKey, "enc"), await hkdfExpand32(masterKey, "mac"));

  const userKey = globalThis.crypto.getRandomValues(new Uint8Array(64));
  const rsa = await subtle().generateKey(
    { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-1" },
    true,
    ["encrypt", "decrypt"],
  );
  const spki = await subtle().exportKey("spki", rsa.publicKey);
  const pkcs8 = new Uint8Array(await subtle().exportKey("pkcs8", rsa.privateKey));

  return {
    salt,
    kdf: 0,
    kdf_iterations: iterations,
    master_password_hash: toB64(loginHash),
    user_key: await encryptType2(userKey, stretched),
    public_key: toB64(spki),
    private_key: await encryptType2(pkcs8, userKey),
  };
}

/** Grove sterkte-indicatie; de echte bescherming is de lengte. */
export function passwordProblems(pw: string, email: string): string[] {
  const problems: string[] = [];
  if (pw.length < 12) problems.push("minstens 12 tekens");
  if (pw.trim() !== pw) problems.push("geen spaties vooraan of achteraan");
  const local = email.split("@")[0].toLowerCase();
  if (local.length >= 3 && pw.toLowerCase().includes(local)) problems.push("niet je gebruikersnaam of e-mailadres");
  return problems;
}
