import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import qrcode from "qrcode-generator";
import { useEffect, useState, type FormEvent } from "react";
import { NavLink } from "react-router";
import { api } from "../api/client";
import type { VaultStatus } from "../api/types";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { formatDate, relative } from "../format";
import { WebVault } from "./VaultItems";
import {
  ARGON2_DEFAULTS,
  changeMasterPassword,
  createVaultKeys,
  deriveLogin,
  PBKDF2_ITERATIONS,
} from "../vault/crypto";

const BASE = "/api/v1/me/vault";

export function VaultPage({ tab = "items" }: { tab?: "items" | "settings" }) {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["vault"], queryFn: () => api.get<VaultStatus>(BASE) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["vault"] });
  // Tellers en apparaten opnieuw ophalen bij het wisselen van tabblad (de webkluis wijzigt ze).
  useEffect(() => void qc.invalidateQueries({ queryKey: ["vault"] }), [tab, qc]);

  if (status.isLoading) return <Loading />;
  if (!status.data) return <ErrorBox error={status.error} />;
  const s = status.data;
  return (
    <>
      <PageHeader
        title="Mijn kluis"
        subtitle="Persoonlijke wachtwoordkluis: hier in de browser, en met de officiële Bitwarden®-apps, -extensies en -CLI."
      />
      {!s.enrolled ? (
        <Enroll s={s} onDone={refresh} />
      ) : (
        <>
          <nav className="page-tabs">
            <NavLink to="/vault" end>
              Items
            </NavLink>
            <NavLink to="/vault/settings">Apps, apparaten en beveiliging</NavLink>
          </nav>
          {tab === "items" ? <WebVault status={s} /> : <Enrolled s={s} onChange={refresh} />}
        </>
      )}
    </>
  );
}

