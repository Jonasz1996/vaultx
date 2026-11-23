import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { roleLabels, userLabel } from "../format";
import { UserPicker } from "./UserPicker";
import { Card, Empty, ErrorBox, Loading, Modal, SourceBadge } from "./ui";
/** Ledenbeheer voor een organisatie (teamId undefined) of een team. */
export function Members({ basePath, roles, canManage, canGrantOwner = false, title = "Leden", }) {
    const qc = useQueryClient();
    const key = ["members", basePath];
    const [adding, setAdding] = useState(false);
    const [role, setRole] = useState(roles[roles.length - 1]);
    const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => api.get(`${basePath}/members`) });
    const invalidate = () => {
        void qc.invalidateQueries({ queryKey: key });
        void qc.invalidateQueries({ queryKey: ["teams"] });
        void qc.invalidateQueries({ queryKey: ["org"] });
    };
    const add = useMutation({
        mutationFn: (userId) => api.post(`${basePath}/members`, { user_id: userId, role }),
        onSuccess: () => {
            setAdding(false);
            invalidate();
        },
    });
    const update = useMutation({
        mutationFn: ({ id, role }) => api.patch(`${basePath}/members/${id}`, { role }),
        onSuccess: invalidate,
    });
    const remove = useMutation({
        mutationFn: (id) => api.delete(`${basePath}/members/${id}`),
        onSuccess: invalidate,
    });
    const grantable = roles.filter((r) => r !== "owner" || canGrantOwner);
    return (_jsxs(Card, { title: `${title}${data ? ` (${data.length})` : ""}`, actions: canManage && (_jsx("button", { className: "btn btn-primary", onClick: () => setAdding(true), children: "Lid toevoegen" })), children: [_jsx(ErrorBox, { error: error ?? update.error ?? remove.error }), isLoading ? (_jsx(Loading, {})) : !data?.length ? (_jsx(Empty, { children: "Nog geen leden." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Gebruiker" }), _jsx("th", { children: "Rol" }), _jsx("th", { children: "Bron" }), canManage && _jsx("th", {})] }) }), _jsx("tbody", { children: data.map((m) => {
                                const editable = canManage && m.source === "manual" && (m.role !== "owner" || canGrantOwner);
                                return (_jsxs("tr", { children: [_jsxs("td", { children: [_jsx(Link, { to: `/users/${m.user.id}`, children: userLabel(m.user) }), _jsx("div", { className: "muted small", children: m.user.email })] }), _jsx("td", { children: editable ? (_jsx("select", { value: m.role, onChange: (e) => update.mutate({ id: m.id, role: e.target.value }), "aria-label": "Rol", children: grantable.map((r) => (_jsx("option", { value: r, children: roleLabels[r] }, r))) })) : (roleLabels[m.role] ?? m.role) }), _jsx("td", { children: _jsx(SourceBadge, { source: m.source }) }), canManage && (_jsx("td", { className: "right", children: editable && (_jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => {
                                                    if (confirm(`${userLabel(m.user)} verwijderen?`))
                                                        remove.mutate(m.id);
                                                }, children: "Verwijderen" })) }))] }, m.id));
                            }) })] }) })), _jsxs(Modal, { title: "Lid toevoegen", open: adding, onClose: () => setAdding(false), children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Rol" }), _jsx("select", { value: role, onChange: (e) => setRole(e.target.value), children: grantable.map((r) => (_jsx("option", { value: r, children: roleLabels[r] }, r))) })] }), _jsx(UserPicker, { onPick: (u) => add.mutate(u.id), exclude: new Set(data?.map((m) => m.user.id)) }), _jsx(ErrorBox, { error: add.error })] })] }));
}
