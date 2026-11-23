import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, isOrgOwner, useMe } from "../api/hooks";
import { AuditTable } from "../components/AuditTable";
import { EntityForm } from "../components/EntityForm";
import { Members } from "../components/Members";
import { Badge, Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
export function OrganizationDetailPage() {
    const { orgId = "" } = useParams();
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [editing, setEditing] = useState(false);
    const [creatingTeam, setCreatingTeam] = useState(false);
    const base = `/api/v1/organizations/${orgId}`;
    const manage = canManageOrg(me, orgId);
    const owner = isOrgOwner(me, orgId);
    const org = useQuery({ queryKey: ["org", orgId], queryFn: () => api.get(base) });
    const teams = useQuery({ queryKey: ["teams", orgId], queryFn: () => api.get(`${base}/teams`) });
    const audit = useQuery({
        queryKey: ["audit", "org", orgId],
        queryFn: () => api.get("/api/v1/audit", { organization_id: orgId, limit: 15 }),
        enabled: manage,
    });
    const verify = useMutation({
        mutationFn: () => api.get("/api/v1/audit/verify", { organization_id: orgId }),
    });
    const update = useMutation({
        mutationFn: (v) => api.patch(base, { name: v.name, description: v.description || null }),
        onSuccess: () => {
            setEditing(false);
            void qc.invalidateQueries({ queryKey: ["org", orgId] });
            void qc.invalidateQueries({ queryKey: ["orgs"] });
        },
    });
    const remove = useMutation({
        mutationFn: () => api.delete(base),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: ["orgs"] });
            void qc.invalidateQueries({ queryKey: ["me"] });
            navigate("/organizations");
        },
    });
    const createTeam = useMutation({
        mutationFn: (v) => api.post(`${base}/teams`, { ...v, description: v.description || null }),
        onSuccess: (t) => {
            setCreatingTeam(false);
            void qc.invalidateQueries({ queryKey: ["teams", orgId] });
            navigate(`/organizations/${orgId}/teams/${t.id}`);
        },
    });
    if (org.isLoading)
        return _jsx(Loading, {});
    if (!org.data)
        return _jsx(ErrorBox, { error: org.error });
    const o = org.data;
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: o.name, subtitle: _jsxs(_Fragment, { children: [_jsx("span", { className: "mono", children: o.slug }), " ", o.description && `· ${o.description}`] }), actions: _jsxs(_Fragment, { children: [manage && (_jsx("button", { className: "btn", onClick: () => setEditing(true), children: "Bewerken" })), owner && (_jsx("button", { className: "btn btn-danger", onClick: () => {
                                if (confirm(`Organisatie '${o.name}' en al haar teams verwijderen?`))
                                    remove.mutate();
                            }, children: "Verwijderen" }))] }) }), _jsx(ErrorBox, { error: remove.error }), _jsxs("div", { className: "grid-2", children: [_jsx(Members, { basePath: base, roles: ["owner", "admin", "member"], canManage: manage, canGrantOwner: owner }), _jsx(Card, { title: `Teams${teams.data ? ` (${teams.data.length})` : ""}`, actions: manage && (_jsx("button", { className: "btn btn-primary", onClick: () => setCreatingTeam(true), children: "Nieuw team" })), children: !teams.data?.length ? (_jsx(Empty, { children: "Nog geen teams." })) : (_jsx("ul", { className: "plain-list", children: teams.data.map((t) => (_jsxs("li", { children: [_jsx(Link, { to: `/organizations/${orgId}/teams/${t.id}`, children: t.name }), _jsxs("span", { className: "muted", children: [" \u00B7 ", t.member_count, " leden"] })] }, t.id))) })) })] }), manage && (_jsx(Card, { title: "Auditlog van deze organisatie", actions: _jsxs("div", { className: "row", children: [verify.data &&
                            (verify.data.valid ? (_jsxs(Badge, { tone: "good", children: ["keten intact (", verify.data.entries_checked, ")"] })) : (_jsxs(Badge, { tone: "bad", children: ["keten gebroken bij #", verify.data.broken_at_id, ": ", verify.data.reason] }))), _jsx("button", { className: "btn", onClick: () => verify.mutate(), disabled: verify.isPending, children: "Keten controleren" }), _jsx(Link, { to: `/audit?organization_id=${orgId}`, children: "Alles" })] }), children: audit.data?.items.length ? _jsx(AuditTable, { items: audit.data.items }) : _jsx(Empty, { children: "Geen gebeurtenissen." }) })), _jsx(Modal, { title: "Organisatie bewerken", open: editing, onClose: () => setEditing(false), children: _jsx(EntityForm, { editing: true, initial: { name: o.name, slug: o.slug, description: o.description ?? "" }, onSubmit: (v) => update.mutate(v), error: update.error, pending: update.isPending }) }), _jsx(Modal, { title: "Nieuw team", open: creatingTeam, onClose: () => setCreatingTeam(false), children: _jsx(EntityForm, { onSubmit: (v) => createTeam.mutate(v), error: createTeam.error, pending: createTeam.isPending, slugHelp: `Authentik-groep 'vaultx:${o.slug}/<slug>' maakt gebruikers automatisch lid.` }) })] }));
}
