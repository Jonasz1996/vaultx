import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, canManageTeam, useMe } from "../api/hooks";
import type { Organization, Team } from "../api/types";
import { EntityForm, type EntityValues } from "../components/EntityForm";
import { Members } from "../components/Members";
import { ErrorBox, Loading, Modal, PageHeader } from "../components/ui";

export function TeamDetailPage() {
  const { orgId = "", teamId = "" } = useParams();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const base = `/api/v1/organizations/${orgId}/teams/${teamId}`;
  const org = useQuery({ queryKey: ["org", orgId], queryFn: () => api.get<Organization>(`/api/v1/organizations/${orgId}`) });
  const team = useQuery({ queryKey: ["team", teamId], queryFn: () => api.get<Team>(base) });
  const update = useMutation({
    mutationFn: (v: EntityValues) => api.patch(base, { name: v.name, description: v.description || null }),
    onSuccess: () => {
      setEditing(false);
      void qc.invalidateQueries({ queryKey: ["team", teamId] });
      void qc.invalidateQueries({ queryKey: ["teams", orgId] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.delete(base),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["teams", orgId] });
      navigate(`/organizations/${orgId}`);
    },
  });
  if (team.isLoading) return <Loading />;
  if (!team.data) return <ErrorBox error={team.error} />;
  const t = team.data;
  const manageTeam = canManageTeam(me, orgId, teamId);
  return (
    <>
      <PageHeader
        title={t.name}
        subtitle={
          <>
            Team in <Link to={`/organizations/${orgId}`}>{org.data?.name ?? "organisatie"}</Link> ·{" "}
            <span className="mono">
              {org.data?.slug}/{t.slug}
            </span>
            {t.description && ` · ${t.description}`}
          </>
        }
        actions={
          <>
            {manageTeam && (
              <button className="btn" onClick={() => setEditing(true)}>
                Bewerken
              </button>
            )}
            {canManageOrg(me, orgId) && (
              <button
                className="btn btn-danger"
                onClick={() => {
                  if (confirm(`Team '${t.name}' verwijderen?`)) remove.mutate();
                }}
              >
                Verwijderen
              </button>
            )}
          </>
        }
      />
      <ErrorBox error={remove.error} />
      <Members basePath={base} roles={["maintainer", "member"]} canManage={manageTeam} title="Teamleden" />
      <Modal title="Team bewerken" open={editing} onClose={() => setEditing(false)}>
        <EntityForm
          editing
          initial={{ name: t.name, slug: t.slug, description: t.description ?? "" }}
          onSubmit={(v) => update.mutate(v)}
          error={update.error}
          pending={update.isPending}
        />
      </Modal>
    </>
  );
}
