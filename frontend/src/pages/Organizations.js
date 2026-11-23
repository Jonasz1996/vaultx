import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import { EntityForm } from "../components/EntityForm";
import { Card, Empty, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { roleLabels } from "../format";
export function OrganizationsPage() {
    const { data: me } = useMe();
    const qc = useQueryClient();
    const navigate = useNavigate();
    const [creating, setCreating] = useState(false);
    const { data, error, isLoading } = useQuery({
        queryKey: ["orgs"],
        queryFn: () => api.get("/api/v1/organizations", { limit: 200 }),
    });
    const create = useMutation({
        mutationFn: (v) => api.post("/api/v1/organizations", { ...v, description: v.description || null }),
        onSuccess: (org) => {
            void qc.invalidateQueries({ queryKey: ["orgs"] });
            void qc.invalidateQueries({ queryKey: ["me"] });
            navigate(`/organizations/${org.id}`);
        },
    });
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Organisaties", subtitle: "Tenants binnen VaultX. Teams, en later kluizen en applicaties, hangen onder een organisatie.", actions: me?.is_admin && (_jsx("button", { className: "btn btn-primary", onClick: () => setCreating(true), children: "Nieuwe organisatie" })) }), _jsxs(Card, { children: [_jsx(ErrorBox, { error: error }), isLoading ? (_jsx(Loading, {})) : !data?.items.length ? (_jsx(Empty, { children: me?.is_admin ? "Nog geen organisaties. Maak er een aan." : "Je bent nog geen lid van een organisatie." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Naam" }), _jsx("th", { children: "Slug" }), _jsx("th", { children: "Leden" }), _jsx("th", { children: "Teams" }), _jsx("th", { children: "Mijn rol" })] }) }), _jsx("tbody", { children: data.items.map((o) => (_jsxs("tr", { children: [_jsx("td", { children: _jsx(Link, { to: `/organizations/${o.id}`, children: o.name }) }), _jsx("td", { className: "mono small", children: o.slug }), _jsx("td", { children: o.member_count }), _jsx("td", { children: o.team_count }), _jsx("td", { children: o.my_role ? roleLabels[o.my_role] : _jsx("span", { className: "muted", children: "\u2014" }) })] }, o.id))) })] }) }))] }), _jsx(Modal, { title: "Nieuwe organisatie", open: creating, onClose: () => setCreating(false), children: _jsx(EntityForm, { onSubmit: (v) => create.mutate(v), error: create.error, pending: create.isPending, slugHelp: "Vast na aanmaken. Authentik-groep 'vaultx:<slug>' maakt gebruikers automatisch lid." }) })] }));
}
