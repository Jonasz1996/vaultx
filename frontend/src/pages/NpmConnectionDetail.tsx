import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import type { DiscoveredHost, NpmConnection, Organization, SyncResult } from "../api/types";
import { AuthLabel, ConnectionForm, type ConnectionValues, HostLink, Warnings } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative } from "../format";
import { SyncStatus, syncSummary } from "./NpmConnections";

export function NpmConnectionDetailPage() {
  const { orgId = "", connId = "" } = useParams();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const base = `/api/v1/organizations/${orgId}/npm-connections/${connId}`;
  const manage = canManageOrg(me, orgId);

  const conn = useQuery({ queryKey: ["npm-conn", connId], queryFn: () => api.get<NpmConnection>(base) });
  const hosts = useQuery({ queryKey: ["npm-hosts", connId], queryFn: () => api.get<DiscoveredHost[]>(`${base}/hosts`) });
  const org = useQuery({
    queryKey: ["org", orgId],
    queryFn: () => api.get<Organization>(`/api/v1/organizations/${orgId}`),
  });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["npm-conn", connId] });
    void qc.invalidateQueries({ queryKey: ["npm-hosts", connId] });
    void qc.invalidateQueries({ queryKey: ["npm", orgId] });
    void qc.invalidateQueries({ queryKey: ["catalog"] });
  };
  const sync = useMutation({
    mutationFn: () => api.post<SyncResult>(`${base}/sync`),
    onSuccess: (r) => setSummary(syncSummary(r)),
    onSettled: invalidate,
  });
  const update = useMutation({
    mutationFn: (v: ConnectionValues) => api.patch<NpmConnection>(base, v),
    onSuccess: () => {
      setEditing(false);
      invalidate();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.delete(base),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["npm", orgId] });
      navigate("/npm");
    },
  });
  const ignore = useMutation({
    mutationFn: (h: DiscoveredHost) => api.patch<DiscoveredHost>(`${base}/hosts/${h.id}`, { ignored: !h.ignored }),
    onSuccess: invalidate,
  });

  if (conn.isLoading) return <Loading />;
  if (!conn.data) return <ErrorBox error={conn.error} />;
  const c = conn.data;
  const all = hosts.data ?? [];
  const removedCount = all.filter((h) => h.removed_at).length;
  const shown = showRemoved ? all : all.filter((h) => !h.removed_at);
  return (
    <>
      <PageHeader
        title={c.name}
        subtitle={
          <>
            <Link to="/npm">NPM</Link> · <Link to={`/organizations/${orgId}`}>{org.data?.name ?? "…"}</Link> ·{" "}
            <span className="mono">{c.base_url}</span>
          </>
        }
        actions={
          manage && (
            <>
              <button className="btn btn-primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
                {sync.isPending ? "Bezig…" : "Nu synchroniseren"}
              </button>
              <button className="btn" onClick={() => setEditing(true)}>
                Bewerken
              </button>
              <button
                className="btn btn-danger"
                onClick={() => {
                  if (confirm(`Koppeling '${c.name}' verwijderen? De catalogusitems blijven bestaan, zonder host.`))
                    remove.mutate();
                }}
              >
                Verwijderen
              </button>
            </>
          )
        }
      />
      <ErrorBox error={sync.error ?? remove.error ?? ignore.error} />
      {summary && !sync.error && <div className="info-box">{summary}</div>}
      <Card title="Koppeling">
        <dl className="kv">
          <dt>Laatste sync</dt>
          <dd>
            <SyncStatus c={c} />
            {c.last_sync_at && <span className="muted small">{formatDate(c.last_sync_at)}</span>}
          </dd>
          {c.last_sync_error && (
            <>
              <dt>Fout</dt>
              <dd style={{ color: "var(--bad-fg)" }}>{c.last_sync_error}</dd>
            </>
          )}
          <dt>NPM-versie</dt>
          <dd className="mono">{c.npm_version ?? "—"}</dd>
          <dt>Account</dt>
          <dd>{c.identity}</dd>
          <dt>TLS controleren</dt>
          <dd>{c.verify_tls ? "ja" : <Badge tone="warn">nee</Badge>}</dd>
          <dt>Automatisch</dt>
          <dd>{c.enabled ? "ja, periodiek" : "nee, enkel handmatig"}</dd>
        </dl>
      </Card>
      <Card
        title={`Proxy hosts (${all.length - removedCount})`}
        actions={
          removedCount > 0 && (
            <label className="check">
              <input type="checkbox" checked={showRemoved} onChange={(e) => setShowRemoved(e.target.checked)} />
              Ook {removedCount} verdwenen host{removedCount > 1 ? "s" : ""} tonen
            </label>
          )
        }
      >
        {hosts.isLoading ? (
          <Loading />
        ) : !shown.length ? (
          <Empty>{c.last_sync_status ? "Geen proxy hosts gevonden." : "Nog niet gesynchroniseerd."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Host</th>
                  <th>Doel</th>
                  <th>Gedetecteerd</th>
                  <th>Waarschuwingen</th>
                  <th>Catalogus</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((h) => (
                  <tr key={h.id} className={h.removed_at || h.ignored ? "dim" : undefined}>
                    <td>
                      <div className="stack">
                        {h.domain_names.map((d) => (
                          <HostLink key={d} domain={d} ssl={h.ssl} />
                        ))}
                      </div>
                      <div className="row wrap small">
                        <span className="muted">#{h.npm_id}</span>
                        {h.removed_at && <Badge>verdwenen {relative(h.removed_at)}</Badge>}
                        {!h.enabled && <Badge tone="bad">uitgeschakeld</Badge>}
                        {!h.nginx_online && <Badge tone="bad">nginx-fout</Badge>}
                        {h.ssl ? <Badge tone="good">TLS</Badge> : <Badge tone="warn">geen TLS</Badge>}
                      </div>
                    </td>
                    <td className="mono small">
                      {h.forward_scheme}://{h.forward_host}:{h.forward_port}
                    </td>
                    <td>
                      <AuthLabel method={h.detected_auth} />
                      {h.access_list && <div className="muted small">access list: {h.access_list}</div>}
                      {h.detected_app_type && <div className="mono small muted">{h.detected_app_type}</div>}
                      {Object.keys(h.labels).length > 0 && (
                        <div className="row wrap">
                          {Object.entries(h.labels).map(([k, v]) => (
                            <Badge key={k} tone="info" title="Label uit Advanced">
                              {k}={v}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="small">
                      <Warnings items={h.warnings} />
                    </td>
                    <td>
                      {h.application ? (
                        <Link to={`/catalog/${orgId}/${h.application.id}`}>{h.application.name}</Link>
                      ) : h.ignored ? (
                        <Badge>genegeerd</Badge>
                      ) : h.labels.ignore ? (
                        <Badge title="Label vaultx.ignore">genegeerd (label)</Badge>
                      ) : (
                        <span className="muted">—</span>
                      )}
                      {manage && !h.removed_at && !h.labels.ignore && (
                        <div>
                          <button
                            className="btn btn-ghost small"
                            disabled={ignore.isPending}
                            onClick={() => {
                              if (
                                h.ignored ||
                                confirm(`${h.domain_names[0]} negeren? Een automatisch aangemaakt catalogusitem verdwijnt.`)
                              )
                                ignore.mutate(h);
                            }}
                          >
                            {h.ignored ? "Weer opnemen" : "Negeren"}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Modal title="Koppeling bewerken" open={editing} onClose={() => setEditing(false)}>
        <ConnectionForm initial={c} onSubmit={(v) => update.mutate(v)} error={update.error} pending={update.isPending} />
      </Modal>
    </>
  );
}
