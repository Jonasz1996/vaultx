import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// API-sleutels voor de lees-API (/api/v1/stats), bv. voor een eigen dashboard (eigen account).
// Een sleutel kan enkel tellingen lezen: geen kluisitems, notities of links, en niets wijzigen.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { formatDate, relative } from "../format";
import { Card, ErrorBox } from "./ui";
const BASE = "/api/v1/me/api-keys";
export function ApiKeys({ isAdmin }) {
    const qc = useQueryClient();
    const q = useQuery({ queryKey: ["me", "api-keys"], queryFn: () => api.get(BASE) });
    const [adding, setAdding] = useState(false);
    const [name, setName] = useState("Dashboard");
    const [days, setDays] = useState("");
    const [token, setToken] = useState(null);
    const [copied, setCopied] = useState(false);
    const refresh = () => void qc.invalidateQueries({ queryKey: ["me", "api-keys"] });
    const add = useMutation({
        mutationFn: () => api.post(BASE, { name, expires_in_days: days ? Number(days) : null }),
        onSuccess: (data) => {
            setToken(data.token);
            setCopied(false);
            setAdding(false);
            refresh();
        },
    });
    const revoke = useMutation({ mutationFn: (id) => api.delete(`${BASE}/${id}`), onSuccess: refresh });
    const keys = q.data ?? [];
    const example = `curl -H "Authorization: Bearer ${token ?? "vxk_..."}" ${window.location.origin}/api/v1/stats`;
    return (_jsxs(Card, { title: "API-sleutels voor je dashboard", actions: !adding && (_jsx("button", { className: "btn", onClick: () => setAdding(true), children: "Sleutel aanmaken" })), children: [_jsx(ErrorBox, { error: q.error ?? add.error ?? revoke.error }), _jsxs("p", { className: "muted small", children: ["Met een sleutel leest een ander programma (bv. je dashboard) statistieken uit VaultX via", " ", _jsx("code", { className: "mono", children: "/api/v1/stats" }), ": aantal kluisitems per soort, ingelogde toestellen, sessies, apps, links en logins van de laatste 24 uur.", " ", isAdmin ? "Als beheerder krijg je de cijfers van heel VaultX." : "Je krijgt enkel je eigen cijfers.", " Een sleutel kan niets wijzigen en geeft nooit wachtwoorden, notities of links zelf."] }), token && (_jsxs("div", { className: "info-box", children: [_jsxs("p", { children: [_jsx("b", { children: "Kopieer deze sleutel nu." }), " VaultX bewaart enkel een hash; je ziet hem maar \u00E9\u00E9n keer."] }), _jsx("code", { className: "mono recovery-code", children: token }), " ", _jsx("button", { className: "btn btn-ghost small", onClick: () => void navigator.clipboard?.writeText(token).then(() => setCopied(true)), children: copied ? "Gekopieerd" : "Kopiëren" }), _jsx("p", { className: "muted small", children: "Proberen:" }), _jsx("code", { className: "mono small", children: example })] })), adding && (_jsxs("form", { className: "form", style: { maxWidth: 520 }, onSubmit: (e) => {
                    e.preventDefault();
                    add.mutate();
                }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam (om de sleutel later te herkennen)" }), _jsx("input", { required: true, maxLength: 100, value: name, onChange: (e) => setName(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Geldig" }), _jsxs("select", { value: days, onChange: (e) => setDays(e.target.value), children: [_jsx("option", { value: "", children: "Altijd (tot je hem intrekt)" }), _jsx("option", { value: "30", children: "30 dagen" }), _jsx("option", { value: "90", children: "90 dagen" }), _jsx("option", { value: "365", children: "1 jaar" })] })] }), _jsxs("div", { className: "row", children: [_jsx("button", { className: "btn btn-primary", disabled: add.isPending, children: "Aanmaken" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setAdding(false), children: "Annuleren" })] })] })), keys.length > 0 && (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Naam" }), _jsx("th", { children: "Sleutel" }), _jsx("th", { children: "Aangemaakt" }), _jsx("th", { children: "Verloopt" }), _jsx("th", { children: "Laatst gebruikt" }), _jsx("th", {})] }) }), _jsx("tbody", { children: keys.map((k) => (_jsxs("tr", { children: [_jsx("td", { children: k.name }), _jsxs("td", { className: "mono", children: [k.prefix, "\u2026"] }), _jsx("td", { children: formatDate(k.created_at) }), _jsx("td", { children: k.expires_at ? formatDate(k.expires_at) : "nooit" }), _jsx("td", { children: k.last_used_at ? `${relative(k.last_used_at)}${k.last_used_ip ? ` vanaf ${k.last_used_ip}` : ""}` : "nog niet" }), _jsx("td", { className: "right", children: _jsx("button", { className: "btn btn-ghost btn-danger-text", disabled: revoke.isPending, onClick: () => revoke.mutate(k.id), children: "Intrekken" }) })] }, k.id))) })] }) }))] }));
}
