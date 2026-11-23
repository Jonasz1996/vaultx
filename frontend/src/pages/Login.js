import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { ErrorBox } from "../components/ui";
import { getAssertion, webAuthnSupported } from "../webauthn";
const errors = {
    invalid_state: "De login is verlopen of ongeldig. Probeer opnieuw.",
    idp_error: "Authentik heeft de login geweigerd.",
    idp_unreachable: "Authentik is momenteel niet bereikbaar.",
    token_invalid: "Het antwoord van Authentik kon niet geverifieerd worden.",
    account_disabled: "Je account is gedeactiveerd in VaultX. Contacteer een beheerder.",
    authentik_not_here: "Inloggen met Authentik kan niet op dit adres. Log in met je VaultX-wachtwoord.",
};
// Hoogstens twee automatische pogingen per minuut, ook als er onderweg iets misloopt (bv. een
// sessiecookie die de browser niet bewaart): nooit een lus tussen VaultX en Authentik.
const AUTO_KEY = "vaultx.autoLogin";
const AUTO_WINDOW_MS = 60_000;
const AUTO_MAX = 2;
function autoAllowed() {
    try {
        const now = Date.now();
        const recent = JSON.parse(sessionStorage.getItem(AUTO_KEY) ?? "[]").filter((t) => typeof t === "number" && now - t < AUTO_WINDOW_MS);
        if (recent.length >= AUTO_MAX)
            return false;
        sessionStorage.setItem(AUTO_KEY, JSON.stringify([...recent, now]));
    }
    catch {
        // Geen sessionStorage: toch proberen, de server stuurt hoogstens één keer door.
    }
    return true;
}
/** Waarde veilig tussen enkele aanhalingstekens voor een shell (bash). */
function shellQuote(value) {
    return `'${value.replace(/'/g, `'\\''`)}'`;
}
/** Wat de beheerder op de VaultX-server uitvoert (Debian-container, als root). */
export function resetCommand(login) {
    return ("set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend && " +
        `/opt/vaultx/venv/bin/python -m app.cli reset-account ${shellQuote(login.trim())}`);
}
/**
 * "Kan je niet inloggen?": toont enkel wat de beheerder moet uitvoeren. Er gaat niets naar de
 * server, dus de tekst is altijd dezelfde en verraadt niet of een account bestaat.
 */
function CannotLogIn({ initial, onBack }) {
    const [email, setEmail] = useState(initial.includes("@") ? initial : "");
    const [shown, setShown] = useState(null);
    const [copied, setCopied] = useState(false);
    const command = shown ? resetCommand(shown) : "";
    return (_jsxs(_Fragment, { children: [_jsx("p", { className: "muted", children: "Wachtwoord vergeten, of je beveiligingssleutel kwijt?" }), !shown ? (_jsxs("form", { className: "form login-form", onSubmit: (e) => {
                    e.preventDefault();
                    setShown(email.trim());
                }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Je inlog-e-mailadres" }), _jsx("input", { required: true, type: "email", autoComplete: "email", autoFocus: true, maxLength: 320, value: email, onChange: (e) => setEmail(e.target.value) })] }), _jsx("button", { className: "btn btn-primary btn-wide", children: "Verder" }), _jsx("button", { type: "button", className: "btn btn-ghost btn-wide", onClick: onBack, children: "Terug naar inloggen" })] })) : (_jsxs("div", { className: "login-form recovery", children: [_jsx("p", { children: "Vraag aan je systeembeheerder om je account te resetten door dit commando uit te voeren op de VaultX-server (als root):" }), _jsx("pre", { className: "recovery-command", "data-testid": "reset-command", children: command }), _jsx("button", { type: "button", className: "btn btn-wide", onClick: () => {
                            void navigator.clipboard?.writeText(command).then(() => setCopied(true));
                        }, children: copied ? "Gekopieerd" : "Commando kopiëren" }), _jsxs("p", { children: ["Je krijgt dan een ", _jsx("strong", { children: "tijdelijk wachtwoord" }), " waarmee je de toegang tot je account kan herstellen. Het werkt \u00E9\u00E9n uur. Bij de eerste aanmelding kies je meteen een nieuw wachtwoord; daarna stel je je tweestapsverificatie (beveiligingssleutel, authenticator-app) opnieuw in."] }), _jsx("p", { className: "muted small", children: "Je kluis blijft versleuteld met je master password; dat kan niemand voor je resetten. Log je in via Authentik? Dan regelt Authentik je wachtwoord en MFA." }), _jsx("button", { type: "button", className: "btn btn-primary btn-wide", onClick: onBack, children: "Terug naar inloggen" })] }))] }));
}
export function LoginPage() {
    const [params] = useSearchParams();
    const error = params.get("error");
    const next = params.get("next") ?? "/";
    const [methods, setMethods] = useState(null);
    const [login, setLogin] = useState("");
    const [password, setPassword] = useState("");
    const [pending, setPending] = useState(false);
    const [loginError, setLoginError] = useState(null);
    const [step, setStep] = useState(null);
    const [recovery, setRecovery] = useState(null);
    const [redirecting, setRedirecting] = useState(false);
    const [change, setChange] = useState(null);
    const [newPassword, setNewPassword] = useState("");
    const [newPassword2, setNewPassword2] = useState("");
    const [help, setHelp] = useState(params.has("herstel"));
    // ?lokaal=1: altijd de gewone loginpagina, nooit doorsturen (noodtoegang als Authentik plat ligt).
    const local = params.has("lokaal");
    useEffect(() => {
        api
            .get("/auth/methods")
            .then((m) => {
            const quiet = local || error || params.get("logged_out") || params.get("stil") || params.has("herstel");
            if (!quiet && (m.silent || m.auto_redirect) && autoAllowed()) {
                setRedirecting(true);
                const query = new URLSearchParams({ next });
                if (m.silent)
                    query.set("silent", "1");
                window.location.replace(`/auth/login?${query}`);
                return;
            }
            setMethods(m);
        })
            .catch(() => setMethods({ authentik: true, password: false }));
    }, []);
    if (!methods) {
        return (_jsx("div", { className: "login", children: _jsxs("div", { className: "login-card", children: [_jsx("img", { src: "/favicon.svg", alt: "", width: 48, height: 48 }), _jsx("h1", { children: "VaultX" }), _jsx("p", { className: "muted", role: "status", children: redirecting ? "Aanmelden via Authentik…" : "Laden…" }), redirecting && (_jsx("a", { className: "btn btn-ghost btn-wide", href: `/login?lokaal=1&next=${encodeURIComponent(next)}`, children: "Toch zelf inloggen" }))] }) }));
    }
    const submit = async (e) => {
        e.preventDefault();
        setPending(true);
        setLoginError(null);
        try {
            const res = await api.post("/auth/password", {
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
            if (res.security_key_options && webAuthnSupported())
                void withKey(res);
            else
                setRecovery("");
        }
        catch (err) {
            setLoginError(err);
            setPassword("");
            setPending(false);
        }
    };
    const finish = async (s, answer) => {
        setPending(true);
        setLoginError(null);
        try {
            const res = await api.post("/auth/password/second-step", {
                ticket: s.ticket,
                next,
                ...answer,
            });
            window.location.assign(res.redirect_url);
        }
        catch (err) {
            setLoginError(err);
            setPending(false);
            // Ticket verlopen of te veel pogingen: terug naar het wachtwoord.
            if (err?.status === 429)
                setStep(null);
        }
    };
    const submitChange = async (e) => {
        e.preventDefault();
        if (!change)
            return;
        if (newPassword !== newPassword2) {
            setLoginError(new Error("De wachtwoorden zijn niet gelijk"));
            return;
        }
        setPending(true);
        setLoginError(null);
        try {
            const res = await api.post("/auth/password/change-temporary", {
                ticket: change.ticket,
                new_password: newPassword,
            });
            window.location.assign(res.redirect_url);
        }
        catch (err) {
            setLoginError(err);
            setPending(false);
            // Verlopen: terug naar het tijdelijke wachtwoord.
            if (err?.status === 401)
                setChange(null);
        }
    };
    const withKey = async (s) => {
        if (!s.security_key_options)
            return;
        setLoginError(null);
        try {
            await finish(s, { security_key: await getAssertion(s.security_key_options) });
        }
        catch (err) {
            setLoginError(err);
        }
    };
    return (_jsx("div", { className: "login", children: _jsxs("div", { className: "login-card", children: [_jsx("img", { src: "/favicon.svg", alt: "", width: 48, height: 48 }), _jsx("h1", { children: "VaultX" }), _jsx("p", { className: "muted", children: methods.password && methods.authentik
                        ? "Log in met je VaultX-account of met Authentik."
                        : methods.password
                            ? "Log in met je VaultX-account."
                            : "Beheeromgeving. Je logt in met je Authentik-account." }), params.get("logged_out") && _jsx("div", { className: "info-box", children: "Je bent uitgelogd." }), local && methods.authentik && (methods.silent || methods.auto_redirect) && (_jsx("div", { className: "info-box small", children: "Noodtoegang: VaultX stuurt je hier niet automatisch naar Authentik." })), error && _jsx("div", { className: "error-box", children: errors[error] ?? `Login mislukt (${error}).` }), help && _jsx(CannotLogIn, { initial: login, onBack: () => setHelp(false) }), change && !help && (_jsxs("form", { className: "form login-form", onSubmit: submitChange, children: [_jsx("div", { className: "info-box small", children: "Je logt in met een tijdelijk wachtwoord. Kies nu je eigen, nieuwe wachtwoord." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Nieuw wachtwoord" }), _jsx("input", { required: true, autoFocus: true, type: "password", autoComplete: "new-password", value: newPassword, onChange: (e) => setNewPassword(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Nogmaals" }), _jsx("input", { required: true, type: "password", autoComplete: "new-password", value: newPassword2, onChange: (e) => setNewPassword2(e.target.value) })] }), _jsx(ErrorBox, { error: loginError }), _jsx("button", { className: "btn btn-primary btn-wide", disabled: pending, children: pending ? "Bezig…" : "Wachtwoord instellen en inloggen" })] })), step && !help && (_jsxs("form", { className: "form login-form", onSubmit: (e) => {
                        e.preventDefault();
                        if (recovery !== null)
                            void finish(step, { recovery_code: recovery });
                        else
                            void withKey(step);
                    }, children: [recovery === null ? (_jsx("div", { className: "info-box", children: "Steek je beveiligingssleutel in en raak hem aan, of gebruik je passkey. Je browser vraagt erom." })) : (_jsxs(_Fragment, { children: [!step.security_key_options && (_jsx("div", { className: "info-box small", children: "Je beveiligingssleutel werkt enkel op de domeinnaam van VaultX, niet op dit adres. Log daar in, of gebruik je herstelcode (die zet de sleutels voor je login uit)." })), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Herstelcode" }), _jsx("input", { required: true, autoFocus: true, autoComplete: "one-time-code", value: recovery, onChange: (e) => setRecovery(e.target.value) })] })] })), _jsx(ErrorBox, { error: loginError }), _jsx("button", { className: "btn btn-primary btn-wide", disabled: pending || recovery === "", children: pending ? "Bezig…" : recovery === null ? "Beveiligingssleutel gebruiken" : "Inloggen met herstelcode" }), _jsxs("div", { className: "form-actions", children: [step.security_key_options && webAuthnSupported() && (_jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setRecovery(recovery === null ? "" : null), children: recovery === null ? "Herstelcode gebruiken" : "Toch met mijn sleutel" })), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => (setStep(null), setLoginError(null)), children: "Terug" })] })] })), methods.password && !step && !change && !help && (_jsxs(_Fragment, { children: [_jsxs("form", { className: "form login-form", onSubmit: submit, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Gebruikersnaam of e-mail" }), _jsx("input", { required: true, autoComplete: "username", autoFocus: true, value: login, onChange: (e) => setLogin(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Wachtwoord" }), _jsx("input", { required: true, type: "password", autoComplete: "current-password", value: password, onChange: (e) => setPassword(e.target.value) })] }), _jsx(ErrorBox, { error: loginError }), _jsx("button", { className: "btn btn-primary btn-wide", disabled: pending, children: pending ? "Bezig…" : "Inloggen" }), _jsx("button", { type: "button", className: "btn btn-ghost btn-wide small", onClick: () => (setHelp(true), setLoginError(null)), children: "Wachtwoord vergeten of kan je niet inloggen?" })] }), methods.authentik && (_jsx("div", { className: "login-divider", children: _jsx("span", { children: "of" }) }))] })), methods.authentik && !step && !change && !help && (_jsx("a", { className: methods.password ? "btn btn-wide" : "btn btn-primary btn-wide", href: `/auth/login?next=${encodeURIComponent(next)}`, children: "Inloggen met Authentik" }))] }) }));
}
