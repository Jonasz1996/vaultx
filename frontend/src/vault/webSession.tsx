// Webkluis: de browser logt in op de Bitwarden-compatibele API van VaultX zoals de officiële
// webkluis van Bitwarden dat doet (/identity/connect/token met de afgeleide login-hash). De
// tokens en de user key leven enkel in het geheugen van dit tabblad; herladen, vergrendelen of
// 15 minuten niets doen gooit ze weg. Zo gelden de grens op mislukte pogingen, de
// tweestapsverificatie van de kluis, de apparatenlijst en de auditlog ook voor de webkluis.

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { VaultStatus } from "../api/types";
import { AUTO_LOCK_MINUTES } from "../notes/session";
import { deriveLogin, openUserKey, type UnlockParams, type UserKey } from "./crypto";

export class VaultApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

/** De server vraagt een tweede stap (tweestapsverificatie van de kluis). `securityKey` bevat de
 * opties voor een beveiligingssleutel als de kluis er een heeft (Bitwarden-provider 7). */
export class TwoFactorRequired extends Error {
  constructor(
    public authenticator: boolean,
    public securityKey: Record<string, unknown> | null,
  ) {
    super("Bevestig met je tweede stap.");
  }
}

export interface TwoFactorAnswer {
  /** Code uit de authenticator-app of de herstelcode. */
  code?: string;
  /** Antwoord van navigator.credentials.get. */
  securityKey?: Record<string, unknown>;
  remember: boolean;
}

interface Session {
  key: UserKey;
  email: string;
  params: UnlockParams;
  encUserKey: string;
  access: string;
  refresh: string | null;
}

interface WebVaultState {
  key: UserKey | null;
  email: string | null;
  unlock: (status: VaultStatus, masterPassword: string, twoFactor?: TwoFactorAnswer) => Promise<void>;
  lock: (reason?: string) => void;
  /** Waarom de kluis vanzelf vergrendeld werd (verlopen sessie, apparaat afgemeld …). */
  lockReason: string | null;
  /** Controleert het master password lokaal (voor items met "master password opnieuw vragen"). */
  verifyMasterPassword: (masterPassword: string) => Promise<boolean>;
  request: <T>(method: string, path: string, body?: unknown) => Promise<T>;
  /** Ruwe request met het access token (bijlagen). */
  fetchRaw: (path: string, init?: RequestInit) => Promise<Response>;
}

const WebVaultContext = createContext<WebVaultState | null>(null);

const DEVICE_ID_KEY = "vaultx.webVault.deviceId";
const REMEMBER_KEY = "vaultx.webVault.twoFactor.";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Vaste identificatie van deze browser, zodat hij één regel in de apparatenlijst krijgt. */
function deviceIdentifier(): string {
  const s = storage();
  try {
    const existing = s?.getItem(DEVICE_ID_KEY);
    if (existing) return existing;
    const id = crypto.randomUUID();
    s?.setItem(DEVICE_ID_KEY, id);
    return id;
  } catch {
    return crypto.randomUUID();
  }
}

