import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { ErrorBox } from "../components/ui";
import { getAssertion, webAuthnSupported } from "../webauthn";

const errors: Record<string, string> = {
  invalid_state: "De login is verlopen of ongeldig. Probeer opnieuw.",
  idp_error: "Authentik heeft de login geweigerd.",
  idp_unreachable: "Authentik is momenteel niet bereikbaar.",
  token_invalid: "Het antwoord van Authentik kon niet geverifieerd worden.",
  account_disabled: "Je account is gedeactiveerd in VaultX. Contacteer een beheerder.",
  authentik_not_here: "Inloggen met Authentik kan niet op dit adres. Log in met je VaultX-wachtwoord.",
};

interface Methods {
  authentik: boolean;
  password: boolean;
}

/** Wachtwoord klopt, nu nog de beveiligingssleutel (of de herstelcode). */
interface SecondStep {
  second_step: "security_key";
  ticket: string;
  security_key_options: Record<string, unknown> | null;
}

export function LoginPage() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const next = params.get("next") ?? "/";
  const [methods, setMethods] = useState<Methods>({ authentik: true, password: false });
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [loginError, setLoginError] = useState<unknown>(null);
  const [step, setStep] = useState<SecondStep | null>(null);
  const [recovery, setRecovery] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<Methods>("/auth/methods")
      .then(setMethods)
      .catch(() => undefined);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setPending(true);
    setLoginError(null);
    try {
      const res = await api.post<{ redirect_url: string } | SecondStep>("/auth/password", { login, password, next });
      if ("redirect_url" in res) {
        window.location.assign(res.redirect_url);
        return;
      }
      setPassword("");
      setStep(res);
      setPending(false);
      if (res.security_key_options && webAuthnSupported()) void withKey(res);
      else setRecovery("");
    } catch (err) {
      setLoginError(err);
      setPassword("");
      setPending(false);
    }
  };

  const finish = async (s: SecondStep, answer: Record<string, unknown>) => {
    setPending(true);
    setLoginError(null);
    try {
      const res = await api.post<{ redirect_url: string }>("/auth/password/second-step", {
        ticket: s.ticket,
        next,
        ...answer,
      });
      window.location.assign(res.redirect_url);
    } catch (err) {
      setLoginError(err);
      setPending(false);
      // Ticket verlopen of te veel pogingen: terug naar het wachtwoord.
      if ((err as { status?: number })?.status === 429) setStep(null);
    }
  };

  const withKey = async (s: SecondStep) => {
    if (!s.security_key_options) return;
    setLoginError(null);
    try {
      await finish(s, { security_key: await getAssertion(s.security_key_options) });
    } catch (err) {
      setLoginError(err);
    }
  };

  return (
    <div className="login">
      <div className="login-card">
        <img src="/favicon.svg" alt="" width={48} height={48} />
        <h1>VaultX</h1>
        <p className="muted">
          {methods.password && methods.authentik
            ? "Log in met je VaultX-account of met Authentik."
            : methods.password
              ? "Log in met je VaultX-account."
              : "Beheeromgeving. Je logt in met je Authentik-account."}
        </p>
        {params.get("logged_out") && <div className="info-box">Je bent uitgelogd.</div>}
        {error && <div className="error-box">{errors[error] ?? `Login mislukt (${error}).`}</div>}
        {step && (
          <form
            className="form login-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (recovery !== null) void finish(step, { recovery_code: recovery });
              else void withKey(step);
            }}
          >
            {recovery === null ? (
              <div className="info-box">
                Steek je beveiligingssleutel in en raak hem aan, of gebruik je passkey. Je browser vraagt erom.
              </div>
            ) : (
              <>
                {!step.security_key_options && (
                  <div className="info-box small">
                    Je beveiligingssleutel werkt enkel op de domeinnaam van VaultX, niet op dit adres. Log daar in,
                    of gebruik je herstelcode (die zet de sleutels voor je login uit).
                  </div>
                )}
                <label className="field">
                  <span>Herstelcode</span>
                  <input
                    required
                    autoFocus
                    autoComplete="one-time-code"
                    value={recovery}
                    onChange={(e) => setRecovery(e.target.value)}
                  />
                </label>
              </>
            )}
            <ErrorBox error={loginError} />
            <button className="btn btn-primary btn-wide" disabled={pending || recovery === ""}>
              {pending ? "Bezig…" : recovery === null ? "Beveiligingssleutel gebruiken" : "Inloggen met herstelcode"}
            </button>
            <div className="form-actions">
              {step.security_key_options && webAuthnSupported() && (
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => setRecovery(recovery === null ? "" : null)}
                >
                  {recovery === null ? "Herstelcode gebruiken" : "Toch met mijn sleutel"}
                </button>
              )}
              <button type="button" className="btn btn-ghost" onClick={() => (setStep(null), setLoginError(null))}>
                Terug
              </button>
            </div>
          </form>
        )}
        {methods.password && !step && (
          <>
            <form className="form login-form" onSubmit={submit}>
              <label className="field">
                <span>Gebruikersnaam of e-mail</span>
                <input
                  required
                  autoComplete="username"
                  autoFocus
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                />
              </label>
              <label className="field">
                <span>Wachtwoord</span>
                <input
                  required
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <ErrorBox error={loginError} />
              <button className="btn btn-primary btn-wide" disabled={pending}>
                {pending ? "Bezig…" : "Inloggen"}
              </button>
            </form>
            {methods.authentik && (
              <div className="login-divider">
                <span>of</span>
              </div>
            )}
          </>
        )}
        {methods.authentik && !step && (
          <a
            className={methods.password ? "btn btn-wide" : "btn btn-primary btn-wide"}
            href={`/auth/login?next=${encodeURIComponent(next)}`}
          >
            Inloggen met Authentik
          </a>
        )}
      </div>
    </div>
  );
}
