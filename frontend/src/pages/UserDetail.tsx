import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import type { AuditPage, Session, UserDetail } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, SourceBadge } from "../components/ui";
import { formatDate, relative, roleLabels, userLabel } from "../format";

export function UserDetailPage() {
  const { userId = "" } = useParams();
  const qc = useQueryClient();
  const { data: me } = useMe();
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
  const revoke = useMutation({
    mutationFn: () => api.post(`/api/v1/users/${userId}/sessions/revoke`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["user", userId, "sessions"] }),
  });

  if (user.isLoading) return <Loading />;
  if (!user.data) return <ErrorBox error={user.error} />;
  const u = user.data;
  const isSelf = me?.id === u.id;
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
      <ErrorBox error={toggle.error ?? revoke.error} />
      <div className="grid-2">
        <Card title="Identiteit">
          <dl className="kv">
            <dt>Gebruikersnaam</dt>
            <dd>{u.username}</dd>
            <dt>Issuer</dt>
            <dd className="mono small">{u.oidc_issuer}</dd>
            <dt>Subject</dt>
            <dd className="mono small">{u.oidc_subject}</dd>
            <dt>Authentik-groepen</dt>
            <dd>{u.idp_groups?.length ? u.idp_groups.map((g) => <Badge key={g}>{g}</Badge>) : "—"}</dd>
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
