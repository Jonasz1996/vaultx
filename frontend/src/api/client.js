// Dunne fetch-wrapper. Cookies zijn first-party (zelfde origin via nginx/Vite-proxy);
// elke wijzigende request stuurt de CSRF-header mee die de backend verplicht.
export class ApiError extends Error {
    status;
    code;
    constructor(status, code, message) {
        super(message);
        this.status = status;
        this.code = code;
    }
}
function withQuery(path, query) {
    if (!query)
        return path;
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null && v !== "")
            params.set(k, String(v));
    }
    const qs = params.toString();
    return qs ? `${path}?${qs}` : path;
}
async function request(method, path, body, query) {
    const headers = { Accept: "application/json" };
    if (method !== "GET")
        headers["X-VaultX-CSRF"] = "1";
    if (body !== undefined)
        headers["Content-Type"] = "application/json";
    const res = await fetch(withQuery(path, query), {
        method,
        headers,
        credentials: "same-origin",
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (res.status === 204)
        return undefined;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        const detail = typeof data.detail === "string"
            ? data.detail
            : Array.isArray(data.detail) && data.detail[0]?.msg
                ? `Ongeldige invoer (${data.detail[0].loc?.slice(-1)[0] ?? "veld"}): ${data.detail[0].msg}`
                : res.statusText;
        throw new ApiError(res.status, data.error ?? "error", detail);
    }
    return data;
}
export const api = {
    get: (path, query) => request("GET", path, undefined, query),
    post: (path, body) => request("POST", path, body ?? {}),
    patch: (path, body) => request("PATCH", path, body),
    put: (path, body) => request("PUT", path, body),
    delete: (path) => request("DELETE", path),
};
/** Loginpagina (wachtwoord of Authentik), daarna terug naar `next`. */
export function loginUrl(next = window.location.pathname + window.location.search + window.location.hash) {
    return `/login?next=${encodeURIComponent(next)}`;
}
export async function logout() {
    const res = await api.post("/auth/logout");
    window.location.assign(res.redirect_url);
}
