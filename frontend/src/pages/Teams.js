import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import { Card, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";
/** Overzicht van alle teams in de organisaties die de gebruiker kan zien. */
export function TeamsPage() {
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => api.get("/api/v1/organizations", { limit: 200 }),
    });
    const teamQueries = useQueries({
        queries: (orgs.data?.items ?? []).map((o) => ({
            queryKey: ["teams", o.id],
            queryFn: () => api.get(`/api/v1/organizations/${o.id}/teams`),
        })),
    });
    if (orgs.isLoading)
        return _jsx(Loading, {});
    const rows = (orgs.data?.items ?? []).flatMap((o, i) => (teamQueries[i]?.data ?? []).map((t) => ({ org: o, team: t })));
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Teams", subtitle: "Teams groeperen leden binnen een organisatie. Maak teams aan vanuit een organisatie." }), _jsxs(Card, { children: [_jsx(ErrorBox, { error: orgs.error }), rows.length === 0 ? (_jsx(Empty, { children: "Nog geen teams." })) : (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Team" }), _jsx("th", { children: "Organisatie" }), _jsx("th", { children: "Slug" }), _jsx("th", { children: "Leden" })] }) }), _jsx("tbody", { children: rows.map(({ org, team }) => (_jsxs("tr", { children: [_jsx("td", { children: _jsx(Link, { to: `/organizations/${org.id}/teams/${team.id}`, children: team.name }) }), _jsx("td", { children: _jsx(Link, { to: `/organizations/${org.id}`, children: org.name }) }), _jsxs("td", { className: "mono small", children: [org.slug, "/", team.slug] }), _jsx("td", { children: team.member_count })] }, team.id))) })] }) }))] })] }));
}
