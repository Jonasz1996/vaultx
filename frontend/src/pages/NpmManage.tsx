// NPM volledig beheren: proxy hosts, redirects, 404-hosts, streams, access lists en certificaten.
// Elke wijziging gaat via een voorbeeld (wat VaultX zou doen, met controles) en wordt daarna
// uitgevoerd met controle en rollback door de backend (services/npm_manage.py).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import type { NpmConnection, ProbeResult } from "../api/types";
import { ChangeStatusBadge } from "../components/npmProtection";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative } from "../format";

export type Kind = "proxy_host" | "redirection_host" | "dead_host" | "stream" | "access_list" | "certificate";
type Action = "create" | "update" | "delete" | "enable" | "disable" | "renew" | "upload";
type Data = Record<string, unknown>;

interface Row extends Data {
  id: number;
  label: string;
  modified_on?: string;
  enabled?: boolean;
  nginx_online?: boolean | null;
  nginx_err?: string | null;
  domain_names?: string[];
  certificate_id?: number;
  managed_blocks?: string[];
  vaultx_published?: boolean;
  vaultx_protected?: boolean;
  used_by?: string[];
}

type Overview = Record<Kind, Row[]>;

interface Check {
  code: string;
  level: "block" | "warn" | "info";
  message: string;
}

interface Plan {
  kind: Kind;
  action: Action;
  npm_id: number | null;
  label: string;
  modified_on: string | null;
  can_apply: boolean;
  checks: Check[];
  steps: string[];
  changes: { field: string; before: unknown; after: unknown }[];
  expect: string | null;
  probe_url: string | null;
  certificate_check: { cn: string | null; issuer: string | null; valid_to: string | null } | null;
}

export interface ManageChange {
  id: string;
  object_type: Kind;
  npm_id: number;
  domain: string;
  action: string;
  status: string;
  verified: boolean;
  actor_label: string | null;
  message: string | null;
  nginx_error: string | null;
  before: Data;
  after: { object?: Data; changes?: Plan["changes"]; steps?: string[] } | null;
  probe_before: ProbeResult | null;
  probe_after: ProbeResult | null;
  created_at: string;
  finished_at: string | null;
}

interface DnsProvider {
  id: string;
  name: string | null;
  credentials: string | null;
}

const KINDS: { key: Kind; tab: string; one: string; nieuw: string }[] = [
  { key: "proxy_host", tab: "Proxy hosts", one: "proxy host", nieuw: "Nieuwe proxy host" },
  { key: "redirection_host", tab: "Redirects", one: "redirection host", nieuw: "Nieuwe redirect" },
  { key: "dead_host", tab: "404-hosts", one: "404-host", nieuw: "Nieuwe 404-host" },
  { key: "stream", tab: "Streams", one: "stream", nieuw: "Nieuwe stream" },
  { key: "access_list", tab: "Access lists", one: "access list", nieuw: "Nieuwe access list" },
  { key: "certificate", tab: "Certificaten", one: "certificaat", nieuw: "Nieuw certificaat" },
];
const kindInfo = (k: Kind) => KINDS.find((x) => x.key === k)!;

export const manageActionLabels: Record<string, string> = {
  create: "Aanmaken",
  update: "Bewerken",
  delete: "Verwijderen",
  enable: "Aanzetten",
  disable: "Uitzetten",
  renew: "Vernieuwen",
  upload: "Nieuw bestand",
};

const FIELD_LABELS: Record<string, string> = {
  domain_names: "Domeinnamen",
  forward_scheme: "Schema",
  forward_host: "Doelhost",
  forward_port: "Doelpoort",
  certificate_id: "Certificaat",
  ssl_forced: "Force SSL",
  hsts_enabled: "HSTS",
  hsts_subdomains: "HSTS subdomeinen",
  trust_forwarded_proto: "X-Forwarded-Proto vertrouwen",
  http2_support: "HTTP/2",
  block_exploits: "Block common exploits",
  caching_enabled: "Cache",
  allow_websocket_upgrade: "Websockets",
  access_list_id: "Access list",
  advanced_config: "Advanced",
  locations: "Custom locations",
  forward_http_code: "HTTP-code",
  forward_domain_name: "Doeldomein",
  preserve_path: "Pad behouden",
  incoming_port: "Inkomende poort",
  forwarding_host: "Doelhost",
  forwarding_port: "Doelpoort",
  tcp_forwarding: "TCP",
  udp_forwarding: "UDP",
  name: "Naam",
  satisfy_any: "Satisfy Any",
  pass_auth: "Auth doorgeven",
  items: "Gebruikers",
  clients: "Adressen",
  provider: "Soort",
  nice_name: "Naam",
  meta: "Opties",
};

// ---------------------------------------------------------------- pagina

