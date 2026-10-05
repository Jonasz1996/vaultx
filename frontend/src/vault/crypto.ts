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

/** Normalisatie zoals de clients: e-mailadres zonder spaties, kleine letters. */
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
