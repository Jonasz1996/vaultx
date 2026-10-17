import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Application, AuthentikOutposts, AuthMethod, DetectionWarning, NpmConnection } from "../api/types";
import { authLabels, statusInfo } from "../format";
import { Badge, ErrorBox } from "./ui";

export function StatusBadge({ status }: { status: string }) {
  const s = statusInfo[status] ?? { label: status, tone: "neutral" as const, hint: "" };
  return (
    <Badge tone={s.tone} title={s.hint}>
      {s.label}
    </Badge>
  );
}

export function AuthLabel({ method }: { method: string }) {
  return <span className={method === "unknown" ? "muted" : undefined}>{authLabels[method] ?? method}</span>;
}

export function Warnings({ items }: { items: DetectionWarning[] }) {
  if (!items.length) return <span className="muted">—</span>;
  return (
    <ul className="warnings">
      {items.map((w) => (
        <li key={w.code} title={w.code}>
          {w.message}
        </li>
      ))}
    </ul>
  );
}

export function HostLink({ domain, ssl = true }: { domain: string; ssl?: boolean }) {
  if (domain.startsWith("*")) return <span className="mono small">{domain}</span>;
  return (
    <a className="mono small" href={`${ssl ? "https" : "http"}://${domain}`} target="_blank" rel="noreferrer">
      {domain}
    </a>
  );
}

const AUTH_OPTIONS: AuthMethod[] = ["forward_auth", "oidc", "saml", "header", "access_list", "app", "none", "unknown"];

export interface ApplicationValues {
  name: string;
  app_type: string | null;
  url: string | null;
  description: string | null;
  auth_method: AuthMethod;
  tags: string[];
}