export function NpmManagePage() {
  const { orgId = "", connId = "" } = useParams();
  const { data: me } = useMe();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") ?? "proxy_host") as Kind | "journal";
  const base = `/api/v1/organizations/${orgId}/npm-connections/${connId}`;
  const mbase = `${base}/manage`;
  const manage = canManageOrg(me, orgId);
  const [editor, setEditor] = useState<EditorState | null>(null);

  const conn = useQuery({ queryKey: ["npm-conn", connId], queryFn: () => api.get<NpmConnection>(base) });
  const overview = useQuery({
    queryKey: ["npm-manage", connId],
    queryFn: () => api.get<Overview>(mbase),
    enabled: manage,
    refetchOnWindowFocus: false,
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["npm-manage", connId] });
    void qc.invalidateQueries({ queryKey: ["npm-manage-changes", connId] });
    void qc.invalidateQueries({ queryKey: ["npm-hosts", connId] });
    void qc.invalidateQueries({ queryKey: ["npm-changes", base] });
    void qc.invalidateQueries({ queryKey: ["catalog"] });
  };

  if (!manage)
    return (
      <div className="content">
        <ErrorBox error={new Error("Enkel beheerders van de organisatie kunnen NPM beheren.")} />
      </div>
    );
  if (conn.isLoading) return <Loading />;
  if (!conn.data) return <ErrorBox error={conn.error} />;
  const c = conn.data;
  const ov = overview.data;
  const write = c.write_enabled;
  const open = (state: EditorState) => setEditor(state);

  return (
    <>
      <PageHeader
        title={`NPM beheren: ${c.name}`}
        subtitle={
          <>
            <Link to="/npm">NPM</Link> · <Link to={`/npm/${orgId}/${connId}`}>{c.name}</Link> ·{" "}
            <span className="mono">{c.base_url}</span>
          </>
        }
        actions={
          <button className="btn" onClick={refresh} disabled={overview.isFetching}>
            {overview.isFetching ? "Bezig…" : "Vernieuwen"}
          </button>
        }
      />
      {!write && (
        <div className="warn-box">
          Alleen lezen: zet <strong>VaultX mag wijzigen</strong> aan op de <Link to={`/npm/${orgId}/${connId}`}>koppeling</Link>.
          Het NPM-account heeft dan <em>Manage</em> nodig op hosts, streams, access lists en certificaten.
        </div>
      )}
      <div className="info-box small">
        Elke wijziging toont eerst een voorbeeld met controles. VaultX schrijft via de NPM-API, kijkt na of nginx de
        config aanvaardt en of de host nog antwoordt zoals verwacht, en zet anders terug. Blokken van VaultX en regels
        met Authentik, <code>sub_filter</code> (CSS-thema) of <code>add_header</code> in Advanced blijven bewaard.
      </div>
      <nav className="page-tabs manage-tabs">
        {KINDS.map((k) => (
          <a
            key={k.key}
            href={`?tab=${k.key}`}
            className={tab === k.key ? "active" : undefined}
            onClick={(e) => {
              e.preventDefault();
              setParams({ tab: k.key });
            }}
          >
            {k.tab}
            {ov && <span className="muted small"> {ov[k.key].length}</span>}
          </a>
        ))}
        <a
          href="?tab=journal"
          className={tab === "journal" ? "active" : undefined}
          onClick={(e) => {
            e.preventDefault();
            setParams({ tab: "journal" });
          }}
        >
          Journaal
        </a>
      </nav>
      <ErrorBox error={overview.error} />
      {tab === "journal" ? (
        <Card title="Wijzigingen via NPM beheren">
          <ManageJournal
            mbase={mbase}
            connId={connId}
            onRestore={
              write
                ? (ch) =>
                    open({
                      kind: ch.object_type,
                      action: "create",
                      initial: restoreData(ch),
                      title: `${kindInfo(ch.object_type).one} opnieuw aanmaken`,
                    })
                : undefined
            }
          />
        </Card>
      ) : overview.isLoading ? (
        <Loading />
      ) : ov ? (
        <Card
          title={kindInfo(tab).tab}
          actions={
            write && (
              <button className="btn btn-primary" onClick={() => open({ kind: tab, action: "create", initial: {} })}>
                {kindInfo(tab).nieuw}
              </button>
            )
          }
        >
          <KindTable kind={tab} ov={ov} write={write} open={open} />
        </Card>
      ) : null}
      {editor && ov && (
        <ManageDialog
          mbase={mbase}
          state={editor}
          ov={ov}
          onClose={() => setEditor(null)}
          onDone={refresh}
        />
      )}
    </>
  );
}

function restoreData(ch: ManageChange): Data {
  const data: Data = { ...ch.before };
  if (ch.object_type === "access_list" && Array.isArray(data.items))
    data.items = (data.items as Data[]).map((i) => ({ username: i.username, password: "" }));
  return data;
}

// ---------------------------------------------------------------- tabellen

interface EditorState {
  kind: Kind;
  action: Action;
  row?: Row;
  initial: Data;
  title?: string;
}

function certName(ov: Overview, id: unknown): string | null {
  if (!id) return null;
  return ov.certificate.find((c) => c.id === id)?.label ?? `#${String(id)}`;
}

function StatusBadges({ r }: { r: Row }) {
  return (
    <>
      {r.enabled === false && <Badge tone="bad">uit</Badge>}
      {r.nginx_online === false && (
        <Badge tone="bad" title={r.nginx_err ?? ""}>
          nginx-fout
        </Badge>
      )}
      {r.vaultx_published && <Badge tone="info">gepubliceerd door VaultX</Badge>}
      {r.vaultx_protected && <Badge tone="info">Authentik door VaultX</Badge>}
      {(r.managed_blocks ?? []).map((b) => (
        <Badge key={b} tone="info" title="Beheerd blok in Advanced; VaultX laat het ongemoeid">
          vaultx:{b}
        </Badge>
      ))}
    </>
  );
}

function SslCell({ r, ov }: { r: Row; ov: Overview }) {
  const name = certName(ov, r.certificate_id);
  if (!name) return <Badge tone="warn">geen TLS</Badge>;
  return (
    <div className="stack">
      <span className="small">{name}</span>
      <div className="row wrap">
        {r.ssl_forced ? <Badge tone="good">Force SSL</Badge> : null}
        {r.http2_support ? <Badge>HTTP/2</Badge> : null}
        {r.hsts_enabled ? <Badge>HSTS{r.hsts_subdomains ? "+sub" : ""}</Badge> : null}
      </div>
    </div>
  );
}

function RowActions({
  kind,
  r,
  write,
  open,
}: {
  kind: Kind;
  r: Row;
  write: boolean;
  open: (s: EditorState) => void;
}) {
  if (!write) return null;
  const toggles = kind !== "access_list" && kind !== "certificate";
  return (
    <div className="row wrap">
      {kind !== "certificate" && (
        <button className="btn btn-ghost small" onClick={() => open({ kind, action: "update", row: r, initial: r })}>
          Bewerken
        </button>
      )}
      {kind === "certificate" && r.provider === "letsencrypt" && (
        <button className="btn btn-ghost small" onClick={() => open({ kind, action: "renew", row: r, initial: {} })}>
          Vernieuwen
        </button>
      )}
      {kind === "certificate" && r.provider === "other" && (
        <button className="btn btn-ghost small" onClick={() => open({ kind, action: "upload", row: r, initial: {} })}>
          Nieuw bestand
        </button>
      )}
      {toggles && (
        <button
          className="btn btn-ghost small"
          onClick={() => open({ kind, action: r.enabled === false ? "enable" : "disable", row: r, initial: {} })}
        >
          {r.enabled === false ? "Aanzetten" : "Uitzetten"}
        </button>
      )}
      <button
        className="btn btn-ghost small btn-danger-text"
        onClick={() => open({ kind, action: "delete", row: r, initial: {} })}
      >
        Verwijderen
      </button>
    </div>
  );
}

