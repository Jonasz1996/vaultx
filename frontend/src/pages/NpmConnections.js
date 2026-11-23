import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import { ConnectionForm } from "../components/catalog";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { relative } from "../format";
export function SyncStatus({ c }) {
    if (!c.last_sync_status)
        return _jsx(Badge, { children: "Nog niet gesynchroniseerd" });
    return c.last_sync_status === "ok" ? (_jsxs(Badge, { tone: "good", title: c.last_sync_at ?? "", children: ["OK \u00B7 ", relative(c.last_sync_at)] })) : (_jsxs(Badge, { tone: "bad", title: c.last_sync_error ?? "", children: ["Fout \u00B7 ", relative(c.last_sync_at)] }));
}
export function syncSummary(r) {
    return (`${r.hosts_total} hosts gelezen (${r.hosts_new} nieuw, ${r.hosts_updated} gewijzigd, ${r.hosts_removed} verdwenen); ` +
        `${r.applications_created} applicaties aangemaakt, ${r.applications_updated} bijgewerkt.`);
}
export function NpmConnectionsPage() {
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [creating, setCreating] = useState(false);
    const [createOrg, setCreateOrg] = useState("");
    const [lastSync, setLastSync] = useState(null);
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => api.get("/api/v1/organizations", { limit: 200 }),
    });
    const orgList = orgs.data?.items ?? [];
    const conns = useQueries({
        queries: orgList.map((o) => ({
            queryKey: ["npm", o.id],
            queryFn: () => api.get(`/api/v1/organizations/${o.id}/npm-connections`),
        })),
    });
    const manageable = orgList.filter((o) => canManageOrg(me, o.id));
    const create = useMutation({
        mutationFn: (v) => api.post(`/api/v1/organizations/${createOrg}/npm-connections`, v),
        onSuccess: (c) => {
            setCreating(false);
            void qc.invalidateQueries({ queryKey: ["npm", c.organization_id] });
            navigate(`/npm/${c.organization_id}/${c.id}`);
        },
    });
    const sync = useMutation({
        mutationFn: (c) => api.post(`/api/v1/organizations/${c.organization_id}/npm-connections/${c.id}/sync`),
        onSuccess: (r) => setLastSync({ id: r.connection_id, text: syncSummary(r) }),
        onSettled: (_r, _e, c) => {
            void qc.invalidateQueries({ queryKey: ["npm", c.organization_id] });
            void qc.invalidateQueries({ queryKey: ["catalog"] });
        },
    });
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Nginx Proxy Manager", subtitle: "VaultX leest de proxy hosts uit NPM en vult er de applicatiecatalogus mee. Er wordt niets in NPM gewijzigd.", actions: manageable.length > 0 && (_jsx("button", { className: "btn btn-primary", onClick: () => {
                        setCreateOrg(manageable[0].id);
                        setCreating(true);
                    }, children: "NPM koppelen" })) }), orgs.isLoading && _jsx(Loading, {}), _jsx(ErrorBox, { error: orgs.error ?? sync.error }), lastSync && _jsx("div", { className: "info-box", children: lastSync.text }), !orgs.isLoading && orgList.length === 0 && (_jsx(Card, { children: _jsx(Empty, { children: "Je bent nog geen lid van een organisatie. Een NPM-koppeling hoort altijd bij een organisatie." }) })), orgList.map((o, i) => {
                const list = conns[i]?.data ?? [];
                const manage = canManageOrg(me, o.id);
                return (_jsx(Card, { title: o.name, children: conns[i]?.isLoading ? (_jsx(Loading, {})) : !list.length ? (_jsx(Empty, { children: "Geen NPM-koppeling voor deze organisatie." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Koppeling" }), _jsx("th", { children: "NPM" }), _jsx("th", { children: "Hosts" }), _jsx("th", { children: "Laatste sync" }), _jsx("th", {})] }) }), _jsx("tbody", { children: list.map((c) => (_jsxs("tr", { children: [_jsxs("td", { children: [_jsx(Link, { to: `/npm/${o.id}/${c.id}`, children: _jsx("strong", { children: c.name }) }), !c.enabled && _jsx(Badge, { children: "Automatisch uit" }), _jsx("div", { className: "mono small muted", children: c.base_url })] }), _jsx("td", { children: c.npm_version ? _jsx("span", { className: "mono", children: c.npm_version }) : _jsx("span", { className: "muted", children: "\u2014" }) }), _jsx("td", { children: c.host_count }), _jsxs("td", { children: [_jsx(SyncStatus, { c: c }), c.last_sync_status === "error" && (_jsx("div", { className: "small", style: { color: "var(--bad-fg)" }, children: c.last_sync_error }))] }), _jsx("td", { className: "right", children: manage && (_jsx("button", { className: "btn", disabled: sync.isPending && sync.variables?.id === c.id, onClick: () => sync.mutate(c), children: sync.isPending && sync.variables?.id === c.id ? "Bezig…" : "Nu synchroniseren" })) })] }, c.id))) })] }) })) }, o.id));
            }), _jsxs(Card, { title: "Labels in NPM", children: [_jsxs("p", { className: "muted", style: { marginTop: 0 }, children: ["NPM kent geen labels. Zet ze als commentaar in het veld ", _jsx("em", { children: "Advanced" }), " van een proxy host (of van een custom location). nginx negeert commentaar, dus de proxy verandert niet."] }), _jsx("pre", { className: "snippet", children: `# vaultx.app = Grafana
# vaultx.auth = oidc          # forward_auth, oidc, saml, header, access_list, app, none
# vaultx.type = grafana
# vaultx.tags = monitoring, ops
# vaultx.description = Dashboards van het team
# vaultx.ignore = true        # niet opnemen in de catalogus` }), _jsxs("p", { className: "muted small", children: ["Zonder label herkent VaultX Authentik forward auth (", _jsx("code", { children: "auth_request" }), " naar de outpost) en NPM access lists zelf, en raadt het applicatietype uit het subdomein, de forward host of de poort."] })] }), _jsxs(Modal, { title: "NPM koppelen", open: creating, onClose: () => setCreating(false), children: [manageable.length > 1 && (_jsxs("label", { className: "field", style: { marginBottom: 12 }, children: [_jsx("span", { children: "Organisatie" }), _jsx("select", { value: createOrg, onChange: (e) => setCreateOrg(e.target.value), children: manageable.map((o) => (_jsx("option", { value: o.id, children: o.name }, o.id))) })] })), _jsx(ConnectionForm, { orgId: createOrg, onSubmit: (v) => create.mutate(v), error: create.error, pending: create.isPending })] })] }));
}