export function ApplicationForm({
  initial,
  onSubmit,
  error,
  pending,
  submitLabel = "Opslaan",
  note,
}: {
  initial?: Partial<Application>;
  onSubmit: (v: ApplicationValues) => void;
  error: unknown;
  pending: boolean;
  submitLabel?: string;
  note?: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [appType, setAppType] = useState(initial?.app_type ?? "");
  const [auth, setAuth] = useState<AuthMethod>(initial?.auth_method ?? "unknown");
  const [tags, setTags] = useState((initial?.tags ?? []).join(", "));
  const [description, setDescription] = useState(initial?.description ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      name,
      url: url.trim() || null,
      app_type: appType.trim() || null,
      auth_method: auth,
      tags: tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      description: description.trim() || null,
    });
  };
  return (
    <form onSubmit={submit} className="form">
      {note && <div className="info-box small">{note}</div>}
      <label className="field">
        <span>Naam</span>
        <input required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>URL</span>
        <input type="url" placeholder="https://grafana.example.be" value={url} onChange={(e) => setUrl(e.target.value)} />
      </label>
      <div className="grid-form">
        <label className="field">
          <span>Aanmelding</span>
          <select value={auth} onChange={(e) => setAuth(e.target.value as AuthMethod)}>
            {AUTH_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {authLabels[a]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Type</span>
          <input placeholder="grafana" value={appType} onChange={(e) => setAppType(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span>Tags</span>
        <input placeholder="monitoring, ops" value={tags} onChange={(e) => setTags(e.target.value)} />
      </label>
      <label className="field">
        <span>Omschrijving</span>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <ErrorBox error={error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={pending}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

export interface ConnectionValues {
  name: string;
  base_url: string;
  identity: string;
  secret?: string;
  verify_tls: boolean;
  enabled: boolean;
  write_enabled: boolean;
  authentik_outpost_url: string;
  probe_host: string;
  probe_http_port: number;
  probe_https_port: number;
  authentik_outpost_pk: string;
  theme_css_template: string;
}

// Fase 4: kies de outpost waarop VaultX zelf providers zet. Leeg = VaultX maakt niets aan in Authentik.
function OutpostPicker({
  orgId,
  value,
  onChange,
}: {
  orgId: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const outposts = useQuery({
    queryKey: ["authentik-outposts", orgId],
    queryFn: () => api.get<AuthentikOutposts>(`/api/v1/organizations/${orgId}/authentik/outposts`),
    enabled: !!orgId,
    staleTime: 60_000,
  });
  const data = outposts.data;
  const known = data?.outposts ?? [];
  const selected = known.find((o) => o.pk === value);
  return (
    <label className="field">
      <span>Authentik-kant automatisch aanmaken</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} disabled={!data?.configured && !value}>
        <option value="">Nee, de provider maak ik zelf in Authentik</option>
        {known.map((o) => (
          <option key={o.pk} value={o.pk}>
            Ja, op outpost {o.name}
          </option>
        ))}
        {value && !selected && <option value={value}>Ja, op outpost {value} (niet gevonden)</option>}
      </select>
      <small className="muted">
        {outposts.isLoading
          ? "Outposts ophalen uit Authentik…"
          : !data?.configured
            ? "De Authentik-API is niet ingesteld op de VaultX-server (VAULTX_AUTHENTIK_API_TOKEN)."
            : data.error
              ? `Authentik: ${data.error}`
              : "VaultX maakt dan per host een proxy provider (forward auth), een applicatie en de groepsbinding " +
                "aan, en zet de provider op deze outpost. Bij weghalen ruimt het die weer op."}
      </small>
      {selected && !selected.authentik_host && (
        <small className="warn-box small">
          Bij deze outpost is <code>authentik_host</code> leeg. Vul in Authentik bij de outpost de publieke URL van
          Authentik in, anders weet de outpost niet waar een browser zich moet aanmelden.
        </small>
      )}
    </label>
  );
}

export function ConnectionForm({
  orgId,
  initial,
  onSubmit,
  error,
  pending,
}: {
  orgId: string;
  initial?: NpmConnection;
  onSubmit: (v: ConnectionValues) => void;
  error: unknown;
  pending: boolean;
}) {
  const editing = !!initial;
  const [name, setName] = useState(initial?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
  const [identity, setIdentity] = useState(initial?.identity ?? "");
  const [secret, setSecret] = useState("");
  const [verifyTls, setVerifyTls] = useState(initial?.verify_tls ?? true);
  const [enabled, setEnabled] = useState(initial?.enabled ?? true);
  const [writeEnabled, setWriteEnabled] = useState(initial?.write_enabled ?? false);
  const [outpost, setOutpost] = useState(initial?.authentik_outpost_url ?? "");
  const [probeHost, setProbeHost] = useState(initial?.probe_host ?? "");
  const [httpPort, setHttpPort] = useState(initial?.probe_http_port ?? 80);
  const [httpsPort, setHttpsPort] = useState(initial?.probe_https_port ?? 443);
  const [outpostPk, setOutpostPk] = useState(initial?.authentik_outpost_pk ?? "");
  const [theme, setTheme] = useState(initial?.theme_css_template ?? "");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSubmit({
      name,
      base_url: baseUrl,
      identity,
      ...(secret ? { secret } : {}),
      verify_tls: verifyTls,
      enabled,
      // Leeg = wissen.
      write_enabled: writeEnabled,
      authentik_outpost_url: outpost.trim(),
      probe_host: probeHost.trim(),
      probe_http_port: httpPort,
      probe_https_port: httpsPort,
      authentik_outpost_pk: outpostPk,
      theme_css_template: theme.trim(),
    });
  };
  return (
    <form onSubmit={submit} className="form">
      <label className="field">
        <span>Naam</span>
        <input required placeholder="Homelab NPM" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Beheer-URL van NPM</span>
        <input
          required
          type="url"
          placeholder="http://npm.lan:81"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
        <small className="muted">De poort van de NPM-beheerinterface (standaard 81), niet de proxypoort.</small>
      </label>
      <label className="field">
        <span>E-mailadres NPM-account</span>
        <input required value={identity} onChange={(e) => setIdentity(e.target.value)} />
      </label>
      <label className="field">
        <span>Wachtwoord</span>
        <input
          type="password"
          required={!editing}
          autoComplete="new-password"
          placeholder={editing ? "Ongewijzigd laten" : ""}
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />
        <small className="muted">
          Wordt versleuteld opgeslagen en nooit teruggegeven. Gebruik een apart NPM-account zonder 2FA. Om te
          lezen volstaat "Proxy Hosts: View"; om Authentik-bescherming te zetten is "Manage" nodig.
        </small>
      </label>
      <label className="check">
        <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
        TLS-certificaat controleren
      </label>
      <label className="check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Automatisch synchroniseren
      </label>
      <fieldset className="form-group">
        <legend>Authentik-bescherming zetten</legend>
        <label className="check">
          <input type="checkbox" checked={writeEnabled} onChange={(e) => setWriteEnabled(e.target.checked)} />
          VaultX mag proxy hosts in deze NPM wijzigen
        </label>
        <small className="muted">
          Enkel op vraag van een beheerder, per host, met eerst een voorbeeld. Mislukt de controle achteraf, dan
          zet VaultX de vorige config terug.
        </small>
        <label className="field">
          <span>Authentik-outpost, gezien vanuit NPM</span>
          <input
            placeholder="http://authentik-server:9000"
            value={outpost}
            onChange={(e) => setOutpost(e.target.value)}
          />
          <small className="muted">
            Het adres waarop nginx in NPM de outpost bereikt, zonder pad. Voor de ingebouwde outpost is dat de
            Authentik-server zelf.
          </small>
        </label>
        <OutpostPicker orgId={orgId} value={outpostPk} onChange={setOutpostPk} />
        <div className="grid-form grid-probe">
          <label className="field">
            <span>Controleadres van NPM</span>
            <input placeholder="host van de beheer-URL" value={probeHost} onChange={(e) => setProbeHost(e.target.value)} />
          </label>
          <label className="field">
            <span>HTTP-poort</span>
            <input
              type="number"
              min={1}
              max={65535}
              value={httpPort}
              onChange={(e) => setHttpPort(Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span>HTTPS-poort</span>
            <input
              type="number"
              min={1}
              max={65535}
              value={httpsPort}
              onChange={(e) => setHttpsPort(Number(e.target.value))}
            />
          </label>
        </div>
        <small className="muted">
          Hier spreekt VaultX een host aan voor en na een wijziging, met de domeinnaam als Host-header. Leeg =
          de host van de beheer-URL.
        </small>
        <label className="field">
          <span>CSS-thema bij publiceren</span>
          <input
            placeholder="https://css.example.be/{app}.css"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          />
          <small className="muted">
            Optioneel. Bij "App publiceren" wordt <code>{"{app}"}</code> de naam van de app (bv. proxmox-ve); het
            thema blijft per app aan te passen.
          </small>
        </label>
      </fieldset>
      <ErrorBox error={error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={pending}>
          {editing ? "Opslaan" : "Koppelen"}
        </button>
      </div>
    </form>
  );
}
