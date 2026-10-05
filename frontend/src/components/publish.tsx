// Fase 6: een nieuwe app in één stap online zetten via NPM (host, certificaat, Authentik, catalogus),
// en een app die VaultX publiceerde weer offline halen.
import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api } from "../api/client";
import type {
  DiscoveredHost,
  NpmCertificate,
  NpmChange,
  NpmConnection,
  PublishPlan,
  PublishRequest,
  UnpublishPlan,
} from "../api/types";
import { AccessPicker, ChangeResult, renderConfig } from "./npmProtection";
import { ErrorBox, Loading, Modal } from "./ui";

function themeFor(template: string | null, name: string): string {
  if (!template) return "";
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "app";
  return template.replaceAll("{app}", slug);
}

function covers(cert: NpmCertificate, domain: string): boolean {
  const d = domain.trim().toLowerCase();
  return cert.domain_names.some((n) => {
    const name = n.toLowerCase();
    if (name === d) return true;
    return name.startsWith("*.") && d.endsWith(name.slice(1)) && !d.slice(0, -name.length + 1).includes(".");
  });
}

function Checks({ checks }: { checks: PublishPlan["checks"] }) {
  if (!checks.length) return null;
  return (
    <ul className="checks">
      {checks.map((c) => (
        <li key={c.code} className={`check-${c.level}`} title={c.code}>
          {c.message}
        </li>
      ))}
    </ul>
  );
}

