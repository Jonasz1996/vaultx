import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Beveiligingssleutels en passkeys als tweede stap na het VaultX-wachtwoord (eigen account).
// Inloggen via Authentik vraagt deze stap niet: daar stel je je sleutel in Authentik zelf in.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { formatDate, relative } from "../format";
import { createCredential, getAssertion, webAuthnSupported } from "../webauthn";
import { Badge, Card, ErrorBox } from "./ui";
const BASE = "/api/v1/me/security-keys";
export function LoginSecurityKeys() {
    const qc = useQueryClient();
    const q = useQuery({ queryKey: ["me", "security-keys"], queryFn: () => api.get(BASE) });
    const [mode, setMode] = useState(null);
    const [name, setName] = useState("YubiKey");
    const [password, setPassword] = useState("");
    const [recoveryInput, setRecoveryInput] = useState("");
    const [recovery, setRecovery] = useState(null);
    const supported = webAuthnSupported();
    const done = () => {
        setPassword("");
        setRecoveryInput("");
        setMode(null);
        void qc.invalidateQueries({ queryKey: ["me", "security-keys"] });
    };
    const add = useMutation({
        mutationFn: async () => {
            const { options } = await api.post(`${BASE}/options`, { password });
            const credential = await createCredential(options);
            return api.post(BASE, { name, credential });
        },
        onSuccess: (data) => {
            if (data.recovery_code)
                setRecovery(data.recovery_code);
            done();
        },
    });
    const remove = useMutation({
        mutationFn: async (id) => {
            let proof = { recovery_code: recoveryInput.trim() };
            if (!recoveryInput.trim()) {
                const { options } = await api.post(`${BASE}/challenge`);
                if (!options || !supported)
                    throw new Error("Je sleutel werkt hier niet. Vul je herstelcode in om de sleutel weg te halen.");
                proof = { security_key: await getAssertion(options) };
            }
            return api.post(`${BASE}/${id}/remove`, { password, ...proof });
        },
        onSuccess: done,
    });
    const s = q.data;
    if (!s?.has_password)
        return null;
    return (_jsxs(Card, { title: "Beveiligingssleutels voor het wachtwoordlogin", actions: s.keys.length ? _jsx(Badge, { tone: "good", children: "aan" }) : _jsx(Badge, { children: "uit" }), children: [recovery && (_jsxs("div", { className: "info-box", children: [_jsxs("p", { children: [_jsx("b", { children: "Bewaar deze herstelcode." }), " Ben je je sleutel kwijt, dan log je daarmee toch in met je wachtwoord; de sleutels voor je login gaan dan uit. Je ziet hem maar \u00E9\u00E9n keer."] }), _jsx("code", { className: "mono recovery-code", children: recovery })] })), _jsxs("p", { className: "muted small", children: ["Na je VaultX-wachtwoord vraagt de loginpagina dan ook je YubiKey, een andere FIDO2-sleutel of een passkey. Werkt enkel op ", _jsx("b", { children: s.rp_id ?? "een domeinnaam" }), "; op een IP-adres log je in met je herstelcode. Inloggen via Authentik gebruikt de MFA van Authentik zelf. Sleutels voor je kluis stel je in bij Mijn kluis."] }), !s.available && (_jsx("div", { className: "error-box small", children: "VaultX heeft geen domeinnaam (VAULTX_PUBLIC_URL is een IP-adres), dus sleutels kunnen hier niet." })), s.keys.length > 0 && (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Naam" }), _jsx("th", { children: "Gekoppeld" }), _jsx("th", { children: "Laatst gebruikt" }), _jsx("th", {})] }) }), _jsx("tbody", { children: s.keys.map((k) => (_jsxs("tr", { children: [_jsxs("td", { children: [k.name, " ", k.backed_up && _jsx(Badge, { children: "passkey" })] }), _jsx("td", { children: formatDate(k.created_at) }), _jsx("td", { children: k.last_used_at ? relative(k.last_used_at) : "nog niet" }), _jsx("td", { className: "right", children: _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => setMode({ kind: "remove", id: k.id }), children: "Weghalen" }) })] }, k.id))) })] }) })), mode ? (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    if (mode.kind === "add")
                        add.mutate();
                    else
                        remove.mutate(mode.id);
                }, children: [mode.kind === "add" && (_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam (om de sleutel later te herkennen)" }), _jsx("input", { required: true, maxLength: 100, value: name, onChange: (e) => setName(e.target.value) })] })), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Je VaultX-wachtwoord" }), _jsx("input", { type: "password", autoComplete: "current-password", required: true, value: password, onChange: (e) => setPassword(e.target.value) })] }), mode.kind === "remove" && (_jsxs("label", { className: "field", children: [_jsx("span", { children: "Herstelcode (leeg: bevestigen met een gekoppelde sleutel)" }), _jsx("input", { autoComplete: "one-time-code", value: recoveryInput, onChange: (e) => setRecoveryInput(e.target.value) })] })), _jsx(ErrorBox, { error: mode.kind === "add" ? add.error : remove.error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: mode.kind === "add" ? "btn btn-primary" : "btn btn-danger", disabled: add.isPending || remove.isPending || !password, children: add.isPending
                                    ? "Wachten op je sleutel…"
                                    : remove.isPending
                                        ? "Controleren…"
                                        : mode.kind === "add"
                                            ? "Koppelen"
                                            : "Sleutel weghalen" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setMode(null), children: "Annuleren" })] })] })) : (_jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", disabled: !s.available || !supported, title: supported ? undefined : "Deze browser ondersteunt geen beveiligingssleutels", onClick: () => setMode({ kind: "add" }), children: "Sleutel of passkey toevoegen" }) }))] }));
}
