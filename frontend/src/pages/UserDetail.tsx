import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import type { AuditPage, Session, UserDetail } from "../api/types";
import { PasswordForm } from "../components/accounts";
import { AuditTable } from "../components/AuditTable";
import { LoginSecurityKeys } from "../components/loginKeys";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader, SourceBadge } from "../components/ui";
import { formatDate, relative, roleLabels, userLabel } from "../format";

export function UserDetailPage() {
  const { userId = "" } = useParams();
  const qc = useQueryClient();
  const { data: me } = useMe();
  const isSelf = me?.id === userId;
  const user = useQuery({ queryKey: ["user", userId], queryFn: () => api.get<UserDetail>(`/api/v1/users/${userId}`) });
  const sessions = useQuery({
    queryKey: ["user", userId, "sessions"],
    queryFn: () => api.get<Session[]>(`/api/v1/users/${userId}/sessions`),
  });
  const audit = useQuery({
    queryKey: ["audit", "actor", userId],
    queryFn: () => api.get<AuditPage>("/api/v1/audit", { actor_user_id: userId, limit: 20 }),
    enabled: !!me?.is_admin,
  });
  const toggle = useMutation({
    mutationFn: (is_active: boolean) => api.patch(`/api/v1/users/${userId}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["user", userId] }),
  });
  const [pwOpen, setPwOpen] = useState<null | "admin" | "own">(null);
  const [pwDone, setPwDone] = useState<string | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["user", userId] });
    void qc.invalidateQueries({ queryKey: ["me"] });
  };
  const setAdmin = useMutation({
    mutationFn: (is_admin: boolean) => api.patch(`/api/v1/users/${userId}`, { is_admin }),
    onSuccess: refresh,
  });
  const setPassword = useMutation({
    mutationFn: (password: string) => api.put(`/api/v1/users/${userId}/password`, { password }),
    onSuccess: () => {
      refresh();
      setPwOpen(null);
      setPwDone(
        isSelf
          ? "Je wachtwoord is ingesteld. Je kan nu ook zonder Authentik inloggen."
          : "Het wachtwoord is ingesteld. Bezorg het veilig aan de gebruiker.",
      );
    },
  });
  const removePassword = useMutation({
    mutationFn: () => api.delete(`/api/v1/users/${userId}/password`),
    onSuccess: refresh,
  });
  const changeOwn = useMutation({
    mutationFn: (v: { current_password: string; new_password: string }) => api.post("/api/v1/me/password", v),
    onSuccess: () => {
      refresh();
      setPwOpen(null);
      setPwDone("Je wachtwoord is gewijzigd. Andere sessies die met je wachtwoord inlogden, zijn afgemeld.");
    },
  });
  const revoke = useMutation({
    mutationFn: () => api.post(`/api/v1/users/${userId}/sessions/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["user", userId, "sessions"] }),
  });

  if (user.isLoading) return <Loading />;
  if (!user.data) return <ErrorBox error={user.error} />;
  const u = user.data;
  const locked = !!u.locked_until && new Date(u.locked_until) > new Date();
  const adminViaGroup = u.is_admin && !u.admin_granted;
  return (
    <>
      <PageHeader
        title={userLabel(u)}
        subtitle={
          <>
            {u.email} {u.is_admin && <Badge tone="info">instantiebeheerder</Badge>}{" "}
            {u.is_active ? <Badge tone="good">actief</Badge> : <Badge tone="bad">gedeactiveerd</Badge>}
          </>
        }
        actions={
          me?.is_admin &&
          !isSelf && (
            <button
              className={u.is_active ? "btn btn-danger" : "btn"}
              onClick={() => toggle.mutate(!u.is_active)}
              disabled={toggle.isPending}
            >
              {u.is_active ? "Deactiveren" : "Activeren"}
            </button>
          )
        }
      />
      <ErrorBox error={toggle.error ?? revoke.error ?? setAdmin.error ?? removePassword.error} />
      {pwDone && <div className="info-box">{pwDone}</div>}
      <div className="grid-2">
        <Card title="Identiteit">
          <dl className="kv">
            <dt>Gebruikersnaam</dt>
            <dd>{u.username}</dd>
            <dt>Inloggen</dt>
            <dd>
              {u.has_authentik && <Badge tone="info">Authentik</Badge>}{" "}
              {u.has_password && <Badge>VaultX-wachtwoord</Badge>}
              {locked && (
                <Badge tone="bad" title="Te veel foute wachtwoorden">
                  vergrendeld tot {formatDate(u.locked_until)}
                </Badge>
              )}
            </dd>
            {u.has_authentik && (
              <>
                <dt>Issuer</dt>
                <dd className="mono small">{u.oidc_issuer}</dd>
                <dt>Subject</dt>
                <dd className="mono small">{u.oidc_subject}</dd>
                <dt>Authentik-groepen</dt>
                <dd>{u.idp_groups?.length ? u.idp_groups.map((g) => <Badge key={g}>{g}</Badge>) : "—"}</dd>
              </>
            )}
            {u.has_password && (
              <>
                <dt>Wachtwoord gewijzigd</dt>
                <dd>{formatDate(u.password_changed_at)}</dd>
              </>
            )}
            <dt>Laatste login</dt>
            <dd>{formatDate(u.last_login_at)}</dd>
            <dt>Aangemaakt</dt>
            <dd>{formatDate(u.created_at)}</dd>
          </dl>
        </Card>
        <Card title="Lidmaatschappen">
          {u.memberships.length === 0 ? (
            <Empty>Geen lidmaatschappen.</Empty>
          ) : (
            <ul className="plain-list">
              {u.memberships.map((m) => (
                <li key={m.id}>
                  <Link to={`/organizations/${m.organization_id}`}>{m.organization_name}</Link>
                  {m.team_name && (
                    <>
                      {" / "}
                      <Link to={`/organizations/${m.organization_id}/teams/${m.team_id}`}>{m.team_name}</Link>
                    </>
                  )}
                  <span className="muted"> · {roleLabels[m.role] ?? m.role} </span>
                  <SourceBadge source={m.source} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {(me?.is_admin || (isSelf && u.has_password)) && (
        <Card title="Toegang">
          <dl className="kv">
            <dt>Wachtwoordlogin</dt>
            <dd>
              {u.has_password ? "Kan inloggen met gebruikersnaam of e-mail en wachtwoord." : "Geen VaultX-wachtwoord."}
              <div className="actions-inline">
                {isSelf && u.has_password && (
                  <button className="btn" onClick={() => setPwOpen("own")}>
                    Mijn wachtwoord wijzigen
                  </button>
                )}
                {me?.is_admin && (
                  <button className="btn" onClick={() => setPwOpen("admin")}>
                    {u.has_password ? "Wachtwoord resetten" : "Wachtwoord instellen"}
                  </button>
                )}
                {me?.is_admin && u.has_password && u.has_authentik && (
                  <button
                    className="btn btn-danger"
                    disabled={removePassword.isPending}
                    onClick={() => {
                      if (confirm("Wachtwoordlogin weghalen? Inloggen kan dan enkel nog via Authentik.")) {
                        removePassword.mutate();
                      }
                    }}
                  >
                    Wachtwoordlogin weghalen
                  </button>
                )}
              </div>
            </dd>
            {me?.is_admin && (
              <>
                <dt>Beheerdersrechten</dt>
                <dd>
                  {u.admin_granted
                    ? "Instantiebeheerder, toegekend in VaultX."
                    : adminViaGroup
                      ? "Instantiebeheerder via een Authentik-groep."
                      : "Geen instantiebeheerder."}
                  {!(isSelf && u.admin_granted && !adminViaGroup) && (
                    <div className="actions-inline">
                      <button
                        className={u.admin_granted ? "btn btn-danger" : "btn"}
                        disabled={setAdmin.isPending}
                        onClick={() => setAdmin.mutate(!u.admin_granted)}
                      >
                        {u.admin_granted ? "Rechten intrekken in VaultX" : "Beheerder maken in VaultX"}
                      </button>
                    </div>
                  )}
                </dd>
              </>
            )}
          </dl>
        </Card>
      )}
      {isSelf && u.has_password && <LoginSecurityKeys />}
      <Modal
        title={pwOpen === "own" ? "Mijn wachtwoord wijzigen" : u.has_password ? "Wachtwoord resetten" : "Wachtwoord instellen"}
        open={pwOpen !== null}
        onClose={() => setPwOpen(null)}
      >
        {pwOpen === "admin" && !u.has_password && u.has_authentik && (
          <p className="muted small">
            Daarna kan {userLabel(u)} ook zonder Authentik inloggen, met gebruikersnaam {u.username ?? u.email} en dit
            wachtwoord. Het blijft hetzelfde account, met dezelfde kluis en notities.
          </p>
        )}
        <PasswordForm
          own={pwOpen === "own"}
          submitLabel={pwOpen === "own" ? "Wijzigen" : "Opslaan"}
          error={pwOpen === "own" ? changeOwn.error : setPassword.error}
          pending={pwOpen === "own" ? changeOwn.isPending : setPassword.isPending}
          onSubmit={(v) =>
            pwOpen === "own"
              ? changeOwn.mutate({ current_password: v.current_password, new_password: v.password })
              : setPassword.mutate(v.password)
          }
        />
      </Modal>
      <Card
        title="Actieve sessies"
        actions={
          (sessions.data?.length ?? 0) > 0 && (
            <button className="btn" onClick={() => revoke.mutate()} disabled={revoke.isPending}>
              Alle sessies intrekken
            </button>
          )
        }
      >
        {sessions.data?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Gestart</th>
                  <th>Via</th>
                  <th>Laatst actief</th>
                  <th>Verloopt</th>
                  <th>IP</th>
                  <th>Browser</th>
                </tr>
              </thead>
              <tbody>
                {sessions.data.map((s) => (
                  <tr key={s.id}>
                    <td>
                      {formatDate(s.created_at)} {s.current && <Badge tone="info">deze sessie</Badge>}
                    </td>
                    <td>{s.auth_method === "password" ? "wachtwoord" : "Authentik"}</td>
                    <td>{relative(s.last_seen_at)}</td>
                    <td>{formatDate(s.expires_at)}</td>
                    <td className="mono small">{s.ip_address}</td>
                    <td className="small truncate">{s.user_agent}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>Geen actieve sessies.</Empty>
        )}
      </Card>
      {me?.is_admin && (
        <Card title="Recente activiteit">
          {audit.data?.items.length ? <AuditTable items={audit.data.items} /> : <Empty>Geen activiteit.</Empty>}
        </Card>
      )}
    </>
  );
}
