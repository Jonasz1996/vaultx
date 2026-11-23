import { jsx as _jsx } from "react/jsx-runtime";
// Ontgrendelde kluis in de browser. De user key leeft enkel in het geheugen van dit tabblad,
// als niet-exporteerbare WebCrypto-sleutel; herladen, vergrendelen of 15 minuten niets doen
// gooit hem weg.
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { deriveLogin, openUserKey } from "../vault/crypto";
export const AUTO_LOCK_MINUTES = 15;
const VaultKeyContext = createContext(null);
export function VaultKeyProvider({ children }) {
    const [key, setKey] = useState(null);
    const lastActivity = useRef(Date.now());
    const lock = useCallback(() => setKey(null), []);
    const unlock = useCallback(async (status, masterPassword) => {
        if (!status.kdf_salt || status.kdf === null || !status.kdf_iterations)
            throw new Error("Je kluis is nog niet actief");
        const { masterKey, loginHash } = await deriveLogin({
            salt: status.kdf_salt,
            kdf: status.kdf,
            iterations: status.kdf_iterations,
            memory: status.kdf_memory,
            parallelism: status.kdf_parallelism,
        }, masterPassword);
        try {
            const res = await api.post("/api/v1/me/vault/unlock", { master_password_hash: loginHash });
            lastActivity.current = Date.now();
            setKey(await openUserKey(masterKey, res.user_key));
        }
        finally {
            masterKey.fill(0);
        }
    }, []);
    useEffect(() => {
        if (!key)
            return;
        const touch = () => (lastActivity.current = Date.now());
        const events = ["keydown", "pointerdown", "wheel"];
        events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
        const check = () => {
            if (Date.now() - lastActivity.current > AUTO_LOCK_MINUTES * 60_000)
                setKey(null);
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
    return _jsx(VaultKeyContext.Provider, { value: { key, unlock, lock }, children: children });
}
export function useVaultKey() {
    const ctx = useContext(VaultKeyContext);
    if (!ctx)
        throw new Error("VaultKeyProvider ontbreekt");
    return ctx;
}
