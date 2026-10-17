import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import type { NpmConnection, Organization, Page, SyncResult } from "../api/types";
import { ConnectionForm, type ConnectionValues } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { relative } from "../format";

export function SyncStatus({ c }: { c: NpmConnection }) {
  if (!c.last_sync_status) return <Badge>Nog niet gesynchroniseerd</Badge>;
  return c.last_sync_status === "ok" ? (
    <Badge tone="good" title={c.last_sync_at ?? ""}>
      OK · {relative(c.last_sync_at)}
    </Badge>
  ) : (
    <Badge tone="bad" title={c.last_sync_error ?? ""}>
      Fout · {relative(c.last_sync_at)}
    </Badge>
  );
}

export function syncSummary(r: SyncResult): string {
  return (
    `${r.hosts_total} hosts gelezen (${r.hosts_new} nieuw, ${r.hosts_updated} gewijzigd, ${r.hosts_removed} verdwenen); ` +
    `${r.applications_created} applicaties aangemaakt, ${r.applications_updated} bijgewerkt.`
  );
}

export function NpmConnectionsPage() {
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const [createOrg, setCreateOrg] = useState("");
  const [lastSync, setLastSync] = useState<{ id: string; text: string } | null>(null);

  const orgs = useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<Page<Organization>>("/api/v1/organizations", { limit: 200 }),
  });
  const orgList = orgs.data?.items ?? [];
  const conns = useQueries({
    queries: orgList.map((o) => ({
      queryKey: ["npm", o.id],
      queryFn: () => api.get<NpmConnection[]>(`/api/v1/organizations/${o.id}/npm-connections`),
    })),
  });
  const manageable = orgList.filter((o) => canManageOrg(me, o.id));

  const create = useMutation({
    mutationFn: (v: ConnectionValues) =>
      api.post<NpmConnection>(`/api/v1/organizations/${createOrg}/npm-connections`, v),
    onSuccess: (c) => {
      setCreating(false);
      void qc.invalidateQueries({ queryKey: ["npm", c.organization_id] });
      navigate(`/npm/${c.organization_id}/${c.id}`);
    },
  });
  const sync = useMutation({
    mutationFn: (c: NpmConnection) =>
      api.post<SyncResult>(`/api/v1/organizations/${c.organization_id}/npm-connections/${c.id}/sync`),
    onSuccess: (r) => setLastSync({ id: r.connection_id, text: syncSummary(r) }),
    onSettled: (_r, _e, c) => {
      void qc.invalidateQueries({ queryKey: ["npm", c.organization_id] });
      void qc.invalidateQueries({ queryKey: ["catalog"] });
    },
  });

  return (
    <>
      <PageHeader
        title="Nginx Proxy Manager"
        subtitle="VaultX leest de proxy hosts uit NPM en vult er de applicatiecatalogus mee. Er wordt niets in NPM gewijzigd."
        actions={
          manageable.length > 0 && (
            <button
              className="btn btn-primary"
              onClick={() => {
                setCreateOrg(manageable[0].id);
                setCreating(true);
              }}
            >
              NPM koppelen
            </button>
          )
        }
      />
      {orgs.isLoading && <Loading />}
      <ErrorBox error={orgs.error ?? sync.error} />
      {lastSync && <div className="info-box">{lastSync.text}</div>}
      {!orgs.isLoading && orgList.length === 0 && (
        <Card>
          <Empty>Je bent nog geen lid van een organisatie. Een NPM-koppeling hoort altijd bij een organisatie.</Empty>
        </Card>
      )}
      {orgList.map((o, i) => {
        const list = conns[i]?.data ?? [];
        const manage = canManageOrg(me, o.id);
        return (
          <Card key={o.id} title={o.name}>
            {conns[i]?.isLoading ? (
              <Loading />
            ) : !list.length ? (
              <Empty>Geen NPM-koppeling voor deze organisatie.</Empty>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Koppeling</th>
                      <th>NPM</th>
                      <th>Hosts</th>
                      <th>Laatste sync</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((c) => (
                      <tr key={c.id}>
                        <td>
                          <Link to={`/npm/${o.id}/${c.id}`}>
                            <strong>{c.name}</strong>
                          </Link>
                          {!c.enabled && <Badge>Automatisch uit</Badge>}
                          <div className="mono small muted">{c.base_url}</div>
                        </td>
                        <td>{c.npm_version ? <span className="mono">{c.npm_version}</span> : <span className="muted">—</span>}</td>
                        <td>{c.host_count}</td>
                        <td>
                          <SyncStatus c={c} />
                          {c.last_sync_status === "error" && (
                            <div className="small" style={{ color: "var(--bad-fg)" }}>
                              {c.last_sync_error}
                            </div>
                          )}
                        </td>
                        <td className="right">
                          {manage && (
                            <button
                              className="btn"
                              disabled={sync.isPending && sync.variables?.id === c.id}
                              onClick={() => sync.mutate(c)}
                            >
                              {sync.isPending && sync.variables?.id === c.id ? "Bezig…" : "Nu synchroniseren"}
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
        );
      })}
      <Card title="Labels in NPM">
        <p className="muted" style={{ marginTop: 0 }}>
          NPM kent geen labels. Zet ze als commentaar in het veld <em>Advanced</em> van een proxy host (of van een
          custom location). nginx negeert commentaar, dus de proxy verandert niet.
        </p>
        <pre className="snippet">{`# vaultx.app = Grafana
# vaultx.auth = oidc          # forward_auth, oidc, saml, header, access_list, app, none
# vaultx.type = grafana
# vaultx.tags = monitoring, ops
# vaultx.description = Dashboards van het team
# vaultx.ignore = true        # niet opnemen in de catalogus`}</pre>
        <p className="muted small">
          Zonder label herkent VaultX Authentik forward auth (<code>auth_request</code> naar de outpost) en NPM access
          lists zelf, en raadt het applicatietype uit het subdomein, de forward host of de poort.
        </p>
      </Card>
      <Modal title="NPM koppelen" open={creating} onClose={() => setCreating(false)}>
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
        <ConnectionForm onSubmit={(v) => create.mutate(v)} error={create.error} pending={create.isPending} />
      </Modal>
    </>
  );
}
