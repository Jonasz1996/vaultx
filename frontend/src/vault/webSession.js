import { jsx as _jsx } from "react/jsx-runtime";
// Webkluis: de browser logt in op de Bitwarden-compatibele API van VaultX zoals de officiële
// webkluis van Bitwarden dat doet (/identity/connect/token met de afgeleide login-hash). De
// tokens en de user key leven enkel in het geheugen van dit tabblad; herladen, vergrendelen of
// 15 minuten niets doen gooit ze weg. Zo gelden de grens op mislukte pogingen, de
// tweestapsverificatie van de kluis, de apparatenlijst en de auditlog ook voor de webkluis.
import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AUTO_LOCK_MINUTES } from "../notes/session";
import { deriveLogin, openUserKey } from "./crypto";
export class VaultApiError extends Error {
    status;
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}
/** De server vraagt een tweede stap (tweestapsverificatie van de kluis). `securityKey` bevat de
 * opties voor een beveiligingssleutel als de kluis er een heeft (Bitwarden-provider 7). */
export class TwoFactorRequired extends Error {
    authenticator;
    securityKey;
    constructor(authenticator, securityKey) {
        super("Bevestig met je tweede stap.");
        this.authenticator = authenticator;
        this.securityKey = securityKey;
    }
}
const WebVaultContext = createContext(null);
const DEVICE_ID_KEY = "vaultx.webVault.deviceId";
const REMEMBER_KEY = "vaultx.webVault.twoFactor.";
function storage() {
    try {
        return window.localStorage;
    }
    catch {
        return null;
    }
}
/** Vaste identificatie van deze browser, zodat hij één regel in de apparatenlijst krijgt. */
function deviceIdentifier() {
    const s = storage();
    try {
        const existing = s?.getItem(DEVICE_ID_KEY);
        if (existing)
            return existing;
        const id = crypto.randomUUID();
        s?.setItem(DEVICE_ID_KEY, id);
        return id;
    }
    catch {
        return crypto.randomUUID();
    }
}
/** Bitwarden DeviceType voor deze browser (VaultX toont er een leesbare naam bij). */
export function browserDevice(ua = navigator.userAgent) {
    if (/Edg\//.test(ua))
        return { type: 12, name: "Edge" };
    if (/OPR\//.test(ua))
        return { type: 11, name: "Opera" };
    if (/Vivaldi\//.test(ua))
        return { type: 18, name: "Vivaldi" };
    if (/Firefox\//.test(ua))
        return { type: 10, name: "Firefox" };
    if (/Chrome\//.test(ua))
        return { type: 9, name: "Chrome" };
    if (/Safari\//.test(ua))
        return { type: 17, name: "Safari" };
    return { type: 14, name: "browser" };
}
function rememberedToken(email) {
    try {
        return storage()?.getItem(REMEMBER_KEY + email) ?? null;
    }
    catch {
        return null;
    }
}
function setRememberedToken(email, token) {
    try {
        if (token)
            storage()?.setItem(REMEMBER_KEY + email, token);
        else
            storage()?.removeItem(REMEMBER_KEY + email);
    }
    catch {
        /* geen opslag: de code wordt dan elke keer gevraagd */
    }
}
function errorMessage(data, fallback) {
    const model = (data.ErrorModel ?? data.errorModel);
    if (typeof model?.Message === "string")
        return model.Message;
    if (typeof data.message === "string")
        return data.message;
    if (typeof data.error_description === "string")
        return data.error_description;
    return fallback;
}
async function tokenRequest(form) {
    const res = await fetch("/identity/connect/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
        body: new URLSearchParams(form).toString(),
        credentials: "omit",
    });
    const data = (await res.json().catch(() => ({})));
    if (res.ok)
        return data;
    const providers = (data.TwoFactorProviders2 ?? data.twoFactorProviders2);
    if (providers)
        throw new TwoFactorRequired("0" in providers, providers["7"] ?? null);
    throw new VaultApiError(res.status, errorMessage(data, res.statusText));
}
export function WebVaultProvider({ children }) {
    const qc = useQueryClient();
    const [session, setSession] = useState(null);
    const [lockReason, setLockReason] = useState(null);
    const sessionRef = useRef(null);
    const lastActivity = useRef(Date.now());
    sessionRef.current = session;
    const lock = useCallback((reason) => {
        sessionRef.current = null;
        setSession(null);
        setLockReason(reason ?? null);
        // Ontsleutelde items niet in de cache laten staan.
        qc.removeQueries({ queryKey: ["webvault"] });
    }, [qc]);
    const unlock = useCallback(async (status, masterPassword, twoFactor) => {
        if (!status.email || !status.kdf_salt || status.kdf === null || !status.kdf_iterations)
            throw new Error("Je kluis is nog niet actief");
        const email = status.email;
        const params = {
            salt: status.kdf_salt,
            kdf: status.kdf,
            iterations: status.kdf_iterations,
            memory: status.kdf_memory,
            parallelism: status.kdf_parallelism,
        };
        const { masterKey, loginHash } = await deriveLogin(params, masterPassword);
        try {
            const device = browserDevice();
            const form = {
                grant_type: "password",
                username: email,
                password: loginHash,
                scope: "api offline_access",
                client_id: "web",
                deviceType: String(device.type),
                deviceIdentifier: deviceIdentifier(),
                deviceName: `VaultX-webkluis (${device.name})`,
            };
            const remembered = rememberedToken(email);
            if (twoFactor?.securityKey) {
                Object.assign(form, {
                    twoFactorToken: JSON.stringify(twoFactor.securityKey),
                    twoFactorProvider: "7",
                    twoFactorRemember: twoFactor.remember ? "1" : "0",
                });
            }
            else if (twoFactor?.code) {
                const code = twoFactor.code.replace(/\s+/g, "");
                Object.assign(form, {
                    twoFactorToken: code,
                    // Zes cijfers: authenticator-app; iets langers: de herstelcode.
                    twoFactorProvider: /^\d{6,8}$/.test(code) ? "0" : "8",
                    twoFactorRemember: twoFactor.remember ? "1" : "0",
                });
            }
            else if (remembered) {
                Object.assign(form, { twoFactorToken: remembered, twoFactorProvider: "5", twoFactorRemember: "0" });
            }
            let data;
            try {
                data = await tokenRequest(form);
            }
            catch (err) {
                if (err instanceof TwoFactorRequired && remembered && !twoFactor)
                    setRememberedToken(email, null);
                throw err;
            }
            if (typeof data.TwoFactorToken === "string")
                setRememberedToken(email, data.TwoFactorToken);
            const encUserKey = String(data.Key ?? data.key ?? "");
            const key = await openUserKey(masterKey, encUserKey);
            lastActivity.current = Date.now();
            setLockReason(null);
            setSession({
                key,
                email,
                params,
                encUserKey,
                access: String(data.access_token),
                refresh: typeof data.refresh_token === "string" ? data.refresh_token : null,
            });
        }
        finally {
            masterKey.fill(0);
        }
    }, []);
    const verifyMasterPassword = useCallback(async (masterPassword) => {
        const s = sessionRef.current;
        if (!s)
            return false;
        const { masterKey } = await deriveLogin(s.params, masterPassword);
        try {
            await openUserKey(masterKey, s.encUserKey);
            return true;
        }
        catch {
            return false;
        }
        finally {
            masterKey.fill(0);
        }
    }, []);
    const refreshAccess = useCallback(async () => {
        const s = sessionRef.current;
        if (!s?.refresh)
            return null;
        try {
            const data = await tokenRequest({ grant_type: "refresh_token", refresh_token: s.refresh, client_id: "web" });
            const next = { ...s, access: String(data.access_token) };
            sessionRef.current = next;
            setSession(next);
            return next.access;
        }
        catch {
            return null;
        }
    }, []);
    const fetchRaw = useCallback(async (path, init = {}) => {
        const s = sessionRef.current;
        if (!s)
            throw new VaultApiError(401, "De kluis is vergrendeld");
        const send = (token) => fetch(path, {
            ...init,
            credentials: "omit",
            headers: { ...init.headers, Authorization: `Bearer ${token}` },
        });
        let res = await send(s.access);
        if (res.status === 401) {
            const token = await refreshAccess();
            if (!token) {
                lock("Je sessie in de kluis is verlopen of dit apparaat werd afgemeld. Ontgrendel opnieuw.");
                throw new VaultApiError(401, "De kluis is vergrendeld");
            }
            res = await send(token);
        }
        return res;
    }, [lock, refreshAccess]);
    const request = useCallback(async (method, path, body) => {
        const headers = { Accept: "application/json" };
        if (body !== undefined)
            headers["Content-Type"] = "application/json";
        const res = await fetchRaw(path, {
            method,
            headers,
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
        const text = await res.text();
        const data = text ? JSON.parse(text) : undefined;
        if (!res.ok)
            throw new VaultApiError(res.status, errorMessage((data ?? {}), res.statusText));
        return data;
    }, [fetchRaw]);
    useEffect(() => {
        if (!session)
            return;
        const touch = () => (lastActivity.current = Date.now());
        const events = ["keydown", "pointerdown", "wheel"];
        events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
        const check = () => {
            if (Date.now() - lastActivity.current > AUTO_LOCK_MINUTES * 60_000)
                lock(`Na ${AUTO_LOCK_MINUTES} minuten zonder activiteit werd de kluis vergrendeld.`);
        };
        const onVisible = () => document.visibilityState === "visible" && check();
        document.addEventListener("visibilitychange", onVisible);
        const timer = window.setInterval(check, 15_000);
        return () => {
            events.forEach((e) => window.removeEventListener(e, touch));
            document.removeEventListener("visibilitychange", onVisible);
            window.clearInterval(timer);
        };
    }, [session, lock]);
    return (_jsx(WebVaultContext.Provider, { value: {
            key: session?.key ?? null,
            email: session?.email ?? null,
            unlock,
            lock,
            lockReason,
            verifyMasterPassword,
            request,
            fetchRaw,
        }, children: children }));
}
export function useWebVault() {
    const ctx = useContext(WebVaultContext);
    if (!ctx)
        throw new Error("WebVaultProvider ontbreekt");
    return ctx;
}
