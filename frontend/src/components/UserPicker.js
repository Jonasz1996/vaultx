import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { userLabel } from "../format";
/** Zoekveld om een bestaande gebruiker te kiezen (gebruikers ontstaan bij hun eerste login). */
export function UserPicker({ onPick, exclude }) {
    const [q, setQ] = useState("");
    const { data, isFetching } = useQuery({
        queryKey: ["users", "pick", q],
        queryFn: () => api.get("/api/v1/users", { q, limit: 8 }),
        enabled: q.trim().length >= 2,
    });
    const results = (data?.items ?? []).filter((u) => !exclude.has(u.id));
    return (_jsxs("div", { className: "picker", children: [_jsx("input", { autoFocus: true, placeholder: "Zoek op naam, e-mail of gebruikersnaam\u2026", value: q, onChange: (e) => setQ(e.target.value) }), q.trim().length >= 2 && (_jsxs("ul", { className: "picker-list", children: [isFetching && _jsx("li", { className: "muted", children: "Zoeken\u2026" }), !isFetching && results.length === 0 && (_jsx("li", { className: "muted", children: "Geen resultaten. Gebruikers verschijnen na hun eerste login via Authentik." })), results.map((u) => (_jsx("li", { children: _jsxs("button", { type: "button", onClick: () => onPick(u), children: [_jsx("strong", { children: userLabel(u) }), _jsx("span", { className: "muted", children: u.email })] }) }, u.id)))] }))] }));
}
