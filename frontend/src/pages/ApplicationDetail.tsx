import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import type { Application, AuditPage, Organization } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { ApplicationForm, type ApplicationValues, AuthLabel, HostLink, StatusBadge, Warnings } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative, statusInfo } from "../format";

export function ApplicationDetailPage() {
  const { orgId = "", appId = "" } = useParams();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const base = `/api/v1/organizations/${orgId}/applications/${appId}`;
  const manage = canManageOrg(me, orgId);

  const app = useQuery({ queryKey: ["app", appId], queryFn: () => api.get<Application>(base) });
  const org = useQuery({
    queryKey: ["org", orgId],
    queryFn: () => api.get<Organization>(`/api/v1/organizations/${orgId}`),
  });
  const audit = useQuery({
    queryKey: ["audit", "app", appId],
    queryFn: () => api.get<AuditPage>("/api/v1/audit", { organization_id: orgId, target_id: appId, limit: 25 }),
    enabled: manage,
  });
  const refresh = (a: Application) => {
    qc.setQueryData(["app", appId], a);
    void qc.invalidateQueries({ queryKey: ["catalog"] });
    void qc.invalidateQueries({ queryKey: ["audit", "app", appId] });
  };
  const update = useMutation({
    mutationFn: (v: Partial<ApplicationValues> & { auto_update?: boolean }) => api.patch<Application>(base, v),
    onSuccess: (a) => {
      setEditing(false);
      refresh(a);
    },
  });
  const remove = useMutation({
    mutationFn: () => api.delete(base),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["catalog"] });
      navigate("/catalog");
    },
  });

  if (app.isLoading) return <Loading />;
  if (!app.data) return <ErrorBox error={app.error} />;
  const a = app.data;
  return (
    <>
      <PageHeader
        title={a.name}
        subtitle={
          <>
            <Link to="/catalog">Catalogus</Link> · <Link to={`/organizations/${orgId}`}>{org.data?.name ?? "…"}</Link>
            {a.description && ` · ${a.description}`}
          </>
        }
        actions={
          manage && (
            <>
              <button className="btn" onClick={() => setEditing(true)}>
                Bewerken
              </button>
              <button
                className="btn btn-danger"
                onClick={() => {
                  const extra = a.hosts.length
                    ? " De gekoppelde NPM-hosts worden genegeerd, zodat de sync hem niet terugzet."
                    : "";
                  if (confirm(`'${a.name}' uit de catalogus verwijderen?${extra}`)) remove.mutate();
                }}
              >
                Verwijderen
              </button>
            </>
          )
        }
      />
      <ErrorBox error={remove.error ?? update.error} />
      <div className="grid-2">
        <Card title="Overzicht">
          <dl className="kv">
            <dt>Status</dt>
            <dd>
              <StatusBadge status={a.status} />
              <span className="muted small">{statusInfo[a.status]?.hint}</span>
            </dd>
            <dt>Aanmelding</dt>
            <dd>
              <AuthLabel method={a.auth_method} />
            </dd>
            <dt>URL</dt>
            <dd>
              {a.url ? (
                <a href={a.url} target="_blank" rel="noreferrer">
                  {a.url}
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>Type</dt>
            <dd className="mono">{a.app_type ?? "—"}</dd>
            <dt>Tags</dt>
            <dd>{a.tags.length ? a.tags.map((t) => <Badge key={t}>{t}</Badge>) : "—"}</dd>
            <dt>Bron</dt>
            <dd>
              {a.source === "npm" ? <Badge tone="info">NPM</Badge> : <Badge>Manueel</Badge>}
              {a.source === "npm" && (
                <span className="muted small">
                  {a.auto_update ? "Sync houdt dit item bij." : "Handmatig aangepast: de sync laat het met rust."}
                </span>
              )}
            </dd>
            <dt>Aangemaakt</dt>
            <dd>{formatDate(a.created_at)}</dd>
            <dt>Gewijzigd</dt>
            <dd>{relative(a.updated_at)}</dd>
          </dl>
          {manage && a.source === "npm" && (
            <div className="row" style={{ marginTop: 12 }}>
              <button
                className="btn"
                disabled={update.isPending}
                onClick={() => update.mutate({ auto_update: !a.auto_update })}
              >
                {a.auto_update ? "Automatisch bijwerken uitzetten" : "Weer automatisch laten bijwerken"}
              </button>
            </div>
          )}
        </Card>
        <Card title={`Proxy hosts (${a.hosts.length})`}>
          {!a.hosts.length ? (
            <Empty>Geen NPM-host gekoppeld. Dit item is manueel.</Empty>
          ) : (
            <ul className="plain-list">
              {a.hosts.map((h) => (
                <li key={h.id}>
                  <div className="row wrap">
                    {h.domain_names.map((d) => (
                      <HostLink key={d} domain={d} ssl={h.ssl} />
                    ))}
                    {h.removed_at && <Badge>verdwenen</Badge>}
                    {!h.enabled && <Badge tone="bad">uitgeschakeld</Badge>}
                    {h.forward_auth && <Badge tone="good">forward auth</Badge>}
                  </div>
                  <div className="muted small">
                    → <span className="mono">{h.forward}</span> · via{" "}
                    <Link to={`/npm/${orgId}/${h.connection_id}`}>{h.connection_name}</Link>
                  </div>
                  {h.warnings.length > 0 && <Warnings items={h.warnings} />}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {manage && (
        <Card title="Geschiedenis">
          {audit.data?.items.length ? <AuditTable items={audit.data.items} /> : <Empty>Geen gebeurtenissen.</Empty>}
        </Card>
      )}
      <Modal title="Applicatie bewerken" open={editing} onClose={() => setEditing(false)}>
        <ApplicationForm
          initial={a}
          onSubmit={(v) => update.mutate(v)}
          error={update.error}
          pending={update.isPending}
          note={
            a.source === "npm" && a.auto_update
              ? "Na opslaan zet VaultX automatisch bijwerken uit, zodat de volgende sync je keuzes niet overschrijft."
              : undefined
          }
        />
      </Modal>
    </>
  );
}
