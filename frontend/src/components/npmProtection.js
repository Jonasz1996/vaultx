import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { actionLabels, changeStatusInfo, formatDate, relative } from "../format";
import { Badge, Empty, ErrorBox, Loading, Modal } from "./ui";
export function ChangeStatusBadge({ status }) {
    const s = changeStatusInfo[status] ?? { label: status, tone: "neutral", hint: "" };
    return (_jsx(Badge, { tone: s.tone, title: s.hint, children: s.label }));
}
// Zoals de config in NPM staat: eerst Advanced van de host, dan elke custom location.
export function renderConfig(cfg) {
    const parts = [`# Advanced van de host\n${cfg.advanced_config?.trim() || "(leeg)"}`];
    for (const loc of (cfg.locations ?? [])) {
        const target = `${loc.forward_scheme}://${loc.forward_host}:${loc.forward_port}${loc.forward_path ?? ""}`;
        const advanced = String(loc.advanced_config ?? "").trim();
        parts.push(`# Custom location ${loc.path} -> ${target}\n${advanced || "(geen eigen config)"}`);
    }
    return parts.join("\n\n");
}
export function ConfigDiff({ before, after }) {
    return (_jsxs("div", { className: "config-diff", children: [_jsxs("div", { children: [_jsx("div", { className: "muted small", children: "Nu in NPM" }), _jsx("pre", { children: renderConfig(before) })] }), after && (_jsxs("div", { children: [_jsx("div", { className: "muted small", children: "Na de wijziging" }), _jsx("pre", { children: renderConfig(after) })] }))] }));
}
function ProbeLine({ label, probe }) {
    if (!probe)
        return null;
    return (_jsxs(_Fragment, { children: [_jsx("dt", { children: label }), _jsxs("dd", { children: [_jsx("span", { children: probe.summary }), _jsx("span", { className: "muted small mono", children: probe.url })] })] }));
}
function AuthentikLines({ ak }) {
    if (!ak)
        return null;
    const s = ak.state ?? ak.remove;
    if (!s)
        return null;
    const made = [
        s.provider_name && `provider '${s.provider_name}'${s.provider_created ? " (door VaultX)" : ""}`,
        s.application_slug && `applicatie ${s.application_slug}${s.application_created ? " (door VaultX)" : ""}`,
        s.outpost_name && `outpost '${s.outpost_name}'`,
    ].filter(Boolean);
    let outcome = null;
    if (ak.undo_error)
        outcome = `terugdraaien mislukt: ${ak.undo_error}`;
    else if (ak.cleanup_error)
        outcome = `opruimen mislukt: ${ak.cleanup_error}`;
    else if (ak.undone)
        outcome = "teruggedraaid";
    else if (ak.removed)
        outcome = "opgeruimd";
    return (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Authentik" }), _jsxs("dd", { children: [_jsx("span", { children: made.join(", ") || "—" }), s.groups.length > 0 && _jsxs("span", { className: "muted small", children: ["toegang: ", s.groups.join(", ")] }), outcome && _jsx("span", { className: "muted small", children: outcome })] })] }));
}
export function ChangeResult({ change }) {
    const box = change.status === "applied" ? "good-box" : change.status === "refused" ? "info-box" : "warn-box";
    return (_jsxs("div", { className: "form", children: [_jsxs("div", { className: change.status === "rollback_failed" ? "error-box" : box, children: [_jsx(ChangeStatusBadge, { status: change.status }), " ", change.message] }), _jsxs("dl", { className: "kv", children: [_jsx(ProbeLine, { label: "Controle vooraf", probe: change.probe_before }), _jsx(ProbeLine, { label: "Controle achteraf", probe: change.probe_after }), _jsx(AuthentikLines, { ak: change.authentik }), change.nginx_error && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "nginx-fout" }), _jsx("dd", { className: "mono small", children: change.nginx_error })] }))] }), change.status === "rollback_failed" && (_jsxs("details", { open: true, children: [_jsx("summary", { children: "Config om met de hand terug te zetten in NPM" }), _jsx(ConfigDiff, { before: change.before, after: null })] }))] }));
}
// Toegang tot de Authentik-applicatie die VaultX aanmaakt (fase 4, ook gebruikt in fase 5).
export function AccessPicker({ orgId, value, onChange, label = "Wie mag de applicatie openen na de Authentik-aanmelding?", }) {
    const teams = useQuery({
        queryKey: ["teams", orgId],
        queryFn: () => api.get(`/api/v1/organizations/${orgId}/teams`),
    });
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: label }), _jsxs("select", { value: value, onChange: (e) => onChange(e.target.value), children: [_jsx("option", { value: "organization", children: "Leden van deze organisatie" }), (teams.data ?? []).map((t) => (_jsxs("option", { value: `team:${t.slug}`, children: ["Leden van team ", t.name] }, t.id))), _jsx("option", { value: "all", children: "Alle Authentik-gebruikers" })] }), _jsxs("small", { className: "muted", children: ["VaultX bindt de Authentik-groepen volgens de groepconventie (bv. ", _jsx("code", { children: "vaultx:organisatie" }), " of", " ", _jsx("code", { children: "vaultx:organisatie/team" }), ") aan de applicatie."] })] }));
}
export function ProtectionDialog({ base, orgId, authentik, host, action, onClose, onDone, }) {
    const url = `${base}/hosts/${host.id}/protection`;
    const [access, setAccess] = useState("organization");
    const plan = useQuery({
        queryKey: ["npm-plan", host.id, action, access],
        queryFn: () => api.get(url, { action, access }),
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
    });
    const [verify, setVerify] = useState(true);
    const apply = useMutation({
        mutationFn: (p) => api.post(url, {
            action,
            expected_modified_on: p.modified_on,
            verify: verify && p.probe_url !== null,
            access,
        }),
        onSettled: onDone,
    });
    const title = `${actionLabels[action]}: ${host.domain_names[0]}`;
    let body;
    if (apply.data) {
        body = (_jsxs(_Fragment, { children: [_jsx(ChangeResult, { change: apply.data }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", onClick: onClose, children: "Sluiten" }) })] }));
    }
    else if (plan.isLoading) {
        body = (_jsxs(_Fragment, { children: [action === "protect" && authentik && _jsx(AccessPicker, { orgId: orgId, value: access, onChange: setAccess }), _jsx(Loading, {})] }));
    }
    else if (!plan.data) {
        body = _jsx(ErrorBox, { error: plan.error });
    }
    else {
        const p = plan.data;
        const canVerify = p.probe_url !== null;
        body = (_jsxs("div", { className: "form", children: [action === "protect" && authentik && _jsx(AccessPicker, { orgId: orgId, value: access, onChange: setAccess }), p.checks.length > 0 && (_jsx("ul", { className: "checks", children: p.checks.map((c) => (_jsx("li", { className: `check-${c.level}`, title: c.code, children: c.message }, c.code))) })), p.steps.length > 0 && (_jsxs("div", { children: [_jsx("strong", { children: p.authentik ? "Wat VaultX in Authentik en NPM wijzigt" : "Wat VaultX in NPM wijzigt" }), _jsx("ol", { className: "steps", children: p.steps.map((s) => (_jsx("li", { children: s }, s))) })] })), p.can_apply && (_jsxs("details", { children: [_jsx("summary", { children: "Config ervoor en erna bekijken" }), _jsx(ConfigDiff, { before: p.before, after: p.after })] })), p.can_apply && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: verify && canVerify, disabled: !canVerify, onChange: (e) => setVerify(e.target.checked) }), _jsxs("span", { children: ["Controleren en bij een fout terugzetten", " ", canVerify ? (_jsx("span", { className: "muted small mono", children: p.probe_url })) : (_jsx("span", { className: "muted small", children: "(niet mogelijk voor deze host)" }))] })] })), p.can_apply && !(verify && canVerify) && (_jsx("div", { className: "warn-box small", children: "Zonder controle zet VaultX enkel terug als nginx de config weigert. Of de host daarna echt naar Authentik doorverwijst, kijk je zelf na." })), _jsx(ErrorBox, { error: apply.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { className: action === "protect" ? "btn btn-primary" : "btn btn-danger", disabled: !p.can_apply || apply.isPending, onClick: () => apply.mutate(p), children: apply.isPending ? "Bezig, even geduld…" : action === "protect" ? "Bescherming zetten" : "Weghalen" })] })] }));
    }
    return (_jsx(Modal, { title: title, open: true, onClose: onClose, wide: true, children: body }));
}
export function ChangeJournal({ base }) {
    const changes = useQuery({
        queryKey: ["npm-changes", base],
        queryFn: () => api.get(`${base}/changes`),
    });
    const [open, setOpen] = useState(null);
    if (changes.isLoading)
        return _jsx(Loading, {});
    if (changes.error)
        return _jsx(ErrorBox, { error: changes.error });
    if (!changes.data?.length)
        return _jsx(Empty, { children: "VaultX heeft nog niets gewijzigd in deze NPM." });
    return (_jsxs("div", { className: "table-wrap", children: [_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Wanneer" }), _jsx("th", { children: "Host" }), _jsx("th", { children: "Wijziging" }), _jsx("th", { children: "Resultaat" }), _jsx("th", { children: "Door" })] }) }), _jsx("tbody", { children: changes.data.map((c) => (_jsxs("tr", { children: [_jsx("td", { className: "nowrap", title: formatDate(c.created_at), children: relative(c.created_at) }), _jsx("td", { className: "mono small", children: c.domain }), _jsxs("td", { children: [actionLabels[c.action], !c.verified && (_jsx("div", { children: _jsx(Badge, { tone: "warn", children: "zonder controle" }) }))] }), _jsxs("td", { children: [_jsx(ChangeStatusBadge, { status: c.status }), _jsx("div", { className: "small", children: c.message }), _jsx("button", { className: "btn btn-ghost small", onClick: () => setOpen(c), children: "Details" })] }), _jsx("td", { className: "small", children: c.actor_label ?? "—" })] }, c.id))) })] }), _jsx(Modal, { title: open ? `${actionLabels[open.action]}: ${open.domain}` : "", open: open !== null, onClose: () => setOpen(null), wide: true, children: open && (_jsxs("div", { className: "form", children: [_jsx(ChangeResult, { change: open }), open.status !== "rollback_failed" && (_jsx(ConfigDiff, { before: open.before, after: open.after?.object ?? open.after })), _jsxs("div", { className: "muted small", children: [formatDate(open.created_at), " door ", open.actor_label ?? "?", open.finished_at && `, klaar ${formatDate(open.finished_at)}`] })] })) })] }));
}
