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
  /** Eerst stil bij Authentik proberen (prompt=none): wie daar aangemeld is, zit meteen binnen. */
  silent?: boolean;
  /** Zonder Authentik-sessie meteen naar het aanmelden bij Authentik, zonder knop. */
  auto_redirect?: boolean;
}

// Hoogstens twee automatische pogingen per minuut, ook als er onderweg iets misloopt (bv. een
// sessiecookie die de browser niet bewaart): nooit een lus tussen VaultX en Authentik.
const AUTO_KEY = "vaultx.autoLogin";
const AUTO_WINDOW_MS = 60_000;
const AUTO_MAX = 2;

function autoAllowed(): boolean {
  try {
    const now = Date.now();
    const recent = (JSON.parse(sessionStorage.getItem(AUTO_KEY) ?? "[]") as number[]).filter(
      (t) => typeof t === "number" && now - t < AUTO_WINDOW_MS,
    );
    if (recent.length >= AUTO_MAX) return false;
    sessionStorage.setItem(AUTO_KEY, JSON.stringify([...recent, now]));
  } catch {
    // Geen sessionStorage: toch proberen, de server stuurt hoogstens één keer door.
  }
  return true;
}

/** Wachtwoord klopt, nu nog de beveiligingssleutel (of de herstelcode). */
interface SecondStep {
  second_step: "security_key";
  ticket: string;
  security_key_options: Record<string, unknown> | null;
}

/** Tijdelijk wachtwoord (accountherstel): eerst een eigen wachtwoord kiezen. */
interface PasswordChange {
  password_change: "required";
  ticket: string;
  temporary_until: string;
}