function Steps({ title, steps }: { title: string; steps: string[] }) {
  if (!steps.length) return null;
  return (
    <div>
      <strong>{title}</strong>
      <ol className="steps">
        {steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
    </div>
  );
}

export function PublishDialog({
  base,
  orgId,
  conn,
  onClose,
  onDone,
}: {
  base: string;
  orgId: string;
  conn: NpmConnection;
  onClose: () => void;
  onDone: () => void;
}) {
  const certs = useQuery({
    queryKey: ["npm-certs", base],
    queryFn: () => api.get<NpmCertificate[]>(`${base}/certificates`),
    staleTime: 30_000,
  });
  const [form, setForm] = useState<PublishRequest>({
    name: "",
    domain: "",
    forward_scheme: "http",
    forward_host: "",
    forward_port: 80,
    certificate_id: 0,
    ssl_forced: true,
    websockets: true,
    block_exploits: true,
    security_headers: true,
    theme_css_url: null,
    description: null,
    protect: true,
    access: "organization",
  });
  // Zolang de beheerder het thema niet zelf aanpaste, volgt het de naam van de app.
  const [themeTouched, setThemeTouched] = useState(false);
  const [certTouched, setCertTouched] = useState(false);
  const [verify, setVerify] = useState(true);
  const set = <K extends keyof PublishRequest>(k: K, v: PublishRequest[K]) => setForm((f) => ({ ...f, [k]: v }));

  const theme = themeTouched ? (form.theme_css_url ?? "") : themeFor(conn.theme_css_template, form.name);
  const autoCert =
    !certTouched && form.domain.includes(".")
      ? ((certs.data ?? []).find((c) => covers(c, form.domain))?.id ?? 0)
      : form.certificate_id;
  const request: PublishRequest = {
    ...form,
    certificate_id: autoCert,
    theme_css_url: theme.trim() || null,
    description: form.description?.trim() || null,
  };

  const preview = useMutation({
    mutationFn: (r: PublishRequest) => api.post<PublishPlan>(`${base}/publish/preview`, r),
  });
  const apply = useMutation({
    mutationFn: (r: PublishRequest) =>
      api.post<NpmChange>(`${base}/publish`, { ...r, verify: verify && preview.data?.probe_url !== null }),
    onSettled: onDone,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    preview.mutate(request);
  };

  if (apply.data) {
    return (
      <Modal title={`App publiceren: ${apply.data.domain}`} open onClose={onClose} wide>
        <ChangeResult change={apply.data} />
        <div className="form-actions">
          <button className="btn" onClick={onClose}>
            Sluiten
          </button>
        </div>
      </Modal>
    );
  }

  const plan = preview.data;
  if (plan) {
    const canVerify = plan.probe_url !== null;
    return (
      <Modal title={`App publiceren: ${plan.domain}`} open onClose={onClose} wide>
        <div className="form">
          <Checks checks={plan.checks} />
          <Steps
            title={plan.authentik ? "Wat VaultX in Authentik, NPM en de catalogus doet" : "Wat VaultX doet"}
            steps={plan.steps}
          />
          {plan.can_apply && (
            <details>
              <summary>Config van de nieuwe host bekijken</summary>
              <pre>{renderConfig(plan.host)}</pre>
            </details>
          )}
          {plan.can_apply && (
            <label className="check">
              <input
                type="checkbox"
                checked={verify && canVerify}
                disabled={!canVerify}
                onChange={(e) => setVerify(e.target.checked)}
              />
              <span>
                Controleren en bij een fout alles terugdraaien{" "}
                {canVerify && <span className="muted small mono">{plan.probe_url}</span>}
              </span>
            </label>
          )}
          <ErrorBox error={apply.error} />
          <div className="form-actions row">
            <button className="btn" onClick={() => preview.reset()} disabled={apply.isPending}>
              Terug
            </button>
            <button
              className="btn btn-primary"
              disabled={!plan.can_apply || apply.isPending}
              onClick={() => apply.mutate(request)}
            >
              {apply.isPending ? "Bezig, even geduld…" : "Publiceren"}
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="App publiceren" open onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <p className="muted small">
          VaultX maakt in NPM een nieuwe proxy host aan, beschermt hem met Authentik en zet de app in de
          catalogus. Je ziet eerst een voorbeeld.
        </p>
        <div className="grid-form">
          <label className="field">
            <span>Naam</span>
            <input required placeholder="Proxmox VE" value={form.name} onChange={(e) => set("name", e.target.value)} />
          </label>
          <label className="field">
            <span>Domein</span>
            <input
              required
              placeholder="pve.example.be"
              value={form.domain}
              onChange={(e) => set("domain", e.target.value.trim())}
            />
          </label>
        </div>
        <div className="grid-form grid-upstream">
          <label className="field">
            <span>Schema</span>
            <select
              value={form.forward_scheme}
              onChange={(e) => set("forward_scheme", e.target.value as "http" | "https")}
            >
              <option value="http">http</option>
              <option value="https">https</option>
            </select>
          </label>
          <label className="field">
            <span>App (host of IP)</span>
            <input
              required
              placeholder="192.168.0.10"
              value={form.forward_host}
              onChange={(e) => set("forward_host", e.target.value.trim())}
            />
          </label>
          <label className="field">
            <span>Poort</span>
            <input
              required
              type="number"
              min={1}
              max={65535}
              value={form.forward_port}
              onChange={(e) => set("forward_port", Number(e.target.value))}
            />
          </label>
        </div>
        <label className="field">
          <span>Certificaat</span>
          <select
            value={autoCert}
            onChange={(e) => {
              setCertTouched(true);
              set("certificate_id", Number(e.target.value));
            }}
          >
            <option value={0}>Geen (enkel http)</option>
            {(certs.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.nice_name || c.domain_names.join(", ")}
                {form.domain && covers(c, form.domain) ? " ✓" : ""}
                {c.expires_on ? ` (tot ${c.expires_on.slice(0, 10)})` : ""}
              </option>
            ))}
          </select>
          <small className="muted">
            {certs.isLoading
              ? "Certificaten ophalen uit NPM…"
              : certs.error
                ? "Certificaten niet op te halen (heeft het NPM-account 'Certificates: View'?)."
                : "Een certificaat dat al in NPM staat, bv. een wildcard. VaultX kiest er een dat het domein dekt."}
          </small>
        </label>
        {autoCert > 0 && (
          <label className="check">
            <input type="checkbox" checked={form.ssl_forced} onChange={(e) => set("ssl_forced", e.target.checked)} />
            TLS afdwingen (http naar https) en HSTS
          </label>
        )}
        <label className="field">
          <span>CSS-thema</span>
          <input
            placeholder="https://css.example.be/app.css"
            value={theme}
            onChange={(e) => {
              setThemeTouched(true);
              set("theme_css_url", e.target.value);
            }}
          />
          <small className="muted">Optioneel: een stylesheet die in elke pagina van de app komt (sub_filter).</small>
        </label>
        <label className="field">
          <span>Omschrijving</span>
          <input
            placeholder="Optioneel, voor de catalogus"
            value={form.description ?? ""}
            onChange={(e) => set("description", e.target.value)}
          />
        </label>
        <fieldset className="form-group">
          <legend>Toegang</legend>
          <label className="check">
            <input type="checkbox" checked={form.protect} onChange={(e) => set("protect", e.target.checked)} />
            Beschermen met Authentik
          </label>
          {form.protect && conn.authentik_outpost_pk && (
            <AccessPicker orgId={orgId} value={form.access} onChange={(v) => set("access", v)} />
          )}
          {form.protect && !conn.authentik_outpost_pk && (
            <small className="muted">
              Op deze koppeling staat geen outpost: VaultX maakt in Authentik niets aan. Er moet al een provider
              zijn die dit domein dekt.
            </small>
          )}
        </fieldset>
        <details>
          <summary>Meer opties</summary>
          <div className="stack">
            <label className="check">
              <input
                type="checkbox"
                checked={form.security_headers}
                onChange={(e) => set("security_headers", e.target.checked)}
              />
              Beveiligingsheaders (X-Frame-Options, nosniff, Referrer-Policy, frame-ancestors)
            </label>
            <label className="check">
              <input type="checkbox" checked={form.websockets} onChange={(e) => set("websockets", e.target.checked)} />
              Websockets
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.block_exploits}
                onChange={(e) => set("block_exploits", e.target.checked)}
              />
              Block Common Exploits
            </label>
          </div>
        </details>
        <ErrorBox error={preview.error} />
        <div className="form-actions row">
          <button type="button" className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button className="btn btn-primary" disabled={preview.isPending}>
            {preview.isPending ? "Nakijken…" : "Voorbeeld"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function UnpublishDialog({
  base,
  host,
  onClose,
  onDone,
}: {
  base: string;
  host: DiscoveredHost;
  onClose: () => void;
  onDone: () => void;
}) {
  const url = `${base}/hosts/${host.id}/unpublish`;
  const plan = useQuery({
    queryKey: ["npm-unpublish", host.id],
    queryFn: () => api.get<UnpublishPlan>(url),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const apply = useMutation({ mutationFn: () => api.post<NpmChange>(url, {}), onSettled: onDone });
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
    body = <Loading />;
  } else if (!plan.data) {
    body = <ErrorBox error={plan.error} />;
  } else {
    const p = plan.data;
    body = (
      <div className="form">
        <Checks checks={p.checks} />
        <Steps title="Wat VaultX doet" steps={p.steps} />
        <ErrorBox error={apply.error} />
        <div className="form-actions row">
          <button className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button className="btn btn-danger" disabled={!p.can_apply || apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? "Bezig…" : "Depubliceren"}
          </button>
        </div>
      </div>
    );
  }
  return (
    <Modal title={`Depubliceren: ${host.domain_names[0]}`} open onClose={onClose} wide>
      {body}
    </Modal>
  );
}
