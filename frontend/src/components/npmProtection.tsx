import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type {
  AuthentikChange,
  DiscoveredHost,
  HostConfig,
  NpmChange,
  NpmLocation,
  ProbeResult,
  ProtectionAction,
  ProtectionPlan,
  Team,
} from "../api/types";
import { actionLabels, changeStatusInfo, formatDate, relative } from "../format";
import { Badge, Empty, ErrorBox, Loading, Modal } from "./ui";

export function ChangeStatusBadge({ status }: { status: string }) {
  const s = changeStatusInfo[status] ?? { label: status, tone: "neutral" as const, hint: "" };
  return (
    <Badge tone={s.tone} title={s.hint}>
      {s.label}
    </Badge>
  );
}

// Zoals de config in NPM staat: eerst Advanced van de host, dan elke custom location.
function renderConfig(cfg: Partial<HostConfig>): string {
  const parts = [`# Advanced van de host\n${cfg.advanced_config?.trim() || "(leeg)"}`];
  for (const loc of (cfg.locations ?? []) as NpmLocation[]) {
    const target = `${loc.forward_scheme}://${loc.forward_host}:${loc.forward_port}${loc.forward_path ?? ""}`;
    const advanced = String(loc.advanced_config ?? "").trim();
    parts.push(`# Custom location ${loc.path} -> ${target}\n${advanced || "(geen eigen config)"}`);
  }
  return parts.join("\n\n");
}

function ConfigDiff({ before, after }: { before: Partial<HostConfig>; after: Partial<HostConfig> | null }) {
  return (
    <div className="config-diff">
      <div>
        <div className="muted small">Nu in NPM</div>
        <pre>{renderConfig(before)}</pre>
      </div>
      {after && (
        <div>
          <div className="muted small">Na de wijziging</div>
          <pre>{renderConfig(after)}</pre>
        </div>
      )}
    </div>
  );
}

function ProbeLine({ label, probe }: { label: string; probe: ProbeResult | null }) {
  if (!probe) return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <span>{probe.summary}</span>
        <span className="muted small mono">{probe.url}</span>
      </dd>
    </>
  );
}

function AuthentikLines({ ak }: { ak: AuthentikChange | null }) {
  if (!ak) return null;
  const s = ak.state ?? ak.remove;
  if (!s) return null;
  const made = [
    s.provider_name && `provider '${s.provider_name}'${s.provider_created ? " (door VaultX)" : ""}`,
    s.application_slug && `applicatie ${s.application_slug}${s.application_created ? " (door VaultX)" : ""}`,
    s.outpost_name && `outpost '${s.outpost_name}'`,
  ].filter(Boolean);
  let outcome: string | null = null;
  if (ak.undo_error) outcome = `terugdraaien mislukt: ${ak.undo_error}`;
  else if (ak.cleanup_error) outcome = `opruimen mislukt: ${ak.cleanup_error}`;
  else if (ak.undone) outcome = "teruggedraaid";
  else if (ak.removed) outcome = "opgeruimd";
  return (
    <>
      <dt>Authentik</dt>
      <dd>
        <span>{made.join(", ") || "—"}</span>
        {s.groups.length > 0 && <span className="muted small">toegang: {s.groups.join(", ")}</span>}
        {outcome && <span className="muted small">{outcome}</span>}
      </dd>
    </>
  );
}

function ChangeResult({ change }: { change: NpmChange }) {
  const box = change.status === "applied" ? "good-box" : change.status === "refused" ? "info-box" : "warn-box";
  return (
    <div className="form">
      <div className={change.status === "rollback_failed" ? "error-box" : box}>
        <ChangeStatusBadge status={change.status} /> {change.message}
      </div>
      <dl className="kv">
        <ProbeLine label="Controle vooraf" probe={change.probe_before} />
        <ProbeLine label="Controle achteraf" probe={change.probe_after} />
        <AuthentikLines ak={change.authentik} />
        {change.nginx_error && (
          <>
            <dt>nginx-fout</dt>
            <dd className="mono small">{change.nginx_error}</dd>
          </>
        )}
      </dl>
      {change.status === "rollback_failed" && (
        <details open>
          <summary>Config om met de hand terug te zetten in NPM</summary>
          <ConfigDiff before={change.before} after={null} />
        </details>
      )}
    </div>
  );
}

// Toegang tot de Authentik-applicatie die VaultX aanmaakt (fase 4, ook gebruikt in fase 5).
export function AccessPicker({
  orgId,
  value,
  onChange,
  label = "Wie mag de applicatie openen na de Authentik-aanmelding?",
}: {
  orgId: string;
  value: string;
  onChange: (v: string) => void;
  label?: string;
}) {
  const teams = useQuery({
    queryKey: ["teams", orgId],
    queryFn: () => api.get<Team[]>(`/api/v1/organizations/${orgId}/teams`),
  });
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="organization">Leden van deze organisatie</option>
        {(teams.data ?? []).map((t) => (
          <option key={t.id} value={`team:${t.slug}`}>
            Leden van team {t.name}
          </option>
        ))}
        <option value="all">Alle Authentik-gebruikers</option>
      </select>
      <small className="muted">
        VaultX bindt de Authentik-groepen volgens de groepconventie (bv. <code>vaultx:organisatie</code> of{" "}
        <code>vaultx:organisatie/team</code>) aan de applicatie.
      </small>
    </label>
  );
}

