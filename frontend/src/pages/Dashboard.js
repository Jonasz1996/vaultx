import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import { AuditTable } from "../components/AuditTable";
import { StatusBadge } from "../components/catalog";
import { Card, Empty, ErrorBox, Loading, PageHeader, Stat } from "../components/ui";
import { roleLabels, userLabel } from "../format";
export function DashboardPage() {
    const { data: me } = useMe();
    const { data, error, isLoading } = useQuery({
        queryKey: ["dashboard"],
        queryFn: () => api.get("/api/v1/dashboard"),
    });
    const catalog = useQuery({ queryKey: ["catalog", "", ""], queryFn: () => api.get("/api/v1/catalog") });
    if (isLoading)
        return _jsx(Loading, {});
    if (error || !data)
        return _jsx(ErrorBox, { error: error });
    const orgs = me?.memberships.filter((m) => m.team_id === null) ?? [];
    const apps = catalog.data ?? [];
    const protectedCount = apps.filter((a) => a.status === "protected").length;
    const severity = ["offline", "unprotected", "unknown", "restricted", "protected", "removed"];
    const attention = apps
        .filter((a) => ["unknown", "unprotected", "offline"].includes(a.status) || a.warning_count > 0)
        .sort((x, y) => severity.indexOf(x.status) - severity.indexOf(y.status) || y.warning_count - x.warning_count);
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Dashboard", subtitle: me ? `Welkom, ${userLabel(me)}` : undefined }), me?.is_admin ? (_jsxs("div", { className: "stats", children: [_jsx(Stat, { label: "Gebruikers", value: data.users_total, hint: `${data.users_active} actief` }), _jsx(Stat, { label: "Beheerders", value: data.admins }), _jsx(Stat, { label: "Organisaties", value: data.organizations }), _jsx(Stat, { label: "Teams", value: data.teams }), _jsx(Stat, { label: "Actieve sessies", value: data.active_sessions }), _jsx(Stat, { label: "Logins (24u)", value: data.logins_24h }), _jsx(Stat, { label: "Geweigerd (24u)", value: data.denied_24h }), _jsx(Stat, { label: "Applicaties", value: apps.length, hint: `${protectedCount} via Authentik` })] })) : (_jsxs("div", { className: "stats", children: [_jsx(Stat, { label: "Mijn organisaties", value: data.organizations }), _jsx(Stat, { label: "Mijn teams", value: data.teams }), _jsx(Stat, { label: "Applicaties", value: apps.length, hint: `${protectedCount} via Authentik` })] })), apps.length > 0 && (_jsx(Card, { title: `Catalogus: aandacht nodig (${attention.length})`, actions: _jsx(Link, { to: "/catalog", children: "Catalogus openen" }), children: attention.length === 0 ? (_jsx(Empty, { children: "Alle applicaties zijn bepaald en zonder waarschuwingen." })) : (_jsx("ul", { className: "plain-list", children: attention.slice(0, 8).map((a) => (_jsxs("li", { className: "row wrap", children: [_jsx(Link, { to: `/catalog/${a.organization_id}/${a.id}`, children: a.name }), _jsx(StatusBadge, { status: a.status }), a.warning_count > 0 && (_jsx("span", { className: "muted small", children: a.hosts.flatMap((h) => h.warnings).map((w) => w.message)[0] }))] }, a.id))) })) })), _jsxs("div", { className: "grid-2", children: [_jsx(Card, { title: "Mijn lidmaatschappen", children: orgs.length === 0 ? (_jsx(Empty, { children: "Je bent nog geen lid van een organisatie." })) : (_jsx("ul", { className: "plain-list", children: orgs.map((m) => (_jsxs("li", { children: [_jsx(Link, { to: `/organizations/${m.organization_id}`, children: m.organization_name }), _jsxs("span", { className: "muted", children: [" \u00B7 ", roleLabels[m.role] ?? m.role] })] }, m.id))) })) }), _jsx(Card, { title: "Recente gebeurtenissen", actions: _jsx(Link, { to: "/audit", children: "Alles bekijken" }), children: data.recent_events.length === 0 ? (_jsx(Empty, { children: "Nog geen gebeurtenissen." })) : (_jsx(AuditTable, { items: data.recent_events, compact: true })) })] })] }));
}
