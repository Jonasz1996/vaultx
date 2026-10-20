// Beveiligingssleutels en passkeys als tweede stap na het VaultX-wachtwoord (eigen account).
// Inloggen via Authentik vraagt deze stap niet: daar stel je je sleutel in Authentik zelf in.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { LoginKeyStatus, WebAuthnOptions } from "../api/types";
import { formatDate, relative } from "../format";
import { createCredential, getAssertion, webAuthnSupported } from "../webauthn";
import { Badge, Card, ErrorBox } from "./ui";

const BASE = "/api/v1/me/security-keys";

export function LoginSecurityKeys() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me", "security-keys"], queryFn: () => api.get<LoginKeyStatus>(BASE) });
  const [mode, setMode] = useState<{ kind: "add" } | { kind: "remove"; id: string } | null>(null);
  const [name, setName] = useState("YubiKey");
  const [password, setPassword] = useState("");
  const [recoveryInput, setRecoveryInput] = useState("");
  const [recovery, setRecovery] = useState<string | null>(null);
  const supported = webAuthnSupported();

  const done = () => {
    setPassword("");
    setRecoveryInput("");
    setMode(null);
    void qc.invalidateQueries({ queryKey: ["me", "security-keys"] });
  };
  const add = useMutation({
    mutationFn: async () => {
      const { options } = await api.post<{ options: WebAuthnOptions }>(`${BASE}/options`, { password });
      const credential = await createCredential(options);
      return api.post<{ recovery_code: string | null }>(BASE, { name, credential });
    },
    onSuccess: (data) => {
      if (data.recovery_code) setRecovery(data.recovery_code);
      done();
    },
  });
  const remove = useMutation({
    mutationFn: async (id: string) => {
      let proof: Record<string, unknown> = { recovery_code: recoveryInput.trim() };
      if (!recoveryInput.trim()) {
        const { options } = await api.post<{ options: WebAuthnOptions | null }>(`${BASE}/challenge`);
        if (!options || !supported)
          throw new Error("Je sleutel werkt hier niet. Vul je herstelcode in om de sleutel weg te halen.");
        proof = { security_key: await getAssertion(options) };
      }
      return api.post(`${BASE}/${id}/remove`, { password, ...proof });
    },
    onSuccess: done,
  });

  const s = q.data;
  if (!s?.has_password) return null;
  return (
    <Card
      title="Beveiligingssleutels voor het wachtwoordlogin"
      actions={s.keys.length ? <Badge tone="good">aan</Badge> : <Badge>uit</Badge>}
    >
      {recovery && (
        <div className="info-box">
          <p>
            <b>Bewaar deze herstelcode.</b> Ben je je sleutel kwijt, dan log je daarmee toch in met je wachtwoord; de
            sleutels voor je login gaan dan uit. Je ziet hem maar één keer.
          </p>
          <code className="mono recovery-code">{recovery}</code>
        </div>
      )}
      <p className="muted small">
        Na je VaultX-wachtwoord vraagt de loginpagina dan ook je YubiKey, een andere FIDO2-sleutel of een passkey.
        Werkt enkel op <b>{s.rp_id ?? "een domeinnaam"}</b>; op een IP-adres log je in met je herstelcode. Inloggen
        via Authentik gebruikt de MFA van Authentik zelf. Sleutels voor je kluis stel je in bij Mijn kluis.
      </p>
      {!s.available && (
        <div className="error-box small">
          VaultX heeft geen domeinnaam (VAULTX_PUBLIC_URL is een IP-adres), dus sleutels kunnen hier niet.
        </div>
      )}
      {s.keys.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Gekoppeld</th>
                <th>Laatst gebruikt</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {s.keys.map((k) => (
                <tr key={k.id}>
                  <td>
                    {k.name} {k.backed_up && <Badge>passkey</Badge>}
                  </td>
                  <td>{formatDate(k.created_at)}</td>
                  <td>{k.last_used_at ? relative(k.last_used_at) : "nog niet"}</td>
                  <td className="right">
                    <button className="btn btn-ghost btn-danger-text" onClick={() => setMode({ kind: "remove", id: k.id })}>
                      Weghalen
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {mode ? (
        <form
          className="form"
          style={{ maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            if (mode.kind === "add") add.mutate();
            else remove.mutate(mode.id);
          }}
        >
          {mode.kind === "add" && (
            <label className="field">
              <span>Naam (om de sleutel later te herkennen)</span>
              <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
          <label className="field">
            <span>Je VaultX-wachtwoord</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {mode.kind === "remove" && (
            <label className="field">
              <span>Herstelcode (leeg: bevestigen met een gekoppelde sleutel)</span>
              <input autoComplete="one-time-code" value={recoveryInput} onChange={(e) => setRecoveryInput(e.target.value)} />
            </label>
          )}
          <ErrorBox error={mode.kind === "add" ? add.error : remove.error} />
          <div className="form-actions">
            <button
              className={mode.kind === "add" ? "btn btn-primary" : "btn btn-danger"}
              disabled={add.isPending || remove.isPending || !password}
            >
              {add.isPending
                ? "Wachten op je sleutel…"
                : remove.isPending
                  ? "Controleren…"
                  : mode.kind === "add"
                    ? "Koppelen"
                    : "Sleutel weghalen"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setMode(null)}>
              Annuleren
            </button>
          </div>
        </form>
      ) : (
        <div className="form-actions">
          <button
            className="btn"
            disabled={!s.available || !supported}
            title={supported ? undefined : "Deze browser ondersteunt geen beveiligingssleutels"}
            onClick={() => setMode({ kind: "add" })}
          >
            Sleutel of passkey toevoegen
          </button>
        </div>
      )}
    </Card>
  );
}
