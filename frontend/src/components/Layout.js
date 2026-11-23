import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
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
    { to: "/links", label: "Links" },
    { to: "/users", label: "Gebruikers", adminOnly: true },
    { to: "/organizations", label: "Organisaties" },
    { to: "/teams", label: "Teams" },
    { to: "/catalog", label: "Catalogus" },
    { to: "/npm", label: "NPM" },
    { to: "/audit", label: "Audit" },
];
export function Layout() {
    const { data: me } = useMe();
    return (_jsxs("div", { className: "shell", children: [_jsxs("aside", { className: "sidebar", children: [_jsxs("div", { className: "brand", children: [_jsx("img", { src: "/favicon.svg", alt: "", width: 28, height: 28 }), _jsx("span", { children: "VaultX" }), _jsx("small", { children: "admin" })] }), _jsx("nav", { children: nav
                            .filter((n) => !n.adminOnly || me?.is_admin)
                            .map((n) => (_jsx(NavLink, { to: n.to, end: n.end, children: n.label }, n.to))) }), me && (_jsxs("div", { className: "whoami", children: [_jsx(Link, { className: "whoami-name", to: `/users/${me.id}`, title: "Mijn account", children: userLabel(me) }), _jsx("div", { className: "muted small", children: me.is_admin ? "Instantiebeheerder" : me.email }), _jsx("button", { className: "btn btn-ghost", onClick: () => void logout(), children: "Uitloggen" })] }))] }), _jsx("main", { className: "content", children: _jsx(VaultKeyProvider, { children: _jsx(WebVaultProvider, { children: _jsx(Outlet, {}) }) }) })] }));
}
