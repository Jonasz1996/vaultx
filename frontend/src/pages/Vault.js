import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import qrcode from "qrcode-generator";
import { useEffect, useState } from "react";
import { NavLink } from "react-router";
import { api } from "../api/client";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { formatDate, relative } from "../format";
import { WebVault } from "./VaultItems";
import { ARGON2_DEFAULTS, changeMasterPassword, createVaultKeys, deriveLogin, PBKDF2_ITERATIONS, } from "../vault/crypto";
import { createCredential, getAssertion, webAuthnSupported } from "../webauthn";
const BASE = "/api/v1/me/vault";
export function VaultPage({ tab = "items" }) {
    const qc = useQueryClient();
    const status = useQuery({ queryKey: ["vault"], queryFn: () => api.get(BASE) });
    const refresh = () => void qc.invalidateQueries({ queryKey: ["vault"] });
    // Tellers en apparaten opnieuw ophalen bij het wisselen van tabblad (de webkluis wijzigt ze).
    useEffect(() => void qc.invalidateQueries({ queryKey: ["vault"] }), [tab, qc]);
    if (status.isLoading)
        return _jsx(Loading, {});
    if (!status.data)
        return _jsx(ErrorBox, { error: status.error });
    const s = status.data;
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Mijn kluis", subtitle: "Persoonlijke wachtwoordkluis: hier in de browser, en met de offici\u00EBle Bitwarden\u00AE-apps, -extensies en -CLI." }), !s.enrolled ? (_jsx(Enroll, { s: s, onDone: refresh })) : (_jsxs(_Fragment, { children: [_jsxs("nav", { className: "page-tabs", children: [_jsx(NavLink, { to: "/vault", end: true, children: "Items" }), _jsx(NavLink, { to: "/vault/settings", children: "Apps, apparaten en beveiliging" })] }), tab === "items" ? _jsx(WebVault, { status: s }) : _jsx(Enrolled, { s: s, onChange: refresh })] }))] }));
}
function Enroll({ s, onDone }) {
    const [password, setPassword] = useState("");
    const [confirmPw, setConfirmPw] = useState("");
    const [understood, setUnderstood] = useState(false);
    const email = s.email ?? "";
    const mismatch = confirmPw.length > 0 && confirmPw !== password;
    const enroll = useMutation({
        mutationFn: async () => api.post(BASE, await createVaultKeys(email, password)),
        onSuccess: () => {
            setPassword("");
            setConfirmPw("");
            onDone();
        },
    });
    const submit = (e) => {
        e.preventDefault();
        if (mismatch || !understood)
            return;
        enroll.mutate();
    };
    if (s.blocked_reason)
        return (_jsx(Card, { title: "Kluis activeren", children: _jsx("div", { className: "error-box", children: s.blocked_reason }) }));
    return (_jsx(Card, { title: "Kluis activeren", children: _jsxs("form", { onSubmit: submit, className: "form", style: { maxWidth: 520 }, children: [_jsx("div", { className: "info-box small", children: "Kies een master password. Je browser leidt daar je sleutels uit af; VaultX krijgt het wachtwoord nooit te zien en kan het dus ook niet herstellen. Ben je het kwijt, dan kan je de kluis alleen leegmaken en opnieuw beginnen." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "E-mailadres (je login in de Bitwarden-apps)" }), _jsx("input", { value: email, disabled: true })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Master password" }), _jsx("input", { type: "password", autoComplete: "new-password", required: true, value: password, onChange: (e) => setPassword(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Herhaal master password" }), _jsx("input", { type: "password", autoComplete: "new-password", required: true, value: confirmPw, onChange: (e) => setConfirmPw(e.target.value) }), mismatch && _jsx("span", { className: "small", style: { color: "var(--bad-fg)" }, children: "Komt niet overeen" })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: understood, onChange: (e) => setUnderstood(e.target.checked) }), "Ik begrijp dat niemand mijn master password kan herstellen."] }), _jsx(ErrorBox, { error: enroll.error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: enroll.isPending || !password || mismatch || !confirmPw || !understood, children: enroll.isPending ? "Sleutels afleiden…" : "Kluis activeren" }) })] }) }));
}
function Enrolled({ s, onChange }) {
    const [showRevoked, setShowRevoked] = useState(false);
    const revoke = useMutation({
        mutationFn: (id) => api.post(`${BASE}/devices/${id}/revoke`),
        onSuccess: onChange,
    });
    const devices = showRevoked ? s.devices : s.devices.filter((d) => !d.revoked_at);
    const revokedCount = s.devices.length - s.devices.filter((d) => !d.revoked_at).length;
    const kdf = s.kdf === 1
        ? `Argon2id, ${s.kdf_iterations} iteraties, ${s.kdf_memory} MiB, parallelisme ${s.kdf_parallelism}`
        : `PBKDF2-SHA256, ${s.kdf_iterations?.toLocaleString("nl-BE")} iteraties`;
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "stats", children: [_jsx(Stat, { label: "Items", value: s.item_count, hint: s.trash_count ? `${s.trash_count} in prullenbak` : undefined }), _jsx(Stat, { label: "Mappen", value: s.folder_count }), _jsx(Stat, { label: "Apparaten", value: s.devices.filter((d) => !d.revoked_at).length }), _jsx(Stat, { label: "Laatste wijziging", value: relative(s.revision_date) })] }), _jsxs(Card, { title: "Verbinden met een Bitwarden-app", children: [s.server_url.startsWith("http://") && (_jsx("div", { className: "error-box small", children: "VaultX draait hier zonder HTTPS. De Bitwarden-apps weigeren dat; zet Nginx Proxy Manager met een certificaat voor VaultX en stel VAULTX_PUBLIC_URL in op het https-adres." })), _jsxs("ol", { className: "steps", children: [_jsxs("li", { children: ["Kies in de app of extensie bij het inloggen ", _jsx("b", { children: "Self-hosted" }), " (of \"Ingelogd op: zelf gehost\") en vul als server-URL in: ", _jsx("code", { className: "mono", children: s.server_url })] }), _jsxs("li", { children: ["Log in met ", _jsx("b", { children: s.email }), " en je master password."] }), _jsxs("li", { children: ["CLI: ", _jsxs("code", { className: "mono", children: ["bw config server ", s.server_url] }), " en daarna", " ", _jsxs("code", { className: "mono", children: ["bw login ", s.email] })] })] }), _jsx("p", { className: "muted small", children: "Inloggen werkt alleen zolang je VaultX-account actief is. Wie in VaultX gedeactiveerd wordt, verliest ook de toegang tot de kluis op al zijn apparaten." })] }), _jsxs(Card, { title: "Apparaten", actions: revokedCount > 0 && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: showRevoked, onChange: (e) => setShowRevoked(e.target.checked) }), "Ook ", revokedCount, " afgemeld", revokedCount > 1 ? "e" : "", " tonen"] })), children: [_jsx(ErrorBox, { error: revoke.error }), !devices.length ? (_jsx(Empty, { children: "Nog geen app ingelogd." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Apparaat" }), _jsx("th", { children: "Type" }), _jsx("th", { children: "Eerste login" }), _jsx("th", { children: "Laatst gezien" }), _jsx("th", {})] }) }), _jsx("tbody", { children: devices.map((d) => (_jsxs("tr", { className: d.revoked_at ? "dim" : undefined, children: [_jsx("td", { children: d.name }), _jsx("td", { children: d.type_name }), _jsx("td", { children: formatDate(d.created_at) }), _jsx("td", { children: relative(d.last_seen_at) }), _jsx("td", { className: "right", children: d.revoked_at ? (_jsx(Badge, { children: "afgemeld" })) : (_jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => revoke.mutate(d.id), disabled: revoke.isPending, children: "Afmelden" })) })] }, d.id))) })] }) }))] }), _jsx(Card, { title: "Details", children: _jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Login" }), _jsx("dd", { children: s.email }), _jsx("dt", { children: "Sleutelafleiding" }), _jsx("dd", { children: kdf }), _jsx("dt", { children: "Geactiveerd" }), _jsx("dd", { children: formatDate(s.enrolled_at) })] }) }), _jsx(TwoFactor, { s: s, onChange: onChange }), _jsx(ChangePassword, { s: s, onDone: onChange }), _jsx(ResetVault, { s: s, onDone: onChange })] }));
}
function ResetVault({ s, onDone }) {
    const [password, setPassword] = useState("");
    const reset = useMutation({
        mutationFn: async () => {
            // Met master password, of leeg als je het kwijt bent: dan moet je net opnieuw ingelogd zijn.
            const body = password ? { master_password_hash: (await deriveLogin(unlockParams(s), password)).loginHash } : {};
            return api.post(`${BASE}/reset`, body);
        },
        onSuccess: () => {
            setPassword("");
            onDone();
        },
    });
    return (_jsxs(Card, { title: "Kluis leegmaken", children: [_jsx("p", { className: "muted small", children: "Master password vergeten? Niemand kan het herstellen. Je kan de kluis wel verwijderen en opnieuw activeren; al je items gaan dan definitief verloren en alle apparaten worden afgemeld." }), _jsx("p", { className: "muted small", children: "Vul je master password in om te bevestigen. Ben je het vergeten, laat het veld dan leeg: dat kan enkel binnen 10 minuten nadat je opnieuw bent ingelogd bij VaultX." }), _jsx(ErrorBox, { error: reset.error }), _jsxs("form", { className: "row", onSubmit: (e) => {
                    e.preventDefault();
                    const answer = prompt(`Typ VERWIJDER om je kluis met ${s.item_count + s.trash_count} items te wissen.`);
                    if (answer === "VERWIJDER")
                        reset.mutate();
                }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Master password (leeg als je het vergeten bent)" }), _jsx("input", { type: "password", autoComplete: "current-password", value: password, onChange: (e) => setPassword(e.target.value) })] }), _jsx("button", { className: "btn btn-danger", type: "submit", disabled: reset.isPending, children: "Kluis verwijderen" })] })] }));
}
function unlockParams(s) {
    if (!s.kdf_salt || s.kdf === null || !s.kdf_iterations)
        throw new Error("Je kluis is nog niet actief");
    return {
        salt: s.kdf_salt,
        kdf: s.kdf,
        iterations: s.kdf_iterations,
        memory: s.kdf_memory,
        parallelism: s.kdf_parallelism,
    };
}
function qrDataUrl(text) {
    const qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr.createDataURL(5, 2);
}
function useProof(s) {
    return async (code) => {
        if (code.trim())
            return { code: code.trim() };
        if (!s.security_keys.length || !webAuthnSupported())
            throw new Error("Vul een code uit je authenticator-app of je herstelcode in.");
        const { options } = await api.post(`${BASE}/2fa/challenge`);
        if (!options)
            throw new Error("Je sleutel werkt hier niet (geen domeinnaam). Vul een code of je herstelcode in.");
        return { security_key: await getAssertion(options) };
    };
}
function TwoFactor({ s, onChange }) {
    const [recovery, setRecovery] = useState(null);
    const showRecovery = (code) => code && setRecovery(code);
    return (_jsxs(Card, { title: "Tweestapsverificatie", actions: s.two_factor_enabled ? _jsx(Badge, { tone: "good", children: "aan" }) : _jsx(Badge, { children: "uit" }), children: [recovery && (_jsxs("div", { className: "info-box", children: [_jsxs("p", { children: [_jsx("b", { children: "Bewaar deze herstelcode" }), " (op papier of buiten deze kluis). Ben je je telefoon of sleutel kwijt, dan log je daarmee toch in; tweestapsverificatie gaat dan uit. Je ziet hem maar \u00E9\u00E9n keer."] }), _jsx("code", { className: "mono recovery-code", children: recovery })] })), _jsxs("p", { className: "muted small", children: [s.two_factor_enabled
                        ? `Aan sinds ${formatDate(s.two_factor_enabled_at)}. `
                        : "Wie je master password kent, kan dan nog niet in je kluis zonder je telefoon of sleutel. ", "Geldt voor de Bitwarden-apps, extensies, CLI en de webkluis hier; met \"Onthoud mij\" slaat een apparaat de tweede stap 30 dagen over."] }), _jsx(SecurityKeys, { s: s, onChange: onChange, onRecovery: showRecovery }), _jsx(Authenticator, { s: s, onChange: onChange, onRecovery: showRecovery }), s.two_factor_enabled && _jsx(DisableAll, { s: s, onChange: () => (setRecovery(null), onChange()) })] }));
}
function SecurityKeys({ s, onChange, onRecovery, }) {
    const [adding, setAdding] = useState(false);
    const [name, setName] = useState("YubiKey");
    const [password, setPassword] = useState("");
    const [removing, setRemoving] = useState(null);
    const [code, setCode] = useState("");
    const proof = useProof(s);
    const supported = webAuthnSupported();
    const add = useMutation({
        mutationFn: async () => {
            const { loginHash } = await deriveLogin(unlockParams(s), password);
            const { options } = await api.post(`${BASE}/2fa/security-keys/options`, {
                master_password_hash: loginHash,
            });
            const credential = await createCredential(options);
            return api.post(`${BASE}/2fa/security-keys`, { name, credential });
        },
        onSuccess: (data) => {
            setPassword("");
            setAdding(false);
            onRecovery(data.recovery_code);
            onChange();
        },
    });
    const remove = useMutation({
        mutationFn: async (id) => {
            const { loginHash } = await deriveLogin(unlockParams(s), password);
            return api.post(`${BASE}/2fa/security-keys/${id}/remove`, {
                master_password_hash: loginHash,
                ...(await proof(code)),
            });
        },
        onSuccess: () => {
            setPassword("");
            setCode("");
            setRemoving(null);
            onChange();
        },
    });
    return (_jsxs("div", { className: "twofactor-section", children: [_jsx("h3", { children: "Beveiligingssleutels en passkeys" }), _jsxs("p", { className: "muted small", children: ["YubiKey of een andere FIDO2-sleutel, of een passkey (Windows Hello, je telefoon). Werkt enkel op", " ", _jsx("b", { children: s.security_keys_rp_id ?? "een domeinnaam" }), ", niet als je VaultX via een IP-adres opent; daar gebruik je de authenticator-app of je herstelcode."] }), !s.security_keys_available && (_jsx("div", { className: "error-box small", children: "VaultX heeft geen domeinnaam (VAULTX_PUBLIC_URL is een IP-adres), dus sleutels kunnen hier niet." })), s.security_keys.length > 0 && (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Naam" }), _jsx("th", { children: "Gekoppeld" }), _jsx("th", { children: "Laatst gebruikt" }), _jsx("th", {})] }) }), _jsx("tbody", { children: s.security_keys.map((k) => (_jsxs("tr", { children: [_jsxs("td", { children: [k.name, " ", k.backed_up && _jsx(Badge, { children: "passkey" })] }), _jsx("td", { children: formatDate(k.created_at) }), _jsx("td", { children: k.last_used_at ? relative(k.last_used_at) : "nog niet" }), _jsx("td", { className: "right", children: _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => (setRemoving(removing === k.id ? null : k.id), setAdding(false)), children: "Weghalen" }) })] }, k.id))) })] }) })), removing && (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    remove.mutate(removing);
                }, children: [_jsx("p", { className: "muted small", children: "Bevestig met je master password en een gekoppelde sleutel. Laat je de code leeg, dan vraagt je browser om een sleutel aan te raken." }), _jsx(MasterField, { value: password, onChange: setPassword }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Of: code uit de app of herstelcode" }), _jsx("input", { autoComplete: "one-time-code", value: code, onChange: (e) => setCode(e.target.value) })] }), _jsx(ErrorBox, { error: remove.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-danger", disabled: remove.isPending || !password, children: remove.isPending ? "Controleren…" : "Sleutel weghalen" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setRemoving(null), children: "Annuleren" })] })] })), adding ? (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    add.mutate();
                }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam (om de sleutel later te herkennen)" }), _jsx("input", { required: true, maxLength: 100, value: name, onChange: (e) => setName(e.target.value) })] }), _jsx(MasterField, { value: password, onChange: setPassword }), _jsx("p", { className: "muted small", children: "Na \"Koppelen\" vraagt je browser om de sleutel in te steken en aan te raken." }), _jsx(ErrorBox, { error: add.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-primary", disabled: add.isPending || !password || !name.trim(), children: add.isPending ? "Wachten op je sleutel…" : "Koppelen" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setAdding(false), children: "Annuleren" })] })] })) : (!removing && (_jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", disabled: !s.security_keys_available || !supported, title: supported ? undefined : "Deze browser ondersteunt geen beveiligingssleutels", onClick: () => setAdding(true), children: "Sleutel of passkey toevoegen" }) })))] }));
}
function MasterField({ value, onChange }) {
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: "Master password" }), _jsx("input", { type: "password", autoComplete: "current-password", required: true, value: value, onChange: (e) => onChange(e.target.value) })] }));
}
function Authenticator({ s, onChange, onRecovery, }) {
    const [password, setPassword] = useState("");
    const [code, setCode] = useState("");
    const [setup, setSetup] = useState(null);
    const [removing, setRemoving] = useState(false);
    const proof = useProof(s);
    const start = useMutation({
        mutationFn: async () => {
            const { loginHash } = await deriveLogin(unlockParams(s), password);
            return api.post(`${BASE}/2fa/setup`, { master_password_hash: loginHash });
        },
        onSuccess: (data) => {
            setPassword("");
            setSetup(data);
        },
    });
    const enable = useMutation({
        mutationFn: () => api.post(`${BASE}/2fa/enable`, { code }),
        onSuccess: (data) => {
            setCode("");
            setSetup(null);
            onRecovery(data.recovery_code);
            onChange();
        },
    });
    const remove = useMutation({
        mutationFn: async () => {
            const { loginHash } = await deriveLogin(unlockParams(s), password);
            return api.post(`${BASE}/2fa/authenticator/remove`, { master_password_hash: loginHash, ...(await proof(code)) });
        },
        onSuccess: () => {
            setPassword("");
            setCode("");
            setRemoving(false);
            onChange();
        },
    });
    return (_jsxs("div", { className: "twofactor-section", children: [_jsxs("h3", { children: ["Authenticator-app ", s.authenticator_enabled ? _jsx(Badge, { tone: "good", children: "gekoppeld" }) : null] }), s.authenticator_enabled ? (removing ? (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    remove.mutate();
                }, children: [_jsx(MasterField, { value: password, onChange: setPassword }), _jsxs("label", { className: "field", children: [_jsxs("span", { children: ["Code uit de app of herstelcode", s.security_keys.length ? " (leeg: bevestigen met je sleutel)" : ""] }), _jsx("input", { autoComplete: "one-time-code", value: code, onChange: (e) => setCode(e.target.value) })] }), _jsx(ErrorBox, { error: remove.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-danger", disabled: remove.isPending || !password || (!code && !s.security_keys.length), children: remove.isPending ? "Controleren…" : "App weghalen" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setRemoving(false), children: "Annuleren" })] })] })) : (_jsxs("div", { className: "form-actions", children: [_jsx("p", { className: "muted small", style: { margin: 0 }, children: "De apps vragen een code uit je authenticator-app." }), _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => setRemoving(true), children: "Weghalen" })] }))) : setup ? (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    enable.mutate();
                }, children: [_jsx("p", { className: "muted small", children: "Scan deze code met je authenticator-app (Aegis, 2FAS, Google Authenticator, Bitwarden Authenticator, ...) en vul de code in die de app toont." }), _jsx("img", { className: "qr", src: qrDataUrl(setup.otpauth_uri), alt: "QR-code voor je authenticator-app" }), _jsxs("p", { className: "small", children: ["Kan je niet scannen? Sleutel: ", _jsx("code", { className: "mono", children: setup.secret })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Code uit de app" }), _jsx("input", { autoComplete: "one-time-code", inputMode: "numeric", required: true, value: code, onChange: (e) => setCode(e.target.value) })] }), _jsx(ErrorBox, { error: enable.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-primary", disabled: enable.isPending || code.length < 6, children: "Aanzetten" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setSetup(null), children: "Annuleren" })] })] })) : (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    start.mutate();
                }, children: [_jsx(MasterField, { value: password, onChange: setPassword }), _jsx(ErrorBox, { error: start.error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", disabled: start.isPending || !password, children: start.isPending ? "Controleren…" : "Instellen met een authenticator-app" }) })] }))] }));
}
function DisableAll({ s, onChange }) {
    const [open, setOpen] = useState(false);
    const [password, setPassword] = useState("");
    const [code, setCode] = useState("");
    const proof = useProof(s);
    const disable = useMutation({
        mutationFn: async () => {
            const { loginHash } = await deriveLogin(unlockParams(s), password);
            return api.post(`${BASE}/2fa/disable`, { master_password_hash: loginHash, ...(await proof(code)) });
        },
        onSuccess: () => {
            setPassword("");
            setCode("");
            setOpen(false);
            onChange();
        },
    });
    if (!open)
        return (_jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => setOpen(true), children: "Tweestapsverificatie helemaal uitzetten" }) }));
    return (_jsxs("form", { className: "form twofactor-section", style: { maxWidth: 520 }, onSubmit: (e) => {
            e.preventDefault();
            disable.mutate();
        }, children: [_jsx("p", { className: "muted small", children: "Haalt de app, alle sleutels en de herstelcode weg." }), _jsx(MasterField, { value: password, onChange: setPassword }), _jsxs("label", { className: "field", children: [_jsxs("span", { children: ["Code uit de app of herstelcode", s.security_keys.length ? " (leeg: bevestigen met je sleutel)" : ""] }), _jsx("input", { autoComplete: "one-time-code", value: code, onChange: (e) => setCode(e.target.value) })] }), _jsx(ErrorBox, { error: disable.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-danger", disabled: disable.isPending || !password || (!code && !s.security_keys.length), children: disable.isPending ? "Controleren…" : "Tweestapsverificatie uitzetten" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setOpen(false), children: "Annuleren" })] })] }));
}
function ChangePassword({ s, onDone }) {
    const [current, setCurrent] = useState("");
    const [password, setPassword] = useState("");
    const [confirmPw, setConfirmPw] = useState("");
    const [kdf, setKdf] = useState(s.kdf ?? 0);
    const [done, setDone] = useState(false);
    const mismatch = confirmPw.length > 0 && confirmPw !== password;
    const change = useMutation({
        mutationFn: async () => {
            const params = unlockParams(s);
            const old = await deriveLogin(params, current);
            const { user_key } = await api.post(`${BASE}/unlock`, {
                master_password_hash: old.loginHash,
            });
            // Zelfde KDF als nu blijft ongewijzigd; anders de standaard van Bitwarden.
            const next = kdf === s.kdf
                ? { kdf, iterations: params.iterations, memory: params.memory, parallelism: params.parallelism }
                : kdf === 1
                    ? { kdf: 1, ...ARGON2_DEFAULTS }
                    : { kdf: 0, iterations: PBKDF2_ITERATIONS };
            const payload = await changeMasterPassword(old, user_key, params.salt, password, next);
            return api.post(`${BASE}/password`, payload);
        },
        onSuccess: () => {
            setCurrent("");
            setPassword("");
            setConfirmPw("");
            setDone(true);
            onDone();
        },
    });
    const submit = (e) => {
        e.preventDefault();
        if (mismatch || !current)
            return;
        setDone(false);
        change.mutate();
    };
    return (_jsx(Card, { title: "Master password wijzigen", children: _jsxs("form", { onSubmit: submit, className: "form", style: { maxWidth: 520 }, children: [_jsx("p", { className: "muted small", children: "Je items blijven gewoon leesbaar: je browser pakt je sleutel opnieuw in met het nieuwe wachtwoord. Daarna worden al je apparaten afgemeld en log je in de Bitwarden-apps opnieuw in met het nieuwe wachtwoord." }), done && _jsx("div", { className: "info-box small", children: "Master password gewijzigd. Log opnieuw in op je apparaten." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Huidig master password" }), _jsx("input", { type: "password", autoComplete: "current-password", required: true, value: current, onChange: (e) => setCurrent(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Nieuw master password" }), _jsx("input", { type: "password", autoComplete: "new-password", required: true, value: password, onChange: (e) => setPassword(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Herhaal nieuw master password" }), _jsx("input", { type: "password", autoComplete: "new-password", required: true, value: confirmPw, onChange: (e) => setConfirmPw(e.target.value) }), mismatch && _jsx("span", { className: "small", style: { color: "var(--bad-fg)" }, children: "Komt niet overeen" })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Sleutelafleiding" }), _jsxs("select", { value: kdf, onChange: (e) => setKdf(Number(e.target.value)), children: [_jsxs("option", { value: 0, children: ["PBKDF2-SHA256 (", PBKDF2_ITERATIONS.toLocaleString("nl-BE"), " iteraties)"] }), _jsxs("option", { value: 1, children: ["Argon2id (", ARGON2_DEFAULTS.iterations, " iteraties, ", ARGON2_DEFAULTS.memory, " MiB,", " ", ARGON2_DEFAULTS.parallelism, " threads), sterker tegen kraken"] })] })] }), _jsx(ErrorBox, { error: change.error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: change.isPending || !current || !password || mismatch || !confirmPw, children: change.isPending ? "Sleutels afleiden…" : "Wijzigen en alle apparaten afmelden" }) })] }) }));
}
