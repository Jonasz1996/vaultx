import { useSearchParams } from "react-router";

const errors: Record<string, string> = {
  invalid_state: "De login is verlopen of ongeldig. Probeer opnieuw.",
  idp_error: "Authentik heeft de login geweigerd.",
  idp_unreachable: "Authentik is momenteel niet bereikbaar.",
  token_invalid: "Het antwoord van Authentik kon niet geverifieerd worden.",
  account_disabled: "Je account is gedeactiveerd in VaultX. Contacteer een beheerder.",
};

export function LoginPage() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const next = params.get("next") ?? "/";
  return (
    <div className="login">
      <div className="login-card">
        <img src="/favicon.svg" alt="" width={48} height={48} />
        <h1>VaultX</h1>
        <p className="muted">Beheeromgeving. Je logt in met je Authentik-account.</p>
        {params.get("logged_out") && <div className="info-box">Je bent uitgelogd.</div>}
        {error && <div className="error-box">{errors[error] ?? `Login mislukt (${error}).`}</div>}
        <a className="btn btn-primary btn-wide" href={`/auth/login?next=${encodeURIComponent(next)}`}>
          Inloggen met Authentik
        </a>
      </div>
    </div>
  );
}