function Enroll({ s, onDone }: { s: VaultStatus; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [understood, setUnderstood] = useState(false);
  const email = s.email ?? "";
  const mismatch = confirmPw.length > 0 && confirmPw !== password;

  const enroll = useMutation({
    mutationFn: async () => api.post<VaultStatus>(BASE, await createVaultKeys(email, password)),
    onSuccess: () => {
      setPassword("");
      setConfirmPw("");
      onDone();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || !understood) return;
    enroll.mutate();
  };

  if (s.blocked_reason)
    return (
      <Card title="Kluis activeren">
        <div className="error-box">{s.blocked_reason}</div>
      </Card>
    );

  return (
    <Card title="Kluis activeren">
      <form onSubmit={submit} className="form" style={{ maxWidth: 520 }}>
        <div className="info-box small">
          Kies een master password. Je browser leidt daar je sleutels uit af; VaultX krijgt het wachtwoord
          nooit te zien en kan het dus ook niet herstellen. Ben je het kwijt, dan kan je de kluis alleen
          leegmaken en opnieuw beginnen.
        </div>
        <label className="field">
          <span>E-mailadres (je login in de Bitwarden-apps)</span>
          <input value={email} disabled />
        </label>
        <label className="field">
          <span>Master password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Herhaal master password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
          />
          {mismatch && <span className="small" style={{ color: "var(--bad-fg)" }}>Komt niet overeen</span>}
        </label>
        <label className="check">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
          Ik begrijp dat niemand mijn master password kan herstellen.
        </label>
        <ErrorBox error={enroll.error} />
        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={enroll.isPending || !password || mismatch || !confirmPw || !understood}
          >
            {enroll.isPending ? "Sleutels afleiden…" : "Kluis activeren"}
          </button>
        </div>
      </form>
    </Card>
  );
}

function Enrolled({ s, onChange }: { s: VaultStatus; onChange: () => void }) {
  const [showRevoked, setShowRevoked] = useState(false);
  const revoke = useMutation({
    mutationFn: (id: string) => api.post(`${BASE}/devices/${id}/revoke`),
    onSuccess: onChange,
  });
  const reset = useMutation({ mutationFn: () => api.delete(BASE), onSuccess: onChange });
  const devices = showRevoked ? s.devices : s.devices.filter((d) => !d.revoked_at);
  const revokedCount = s.devices.length - s.devices.filter((d) => !d.revoked_at).length;
  const kdf =
    s.kdf === 1
      ? `Argon2id, ${s.kdf_iterations} iteraties, ${s.kdf_memory} MiB, parallelisme ${s.kdf_parallelism}`
      : `PBKDF2-SHA256, ${s.kdf_iterations?.toLocaleString("nl-BE")} iteraties`;

  return (
    <>
      <div className="stats">
        <Stat label="Items" value={s.item_count} hint={s.trash_count ? `${s.trash_count} in prullenbak` : undefined} />
        <Stat label="Mappen" value={s.folder_count} />
        <Stat label="Apparaten" value={s.devices.filter((d) => !d.revoked_at).length} />
        <Stat label="Laatste wijziging" value={relative(s.revision_date)} />
      </div>
      <Card title="Verbinden met een Bitwarden-app">
        {s.server_url.startsWith("http://") && (
          <div className="error-box small">
            VaultX draait hier zonder HTTPS. De Bitwarden-apps weigeren dat; zet Nginx Proxy Manager met een
            certificaat voor VaultX en stel VAULTX_PUBLIC_URL in op het https-adres.
          </div>
        )}
        <ol className="steps">
          <li>
            Kies in de app of extensie bij het inloggen <b>Self-hosted</b> (of "Ingelogd op: zelf gehost") en vul als
            server-URL in: <code className="mono">{s.server_url}</code>
          </li>
          <li>
            Log in met <b>{s.email}</b> en je master password.
          </li>
          <li>
            CLI: <code className="mono">bw config server {s.server_url}</code> en daarna{" "}
            <code className="mono">bw login {s.email}</code>
          </li>
        </ol>
        <p className="muted small">
          Inloggen werkt alleen zolang je VaultX-account actief is. Wie in VaultX gedeactiveerd wordt, verliest ook de
          toegang tot de kluis op al zijn apparaten.
        </p>
      </Card>
      <Card
        title="Apparaten"
        actions={
          revokedCount > 0 && (
            <label className="check">
              <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} />
              Ook {revokedCount} afgemeld{revokedCount > 1 ? "e" : ""} tonen
            </label>
          )
        }
      >
        <ErrorBox error={revoke.error} />
        {!devices.length ? (
          <Empty>Nog geen app ingelogd.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Apparaat</th>
                  <th>Type</th>
                  <th>Eerste login</th>
                  <th>Laatst gezien</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.id} className={d.revoked_at ? "dim" : undefined}>
                    <td>{d.name}</td>
                    <td>{d.type_name}</td>
                    <td>{formatDate(d.created_at)}</td>
                    <td>{relative(d.last_seen_at)}</td>
                    <td className="right">
                      {d.revoked_at ? (
                        <Badge>afgemeld</Badge>
                      ) : (
                        <button
                          className="btn btn-ghost btn-danger-text"
                          onClick={() => revoke.mutate(d.id)}
                          disabled={revoke.isPending}
                        >
                          Afmelden
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card title="Details">
        <dl className="kv">
          <dt>Login</dt>
          <dd>{s.email}</dd>
          <dt>Sleutelafleiding</dt>
          <dd>{kdf}</dd>
          <dt>Geactiveerd</dt>
          <dd>{formatDate(s.enrolled_at)}</dd>
        </dl>
      </Card>
      <TwoFactor s={s} onChange={onChange} />
      <ChangePassword s={s} onDone={onChange} />
      <Card title="Kluis leegmaken">
        <p className="muted small">
          Master password vergeten? Niemand kan het herstellen. Je kan de kluis wel verwijderen en opnieuw activeren;
          al je items gaan dan definitief verloren en alle apparaten worden afgemeld.
        </p>
        <ErrorBox error={reset.error} />
        <button
          className="btn btn-danger"
          disabled={reset.isPending}
          onClick={() => {
            const answer = prompt(`Typ VERWIJDER om je kluis met ${s.item_count + s.trash_count} items te wissen.`);
            if (answer === "VERWIJDER") reset.mutate();
          }}
        >
          Kluis verwijderen
        </button>
      </Card>
    </>
  );
}

function unlockParams(s: VaultStatus) {
  if (!s.kdf_salt || s.kdf === null || !s.kdf_iterations) throw new Error("Je kluis is nog niet actief");
  return {
    salt: s.kdf_salt,
    kdf: s.kdf,
    iterations: s.kdf_iterations,
    memory: s.kdf_memory,
    parallelism: s.kdf_parallelism,
  };
}

function qrDataUrl(text: string): string {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  return qr.createDataURL(5, 2);
}

function TwoFactor({ s, onChange }: { s: VaultStatus; onChange: () => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [recovery, setRecovery] = useState<string | null>(null);

  const start = useMutation({
    mutationFn: async () => {
      const { loginHash } = await deriveLogin(unlockParams(s), password);
      return api.post<{ secret: string; otpauth_uri: string }>(`${BASE}/2fa/setup`, { master_password_hash: loginHash });
    },
    onSuccess: (data) => {
      setPassword("");
      setSetup(data);
    },
  });
  const enable = useMutation({
    mutationFn: () => api.post<{ recovery_code: string }>(`${BASE}/2fa/enable`, { code }),
    onSuccess: (data) => {
      setCode("");
      setSetup(null);
      setRecovery(data.recovery_code);
      onChange();
    },
  });
  const disable = useMutation({
    mutationFn: async () => {
      const { loginHash } = await deriveLogin(unlockParams(s), password);
      return api.post(`${BASE}/2fa/disable`, { master_password_hash: loginHash, code });
    },
    onSuccess: () => {
      setPassword("");
      setCode("");
      setRecovery(null);
      onChange();
    },
  });

  const passwordField = (
    <label className="field">
      <span>Master password</span>
      <input
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
    </label>
  );

  return (
    <Card
      title="Tweestapsverificatie"
      actions={s.two_factor_enabled ? <Badge tone="good">aan</Badge> : <Badge>uit</Badge>}
    >
      {recovery && (
        <div className="info-box">
          <p>
            <b>Bewaar deze herstelcode</b> (op papier of buiten deze kluis). Ben je je telefoon kwijt, dan log je
            daarmee toch in; tweestapsverificatie gaat dan uit. Je ziet hem maar één keer.
          </p>
          <code className="mono recovery-code">{recovery}</code>
        </div>
      )}
      {s.two_factor_enabled ? (
        <form
          className="form"
          style={{ maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            disable.mutate();
          }}
        >
          <p className="muted small">
            Aan sinds {formatDate(s.two_factor_enabled_at)}. De Bitwarden-apps vragen na je master password een code
            uit je authenticator-app; met "Onthoud mij" slaat een apparaat dat 30 dagen over.
          </p>
          {passwordField}
          <label className="field">
            <span>Code uit de app of herstelcode</span>
            <input autoComplete="one-time-code" required value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <ErrorBox error={disable.error} />
          <div className="form-actions">
            <button className="btn btn-danger" disabled={disable.isPending || !password || !code}>
              {disable.isPending ? "Controleren…" : "Tweestapsverificatie uitzetten"}
            </button>
          </div>
        </form>
      ) : setup ? (
        <form
          className="form"
          style={{ maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            enable.mutate();
          }}
        >
          <p className="muted small">
            Scan deze code met je authenticator-app (Aegis, 2FAS, Google Authenticator, Bitwarden Authenticator, ...)
            en vul de code in die de app toont.
          </p>
          <img className="qr" src={qrDataUrl(setup.otpauth_uri)} alt="QR-code voor je authenticator-app" />
          <p className="small">
            Kan je niet scannen? Sleutel: <code className="mono">{setup.secret}</code>
          </p>
          <label className="field">
            <span>Code uit de app</span>
            <input
              autoComplete="one-time-code"
              inputMode="numeric"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <ErrorBox error={enable.error} />
          <div className="form-actions">
            <button className="btn btn-primary" disabled={enable.isPending || code.length < 6}>
              Aanzetten
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setSetup(null)}>
              Annuleren
            </button>
          </div>
        </form>
      ) : (
        <form
          className="form"
          style={{ maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            start.mutate();
          }}
        >
          <p className="muted small">
            Wie je master password kent, kan dan nog niet inloggen in de Bitwarden-apps zonder je telefoon. Geldt voor
            de apps, extensies en CLI; in VaultX zelf beschermt Authentik je login.
          </p>
          {passwordField}
          <ErrorBox error={start.error} />
          <div className="form-actions">
            <button className="btn btn-primary" disabled={start.isPending || !password}>
              {start.isPending ? "Controleren…" : "Instellen met een authenticator-app"}
            </button>
          </div>
        </form>
      )}
    </Card>
  );
}

function ChangePassword({ s, onDone }: { s: VaultStatus; onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [kdf, setKdf] = useState<number>(s.kdf ?? 0);
  const [done, setDone] = useState(false);
  const mismatch = confirmPw.length > 0 && confirmPw !== password;

  const change = useMutation({
    mutationFn: async () => {
      const params = unlockParams(s);
      const old = await deriveLogin(params, current);
      const { user_key } = await api.post<{ user_key: string }>(`${BASE}/unlock`, {
        master_password_hash: old.loginHash,
      });
      // Zelfde KDF als nu blijft ongewijzigd; anders de standaard van Bitwarden.
      const next =
        kdf === s.kdf
          ? { kdf, iterations: params.iterations, memory: params.memory, parallelism: params.parallelism }
          : kdf === 1
            ? { kdf: 1, ...ARGON2_DEFAULTS }
            : { kdf: 0, iterations: PBKDF2_ITERATIONS };
      const payload = await changeMasterPassword(old, user_key, params.salt, password, next);
      return api.post<VaultStatus>(`${BASE}/password`, payload);
    },
    onSuccess: () => {
      setCurrent("");
      setPassword("");
      setConfirmPw("");
      setDone(true);
      onDone();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || !current) return;
    setDone(false);
    change.mutate();
  };

  return (
    <Card title="Master password wijzigen">
      <form onSubmit={submit} className="form" style={{ maxWidth: 520 }}>
        <p className="muted small">
          Je items blijven gewoon leesbaar: je browser pakt je sleutel opnieuw in met het nieuwe wachtwoord. Daarna
          worden al je apparaten afgemeld en log je in de Bitwarden-apps opnieuw in met het nieuwe wachtwoord.
        </p>
        {done && <div className="info-box small">Master password gewijzigd. Log opnieuw in op je apparaten.</div>}
        <label className="field">
          <span>Huidig master password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Nieuw master password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Herhaal nieuw master password</span>
          <input
            type="password"
            autoComplete="new-password"
            required
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
          />
          {mismatch && <span className="small" style={{ color: "var(--bad-fg)" }}>Komt niet overeen</span>}
        </label>
        <label className="field">
          <span>Sleutelafleiding</span>
          <select value={kdf} onChange={(e) => setKdf(Number(e.target.value))}>
            <option value={0}>PBKDF2-SHA256 ({PBKDF2_ITERATIONS.toLocaleString("nl-BE")} iteraties)</option>
            <option value={1}>
              Argon2id ({ARGON2_DEFAULTS.iterations} iteraties, {ARGON2_DEFAULTS.memory} MiB,{" "}
              {ARGON2_DEFAULTS.parallelism} threads), sterker tegen kraken
            </option>
          </select>
        </label>
        <ErrorBox error={change.error} />
        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={change.isPending || !current || !password || mismatch || !confirmPw}
          >
            {change.isPending ? "Sleutels afleiden…" : "Wijzigen en alle apparaten afmelden"}
          </button>
        </div>
      </form>
    </Card>
  );
}
