import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import { ApplicationForm, AuthLabel, HostLink, StatusBadge } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader, Stat } from "../components/ui";
import { statusInfo } from "../format";
const STATUS_ORDER = ["protected", "restricted", "unknown", "unprotected", "offline", "removed"];
export function CatalogPage() {
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [params, setParams] = useSearchParams();
    const orgFilter = params.get("organization_id") ?? "";
    const statusFilter = params.get("status") ?? "";
    const [q, setQ] = useState("");
    const [creating, setCreating] = useState(false);
    const [createOrg, setCreateOrg] = useState("");
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => api.get("/api/v1/organizations", { limit: 200 }),
    });
    const catalog = useQuery({
        queryKey: ["catalog", orgFilter, q],
        queryFn: () => api.get("/api/v1/catalog", { organization_id: orgFilter, q }),
    });
    const create = useMutation({
        mutationFn: (v) => api.post(`/api/v1/organizations/${createOrg}/applications`, v),
        onSuccess: (a) => {
            setCreating(false);
            void qc.invalidateQueries({ queryKey: ["catalog"] });
            navigate(`/catalog/${a.organization_id}/${a.id}`);
        },
    });
    const orgName = new Map(orgs.data?.items.map((o) => [o.id, o.name]));
    const manageable = orgs.data?.items.filter((o) => canManageOrg(me, o.id)) ?? [];
    const all = catalog.data ?? [];
    const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, all.filter((a) => a.status === s).length]));
    const shown = statusFilter ? all.filter((a) => a.status === statusFilter) : all;
    const setFilter = (key, value) => {
        const next = new URLSearchParams(params);
        if (value)
            next.set(key, value);
        else
            next.delete(key);
        setParams(next, { replace: true });
    };
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Applicatiecatalogus", subtitle: "Alles wat achter Nginx Proxy Manager draait, met hoe je er binnenkomt. NPM-koppelingen vullen dit automatisch.", actions: _jsxs(_Fragment, { children: [_jsx(Link, { className: "btn", to: "/npm", children: "NPM" }), manageable.length > 0 && (_jsx("button", { className: "btn btn-primary", onClick: () => {
                                setCreateOrg(orgFilter && manageable.some((o) => o.id === orgFilter) ? orgFilter : manageable[0].id);
                                setCreating(true);
                            }, children: "Applicatie toevoegen" }))] }) }), _jsxs("div", { className: "stats", children: [_jsx(Stat, { label: "Applicaties", value: all.length }), STATUS_ORDER.filter((s) => counts[s] > 0 || s === "protected" || s === "unknown").map((s) => (_jsxs("button", { className: `stat stat-button${statusFilter === s ? " selected" : ""}`, onClick: () => setFilter("status", statusFilter === s ? "" : s), title: statusInfo[s].hint, children: [_jsx("div", { className: "stat-value", children: counts[s] }), _jsx("div", { className: "stat-label", children: statusInfo[s].label })] }, s)))] }), _jsxs(Card, { actions: _jsxs("div", { className: "row wrap", children: [_jsx("input", { className: "search", placeholder: "Zoeken op naam, URL of type", value: q, onChange: (e) => setQ(e.target.value) }), _jsxs("select", { value: orgFilter, onChange: (e) => setFilter("organization_id", e.target.value), children: [_jsx("option", { value: "", children: "Alle organisaties" }), orgs.data?.items.map((o) => (_jsx("option", { value: o.id, children: o.name }, o.id)))] }), statusFilter && (_jsxs("button", { className: "btn btn-ghost", onClick: () => setFilter("status", ""), children: ["Filter wissen (", statusInfo[statusFilter]?.label, ")"] }))] }), children: [_jsx(ErrorBox, { error: catalog.error }), catalog.isLoading ? (_jsx(Loading, {})) : !shown.length ? (_jsx(Empty, { children: all.length ? "Geen applicaties met deze filter." : "De catalogus is nog leeg. Koppel een Nginx Proxy Manager om hem te vullen." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Applicatie" }), _jsx("th", { children: "Host" }), _jsx("th", { children: "Aanmelding" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Organisatie" }), _jsx("th", { children: "Bron" })] }) }), _jsx("tbody", { children: shown.map((a) => {
                                        const live = a.hosts.filter((h) => !h.removed_at);
                                        const domains = (live.length ? live : a.hosts).flatMap((h) => h.domain_names.map((d) => ({ d, ssl: h.ssl })));
                                        return (_jsxs("tr", { children: [_jsxs("td", { children: [_jsx(Link, { to: `/catalog/${a.organization_id}/${a.id}`, children: _jsx("strong", { children: a.name }) }), _jsxs("div", { className: "row wrap small", children: [a.app_type && _jsx("span", { className: "muted mono", children: a.app_type }), a.tags.map((t) => (_jsx(Badge, { children: t }, t)))] })] }), _jsx("td", { children: domains.length ? (_jsxs("div", { className: "stack", children: [domains.slice(0, 3).map(({ d, ssl }) => (_jsx(HostLink, { domain: d, ssl: ssl }, d))), domains.length > 3 && _jsxs("span", { className: "muted small", children: ["+", domains.length - 3] })] })) : a.url ? (_jsx("a", { className: "mono small", href: a.url, target: "_blank", rel: "noreferrer", children: a.url })) : (_jsx("span", { className: "muted", children: "\u2014" })) }), _jsxs("td", { children: [_jsx(AuthLabel, { method: a.auth_method }), a.auto_login && (_jsx(Badge, { tone: "good", title: "Automatische login via Authentik ingericht", children: "auto-login" }))] }), _jsxs("td", { className: "nowrap", children: [_jsx(StatusBadge, { status: a.status }), a.warning_count > 0 && (_jsxs(Badge, { tone: "warn", title: "Waarschuwingen bij de host", children: [a.warning_count, " \u26A0"] }))] }), _jsx("td", { children: _jsx(Link, { to: `/organizations/${a.organization_id}`, children: orgName.get(a.organization_id) ?? "—" }) }), _jsx("td", { children: a.source === "npm" ? (_jsxs(Badge, { tone: "info", title: a.auto_update ? "Sync houdt dit item bij" : "Handmatig aangepast", children: ["NPM", a.auto_update ? "" : " · aangepast"] })) : (_jsx(Badge, { children: "Manueel" })) })] }, a.id));
                                    }) })] }) }))] }), _jsxs(Modal, { title: "Applicatie toevoegen", open: creating, onClose: () => setCreating(false), children: [manageable.length > 1 && (_jsxs("label", { className: "field", style: { marginBottom: 12 }, children: [_jsx("span", { children: "Organisatie" }), _jsx("select", { value: createOrg, onChange: (e) => setCreateOrg(e.target.value), children: manageable.map((o) => (_jsx("option", { value: o.id, children: o.name }, o.id))) })] })), _jsx(ApplicationForm, { submitLabel: "Toevoegen", onSubmit: (v) => create.mutate(v), error: create.error, pending: create.isPending })] })] }));
}