function Domains({ r }: { r: Row }) {
  return (
    <div className="stack">
      {(r.domain_names ?? []).map((d) => (
        <span key={d} className="mono small">
          {d}
        </span>
      ))}
    </div>
  );
}

function KindTable({
  kind,
  ov,
  write,
  open,
}: {
  kind: Kind;
  ov: Overview;
  write: boolean;
  open: (s: EditorState) => void;
}) {
  const rows = ov[kind];
  if (!rows.length) return <Empty>Nog niets in NPM.</Empty>;
  const actions = (r: Row) => <RowActions kind={kind} r={r} write={write} open={open} />;
  let head: ReactNode;
  let body: (r: Row) => ReactNode;
  if (kind === "proxy_host") {
    head = ["Domeinen", "Doel", "TLS", "Toegang", "Opties", ""];
    body = (r) => (
      <>
        <td>
          <Domains r={r} />
          <div className="row wrap small">
            <span className="muted">#{r.id}</span>
            <StatusBadges r={r} />
          </div>
        </td>
        <td className="mono small">
          {String(r.forward_scheme)}://{String(r.forward_host)}:{String(r.forward_port)}
          {Array.isArray(r.locations) && r.locations.length > 0 && (
            <div className="muted">+ {r.locations.length} custom location(s)</div>
          )}
        </td>
        <td>
          <SslCell r={r} ov={ov} />
        </td>
        <td className="small">
          {r.access_list_id ? (
            (ov.access_list.find((a) => a.id === r.access_list_id)?.label ?? `#${String(r.access_list_id)}`)
          ) : (
            <span className="muted">openbaar</span>
          )}
        </td>
        <td>
          <div className="row wrap">
            {r.allow_websocket_upgrade ? <Badge>websockets</Badge> : null}
            {r.block_exploits ? <Badge>exploits</Badge> : null}
            {r.caching_enabled ? <Badge>cache</Badge> : null}
            {r.advanced_config ? <Badge tone="info">Advanced</Badge> : null}
          </div>
        </td>
        <td>{actions(r)}</td>
      </>
    );
  } else if (kind === "redirection_host") {
    head = ["Domeinen", "Doel", "TLS", ""];
    body = (r) => (
      <>
        <td>
          <Domains r={r} />
          <div className="row wrap small">
            <StatusBadges r={r} />
          </div>
        </td>
        <td className="small">
          <span className="mono">
            {String(r.forward_http_code)} → {r.forward_scheme === "auto" ? "" : `${String(r.forward_scheme)}://`}
            {String(r.forward_domain_name)}
          </span>
          {r.preserve_path ? <div className="muted">pad behouden</div> : null}
        </td>
        <td>
          <SslCell r={r} ov={ov} />
        </td>
        <td>{actions(r)}</td>
      </>
    );
  } else if (kind === "dead_host") {
    head = ["Domeinen", "TLS", ""];
    body = (r) => (
      <>
        <td>
          <Domains r={r} />
          <div className="row wrap small">
            <StatusBadges r={r} />
          </div>
        </td>
        <td>
          <SslCell r={r} ov={ov} />
        </td>
        <td>{actions(r)}</td>
      </>
    );
  } else if (kind === "stream") {
    head = ["Inkomend", "Doel", "Protocol", ""];
    body = (r) => (
      <>
        <td className="mono">
          :{String(r.incoming_port)}
          <div className="row wrap small">
            <StatusBadges r={r} />
          </div>
        </td>
        <td className="mono small">
          {String(r.forwarding_host)}:{String(r.forwarding_port)}
        </td>
        <td>
          {r.tcp_forwarding ? <Badge>TCP</Badge> : null}
          {r.udp_forwarding ? <Badge>UDP</Badge> : null}
        </td>
        <td>{actions(r)}</td>
      </>
    );
  } else if (kind === "access_list") {
    head = ["Naam", "Gebruikers", "Adressen", "In gebruik door", ""];
    body = (r) => (
      <>
        <td>
          <strong>{r.label}</strong>
          <div className="row wrap small">
            {r.satisfy_any ? <Badge tone="warn">Satisfy Any</Badge> : <Badge>Satisfy All</Badge>}
            {r.pass_auth ? <Badge>auth doorgeven</Badge> : null}
          </div>
        </td>
        <td className="small">{((r.items as Data[]) ?? []).map((i) => String(i.username)).join(", ") || "—"}</td>
        <td className="small mono">
          {((r.clients as Data[]) ?? []).map((cl) => `${String(cl.directive)} ${String(cl.address)}`).join(", ") ||
            "—"}
        </td>
        <td className="small">{(r.used_by ?? []).join(", ") || <span className="muted">niets</span>}</td>
        <td>{actions(r)}</td>
      </>
    );
  } else {
    head = ["Naam", "Domeinen", "Soort", "Vervalt", "In gebruik door", ""];
    body = (r) => (
      <>
        <td>
          <strong>{r.label}</strong>
        </td>
        <td>
          <Domains r={r} />
        </td>
        <td>{r.provider === "letsencrypt" ? "Let's Encrypt" : "eigen certificaat"}</td>
        <td className="small">
          <Expiry value={r.expires_on as string | undefined} />
        </td>
        <td className="small">{(r.used_by ?? []).join(", ") || <span className="muted">niets</span>}</td>
        <td>{actions(r)}</td>
      </>
    );
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {(head as string[]).map((h, i) => (
              <th key={i}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.enabled === false ? "dim" : undefined}>
              {body(r)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Expiry({ value }: { value?: string }) {
  if (!value) return <span className="muted">—</span>;
  const days = Math.round((new Date(value.replace(" ", "T") + "Z").getTime() - Date.now()) / 86_400_000);
  const tone = days < 0 ? "bad" : days < 21 ? "warn" : "good";
  return (
    <>
      {value.slice(0, 10)}{" "}
      <Badge tone={tone}>{days < 0 ? "vervallen" : `nog ${days} dagen`}</Badge>
    </>
  );
}

// ---------------------------------------------------------------- dialoog: formulier → voorbeeld → resultaat

function ManageDialog({
  mbase,
  state,
  ov,
  onClose,
  onDone,
}: {
  mbase: string;
  state: EditorState;
  ov: Overview;
  onClose: () => void;
  onDone: () => void;
}) {
  const { kind, action, row } = state;
  const needsForm = action === "create" || action === "update" || action === "upload";
  const [data, setData] = useState<Data>(() => formData(kind, action, state.initial));
  const [step, setStep] = useState<"form" | "preview">(needsForm ? "form" : "preview");
  const [confirmRemovals, setConfirmRemovals] = useState(false);
  const [verify, setVerify] = useState(true);
  const request = () => ({
    action,
    npm_id: row?.id ?? null,
    data: needsForm ? cleanData(kind, action, data) : {},
    confirm_removals: confirmRemovals,
  });
  const plan = useQuery({
    queryKey: ["npm-manage-plan", kind, action, row?.id, JSON.stringify(request())],
    queryFn: () => api.post<Plan>(`${mbase}/${kind}/preview`, request()),
    enabled: step === "preview",
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const apply = useMutation({
    mutationFn: (p: Plan) =>
      api.post<ManageChange>(`${mbase}/${kind}/apply`, {
        ...request(),
        expected_modified_on: p.modified_on,
        verify: verify && p.probe_url !== null,
      }),
    onSettled: onDone,
  });
  const info = kindInfo(kind);
  const title =
    state.title ??
    (action === "create" ? info.nieuw : `${manageActionLabels[action]}: ${row?.label ?? info.one}`);

  let body: ReactNode;
  if (apply.data) {
    body = (
      <>
        <ManageResult change={apply.data} />
        <div className="form-actions">
          <button className="btn" onClick={onClose}>
            Sluiten
          </button>
        </div>
      </>
    );
  } else if (step === "form") {
    body = (
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          setStep("preview");
        }}
      >
        <KindForm kind={kind} action={action} data={data} set={setData} ov={ov} row={row} mbase={mbase} />
        <div className="form-actions row">
          <button type="button" className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button type="submit" className="btn btn-primary">
            Voorbeeld bekijken
          </button>
        </div>
      </form>
    );
  } else if (plan.isLoading) {
    body = <Loading />;
  } else if (!plan.data) {
    body = (
      <>
        <ErrorBox error={plan.error} />
        <div className="form-actions row">
          {needsForm && (
            <button className="btn" onClick={() => setStep("form")}>
              Terug naar formulier
            </button>
          )}
          <button className="btn" onClick={onClose}>
            Sluiten
          </button>
        </div>
      </>
    );
  } else {
    const p = plan.data;
    const canVerify = p.probe_url !== null;
    const removals = p.checks.some((c) => c.code.startsWith("removes_"));
    body = (
      <div className="form">
        {p.checks.length > 0 && (
          <ul className="checks">
            {p.checks.map((c, i) => (
              <li key={`${c.code}-${i}`} className={`check-${c.level}`} title={c.code}>
                {c.message}
              </li>
            ))}
          </ul>
        )}
        {removals && (
          <label className="check warn-box">
            <input type="checkbox" checked={confirmRemovals} onChange={(e) => setConfirmRemovals(e.target.checked)} />
            <span>Beschermde regels mogen weg: ik haal bewust Authentik, het CSS-thema of headers uit Advanced.</span>
          </label>
        )}
        {p.steps.length > 0 && (
          <div>
            <strong>Wat VaultX in NPM doet</strong>
            <ol className="steps">
              {p.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
          </div>
        )}
        {p.certificate_check && (
          <dl className="kv">
            <dt>Certificaat</dt>
            <dd>{p.certificate_check.cn}</dd>
            <dt>Uitgever</dt>
            <dd>{p.certificate_check.issuer}</dd>
            <dt>Geldig tot</dt>
            <dd>{p.certificate_check.valid_to}</dd>
          </dl>
        )}
        {p.changes.length > 0 && <ChangesTable changes={p.changes} />}
        {p.can_apply && p.expect && (
          <label className="check">
            <input
              type="checkbox"
              checked={verify && canVerify}
              disabled={!canVerify}
              onChange={(e) => setVerify(e.target.checked)}
            />
            <span>
              Controleren (verwacht: {p.expect}) en bij een fout terugzetten{" "}
              {canVerify ? (
                <span className="muted small mono">{p.probe_url}</span>
              ) : (
                <span className="muted small">(niet mogelijk voor deze host)</span>
              )}
            </span>
          </label>
        )}
        <ErrorBox error={apply.error} />
        <div className="form-actions row">
          {needsForm && (
            <button className="btn" onClick={() => setStep("form")} disabled={apply.isPending}>
              Terug naar formulier
            </button>
          )}
          <button className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button
            className={action === "delete" || action === "disable" ? "btn btn-danger" : "btn btn-primary"}
            disabled={!p.can_apply || apply.isPending}
            onClick={() => apply.mutate(p)}
          >
            {apply.isPending ? "Bezig, even geduld…" : action === "update" ? "Opslaan" : manageActionLabels[action]}
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

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "aan" : "uit";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

function ChangesTable({ changes }: { changes: Plan["changes"] }) {
  return (
    <div className="table-wrap">
      <table className="changes-table">
        <thead>
          <tr>
            <th>Veld</th>
            <th>Nu in NPM</th>
            <th>Na de wijziging</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c) => (
            <tr key={c.field}>
              <td>{FIELD_LABELS[c.field] ?? c.field}</td>
              <td>
                <pre className="cell-pre">{show(c.before)}</pre>
              </td>
              <td>
                <pre className="cell-pre">{show(c.after)}</pre>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ManageResult({ change }: { change: ManageChange }) {
  const box = change.status === "applied" ? "good-box" : change.status === "refused" ? "info-box" : "warn-box";
  const probe = (label: string, p: ProbeResult | null) =>
    p && (
      <>
        <dt>{label}</dt>
        <dd>
          <span>{p.summary}</span>
          <span className="muted small mono">{p.url}</span>
        </dd>
      </>
    );
  return (
    <div className="form">
      <div className={change.status === "rollback_failed" ? "error-box" : box}>
        <ChangeStatusBadge status={change.status} /> {change.message}
      </div>
      <dl className="kv">
        {probe("Controle vooraf", change.probe_before)}
        {probe("Controle achteraf", change.probe_after)}
        {change.nginx_error && (
          <>
            <dt>nginx-fout</dt>
            <dd className="mono small">{change.nginx_error}</dd>
          </>
        )}
      </dl>
    </div>
  );
}

// ---------------------------------------------------------------- formulieren

const lines = (v: unknown) =>
  String(v ?? "")
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

function formData(kind: Kind, action: Action, initial: Data): Data {
  const d: Data = { ...initial };
  if (kind === "access_list") {
    d.items = ((initial.items as Data[]) ?? []).map((i) => ({ username: i.username, password: "" }));
    d.clients = ((initial.clients as Data[]) ?? []).map((c) => ({ address: c.address, directive: c.directive }));
  }
  if (kind === "proxy_host")
    d.locations = ((initial.locations as Data[]) ?? []).map((l) => ({
      id: l.id,
      path: l.path,
      forward_scheme: l.forward_scheme ?? "http",
      forward_host: l.forward_host ?? "",
      forward_port: l.forward_port ?? 80,
      forward_path: l.forward_path ?? "",
      advanced_config: l.advanced_config ?? "",
    }));
  if (Array.isArray(initial.domain_names)) d.domain_names_text = (initial.domain_names as string[]).join("\n");
  if (action === "create" && Object.keys(initial).length === 0) {
    if (kind === "proxy_host")
      Object.assign(d, { forward_scheme: "http", block_exploits: true, allow_websocket_upgrade: true });
    if (kind === "redirection_host")
      Object.assign(d, { forward_scheme: "auto", forward_http_code: 301, preserve_path: true, block_exploits: true });
    if (kind === "stream") Object.assign(d, { tcp_forwarding: true });
    if (kind === "certificate") Object.assign(d, { provider: "letsencrypt", key_type: "rsa" });
  }
  return d;
}

const FIELDS: Record<Kind, string[]> = {
  proxy_host: [
    "forward_scheme",
    "forward_host",
    "forward_port",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "trust_forwarded_proto",
    "http2_support",
    "block_exploits",
    "caching_enabled",
    "allow_websocket_upgrade",
    "access_list_id",
    "advanced_config",
    "locations",
  ],
  redirection_host: [
    "forward_scheme",
    "forward_http_code",
    "forward_domain_name",
    "preserve_path",
    "certificate_id",
    "ssl_forced",
    "hsts_enabled",
    "hsts_subdomains",
    "http2_support",
    "block_exploits",
    "advanced_config",
  ],
  dead_host: ["certificate_id", "ssl_forced", "hsts_enabled", "hsts_subdomains", "http2_support", "advanced_config"],
  stream: ["incoming_port", "forwarding_host", "forwarding_port", "tcp_forwarding", "udp_forwarding"],
  access_list: ["name", "satisfy_any", "pass_auth", "items", "clients"],
  certificate: [
    "provider",
    "nice_name",
    "dns_challenge",
    "dns_provider",
    "dns_provider_credentials",
    "propagation_seconds",
    "key_type",
    "certificate",
    "certificate_key",
    "intermediate_certificate",
  ],
};
const NUMBERS = new Set([
  "forward_port",
  "forward_http_code",
  "incoming_port",
  "forwarding_port",
  "certificate_id",
  "access_list_id",
  "propagation_seconds",
]);

function cleanData(kind: Kind, action: Action, d: Data): Data {
  const out: Data = {};
  for (const f of FIELDS[kind]) {
    if (!(f in d) || d[f] === undefined) continue;
    let v = d[f];
    if (NUMBERS.has(f)) {
      if (v === "" || v === null) {
        if (f === "certificate_id" || f === "access_list_id") v = 0;
        else continue;
      } else v = Number(v);
    }
    if (f === "locations")
      v = (v as Data[]).map((l) => {
        const loc: Data = {
          path: l.path,
          forward_scheme: l.forward_scheme,
          forward_host: l.forward_host,
          forward_port: Number(l.forward_port),
          advanced_config: l.advanced_config ?? "",
        };
        if (l.id) loc.id = l.id;
        if (l.forward_path) loc.forward_path = l.forward_path;
        return loc;
      });
    if (f === "items") v = (v as Data[]).filter((i) => String(i.username ?? "").trim());
    if (f === "clients") v = (v as Data[]).filter((c) => String(c.address ?? "").trim());
    out[f] = v;
  }
  if (kind !== "stream" && kind !== "access_list" && "domain_names_text" in d)
    out.domain_names = lines(d.domain_names_text);
  if (kind === "certificate") {
    if (action === "upload") {
      for (const k of Object.keys(out))
        if (!["certificate", "certificate_key", "intermediate_certificate"].includes(k)) delete out[k];
    } else if (out.provider === "letsencrypt") {
      delete out.certificate;
      delete out.certificate_key;
      delete out.intermediate_certificate;
      if (!out.dns_challenge) {
        delete out.dns_provider;
        delete out.dns_provider_credentials;
        delete out.propagation_seconds;
      }
    } else {
      delete out.domain_names;
      delete out.dns_challenge;
      delete out.dns_provider;
      delete out.dns_provider_credentials;
      delete out.propagation_seconds;
      delete out.key_type;
    }
  }
  return out;
}

type Setter = (fn: (d: Data) => Data) => void;

function Text({
  label,
  name,
  data,
  set,
  placeholder,
  type = "text",
  hint,
  required,
}: {
  label: string;
  name: string;
  data: Data;
  set: Setter;
  placeholder?: string;
  type?: string;
  hint?: ReactNode;
  required?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type={type}
        value={String(data[name] ?? "")}
        placeholder={placeholder}
        required={required}
        onChange={(e) => {
          const value = e.target.value;
          set((d) => ({ ...d, [name]: value }));
        }}
      />
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

function Area({
  label,
  name,
  data,
  set,
  rows = 4,
  hint,
  mono = true,
  placeholder,
}: {
  label: string;
  name: string;
  data: Data;
  set: Setter;
  rows?: number;
  hint?: ReactNode;
  mono?: boolean;
  placeholder?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea
        className={mono ? "mono" : undefined}
        rows={rows}
        value={String(data[name] ?? "")}
        placeholder={placeholder}
        spellCheck={false}
        onChange={(e) => {
          const value = e.target.value;
          set((d) => ({ ...d, [name]: value }));
        }}
      />
      {hint && <small className="muted">{hint}</small>}
    </label>
  );
}

function Toggle({ label, name, data, set, hint }: { label: string; name: string; data: Data; set: Setter; hint?: string }) {
  return (
    <label className="check" title={hint}>
      <input
        type="checkbox"
        checked={!!data[name]}
        onChange={(e) => {
          const value = e.target.checked;
          set((d) => ({ ...d, [name]: value }));
        }}
      />
      <span>{label}</span>
    </label>
  );
}

function Select({
  label,
  name,
  data,
  set,
  options,
}: {
  label: string;
  name: string;
  data: Data;
  set: Setter;
  options: [string | number, string][];
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={String(data[name] ?? options[0]?.[0] ?? "")}
        onChange={(e) => {
          const value = e.target.value;
          set((d) => ({ ...d, [name]: value }));
        }}
      >
        {options.map(([v, l]) => (
          <option key={String(v)} value={String(v)}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function SslFields({ data, set, ov }: { data: Data; set: Setter; ov: Overview }) {
  const certs: [number, string][] = [[0, "Geen certificaat (enkel http)"]];
  for (const c of ov.certificate) certs.push([c.id, `${c.label} (${(c.domain_names ?? []).join(", ")})`]);
  return (
    <fieldset className="fieldset">
      <legend>TLS</legend>
      <Select label="Certificaat" name="certificate_id" data={data} set={set} options={certs} />
      <small className="muted">
        Nog geen certificaat? Vraag er een aan bij <em>Certificaten</em> en kies het daarna hier.
      </small>
      <div className="row wrap">
        <Toggle label="Force SSL" name="ssl_forced" data={data} set={set} />
        <Toggle label="HTTP/2" name="http2_support" data={data} set={set} />
        <Toggle label="HSTS" name="hsts_enabled" data={data} set={set} />
        <Toggle label="HSTS ook voor subdomeinen" name="hsts_subdomains" data={data} set={set} />
      </div>
    </fieldset>
  );
}

function AdvancedField({ data, set, row }: { data: Data; set: Setter; row?: Row }) {
  const blocks = row?.managed_blocks ?? [];
  return (
    <Area
      label="Advanced (eigen nginx-config)"
      name="advanced_config"
      data={data}
      set={set}
      rows={10}
      hint={
        <>
          {blocks.length > 0 && (
            <>
              Blokken tussen <code># &gt;&gt;&gt; vaultx:…</code> en <code># &lt;&lt;&lt; vaultx:…</code> (
              {blocks.join(", ")}) beheert VaultX zelf; laat ze staan.{" "}
            </>
          )}
          Regels met Authentik, <code>sub_filter</code> of <code>add_header</code> weghalen kan enkel met een extra
          bevestiging.
        </>
      }
    />
  );
}

function Locations({ data, set }: { data: Data; set: Setter }) {
  const locs = (data.locations as Data[]) ?? [];
  const update = (i: number, patch: Data) =>
    set((d) => ({ ...d, locations: (d.locations as Data[]).map((l, j) => (i === j ? { ...l, ...patch } : l)) }));
  return (
    <fieldset className="fieldset">
      <legend>Custom locations</legend>
      {locs.map((l, i) => (
        <div key={i} className="location-edit">
          <div className="grid-location">
            <input
              value={String(l.path ?? "")}
              placeholder="/pad"
              onChange={(e) => update(i, { path: e.target.value })}
            />
            <select value={String(l.forward_scheme)} onChange={(e) => update(i, { forward_scheme: e.target.value })}>
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
            <input
              value={String(l.forward_host ?? "")}
              placeholder="host"
              onChange={(e) => update(i, { forward_host: e.target.value })}
            />
            <input
              type="number"
              value={String(l.forward_port ?? "")}
              onChange={(e) => update(i, { forward_port: e.target.value })}
            />
            <button
              type="button"
              className="btn btn-ghost small btn-danger-text"
              onClick={() => set((d) => ({ ...d, locations: (d.locations as Data[]).filter((_, j) => j !== i) }))}
            >
              Weg
            </button>
          </div>
          <textarea
            className="mono"
            rows={3}
            spellCheck={false}
            placeholder="Eigen config voor deze location"
            value={String(l.advanced_config ?? "")}
            onChange={(e) => update(i, { advanced_config: e.target.value })}
          />
        </div>
      ))}
      <button
        type="button"
        className="btn btn-ghost small"
        onClick={() =>
          set((d) => ({
            ...d,
            locations: [
              ...((d.locations as Data[]) ?? []),
              { path: "", forward_scheme: "http", forward_host: "", forward_port: 80, advanced_config: "" },
            ],
          }))
        }
      >
        + Location toevoegen
      </button>
    </fieldset>
  );
}

function PemFile({ label, name, set }: { label: string; name: string; set: Setter }) {
  return (
    <label className="small muted">
      {label}:{" "}
      <input
        type="file"
        accept=".pem,.crt,.cer,.key,text/plain"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void f.text().then((t) => set((d) => ({ ...d, [name]: t })));
        }}
      />
    </label>
  );
}

function KindForm({
  kind,
  action,
  data,
  set,
  ov,
  row,
  mbase,
}: {
  kind: Kind;
  action: Action;
  data: Data;
  set: Setter;
  ov: Overview;
  row?: Row;
  mbase: string;
}) {
  const domains = (
    <Area
      label="Domeinnamen"
      name="domain_names_text"
      data={data}
      set={set}
      rows={2}
      placeholder="app.jbogaert.be"
      hint="Eén per regel (of gescheiden door komma's)."
    />
  );
  if (kind === "proxy_host") {
    const lists: [number, string][] = [[0, "Openbaar (geen access list)"]];
    for (const a of ov.access_list) lists.push([a.id, a.label]);
    return (
      <>
        {domains}
        <div className="grid-upstream grid-form">
          <Select
            label="Schema"
            name="forward_scheme"
            data={data}
            set={set}
            options={[
              ["http", "http"],
              ["https", "https"],
            ]}
          />
          <Text label="Doelhost of IP" name="forward_host" data={data} set={set} placeholder="192.168.10.20" />
          <Text label="Poort" name="forward_port" data={data} set={set} type="number" placeholder="8080" />
        </div>
        <div className="row wrap">
          <Toggle label="Websockets" name="allow_websocket_upgrade" data={data} set={set} />
          <Toggle label="Block common exploits" name="block_exploits" data={data} set={set} />
          <Toggle label="Cache assets" name="caching_enabled" data={data} set={set} />
          <Toggle
            label="X-Forwarded-Proto vertrouwen"
            name="trust_forwarded_proto"
            data={data}
            set={set}
            hint="Voor een NPM achter een andere proxy"
          />
        </div>
        <Select label="Access list" name="access_list_id" data={data} set={set} options={lists} />
        <SslFields data={data} set={set} ov={ov} />
        <Locations data={data} set={set} />
        <AdvancedField data={data} set={set} row={row} />
      </>
    );
  }
  if (kind === "redirection_host")
    return (
      <>
        {domains}
        <div className="grid-form">
          <Select
            label="HTTP-code"
            name="forward_http_code"
            data={data}
            set={set}
            options={[
              [301, "301 Permanent"],
              [302, "302 Tijdelijk"],
              [307, "307 Tijdelijk (methode behouden)"],
              [308, "308 Permanent (methode behouden)"],
              [300, "300 Multiple choices"],
              [303, "303 See other"],
            ]}
          />
          <Select
            label="Schema"
            name="forward_scheme"
            data={data}
            set={set}
            options={[
              ["auto", "zoals de bezoeker"],
              ["https", "https"],
              ["http", "http"],
            ]}
          />
        </div>
        <Text label="Doeldomein" name="forward_domain_name" data={data} set={set} placeholder="nieuw.jbogaert.be" />
        <div className="row wrap">
          <Toggle label="Pad behouden" name="preserve_path" data={data} set={set} />
          <Toggle label="Block common exploits" name="block_exploits" data={data} set={set} />
        </div>
        <SslFields data={data} set={set} ov={ov} />
        <AdvancedField data={data} set={set} row={row} />
      </>
    );
  if (kind === "dead_host")
    return (
      <>
        {domains}
        <SslFields data={data} set={set} ov={ov} />
        <AdvancedField data={data} set={set} row={row} />
      </>
    );
  if (kind === "stream")
    return (
      <>
        <div className="grid-form">
          <Text label="Inkomende poort" name="incoming_port" data={data} set={set} type="number" placeholder="2222" />
          <Text label="Doelpoort" name="forwarding_port" data={data} set={set} type="number" placeholder="22" />
        </div>
        <Text label="Doelhost of IP" name="forwarding_host" data={data} set={set} placeholder="192.168.10.30" />
        <div className="row wrap">
          <Toggle label="TCP" name="tcp_forwarding" data={data} set={set} />
          <Toggle label="UDP" name="udp_forwarding" data={data} set={set} />
        </div>
        <small className="muted">De inkomende poort moet ook open staan op de NPM-container (ports in compose).</small>
      </>
    );
  if (kind === "access_list") return <AccessListForm data={data} set={set} editing={action === "update"} />;
  return <CertificateForm action={action} data={data} set={set} mbase={mbase} />;
}

function AccessListForm({ data, set, editing }: { data: Data; set: Setter; editing: boolean }) {
  const items = (data.items as Data[]) ?? [];
  const clients = (data.clients as Data[]) ?? [];
  const setItem = (i: number, patch: Data) =>
    set((d) => ({ ...d, items: (d.items as Data[]).map((x, j) => (i === j ? { ...x, ...patch } : x)) }));
  const setClient = (i: number, patch: Data) =>
    set((d) => ({ ...d, clients: (d.clients as Data[]).map((x, j) => (i === j ? { ...x, ...patch } : x)) }));
  return (
    <>
      <Text label="Naam" name="name" data={data} set={set} placeholder="LAN" />
      <div className="row wrap">
        <Toggle
          label="Satisfy Any"
          name="satisfy_any"
          data={data}
          set={set}
          hint="Binnen met een geldig adres óf een geldige login (anders: allebei nodig)"
        />
        <Toggle label="Auth doorgeven aan de app" name="pass_auth" data={data} set={set} />
      </div>
      <fieldset className="fieldset">
        <legend>Gebruikers (basic auth)</legend>
        {items.map((it, i) => (
          <div key={i} className="row">
            <input
              value={String(it.username ?? "")}
              placeholder="gebruikersnaam"
              onChange={(e) => setItem(i, { username: e.target.value })}
            />
            <input
              type="password"
              autoComplete="new-password"
              value={String(it.password ?? "")}
              placeholder={editing ? "leeg = ongewijzigd" : "wachtwoord"}
              onChange={(e) => setItem(i, { password: e.target.value })}
            />
            <button
              type="button"
              className="btn btn-ghost small btn-danger-text"
              onClick={() => set((d) => ({ ...d, items: (d.items as Data[]).filter((_, j) => j !== i) }))}
            >
              Weg
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn btn-ghost small"
          onClick={() => set((d) => ({ ...d, items: [...((d.items as Data[]) ?? []), { username: "", password: "" }] }))}
        >
          + Gebruiker
        </button>
      </fieldset>
      <fieldset className="fieldset">
        <legend>Adressen</legend>
        {clients.map((cl, i) => (
          <div key={i} className="row">
            <select value={String(cl.directive ?? "allow")} onChange={(e) => setClient(i, { directive: e.target.value })}>
              <option value="allow">toestaan</option>
              <option value="deny">weigeren</option>
            </select>
            <input
              value={String(cl.address ?? "")}
              placeholder="192.168.10.0/24 of all"
              onChange={(e) => setClient(i, { address: e.target.value })}
            />
            <button
              type="button"
              className="btn btn-ghost small btn-danger-text"
              onClick={() => set((d) => ({ ...d, clients: (d.clients as Data[]).filter((_, j) => j !== i) }))}
            >
              Weg
            </button>
          </div>
        ))}
        <button
          type="button"
          className="btn btn-ghost small"
          onClick={() =>
            set((d) => ({ ...d, clients: [...((d.clients as Data[]) ?? []), { address: "", directive: "allow" }] }))
          }
        >
          + Adres
        </button>
      </fieldset>
    </>
  );
}

function CertificateForm({ action, data, set, mbase }: { action: Action; data: Data; set: Setter; mbase: string }) {
  const dns = useQuery({
    queryKey: ["npm-dns-providers", mbase],
    queryFn: () => api.get<DnsProvider[]>(`${mbase}/dns-providers`),
    enabled: action === "create" && data.provider === "letsencrypt" && !!data.dns_challenge,
    staleTime: 3_600_000,
  });
  const pem = (
    <>
      <Area label="Certificaat (PEM)" name="certificate" data={data} set={set} rows={4} placeholder="-----BEGIN CERTIFICATE-----" />
      <PemFile label="of kies een bestand" name="certificate" set={set} />
      <Area label="Private key (PEM)" name="certificate_key" data={data} set={set} rows={4} placeholder="-----BEGIN PRIVATE KEY-----" />
      <PemFile label="of kies een bestand" name="certificate_key" set={set} />
      <Area label="Tussencertificaten (optioneel)" name="intermediate_certificate" data={data} set={set} rows={3} />
      <small className="muted">De private key gaat rechtstreeks naar NPM; VaultX bewaart hem niet en zet hem niet in het journaal.</small>
    </>
  );
  if (action === "upload") return pem;
  const providers: [string, string][] = (dns.data ?? []).map((p) => [p.id, p.name ?? p.id]);
  return (
    <>
      <Select
        label="Soort"
        name="provider"
        data={data}
        set={set}
        options={[
          ["letsencrypt", "Let's Encrypt aanvragen"],
          ["other", "Eigen certificaat opladen"],
        ]}
      />
      {data.provider === "letsencrypt" ? (
        <>
          <Area
            label="Domeinnamen"
            name="domain_names_text"
            data={data}
            set={set}
            rows={2}
            placeholder="app.jbogaert.be"
            hint="Eén per regel. Een wildcard (*.jbogaert.be) kan enkel met een DNS-challenge."
          />
          <Text label="Naam (optioneel)" name="nice_name" data={data} set={set} />
          <Select
            label="Sleuteltype"
            name="key_type"
            data={data}
            set={set}
            options={[
              ["rsa", "RSA"],
              ["ecdsa", "ECDSA"],
            ]}
          />
          <Toggle label="DNS-challenge gebruiken" name="dns_challenge" data={data} set={set} />
          {!!data.dns_challenge && (
            <>
              {dns.isLoading ? (
                <Loading />
              ) : (
                <label className="field">
                  <span>DNS-provider</span>
                  <select
                    value={String(data.dns_provider ?? "")}
                    onChange={(e) => {
                      const id = e.target.value;
                      const tpl = dns.data?.find((p) => p.id === id)?.credentials ?? "";
                      set((d) => ({ ...d, dns_provider: id, dns_provider_credentials: d.dns_provider_credentials || tpl }));
                    }}
                  >
                    <option value="">Kies…</option>
                    {providers.map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <Area
                label="Gegevens voor de DNS-provider"
                name="dns_provider_credentials"
                data={data}
                set={set}
                rows={4}
                hint="Gaat rechtstreeks naar NPM; VaultX zet ze niet in het journaal."
              />
              <Text label="Propagatietijd (seconden, optioneel)" name="propagation_seconds" data={data} set={set} type="number" />
            </>
          )}
        </>
      ) : (
        <>
          <Text label="Naam" name="nice_name" data={data} set={set} placeholder="jbogaert.be wildcard" />
          {pem}
        </>
      )}
    </>
  );
}

// ---------------------------------------------------------------- journaal

function ManageJournal({
  mbase,
  connId,
  onRestore,
}: {
  mbase: string;
  connId: string;
  onRestore?: (c: ManageChange) => void;
}) {
  const changes = useQuery({
    queryKey: ["npm-manage-changes", connId],
    queryFn: () => api.get<ManageChange[]>(`${mbase}/changes`),
  });
  const [open, setOpen] = useState<ManageChange | null>(null);
  if (changes.isLoading) return <Loading />;
  if (changes.error) return <ErrorBox error={changes.error} />;
  if (!changes.data?.length) return <Empty>VaultX heeft nog niets gewijzigd in deze NPM.</Empty>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Wanneer</th>
            <th>Object</th>
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
              <td className="small">
                <div className="muted">{kindInfo(c.object_type)?.one ?? c.object_type}</div>
                <span className="mono">{c.domain}</span>
              </td>
              <td>
                {manageActionLabels[c.action] ?? c.action}
                {!c.verified && (
                  <div>
                    <Badge tone="warn">zonder controle</Badge>
                  </div>
                )}
              </td>
              <td>
                <ChangeStatusBadge status={c.status} />
                <div className="small">{c.message}</div>
                <div className="row wrap">
                  <button className="btn btn-ghost small" onClick={() => setOpen(c)}>
                    Details
                  </button>
                  {onRestore &&
                    c.action === "delete" &&
                    c.status === "applied" &&
                    c.object_type !== "certificate" && (
                      <button className="btn btn-ghost small" onClick={() => onRestore(c)}>
                        Opnieuw aanmaken
                      </button>
                    )}
                </div>
              </td>
              <td className="small">{c.actor_label ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Modal
        title={open ? `${manageActionLabels[open.action] ?? open.action}: ${open.domain}` : ""}
        open={open !== null}
        onClose={() => setOpen(null)}
        wide
      >
        {open && (
          <div className="form">
            <ManageResult change={open} />
            {open.after?.changes && open.after.changes.length > 0 && <ChangesTable changes={open.after.changes} />}
            {Object.keys(open.before).length > 0 && (
              <details open={open.status === "rollback_failed"}>
                <summary>Volledige config ervoor</summary>
                <pre className="cell-pre">{JSON.stringify(open.before, null, 2)}</pre>
              </details>
            )}
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
