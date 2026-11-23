import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Suspense, lazy, useEffect } from "react";
import { Outlet, createBrowserRouter } from "react-router";
import { ApiError, loginUrl } from "./api/client";
import { useMe } from "./api/hooks";
import { Layout } from "./components/Layout";
import { ErrorBox, Loading } from "./components/ui";
import { ApplicationDetailPage } from "./pages/ApplicationDetail";
import { AuditPageView } from "./pages/Audit";
import { CatalogPage } from "./pages/Catalog";
import { DashboardPage } from "./pages/Dashboard";
import { LoginPage } from "./pages/Login";
import { NpmConnectionDetailPage } from "./pages/NpmConnectionDetail";
import { NpmConnectionsPage } from "./pages/NpmConnections";
import { NpmManagePage } from "./pages/NpmManage";
import { OrganizationDetailPage } from "./pages/OrganizationDetail";
import { OrganizationsPage } from "./pages/Organizations";
import { TeamDetailPage } from "./pages/TeamDetail";
import { TeamsPage } from "./pages/Teams";
import { UserDetailPage } from "./pages/UserDetail";
import { UsersPage } from "./pages/Users";
import { VaultPage } from "./pages/Vault";
// Notities (Markdown, grafiek, zip) laden pas wanneer je ze opent.
const NotesPage = lazy(() => import("./pages/Notes").then((m) => ({ default: m.NotesPage })));
const LinksPage = lazy(() => import("./pages/Links").then((m) => ({ default: m.LinksPage })));
/** Laat enkel ingelogde gebruikers door; anders naar de loginpagina. */
function RequireAuth() {
    const { data, error, isLoading } = useMe();
    const unauthenticated = error instanceof ApiError && error.status === 401;
    useEffect(() => {
        if (unauthenticated) {
            const next = window.location.pathname + window.location.search + window.location.hash;
            window.location.replace(`/login?next=${encodeURIComponent(next)}`);
        }
    }, [unauthenticated]);
    if (isLoading || unauthenticated)
        return _jsx(Loading, {});
    if (error || !data)
        return (_jsxs("div", { className: "content", children: [_jsx(ErrorBox, { error: error }), _jsx("a", { href: loginUrl(), children: "Opnieuw inloggen" })] }));
    return _jsx(Outlet, {});
}
export const router = createBrowserRouter([
    { path: "/login", element: _jsx(LoginPage, {}) },
    {
        element: _jsx(RequireAuth, {}),
        children: [
            {
                element: _jsx(Layout, {}),
                children: [
                    { index: true, element: _jsx(DashboardPage, {}) },
                    { path: "vault", element: _jsx(VaultPage, { tab: "items" }) },
                    { path: "vault/settings", element: _jsx(VaultPage, { tab: "settings" }) },
                    {
                        path: "notes",
                        element: (_jsx(Suspense, { fallback: _jsx(Loading, {}), children: _jsx(NotesPage, {}) })),
                    },
                    {
                        path: "links",
                        element: (_jsx(Suspense, { fallback: _jsx(Loading, {}), children: _jsx(LinksPage, {}) })),
                    },
                    { path: "users", element: _jsx(UsersPage, {}) },
                    { path: "users/:userId", element: _jsx(UserDetailPage, {}) },
                    { path: "organizations", element: _jsx(OrganizationsPage, {}) },
                    { path: "organizations/:orgId", element: _jsx(OrganizationDetailPage, {}) },
                    { path: "organizations/:orgId/teams/:teamId", element: _jsx(TeamDetailPage, {}) },
                    { path: "teams", element: _jsx(TeamsPage, {}) },
                    { path: "catalog", element: _jsx(CatalogPage, {}) },
                    { path: "catalog/:orgId/:appId", element: _jsx(ApplicationDetailPage, {}) },
                    { path: "npm", element: _jsx(NpmConnectionsPage, {}) },
                    { path: "npm/:orgId/:connId", element: _jsx(NpmConnectionDetailPage, {}) },
                    { path: "npm/:orgId/:connId/beheer", element: _jsx(NpmManagePage, {}) },
                    { path: "audit", element: _jsx(AuditPageView, {}) },
                ],
            },
        ],
    },
]);