/** Bitwarden DeviceType voor deze browser (VaultX toont er een leesbare naam bij). */
export function browserDevice(ua = navigator.userAgent): { type: number; name: string } {
  if (/Edg\//.test(ua)) return { type: 12, name: "Edge" };
  if (/OPR\//.test(ua)) return { type: 11, name: "Opera" };
  if (/Vivaldi\//.test(ua)) return { type: 18, name: "Vivaldi" };
  if (/Firefox\//.test(ua)) return { type: 10, name: "Firefox" };
  if (/Chrome\//.test(ua)) return { type: 9, name: "Chrome" };
  if (/Safari\//.test(ua)) return { type: 17, name: "Safari" };
  return { type: 14, name: "browser" };
}

function rememberedToken(email: string): string | null {
  try {
    return storage()?.getItem(REMEMBER_KEY + email) ?? null;
  } catch {
    return null;
  }
}

function setRememberedToken(email: string, token: string | null) {
  try {
    if (token) storage()?.setItem(REMEMBER_KEY + email, token);
    else storage()?.removeItem(REMEMBER_KEY + email);
  } catch {
    /* geen opslag: de code wordt dan elke keer gevraagd */
  }
}

function errorMessage(data: Record<string, unknown>, fallback: string): string {
  const model = (data.ErrorModel ?? data.errorModel) as { Message?: string } | undefined;
  if (typeof model?.Message === "string") return model.Message;
  if (typeof data.message === "string") return data.message;
  if (typeof data.error_description === "string") return data.error_description;
  return fallback;
}

async function tokenRequest(form: Record<string, string>): Promise<Record<string, unknown>> {
  const res = await fetch("/identity/connect/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams(form).toString(),
    credentials: "omit",
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (res.ok) return data;
  const providers = (data.TwoFactorProviders2 ?? data.twoFactorProviders2) as Record<string, unknown> | undefined;
  if (providers)
    throw new TwoFactorRequired("0" in providers, (providers["7"] as Record<string, unknown> | null) ?? null);
  throw new VaultApiError(res.status, errorMessage(data, res.statusText));
}

export function WebVaultProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [session, setSession] = useState<Session | null>(null);
  const [lockReason, setLockReason] = useState<string | null>(null);
  const sessionRef = useRef<Session | null>(null);
  const lastActivity = useRef(Date.now());
  sessionRef.current = session;

  const lock = useCallback((reason?: string) => {
    sessionRef.current = null;
    setSession(null);
    setLockReason(reason ?? null);
    // Ontsleutelde items niet in de cache laten staan.
    qc.removeQueries({ queryKey: ["webvault"] });
  }, [qc]);

  const unlock = useCallback(async (status: VaultStatus, masterPassword: string, twoFactor?: TwoFactorAnswer) => {
    if (!status.email || !status.kdf_salt || status.kdf === null || !status.kdf_iterations)
      throw new Error("Je kluis is nog niet actief");
    const email = status.email;
    const params: UnlockParams = {
      salt: status.kdf_salt,
      kdf: status.kdf,
      iterations: status.kdf_iterations,
      memory: status.kdf_memory,
      parallelism: status.kdf_parallelism,
    };
    const { masterKey, loginHash } = await deriveLogin(params, masterPassword);
    try {
      const device = browserDevice();
      const form: Record<string, string> = {
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
      } else if (twoFactor?.code) {
        const code = twoFactor.code.replace(/\s+/g, "");
        Object.assign(form, {
          twoFactorToken: code,
          // Zes cijfers: authenticator-app; iets langers: de herstelcode.
          twoFactorProvider: /^\d{6,8}$/.test(code) ? "0" : "8",
          twoFactorRemember: twoFactor.remember ? "1" : "0",
        });
      } else if (remembered) {
        Object.assign(form, { twoFactorToken: remembered, twoFactorProvider: "5", twoFactorRemember: "0" });
      }
      let data: Record<string, unknown>;
      try {
        data = await tokenRequest(form);
      } catch (err) {
        if (err instanceof TwoFactorRequired && remembered && !twoFactor) setRememberedToken(email, null);
        throw err;
      }
      if (typeof data.TwoFactorToken === "string") setRememberedToken(email, data.TwoFactorToken);
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
    } finally {
      masterKey.fill(0);
    }
  }, []);

  const verifyMasterPassword = useCallback(async (masterPassword: string) => {
    const s = sessionRef.current;
    if (!s) return false;
    const { masterKey } = await deriveLogin(s.params, masterPassword);
    try {
      await openUserKey(masterKey, s.encUserKey);
      return true;
    } catch {
      return false;
    } finally {
      masterKey.fill(0);
    }
  }, []);

  const refreshAccess = useCallback(async (): Promise<string | null> => {
    const s = sessionRef.current;
    if (!s?.refresh) return null;
    try {
      const data = await tokenRequest({ grant_type: "refresh_token", refresh_token: s.refresh, client_id: "web" });
      const next = { ...s, access: String(data.access_token) };
      sessionRef.current = next;
      setSession(next);
      return next.access;
    } catch {
      return null;
    }
  }, []);

  const fetchRaw = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const s = sessionRef.current;
      if (!s) throw new VaultApiError(401, "De kluis is vergrendeld");
      const send = (token: string) =>
        fetch(path, {
          ...init,
          credentials: "omit",
          headers: { ...(init.headers as Record<string, string> | undefined), Authorization: `Bearer ${token}` },
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
    },
    [lock, refreshAccess],
  );

  const request = useCallback(
    async <T,>(method: string, path: string, body?: unknown): Promise<T> => {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const res = await fetchRaw(path, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const text = await res.text();
      const data = text ? (JSON.parse(text) as unknown) : undefined;
      if (!res.ok)
        throw new VaultApiError(res.status, errorMessage((data ?? {}) as Record<string, unknown>, res.statusText));
      return data as T;
    },
    [fetchRaw],
  );

  useEffect(() => {
    if (!session) return;
    const touch = () => (lastActivity.current = Date.now());
    const events = ["keydown", "pointerdown", "wheel"] as const;
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

  return (
    <WebVaultContext.Provider
      value={{
        key: session?.key ?? null,
        email: session?.email ?? null,
        unlock,
        lock,
        lockReason,
        verifyMasterPassword,
        request,
        fetchRaw,
      }}
    >
      {children}
    </WebVaultContext.Provider>
  );
}

export function useWebVault(): WebVaultState {
  const ctx = useContext(WebVaultContext);
  if (!ctx) throw new Error("WebVaultProvider ontbreekt");
  return ctx;
}
