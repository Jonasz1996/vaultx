import { useEffect } from "react";
import { Outlet, createBrowserRouter } from "react-router";
import { ApiError, loginUrl } from "./api/client";
import { useMe } from "./api/hooks";
import { Layout } from "./components/Layout";
import { ErrorBox, Loading } from "./components/ui";
import { AuditPageView } from "./pages/Audit";
import { DashboardPage } from "./pages/Dashboard";
import { LoginPage } from "./pages/Login";
import { OrganizationDetailPage } from "./pages/OrganizationDetail";
import { OrganizationsPage } from "./pages/Organizations";
import { TeamDetailPage } from "./pages/TeamDetail";
import { TeamsPage } from "./pages/Teams";
import { UserDetailPage } from "./pages/UserDetail";
import { UsersPage } from "./pages/Users";

/** Laat enkel ingelogde gebruikers door; anders naar de loginpagina. */
function RequireAuth() {
  const { data, error, isLoading } = useMe();
  const unauthenticated = error instanceof ApiError && error.status === 401;
  useEffect(() => {
    if (unauthenticated) {
      const next = window.location.pathname + window.location.search;
      window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [unauthenticated]);
  if (isLoading || unauthenticated) return <Loading />;
  if (error || !data)
    return (
      <div className="content">
        <ErrorBox error={error} />
        <a href={loginUrl()}>Opnieuw inloggen</a>
      </div>
    );
  return <Outlet />;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { index: true, element: <DashboardPage /> },
          { path: "users", element: <UsersPage /> },
          { path: "users/:userId", element: <UserDetailPage /> },
          { path: "organizations", element: <OrganizationsPage /> },
          { path: "organizations/:orgId", element: <OrganizationDetailPage /> },
          { path: "organizations/:orgId/teams/:teamId", element: <TeamDetailPage /> },
          { path: "teams", element: <TeamsPage /> },
          { path: "audit", element: <AuditPageView /> },
        ],
      },
    ],
  },
]);
