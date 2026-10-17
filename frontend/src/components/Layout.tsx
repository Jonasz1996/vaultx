import { NavLink, Outlet } from "react-router";
import { logout } from "../api/client";
import { useMe } from "../api/hooks";
import { userLabel } from "../format";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/vault", label: "Mijn kluis" },
  { to: "/users", label: "Gebruikers", adminOnly: true },
  { to: "/organizations", label: "Organisaties" },
  { to: "/teams", label: "Teams" },
  { to: "/catalog", label: "Catalogus" },
  { to: "/npm", label: "NPM" },
  { to: "/audit", label: "Audit" },
];

export function Layout() {
  const { data: me } = useMe();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <span>VaultX</span>
          <small>admin</small>
        </div>
        <nav>
          {nav
            .filter((n) => !n.adminOnly || me?.is_admin)
            .map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}>
                {n.label}
              </NavLink>
            ))}
        </nav>
        {me && (
          <div className="whoami">
            <div className="whoami-name">{userLabel(me)}</div>
            <div className="muted small">{me.is_admin ? "Instantiebeheerder" : me.email}</div>
            <button className="btn btn-ghost" onClick={() => void logout()}>
              Uitloggen
            </button>
          </div>
        )}
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
