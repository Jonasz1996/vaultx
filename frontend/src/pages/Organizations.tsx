import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import type { Organization, Page } from "../api/types";
import { EntityForm, type EntityValues } from "../components/EntityForm";
import { Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { roleLabels } from "../format";

export function OrganizationsPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<Page<Organization>>("/api/v1/organizations", { limit: 200 }),
  });
  const create = useMutation({
    mutationFn: (v: EntityValues) =>
      api.post<Organization>("/api/v1/organizations", { ...v, description: v.description || null }),
    onSuccess: (org) => {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      void qc.invalidateQueries({ queryKey: ["me"] });
      navigate(`/organizations/${org.id}`);
    },
  });
  return (
    <>
      <PageHeader
        title="Organisaties"
        subtitle="Tenants binnen VaultX. Teams, en later kluizen en applicaties, hangen onder een organisatie."
        actions={
          me?.is_admin && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              Nieuwe organisatie
            </button>
          )
        }
      />
      <Card>
        <ErrorBox error={error} />
        {isLoading ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty>{me?.is_admin ? "Nog geen organisaties. Maak er een aan." : "Je bent nog geen lid van een organisatie."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Naam</th>
                  <th>Slug</th>
                  <th>Leden</th>
                  <th>Teams</th>
                  <th>Mijn rol</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((o) => (
                  <tr key={o.id}>
                    <td>
                      <Link to={`/organizations/${o.id}`}>{o.name}</Link>
                    </td>
                    <td className="mono small">{o.slug}</td>
                    <td>{o.member_count}</td>
                    <td>{o.team_count}</td>
                    <td>{o.my_role ? roleLabels[o.my_role] : <span className="muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal title="Nieuwe organisatie" open={creating} onClose={() => setCreating(false)}>
        <EntityForm
          onSubmit={(v) => create.mutate(v)}
          error={create.error}
          pending={create.isPending}
          slugHelp="Vast na aanmaken. Authentik-groep 'vaultx:<slug>' maakt gebruikers automatisch lid."
        />
      </Modal>
    </>
  );
}
