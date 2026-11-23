import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef } from "react";
import { ApiError } from "../api/client";
export function PageHeader({ title, subtitle, actions }) {
    return (_jsxs("header", { className: "page-header", children: [_jsxs("div", { children: [_jsx("h1", { children: title }), subtitle && _jsx("p", { className: "muted", children: subtitle })] }), actions && _jsx("div", { className: "actions", children: actions })] }));
}
export function Card({ title, actions, children }) {
    return (_jsxs("section", { className: "card", children: [(title || actions) && (_jsxs("div", { className: "card-head", children: [title && _jsx("h2", { children: title }), actions] })), children] }));
}
export function Badge({ tone = "neutral", children, title }) {
    return (_jsx("span", { className: `badge badge-${tone}`, title: title, children: children }));
}
export function Stat({ label, value, hint }) {
    return (_jsxs("div", { className: "stat", children: [_jsx("div", { className: "stat-value", children: value }), _jsx("div", { className: "stat-label", children: label }), hint && _jsx("div", { className: "stat-hint", children: hint })] }));
}
export function ErrorBox({ error }) {
    if (!error)
        return null;
    const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
    return _jsx("div", { className: "error-box", role: "alert", children: msg });
}
export function Loading() {
    return _jsx("div", { className: "loading", children: "Laden\u2026" });
}
export function Empty({ children }) {
    return _jsx("div", { className: "empty", children: children });
}
export function Modal({ title, open, onClose, children, wide = false, }) {
    const ref = useRef(null);
    useEffect(() => {
        const d = ref.current;
        if (!d)
            return;
        if (open && !d.open)
            d.showModal();
        if (!open && d.open)
            d.close();
    }, [open]);
    return (_jsxs("dialog", { ref: ref, className: wide ? "modal modal-wide" : "modal", onClose: onClose, children: [_jsxs("div", { className: "modal-head", children: [_jsx("h2", { children: title }), _jsx("button", { className: "btn-icon", onClick: onClose, "aria-label": "Sluiten", children: "\u00D7" })] }), open && children] }));
}
export function SourceBadge({ source }) {
    return source === "idp" ? (_jsx(Badge, { tone: "info", title: "Beheerd via een Authentik-groep", children: "Authentik" })) : (_jsx(Badge, { children: "Manueel" }));
}
