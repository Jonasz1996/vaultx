import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import { AuditTable } from "../components/AuditTable";
import { AutoLoginCard } from "../components/autoLogin";
import { ApplicationForm, AuthLabel, HostLink, StatusBadge, Warnings } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative, statusInfo } from "../format";
export function ApplicationDetailPage() {
    const { orgId = "", appId = "" } = useParams();
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [editing, setEditing] = useState(false);
    const base = `/api/v1/organizations/${orgId}/applications/${appId}`;
    const manage = canManageOrg(me, orgId);
    const app = useQuery({ queryKey: ["app", appId], queryFn: () => api.get(base) });
    const org = useQuery({
        queryKey: ["org", orgId],
        queryFn: () => api.get(`/api/v1/organizations/${orgId}`),
    });
    const audit = useQuery({
        queryKey: ["audit", "app", appId],
        queryFn: () => api.get("/api/v1/audit", { organization_id: orgId, target_id: appId, limit: 25 }),
        enabled: manage,
    });
    const refresh = (a) => {
        qc.setQueryData(["app", appId], a);
        void qc.invalidateQueries({ queryKey: ["catalog"] });
        void qc.invalidateQueries({ queryKey: ["audit", "app", appId] });
    };
    const update = useMutation({
        mutationFn: (v) => api.patch(base, v),
        onSuccess: (a) => {
            setEditing(false);
            refresh(a);
        },
    });
    const remove = useMutation({
        mutationFn: () => api.delete(base),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: ["catalog"] });
            navigate("/catalog");
        },
    });
    if (app.isLoading)
        return _jsx(Loading, {});
    if (!app.data)
        return _jsx(ErrorBox, { error: app.error });
    const a = app.data;
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: a.name, subtitle: _jsxs(_Fragment, { children: [_jsx(Link, { to: "/catalog", children: "Catalogus" }), " \u00B7 ", _jsx(Link, { to: `/organizations/${orgId}`, children: org.data?.name ?? "…" }), a.description && ` · ${a.description}`] }), actions: manage && (_jsxs(_Fragment, { children: [_jsx("button", { className: "btn", onClick: () => setEditing(true), children: "Bewerken" }), _jsx("button", { className: "btn btn-danger", disabled: a.auto_login, title: a.auto_login ? "Haal eerst de automatische login weg" : undefined, onClick: () => {
                                const extra = a.hosts.length
                                    ? " De gekoppelde NPM-hosts worden genegeerd, zodat de sync hem niet terugzet."
                                    : "";
                                if (confirm(`'${a.name}' uit de catalogus verwijderen?${extra}`))
                                    remove.mutate();
                            }, children: "Verwijderen" })] })) }), _jsx(ErrorBox, { error: remove.error ?? update.error }), _jsxs("div", { className: "grid-2", children: [_jsxs(Card, { title: "Overzicht", children: [_jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Status" }), _jsxs("dd", { children: [_jsx(StatusBadge, { status: a.status }), _jsx("span", { className: "muted small", children: statusInfo[a.status]?.hint })] }), _jsx("dt", { children: "Aanmelding" }), _jsxs("dd", { children: [_jsx(AuthLabel, { method: a.auth_method }), a.auto_login && _jsx(Badge, { tone: "good", children: "automatische login" })] }), _jsx("dt", { children: "URL" }), _jsx("dd", { children: a.url ? (_jsx("a", { href: a.url, target: "_blank", rel: "noreferrer", children: a.url })) : ("—") }), _jsx("dt", { children: "Type" }), _jsx("dd", { className: "mono", children: a.app_type ?? "—" }), _jsx("dt", { children: "Tags" }), _jsx("dd", { children: a.tags.length ? a.tags.map((t) => _jsx(Badge, { children: t }, t)) : "—" }), _jsx("dt", { children: "Bron" }), _jsxs("dd", { children: [a.source === "npm" ? _jsx(Badge, { tone: "info", children: "NPM" }) : _jsx(Badge, { children: "Manueel" }), a.source === "npm" && (_jsx("span", { className: "muted small", children: a.auto_update ? "Sync houdt dit item bij." : "Handmatig aangepast: de sync laat het met rust." }))] }), _jsx("dt", { children: "Aangemaakt" }), _jsx("dd", { children: formatDate(a.created_at) }), _jsx("dt", { children: "Gewijzigd" }), _jsx("dd", { children: relative(a.updated_at) })] }), manage && a.source === "npm" && (_jsx("div", { className: "row", style: { marginTop: 12 }, children: _jsx("button", { className: "btn", disabled: update.isPending, onClick: () => update.mutate({ auto_update: !a.auto_update }), children: a.auto_update ? "Automatisch bijwerken uitzetten" : "Weer automatisch laten bijwerken" }) }))] }), _jsx(Card, { title: `Proxy hosts (${a.hosts.length})`, children: !a.hosts.length ? (_jsx(Empty, { children: "Geen NPM-host gekoppeld. Dit item is manueel." })) : (_jsx("ul", { className: "plain-list", children: a.hosts.map((h) => (_jsxs("li", { children: [_jsxs("div", { className: "row wrap", children: [h.domain_names.map((d) => (_jsx(HostLink, { domain: d, ssl: h.ssl }, d))), h.removed_at && _jsx(Badge, { children: "verdwenen" }), !h.enabled && _jsx(Badge, { tone: "bad", children: "uitgeschakeld" }), h.forward_auth && _jsx(Badge, { tone: "good", children: "forward auth" })] }), _jsxs("div", { className: "muted small", children: ["\u2192 ", _jsx("span", { className: "mono", children: h.forward }), " \u00B7 via", " ", _jsx(Link, { to: `/npm/${orgId}/${h.connection_id}`, children: h.connection_name })] }), h.warnings.length > 0 && _jsx(Warnings, { items: h.warnings })] }, h.id))) })) })] }), _jsx(AutoLoginCard, { orgId: orgId, app: a, manage: manage }), manage && (_jsx(Card, { title: "Geschiedenis", children: audit.data?.items.length ? _jsx(AuditTable, { items: audit.data.items }) : _jsx(Empty, { children: "Geen gebeurtenissen." }) })), _jsx(Modal, { title: "Applicatie bewerken", open: editing, onClose: () => setEditing(false), children: _jsx(ApplicationForm, { initial: a, onSubmit: (v) => update.mutate(v), error: update.error, pending: update.isPending, note: a.source === "npm" && a.auto_update
                        ? "Na opslaan zet VaultX automatisch bijwerken uit, zodat de volgende sync je keuzes niet overschrijft."
                        : undefined }) })] }));
}
