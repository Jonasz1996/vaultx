import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Fase 6: een nieuwe app in één stap online zetten via NPM (host, certificaat, Authentik, catalogus),
// en een app die VaultX publiceerde weer offline halen.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { AccessPicker, ChangeResult, renderConfig } from "./npmProtection";
import { ErrorBox, Loading, Modal } from "./ui";
function themeFor(template, name) {
    if (!template)
        return "";
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "app";
    return template.replaceAll("{app}", slug);
}
function covers(cert, domain) {
    const d = domain.trim().toLowerCase();
    return cert.domain_names.some((n) => {
        const name = n.toLowerCase();
        if (name === d)
            return true;
        return name.startsWith("*.") && d.endsWith(name.slice(1)) && !d.slice(0, -name.length + 1).includes(".");
    });
}
function Checks({ checks }) {
    if (!checks.length)
        return null;
    return (_jsx("ul", { className: "checks", children: checks.map((c) => (_jsx("li", { className: `check-${c.level}`, title: c.code, children: c.message }, c.code))) }));
}
function Steps({ title, steps }) {
    if (!steps.length)
        return null;
    return (_jsxs("div", { children: [_jsx("strong", { children: title }), _jsx("ol", { className: "steps", children: steps.map((s) => (_jsx("li", { children: s }, s))) })] }));
}
export function PublishDialog({ base, orgId, conn, onClose, onDone, }) {
    const certs = useQuery({
        queryKey: ["npm-certs", base],
        queryFn: () => api.get(`${base}/certificates`),
        staleTime: 30_000,
    });
    const [form, setForm] = useState({
        name: "",
        domain: "",
        forward_scheme: "http",
        forward_host: "",
        forward_port: 80,
        certificate_id: 0,
        ssl_forced: true,
        websockets: true,
        block_exploits: true,
        security_headers: true,
        theme_css_url: null,
        description: null,
        protect: true,
        access: "organization",
    });
    // Zolang de beheerder het thema niet zelf aanpaste, volgt het de naam van de app.
    const [themeTouched, setThemeTouched] = useState(false);
    const [certTouched, setCertTouched] = useState(false);
    const [verify, setVerify] = useState(true);
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const theme = themeTouched ? (form.theme_css_url ?? "") : themeFor(conn.theme_css_template, form.name);
    const autoCert = !certTouched && form.domain.includes(".")
        ? ((certs.data ?? []).find((c) => covers(c, form.domain))?.id ?? 0)
        : form.certificate_id;
    const request = {
        ...form,
        certificate_id: autoCert,
        theme_css_url: theme.trim() || null,
        description: form.description?.trim() || null,
    };
    const preview = useMutation({
        mutationFn: (r) => api.post(`${base}/publish/preview`, r),
    });
    const apply = useMutation({
        mutationFn: (r) => api.post(`${base}/publish`, { ...r, verify: verify && preview.data?.probe_url !== null }),
        onSettled: onDone,
    });
    const submit = (e) => {
        e.preventDefault();
        preview.mutate(request);
    };
    if (apply.data) {
        return (_jsxs(Modal, { title: `App publiceren: ${apply.data.domain}`, open: true, onClose: onClose, wide: true, children: [_jsx(ChangeResult, { change: apply.data }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", onClick: onClose, children: "Sluiten" }) })] }));
    }
    const plan = preview.data;
    if (plan) {
        const canVerify = plan.probe_url !== null;
        return (_jsx(Modal, { title: `App publiceren: ${plan.domain}`, open: true, onClose: onClose, wide: true, children: _jsxs("div", { className: "form", children: [_jsx(Checks, { checks: plan.checks }), _jsx(Steps, { title: plan.authentik ? "Wat VaultX in Authentik, NPM en de catalogus doet" : "Wat VaultX doet", steps: plan.steps }), plan.can_apply && (_jsxs("details", { children: [_jsx("summary", { children: "Config van de nieuwe host bekijken" }), _jsx("pre", { children: renderConfig(plan.host) })] })), plan.can_apply && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: verify && canVerify, disabled: !canVerify, onChange: (e) => setVerify(e.target.checked) }), _jsxs("span", { children: ["Controleren en bij een fout alles terugdraaien", " ", canVerify && _jsx("span", { className: "muted small mono", children: plan.probe_url })] })] })), _jsx(ErrorBox, { error: apply.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { className: "btn", onClick: () => preview.reset(), disabled: apply.isPending, children: "Terug" }), _jsx("button", { className: "btn btn-primary", disabled: !plan.can_apply || apply.isPending, onClick: () => apply.mutate(request), children: apply.isPending ? "Bezig, even geduld…" : "Publiceren" })] })] }) }));
    }
    return (_jsx(Modal, { title: "App publiceren", open: true, onClose: onClose, wide: true, children: _jsxs("form", { className: "form", onSubmit: submit, children: [_jsx("p", { className: "muted small", children: "VaultX maakt in NPM een nieuwe proxy host aan, beschermt hem met Authentik en zet de app in de catalogus. Je ziet eerst een voorbeeld." }), _jsxs("div", { className: "grid-form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { required: true, placeholder: "Proxmox VE", value: form.name, onChange: (e) => set("name", e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Domein" }), _jsx("input", { required: true, placeholder: "pve.example.be", value: form.domain, onChange: (e) => set("domain", e.target.value.trim()) })] })] }), _jsxs("div", { className: "grid-form grid-upstream", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Schema" }), _jsxs("select", { value: form.forward_scheme, onChange: (e) => set("forward_scheme", e.target.value), children: [_jsx("option", { value: "http", children: "http" }), _jsx("option", { value: "https", children: "https" })] })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "App (host of IP)" }), _jsx("input", { required: true, placeholder: "192.168.0.10", value: form.forward_host, onChange: (e) => set("forward_host", e.target.value.trim()) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Poort" }), _jsx("input", { required: true, type: "number", min: 1, max: 65535, value: form.forward_port, onChange: (e) => set("forward_port", Number(e.target.value)) })] })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Certificaat" }), _jsxs("select", { value: autoCert, onChange: (e) => {
                                setCertTouched(true);
                                set("certificate_id", Number(e.target.value));
                            }, children: [_jsx("option", { value: 0, children: "Geen (enkel http)" }), (certs.data ?? []).map((c) => (_jsxs("option", { value: c.id, children: [c.nice_name || c.domain_names.join(", "), form.domain && covers(c, form.domain) ? " ✓" : "", c.expires_on ? ` (tot ${c.expires_on.slice(0, 10)})` : ""] }, c.id)))] }), _jsx("small", { className: "muted", children: certs.isLoading
                                ? "Certificaten ophalen uit NPM…"
                                : certs.error
                                    ? "Certificaten niet op te halen (heeft het NPM-account 'Certificates: View'?)."
                                    : "Een certificaat dat al in NPM staat, bv. een wildcard. VaultX kiest er een dat het domein dekt." })] }), autoCert > 0 && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: form.ssl_forced, onChange: (e) => set("ssl_forced", e.target.checked) }), "TLS afdwingen (http naar https) en HSTS"] })), _jsxs("label", { className: "field", children: [_jsx("span", { children: "CSS-thema" }), _jsx("input", { placeholder: "https://css.example.be/app.css", value: theme, onChange: (e) => {
                                setThemeTouched(true);
                                set("theme_css_url", e.target.value);
                            } }), _jsx("small", { className: "muted", children: "Optioneel: een stylesheet die in elke pagina van de app komt (sub_filter)." })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Omschrijving" }), _jsx("input", { placeholder: "Optioneel, voor de catalogus", value: form.description ?? "", onChange: (e) => set("description", e.target.value) })] }), _jsxs("fieldset", { className: "form-group", children: [_jsx("legend", { children: "Toegang" }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: form.protect, onChange: (e) => set("protect", e.target.checked) }), "Beschermen met Authentik"] }), form.protect && conn.authentik_outpost_pk && (_jsx(AccessPicker, { orgId: orgId, value: form.access, onChange: (v) => set("access", v) })), form.protect && !conn.authentik_outpost_pk && (_jsx("small", { className: "muted", children: "Op deze koppeling staat geen outpost: VaultX maakt in Authentik niets aan. Er moet al een provider zijn die dit domein dekt." }))] }), _jsxs("details", { children: [_jsx("summary", { children: "Meer opties" }), _jsxs("div", { className: "stack", children: [_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: form.security_headers, onChange: (e) => set("security_headers", e.target.checked) }), "Beveiligingsheaders (X-Frame-Options, nosniff, Referrer-Policy, frame-ancestors)"] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: form.websockets, onChange: (e) => set("websockets", e.target.checked) }), "Websockets"] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: form.block_exploits, onChange: (e) => set("block_exploits", e.target.checked) }), "Block Common Exploits"] })] })] }), _jsx(ErrorBox, { error: preview.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { type: "button", className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { className: "btn btn-primary", disabled: preview.isPending, children: preview.isPending ? "Nakijken…" : "Voorbeeld" })] })] }) }));
}
export function UnpublishDialog({ base, host, onClose, onDone, }) {
    const url = `${base}/hosts/${host.id}/unpublish`;
    const plan = useQuery({
        queryKey: ["npm-unpublish", host.id],
        queryFn: () => api.get(url),
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
    });
    const apply = useMutation({ mutationFn: () => api.post(url, {}), onSettled: onDone });
    let body;
    if (apply.data) {
        body = (_jsxs(_Fragment, { children: [_jsx(ChangeResult, { change: apply.data }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", onClick: onClose, children: "Sluiten" }) })] }));
    }
    else if (plan.isLoading) {
        body = _jsx(Loading, {});
    }
    else if (!plan.data) {
        body = _jsx(ErrorBox, { error: plan.error });
    }
    else {
        const p = plan.data;
        body = (_jsxs("div", { className: "form", children: [_jsx(Checks, { checks: p.checks }), _jsx(Steps, { title: "Wat VaultX doet", steps: p.steps }), _jsx(ErrorBox, { error: apply.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { className: "btn btn-danger", disabled: !p.can_apply || apply.isPending, onClick: () => apply.mutate(), children: apply.isPending ? "Bezig…" : "Depubliceren" })] })] }));
    }
    return (_jsx(Modal, { title: `Depubliceren: ${host.domain_names[0]}`, open: true, onClose: onClose, wide: true, children: body }));
}
