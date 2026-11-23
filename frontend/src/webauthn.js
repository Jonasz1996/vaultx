// Beveiligingssleutels en passkeys (WebAuthn) in de browser. De server stuurt de opties met
// base64url-velden (zoals de Bitwarden-server); hier worden dat bytes voor de browser, en het
// antwoord van de sleutel weer JSON met base64url voor de server.
export function webAuthnSupported() {
    return typeof window !== "undefined" && "PublicKeyCredential" in window && !!navigator.credentials;
}
function fromB64url(value) {
    const b64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const bin = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++)
        out[i] = bin.charCodeAt(i);
    return out.buffer;
}
function toB64url(buf) {
    if (!buf)
        return undefined;
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (const b of bytes)
        bin += String.fromCharCode(b);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function descriptors(list) {
    return (list ?? []).map((c) => ({
        type: "public-key",
        id: fromB64url(c.id),
        transports: c.transports,
    }));
}
/** Uitleg in het Nederlands voor de fouten die de browser geeft. */
function explain(err) {
    const name = err?.name;
    if (name === "NotAllowedError")
        return new Error("Geannuleerd of te lang gewacht. Probeer opnieuw en raak je sleutel aan als hij knippert.");
    if (name === "InvalidStateError")
        return new Error("Deze sleutel is al gekoppeld.");
    if (name === "SecurityError")
        return new Error("Beveiligingssleutels werken hier niet. Open VaultX via zijn domeinnaam met https.");
    return err instanceof Error ? err : new Error(String(err));
}
/** Nieuwe sleutel koppelen (navigator.credentials.create). */
export async function createCredential(options) {
    const user = options.user;
    const publicKey = {
        ...options,
        challenge: fromB64url(options.challenge),
        user: { ...user, id: fromB64url(user.id) },
        excludeCredentials: descriptors(options.excludeCredentials),
    };
    let cred;
    try {
        cred = (await navigator.credentials.create({ publicKey }));
    }
    catch (err) {
        throw explain(err);
    }
    const response = cred.response;
    return {
        id: cred.id,
        rawId: toB64url(cred.rawId),
        type: cred.type,
        response: {
            clientDataJSON: toB64url(response.clientDataJSON),
            attestationObject: toB64url(response.attestationObject),
            transports: typeof response.getTransports === "function" ? response.getTransports() : [],
        },
        clientExtensionResults: cred.getClientExtensionResults(),
    };
}
/** Bevestigen met een gekoppelde sleutel (navigator.credentials.get). */
export async function getAssertion(options) {
    const publicKey = {
        ...options,
        challenge: fromB64url(options.challenge),
        allowCredentials: descriptors(options.allowCredentials),
    };
    let cred;
    try {
        cred = (await navigator.credentials.get({ publicKey }));
    }
    catch (err) {
        throw explain(err);
    }
    const response = cred.response;
    return {
        id: cred.id,
        rawId: toB64url(cred.rawId),
        type: cred.type,
        response: {
            clientDataJSON: toB64url(response.clientDataJSON),
            authenticatorData: toB64url(response.authenticatorData),
            signature: toB64url(response.signature),
            userHandle: toB64url(response.userHandle),
        },
    };
}
