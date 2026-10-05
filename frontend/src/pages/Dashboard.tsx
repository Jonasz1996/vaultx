import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import type { Application, Dashboard } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { StatusBadge } from "../components/catalog";
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { roleLabels, userLabel } from "../format";

export function DashboardPage() {
  const { data: me } = useMe();
  const { data, error, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<Dashboard>("/api/v1/dashboard"),
  });
  const catalog = useQuery({ queryKey: ["catalog", "", ""], queryFn: () => api.get<Application[]>("/api/v1/catalog") });
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorBox error={error} />;
  const orgs = me?.memberships.filter((m) => m.team_id === null) ?? [];
  const apps = catalog.data ?? [];
  const protectedCount = apps.filter((a) => a.status === "protected").length;
  const severity = ["offline", "unprotected", "unknown", "restricted", "protected", "removed"];
  const attention = apps
    .filter((a) => ["unknown", "unprotected", "offline"].includes(a.status) || a.warning_count > 0)
    .sort((x, y) => severity.indexOf(x.status) - severity.indexOf(y.status) || y.warning_count - x.warning_count);
  return (
    <>
      <PageHeader title="Dashboard" subtitle={me ? `Welkom, ${userLabel(me)}` : undefined} />
      {me?.is_admin ? (
        <div className="stats">
          <Stat label="Gebruikers" value={data.users_total} hint={`${data.users_active} actief`} />
          <Stat label="Beheerders" value={data.admins} />
          <Stat label="Organisaties" value={data.organizations} />
          <Stat label="Teams" value={data.teams} />
          <Stat label="Actieve sessies" value={data.active_sessions} />
          <Stat label="Logins (24u)" value={data.logins_24h} />
          <Stat label="Geweigerd (24u)" value={data.denied_24h} />
          <Stat label="Applicaties" value={apps.length} hint={`${protectedCount} via Authentik`} />
        </div>
      ) : (
        <div className="stats">
          <Stat label="Mijn organisaties" value={data.organizations} />
          <Stat label="Mijn teams" value={data.teams} />
          <Stat label="Applicaties" value={apps.length} hint={`${protectedCount} via Authentik`} />
        </div>
      )}
      {apps.length > 0 && (
        <Card
          title={`Catalogus: aandacht nodig (${attention.length})`}
          actions={<Link to="/catalog">Catalogus openen</Link>}
        >
          {attention.length === 0 ? (
            <Empty>Alle applicaties zijn bepaald en zonder waarschuwingen.</Empty>
          ) : (
            <ul className="plain-list">
              {attention.slice(0, 8).map((a) => (
                <li key={a.id} className="row wrap">
                  <Link to={`/catalog/${a.organization_id}/${a.id}`}>{a.name}</Link>
                  <StatusBadge status={a.status} />
                  {a.warning_count > 0 && (
                    <span className="muted small">
                      {a.hosts.flatMap((h) => h.warnings).map((w) => w.message)[0]}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
      <div className="grid-2">
        <Card title="Mijn lidmaatschappen">
          {orgs.length === 0 ? (
            <Empty>Je bent nog geen lid van een organisatie.</Empty>
          ) : (
            <ul className="plain-list">
              {orgs.map((m) => (
                <li key={m.id}>
                  <Link to={`/organizations/${m.organization_id}`}>{m.organization_name}</Link>
                  <span className="muted"> · {roleLabels[m.role] ?? m.role}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recente gebeurtenissen" actions={<Link to="/audit">Alles bekijken</Link>}>
          {data.recent_events.length === 0 ? (
            <Empty>Nog geen gebeurtenissen.</Empty>
          ) : (
            <AuditTable items={data.recent_events} compact />
          )}
        </Card>
      </div>
    </>
  );
}
