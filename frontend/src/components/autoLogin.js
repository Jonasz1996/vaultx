import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { AccessPicker } from "./npmProtection";
import { Card, Empty, ErrorBox, Loading, Modal } from "./ui";
function accessText(login) {
    if (login.access === "all")
        return "alle Authentik-gebruikers";
    return login.groups.join(", ") || login.access;
}
function PlanView({ plan }) {
    return (_jsxs(_Fragment, { children: [plan.checks.length > 0 && (_jsx("ul", { className: "checks", children: plan.checks.map((c, i) => (_jsx("li", { className: `check-${c.level}`, title: c.code, children: c.message }, `${c.code}-${i}`))) })), plan.steps.length > 0 && (_jsxs("div", { children: [_jsx("strong", { children: "Wat VaultX doet" }), _jsx("ol", { className: "steps", children: plan.steps.map((s) => (_jsx("li", { children: s }, s))) })] }))] }));
}
function SetupDialog({ orgId, base, state, onClose, onDone, }) {
    const [appUrl, setAppUrl] = useState(state.suggested_app_url ?? "");
    const [access, setAccess] = useState("organization");
    const [redirects, setRedirects] = useState("");
    const input = () => ({
        redirect_uris: redirects
            .split("\n")
            .map((r) => r.trim())
            .filter(Boolean),
        access,
        app_url: appUrl.trim() || null,
    });
    const preview = useMutation({ mutationFn: () => api.post(`${base}/preview`, input()) });
    const apply = useMutation({
        mutationFn: () => api.post(base, input()),
        onSuccess: onDone,
    });
    const reset = () => {
        preview.reset();
        apply.reset();
    };
    const set = (fn) => (v) => {
        fn(v);
        reset();
    };
    const plan = preview.data;
    return (_jsx(Modal, { title: "Automatische login inrichten", open: true, onClose: onClose, wide: true, children: _jsxs("div", { className: "form", children: [_jsx("div", { className: "info-box small", children: "De app meldt zich zelf aan via Authentik (OpenID Connect). Wie al bij Authentik is aangemeld, komt meteen binnen, zonder tweede login en zonder wachtwoord voor de app. VaultX richt Authentik in; issuer, client ID en secret vul je daarna zelf in de app in." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "URL van de app" }), _jsx("input", { value: appUrl, onChange: (e) => set(setAppUrl)(e.target.value), placeholder: "https://app.example.be" })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Redirect URI's van de app (\u00E9\u00E9n per regel)" }), _jsx("textarea", { rows: 3, value: redirects, onChange: (e) => set(setRedirects)(e.target.value), placeholder: `${(appUrl.trim() || "https://app.example.be").replace(/\/+$/, "")}/oauth/callback` }), _jsx("small", { className: "muted", children: "Staat in de documentatie van de app, bij OpenID Connect of OAuth. Authentik aanvaardt enkel exact deze URI's." })] }), _jsx(AccessPicker, { orgId: orgId, value: access, onChange: set(setAccess), label: "Wie mag zich via Authentik aanmelden bij de app?" }), plan && _jsx(PlanView, { plan: plan }), _jsx(ErrorBox, { error: preview.error ?? apply.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { className: "btn", onClick: onClose, children: "Annuleren" }), !plan ? (_jsx("button", { className: "btn btn-primary", disabled: preview.isPending, onClick: () => preview.mutate(), children: preview.isPending ? "Bezig…" : "Voorbeeld bekijken" })) : (_jsx("button", { className: "btn btn-primary", disabled: !plan.can_apply || apply.isPending, onClick: () => apply.mutate(), children: apply.isPending ? "Bezig, even geduld…" : "Inrichten" }))] })] }) }));
}
function ConfigDialog({ base, onClose }) {
    const cfg = useQuery({
        queryKey: ["app-login-config", base],
        queryFn: () => api.get(`${base}/config`),
        staleTime: 0,
        gcTime: 0,
        refetchOnWindowFocus: false,
    });
    return (_jsx(Modal, { title: "Instellingen voor de app", open: true, onClose: onClose, wide: true, children: cfg.isLoading ? (_jsx(Loading, {})) : !cfg.data ? (_jsx(ErrorBox, { error: cfg.error })) : (_jsxs("div", { className: "form", children: [_jsx("div", { className: "warn-box small", children: "Bevat het client secret. Het opvragen staat in de auditlog; zet het secret niet in Git." }), _jsx("p", { className: "small", children: "Vul dit in bij de OpenID Connect- of OAuth-instellingen van de app. Meestal volstaan de discovery-URL (of issuer), client ID en secret. Meld je daarna aan in de app om te testen." }), _jsxs("div", { children: [_jsxs("div", { className: "row", children: [_jsx("strong", { children: "Config" }), _jsx("button", { className: "btn btn-ghost small", onClick: () => void navigator.clipboard?.writeText(cfg.data.text), children: "Kopi\u00EBren" })] }), _jsx("pre", { className: "snippet", children: cfg.data.text })] })] })) }));
}
function RemoveDialog({ base, login, onClose, onDone, }) {
    const remove = useMutation({
        mutationFn: () => api.post(`${base}/remove`),
        onSuccess: onDone,
    });
    return (_jsx(Modal, { title: "Automatische login weghalen", open: true, onClose: onClose, children: _jsxs("div", { className: "form", children: [_jsxs("p", { children: ["VaultX verwijdert in Authentik de applicatie ", _jsx("code", { children: login.application_slug }), " en provider '", login.provider_name, "'. Zet de login daarna ook in de app zelf uit: de app kan dan niet meer via Authentik aanmelden."] }), _jsx(ErrorBox, { error: remove.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { className: "btn", onClick: onClose, children: "Annuleren" }), _jsx("button", { className: "btn btn-danger", disabled: remove.isPending, onClick: () => remove.mutate(), children: remove.isPending ? "Bezig…" : "Weghalen" })] })] }) }));
}
export function AutoLoginCard({ orgId, app, manage }) {
    const base = `/api/v1/organizations/${orgId}/applications/${app.id}/login`;
    const qc = useQueryClient();
    const state = useQuery({ queryKey: ["app-login", app.id], queryFn: () => api.get(base) });
    const [dialog, setDialog] = useState(null);
    const refresh = () => {
        void qc.invalidateQueries({ queryKey: ["app-login", app.id] });
        void qc.invalidateQueries({ queryKey: ["app", app.id] });
        void qc.invalidateQueries({ queryKey: ["catalog"] });
        void qc.invalidateQueries({ queryKey: ["audit", "app", app.id] });
    };
    let body;
    if (state.isLoading)
        body = _jsx(Loading, {});
    else if (!state.data)
        body = _jsx(ErrorBox, { error: state.error });
    else if (!state.data.login) {
        body = !state.data.configured ? (_jsx(Empty, { children: "De Authentik-API is niet ingesteld op de VaultX-server (VAULTX_AUTHENTIK_API_TOKEN). Zie docs/npm.md, sectie 6." })) : (_jsxs("div", { className: "form", children: [_jsxs("p", { className: "muted", children: ["Nog niet ingericht. Laat ", app.name, " zelf via Authentik aanmelden: wie al bij Authentik is aangemeld, komt meteen binnen. De app moet OpenID Connect kennen."] }), manage && (_jsx("div", { className: "row", children: _jsx("button", { className: "btn btn-primary", onClick: () => setDialog("setup"), children: "Automatische login inrichten" }) }))] }));
    }
    else {
        const l = state.data.login;
        body = (_jsxs("div", { className: "form", children: [l.cleanup_error && (_jsxs("div", { className: "error-box", children: ["Opruimen in Authentik mislukte: ", l.cleanup_error, ". Probeer opnieuw of verwijder het met de hand."] })), _jsxs("dl", { className: "kv", children: [_jsx("dt", { children: "App" }), _jsx("dd", { className: "mono small", children: l.app_url }), _jsx("dt", { children: "Redirect URI" }), _jsx("dd", { className: "mono small", children: l.redirect_uris.join(", ") }), _jsx("dt", { children: "Authentik" }), _jsxs("dd", { children: [_jsxs("span", { children: ["provider '", l.provider_name, "', applicatie ", _jsx("code", { children: l.application_slug })] }), _jsxs("span", { className: "muted small mono", children: ["client ", l.client_id] })] }), _jsx("dt", { children: "Aanmelden mag" }), _jsx("dd", { children: accessText(l) })] }), _jsx("p", { className: "muted small", children: manage
                        ? "De app zelf stelt VaultX niet in: vul de instellingen in de app in (Instellingen tonen) en meld je daarna aan om te testen."
                        : "Een beheerder vult de instellingen in de app in. Daarna meld je je aan via Authentik." }), manage && (_jsxs("div", { className: "row wrap", children: [_jsx("button", { className: "btn", onClick: () => setDialog("config"), children: "Instellingen tonen" }), _jsx("button", { className: "btn btn-danger", onClick: () => setDialog("remove"), children: "Weghalen" })] }))] }));
    }
    return (_jsxs(Card, { title: "Automatische login", children: [body, dialog === "setup" && state.data && (_jsx(SetupDialog, { orgId: orgId, base: base, state: state.data, onClose: () => setDialog(null), onDone: () => {
                    refresh();
                    setDialog("config");
                } })), dialog === "config" && (_jsx(ConfigDialog, { base: base, onClose: () => {
                    setDialog(null);
                    refresh();
                } })), dialog === "remove" && state.data?.login && (_jsx(RemoveDialog, { base: base, login: state.data.login, onClose: () => setDialog(null), onDone: () => {
                    refresh();
                    setDialog(null);
                } }))] }));
}
