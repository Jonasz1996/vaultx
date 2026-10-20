import { Link, NavLink, Outlet } from "react-router";
import { logout } from "../api/client";
import { useMe } from "../api/hooks";
import { userLabel } from "../format";
import { VaultKeyProvider } from "../notes/session";
import { WebVaultProvider } from "../vault/webSession";

const nav = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/vault", label: "Mijn kluis" },
  { to: "/notes", label: "Notities" },
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
            <Link className="whoami-name" to={`/users/${me.id}`} title="Mijn account">
              {userLabel(me)}
            </Link>
            <div className="muted small">{me.is_admin ? "Instantiebeheerder" : me.email}</div>
            <button className="btn btn-ghost" onClick={() => void logout()}>
              Uitloggen
            </button>
          </div>
        )}
      </aside>
      <main className="content">
        <VaultKeyProvider>
          <WebVaultProvider>
            <Outlet />
          </WebVaultProvider>
        </VaultKeyProvider>
      </main>
    </div>
  );
}
