import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, useMe } from "../api/hooks";
import { AuthLabel, ConnectionForm, HostLink, Warnings } from "../components/catalog";
import { ChangeJournal, ProtectionDialog } from "../components/npmProtection";
import { PublishDialog, UnpublishDialog } from "../components/publish";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { formatDate, relative } from "../format";
import { SyncStatus, syncSummary } from "./NpmConnections";
export function NpmConnectionDetailPage() {
    const { orgId = "", connId = "" } = useParams();
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [editing, setEditing] = useState(false);
    const [showRemoved, setShowRemoved] = useState(false);
    const [summary, setSummary] = useState(null);
    const [protecting, setProtecting] = useState(null);
    const [publishing, setPublishing] = useState(false);
    const [unpublishing, setUnpublishing] = useState(null);
    const base = `/api/v1/organizations/${orgId}/npm-connections/${connId}`;
    const manage = canManageOrg(me, orgId);
    const conn = useQuery({ queryKey: ["npm-conn", connId], queryFn: () => api.get(base) });
    const hosts = useQuery({ queryKey: ["npm-hosts", connId], queryFn: () => api.get(`${base}/hosts`) });
    const org = useQuery({
        queryKey: ["org", orgId],
        queryFn: () => api.get(`/api/v1/organizations/${orgId}`),
    });
    const outposts = useQuery({
        queryKey: ["authentik-outposts", orgId],
        queryFn: () => api.get(`/api/v1/organizations/${orgId}/authentik/outposts`),
        enabled: manage && !!conn.data?.authentik_outpost_pk,
        staleTime: 60_000,
    });
    const invalidate = () => {
        void qc.invalidateQueries({ queryKey: ["npm-conn", connId] });
        void qc.invalidateQueries({ queryKey: ["npm-hosts", connId] });
        void qc.invalidateQueries({ queryKey: ["npm", orgId] });
        void qc.invalidateQueries({ queryKey: ["catalog"] });
        void qc.invalidateQueries({ queryKey: ["npm-changes", base] });
    };
    const sync = useMutation({
        mutationFn: () => api.post(`${base}/sync`),
        onSuccess: (r) => setSummary(syncSummary(r)),
        onSettled: invalidate,
    });
    const update = useMutation({
        mutationFn: (v) => api.patch(base, v),
        onSuccess: () => {
            setEditing(false);
            invalidate();
        },
    });
    const remove = useMutation({
        mutationFn: () => api.delete(base),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: ["npm", orgId] });
            navigate("/npm");
        },
    });
    const ignore = useMutation({
        mutationFn: (h) => api.patch(`${base}/hosts/${h.id}`, { ignored: !h.ignored }),
        onSuccess: invalidate,
    });
    if (conn.isLoading)
        return _jsx(Loading, {});
    if (!conn.data)
        return _jsx(ErrorBox, { error: conn.error });
    const c = conn.data;
    const all = hosts.data ?? [];
    const removedCount = all.filter((h) => h.removed_at).length;
    const shown = showRemoved ? all : all.filter((h) => !h.removed_at);
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: c.name, subtitle: _jsxs(_Fragment, { children: [_jsx(Link, { to: "/npm", children: "NPM" }), " \u00B7 ", _jsx(Link, { to: `/organizations/${orgId}`, children: org.data?.name ?? "…" }), " \u00B7", " ", _jsx("span", { className: "mono", children: c.base_url })] }), actions: manage && (_jsxs(_Fragment, { children: [_jsx(Link, { className: "btn", to: `/npm/${orgId}/${connId}/beheer`, children: "NPM beheren" }), c.write_enabled && (_jsx("button", { className: "btn btn-primary", onClick: () => setPublishing(true), children: "App publiceren" })), _jsx("button", { className: "btn", onClick: () => sync.mutate(), disabled: sync.isPending, children: sync.isPending ? "Bezig…" : "Nu synchroniseren" }), _jsx("button", { className: "btn", onClick: () => setEditing(true), children: "Bewerken" }), _jsx("button", { className: "btn btn-danger", onClick: () => {
                                if (confirm(`Koppeling '${c.name}' verwijderen? De catalogusitems blijven bestaan, zonder host.`))
                                    remove.mutate();
                            }, children: "Verwijderen" })] })) }), _jsx(ErrorBox, { error: sync.error ?? remove.error ?? ignore.error }), summary && !sync.error && _jsx("div", { className: "info-box", children: summary }), _jsx(Card, { title: "Koppeling", children: _jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Laatste sync" }), _jsxs("dd", { children: [_jsx(SyncStatus, { c: c }), c.last_sync_at && _jsx("span", { className: "muted small", children: formatDate(c.last_sync_at) })] }), c.last_sync_error && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Fout" }), _jsx("dd", { style: { color: "var(--bad-fg)" }, children: c.last_sync_error })] })), _jsx("dt", { children: "NPM-versie" }), _jsx("dd", { className: "mono", children: c.npm_version ?? "—" }), _jsx("dt", { children: "Account" }), _jsx("dd", { children: c.identity }), _jsx("dt", { children: "TLS controleren" }), _jsx("dd", { children: c.verify_tls ? "ja" : _jsx(Badge, { tone: "warn", children: "nee" }) }), _jsx("dt", { children: "Automatisch" }), _jsx("dd", { children: c.enabled ? "ja, periodiek" : "nee, enkel handmatig" }), _jsx("dt", { children: "Wijzigen in NPM" }), _jsx("dd", { children: c.write_enabled ? (_jsx(Badge, { tone: "info", children: "aan" })) : (_jsxs("span", { className: "muted", children: ["uit", manage && ", aanzetten via Bewerken"] })) }), (c.write_enabled || c.authentik_outpost_url) && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Authentik-outpost" }), _jsx("dd", { className: "mono", children: c.authentik_outpost_url ?? _jsx(Badge, { tone: "warn", children: "niet ingesteld" }) }), _jsx("dt", { children: "Authentik-kant" }), _jsx("dd", { children: c.authentik_outpost_pk ? (_jsxs(_Fragment, { children: ["VaultX maakt provider en applicatie aan op outpost", " ", _jsx("strong", { children: outposts.data?.outposts.find((o) => o.pk === c.authentik_outpost_pk)?.name ??
                                                    c.authentik_outpost_pk })] })) : (_jsxs("span", { className: "muted", children: ["met de hand in Authentik", manage && " (automatisch: via Bewerken)"] })) }), _jsx("dt", { children: "Controleadres" }), _jsxs("dd", { className: "mono", children: [c.probe_host ?? new URL(c.base_url).hostname, ", poort ", c.probe_http_port, " / ", c.probe_https_port] })] }))] }) }), _jsx(Card, { title: `Proxy hosts (${all.length - removedCount})`, actions: removedCount > 0 && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: showRemoved, onChange: (e) => setShowRemoved(e.target.checked) }), "Ook ", removedCount, " verdwenen host", removedCount > 1 ? "s" : "", " tonen"] })), children: hosts.isLoading ? (_jsx(Loading, {})) : !shown.length ? (_jsx(Empty, { children: c.last_sync_status ? "Geen proxy hosts gevonden." : "Nog niet gesynchroniseerd." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Host" }), _jsx("th", { children: "Doel" }), _jsx("th", { children: "Gedetecteerd" }), _jsx("th", { children: "Waarschuwingen" }), _jsx("th", { children: "Catalogus" })] }) }), _jsx("tbody", { children: shown.map((h) => (_jsxs("tr", { className: h.removed_at || h.ignored ? "dim" : undefined, children: [_jsxs("td", { children: [_jsx("div", { className: "stack", children: h.domain_names.map((d) => (_jsx(HostLink, { domain: d, ssl: h.ssl }, d))) }), _jsxs("div", { className: "row wrap small", children: [_jsxs("span", { className: "muted", children: ["#", h.npm_id] }), h.removed_at && _jsxs(Badge, { children: ["verdwenen ", relative(h.removed_at)] }), !h.enabled && _jsx(Badge, { tone: "bad", children: "uitgeschakeld" }), !h.nginx_online && _jsx(Badge, { tone: "bad", children: "nginx-fout" }), h.ssl ? _jsx(Badge, { tone: "good", children: "TLS" }) : _jsx(Badge, { tone: "warn", children: "geen TLS" })] })] }), _jsxs("td", { className: "mono small", children: [h.forward_scheme, "://", h.forward_host, ":", h.forward_port] }), _jsxs("td", { children: [_jsx(AuthLabel, { method: h.detected_auth }), h.vaultx_managed && (_jsx(Badge, { tone: "info", title: "Authentik-config gezet door VaultX", children: "door VaultX" })), h.vaultx_published && (_jsx(Badge, { tone: "info", title: "Deze host werd aangemaakt via 'App publiceren'", children: "gepubliceerd door VaultX" })), h.access_list && _jsxs("div", { className: "muted small", children: ["access list: ", h.access_list] }), h.detected_app_type && _jsx("div", { className: "mono small muted", children: h.detected_app_type }), Object.keys(h.labels).length > 0 && (_jsx("div", { className: "row wrap", children: Object.entries(h.labels).map(([k, v]) => (_jsxs(Badge, { tone: "info", title: "Label uit Advanced", children: [k, "=", v] }, k))) })), manage && c.write_enabled && !h.removed_at && (_jsxs("div", { children: [h.vaultx_managed ? (_jsx("button", { className: "btn btn-ghost small btn-danger-text", onClick: () => setProtecting({ host: h, action: "unprotect" }), children: "Bescherming weghalen" })) : (h.detected_auth !== "forward_auth" && (_jsx("button", { className: "btn btn-ghost small", onClick: () => setProtecting({ host: h, action: "protect" }), children: "Beschermen met Authentik" }))), h.vaultx_published && (_jsx("button", { className: "btn btn-ghost small btn-danger-text", onClick: () => setUnpublishing(h), children: "Depubliceren" }))] }))] }), _jsx("td", { className: "small", children: _jsx(Warnings, { items: h.warnings }) }), _jsxs("td", { children: [h.application ? (_jsx(Link, { to: `/catalog/${orgId}/${h.application.id}`, children: h.application.name })) : h.ignored ? (_jsx(Badge, { children: "genegeerd" })) : h.labels.ignore ? (_jsx(Badge, { title: "Label vaultx.ignore", children: "genegeerd (label)" })) : (_jsx("span", { className: "muted", children: "\u2014" })), manage && !h.removed_at && !h.labels.ignore && (_jsx("div", { children: _jsx("button", { className: "btn btn-ghost small", disabled: ignore.isPending, onClick: () => {
                                                            if (h.ignored ||
                                                                confirm(`${h.domain_names[0]} negeren? Een automatisch aangemaakt catalogusitem verdwijnt.`))
                                                                ignore.mutate(h);
                                                        }, children: h.ignored ? "Weer opnemen" : "Negeren" }) }))] })] }, h.id))) })] }) })) }), manage && (c.write_enabled || hasChanges(all)) && (_jsx(Card, { title: "Wijzigingen door VaultX", children: _jsx(ChangeJournal, { base: base }) })), publishing && (_jsx(PublishDialog, { base: base, orgId: orgId, conn: c, onClose: () => setPublishing(false), onDone: invalidate })), unpublishing && (_jsx(UnpublishDialog, { base: base, host: unpublishing, onClose: () => setUnpublishing(null), onDone: invalidate })), protecting && (_jsx(ProtectionDialog, { base: base, orgId: orgId, authentik: !!c.authentik_outpost_pk, host: protecting.host, action: protecting.action, onClose: () => setProtecting(null), onDone: invalidate })), _jsx(Modal, { title: "Koppeling bewerken", open: editing, onClose: () => setEditing(false), children: _jsx(ConnectionForm, { orgId: orgId, initial: c, onSubmit: (v) => update.mutate(v), error: update.error, pending: update.isPending }) })] }));
}
function hasChanges(hosts) {
    return hosts.some((h) => h.vaultx_managed || h.vaultx_published);
}
