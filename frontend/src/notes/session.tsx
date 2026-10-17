// Ontgrendelde kluis in de browser. De user key leeft enkel in het geheugen van dit tabblad,
// als niet-exporteerbare WebCrypto-sleutel; herladen, vergrendelen of 15 minuten niets doen
// gooit hem weg.

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { VaultStatus } from "../api/types";
import { deriveLogin, openUserKey, type UserKey } from "../vault/crypto";

export const AUTO_LOCK_MINUTES = 15;

interface VaultKeyState {
  key: UserKey | null;
  unlock: (status: VaultStatus, masterPassword: string) => Promise<void>;
  lock: () => void;
}

const VaultKeyContext = createContext<VaultKeyState | null>(null);

export function VaultKeyProvider({ children }: { children: ReactNode }) {
  const [key, setKey] = useState<UserKey | null>(null);
  const lastActivity = useRef(Date.now());

  const lock = useCallback(() => setKey(null), []);

  const unlock = useCallback(async (status: VaultStatus, masterPassword: string) => {
    if (!status.kdf_salt || status.kdf === null || !status.kdf_iterations) throw new Error("Je kluis is nog niet actief");
    const { masterKey, loginHash } = await deriveLogin(
      { salt: status.kdf_salt, kdf: status.kdf, iterations: status.kdf_iterations },
      masterPassword,
    );
    try {
      const res = await api.post<{ user_key: string }>("/api/v1/me/vault/unlock", { master_password_hash: loginHash });
      lastActivity.current = Date.now();
      setKey(await openUserKey(masterKey, res.user_key));
    } finally {
      masterKey.fill(0);
    }
  }, []);

  useEffect(() => {
    if (!key) return;
    const touch = () => (lastActivity.current = Date.now());
    const events = ["keydown", "pointerdown", "wheel"] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const check = () => {
      if (Date.now() - lastActivity.current > AUTO_LOCK_MINUTES * 60_000) setKey(null);
    };
    // Browsers vertragen timers in achtergrondtabs: bij terugkeren naar de tab meteen controleren.
    const onVisible = () => document.visibilityState === "visible" && check();
    document.addEventListener("visibilitychange", onVisible);
    const timer = window.setInterval(check, 15_000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      document.removeEventListener("visibilitychange", onVisible);
      window.clearInterval(timer);
    };
  }, [key]);

  return <VaultKeyContext.Provider value={{ key, unlock, lock }}>{children}</VaultKeyContext.Provider>;
}

export function useVaultKey(): VaultKeyState {
  const ctx = useContext(VaultKeyContext);
  if (!ctx) throw new Error("VaultKeyProvider ontbreekt");
  return ctx;
}
