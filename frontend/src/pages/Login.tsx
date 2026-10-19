import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { ErrorBox } from "../components/ui";

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

export function LoginPage() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const next = params.get("next") ?? "/";
  const [methods, setMethods] = useState<Methods>({ authentik: true, password: false });
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [loginError, setLoginError] = useState<unknown>(null);

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
      const res = await api.post<{ redirect_url: string }>("/auth/password", { login, password, next });
      window.location.assign(res.redirect_url);
    } catch (err) {
      setLoginError(err);
      setPassword("");
      setPending(false);
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
        {methods.password && (
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
        {methods.authentik && (
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
