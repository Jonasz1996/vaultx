// Kluisitems in het draadformaat van Bitwarden, ontsleuteld tot iets wat de webkluis kan tonen
// en bewerken, en weer versleuteld. Elk tekstveld is een EncString met de user key, of met de
// eigen sleutel van het item als de client die gezet heeft ("key", versleuteld met de user key).
// Velden die de webkluis niet kent (passkeys, autofill-instellingen …) blijven bij het opslaan
// ongewijzigd staan, zodat de Bitwarden-apps niets verliezen.
import { decryptBytes, decryptText, decryptToBytes, encryptBytes, encryptFromBytes, encryptText, importSymmetricKey, sha256B64, } from "./crypto";
export const CIPHER_TYPES = {
    1: "Login",
    2: "Notitie",
    3: "Kaart",
    4: "Identiteit",
    5: "SSH-sleutel",
};
export const FIELD_TYPES = { 0: "Tekst", 1: "Verborgen", 2: "Ja/nee" };
export const IDENTITY_FIELDS = [
    ["title", "Aanspreking"],
    ["firstName", "Voornaam"],
    ["middleName", "Tweede voornaam"],
    ["lastName", "Achternaam"],
    ["username", "Gebruikersnaam"],
    ["company", "Bedrijf"],
    ["email", "E-mailadres"],
    ["phone", "Telefoon"],
    ["ssn", "Rijksregisternummer"],
    ["passportNumber", "Paspoortnummer"],
    ["licenseNumber", "Rijbewijsnummer"],
    ["address1", "Adres"],
    ["address2", "Adres 2"],
    ["address3", "Adres 3"],
    ["postalCode", "Postcode"],
    ["city", "Gemeente"],
    ["state", "Provincie"],
    ["country", "Land"],
];
const str = (v) => (typeof v === "string" && v ? v : null);
const obj = (v) => v && typeof v === "object" && !Array.isArray(v) ? v : {};
const arr = (v) => (Array.isArray(v) ? v : []);
export function emptyItem(type, folderId = null) {
    return {
        id: "",
        type,
        folderId,
        favorite: false,
        reprompt: false,
        name: "",
        notes: "",
        login: { username: "", password: "", totp: "", uris: [{ uri: "", match: null }], passwordRevisionDate: null },
        card: { cardholderName: "", brand: "", number: "", expMonth: "", expYear: "", code: "" },
        identity: Object.fromEntries(IDENTITY_FIELDS.map(([k]) => [k, ""])),
        sshKey: { privateKey: "", publicKey: "", keyFingerprint: "" },
        fields: [],
        passwordHistory: [],
        attachments: [],
        revisionDate: "",
        creationDate: "",
        deletedDate: null,
        raw: null,
        broken: false,
    };
}
/** De sleutel waarmee de velden van dit item versleuteld zijn. */
export async function itemKey(raw, userKey) {
    const k = raw ? str(raw.key) : null;
    return k ? importSymmetricKey(await decryptToBytes(k, userKey)) : userKey;
}
export async function decryptCipher(raw, userKey) {
    const view = emptyItem((raw.type in CIPHER_TYPES ? raw.type : 2), raw.folderId);
    let broken = false;
    let key;
    try {
        key = await itemKey(raw, userKey);
    }
    catch {
        return { ...view, id: raw.id, name: "(niet te ontsleutelen)", raw, broken: true };
    }
    const dec = async (v) => {
        const s = str(v);
        if (!s)
            return "";
        try {
            return await decryptText(s, key);
        }
        catch {
            broken = true;
            return "";
        }
    };
    view.id = raw.id;
    view.favorite = Boolean(raw.favorite);
    view.reprompt = raw.reprompt === 1;
    view.name = (await dec(raw.name)) || (broken ? "(niet te ontsleutelen)" : "");
    view.notes = await dec(raw.notes);
    const login = obj(raw.login);
    view.login = {
        username: await dec(login.username),
        password: await dec(login.password),
        totp: await dec(login.totp),
        uris: await Promise.all(arr(login.uris).map(async (u) => ({ uri: await dec(u.uri), match: typeof u.match === "number" ? u.match : null }))),
        passwordRevisionDate: str(login.passwordRevisionDate),
    };
    const card = obj(raw.card);
    view.card = {
        cardholderName: await dec(card.cardholderName),
        brand: await dec(card.brand),
        number: await dec(card.number),
        expMonth: await dec(card.expMonth),
        expYear: await dec(card.expYear),
        code: await dec(card.code),
    };
    const identity = obj(raw.identity);
    for (const [k] of IDENTITY_FIELDS)
        view.identity[k] = await dec(identity[k]);
    const ssh = obj(raw.sshKey);
    view.sshKey = {
        privateKey: await dec(ssh.privateKey),
        publicKey: await dec(ssh.publicKey),
        keyFingerprint: await dec(ssh.keyFingerprint),
    };
    view.fields = await Promise.all(arr(raw.fields).map(async (f) => ({
        name: await dec(f.name),
        value: await dec(f.value),
        type: typeof f.type === "number" ? f.type : 0,
        linkedId: typeof f.linkedId === "number" ? f.linkedId : null,
    })));
    view.passwordHistory = await Promise.all(arr(raw.passwordHistory).map(async (h) => ({
        password: await dec(h.password),
        lastUsedDate: str(h.lastUsedDate) ?? "",
    })));
    view.attachments = await Promise.all((raw.attachments ?? []).map(async (a) => ({
        id: a.id,
        fileName: (await dec(a.fileName)) || "bijlage",
        sizeName: a.sizeName,
        url: a.url,
        key: a.key,
    })));
    view.revisionDate = raw.revisionDate;
    view.creationDate = raw.creationDate;
    view.deletedDate = raw.deletedDate;
    view.raw = raw;
    view.broken = broken;
    return view;
}
export async function decryptFolder(raw, userKey) {
    try {
        return { id: raw.id, name: await decryptText(raw.name, userKey) };
    }
    catch {
        return { id: raw.id, name: "(niet te ontsleutelen)" };
    }
}
// Velden die de server beheert of die we bij opslaan opnieuw zetten.
const SERVER_FIELDS = new Set([
    "id",
    "organizationId",
    "edit",
    "viewPassword",
    "permissions",
    "organizationUseTotp",
    "collectionIds",
    "attachments",
    "revisionDate",
    "creationDate",
    "deletedDate",
    "object",
]);
/** Het item versleutelen tot een request voor POST /api/ciphers of PUT /api/ciphers/{id}. */
export async function encryptCipher(view, userKey) {
    const raw = view.raw;
    const key = await itemKey(raw, userKey);
    const enc = async (v) => (v ? encryptText(v, key) : null);
    const base = {};
    for (const [k, v] of Object.entries(raw ?? {}))
        if (!SERVER_FIELDS.has(k))
            base[k] = v;
    const body = {
        ...base,
        type: view.type,
        folderId: view.folderId,
        favorite: view.favorite,
        reprompt: view.reprompt ? 1 : 0,
        key: raw ? (str(raw.key) ?? null) : null,
        name: await encryptText(view.name || "Naamloos", key),
        notes: await enc(view.notes),
        fields: view.fields.length
            ? await Promise.all(view.fields.map(async (f) => ({
                name: await enc(f.name),
                value: await enc(f.value),
                type: f.type,
                linkedId: f.linkedId,
            })))
            : null,
        passwordHistory: view.passwordHistory.length
            ? await Promise.all(view.passwordHistory.map(async (h) => ({ password: await encryptText(h.password, key), lastUsedDate: h.lastUsedDate })))
            : null,
        login: null,
        card: null,
        identity: null,
        secureNote: null,
        sshKey: null,
        lastKnownRevisionDate: raw?.revisionDate ?? null,
    };
    if (view.type === 1) {
        const prev = obj(raw?.login);
        body.login = {
            ...prev,
            username: await enc(view.login.username),
            password: await enc(view.login.password),
            totp: await enc(view.login.totp),
            passwordRevisionDate: view.login.passwordRevisionDate,
            uris: await Promise.all(view.login.uris
                .filter((u) => u.uri.trim())
                .map(async (u) => ({
                uri: await encryptText(u.uri.trim(), key),
                uriChecksum: await encryptText(await sha256B64(u.uri.trim()), key),
                match: u.match,
            }))),
        };
    }
    else if (view.type === 2) {
        body.secureNote = { ...obj(raw?.secureNote), type: 0 };
    }
    else if (view.type === 3) {
        const c = view.card;
        body.card = {
            cardholderName: await enc(c.cardholderName),
            brand: await enc(c.brand),
            number: await enc(c.number),
            expMonth: await enc(c.expMonth),
            expYear: await enc(c.expYear),
            code: await enc(c.code),
        };
    }
    else if (view.type === 4) {
        const out = {};
        for (const [k] of IDENTITY_FIELDS)
            out[k] = await enc(view.identity[k]);
        body.identity = out;
    }
    else if (view.type === 5) {
        body.sshKey = {
            privateKey: await enc(view.sshKey.privateKey),
            publicKey: await enc(view.sshKey.publicKey),
            keyFingerprint: await enc(view.sshKey.keyFingerprint),
        };
    }
    return body;
}
/** Oud wachtwoord bewaren in de geschiedenis wanneer het verandert (zoals Bitwarden, max. 5). */
export function withPasswordHistory(edited, original) {
    if (!original || edited.type !== 1)
        return edited;
    const old = original.login.password;
    if (!old || old === edited.login.password)
        return edited;
    const now = new Date().toISOString();
    return {
        ...edited,
        login: { ...edited.login, passwordRevisionDate: now },
        passwordHistory: [{ password: old, lastUsedDate: now }, ...edited.passwordHistory].slice(0, 5),
    };
}
/** Pad (zonder host) van een URL die de server absoluut teruggeeft, zodat alles same-origin blijft. */
export function localPath(url) {
    try {
        const u = new URL(url, window.location.origin);
        return u.pathname + u.search;
    }
    catch {
        return url;
    }
}
/** Bijlage downloaden en in de browser ontsleutelen. */
export async function decryptAttachment(item, att, userKey, data) {
    const key = await itemKey(item.raw, userKey);
    const attKey = att.key ? await importSymmetricKey(await decryptToBytes(att.key, key)) : key;
    return decryptBytes(data, attKey);
}
/** Bijlage versleutelen met een nieuwe eigen sleutel. */
export async function encryptAttachment(item, userKey, fileName, data) {
    const key = await itemKey(item.raw, userKey);
    const raw = crypto.getRandomValues(new Uint8Array(64));
    const encKey = await encryptFromBytes(raw, key);
    const attKey = await importSymmetricKey(raw);
    return {
        fileName: await encryptText(fileName, key),
        key: encKey,
        data: await encryptBytes(data, attKey),
    };
}
/** Domein voor het website-icoon. */
export function iconDomain(item) {
    for (const u of item.login.uris) {
        const s = u.uri.trim();
        if (!s)
            continue;
        try {
            const host = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(s) ? s : `https://${s}`).hostname;
            if (host && host.includes("."))
                return host;
        }
        catch {
            /* geen geldige URL */
        }
    }
    return null;
}
/** Link die je kan openen (enkel http/https). */
export function launchUrl(uri) {
    const s = uri.trim();
    if (!s)
        return null;
    const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(s) ? s : `https://${s}`;
    try {
        const u = new URL(withScheme);
        return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
    }
    catch {
        return null;
    }
}
/** Korte tweede regel in de lijst. */
export function subtitle(item) {
    switch (item.type) {
        case 1:
            return item.login.username;
        case 3:
            return [item.card.brand, item.card.number ? `*${item.card.number.slice(-4)}` : ""].filter(Boolean).join(", ");
        case 4:
            return [item.identity.firstName, item.identity.lastName].filter(Boolean).join(" ");
        case 5:
            return item.sshKey.keyFingerprint;
        default:
            return item.notes.split("\n")[0].slice(0, 80);
    }
}
