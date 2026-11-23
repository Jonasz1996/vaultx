import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// NPM volledig beheren: proxy hosts, redirects, 404-hosts, streams, access lists en certificaten.
// Elke wijziging gaat via een voorbeeld (wat VaultX zou doen, met controles) en wordt daarna
// uitgevoerd met controle en rollback door de backend (services/npm_manage.py).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import { ChangeStatusBadge } from "../components/npmProtection";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative } from "../format";
const KINDS = [
    { key: "proxy_host", tab: "Proxy hosts", one: "proxy host", nieuw: "Nieuwe proxy host" },
    { key: "redirection_host", tab: "Redirects", one: "redirection host", nieuw: "Nieuwe redirect" },
    { key: "dead_host", tab: "404-hosts", one: "404-host", nieuw: "Nieuwe 404-host" },
    { key: "stream", tab: "Streams", one: "stream", nieuw: "Nieuwe stream" },
    { key: "access_list", tab: "Access lists", one: "access list", nieuw: "Nieuwe access list" },
    { key: "certificate", tab: "Certificaten", one: "certificaat", nieuw: "Nieuw certificaat" },
];
const kindInfo = (k) => KINDS.find((x) => x.key === k);
export const manageActionLabels = {
    create: "Aanmaken",
    update: "Bewerken",
    delete: "Verwijderen",
    enable: "Aanzetten",
    disable: "Uitzetten",
    renew: "Vernieuwen",
    upload: "Nieuw bestand",
};
const FIELD_LABELS = {
    domain_names: "Domeinnamen",
    forward_scheme: "Schema",
    forward_host: "Doelhost",
    forward_port: "Doelpoort",
    certificate_id: "Certificaat",
    ssl_forced: "Force SSL",
    hsts_enabled: "HSTS",
    hsts_subdomains: "HSTS subdomeinen",
    trust_forwarded_proto: "X-Forwarded-Proto vertrouwen",
    http2_support: "HTTP/2",
    block_exploits: "Block common exploits",
    caching_enabled: "Cache",
    allow_websocket_upgrade: "Websockets",
    access_list_id: "Access list",
    advanced_config: "Advanced",
    locations: "Custom locations",
    forward_http_code: "HTTP-code",
    forward_domain_name: "Doeldomein",
    preserve_path: "Pad behouden",
    incoming_port: "Inkomende poort",
    forwarding_host: "Doelhost",
    forwarding_port: "Doelpoort",
    tcp_forwarding: "TCP",
    udp_forwarding: "UDP",
    name: "Naam",
    satisfy_any: "Satisfy Any",
    pass_auth: "Auth doorgeven",
    items: "Gebruikers",
    clients: "Adressen",
    provider: "Soort",
    nice_name: "Naam",
    meta: "Opties",
};
// ---------------------------------------------------------------- pagina
export function NpmManagePage() {
    const { orgId = "", connId = "" } = useParams();
    const { data: me } = useMe();
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const tab = (params.get("tab") ?? "proxy_host");
    const base = `/api/v1/organizations/${orgId}/npm-connections/${connId}`;
    const mbase = `${base}/manage`;
    const manage = canManageOrg(me, orgId);
    const [editor, setEditor] = useState(null);
    const conn = useQuery({ queryKey: ["npm-conn", connId], queryFn: () => api.get(base) });
    const overview = useQuery({
        queryKey: ["npm-manage", connId],
        queryFn: () => api.get(mbase),
        enabled: manage,
        refetchOnWindowFocus: false,
    });
    const refresh = () => {
        void qc.invalidateQueries({ queryKey: ["npm-manage", connId] });
        void qc.invalidateQueries({ queryKey: ["npm-manage-changes", connId] });
        void qc.invalidateQueries({ queryKey: ["npm-hosts", connId] });
        void qc.invalidateQueries({ queryKey: ["npm-changes", base] });
        void qc.invalidateQueries({ queryKey: ["catalog"] });
    };
    if (!manage)
        return (_jsx("div", { className: "content", children: _jsx(ErrorBox, { error: new Error("Enkel beheerders van de organisatie kunnen NPM beheren.") }) }));
    if (conn.isLoading)
        return _jsx(Loading, {});
    if (!conn.data)
        return _jsx(ErrorBox, { error: conn.error });
    const c = conn.data;
    const ov = overview.data;
    const write = c.write_enabled;
    const open = (state) => setEditor(state);
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: `NPM beheren: ${c.name}`, subtitle: _jsxs(_Fragment, { children: [_jsx(Link, { to: "/npm", children: "NPM" }), " \u00B7 ", _jsx(Link, { to: `/npm/${orgId}/${connId}`, children: c.name }), " \u00B7", " ", _jsx("span", { className: "mono", children: c.base_url })] }), actions: _jsx("button", { className: "btn", onClick: refresh, disabled: overview.isFetching, children: overview.isFetching ? "Bezig…" : "Vernieuwen" }) }), !write && (_jsxs("div", { className: "warn-box", children: ["Alleen lezen: zet ", _jsx("strong", { children: "VaultX mag wijzigen" }), " aan op de ", _jsx(Link, { to: `/npm/${orgId}/${connId}`, children: "koppeling" }), ". Het NPM-account heeft dan ", _jsx("em", { children: "Manage" }), " nodig op hosts, streams, access lists en certificaten."] })), _jsxs("div", { className: "info-box small", children: ["Elke wijziging toont eerst een voorbeeld met controles. VaultX schrijft via de NPM-API, kijkt na of nginx de config aanvaardt en of de host nog antwoordt zoals verwacht, en zet anders terug. Blokken van VaultX en regels met Authentik, ", _jsx("code", { children: "sub_filter" }), " (CSS-thema) of ", _jsx("code", { children: "add_header" }), " in Advanced blijven bewaard."] }), _jsxs("nav", { className: "page-tabs manage-tabs", children: [KINDS.map((k) => (_jsxs("a", { href: `?tab=${k.key}`, className: tab === k.key ? "active" : undefined, onClick: (e) => {
                            e.preventDefault();
                            setParams({ tab: k.key });
                        }, children: [k.tab, ov && _jsxs("span", { className: "muted small", children: [" ", ov[k.key].length] })] }, k.key))), _jsx("a", { href: "?tab=journal", className: tab === "journal" ? "active" : undefined, onClick: (e) => {
                            e.preventDefault();
                            setParams({ tab: "journal" });
                        }, children: "Journaal" })] }), _jsx(ErrorBox, { error: overview.error }), tab === "journal" ? (_jsx(Card, { title: "Wijzigingen via NPM beheren", children: _jsx(ManageJournal, { mbase: mbase, connId: connId, onRestore: write
                        ? (ch) => open({
                            kind: ch.object_type,
                            action: "create",
                            initial: restoreData(ch),
                            title: `${kindInfo(ch.object_type).one} opnieuw aanmaken`,
                        })
                        : undefined }) })) : overview.isLoading ? (_jsx(Loading, {})) : ov ? (_jsx(Card, { title: kindInfo(tab).tab, actions: write && (_jsx("button", { className: "btn btn-primary", onClick: () => open({ kind: tab, action: "create", initial: {} }), children: kindInfo(tab).nieuw })), children: _jsx(KindTable, { kind: tab, ov: ov, write: write, open: open }) })) : null, editor && ov && (_jsx(ManageDialog, { mbase: mbase, state: editor, ov: ov, onClose: () => setEditor(null), onDone: refresh }))] }));
}
function restoreData(ch) {
    const data = { ...ch.before };
    if (ch.object_type === "access_list" && Array.isArray(data.items))
        data.items = data.items.map((i) => ({ username: i.username, password: "" }));
    return data;
}
function certName(ov, id) {
    if (!id)
        return null;
    return ov.certificate.find((c) => c.id === id)?.label ?? `#${String(id)}`;
}
function StatusBadges({ r }) {
    return (_jsxs(_Fragment, { children: [r.enabled === false && _jsx(Badge, { tone: "bad", children: "uit" }), r.nginx_online === false && (_jsx(Badge, { tone: "bad", title: r.nginx_err ?? "", children: "nginx-fout" })), r.vaultx_published && _jsx(Badge, { tone: "info", children: "gepubliceerd door VaultX" }), r.vaultx_protected && _jsx(Badge, { tone: "info", children: "Authentik door VaultX" }), (r.managed_blocks ?? []).map((b) => (_jsxs(Badge, { tone: "info", title: "Beheerd blok in Advanced; VaultX laat het ongemoeid", children: ["vaultx:", b] }, b)))] }));
}
function SslCell({ r, ov }) {
    const name = certName(ov, r.certificate_id);
    if (!name)
        return _jsx(Badge, { tone: "warn", children: "geen TLS" });
    return (_jsxs("div", { className: "stack", children: [_jsx("span", { className: "small", children: name }), _jsxs("div", { className: "row wrap", children: [r.ssl_forced ? _jsx(Badge, { tone: "good", children: "Force SSL" }) : null, r.http2_support ? _jsx(Badge, { children: "HTTP/2" }) : null, r.hsts_enabled ? _jsxs(Badge, { children: ["HSTS", r.hsts_subdomains ? "+sub" : ""] }) : null] })] }));
}
function RowActions({ kind, r, write, open, }) {
    if (!write)
        return null;
    const toggles = kind !== "access_list" && kind !== "certificate";
    return (_jsxs("div", { className: "row wrap", children: [kind !== "certificate" && (_jsx("button", { className: "btn btn-ghost small", onClick: () => open({ kind, action: "update", row: r, initial: r }), children: "Bewerken" })), kind === "certificate" && r.provider === "letsencrypt" && (_jsx("button", { className: "btn btn-ghost small", onClick: () => open({ kind, action: "renew", row: r, initial: {} }), children: "Vernieuwen" })), kind === "certificate" && r.provider === "other" && (_jsx("button", { className: "btn btn-ghost small", onClick: () => open({ kind, action: "upload", row: r, initial: {} }), children: "Nieuw bestand" })), toggles && (_jsx("button", { className: "btn btn-ghost small", onClick: () => open({ kind, action: r.enabled === false ? "enable" : "disable", row: r, initial: {} }), children: r.enabled === false ? "Aanzetten" : "Uitzetten" })), _jsx("button", { className: "btn btn-ghost small btn-danger-text", onClick: () => open({ kind, action: "delete", row: r, initial: {} }), children: "Verwijderen" })] }));
}
function Domains({ r }) {
    return (_jsx("div", { className: "stack", children: (r.domain_names ?? []).map((d) => (_jsx("span", { className: "mono small", children: d }, d))) }));
}
function KindTable({ kind, ov, write, open, }) {
    const rows = ov[kind];
    if (!rows.length)
        return _jsx(Empty, { children: "Nog niets in NPM." });
    const actions = (r) => _jsx(RowActions, { kind: kind, r: r, write: write, open: open });
    let head;
    let body;
    if (kind === "proxy_host") {
        head = ["Domeinen", "Doel", "TLS", "Toegang", "Opties", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsxs("td", { children: [_jsx(Domains, { r: r }), _jsxs("div", { className: "row wrap small", children: [_jsxs("span", { className: "muted", children: ["#", r.id] }), _jsx(StatusBadges, { r: r })] })] }), _jsxs("td", { className: "mono small", children: [String(r.forward_scheme), "://", String(r.forward_host), ":", String(r.forward_port), Array.isArray(r.locations) && r.locations.length > 0 && (_jsxs("div", { className: "muted", children: ["+ ", r.locations.length, " custom location(s)"] }))] }), _jsx("td", { children: _jsx(SslCell, { r: r, ov: ov }) }), _jsx("td", { className: "small", children: r.access_list_id ? ((ov.access_list.find((a) => a.id === r.access_list_id)?.label ?? `#${String(r.access_list_id)}`)) : (_jsx("span", { className: "muted", children: "openbaar" })) }), _jsx("td", { children: _jsxs("div", { className: "row wrap", children: [r.allow_websocket_upgrade ? _jsx(Badge, { children: "websockets" }) : null, r.block_exploits ? _jsx(Badge, { children: "exploits" }) : null, r.caching_enabled ? _jsx(Badge, { children: "cache" }) : null, r.advanced_config ? _jsx(Badge, { tone: "info", children: "Advanced" }) : null] }) }), _jsx("td", { children: actions(r) })] }));
    }
    else if (kind === "redirection_host") {
        head = ["Domeinen", "Doel", "TLS", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsxs("td", { children: [_jsx(Domains, { r: r }), _jsx("div", { className: "row wrap small", children: _jsx(StatusBadges, { r: r }) })] }), _jsxs("td", { className: "small", children: [_jsxs("span", { className: "mono", children: [String(r.forward_http_code), " \u2192 ", r.forward_scheme === "auto" ? "" : `${String(r.forward_scheme)}://`, String(r.forward_domain_name)] }), r.preserve_path ? _jsx("div", { className: "muted", children: "pad behouden" }) : null] }), _jsx("td", { children: _jsx(SslCell, { r: r, ov: ov }) }), _jsx("td", { children: actions(r) })] }));
    }
    else if (kind === "dead_host") {
        head = ["Domeinen", "TLS", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsxs("td", { children: [_jsx(Domains, { r: r }), _jsx("div", { className: "row wrap small", children: _jsx(StatusBadges, { r: r }) })] }), _jsx("td", { children: _jsx(SslCell, { r: r, ov: ov }) }), _jsx("td", { children: actions(r) })] }));
    }
    else if (kind === "stream") {
        head = ["Inkomend", "Doel", "Protocol", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsxs("td", { className: "mono", children: [":", String(r.incoming_port), _jsx("div", { className: "row wrap small", children: _jsx(StatusBadges, { r: r }) })] }), _jsxs("td", { className: "mono small", children: [String(r.forwarding_host), ":", String(r.forwarding_port)] }), _jsxs("td", { children: [r.tcp_forwarding ? _jsx(Badge, { children: "TCP" }) : null, r.udp_forwarding ? _jsx(Badge, { children: "UDP" }) : null] }), _jsx("td", { children: actions(r) })] }));
    }
    else if (kind === "access_list") {
        head = ["Naam", "Gebruikers", "Adressen", "In gebruik door", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsxs("td", { children: [_jsx("strong", { children: r.label }), _jsxs("div", { className: "row wrap small", children: [r.satisfy_any ? _jsx(Badge, { tone: "warn", children: "Satisfy Any" }) : _jsx(Badge, { children: "Satisfy All" }), r.pass_auth ? _jsx(Badge, { children: "auth doorgeven" }) : null] })] }), _jsx("td", { className: "small", children: (r.items ?? []).map((i) => String(i.username)).join(", ") || "—" }), _jsx("td", { className: "small mono", children: (r.clients ?? []).map((cl) => `${String(cl.directive)} ${String(cl.address)}`).join(", ") ||
                        "—" }), _jsx("td", { className: "small", children: (r.used_by ?? []).join(", ") || _jsx("span", { className: "muted", children: "niets" }) }), _jsx("td", { children: actions(r) })] }));
    }
    else {
        head = ["Naam", "Domeinen", "Soort", "Vervalt", "In gebruik door", ""];
        body = (r) => (_jsxs(_Fragment, { children: [_jsx("td", { children: _jsx("strong", { children: r.label }) }), _jsx("td", { children: _jsx(Domains, { r: r }) }), _jsx("td", { children: r.provider === "letsencrypt" ? "Let's Encrypt" : "eigen certificaat" }), _jsx("td", { className: "small", children: _jsx(Expiry, { value: r.expires_on }) }), _jsx("td", { className: "small", children: (r.used_by ?? []).join(", ") || _jsx("span", { className: "muted", children: "niets" }) }), _jsx("td", { children: actions(r) })] }));
    }
    return (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsx("tr", { children: head.map((h, i) => (_jsx("th", { children: h }, i))) }) }), _jsx("tbody", { children: rows.map((r) => (_jsx("tr", { className: r.enabled === false ? "dim" : undefined, children: body(r) }, r.id))) })] }) }));
}
function Expiry({ value }) {
    if (!value)
        return _jsx("span", { className: "muted", children: "\u2014" });
    const days = Math.round((new Date(value.replace(" ", "T") + "Z").getTime() - Date.now()) / 86_400_000);
    const tone = days < 0 ? "bad" : days < 21 ? "warn" : "good";
    return (_jsxs(_Fragment, { children: [value.slice(0, 10), " ", _jsx(Badge, { tone: tone, children: days < 0 ? "vervallen" : `nog ${days} dagen` })] }));
}
// ---------------------------------------------------------------- dialoog: formulier → voorbeeld → resultaat
function ManageDialog({ mbase, state, ov, onClose, onDone, }) {
    const { kind, action, row } = state;
    const needsForm = action === "create" || action === "update" || action === "upload";
    const [data, setData] = useState(() => formData(kind, action, state.initial));
    const [step, setStep] = useState(needsForm ? "form" : "preview");
    const [confirmRemovals, setConfirmRemovals] = useState(false);
    const [verify, setVerify] = useState(true);
    const request = () => ({
        action,
        npm_id: row?.id ?? null,
        data: needsForm ? cleanData(kind, action, data) : {},
        confirm_removals: confirmRemovals,
    });
    const plan = useQuery({
        queryKey: ["npm-manage-plan", kind, action, row?.id, JSON.stringify(request())],
        queryFn: () => api.post(`${mbase}/${kind}/preview`, request()),
        enabled: step === "preview",
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
        retry: false,
    });
    const apply = useMutation({
        mutationFn: (p) => api.post(`${mbase}/${kind}/apply`, {
            ...request(),
            expected_modified_on: p.modified_on,
            verify: verify && p.probe_url !== null,
        }),
        onSettled: onDone,
    });
    const info = kindInfo(kind);
    const title = state.title ??
        (action === "create" ? info.nieuw : `${manageActionLabels[action]}: ${row?.label ?? info.one}`);
    let body;
    if (apply.data) {
        body = (_jsxs(_Fragment, { children: [_jsx(ManageResult, { change: apply.data }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn", onClick: onClose, children: "Sluiten" }) })] }));
    }
    else if (step === "form") {
        body = (_jsxs("form", { className: "form", onSubmit: (e) => {
                e.preventDefault();
                setStep("preview");
            }, children: [_jsx(KindForm, { kind: kind, action: action, data: data, set: setData, ov: ov, row: row, mbase: mbase }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { type: "button", className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { type: "submit", className: "btn btn-primary", children: "Voorbeeld bekijken" })] })] }));
    }
    else if (plan.isLoading) {
        body = _jsx(Loading, {});
    }
    else if (!plan.data) {
        body = (_jsxs(_Fragment, { children: [_jsx(ErrorBox, { error: plan.error }), _jsxs("div", { className: "form-actions row", children: [needsForm && (_jsx("button", { className: "btn", onClick: () => setStep("form"), children: "Terug naar formulier" })), _jsx("button", { className: "btn", onClick: onClose, children: "Sluiten" })] })] }));
    }
    else {
        const p = plan.data;
        const canVerify = p.probe_url !== null;
        const removals = p.checks.some((c) => c.code.startsWith("removes_"));
        body = (_jsxs("div", { className: "form", children: [p.checks.length > 0 && (_jsx("ul", { className: "checks", children: p.checks.map((c, i) => (_jsx("li", { className: `check-${c.level}`, title: c.code, children: c.message }, `${c.code}-${i}`))) })), removals && (_jsxs("label", { className: "check warn-box", children: [_jsx("input", { type: "checkbox", checked: confirmRemovals, onChange: (e) => setConfirmRemovals(e.target.checked) }), _jsx("span", { children: "Beschermde regels mogen weg: ik haal bewust Authentik, het CSS-thema of headers uit Advanced." })] })), p.steps.length > 0 && (_jsxs("div", { children: [_jsx("strong", { children: "Wat VaultX in NPM doet" }), _jsx("ol", { className: "steps", children: p.steps.map((s) => (_jsx("li", { children: s }, s))) })] })), p.certificate_check && (_jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Certificaat" }), _jsx("dd", { children: p.certificate_check.cn }), _jsx("dt", { children: "Uitgever" }), _jsx("dd", { children: p.certificate_check.issuer }), _jsx("dt", { children: "Geldig tot" }), _jsx("dd", { children: p.certificate_check.valid_to })] })), p.changes.length > 0 && _jsx(ChangesTable, { changes: p.changes }), p.can_apply && p.expect && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: verify && canVerify, disabled: !canVerify, onChange: (e) => setVerify(e.target.checked) }), _jsxs("span", { children: ["Controleren (verwacht: ", p.expect, ") en bij een fout terugzetten", " ", canVerify ? (_jsx("span", { className: "muted small mono", children: p.probe_url })) : (_jsx("span", { className: "muted small", children: "(niet mogelijk voor deze host)" }))] })] })), _jsx(ErrorBox, { error: apply.error }), _jsxs("div", { className: "form-actions row", children: [needsForm && (_jsx("button", { className: "btn", onClick: () => setStep("form"), disabled: apply.isPending, children: "Terug naar formulier" })), _jsx("button", { className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { className: action === "delete" || action === "disable" ? "btn btn-danger" : "btn btn-primary", disabled: !p.can_apply || apply.isPending, onClick: () => apply.mutate(p), children: apply.isPending ? "Bezig, even geduld…" : action === "update" ? "Opslaan" : manageActionLabels[action] })] })] }));
    }
    return (_jsx(Modal, { title: title, open: true, onClose: onClose, wide: true, children: body }));
}
function show(v) {
    if (v === null || v === undefined || v === "")
        return "—";
    if (typeof v === "boolean")
        return v ? "aan" : "uit";
    if (typeof v === "string")
        return v;
    return JSON.stringify(v, null, 2);
}
function ChangesTable({ changes }) {
    return (_jsx("div", { className: "table-wrap", children: _jsxs("table", { className: "changes-table", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Veld" }), _jsx("th", { children: "Nu in NPM" }), _jsx("th", { children: "Na de wijziging" })] }) }), _jsx("tbody", { children: changes.map((c) => (_jsxs("tr", { children: [_jsx("td", { children: FIELD_LABELS[c.field] ?? c.field }), _jsx("td", { children: _jsx("pre", { className: "cell-pre", children: show(c.before) }) }), _jsx("td", { children: _jsx("pre", { className: "cell-pre", children: show(c.after) }) })] }, c.field))) })] }) }));
}
export function ManageResult({ change }) {
    const box = change.status === "applied" ? "good-box" : change.status === "refused" ? "info-box" : "warn-box";
    const probe = (label, p) => p && (_jsxs(_Fragment, { children: [_jsx("dt", { children: label }), _jsxs("dd", { children: [_jsx("span", { children: p.summary }), _jsx("span", { className: "muted small mono", children: p.url })] })] }));
    return (_jsxs("div", { className: "form", children: [_jsxs("div", { className: change.status === "rollback_failed" ? "error-box" : box, children: [_jsx(ChangeStatusBadge, { status: change.status }), " ", change.message] }), _jsxs("dl", { className: "kv", children: [probe("Controle vooraf", change.probe_before), probe("Controle achteraf", change.probe_after), change.nginx_error && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "nginx-fout" }), _jsx("dd", { className: "mono small", children: change.nginx_error })] }))] })] }));
}
// ---------------------------------------------------------------- formulieren
const lines = (v) => String(v ?? "")
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
function formData(kind, action, initial) {
    const d = { ...initial };
    if (kind === "access_list") {
        d.items = (initial.items ?? []).map((i) => ({ username: i.username, password: "" }));
        d.clients = (initial.clients ?? []).map((c) => ({ address: c.address, directive: c.directive }));
    }
    if (kind === "proxy_host")
        d.locations = (initial.locations ?? []).map((l) => ({
            id: l.id,
            path: l.path,
            forward_scheme: l.forward_scheme ?? "http",
            forward_host: l.forward_host ?? "",
            forward_port: l.forward_port ?? 80,
            forward_path: l.forward_path ?? "",
            advanced_config: l.advanced_config ?? "",
        }));
    if (Array.isArray(initial.domain_names))
        d.domain_names_text = initial.domain_names.join("\n");
    if (action === "create" && Object.keys(initial).length === 0) {
        if (kind === "proxy_host")
            Object.assign(d, { forward_scheme: "http", block_exploits: true, allow_websocket_upgrade: true });
        if (kind === "redirection_host")
            Object.assign(d, { forward_scheme: "auto", forward_http_code: 301, preserve_path: true, block_exploits: true });
        if (kind === "stream")
            Object.assign(d, { tcp_forwarding: true });
        if (kind === "certificate")
            Object.assign(d, { provider: "letsencrypt", key_type: "rsa" });
    }
    return d;
}
const FIELDS = {
    proxy_host: [
        "forward_scheme",
        "forward_host",
        "forward_port",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "trust_forwarded_proto",
        "http2_support",
        "block_exploits",
        "caching_enabled",
        "allow_websocket_upgrade",
        "access_list_id",
        "advanced_config",
        "locations",
    ],
    redirection_host: [
        "forward_scheme",
        "forward_http_code",
        "forward_domain_name",
        "preserve_path",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "http2_support",
        "block_exploits",
        "advanced_config",
    ],
    dead_host: ["certificate_id", "ssl_forced", "hsts_enabled", "hsts_subdomains", "http2_support", "advanced_config"],
    stream: ["incoming_port", "forwarding_host", "forwarding_port", "tcp_forwarding", "udp_forwarding"],
    access_list: ["name", "satisfy_any", "pass_auth", "items", "clients"],
    certificate: [
        "provider",
        "nice_name",
        "dns_challenge",
        "dns_provider",
        "dns_provider_credentials",
        "propagation_seconds",
        "key_type",
        "certificate",
        "certificate_key",
        "intermediate_certificate",
    ],
};
const NUMBERS = new Set([
    "forward_port",
    "forward_http_code",
    "incoming_port",
    "forwarding_port",
    "certificate_id",
    "access_list_id",
    "propagation_seconds",
]);
function cleanData(kind, action, d) {
    const out = {};
    for (const f of FIELDS[kind]) {
        if (!(f in d) || d[f] === undefined)
            continue;
        let v = d[f];
        if (NUMBERS.has(f)) {
            if (v === "" || v === null) {
                if (f === "certificate_id" || f === "access_list_id")
                    v = 0;
                else
                    continue;
            }
            else
                v = Number(v);
        }
        if (f === "locations")
            v = v.map((l) => {
                const loc = {
                    path: l.path,
                    forward_scheme: l.forward_scheme,
                    forward_host: l.forward_host,
                    forward_port: Number(l.forward_port),
                    advanced_config: l.advanced_config ?? "",
                };
                if (l.id)
                    loc.id = l.id;
                if (l.forward_path)
                    loc.forward_path = l.forward_path;
                return loc;
            });
        if (f === "items")
            v = v.filter((i) => String(i.username ?? "").trim());
        if (f === "clients")
            v = v.filter((c) => String(c.address ?? "").trim());
        out[f] = v;
    }
    if (kind !== "stream" && kind !== "access_list" && "domain_names_text" in d)
        out.domain_names = lines(d.domain_names_text);
    if (kind === "certificate") {
        if (action === "upload") {
            for (const k of Object.keys(out))
                if (!["certificate", "certificate_key", "intermediate_certificate"].includes(k))
                    delete out[k];
        }
        else if (out.provider === "letsencrypt") {
            delete out.certificate;
            delete out.certificate_key;
            delete out.intermediate_certificate;
            if (!out.dns_challenge) {
                delete out.dns_provider;
                delete out.dns_provider_credentials;
                delete out.propagation_seconds;
            }
        }
        else {
            delete out.domain_names;
            delete out.dns_challenge;
            delete out.dns_provider;
            delete out.dns_provider_credentials;
            delete out.propagation_seconds;
            delete out.key_type;
        }
    }
    return out;
}
function Text({ label, name, data, set, placeholder, type = "text", hint, required, }) {
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: label }), _jsx("input", { type: type, value: String(data[name] ?? ""), placeholder: placeholder, required: required, onChange: (e) => {
                    const value = e.target.value;
                    set((d) => ({ ...d, [name]: value }));
                } }), hint && _jsx("small", { className: "muted", children: hint })] }));
}
function Area({ label, name, data, set, rows = 4, hint, mono = true, placeholder, }) {
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: label }), _jsx("textarea", { className: mono ? "mono" : undefined, rows: rows, value: String(data[name] ?? ""), placeholder: placeholder, spellCheck: false, onChange: (e) => {
                    const value = e.target.value;
                    set((d) => ({ ...d, [name]: value }));
                } }), hint && _jsx("small", { className: "muted", children: hint })] }));
}
function Toggle({ label, name, data, set, hint }) {
    return (_jsxs("label", { className: "check", title: hint, children: [_jsx("input", { type: "checkbox", checked: !!data[name], onChange: (e) => {
                    const value = e.target.checked;
                    set((d) => ({ ...d, [name]: value }));
                } }), _jsx("span", { children: label })] }));
}
function Select({ label, name, data, set, options, }) {
    return (_jsxs("label", { className: "field", children: [_jsx("span", { children: label }), _jsx("select", { value: String(data[name] ?? options[0]?.[0] ?? ""), onChange: (e) => {
                    const value = e.target.value;
                    set((d) => ({ ...d, [name]: value }));
                }, children: options.map(([v, l]) => (_jsx("option", { value: String(v), children: l }, String(v)))) })] }));
}
function SslFields({ data, set, ov }) {
    const certs = [[0, "Geen certificaat (enkel http)"]];
    for (const c of ov.certificate)
        certs.push([c.id, `${c.label} (${(c.domain_names ?? []).join(", ")})`]);
    return (_jsxs("fieldset", { className: "fieldset", children: [_jsx("legend", { children: "TLS" }), _jsx(Select, { label: "Certificaat", name: "certificate_id", data: data, set: set, options: certs }), _jsxs("small", { className: "muted", children: ["Nog geen certificaat? Vraag er een aan bij ", _jsx("em", { children: "Certificaten" }), " en kies het daarna hier."] }), _jsxs("div", { className: "row wrap", children: [_jsx(Toggle, { label: "Force SSL", name: "ssl_forced", data: data, set: set }), _jsx(Toggle, { label: "HTTP/2", name: "http2_support", data: data, set: set }), _jsx(Toggle, { label: "HSTS", name: "hsts_enabled", data: data, set: set }), _jsx(Toggle, { label: "HSTS ook voor subdomeinen", name: "hsts_subdomains", data: data, set: set })] })] }));
}
function AdvancedField({ data, set, row }) {
    const blocks = row?.managed_blocks ?? [];
    return (_jsx(Area, { label: "Advanced (eigen nginx-config)", name: "advanced_config", data: data, set: set, rows: 10, hint: _jsxs(_Fragment, { children: [blocks.length > 0 && (_jsxs(_Fragment, { children: ["Blokken tussen ", _jsx("code", { children: "# >>> vaultx:\u2026" }), " en ", _jsx("code", { children: "# <<< vaultx:\u2026" }), " (", blocks.join(", "), ") beheert VaultX zelf; laat ze staan.", " "] })), "Regels met Authentik, ", _jsx("code", { children: "sub_filter" }), " of ", _jsx("code", { children: "add_header" }), " weghalen kan enkel met een extra bevestiging."] }) }));
}
function Locations({ data, set }) {
    const locs = data.locations ?? [];
    const update = (i, patch) => set((d) => ({ ...d, locations: d.locations.map((l, j) => (i === j ? { ...l, ...patch } : l)) }));
    return (_jsxs("fieldset", { className: "fieldset", children: [_jsx("legend", { children: "Custom locations" }), locs.map((l, i) => (_jsxs("div", { className: "location-edit", children: [_jsxs("div", { className: "grid-location", children: [_jsx("input", { value: String(l.path ?? ""), placeholder: "/pad", onChange: (e) => update(i, { path: e.target.value }) }), _jsxs("select", { value: String(l.forward_scheme), onChange: (e) => update(i, { forward_scheme: e.target.value }), children: [_jsx("option", { value: "http", children: "http" }), _jsx("option", { value: "https", children: "https" })] }), _jsx("input", { value: String(l.forward_host ?? ""), placeholder: "host", onChange: (e) => update(i, { forward_host: e.target.value }) }), _jsx("input", { type: "number", value: String(l.forward_port ?? ""), onChange: (e) => update(i, { forward_port: e.target.value }) }), _jsx("button", { type: "button", className: "btn btn-ghost small btn-danger-text", onClick: () => set((d) => ({ ...d, locations: d.locations.filter((_, j) => j !== i) })), children: "Weg" })] }), _jsx("textarea", { className: "mono", rows: 3, spellCheck: false, placeholder: "Eigen config voor deze location", value: String(l.advanced_config ?? ""), onChange: (e) => update(i, { advanced_config: e.target.value }) })] }, i))), _jsx("button", { type: "button", className: "btn btn-ghost small", onClick: () => set((d) => ({
                    ...d,
                    locations: [
                        ...(d.locations ?? []),
                        { path: "", forward_scheme: "http", forward_host: "", forward_port: 80, advanced_config: "" },
                    ],
                })), children: "+ Location toevoegen" })] }));
}
function PemFile({ label, name, set }) {
    return (_jsxs("label", { className: "small muted", children: [label, ":", " ", _jsx("input", { type: "file", accept: ".pem,.crt,.cer,.key,text/plain", onChange: (e) => {
                    const f = e.target.files?.[0];
                    if (f)
                        void f.text().then((t) => set((d) => ({ ...d, [name]: t })));
                } })] }));
}
function KindForm({ kind, action, data, set, ov, row, mbase, }) {
    const domains = (_jsx(Area, { label: "Domeinnamen", name: "domain_names_text", data: data, set: set, rows: 2, placeholder: "app.jbogaert.be", hint: "E\u00E9n per regel (of gescheiden door komma's)." }));
    if (kind === "proxy_host") {
        const lists = [[0, "Openbaar (geen access list)"]];
        for (const a of ov.access_list)
            lists.push([a.id, a.label]);
        return (_jsxs(_Fragment, { children: [domains, _jsxs("div", { className: "grid-upstream grid-form", children: [_jsx(Select, { label: "Schema", name: "forward_scheme", data: data, set: set, options: [
                                ["http", "http"],
                                ["https", "https"],
                            ] }), _jsx(Text, { label: "Doelhost of IP", name: "forward_host", data: data, set: set, placeholder: "192.168.10.20" }), _jsx(Text, { label: "Poort", name: "forward_port", data: data, set: set, type: "number", placeholder: "8080" })] }), _jsxs("div", { className: "row wrap", children: [_jsx(Toggle, { label: "Websockets", name: "allow_websocket_upgrade", data: data, set: set }), _jsx(Toggle, { label: "Block common exploits", name: "block_exploits", data: data, set: set }), _jsx(Toggle, { label: "Cache assets", name: "caching_enabled", data: data, set: set }), _jsx(Toggle, { label: "X-Forwarded-Proto vertrouwen", name: "trust_forwarded_proto", data: data, set: set, hint: "Voor een NPM achter een andere proxy" })] }), _jsx(Select, { label: "Access list", name: "access_list_id", data: data, set: set, options: lists }), _jsx(SslFields, { data: data, set: set, ov: ov }), _jsx(Locations, { data: data, set: set }), _jsx(AdvancedField, { data: data, set: set, row: row })] }));
    }
    if (kind === "redirection_host")
        return (_jsxs(_Fragment, { children: [domains, _jsxs("div", { className: "grid-form", children: [_jsx(Select, { label: "HTTP-code", name: "forward_http_code", data: data, set: set, options: [
                                [301, "301 Permanent"],
                                [302, "302 Tijdelijk"],
                                [307, "307 Tijdelijk (methode behouden)"],
                                [308, "308 Permanent (methode behouden)"],
                                [300, "300 Multiple choices"],
                                [303, "303 See other"],
                            ] }), _jsx(Select, { label: "Schema", name: "forward_scheme", data: data, set: set, options: [
                                ["auto", "zoals de bezoeker"],
                                ["https", "https"],
                                ["http", "http"],
                            ] })] }), _jsx(Text, { label: "Doeldomein", name: "forward_domain_name", data: data, set: set, placeholder: "nieuw.jbogaert.be" }), _jsxs("div", { className: "row wrap", children: [_jsx(Toggle, { label: "Pad behouden", name: "preserve_path", data: data, set: set }), _jsx(Toggle, { label: "Block common exploits", name: "block_exploits", data: data, set: set })] }), _jsx(SslFields, { data: data, set: set, ov: ov }), _jsx(AdvancedField, { data: data, set: set, row: row })] }));
    if (kind === "dead_host")
        return (_jsxs(_Fragment, { children: [domains, _jsx(SslFields, { data: data, set: set, ov: ov }), _jsx(AdvancedField, { data: data, set: set, row: row })] }));
    if (kind === "stream")
        return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid-form", children: [_jsx(Text, { label: "Inkomende poort", name: "incoming_port", data: data, set: set, type: "number", placeholder: "2222" }), _jsx(Text, { label: "Doelpoort", name: "forwarding_port", data: data, set: set, type: "number", placeholder: "22" })] }), _jsx(Text, { label: "Doelhost of IP", name: "forwarding_host", data: data, set: set, placeholder: "192.168.10.30" }), _jsxs("div", { className: "row wrap", children: [_jsx(Toggle, { label: "TCP", name: "tcp_forwarding", data: data, set: set }), _jsx(Toggle, { label: "UDP", name: "udp_forwarding", data: data, set: set })] }), _jsx("small", { className: "muted", children: "De inkomende poort moet ook open staan op de NPM-container (ports in compose)." })] }));
    if (kind === "access_list")
        return _jsx(AccessListForm, { data: data, set: set, editing: action === "update" });
    return _jsx(CertificateForm, { action: action, data: data, set: set, mbase: mbase });
}
function AccessListForm({ data, set, editing }) {
    const items = data.items ?? [];
    const clients = data.clients ?? [];
    const setItem = (i, patch) => set((d) => ({ ...d, items: d.items.map((x, j) => (i === j ? { ...x, ...patch } : x)) }));
    const setClient = (i, patch) => set((d) => ({ ...d, clients: d.clients.map((x, j) => (i === j ? { ...x, ...patch } : x)) }));
    return (_jsxs(_Fragment, { children: [_jsx(Text, { label: "Naam", name: "name", data: data, set: set, placeholder: "LAN" }), _jsxs("div", { className: "row wrap", children: [_jsx(Toggle, { label: "Satisfy Any", name: "satisfy_any", data: data, set: set, hint: "Binnen met een geldig adres \u00F3f een geldige login (anders: allebei nodig)" }), _jsx(Toggle, { label: "Auth doorgeven aan de app", name: "pass_auth", data: data, set: set })] }), _jsxs("fieldset", { className: "fieldset", children: [_jsx("legend", { children: "Gebruikers (basic auth)" }), items.map((it, i) => (_jsxs("div", { className: "row", children: [_jsx("input", { value: String(it.username ?? ""), placeholder: "gebruikersnaam", onChange: (e) => setItem(i, { username: e.target.value }) }), _jsx("input", { type: "password", autoComplete: "new-password", value: String(it.password ?? ""), placeholder: editing ? "leeg = ongewijzigd" : "wachtwoord", onChange: (e) => setItem(i, { password: e.target.value }) }), _jsx("button", { type: "button", className: "btn btn-ghost small btn-danger-text", onClick: () => set((d) => ({ ...d, items: d.items.filter((_, j) => j !== i) })), children: "Weg" })] }, i))), _jsx("button", { type: "button", className: "btn btn-ghost small", onClick: () => set((d) => ({ ...d, items: [...(d.items ?? []), { username: "", password: "" }] })), children: "+ Gebruiker" })] }), _jsxs("fieldset", { className: "fieldset", children: [_jsx("legend", { children: "Adressen" }), clients.map((cl, i) => (_jsxs("div", { className: "row", children: [_jsxs("select", { value: String(cl.directive ?? "allow"), onChange: (e) => setClient(i, { directive: e.target.value }), children: [_jsx("option", { value: "allow", children: "toestaan" }), _jsx("option", { value: "deny", children: "weigeren" })] }), _jsx("input", { value: String(cl.address ?? ""), placeholder: "192.168.10.0/24 of all", onChange: (e) => setClient(i, { address: e.target.value }) }), _jsx("button", { type: "button", className: "btn btn-ghost small btn-danger-text", onClick: () => set((d) => ({ ...d, clients: d.clients.filter((_, j) => j !== i) })), children: "Weg" })] }, i))), _jsx("button", { type: "button", className: "btn btn-ghost small", onClick: () => set((d) => ({ ...d, clients: [...(d.clients ?? []), { address: "", directive: "allow" }] })), children: "+ Adres" })] })] }));
}
function CertificateForm({ action, data, set, mbase }) {
    const dns = useQuery({
        queryKey: ["npm-dns-providers", mbase],
        queryFn: () => api.get(`${mbase}/dns-providers`),
        enabled: action === "create" && data.provider === "letsencrypt" && !!data.dns_challenge,
        staleTime: 3_600_000,
    });
    const pem = (_jsxs(_Fragment, { children: [_jsx(Area, { label: "Certificaat (PEM)", name: "certificate", data: data, set: set, rows: 4, placeholder: "-----BEGIN CERTIFICATE-----" }), _jsx(PemFile, { label: "of kies een bestand", name: "certificate", set: set }), _jsx(Area, { label: "Private key (PEM)", name: "certificate_key", data: data, set: set, rows: 4, placeholder: "-----BEGIN PRIVATE KEY-----" }), _jsx(PemFile, { label: "of kies een bestand", name: "certificate_key", set: set }), _jsx(Area, { label: "Tussencertificaten (optioneel)", name: "intermediate_certificate", data: data, set: set, rows: 3 }), _jsx("small", { className: "muted", children: "De private key gaat rechtstreeks naar NPM; VaultX bewaart hem niet en zet hem niet in het journaal." })] }));
    if (action === "upload")
        return pem;
    const providers = (dns.data ?? []).map((p) => [p.id, p.name ?? p.id]);
    return (_jsxs(_Fragment, { children: [_jsx(Select, { label: "Soort", name: "provider", data: data, set: set, options: [
                    ["letsencrypt", "Let's Encrypt aanvragen"],
                    ["other", "Eigen certificaat opladen"],
                ] }), data.provider === "letsencrypt" ? (_jsxs(_Fragment, { children: [_jsx(Area, { label: "Domeinnamen", name: "domain_names_text", data: data, set: set, rows: 2, placeholder: "app.jbogaert.be", hint: "E\u00E9n per regel. Een wildcard (*.jbogaert.be) kan enkel met een DNS-challenge." }), _jsx(Text, { label: "Naam (optioneel)", name: "nice_name", data: data, set: set }), _jsx(Select, { label: "Sleuteltype", name: "key_type", data: data, set: set, options: [
                            ["rsa", "RSA"],
                            ["ecdsa", "ECDSA"],
                        ] }), _jsx(Toggle, { label: "DNS-challenge gebruiken", name: "dns_challenge", data: data, set: set }), !!data.dns_challenge && (_jsxs(_Fragment, { children: [dns.isLoading ? (_jsx(Loading, {})) : (_jsxs("label", { className: "field", children: [_jsx("span", { children: "DNS-provider" }), _jsxs("select", { value: String(data.dns_provider ?? ""), onChange: (e) => {
                                            const id = e.target.value;
                                            const tpl = dns.data?.find((p) => p.id === id)?.credentials ?? "";
                                            set((d) => ({ ...d, dns_provider: id, dns_provider_credentials: d.dns_provider_credentials || tpl }));
                                        }, children: [_jsx("option", { value: "", children: "Kies\u2026" }), providers.map(([id, name]) => (_jsx("option", { value: id, children: name }, id)))] })] })), _jsx(Area, { label: "Gegevens voor de DNS-provider", name: "dns_provider_credentials", data: data, set: set, rows: 4, hint: "Gaat rechtstreeks naar NPM; VaultX zet ze niet in het journaal." }), _jsx(Text, { label: "Propagatietijd (seconden, optioneel)", name: "propagation_seconds", data: data, set: set, type: "number" })] }))] })) : (_jsxs(_Fragment, { children: [_jsx(Text, { label: "Naam", name: "nice_name", data: data, set: set, placeholder: "jbogaert.be wildcard" }), pem] }))] }));
}
// ---------------------------------------------------------------- journaal
function ManageJournal({ mbase, connId, onRestore, }) {
    const changes = useQuery({
        queryKey: ["npm-manage-changes", connId],
        queryFn: () => api.get(`${mbase}/changes`),
    });
    const [open, setOpen] = useState(null);
    if (changes.isLoading)
        return _jsx(Loading, {});
    if (changes.error)
        return _jsx(ErrorBox, { error: changes.error });
    if (!changes.data?.length)
        return _jsx(Empty, { children: "VaultX heeft nog niets gewijzigd in deze NPM." });
    return (_jsxs("div", { className: "table-wrap", children: [_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Wanneer" }), _jsx("th", { children: "Object" }), _jsx("th", { children: "Wijziging" }), _jsx("th", { children: "Resultaat" }), _jsx("th", { children: "Door" })] }) }), _jsx("tbody", { children: changes.data.map((c) => (_jsxs("tr", { children: [_jsx("td", { className: "nowrap", title: formatDate(c.created_at), children: relative(c.created_at) }), _jsxs("td", { className: "small", children: [_jsx("div", { className: "muted", children: kindInfo(c.object_type)?.one ?? c.object_type }), _jsx("span", { className: "mono", children: c.domain })] }), _jsxs("td", { children: [manageActionLabels[c.action] ?? c.action, !c.verified && (_jsx("div", { children: _jsx(Badge, { tone: "warn", children: "zonder controle" }) }))] }), _jsxs("td", { children: [_jsx(ChangeStatusBadge, { status: c.status }), _jsx("div", { className: "small", children: c.message }), _jsxs("div", { className: "row wrap", children: [_jsx("button", { className: "btn btn-ghost small", onClick: () => setOpen(c), children: "Details" }), onRestore &&
                                                    c.action === "delete" &&
                                                    c.status === "applied" &&
                                                    c.object_type !== "certificate" && (_jsx("button", { className: "btn btn-ghost small", onClick: () => onRestore(c), children: "Opnieuw aanmaken" }))] })] }), _jsx("td", { className: "small", children: c.actor_label ?? "—" })] }, c.id))) })] }), _jsx(Modal, { title: open ? `${manageActionLabels[open.action] ?? open.action}: ${open.domain}` : "", open: open !== null, onClose: () => setOpen(null), wide: true, children: open && (_jsxs("div", { className: "form", children: [_jsx(ManageResult, { change: open }), open.after?.changes && open.after.changes.length > 0 && _jsx(ChangesTable, { changes: open.after.changes }), Object.keys(open.before).length > 0 && (_jsxs("details", { open: open.status === "rollback_failed", children: [_jsx("summary", { children: "Volledige config ervoor" }), _jsx("pre", { className: "cell-pre", children: JSON.stringify(open.before, null, 2) })] })), _jsxs("div", { className: "muted small", children: [formatDate(open.created_at), " door ", open.actor_label ?? "?", open.finished_at && `, klaar ${formatDate(open.finished_at)}`] })] })) })] }));
}
