import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api/client";
import { canManageOrg, canManageTeam, useMe } from "../api/hooks";
import { EntityForm } from "../components/EntityForm";
import { Members } from "../components/Members";
import { ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
export function TeamDetailPage() {
    const { orgId = "", teamId = "" } = useParams();
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [editing, setEditing] = useState(false);
    const base = `/api/v1/organizations/${orgId}/teams/${teamId}`;
    const org = useQuery({ queryKey: ["org", orgId], queryFn: () => api.get(`/api/v1/organizations/${orgId}`) });
    const team = useQuery({ queryKey: ["team", teamId], queryFn: () => api.get(base) });
    const update = useMutation({
        mutationFn: (v) => api.patch(base, { name: v.name, description: v.description || null }),
        onSuccess: () => {
            setEditing(false);
            void qc.invalidateQueries({ queryKey: ["team", teamId] });
            void qc.invalidateQueries({ queryKey: ["teams", orgId] });
        },
    });
    const remove = useMutation({
        mutationFn: () => api.delete(base),
        onSuccess: () => {
            void qc.invalidateQueries({ queryKey: ["teams", orgId] });
            navigate(`/organizations/${orgId}`);
        },
    });
    if (team.isLoading)
        return _jsx(Loading, {});
    if (!team.data)
        return _jsx(ErrorBox, { error: team.error });
    const t = team.data;
    const manageTeam = canManageTeam(me, orgId, teamId);
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: t.name, subtitle: _jsxs(_Fragment, { children: ["Team in ", _jsx(Link, { to: `/organizations/${orgId}`, children: org.data?.name ?? "organisatie" }), " \u00B7", " ", _jsxs("span", { className: "mono", children: [org.data?.slug, "/", t.slug] }), t.description && ` · ${t.description}`] }), actions: _jsxs(_Fragment, { children: [manageTeam && (_jsx("button", { className: "btn", onClick: () => setEditing(true), children: "Bewerken" })), canManageOrg(me, orgId) && (_jsx("button", { className: "btn btn-danger", onClick: () => {
                                if (confirm(`Team '${t.name}' verwijderen?`))
                                    remove.mutate();
                            }, children: "Verwijderen" }))] }) }), _jsx(ErrorBox, { error: remove.error }), _jsx(Members, { basePath: base, roles: ["maintainer", "member"], canManage: manageTeam, title: "Teamleden" }), _jsx(Modal, { title: "Team bewerken", open: editing, onClose: () => setEditing(false), children: _jsx(EntityForm, { editing: true, initial: { name: t.name, slug: t.slug, description: t.description ?? "" }, onSubmit: (v) => update.mutate(v), error: update.error, pending: update.isPending }) })] }));
}
