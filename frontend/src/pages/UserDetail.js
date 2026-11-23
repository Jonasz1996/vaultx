import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import { PasswordForm } from "../components/accounts";
import { AuditTable } from "../components/AuditTable";
import { ApiKeys } from "../components/apiKeys";
import { LoginSecurityKeys } from "../components/loginKeys";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader, SourceBadge } from "../components/ui";
import { formatDate, relative, roleLabels, userLabel } from "../format";
export function UserDetailPage() {
    const { userId = "" } = useParams();
    const qc = useQueryClient();
    const { data: me } = useMe();
    const isSelf = me?.id === userId;
    const [params] = useSearchParams();
    const restored = isSelf && params.has("hersteld");
    const user = useQuery({ queryKey: ["user", userId], queryFn: () => api.get(`/api/v1/users/${userId}`) });
    const sessions = useQuery({
        queryKey: ["user", userId, "sessions"],
        queryFn: () => api.get(`/api/v1/users/${userId}/sessions`),
    });
    const audit = useQuery({
        queryKey: ["audit", "actor", userId],
        queryFn: () => api.get("/api/v1/audit", { actor_user_id: userId, limit: 20 }),
        enabled: !!me?.is_admin,
    });
    const toggle = useMutation({
        mutationFn: (is_active) => api.patch(`/api/v1/users/${userId}`, { is_active }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["user", userId] }),
    });
    const [pwOpen, setPwOpen] = useState(null);
    const [pwDone, setPwDone] = useState(null);
    const refresh = () => {
        void qc.invalidateQueries({ queryKey: ["user", userId] });
        void qc.invalidateQueries({ queryKey: ["me"] });
    };
    const setAdmin = useMutation({
        mutationFn: (is_admin) => api.patch(`/api/v1/users/${userId}`, { is_admin }),
        onSuccess: refresh,
    });
    const setPassword = useMutation({
        mutationFn: (password) => api.put(`/api/v1/users/${userId}/password`, { password }),
        onSuccess: () => {
            refresh();
            setPwOpen(null);
            setPwDone(isSelf
                ? "Je wachtwoord is ingesteld. Je kan nu ook zonder Authentik inloggen."
                : "Het wachtwoord is ingesteld. Bezorg het veilig aan de gebruiker.");
        },
    });
    const removePassword = useMutation({
        mutationFn: () => api.delete(`/api/v1/users/${userId}/password`),
        onSuccess: refresh,
    });
    const changeOwn = useMutation({
        mutationFn: (v) => api.post("/api/v1/me/password", v),
        onSuccess: () => {
            refresh();
            setPwOpen(null);
            setPwDone("Je wachtwoord is gewijzigd. Andere sessies die met je wachtwoord inlogden, zijn afgemeld.");
        },
    });
    const revoke = useMutation({
        mutationFn: () => api.post(`/api/v1/users/${userId}/sessions/revoke`),
        onSuccess: () => qc.invalidateQueries({ queryKey: ["user", userId, "sessions"] }),
    });
    if (user.isLoading)
        return _jsx(Loading, {});
    if (!user.data)
        return _jsx(ErrorBox, { error: user.error });
    const u = user.data;
    const locked = !!u.locked_until && new Date(u.locked_until) > new Date();
    const adminViaGroup = u.is_admin && !u.admin_granted;
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: userLabel(u), subtitle: _jsxs(_Fragment, { children: [u.email, " ", u.is_admin && _jsx(Badge, { tone: "info", children: "instantiebeheerder" }), " ", u.is_active ? _jsx(Badge, { tone: "good", children: "actief" }) : _jsx(Badge, { tone: "bad", children: "gedeactiveerd" })] }), actions: me?.is_admin &&
                    !isSelf && (_jsx("button", { className: u.is_active ? "btn btn-danger" : "btn", onClick: () => toggle.mutate(!u.is_active), disabled: toggle.isPending, children: u.is_active ? "Deactiveren" : "Activeren" })) }), _jsx(ErrorBox, { error: toggle.error ?? revoke.error ?? setAdmin.error ?? removePassword.error }), pwDone && _jsx("div", { className: "info-box", children: pwDone }), restored && (_jsxs("div", { className: "info-box", children: [_jsx("strong", { children: "Je account is hersteld." }), " Je nieuwe wachtwoord is ingesteld. Stel nu je tweestapsverificatie opnieuw in: een beveiligingssleutel of passkey voor je login (hieronder, bij", " ", _jsx("em", { children: "Beveiligingssleutels voor het wachtwoordlogin" }), ") en de tweestapsverificatie van je kluis op", " ", _jsx(Link, { to: "/vault/settings", children: "Mijn kluis" }), ". Je kluis zelf en je master password zijn niet gewijzigd."] })), _jsxs("div", { className: "grid-2", children: [_jsx(Card, { title: "Identiteit", children: _jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Gebruikersnaam" }), _jsx("dd", { children: u.username }), _jsx("dt", { children: "Inloggen" }), _jsxs("dd", { children: [u.has_authentik && _jsx(Badge, { tone: "info", children: "Authentik" }), " ", u.has_password && _jsx(Badge, { children: "VaultX-wachtwoord" }), locked && (_jsxs(Badge, { tone: "bad", title: "Te veel foute wachtwoorden", children: ["vergrendeld tot ", formatDate(u.locked_until)] }))] }), u.has_authentik && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Issuer" }), _jsx("dd", { className: "mono small", children: u.oidc_issuer }), _jsx("dt", { children: "Subject" }), _jsx("dd", { className: "mono small", children: u.oidc_subject }), _jsx("dt", { children: "Authentik-groepen" }), _jsx("dd", { children: u.idp_groups?.length ? u.idp_groups.map((g) => _jsx(Badge, { children: g }, g)) : "—" })] })), u.has_password && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Wachtwoord gewijzigd" }), _jsx("dd", { children: formatDate(u.password_changed_at) })] })), _jsx("dt", { children: "Laatste login" }), _jsx("dd", { children: formatDate(u.last_login_at) }), _jsx("dt", { children: "Aangemaakt" }), _jsx("dd", { children: formatDate(u.created_at) })] }) }), _jsx(Card, { title: "Lidmaatschappen", children: u.memberships.length === 0 ? (_jsx(Empty, { children: "Geen lidmaatschappen." })) : (_jsx("ul", { className: "plain-list", children: u.memberships.map((m) => (_jsxs("li", { children: [_jsx(Link, { to: `/organizations/${m.organization_id}`, children: m.organization_name }), m.team_name && (_jsxs(_Fragment, { children: [" / ", _jsx(Link, { to: `/organizations/${m.organization_id}/teams/${m.team_id}`, children: m.team_name })] })), _jsxs("span", { className: "muted", children: [" \u00B7 ", roleLabels[m.role] ?? m.role, " "] }), _jsx(SourceBadge, { source: m.source })] }, m.id))) })) })] }), (me?.is_admin || (isSelf && u.has_password)) && (_jsx(Card, { title: "Toegang", children: _jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "Wachtwoordlogin" }), _jsxs("dd", { children: [u.has_password ? "Kan inloggen met gebruikersnaam of e-mail en wachtwoord." : "Geen VaultX-wachtwoord.", _jsxs("div", { className: "actions-inline", children: [isSelf && u.has_password && (_jsx("button", { className: "btn", onClick: () => setPwOpen("own"), children: "Mijn wachtwoord wijzigen" })), me?.is_admin && (_jsx("button", { className: "btn", onClick: () => setPwOpen("admin"), children: u.has_password ? "Wachtwoord resetten" : "Wachtwoord instellen" })), me?.is_admin && u.has_password && u.has_authentik && (_jsx("button", { className: "btn btn-danger", disabled: removePassword.isPending, onClick: () => {
                                                if (confirm("Wachtwoordlogin weghalen? Inloggen kan dan enkel nog via Authentik.")) {
                                                    removePassword.mutate();
                                                }
                                            }, children: "Wachtwoordlogin weghalen" }))] })] }), me?.is_admin && (_jsxs(_Fragment, { children: [_jsx("dt", { children: "Beheerdersrechten" }), _jsxs("dd", { children: [u.admin_granted
                                            ? "Instantiebeheerder, toegekend in VaultX."
                                            : adminViaGroup
                                                ? "Instantiebeheerder via een Authentik-groep."
                                                : "Geen instantiebeheerder.", !(isSelf && u.admin_granted && !adminViaGroup) && (_jsx("div", { className: "actions-inline", children: _jsx("button", { className: u.admin_granted ? "btn btn-danger" : "btn", disabled: setAdmin.isPending, onClick: () => setAdmin.mutate(!u.admin_granted), children: u.admin_granted ? "Rechten intrekken in VaultX" : "Beheerder maken in VaultX" }) }))] })] }))] }) })), isSelf && u.has_password && _jsx(LoginSecurityKeys, {}), isSelf && _jsx(ApiKeys, { isAdmin: !!u.is_admin }), _jsxs(Modal, { title: pwOpen === "own" ? "Mijn wachtwoord wijzigen" : u.has_password ? "Wachtwoord resetten" : "Wachtwoord instellen", open: pwOpen !== null, onClose: () => setPwOpen(null), children: [pwOpen === "admin" && !u.has_password && u.has_authentik && (_jsxs("p", { className: "muted small", children: ["Daarna kan ", userLabel(u), " ook zonder Authentik inloggen, met gebruikersnaam ", u.username ?? u.email, " en dit wachtwoord. Het blijft hetzelfde account, met dezelfde kluis en notities."] })), _jsx(PasswordForm, { own: pwOpen === "own", submitLabel: pwOpen === "own" ? "Wijzigen" : "Opslaan", error: pwOpen === "own" ? changeOwn.error : setPassword.error, pending: pwOpen === "own" ? changeOwn.isPending : setPassword.isPending, onSubmit: (v) => pwOpen === "own"
                            ? changeOwn.mutate({ current_password: v.current_password, new_password: v.password })
                            : setPassword.mutate(v.password) })] }), _jsx(Card, { title: "Actieve sessies", actions: (sessions.data?.length ?? 0) > 0 && (_jsx("button", { className: "btn", onClick: () => revoke.mutate(), disabled: revoke.isPending, children: "Alle sessies intrekken" })), children: sessions.data?.length ? (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Gestart" }), _jsx("th", { children: "Via" }), _jsx("th", { children: "Laatst actief" }), _jsx("th", { children: "Verloopt" }), _jsx("th", { children: "IP" }), _jsx("th", { children: "Browser" })] }) }), _jsx("tbody", { children: sessions.data.map((s) => (_jsxs("tr", { children: [_jsxs("td", { children: [formatDate(s.created_at), " ", s.current && _jsx(Badge, { tone: "info", children: "deze sessie" })] }), _jsx("td", { children: s.auth_method === "password" ? "wachtwoord" : "Authentik" }), _jsx("td", { children: relative(s.last_seen_at) }), _jsx("td", { children: formatDate(s.expires_at) }), _jsx("td", { className: "mono small", children: s.ip_address }), _jsx("td", { className: "small truncate", children: s.user_agent })] }, s.id))) })] }) })) : (_jsx(Empty, { children: "Geen actieve sessies." })) }), me?.is_admin && (_jsx(Card, { title: "Recente activiteit", children: audit.data?.items.length ? _jsx(AuditTable, { items: audit.data.items }) : _jsx(Empty, { children: "Geen activiteit." }) }))] }));
}
