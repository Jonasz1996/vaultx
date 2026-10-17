import { useState, type FormEvent } from "react";
import { ErrorBox } from "./ui";

// Gebruikersnaam zoals de backend hem aanvaardt (USERNAME_PATTERN).
const USERNAME_PATTERN = "[A-Za-z0-9][A-Za-z0-9._@+\\-]{1,149}";
export const MIN_PASSWORD = 12;

/** Willekeurig wachtwoord van 20 tekens, zonder tekens die je makkelijk verwart. */
function generatePassword(): string {
  const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!";
  const bytes = crypto.getRandomValues(new Uint32Array(20));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

/** Nieuw wachtwoord met bevestiging, optioneel genereren en tonen. */
function NewPasswordFields({
  value,
  onChange,
  confirm,
  onConfirm,
  allowGenerate,
}: {
  value: string;
  onChange: (v: string) => void;
  confirm: string;
  onConfirm: (v: string) => void;
  allowGenerate: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <>
      <label className="field">
        <span>Nieuw wachtwoord</span>
        <div className="input-row">
          <input
            required
            type={show ? "text" : "password"}
            minLength={MIN_PASSWORD}
            autoComplete="new-password"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          <button type="button" className="btn" onClick={() => setShow(!show)}>
            {show ? "Verbergen" : "Tonen"}
          </button>
          {allowGenerate && (
            <button
              type="button"
              className="btn"
              onClick={() => {
                const pw = generatePassword();
                onChange(pw);
                onConfirm(pw);
                setShow(true);
              }}
            >
              Genereren
            </button>
          )}
        </div>
        <small className="muted">Minstens {MIN_PASSWORD} tekens. Gebruik niet je master password van de kluis.</small>
      </label>
      <label className="field">
        <span>Nogmaals</span>
        <input
          required
          type={show ? "text" : "password"}
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => onConfirm(e.target.value)}
        />
      </label>
    </>
  );
}

function mismatch(a: string, b: string): string | null {
  return a !== b ? "De twee wachtwoorden zijn niet gelijk." : null;
}

export interface NewAccount {
  username: string;
  email: string;
  display_name: string;
  password: string;
  is_admin: boolean;
}

export function CreateAccountForm({
  onSubmit,
  error,
  pending,
}: {
  onSubmit: (v: NewAccount) => void;
  error: unknown;
  pending: boolean;
}) {
  const [v, setV] = useState({ username: "", email: "", display_name: "", is_admin: false });
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const m = mismatch(password, confirm);
    setLocalError(m);
    if (!m) onSubmit({ ...v, password });
  };
  return (
    <form className="form" onSubmit={submit}>
      <p className="muted small">
        Een gewoon account logt in met gebruikersnaam (of e-mail) en wachtwoord, zonder Authentik. Het e-mailadres
        wordt ook het adres van de kluis.
      </p>
      <label className="field">
        <span>Gebruikersnaam</span>
        <input
          required
          pattern={USERNAME_PATTERN}
          autoComplete="off"
          value={v.username}
          onChange={(e) => setV({ ...v, username: e.target.value })}
        />
      </label>
      <label className="field">
        <span>E-mail</span>
        <input
          required
          type="email"
          autoComplete="off"
          value={v.email}
          onChange={(e) => setV({ ...v, email: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Weergavenaam</span>
        <input value={v.display_name} onChange={(e) => setV({ ...v, display_name: e.target.value })} />
      </label>
      <NewPasswordFields
        value={password}
        onChange={setPassword}
        confirm={confirm}
        onConfirm={setConfirm}
        allowGenerate
      />
      <label className="check">
        <input type="checkbox" checked={v.is_admin} onChange={(e) => setV({ ...v, is_admin: e.target.checked })} />
        Instantiebeheerder
      </label>
      <ErrorBox error={localError ?? error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={pending}>
          Account aanmaken
        </button>
      </div>
    </form>
  );
}

/** Beheerder zet het wachtwoord van een account, of je wijzigt je eigen (met huidig wachtwoord). */
export function PasswordForm({
  own,
  onSubmit,
  error,
  pending,
  submitLabel,
}: {
  own: boolean;
  onSubmit: (v: { current_password: string; password: string }) => void;
  error: unknown;
  pending: boolean;
  submitLabel: string;
}) {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const m = mismatch(password, confirm);
    setLocalError(m);
    if (!m) onSubmit({ current_password: current, password });
  };
  return (
    <form className="form" onSubmit={submit}>
      {own && (
        <label className="field">
          <span>Huidig wachtwoord</span>
          <input
            required
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
      )}
      <NewPasswordFields
        value={password}
        onChange={setPassword}
        confirm={confirm}
        onConfirm={setConfirm}
        allowGenerate={!own}
      />
      <ErrorBox error={localError ?? error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={pending}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}
