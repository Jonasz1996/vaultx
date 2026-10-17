import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { VaultStatus } from "../api/types";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { formatDate, relative } from "../format";
import { createVaultKeys, passwordProblems } from "../vault/crypto";

const BASE = "/api/v1/me/vault";

export function VaultPage() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["vault"], queryFn: () => api.get<VaultStatus>(BASE) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["vault"] });

  if (status.isLoading) return <Loading />;
  if (!status.data) return <ErrorBox error={status.error} />;
  const s = status.data;
  return (
    <>
      <PageHeader
        title="Mijn kluis"
        subtitle="Persoonlijke wachtwoordkluis die werkt met de officiële Bitwarden®-apps, -extensies en -CLI."
      />
      {s.enrolled ? <Enrolled s={s} onChange={refresh} /> : <Enroll s={s} onDone={refresh} />}
    </>
  );
}

function Enroll({ s, onDone }: { s: VaultStatus; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [understood, setUnderstood] = useState(false);
  const email = s.email ?? "";
  const problems = password ? passwordProblems(password, email) : [];
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
    if (problems.length || mismatch || !understood) return;
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
          {problems.length > 0 && <span className="muted small">Nog nodig: {problems.join(", ")}</span>}
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
            disabled={enroll.isPending || !password || problems.length > 0 || mismatch || !confirmPw || !understood}
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