export function ProtectionDialog({
  base,
  orgId,
  authentik,
  host,
  action,
  onClose,
  onDone,
}: {
  base: string;
  orgId: string;
  authentik: boolean;
  host: DiscoveredHost;
  action: ProtectionAction;
  onClose: () => void;
  onDone: () => void;
}) {
  const url = `${base}/hosts/${host.id}/protection`;
  const [access, setAccess] = useState("organization");
  const plan = useQuery({
    queryKey: ["npm-plan", host.id, action, access],
    queryFn: () => api.get<ProtectionPlan>(url, { action, access }),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const [verify, setVerify] = useState(true);
  const apply = useMutation({
    mutationFn: (p: ProtectionPlan) =>
      api.post<NpmChange>(url, {
        action,
        expected_modified_on: p.modified_on,
        verify: verify && p.probe_url !== null,
        access,
      }),
    onSettled: onDone,
  });
  const title = `${actionLabels[action]}: ${host.domain_names[0]}`;

  let body;
  if (apply.data) {
    body = (
      <>
        <ChangeResult change={apply.data} />
        <div className="form-actions">
          <button className="btn" onClick={onClose}>
            Sluiten
          </button>
        </div>
      </>
    );
  } else if (plan.isLoading) {
    body = (
      <>
        {action === "protect" && authentik && <AccessPicker orgId={orgId} value={access} onChange={setAccess} />}
        <Loading />
      </>
    );
  } else if (!plan.data) {
    body = <ErrorBox error={plan.error} />;
  } else {
    const p = plan.data;
    const canVerify = p.probe_url !== null;
    body = (
      <div className="form">
        {action === "protect" && authentik && <AccessPicker orgId={orgId} value={access} onChange={setAccess} />}
        {p.checks.length > 0 && (
          <ul className="checks">
            {p.checks.map((c) => (
              <li key={c.code} className={`check-${c.level}`} title={c.code}>
                {c.message}
              </li>
            ))}
          </ul>
        )}
        {p.steps.length > 0 && (
          <div>
            <strong>{p.authentik ? "Wat VaultX in Authentik en NPM wijzigt" : "Wat VaultX in NPM wijzigt"}</strong>
            <ol className="steps">
              {p.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          </div>
        )}
        {p.can_apply && (
          <details>
            <summary>Config ervoor en erna bekijken</summary>
            <ConfigDiff before={p.before} after={p.after} />
          </details>
        )}
        {p.can_apply && (
          <label className="check">
            <input
              type="checkbox"
              checked={verify && canVerify}
              disabled={!canVerify}
              onChange={(e) => setVerify(e.target.checked)}
            />
            <span>
              Controleren en bij een fout terugzetten{" "}
              {canVerify ? (
                <span className="muted small mono">{p.probe_url}</span>
              ) : (
                <span className="muted small">(niet mogelijk voor deze host)</span>
              )}
            </span>
          </label>
        )}
        {p.can_apply && !(verify && canVerify) && (
          <div className="warn-box small">
            Zonder controle zet VaultX enkel terug als nginx de config weigert. Of de host daarna echt naar
            Authentik doorverwijst, kijk je zelf na.
          </div>
        )}
        <ErrorBox error={apply.error} />
        <div className="form-actions row">
          <button className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button
            className={action === "protect" ? "btn btn-primary" : "btn btn-danger"}
            disabled={!p.can_apply || apply.isPending}
            onClick={() => apply.mutate(p)}
          >
            {apply.isPending ? "Bezig, even geduld…" : action === "protect" ? "Bescherming zetten" : "Weghalen"}
          </button>
        </div>
      </div>
    );
  }
  return (
    <Modal title={title} open onClose={onClose} wide>
      {body}
    </Modal>
  );
}

export function ChangeJournal({ base }: { base: string }) {
  const changes = useQuery({
    queryKey: ["npm-changes", base],
    queryFn: () => api.get<NpmChange[]>(`${base}/changes`),
  });
  const [open, setOpen] = useState<NpmChange | null>(null);
  if (changes.isLoading) return <Loading />;
  if (changes.error) return <ErrorBox error={changes.error} />;
  if (!changes.data?.length) return <Empty>VaultX heeft nog niets gewijzigd in deze NPM.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Wanneer</th>
            <th>Host</th>
            <th>Wijziging</th>
            <th>Resultaat</th>
            <th>Door</th>
          </tr>
        </thead>
        <tbody>
          {changes.data.map((c) => (
            <tr key={c.id}>
              <td className="nowrap" title={formatDate(c.created_at)}>
                {relative(c.created_at)}
              </td>
              <td className="mono small">{c.domain}</td>
              <td>
                {actionLabels[c.action]}
                {!c.verified && (
                  <div>
                    <Badge tone="warn">zonder controle</Badge>
                  </div>
                )}
              </td>
              <td>
                <ChangeStatusBadge status={c.status} />
                <div className="small">{c.message}</div>
                <button className="btn btn-ghost small" onClick={() => setOpen(c)}>
                  Details
                </button>
              </td>
              <td className="small">{c.actor_label ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Modal
        title={open ? `${actionLabels[open.action]}: ${open.domain}` : ""}
        open={open !== null}
        onClose={() => setOpen(null)}
        wide
      >
        {open && (
          <div className="form">
            <ChangeResult change={open} />
            {open.status !== "rollback_failed" && <ConfigDiff before={open.before} after={open.after} />}
            <div className="muted small">
              {formatDate(open.created_at)} door {open.actor_label ?? "?"}
              {open.finished_at && `, klaar ${formatDate(open.finished_at)}`}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
