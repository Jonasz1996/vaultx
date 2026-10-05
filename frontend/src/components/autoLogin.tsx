import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type {
  AppLogin,
  AppLoginConfig,
  AppLoginInput,
  AppLoginPlan,
  AppLoginState,
  Application,
  GrafanaAdmin,
  LoginTemplateKey,
} from "../api/types";
import { formatDate, relative } from "../format";
import { AccessPicker } from "./npmProtection";
import { Badge, Card, Empty, ErrorBox, Loading, Modal } from "./ui";

const checkInfo: Record<string, { label: string; tone: "good" | "bad" | "neutral" }> = {
  ok: { label: "Werkt", tone: "good" },
  failed: { label: "Werkt nog niet", tone: "bad" },
  unknown: { label: "Niet te controleren", tone: "neutral" },
};

function accessText(login: AppLogin): string {
  if (login.access === "all") return "alle Authentik-gebruikers";
  return login.groups.join(", ") || login.access;
}

function GrafanaAdminFields({
  value,
  onChange,
  hint,
}: {
  value: GrafanaAdmin;
  onChange: (v: GrafanaAdmin) => void;
  hint: string;
}) {
  return (
    <div className="form">
      <label className="field">
        <span>Adres van Grafana voor VaultX</span>
        <input value={value.url} onChange={(e) => onChange({ ...value, url: e.target.value })} placeholder="http://10.0.0.5:3000" />
        <small className="muted">Het interne adres, niet via Authentik forward auth (zoals NPM Grafana bereikt).</small>
      </label>
      <div className="row wrap">
        <label className="field">
          <span>Grafana-beheerder</span>
          <input value={value.username} autoComplete="off" onChange={(e) => onChange({ ...value, username: e.target.value })} />
        </label>
        <label className="field">
          <span>Wachtwoord</span>
          <input
            type="password"
            autoComplete="new-password"
            value={value.password}
            onChange={(e) => onChange({ ...value, password: e.target.value })}
          />
        </label>
      </div>
      <small className="muted">{hint}</small>
    </div>
  );
}

