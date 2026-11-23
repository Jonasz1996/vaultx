import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { CreateAccountForm } from "../components/accounts";
import { Badge, Card, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { relative, userLabel } from "../format";
const PAGE = 25;
export function UsersPage() {
    const [q, setQ] = useState("");
    const [offset, setOffset] = useState(0);
    const [creating, setCreating] = useState(false);
    const qc = useQueryClient();
    const navigate = useNavigate();
    const methods = useQuery({
        queryKey: ["auth-methods"],
        queryFn: () => api.get("/auth/methods"),
    });
    const create = useMutation({
        mutationFn: (v) => api.post("/api/v1/users", v),
        onSuccess: (u) => {
            void qc.invalidateQueries({ queryKey: ["users"] });
            setCreating(false);
            navigate(`/users/${u.id}`);
        },
    });
    const { data, error, isLoading } = useQuery({
        queryKey: ["users", q, offset],
        queryFn: () => api.get("/api/v1/users", { q, limit: PAGE, offset }),
        placeholderData: keepPreviousData,
    });
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Gebruikers", subtitle: "Authentik-gebruikers verschijnen bij hun eerste login. Gewone accounts met een VaultX-wachtwoord maak je hier zelf aan.", actions: methods.data?.password && (_jsx("button", { className: "btn btn-primary", onClick: () => setCreating(true), children: "Account aanmaken" })) }), _jsx(Modal, { title: "Gewoon account aanmaken", open: creating, onClose: () => setCreating(false), children: _jsx(CreateAccountForm, { onSubmit: (v) => create.mutate(v), error: create.error, pending: create.isPending }) }), _jsxs(Card, { actions: _jsx("input", { className: "search", placeholder: "Zoeken\u2026", value: q, onChange: (e) => {
                        setQ(e.target.value);
                        setOffset(0);
                    } }), children: [_jsx(ErrorBox, { error: error }), isLoading ? (_jsx(Loading, {})) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Naam" }), _jsx("th", { children: "E-mail" }), _jsx("th", { children: "Status" }), _jsx("th", { children: "Inloggen" }), _jsx("th", { children: "Authentik-groepen" }), _jsx("th", { children: "Laatste login" })] }) }), _jsx("tbody", { children: data?.items.map((u) => (_jsxs("tr", { children: [_jsxs("td", { children: [_jsx(Link, { to: `/users/${u.id}`, children: userLabel(u) }), u.is_admin && _jsx(Badge, { tone: "info", children: "admin" })] }), _jsx("td", { children: u.email }), _jsx("td", { children: u.is_active ? _jsx(Badge, { tone: "good", children: "actief" }) : _jsx(Badge, { tone: "bad", children: "gedeactiveerd" }) }), _jsxs("td", { className: "nowrap", children: [u.has_authentik && _jsx(Badge, { tone: "info", children: "Authentik" }), " ", u.has_password && _jsx(Badge, { children: "wachtwoord" })] }), _jsx("td", { className: "small", children: u.idp_groups?.join(", ") }), _jsx("td", { className: "nowrap", children: relative(u.last_login_at) })] }, u.id))) })] }) })), data && data.total > PAGE && (_jsxs("div", { className: "pager", children: [_jsx("button", { className: "btn", disabled: offset === 0, onClick: () => setOffset(offset - PAGE), children: "Vorige" }), _jsxs("span", { className: "muted", children: [offset + 1, "\u2013", Math.min(offset + PAGE, data.total), " van ", data.total] }), _jsx("button", { className: "btn", disabled: offset + PAGE >= data.total, onClick: () => setOffset(offset + PAGE), children: "Volgende" })] }))] })] }));
}
