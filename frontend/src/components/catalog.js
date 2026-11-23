import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { authLabels, statusInfo } from "../format";
import { Badge, ErrorBox } from "./ui";
export function StatusBadge({ status }) {
    const s = statusInfo[status] ?? { label: status, tone: "neutral", hint: "" };
    return (_jsx(Badge, { tone: s.tone, title: s.hint, children: s.label }));
}
export function AuthLabel({ method }) {
    return _jsx("span", { className: method === "unknown" ? "muted" : undefined, children: authLabels[method] ?? method });
}
export function Warnings({ items }) {
    if (!items.length)
        return _jsx("span", { className: "muted", children: "\u2014" });
    return (_jsx("ul", { className: "warnings", children: items.map((w) => (_jsx("li", { title: w.code, children: w.message }, w.code))) }));
}
export function HostLink({ domain, ssl = true }) {
    if (domain.startsWith("*"))
        return _jsx("span", { className: "mono small", children: domain });
    return (_jsx("a", { className: "mono small", href: `${ssl ? "https" : "http"}://${domain}`, target: "_blank", rel: "noreferrer", children: domain }));
}
const AUTH_OPTIONS = ["forward_auth", "oidc", "saml", "header", "access_list", "app", "none", "unknown"];
export function ApplicationForm({ initial, onSubmit, error, pending, submitLabel = "Opslaan", note, }) {
    const [name, setName] = useState(initial?.name ?? "");
    const [url, setUrl] = useState(initial?.url ?? "");
    const [appType, setAppType] = useState(initial?.app_type ?? "");
    const [auth, setAuth] = useState(initial?.auth_method ?? "unknown");
    const [tags, setTags] = useState((initial?.tags ?? []).join(", "));
    const [description, setDescription] = useState(initial?.description ?? "");
    const submit = (e) => {
        e.preventDefault();
        onSubmit({
            name,
            url: url.trim() || null,
            app_type: appType.trim() || null,
            auth_method: auth,
            tags: tags
                .split(",")
                .map((t) => t.trim())
                .filter(Boolean),
            description: description.trim() || null,
        });
    };
    return (_jsxs("form", { onSubmit: submit, className: "form", children: [note && _jsx("div", { className: "info-box small", children: note }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { required: true, value: name, onChange: (e) => setName(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "URL" }), _jsx("input", { type: "url", placeholder: "https://grafana.example.be", value: url, onChange: (e) => setUrl(e.target.value) })] }), _jsxs("div", { className: "grid-form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Aanmelding" }), _jsx("select", { value: auth, onChange: (e) => setAuth(e.target.value), children: AUTH_OPTIONS.map((a) => (_jsx("option", { value: a, children: authLabels[a] }, a))) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Type" }), _jsx("input", { placeholder: "grafana", value: appType, onChange: (e) => setAppType(e.target.value) })] })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Tags" }), _jsx("input", { placeholder: "monitoring, ops", value: tags, onChange: (e) => setTags(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Omschrijving" }), _jsx("textarea", { rows: 3, value: description, onChange: (e) => setDescription(e.target.value) })] }), _jsx(ErrorBox, { error: error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: pending, children: submitLabel }) })] }));
}
// Fase 4: kies de outpost waarop VaultX zelf providers zet. Leeg = VaultX maakt niets aan in Authentik.
function OutpostPicker({ orgId, value, onChange, }) {
    const outposts = useQuery({
        queryKey: ["authentik-outposts", orgId],
        queryFn: () => api.get(`/api/v1/organizations/${orgId}/authentik/outposts`),
        enabled: !!orgId,
        staleTime: 60_000,
    });
    const data = outposts.data;
    const known = data?.outposts ?? [];
    const selected = known.find((o) => o.pk === value);
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: "Authentik-kant automatisch aanmaken" }), _jsxs("select", { value: value, onChange: (e) => onChange(e.target.value), disabled: !data?.configured && !value, children: [_jsx("option", { value: "", children: "Nee, de provider maak ik zelf in Authentik" }), known.map((o) => (_jsxs("option", { value: o.pk, children: ["Ja, op outpost ", o.name] }, o.pk))), value && !selected && _jsxs("option", { value: value, children: ["Ja, op outpost ", value, " (niet gevonden)"] })] }), _jsx("small", { className: "muted", children: outposts.isLoading
                    ? "Outposts ophalen uit Authentik…"
                    : !data?.configured
                        ? "De Authentik-API is niet ingesteld op de VaultX-server (VAULTX_AUTHENTIK_API_TOKEN)."
                        : data.error
                            ? `Authentik: ${data.error}`
                            : "VaultX maakt dan per host een proxy provider (forward auth), een applicatie en de groepsbinding " +
                                "aan, en zet de provider op deze outpost. Bij weghalen ruimt het die weer op." }), selected && !selected.authentik_host && (_jsxs("small", { className: "warn-box small", children: ["Bij deze outpost is ", _jsx("code", { children: "authentik_host" }), " leeg. Vul in Authentik bij de outpost de publieke URL van Authentik in, anders weet de outpost niet waar een browser zich moet aanmelden."] }))] }));
}
export function ConnectionForm({ orgId, initial, onSubmit, error, pending, }) {
    const editing = !!initial;
    const [name, setName] = useState(initial?.name ?? "");
    const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? "");
    const [identity, setIdentity] = useState(initial?.identity ?? "");
    const [secret, setSecret] = useState("");
    const [verifyTls, setVerifyTls] = useState(initial?.verify_tls ?? true);
    const [enabled, setEnabled] = useState(initial?.enabled ?? true);
    const [writeEnabled, setWriteEnabled] = useState(initial?.write_enabled ?? false);
    const [outpost, setOutpost] = useState(initial?.authentik_outpost_url ?? "");
    const [probeHost, setProbeHost] = useState(initial?.probe_host ?? "");
    const [httpPort, setHttpPort] = useState(initial?.probe_http_port ?? 80);
    const [httpsPort, setHttpsPort] = useState(initial?.probe_https_port ?? 443);
    const [outpostPk, setOutpostPk] = useState(initial?.authentik_outpost_pk ?? "");
    const [theme, setTheme] = useState(initial?.theme_css_template ?? "");
    const submit = (e) => {
        e.preventDefault();
        onSubmit({
            name,
            base_url: baseUrl,
            identity,
            ...(secret ? { secret } : {}),
            verify_tls: verifyTls,
            enabled,
            // Leeg = wissen.
            write_enabled: writeEnabled,
            authentik_outpost_url: outpost.trim(),
            probe_host: probeHost.trim(),
            probe_http_port: httpPort,
            probe_https_port: httpsPort,
            authentik_outpost_pk: outpostPk,
            theme_css_template: theme.trim(),
        });
    };
    return (_jsxs("form", { onSubmit: submit, className: "form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { required: true, placeholder: "Homelab NPM", value: name, onChange: (e) => setName(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Beheer-URL van NPM" }), _jsx("input", { required: true, type: "url", placeholder: "http://npm.lan:81", value: baseUrl, onChange: (e) => setBaseUrl(e.target.value) }), _jsx("small", { className: "muted", children: "De poort van de NPM-beheerinterface (standaard 81), niet de proxypoort." })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "E-mailadres NPM-account" }), _jsx("input", { required: true, value: identity, onChange: (e) => setIdentity(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Wachtwoord" }), _jsx("input", { type: "password", required: !editing, autoComplete: "new-password", placeholder: editing ? "Ongewijzigd laten (opnieuw invullen bij een andere URL of gebruiker)" : "", value: secret, onChange: (e) => setSecret(e.target.value) }), _jsx("small", { className: "muted", children: "Wordt versleuteld opgeslagen en nooit teruggegeven. Gebruik een apart NPM-account zonder 2FA. Om te lezen volstaat \"Proxy Hosts: View\"; om Authentik-bescherming te zetten is \"Manage\" nodig." })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: verifyTls, onChange: (e) => setVerifyTls(e.target.checked) }), "TLS-certificaat controleren"] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: enabled, onChange: (e) => setEnabled(e.target.checked) }), "Automatisch synchroniseren"] }), _jsxs("fieldset", { className: "form-group", children: [_jsx("legend", { children: "Authentik-bescherming zetten" }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: writeEnabled, onChange: (e) => setWriteEnabled(e.target.checked) }), "VaultX mag proxy hosts in deze NPM wijzigen"] }), _jsx("small", { className: "muted", children: "Enkel op vraag van een beheerder, per host, met eerst een voorbeeld. Mislukt de controle achteraf, dan zet VaultX de vorige config terug." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Authentik-outpost, gezien vanuit NPM" }), _jsx("input", { placeholder: "http://authentik-server:9000", value: outpost, onChange: (e) => setOutpost(e.target.value) }), _jsx("small", { className: "muted", children: "Het adres waarop nginx in NPM de outpost bereikt, zonder pad. Voor de ingebouwde outpost is dat de Authentik-server zelf." })] }), _jsx(OutpostPicker, { orgId: orgId, value: outpostPk, onChange: setOutpostPk }), _jsxs("div", { className: "grid-form grid-probe", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Controleadres van NPM" }), _jsx("input", { placeholder: "host van de beheer-URL", value: probeHost, onChange: (e) => setProbeHost(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "HTTP-poort" }), _jsx("input", { type: "number", min: 1, max: 65535, value: httpPort, onChange: (e) => setHttpPort(Number(e.target.value)) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "HTTPS-poort" }), _jsx("input", { type: "number", min: 1, max: 65535, value: httpsPort, onChange: (e) => setHttpsPort(Number(e.target.value)) })] })] }), _jsx("small", { className: "muted", children: "Hier spreekt VaultX een host aan voor en na een wijziging, met de domeinnaam als Host-header. Leeg = de host van de beheer-URL." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "CSS-thema bij publiceren" }), _jsx("input", { placeholder: "https://css.example.be/{app}.css", value: theme, onChange: (e) => setTheme(e.target.value) }), _jsxs("small", { className: "muted", children: ["Optioneel. Bij \"App publiceren\" wordt ", _jsx("code", { children: "{app}" }), " de naam van de app (bv. proxmox-ve); het thema blijft per app aan te passen."] })] })] }), _jsx(ErrorBox, { error: error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: pending, children: editing ? "Opslaan" : "Koppelen" }) })] }));
}