function PlanView({ plan }: { plan: AppLoginPlan }) {
  return (
    <>
      {plan.checks.length > 0 && (
        <ul className="checks">
          {plan.checks.map((c) => (
            <li key={c.code} className={`check-${c.level}`} title={c.code}>
              {c.message}
            </li>
          ))}
        </ul>
      )}
      {plan.steps.length > 0 && (
        <div>
          <strong>Wat VaultX doet</strong>
          <ol className="steps">
            {plan.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
        </div>
      )}
    </>
  );
}

function SetupDialog({
  orgId,
  base,
  state,
  onClose,
  onDone,
}: {
  orgId: string;
  base: string;
  state: AppLoginState;
  onClose: () => void;
  onDone: (login: AppLogin) => void;
}) {
  const [template, setTemplate] = useState<LoginTemplateKey>(state.templates[0]?.key ?? "oidc");
  const [appUrl, setAppUrl] = useState(state.suggested_app_url ?? "");
  const [access, setAccess] = useState("organization");
  const [role, setRole] = useState<AppLoginInput["default_role"]>("Viewer");
  const [redirects, setRedirects] = useState("");
  const [pushGrafana, setPushGrafana] = useState(true);
  const [admin, setAdmin] = useState<GrafanaAdmin>({
    url: state.suggested_grafana_url ?? state.suggested_app_url ?? "",
    username: "admin",
    password: "",
  });
  const tpl = state.templates.find((t) => t.key === template);
  const input = (): AppLoginInput => ({
    template,
    access,
    app_url: appUrl.trim() || null,
    redirect_uris: redirects.split("\n").map((r) => r.trim()).filter(Boolean),
    default_role: role,
    grafana: template === "grafana" && pushGrafana ? admin : null,
  });
  const preview = useMutation({ mutationFn: () => api.post<AppLoginPlan>(`${base}/preview`, input()) });
  const apply = useMutation({
    mutationFn: () => api.post<AppLogin>(base, input()),
    onSuccess: onDone,
  });
  const reset = () => {
    preview.reset();
    apply.reset();
  };
  const set = <T,>(fn: (v: T) => void) => (v: T) => {
    fn(v);
    reset();
  };
  const plan = preview.data;

  return (
    <Modal title="Automatische login inrichten" open onClose={onClose} wide>
      <div className="form">
        <div className="info-box small">
          De app meldt zich zelf aan via Authentik (OpenID Connect). Wie al bij Authentik is aangemeld, komt meteen
          binnen, zonder tweede login en zonder wachtwoord voor de app.
        </div>
        <label className="field">
          <span>Sjabloon</span>
          <select value={template} onChange={(e) => set(setTemplate)(e.target.value as LoginTemplateKey)}>
            {state.templates.map((t) => (
              <option key={t.key} value={t.key}>
                {t.label}
              </option>
            ))}
          </select>
          {tpl && <small className="muted">{tpl.description}</small>}
        </label>
        <label className="field">
          <span>URL van de app</span>
          <input value={appUrl} onChange={(e) => set(setAppUrl)(e.target.value)} placeholder="https://grafana.example.be" />
          {tpl?.redirect_path && appUrl && (
            <small className="muted">
              Redirect URI in Authentik: <code>{appUrl.replace(/\/+$/, "") + tpl.redirect_path}</code>
            </small>
          )}
        </label>
        {template === "oidc" && (
          <label className="field">
            <span>Redirect URI's van de app (één per regel)</span>
            <textarea rows={3} value={redirects} onChange={(e) => set(setRedirects)(e.target.value)} />
            <small className="muted">Staat in de documentatie van de app, bv. https://portainer.example.be/</small>
          </label>
        )}
        <AccessPicker orgId={orgId} value={access} onChange={set(setAccess)} label="Wie mag zich via Authentik aanmelden bij de app?" />
        {template === "grafana" && (
          <>
            <label className="field">
              <span>Rol in Grafana</span>
              <select value={role} onChange={(e) => set(setRole)(e.target.value as AppLoginInput["default_role"])}>
                <option value="Viewer">Viewer</option>
                <option value="Editor">Editor</option>
                <option value="Admin">Admin</option>
              </select>
              <small className="muted">
                Eigenaars en beheerders van de organisatie (en VaultX-beheerders) worden altijd Admin in Grafana.
              </small>
            </label>
            <label className="check">
              <input type="checkbox" checked={pushGrafana} onChange={(e) => set(setPushGrafana)(e.target.checked)} />
              <span>De login meteen in Grafana zetten (Grafana 11 of nieuwer, zonder herstart)</span>
            </label>
            {pushGrafana ? (
              <GrafanaAdminFields
                value={admin}
                onChange={set(setAdmin)}
                hint="VaultX gebruikt dit account enkel voor deze actie en bewaart het niet. Een serviceaccount volstaat niet: de SSO-instellingen vragen een Grafana-serverbeheerder."
              />
            ) : (
              <small className="muted">
                Dan toont VaultX na het inrichten de grafana.ini en de omgevingsvariabelen om zelf in te vullen.
              </small>
            )}
          </>
        )}
        {plan && <PlanView plan={plan} />}
        <ErrorBox error={preview.error ?? apply.error} />
        <div className="form-actions row">
          <button className="btn" onClick={onClose}>
            Annuleren
          </button>
          {!plan ? (
            <button className="btn btn-primary" disabled={preview.isPending} onClick={() => preview.mutate()}>
              {preview.isPending ? "Bezig…" : "Voorbeeld bekijken"}
            </button>
          ) : (
            <button className="btn btn-primary" disabled={!plan.can_apply || apply.isPending} onClick={() => apply.mutate()}>
              {apply.isPending ? "Bezig, even geduld…" : "Inrichten"}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}

function ConfigDialog({ base, onClose }: { base: string; onClose: () => void }) {
  const cfg = useQuery({
    queryKey: ["app-login-config", base],
    queryFn: () => api.get<AppLoginConfig>(`${base}/config`),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  return (
    <Modal title="Config voor de app" open onClose={onClose} wide>
      {cfg.isLoading ? (
        <Loading />
      ) : !cfg.data ? (
        <ErrorBox error={cfg.error} />
      ) : (
        <div className="form">
          <div className="warn-box small">
            Bevat het client secret. Het opvragen staat in de auditlog; zet het secret niet in Git.
          </div>
          {cfg.data.files.map((f) => (
            <div key={f.name}>
              <div className="row">
                <strong className="mono">{f.name}</strong>
                <button className="btn btn-ghost small" onClick={() => void navigator.clipboard?.writeText(f.content)}>
                  Kopiëren
                </button>
              </div>
              <pre className="snippet">{f.content}</pre>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

function RemoveDialog({
  base,
  login,
  suggestedUrl,
  onClose,
  onDone,
}: {
  base: string;
  login: AppLogin;
  suggestedUrl: string;
  onClose: () => void;
  onDone: (rest: AppLogin | null) => void;
}) {
  const [admin, setAdmin] = useState<GrafanaAdmin>({ url: suggestedUrl, username: "admin", password: "" });
  const remove = useMutation({
    mutationFn: () => api.post<AppLogin | null>(`${base}/remove`, { grafana: login.app_configured ? admin : null }),
    onSuccess: onDone,
  });
  return (
    <Modal title="Automatische login weghalen" open onClose={onClose}>
      <div className="form">
        <p>
          VaultX verwijdert in Authentik de applicatie <code>{login.application_slug}</code> en provider '
          {login.provider_name}'.
          {login.app_configured && " In Grafana zet VaultX de Authentik-login weer uit, zodat lokaal aanmelden terug werkt."}
        </p>
        {login.app_configured && (
          <GrafanaAdminFields value={admin} onChange={setAdmin} hint="Wordt niet bewaard." />
        )}
        <ErrorBox error={remove.error} />
        <div className="form-actions row">
          <button className="btn" onClick={onClose}>
            Annuleren
          </button>
          <button className="btn btn-danger" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending ? "Bezig…" : "Weghalen"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function AutoLoginCard({ orgId, app, manage }: { orgId: string; app: Application; manage: boolean }) {
  const base = `/api/v1/organizations/${orgId}/applications/${app.id}/login`;
  const qc = useQueryClient();
  const state = useQuery({ queryKey: ["app-login", app.id], queryFn: () => api.get<AppLoginState>(base) });
  const [dialog, setDialog] = useState<"setup" | "config" | "remove" | null>(null);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["app-login", app.id] });
    void qc.invalidateQueries({ queryKey: ["app", app.id] });
    void qc.invalidateQueries({ queryKey: ["catalog"] });
    void qc.invalidateQueries({ queryKey: ["audit", "app", app.id] });
  };
  const check = useMutation({ mutationFn: () => api.post<AppLogin>(`${base}/check`), onSettled: refresh });

  let body;
  if (state.isLoading) body = <Loading />;
  else if (!state.data) body = <ErrorBox error={state.error} />;
  else if (!state.data.login) {
    body = !state.data.configured ? (
      <Empty>
        De Authentik-API is niet ingesteld op de VaultX-server (VAULTX_AUTHENTIK_API_TOKEN). Zie docs/npm.md, sectie 6.
      </Empty>
    ) : (
      <div className="form">
        <p className="muted">
          Nog niet ingericht. Laat {app.name} zelf via Authentik aanmelden: wie al bij Authentik is aangemeld, komt
          meteen binnen.
        </p>
        {manage && (
          <div className="row">
            <button className="btn btn-primary" onClick={() => setDialog("setup")}>
              Automatische login inrichten
            </button>
          </div>
        )}
      </div>
    );
  } else {
    const l = state.data.login;
    const c = l.last_check_status ? checkInfo[l.last_check_status] : null;
    body = (
      <div className="form">
        {l.cleanup_error && (
          <div className="error-box">
            Opruimen in Authentik mislukte: {l.cleanup_error}. Probeer opnieuw of verwijder het met de hand.
          </div>
        )}
        <dl className="kv">
          <dt>Sjabloon</dt>
          <dd>
            {state.data.templates.find((t) => t.key === l.template)?.label ?? l.template}
            {l.template === "grafana" && l.options.default_role && (
              <span className="muted small">standaardrol {l.options.default_role}</span>
            )}
          </dd>
          <dt>App</dt>
          <dd className="mono small">{l.app_url}</dd>
          <dt>Authentik</dt>
          <dd>
            <span>
              provider '{l.provider_name}', applicatie <code>{l.application_slug}</code>
            </span>
            <span className="muted small mono">client {l.client_id}</span>
          </dd>
          <dt>Aanmelden mag</dt>
          <dd>{accessText(l)}</dd>
          {l.template === "grafana" && (
            <>
              <dt>In Grafana</dt>
              <dd>
                {l.app_configured ? (
                  <Badge tone="good">door VaultX gezet</Badge>
                ) : (
                  <span className="muted">zelf in te vullen (zie config)</span>
                )}
              </dd>
            </>
          )}
          {c && (
            <>
              <dt>Controle</dt>
              <dd>
                <Badge tone={c.tone}>{c.label}</Badge>
                <span className="small">{l.last_check_message}</span>
                {l.last_check_at && (
                  <span className="muted small" title={formatDate(l.last_check_at)}>
                    {relative(l.last_check_at)}
                  </span>
                )}
              </dd>
            </>
          )}
        </dl>
        <ErrorBox error={check.error} />
        {manage && (
          <div className="row wrap">
            {l.template === "grafana" && (
              <button className="btn" disabled={check.isPending} onClick={() => check.mutate()}>
                {check.isPending ? "Bezig…" : "Opnieuw controleren"}
              </button>
            )}
            <button className="btn" onClick={() => setDialog("config")}>
              Config tonen
            </button>
            <button className="btn btn-danger" onClick={() => setDialog("remove")}>
              Weghalen
            </button>
          </div>
        )}
      </div>
    );
  }

  return (
    <Card title="Automatische login">
      {body}
      {dialog === "setup" && state.data && (
        <SetupDialog
          orgId={orgId}
          base={base}
          state={state.data}
          onClose={() => setDialog(null)}
          onDone={(login) => {
            refresh();
            setDialog(!login.app_configured && login.template !== "grafana" ? "config" : null);
          }}
        />
      )}
      {dialog === "config" && <ConfigDialog base={base} onClose={() => { setDialog(null); refresh(); }} />}
      {dialog === "remove" && state.data?.login && (
        <RemoveDialog
          base={base}
          login={state.data.login}
          suggestedUrl={state.data.suggested_grafana_url ?? state.data.login.app_url}
          onClose={() => setDialog(null)}
          onDone={() => {
            refresh();
            setDialog(null);
          }}
        />
      )}
    </Card>
  );
}
