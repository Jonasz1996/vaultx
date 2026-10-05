import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import type { Application, Organization, Page } from "../api/types";
import { ApplicationForm, type ApplicationValues, AuthLabel, HostLink, StatusBadge } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader, Stat } from "../components/ui";
import { statusInfo } from "../format";

const STATUS_ORDER = ["protected", "restricted", "unknown", "unprotected", "offline", "removed"];

export function CatalogPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const orgFilter = params.get("organization_id") ?? "";
  const statusFilter = params.get("status") ?? "";
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [createOrg, setCreateOrg] = useState("");

  const orgs = useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<Page<Organization>>("/api/v1/organizations", { limit: 200 }),
  });
  const catalog = useQuery({
    queryKey: ["catalog", orgFilter, q],
    queryFn: () => api.get<Application[]>("/api/v1/catalog", { organization_id: orgFilter, q }),
  });
  const create = useMutation({
    mutationFn: (v: ApplicationValues) =>
      api.post<Application>(`/api/v1/organizations/${createOrg}/applications`, v),
    onSuccess: (a) => {
      setCreating(false);
      void qc.invalidateQueries({ queryKey: ["catalog"] });
      navigate(`/catalog/${a.organization_id}/${a.id}`);
    },
  });

  const orgName = new Map(orgs.data?.items.map((o) => [o.id, o.name]));
  const manageable = orgs.data?.items.filter((o) => canManageOrg(me, o.id)) ?? [];
  const all = catalog.data ?? [];
  const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, all.filter((a) => a.status === s).length]));
  const shown = statusFilter ? all.filter((a) => a.status === statusFilter) : all;
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Applicatiecatalogus"
        subtitle="Alles wat achter Nginx Proxy Manager draait, met hoe je er binnenkomt. NPM-koppelingen vullen dit automatisch."
        actions={
          <>
            <Link className="btn" to="/npm">
              NPM
            </Link>
            {manageable.length > 0 && (
              <button
                className="btn btn-primary"
                onClick={() => {
                  setCreateOrg(orgFilter && manageable.some((o) => o.id === orgFilter) ? orgFilter : manageable[0].id);
                  setCreating(true);
                }}
              >
                Applicatie toevoegen
              </button>
            )}
          </>
        }
      />
      <div className="stats">
        <Stat label="Applicaties" value={all.length} />
        {STATUS_ORDER.filter((s) => counts[s] > 0 || s === "protected" || s === "unknown").map((s) => (
          <button
            key={s}
            className={`stat stat-button${statusFilter === s ? " selected" : ""}`}
            onClick={() => setFilter("status", statusFilter === s ? "" : s)}
            title={statusInfo[s].hint}
          >
            <div className="stat-value">{counts[s]}</div>
            <div className="stat-label">{statusInfo[s].label}</div>
          </button>
        ))}
      </div>
      <Card
        actions={
          <div className="row wrap">
            <input
              className="search"
              placeholder="Zoeken op naam, URL of type"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <select value={orgFilter} onChange={(e) => setFilter("organization_id", e.target.value)}>
              <option value="">Alle organisaties</option>
              {orgs.data?.items.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
            {statusFilter && (
              <button className="btn btn-ghost" onClick={() => setFilter("status", "")}>
                Filter wissen ({statusInfo[statusFilter]?.label})
              </button>
            )}
          </div>
        }
      >
        <ErrorBox error={catalog.error} />
        {catalog.isLoading ? (
          <Loading />
        ) : !shown.length ? (
          <Empty>
            {all.length ? "Geen applicaties met deze filter." : "De catalogus is nog leeg. Koppel een Nginx Proxy Manager om hem te vullen."}
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Applicatie</th>
                  <th>Host</th>
                  <th>Aanmelding</th>
                  <th>Status</th>
                  <th>Organisatie</th>
                  <th>Bron</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((a) => {
                  const live = a.hosts.filter((h) => !h.removed_at);
                  const domains = (live.length ? live : a.hosts).flatMap((h) =>
                    h.domain_names.map((d) => ({ d, ssl: h.ssl })),
                  );
                  return (
                    <tr key={a.id}>
                      <td>
                        <Link to={`/catalog/${a.organization_id}/${a.id}`}>
                          <strong>{a.name}</strong>
                        </Link>
                        <div className="row wrap small">
                          {a.app_type && <span className="muted mono">{a.app_type}</span>}
                          {a.tags.map((t) => (
                            <Badge key={t}>{t}</Badge>
                          ))}
                        </div>
                      </td>
                      <td>
                        {domains.length ? (
                          <div className="stack">
                            {domains.slice(0, 3).map(({ d, ssl }) => (
                              <HostLink key={d} domain={d} ssl={ssl} />
                            ))}
                            {domains.length > 3 && <span className="muted small">+{domains.length - 3}</span>}
                          </div>
                        ) : a.url ? (
                          <a className="mono small" href={a.url} target="_blank" rel="noreferrer">
                            {a.url}
                          </a>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        <AuthLabel method={a.auth_method} />
                      </td>
                      <td className="nowrap">
                        <StatusBadge status={a.status} />
                        {a.warning_count > 0 && (
                          <Badge tone="warn" title="Waarschuwingen bij de host">
                            {a.warning_count} ⚠
                          </Badge>
                        )}
                      </td>
                      <td>
                        <Link to={`/organizations/${a.organization_id}`}>{orgName.get(a.organization_id) ?? "—"}</Link>
                      </td>
                      <td>
                        {a.source === "npm" ? (
                          <Badge tone="info" title={a.auto_update ? "Sync houdt dit item bij" : "Handmatig aangepast"}>
                            NPM{a.auto_update ? "" : " · aangepast"}
                          </Badge>
                        ) : (
                          <Badge>Manueel</Badge>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal title="Applicatie toevoegen" open={creating} onClose={() => setCreating(false)}>
        {manageable.length > 1 && (
          <label className="field" style={{ marginBottom: 12 }}>
            <span>Organisatie</span>
            <select value={createOrg} onChange={(e) => setCreateOrg(e.target.value)}>
              {manageable.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <ApplicationForm
          submitLabel="Toevoegen"
          onSubmit={(v) => create.mutate(v)}
          error={create.error}
          pending={create.isPending}
        />
      </Modal>
    </>
  );
}