/** Waarde veilig tussen enkele aanhalingstekens voor een shell (bash). */
function shellQuote(value: string): string {
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

/** Wat de beheerder op de VaultX-server uitvoert (Debian-container, als root). */
export function resetCommand(login: string): string {
  return (
    "set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend && " +
    `/opt/vaultx/venv/bin/python -m app.cli reset-account ${shellQuote(login.trim())}`
  );
}

/**
 * "Kan je niet inloggen?": toont enkel wat de beheerder moet uitvoeren. Er gaat niets naar de
 * server, dus de tekst is altijd dezelfde en verraadt niet of een account bestaat.
 */
function CannotLogIn({ initial, onBack }: { initial: string; onBack: () => void }) {
  const [email, setEmail] = useState(initial.includes("@") ? initial : "");
  const [shown, setShown] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const command = shown ? resetCommand(shown) : "";
  return (
    <>
      <p className="muted">Wachtwoord vergeten, of je beveiligingssleutel kwijt?</p>
      {!shown ? (
        <form
          className="form login-form"
          onSubmit={(e) => {
            e.preventDefault();
            setShown(email.trim());
          }}
        >
          <label className="field">
            <span>Je inlog-e-mailadres</span>
            <input
              required
              type="email"
              autoComplete="email"
              autoFocus
              maxLength={320}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button className="btn btn-primary btn-wide">Verder</button>
          <button type="button" className="btn btn-ghost btn-wide" onClick={onBack}>
            Terug naar inloggen
          </button>
        </form>
      ) : (
        <div className="login-form recovery">
          <p>
            Vraag aan je systeembeheerder om je account te resetten door dit commando uit te voeren op de VaultX-server
            (als root):
          </p>
          <pre className="recovery-command" data-testid="reset-command">
            {command}
          </pre>
          <button
            type="button"
            className="btn btn-wide"
            onClick={() => {
              void navigator.clipboard?.writeText(command).then(() => setCopied(true));
            }}
          >
            {copied ? "Gekopieerd" : "Commando kopiëren"}
          </button>
          <p>
            Je krijgt dan een <strong>tijdelijk wachtwoord</strong> waarmee je de toegang tot je account kan herstellen.
            Het werkt één uur. Bij de eerste aanmelding kies je meteen een nieuw wachtwoord; daarna stel je je
            tweestapsverificatie (beveiligingssleutel, authenticator-app) opnieuw in.
          </p>
          <p className="muted small">
            Je kluis blijft versleuteld met je master password; dat kan niemand voor je resetten. Log je in via
            Authentik? Dan regelt Authentik je wachtwoord en MFA.
          </p>
          <button type="button" className="btn btn-primary btn-wide" onClick={onBack}>
            Terug naar inloggen
          </button>
        </div>
      )}
    </>
  );
}

export function LoginPage() {
  const [params] = useSearchParams();
  const error = params.get("error");
  const next = params.get("next") ?? "/";
  const [methods, setMethods] = useState<Methods | null>(null);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [loginError, setLoginError] = useState<unknown>(null);
  const [step, setStep] = useState<SecondStep | null>(null);
  const [recovery, setRecovery] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [change, setChange] = useState<PasswordChange | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [help, setHelp] = useState(params.has("herstel"));
  // ?lokaal=1: altijd de gewone loginpagina, nooit doorsturen (noodtoegang als Authentik plat ligt).
  const local = params.has("lokaal");

  useEffect(() => {
    api
      .get<Methods>("/auth/methods")
      .then((m) => {
        const quiet = local || error || params.get("logged_out") || params.get("stil") || params.has("herstel");
        if (!quiet && (m.silent || m.auto_redirect) && autoAllowed()) {
          setRedirecting(true);
          const query = new URLSearchParams({ next });
          if (m.silent) query.set("silent", "1");
          window.location.replace(`/auth/login?${query}`);
          return;
        }
        setMethods(m);
      })
      .catch(() => setMethods({ authentik: true, password: false }));
  }, []);

  if (!methods) {
    return (
      <div className="login">
        <div className="login-card">
          <img src="/favicon.svg" alt="" width={48} height={48} />
          <h1>VaultX</h1>
          <p className="muted" role="status">
            {redirecting ? "Aanmelden via Authentik…" : "Laden…"}
          </p>
          {redirecting && (
            <a className="btn btn-ghost btn-wide" href={`/login?lokaal=1&next=${encodeURIComponent(next)}`}>
              Toch zelf inloggen
            </a>
          )}
        </div>
      </div>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setPending(true);
    setLoginError(null);
    try {
      const res = await api.post<{ redirect_url: string } | SecondStep | PasswordChange>("/auth/password", {
        login,
        password,
        next,
      });
      if ("redirect_url" in res) {
        window.location.assign(res.redirect_url);
        return;
      }
      setPassword("");
      if ("password_change" in res) {
        setChange(res);
        setPending(false);
        return;
      }
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

  const submitChange = async (e: FormEvent) => {
    e.preventDefault();
    if (!change) return;
    if (newPassword !== newPassword2) {
      setLoginError(new Error("De wachtwoorden zijn niet gelijk"));
      return;
    }
    setPending(true);
    setLoginError(null);
    try {
      const res = await api.post<{ redirect_url: string }>("/auth/password/change-temporary", {
        ticket: change.ticket,
        new_password: newPassword,
      });
      window.location.assign(res.redirect_url);
    } catch (err) {
      setLoginError(err);
      setPending(false);
      // Verlopen: terug naar het tijdelijke wachtwoord.
      if ((err as { status?: number })?.status === 401) setChange(null);
    }
  };

  const withKey = async (s: SecondStep) => {
    if (!s.security_key_options) return;
    setLoginError(null);
    try {
      await finish(s, {
        security_key: await getAssertion(s.security_key_options),
      });
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
        {local && methods.authentik && (methods.silent || methods.auto_redirect) && (
          <div className="info-box small">Noodtoegang: VaultX stuurt je hier niet automatisch naar Authentik.</div>
        )}
        {error && <div className="error-box">{errors[error] ?? `Login mislukt (${error}).`}</div>}
        {help && <CannotLogIn initial={login} onBack={() => setHelp(false)} />}
        {change && !help && (
          <form className="form login-form" onSubmit={submitChange}>
            <div className="info-box small">
              Je logt in met een tijdelijk wachtwoord. Kies nu je eigen, nieuwe wachtwoord.
            </div>
            <label className="field">
              <span>Nieuw wachtwoord</span>
              <input
                required
                autoFocus
                type="password"
                autoComplete="new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Nogmaals</span>
              <input
                required
                type="password"
                autoComplete="new-password"
                value={newPassword2}
                onChange={(e) => setNewPassword2(e.target.value)}
              />
            </label>
            <ErrorBox error={loginError} />
            <button className="btn btn-primary btn-wide" disabled={pending}>
              {pending ? "Bezig…" : "Wachtwoord instellen en inloggen"}
            </button>
          </form>
        )}
        {step && !help && (
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
                    Je beveiligingssleutel werkt enkel op de domeinnaam van VaultX, niet op dit adres. Log daar in, of
                    gebruik je herstelcode (die zet de sleutels voor je login uit).
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
        {methods.password && !step && !change && !help && (
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
              <button
                type="button"
                className="btn btn-ghost btn-wide small"
                onClick={() => (setHelp(true), setLoginError(null))}
              >
                Wachtwoord vergeten of kan je niet inloggen?
              </button>
            </form>
            {methods.authentik && (
              <div className="login-divider">
                <span>of</span>
              </div>
            )}
          </>
        )}
        {methods.authentik && !step && !change && !help && (
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
