import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, isOrgOwner, useMe } from "../api/hooks";
import type { AuditPage, AuditVerify, Organization, Team } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { EntityForm, type EntityValues } from "../components/EntityForm";
import { Members } from "../components/Members";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";

export function OrganizationDetailPage() {
  const { orgId = "" } = useParams();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [creatingTeam, setCreatingTeam] = useState(false);
  const base = `/api/v1/organizations/${orgId}`;
  const manage = canManageOrg(me, orgId);
  const owner = isOrgOwner(me, orgId);

  const org = useQuery({ queryKey: ["org", orgId], queryFn: () => api.get<Organization>(base) });
  const teams = useQuery({ queryKey: ["teams", orgId], queryFn: () => api.get<Team[]>(`${base}/teams`) });
  const audit = useQuery({
    queryKey: ["audit", "org", orgId],
    queryFn: () => api.get<AuditPage>("/api/v1/audit", { organization_id: orgId, limit: 15 }),
    enabled: manage,
  });
  const verify = useMutation({
    mutationFn: () => api.get<AuditVerify>("/api/v1/audit/verify", { organization_id: orgId }),
  });
  const update = useMutation({
    mutationFn: (v: EntityValues) => api.patch(base, { name: v.name, description: v.description || null }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ["org", orgId] });
      void qc.invalidateQueries({ queryKey: ["orgs"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.delete(base),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      navigate("/organizations");
    },
  });
  const createTeam = useMutation({
    mutationFn: (v: EntityValues) => api.post<Team>(`${base}/teams`, { ...v, description: v.description || null }),
    onSuccess: (t) => {
      setCreatingTeam(false);
      void qc.invalidateQueries({ queryKey: ["teams", orgId] });
      navigate(`/organizations/${orgId}/teams/${t.id}`);
    },
  });

  if (org.isLoading) return <Loading />;
  if (!org.data) return <ErrorBox error={org.error} />;
  const o = org.data;
  return (
    <>
      <PageHeader
        title={o.name}
        subtitle={
          <>
            <span className="mono">{o.slug}</span> {o.description && `· ${o.description}`}
          </>
        }
        actions={
          <>
            {manage && (
              <button className="btn" onClick={() => setEditing(true)}>
                Bewerken
              </button>
            )}
            {owner && (
              <button
                className="btn btn-danger"
                onClick={() => {
                  if (confirm(`Organisatie '${o.name}' en al haar teams verwijderen?`)) remove.mutate();
                }}
              >
                Verwijderen
              </button>
            )}
          </>
        }
      />
      <ErrorBox error={remove.error} />
      <div className="grid-2">
        <Members basePath={base} roles={["owner", "admin", "member"]} canManage={manage} canGrantOwner={owner} />
        <Card
          title={`Teams${teams.data ? ` (${teams.data.length})` : ""}`}
          actions={
            manage && (
              <button className="btn btn-primary" onClick={() => setCreatingTeam(true)}>
                Nieuw team
              </button>
            )
          }
        >
          {!teams.data?.length ? (
            <Empty>Nog geen teams.</Empty>
          ) : (
            <ul className="plain-list">
              {teams.data.map((t) => (
                <li key={t.id}>
                  <Link to={`/organizations/${orgId}/teams/${t.id}`}>{t.name}</Link>
                  <span className="muted"> · {t.member_count} leden</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      {manage && (
        <Card
          title="Auditlog van deze organisatie"
          actions={
            <div className="row">
              {verify.data &&
                (verify.data.valid ? (
                  <Badge tone="good">keten intact ({verify.data.entries_checked})</Badge>
                ) : (
                  <Badge tone="bad">
                    keten gebroken bij #{verify.data.broken_at_id}: {verify.data.reason}
                  </Badge>
                ))}
              <button className="btn" onClick={() => verify.mutate()} disabled={verify.isPending}>
                Keten controleren
              </button>
              <Link to={`/audit?organization_id=${orgId}`}>Alles</Link>
            </div>
          }
        >
          {audit.data?.items.length ? <AuditTable items={audit.data.items} /> : <Empty>Geen gebeurtenissen.</Empty>}
        </Card>
      )}
      <Modal title="Organisatie bewerken" open={editing} onClose={() => setEditing(false)}>
        <EntityForm
          editing
          initial={{ name: o.name, slug: o.slug, description: o.description ?? "" }}
          onSubmit={(v) => update.mutate(v)}
          error={update.error}
          pending={update.isPending}
        />
      </Modal>
      <Modal title="Nieuw team" open={creatingTeam} onClose={() => setCreatingTeam(false)}>
        <EntityForm
          onSubmit={(v) => createTeam.mutate(v)}
          error={createTeam.error}
          pending={createTeam.isPending}
          slugHelp={`Authentik-groep 'vaultx:${o.slug}/<slug>' maakt gebruikers automatisch lid.`}
        />
      </Modal>
    </>
  );
}
