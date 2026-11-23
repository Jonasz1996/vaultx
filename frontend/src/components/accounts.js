import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { ErrorBox } from "./ui";
// Gebruikersnaam zoals de backend hem aanvaardt (USERNAME_PATTERN).
const USERNAME_PATTERN = "[A-Za-z0-9][A-Za-z0-9._@+\\-]{1,149}";
/** Willekeurig wachtwoord van 20 tekens, zonder tekens die je makkelijk verwart. */
function generatePassword() {
    const alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!";
    const bytes = crypto.getRandomValues(new Uint32Array(20));
    return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}
/** Nieuw wachtwoord met bevestiging, optioneel genereren en tonen. */
function NewPasswordFields({ value, onChange, confirm, onConfirm, allowGenerate, }) {
    const [show, setShow] = useState(false);
    return (_jsxs(_Fragment, { children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Nieuw wachtwoord" }), _jsxs("div", { className: "input-row", children: [_jsx("input", { required: true, type: show ? "text" : "password", autoComplete: "new-password", value: value, onChange: (e) => onChange(e.target.value) }), _jsx("button", { type: "button", className: "btn", onClick: () => setShow(!show), children: show ? "Verbergen" : "Tonen" }), allowGenerate && (_jsx("button", { type: "button", className: "btn", onClick: () => {
                                    const pw = generatePassword();
                                    onChange(pw);
                                    onConfirm(pw);
                                    setShow(true);
                                }, children: "Genereren" }))] }), _jsx("small", { className: "muted", children: "Gebruik niet je master password van de kluis." })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Nogmaals" }), _jsx("input", { required: true, type: show ? "text" : "password", autoComplete: "new-password", value: confirm, onChange: (e) => onConfirm(e.target.value) })] })] }));
}
function mismatch(a, b) {
    return a !== b ? "De twee wachtwoorden zijn niet gelijk." : null;
}
export function CreateAccountForm({ onSubmit, error, pending, }) {
    const [v, setV] = useState({ username: "", email: "", display_name: "", is_admin: false });
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [localError, setLocalError] = useState(null);
    const submit = (e) => {
        e.preventDefault();
        const m = mismatch(password, confirm);
        setLocalError(m);
        if (!m)
            onSubmit({ ...v, password });
    };
    return (_jsxs("form", { className: "form", onSubmit: submit, children: [_jsx("p", { className: "muted small", children: "Een gewoon account logt in met gebruikersnaam (of e-mail) en wachtwoord, zonder Authentik. Het e-mailadres wordt ook het adres van de kluis." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Gebruikersnaam" }), _jsx("input", { required: true, pattern: USERNAME_PATTERN, autoComplete: "off", value: v.username, onChange: (e) => setV({ ...v, username: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "E-mail" }), _jsx("input", { required: true, type: "email", autoComplete: "off", value: v.email, onChange: (e) => setV({ ...v, email: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Weergavenaam" }), _jsx("input", { value: v.display_name, onChange: (e) => setV({ ...v, display_name: e.target.value }) })] }), _jsx(NewPasswordFields, { value: password, onChange: setPassword, confirm: confirm, onConfirm: setConfirm, allowGenerate: true }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: v.is_admin, onChange: (e) => setV({ ...v, is_admin: e.target.checked }) }), "Instantiebeheerder"] }), _jsx(ErrorBox, { error: localError ?? error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: pending, children: "Account aanmaken" }) })] }));
}
/** Beheerder zet het wachtwoord van een account, of je wijzigt je eigen (met huidig wachtwoord). */
export function PasswordForm({ own, onSubmit, error, pending, submitLabel, }) {
    const [current, setCurrent] = useState("");
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [localError, setLocalError] = useState(null);
    const submit = (e) => {
        e.preventDefault();
        const m = mismatch(password, confirm);
        setLocalError(m);
        if (!m)
            onSubmit({ current_password: current, password });
    };
    return (_jsxs("form", { className: "form", onSubmit: submit, children: [own && (_jsxs("label", { className: "field", children: [_jsx("span", { children: "Huidig wachtwoord" }), _jsx("input", { required: true, type: "password", autoComplete: "current-password", value: current, onChange: (e) => setCurrent(e.target.value) })] })), _jsx(NewPasswordFields, { value: password, onChange: setPassword, confirm: confirm, onConfirm: setConfirm, allowGenerate: !own }), _jsx(ErrorBox, { error: localError ?? error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: pending, children: submitLabel }) })] }));
}
