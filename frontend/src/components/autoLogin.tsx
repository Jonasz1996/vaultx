import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { AppLogin, AppLoginConfig, AppLoginInput, AppLoginPlan, AppLoginState, Application } from "../api/types";
import { AccessPicker } from "./npmProtection";
import { Card, Empty, ErrorBox, Loading, Modal } from "./ui";

function accessText(login: AppLogin): string {
  if (login.access === "all") return "alle Authentik-gebruikers";
  return login.groups.join(", ") || login.access;
}

function PlanView({ plan }: { plan: AppLoginPlan }) {
  return (
    <>
      {plan.checks.length > 0 && (
        <ul className="checks">
          {plan.checks.map((c, i) => (
            <li key={`${c.code}-${i}`} className={`check-${c.level}`} title={c.code}>
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
  const [appUrl, setAppUrl] = useState(state.suggested_app_url ?? "");
  const [access, setAccess] = useState("organization");
  const [redirects, setRedirects] = useState("");
  const input = (): AppLoginInput => ({
    redirect_uris: redirects
      .split("\n")
      .map((r) => r.trim())
      .filter(Boolean),
    access,
    app_url: appUrl.trim() || null,
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
          binnen, zonder tweede login en zonder wachtwoord voor de app. VaultX richt Authentik in; issuer, client ID
          en secret vul je daarna zelf in de app in.
        </div>
        <label className="field">
          <span>URL van de app</span>
          <input value={appUrl} onChange={(e) => set(setAppUrl)(e.target.value)} placeholder="https://app.example.be" />
        </label>
        <label className="field">
          <span>Redirect URI's van de app (één per regel)</span>
          <textarea
            rows={3}
            value={redirects}
            onChange={(e) => set(setRedirects)(e.target.value)}
            placeholder={`${(appUrl.trim() || "https://app.example.be").replace(/\/+$/, "")}/oauth/callback`}
          />
          <small className="muted">
            Staat in de documentatie van de app, bij OpenID Connect of OAuth. Authentik aanvaardt enkel exact deze
            URI's.
          </small>
        </label>
        <AccessPicker orgId={orgId} value={access} onChange={set(setAccess)} label="Wie mag zich via Authentik aanmelden bij de app?" />
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
    <Modal title="Instellingen voor de app" open onClose={onClose} wide>
      {cfg.isLoading ? (
        <Loading />
      ) : !cfg.data ? (
        <ErrorBox error={cfg.error} />
      ) : (
        <div className="form">
          <div className="warn-box small">
            Bevat het client secret. Het opvragen staat in de auditlog; zet het secret niet in Git.
          </div>
          <p className="small">
            Vul dit in bij de OpenID Connect- of OAuth-instellingen van de app. Meestal volstaan de discovery-URL (of
            issuer), client ID en secret. Meld je daarna aan in de app om te testen.
          </p>
          <div>
            <div className="row">
              <strong>Config</strong>
              <button className="btn btn-ghost small" onClick={() => void navigator.clipboard?.writeText(cfg.data.text)}>
                Kopiëren
              </button>
            </div>
            <pre className="snippet">{cfg.data.text}</pre>
          </div>
        </div>
      )}
    </Modal>
  );
}

function RemoveDialog({
  base,
  login,
  onClose,
  onDone,
}: {
  base: string;
  login: AppLogin;
  onClose: () => void;
  onDone: (rest: AppLogin | null) => void;
}) {
  const remove = useMutation({
    mutationFn: () => api.post<AppLogin | null>(`${base}/remove`),
    onSuccess: onDone,
  });
  return (
    <Modal title="Automatische login weghalen" open onClose={onClose}>
      <div className="form">
        <p>
          VaultX verwijdert in Authentik de applicatie <code>{login.application_slug}</code> en provider '
          {login.provider_name}'. Zet de login daarna ook in de app zelf uit: de app kan dan niet meer via Authentik
          aanmelden.
        </p>
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
          meteen binnen. De app moet OpenID Connect kennen.
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
    body = (
      <div className="form">
        {l.cleanup_error && (
          <div className="error-box">
            Opruimen in Authentik mislukte: {l.cleanup_error}. Probeer opnieuw of verwijder het met de hand.
          </div>
        )}
        <dl className="kv">
          <dt>App</dt>
          <dd className="mono small">{l.app_url}</dd>
          <dt>Redirect URI</dt>
          <dd className="mono small">{l.redirect_uris.join(", ")}</dd>
          <dt>Authentik</dt>
          <dd>
            <span>
              provider '{l.provider_name}', applicatie <code>{l.application_slug}</code>
            </span>
            <span className="muted small mono">client {l.client_id}</span>
          </dd>
          <dt>Aanmelden mag</dt>
          <dd>{accessText(l)}</dd>
        </dl>
        <p className="muted small">
          {manage
            ? "De app zelf stelt VaultX niet in: vul de instellingen in de app in (Instellingen tonen) en meld je daarna aan om te testen."
            : "Een beheerder vult de instellingen in de app in. Daarna meld je je aan via Authentik."}
        </p>
        {manage && (
          <div className="row wrap">
            <button className="btn" onClick={() => setDialog("config")}>
              Instellingen tonen
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
          onDone={() => {
            refresh();
            setDialog("config");
          }}
        />
      )}
      {dialog === "config" && (
        <ConfigDialog
          base={base}
          onClose={() => {
            setDialog(null);
            refresh();
          }}
        />
      )}
      {dialog === "remove" && state.data?.login && (
        <RemoveDialog
          base={base}
          login={state.data.login}
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
