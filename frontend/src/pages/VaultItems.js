import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Webkluis: je items bekijken en bewerken in de browser, zoals de webkluis van Vaultwarden.
// Alles wordt in de browser ont- en versleuteld; VaultX ziet enkel versleutelde items.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Badge, Card, Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate } from "../format";
import { AUTO_LOCK_MINUTES } from "../notes/session";
import { encryptText } from "../vault/crypto";
import { GENERATOR_DEFAULTS, generatePassword, parseTotp, totpCode } from "../vault/generate";
import { CIPHER_TYPES, FIELD_TYPES, IDENTITY_FIELDS, decryptAttachment, decryptCipher, decryptFolder, emptyItem, encryptAttachment, encryptCipher, iconDomain, launchUrl, localPath, subtitle, withPasswordHistory, } from "../vault/items";
import { TwoFactorRequired, useWebVault } from "../vault/webSession";
import { getAssertion, webAuthnSupported } from "../webauthn";
export function WebVault({ status }) {
    const { key } = useWebVault();
    if (!key)
        return _jsx(WebUnlock, { status: status });
    return _jsx(VaultBrowser, { userKey: key });
}
// --------------------------------------------------------------------------- ontgrendelen
function WebUnlock({ status }) {
    const { unlock, lockReason } = useWebVault();
    const [password, setPassword] = useState("");
    const [challenge, setChallenge] = useState(null);
    const [useCode, setUseCode] = useState(false);
    const [code, setCode] = useState("");
    const [remember, setRemember] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const keyPossible = !!challenge?.securityKey && webAuthnSupported();
    const askCode = !!challenge && (useCode || !keyPossible);
    const attempt = async (answer) => {
        setBusy(true);
        setError(null);
        try {
            await unlock(status, password, answer);
            setPassword("");
            setCode("");
            setChallenge(null);
        }
        catch (err) {
            if (err instanceof TwoFactorRequired) {
                // Eerste keer: vraag de tweede stap. Daarna: nieuwe challenge, en de fout tonen.
                if (challenge)
                    setError(new Error("Dat werd niet aanvaard. Probeer opnieuw."));
                setChallenge(err);
            }
            else
                setError(err);
        }
        finally {
            setBusy(false);
        }
    };
    const withKey = async () => {
        if (!challenge?.securityKey)
            return;
        setError(null);
        let answer;
        try {
            answer = await getAssertion(challenge.securityKey);
        }
        catch (err) {
            setError(err);
            return;
        }
        await attempt({ securityKey: answer, remember });
    };
    const submit = async (e) => {
        e.preventDefault();
        if (!challenge)
            await attempt();
        else if (askCode)
            await attempt({ code, remember });
        else
            await withKey();
    };
    return (_jsx(Card, { title: "Kluis ontgrendelen", children: _jsxs("form", { className: "form", style: { maxWidth: 460 }, onSubmit: submit, children: [lockReason && _jsx("div", { className: "info-box small", children: lockReason }), _jsxs("p", { className: "muted small", style: { margin: 0 }, children: ["Je browser leidt je sleutel af uit je master password en ontsleutelt de items zelf; VaultX ziet het wachtwoord en de inhoud van je items nooit. Na ", AUTO_LOCK_MINUTES, " minuten zonder activiteit vergrendelt de kluis vanzelf."] }), _jsxs("label", { className: "field", children: [_jsxs("span", { children: ["Master password voor ", status.email] }), _jsx("input", { type: "password", autoComplete: "current-password", autoFocus: true, required: true, value: password, onChange: (e) => setPassword(e.target.value) })] }), challenge && !askCode && (_jsx("div", { className: "info-box small", children: "Bevestig met je beveiligingssleutel of passkey: klik op de knop en raak je YubiKey aan als hij knippert." })), challenge && !challenge.securityKey && status.security_keys.length > 0 && (_jsxs("div", { className: "info-box small", children: ["Je beveiligingssleutel werkt enkel op ", status.security_keys_rp_id ?? "de domeinnaam van VaultX", ". Hier kan je de code uit je authenticator-app of je herstelcode gebruiken."] })), askCode && (_jsxs("label", { className: "field", children: [_jsx("span", { children: challenge.authenticator ? "Code uit je authenticator-app of herstelcode" : "Herstelcode" }), _jsx("input", { autoComplete: "one-time-code", autoFocus: true, required: true, value: code, onChange: (e) => setCode(e.target.value) })] })), challenge && (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: remember, onChange: (e) => setRemember(e.target.checked) }), "Deze browser 30 dagen onthouden"] })), _jsx(ErrorBox, { error: error }), _jsxs("div", { className: "form-actions", children: [_jsx("button", { className: "btn btn-primary", disabled: busy || !password || (askCode && !code), children: busy
                                ? challenge
                                    ? "Controleren…"
                                    : "Sleutel afleiden…"
                                : challenge && !askCode
                                    ? "Beveiligingssleutel gebruiken"
                                    : "Ontgrendelen" }), keyPossible && (_jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setUseCode(!useCode), children: useCode ? "Toch met mijn sleutel" : "Code of herstelcode gebruiken" }))] })] }) }));
}
const SYNC_KEY = ["webvault", "sync"];
function sameFilter(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
}
function VaultBrowser({ userKey }) {
    const { request, lock } = useWebVault();
    const qc = useQueryClient();
    const sync = useQuery({
        queryKey: SYNC_KEY,
        queryFn: async () => {
            const data = await request("GET", "/api/sync?excludeDomains=true");
            const [folders, items] = await Promise.all([
                Promise.all(data.folders.map((f) => decryptFolder(f, userKey))),
                Promise.all(data.ciphers.map((c) => decryptCipher(c, userKey))),
            ]);
            folders.sort((a, b) => a.name.localeCompare(b.name, "nl"));
            items.sort((a, b) => a.name.localeCompare(b.name, "nl", { sensitivity: "base" }));
            return { folders, items };
        },
        staleTime: 30_000,
        refetchOnWindowFocus: true,
    });
    const refresh = () => qc.invalidateQueries({ queryKey: SYNC_KEY });
    const [filter, setFilter] = useState({ kind: "all" });
    const [search, setSearch] = useState("");
    const [selectedId, setSelectedId] = useState(null);
    const [editing, setEditing] = useState(null);
    const [folderDialog, setFolderDialog] = useState(null);
    const items = sync.data?.items ?? [];
    const folders = sync.data?.folders ?? [];
    const visible = useMemo(() => {
        const q = search.trim().toLowerCase();
        return items.filter((i) => {
            if (filter.kind === "trash") {
                if (!i.deletedDate)
                    return false;
            }
            else if (i.deletedDate)
                return false;
            if (filter.kind === "favorites" && !i.favorite)
                return false;
            if (filter.kind === "type" && i.type !== filter.type)
                return false;
            if (filter.kind === "folder" && i.folderId !== filter.id)
                return false;
            if (!q)
                return true;
            return [i.name, i.login.username, subtitle(i), ...i.login.uris.map((u) => u.uri)].some((s) => s.toLowerCase().includes(q));
        });
    }, [items, filter, search]);
    const selected = items.find((i) => i.id === selectedId) ?? null;
    const counts = useMemo(() => {
        const live = items.filter((i) => !i.deletedDate);
        return {
            all: live.length,
            favorites: live.filter((i) => i.favorite).length,
            trash: items.length - live.length,
            type: (t) => live.filter((i) => i.type === t).length,
            folder: (id) => live.filter((i) => i.folderId === id).length,
        };
    }, [items]);
    const newItem = (type) => setEditing(emptyItem(type, filter.kind === "folder" ? filter.id : null));
    if (sync.isLoading)
        return _jsx(Loading, {});
    if (!sync.data)
        return _jsx(ErrorBox, { error: sync.error });
    const nav = (f, label, count) => (_jsxs("button", { className: sameFilter(filter, f) ? "active" : undefined, onClick: () => {
            setFilter(f);
            setSelectedId(null);
        }, children: [_jsx("span", { children: label }), count !== undefined && _jsx("span", { className: "muted", children: count })] }, JSON.stringify(f)));
    return (_jsxs("div", { className: "wv", children: [_jsxs("aside", { className: "wv-side", children: [_jsxs("div", { className: "wv-nav", children: [nav({ kind: "all" }, "Alle items", counts.all), nav({ kind: "favorites" }, "Favorieten", counts.favorites)] }), _jsxs("div", { className: "wv-nav", children: [_jsx("div", { className: "wv-nav-head", children: "Soort" }), Object.keys(CIPHER_TYPES).map(Number).map((t) => nav({ kind: "type", type: t }, CIPHER_TYPES[t], counts.type(t)))] }), _jsxs("div", { className: "wv-nav", children: [_jsxs("div", { className: "wv-nav-head", children: ["Mappen", _jsx("button", { className: "btn-link", onClick: () => setFolderDialog("new"), title: "Nieuwe map", children: "+ map" })] }), folders.map((f) => (_jsxs("div", { className: "wv-folder", children: [nav({ kind: "folder", id: f.id }, f.name, counts.folder(f.id)), _jsx("button", { className: "btn-link", onClick: () => setFolderDialog(f), "aria-label": `Map ${f.name} bewerken`, children: "\u270E" })] }, f.id))), nav({ kind: "folder", id: null }, "Geen map", counts.folder(null))] }), _jsx("div", { className: "wv-nav", children: nav({ kind: "trash" }, "Prullenbak", counts.trash) }), _jsxs("div", { className: "wv-side-foot", children: [_jsx("button", { className: "btn btn-ghost", onClick: () => void refresh(), disabled: sync.isFetching, children: sync.isFetching ? "Synchroniseren…" : "Synchroniseren" }), _jsx("button", { className: "btn btn-ghost", onClick: () => lock(), children: "Vergrendelen" })] })] }), _jsxs("section", { className: "wv-list", children: [_jsxs("div", { className: "wv-list-head", children: [_jsx("input", { type: "search", placeholder: "Zoeken in de kluis", "aria-label": "Zoeken in de kluis", value: search, onChange: (e) => setSearch(e.target.value) }), _jsx(NewItemMenu, { onPick: newItem })] }), _jsx(ErrorBox, { error: sync.error }), !visible.length ? (_jsx(Empty, { children: filter.kind === "trash" ? "De prullenbak is leeg." : search ? "Niets gevonden." : "Nog geen items." })) : (_jsx("ul", { className: "wv-items", children: visible.map((i) => (_jsx("li", { children: _jsxs("button", { className: i.id === selectedId ? "active" : undefined, onClick: () => {
                                    setSelectedId(i.id);
                                    setEditing(null);
                                }, children: [_jsx(ItemIcon, { item: i }), _jsxs("span", { className: "wv-item-text", children: [_jsxs("span", { className: "wv-item-name", children: [i.name, i.favorite && _jsx("span", { className: "wv-star", "aria-label": "favoriet", children: "\u2605" })] }), _jsx("span", { className: "muted small", children: subtitle(i) })] })] }) }, i.id))) }))] }), _jsx("section", { className: "wv-detail", children: editing ? (_jsx(ItemEditor, { initial: editing, folders: folders, userKey: userKey, onCancel: () => setEditing(null), onSaved: (id) => {
                        setEditing(null);
                        setSelectedId(id);
                    } }, editing.id || `new-${editing.type}`)) : selected ? (_jsx(ItemDetail, { item: selected, folders: folders, userKey: userKey, onEdit: () => setEditing(selected), onGone: () => setSelectedId(null) }, selected.id)) : (_jsx("div", { className: "wv-placeholder muted", children: "Kies een item, of maak een nieuw item aan." })) }), _jsx(FolderDialog, { folder: folderDialog, userKey: userKey, onClose: () => setFolderDialog(null), onDeleted: (id) => {
                    if (filter.kind === "folder" && filter.id === id)
                        setFilter({ kind: "all" });
                } })] }));
}
function NewItemMenu({ onPick }) {
    const [open, setOpen] = useState(false);
    const ref = useRef(null);
    useEffect(() => {
        if (!open)
            return;
        const close = (e) => !ref.current?.contains(e.target) && setOpen(false);
        window.addEventListener("pointerdown", close);
        return () => window.removeEventListener("pointerdown", close);
    }, [open]);
    return (_jsxs("div", { className: "wv-menu", ref: ref, children: [_jsx("button", { className: "btn btn-primary", onClick: () => setOpen(!open), "aria-expanded": open, children: "+ Nieuw" }), open && (_jsx("div", { className: "wv-menu-list", role: "menu", children: Object.keys(CIPHER_TYPES).map(Number)
                    .filter((t) => t !== 5)
                    .map((t) => (_jsx("button", { role: "menuitem", onClick: () => {
                        setOpen(false);
                        onPick(t);
                    }, children: CIPHER_TYPES[t] }, t))) }))] }));
}
const TYPE_GLYPH = { 1: "🔑", 2: "📝", 3: "💳", 4: "🪪", 5: "⌘" };
function ItemIcon({ item }) {
    const domain = item.type === 1 ? iconDomain(item) : null;
    const [failed, setFailed] = useState(false);
    if (domain && !failed)
        return (_jsx("img", { className: "wv-icon", src: `/icons/${encodeURIComponent(domain)}/icon.png`, alt: "", loading: "lazy", onError: () => setFailed(true) }));
    return (_jsx("span", { className: "wv-icon wv-icon-glyph", "aria-hidden": true, children: TYPE_GLYPH[item.type] }));
}
// --------------------------------------------------------------------------- bekijken
const CLIPBOARD_CLEAR_MS = 30_000;
function useCopy() {
    const [copied, setCopied] = useState(null);
    useEffect(() => {
        if (!copied)
            return;
        const t = window.setTimeout(() => setCopied(null), 1500);
        return () => window.clearTimeout(t);
    }, [copied]);
    const copy = async (label, value) => {
        await navigator.clipboard.writeText(value);
        setCopied(label);
        // Zoals Bitwarden: het klembord na 30 seconden leegmaken, zodat een gekopieerd wachtwoord
        // of TOTP-code er niet blijft staan na het vergrendelen. Faalt stil zonder focus.
        window.setTimeout(() => void navigator.clipboard.writeText("").catch(() => { }), CLIPBOARD_CLEAR_MS);
    };
    return { copied, copy };
}
function FieldRow({ label, value, secret = false, mono = false, multiline = false, link = null, copy, copied, }) {
    const [shown, setShown] = useState(false);
    if (!value)
        return null;
    const display = secret && !shown ? "••••••••••••" : value;
    return (_jsxs("div", { className: "wv-field", children: [_jsx("div", { className: "wv-field-label muted small", children: label }), _jsxs("div", { className: "wv-field-row", children: [link ? (_jsx("a", { className: `wv-field-value${mono ? " mono" : ""}`, href: link, target: "_blank", rel: "noopener noreferrer", children: display })) : multiline ? (_jsx("pre", { className: `wv-field-value wv-pre${mono ? " mono" : ""}`, children: display })) : (_jsx("span", { className: `wv-field-value${mono || secret ? " mono" : ""}`, children: display })), _jsxs("span", { className: "wv-field-actions", children: [secret && (_jsx("button", { className: "btn btn-ghost", onClick: () => setShown(!shown), children: shown ? "Verbergen" : "Tonen" })), _jsx("button", { className: "btn btn-ghost", onClick: () => void copy(label, value), "aria-label": `${label} kopiëren`, children: copied === label ? "Gekopieerd" : "Kopiëren" })] })] })] }));
}
function Totp({ secret, copy, copied }) {
    const params = useMemo(() => parseTotp(secret), [secret]);
    const [code, setCode] = useState("");
    const [left, setLeft] = useState(0);
    useEffect(() => {
        if (!params)
            return;
        let alive = true;
        const tick = async () => {
            const now = Date.now();
            const c = await totpCode(params, now);
            if (!alive)
                return;
            setCode(c);
            setLeft(params.period - (Math.floor(now / 1000) % params.period));
        };
        void tick();
        const t = window.setInterval(() => void tick(), 1000);
        return () => {
            alive = false;
            window.clearInterval(t);
        };
    }, [params]);
    if (!params)
        return _jsx(FieldRow, { label: "TOTP-sleutel", value: secret, secret: true, mono: true, copy: copy, copied: copied });
    const pretty = code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
    return (_jsxs("div", { className: "wv-field", children: [_jsx("div", { className: "wv-field-label muted small", children: "Verificatiecode (TOTP)" }), _jsxs("div", { className: "wv-field-row", children: [_jsx("span", { className: "wv-field-value mono wv-totp", children: pretty }), _jsxs("span", { className: "muted small", children: [left, "s"] }), _jsx("span", { className: "wv-field-actions", children: _jsx("button", { className: "btn btn-ghost", onClick: () => void copy("Verificatiecode", code), "aria-label": "Verificatiecode kopi\u00EBren", children: copied === "Verificatiecode" ? "Gekopieerd" : "Kopiëren" }) })] })] }));
}
function ItemDetail({ item, folders, userKey, onEdit, onGone, }) {
    const { request, verifyMasterPassword } = useWebVault();
    const qc = useQueryClient();
    const { copied, copy } = useCopy();
    const [unlocked, setUnlocked] = useState(!item.reprompt);
    const refresh = () => qc.invalidateQueries({ queryKey: SYNC_KEY });
    const act = useMutation({
        mutationFn: async (action) => {
            if (action === "trash")
                await request("PUT", `/api/ciphers/${item.id}/delete`);
            if (action === "restore")
                await request("PUT", `/api/ciphers/${item.id}/restore`);
            if (action === "delete")
                await request("DELETE", `/api/ciphers/${item.id}`);
            if (action === "favorite")
                await request("PUT", `/api/ciphers/${item.id}/partial`, { folderId: item.folderId, favorite: !item.favorite });
            return action;
        },
        onSuccess: async (action) => {
            await refresh();
            if (action !== "favorite")
                onGone();
        },
    });
    const folder = folders.find((f) => f.id === item.folderId);
    const row = (label, value, opts = {}) => (_jsx(FieldRow, { label: label, value: value, copy: copy, copied: copied, ...opts }, label));
    return (_jsxs("div", { className: "wv-view", children: [_jsxs("div", { className: "wv-view-head", children: [_jsx(ItemIcon, { item: item }), _jsxs("div", { children: [_jsx("h2", { children: item.name }), _jsxs("div", { className: "muted small", children: [CIPHER_TYPES[item.type], folder && _jsxs(_Fragment, { children: [" \u00B7 ", folder.name] }), item.deletedDate && _jsx(Badge, { tone: "warn", children: "in prullenbak" }), item.broken && _jsx(Badge, { tone: "bad", children: "deels niet te ontsleutelen" })] })] }), _jsx("div", { className: "wv-view-actions", children: item.deletedDate ? (_jsxs(_Fragment, { children: [_jsx("button", { className: "btn", disabled: act.isPending, onClick: () => act.mutate("restore"), children: "Terugzetten" }), _jsx("button", { className: "btn btn-danger", disabled: act.isPending, onClick: () => confirm(`"${item.name}" definitief verwijderen?`) && act.mutate("delete"), children: "Definitief verwijderen" })] })) : (_jsxs(_Fragment, { children: [_jsx("button", { className: "btn btn-ghost", disabled: act.isPending, onClick: () => act.mutate("favorite"), "aria-label": item.favorite ? "Geen favoriet meer" : "Favoriet maken", title: item.favorite ? "Geen favoriet meer" : "Favoriet maken", children: item.favorite ? "★" : "☆" }), _jsx("button", { className: "btn", onClick: onEdit, disabled: !unlocked || item.broken, children: "Bewerken" }), _jsx("button", { className: "btn btn-ghost btn-danger-text", disabled: act.isPending, onClick: () => act.mutate("trash"), children: "Naar prullenbak" })] })) })] }), _jsx(ErrorBox, { error: act.error }), !unlocked ? (_jsx(Reprompt, { verify: verifyMasterPassword, onOk: () => setUnlocked(true) })) : (_jsxs(_Fragment, { children: [item.type === 1 && (_jsxs("div", { className: "wv-group", children: [row("Gebruikersnaam", item.login.username), row("Wachtwoord", item.login.password, { secret: true }), item.login.totp && _jsx(Totp, { secret: item.login.totp, copy: copy, copied: copied })] })), item.type === 1 && item.login.uris.some((u) => u.uri) && (_jsx("div", { className: "wv-group", children: item.login.uris
                            .filter((u) => u.uri)
                            .map((u, n) => (_jsx(FieldRow, { label: n === 0 ? "Website" : `Website ${n + 1}`, value: u.uri, link: launchUrl(u.uri), copy: copy, copied: copied }, n))) })), item.type === 3 && (_jsxs("div", { className: "wv-group", children: [row("Kaarthouder", item.card.cardholderName), row("Merk", item.card.brand), row("Kaartnummer", item.card.number, { secret: true }), row("Vervaldatum", [item.card.expMonth.padStart(item.card.expMonth ? 2 : 0, "0"), item.card.expYear].filter(Boolean).join(" / ")), row("Beveiligingscode", item.card.code, { secret: true })] })), item.type === 4 && (_jsx("div", { className: "wv-group", children: IDENTITY_FIELDS.map(([k, label]) => row(label, item.identity[k])) })), item.type === 5 && (_jsxs("div", { className: "wv-group", children: [row("Vingerafdruk", item.sshKey.keyFingerprint, { mono: true }), row("Publieke sleutel", item.sshKey.publicKey, { mono: true, multiline: true }), row("Private sleutel", item.sshKey.privateKey, { secret: true, multiline: true })] })), item.notes && _jsx("div", { className: "wv-group", children: row("Notities", item.notes, { multiline: true }) }), item.fields.length > 0 && (_jsxs("div", { className: "wv-group", children: [_jsx("div", { className: "wv-group-head", children: "Eigen velden" }), item.fields.map((f, n) => (_jsx(FieldRow, { label: f.name || `Veld ${n + 1}`, value: f.type === 2 ? (f.value === "true" ? "Ja" : "Nee") : f.value, secret: f.type === 1, copy: copy, copied: copied }, n)))] })), _jsx(Attachments, { item: item, userKey: userKey }), item.passwordHistory.length > 0 && _jsx(PasswordHistory, { item: item, copy: copy, copied: copied })] })), _jsxs("div", { className: "muted small wv-dates", children: ["Laatst gewijzigd ", formatDate(item.revisionDate), " \u00B7 aangemaakt ", formatDate(item.creationDate), item.login.passwordRevisionDate && _jsxs(_Fragment, { children: [" \u00B7 wachtwoord gewijzigd ", formatDate(item.login.passwordRevisionDate)] })] })] }));
}
function Reprompt({ verify, onOk }) {
    const [pw, setPw] = useState("");
    const [busy, setBusy] = useState(false);
    const [wrong, setWrong] = useState(false);
    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        const ok = await verify(pw).finally(() => setBusy(false));
        if (ok)
            onOk();
        else
            setWrong(true);
    };
    return (_jsxs("form", { className: "form wv-group", onSubmit: submit, style: { maxWidth: 420 }, children: [_jsx("p", { className: "muted small", style: { margin: 0 }, children: "Dit item vraagt je master password opnieuw voor je het kan bekijken." }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Master password" }), _jsx("input", { type: "password", autoComplete: "current-password", autoFocus: true, value: pw, onChange: (e) => setPw(e.target.value) })] }), wrong && _jsx("div", { className: "error-box", children: "Master password is onjuist" }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: busy || !pw, children: busy ? "Controleren…" : "Tonen" }) })] }));
}
function PasswordHistory({ item, copy, copied }) {
    const [open, setOpen] = useState(false);
    return (_jsxs("div", { className: "wv-group", children: [_jsxs("button", { className: "btn-link", onClick: () => setOpen(!open), children: ["Wachtwoordgeschiedenis (", item.passwordHistory.length, ")"] }), open &&
                item.passwordHistory.map((h, n) => (_jsx(FieldRow, { label: `Gebruikt tot ${formatDate(h.lastUsedDate)}`, value: h.password, secret: true, copy: copy, copied: copied }, n)))] }));
}
function Attachments({ item, userKey }) {
    const { fetchRaw, request } = useWebVault();
    const qc = useQueryClient();
    const input = useRef(null);
    const download = useMutation({
        mutationFn: async (att) => {
            const res = await fetchRaw(localPath(att.url));
            if (!res.ok)
                throw new Error("Bijlage downloaden mislukt");
            const plain = await decryptAttachment(item, att, userKey, new Uint8Array(await res.arrayBuffer()));
            const url = URL.createObjectURL(new Blob([plain]));
            const a = document.createElement("a");
            a.href = url;
            a.download = att.fileName;
            a.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
        },
    });
    const upload = useMutation({
        mutationFn: async (file) => {
            const enc = await encryptAttachment(item, userKey, file.name, new Uint8Array(await file.arrayBuffer()));
            const res = await request("POST", `/api/ciphers/${item.id}/attachment/v2`, {
                fileName: enc.fileName,
                key: enc.key,
                fileSize: enc.data.length,
            });
            const form = new FormData();
            form.append("data", new Blob([enc.data]), enc.fileName);
            const up = await fetchRaw(localPath(res.url), { method: "POST", body: form });
            if (!up.ok) {
                const data = await up.json().catch(() => ({}));
                throw new Error(data.message ?? "Uploaden mislukt");
            }
        },
        onSettled: () => qc.invalidateQueries({ queryKey: SYNC_KEY }),
    });
    const remove = useMutation({
        mutationFn: (att) => request("DELETE", `/api/ciphers/${item.id}/attachment/${att.id}`),
        onSuccess: () => qc.invalidateQueries({ queryKey: SYNC_KEY }),
    });
    if (item.deletedDate && !item.attachments.length)
        return null;
    return (_jsxs("div", { className: "wv-group", children: [_jsxs("div", { className: "wv-group-head", children: ["Bijlagen", !item.deletedDate && (_jsxs(_Fragment, { children: [_jsx("button", { className: "btn-link", onClick: () => input.current?.click(), disabled: upload.isPending, children: upload.isPending ? "Versleutelen en uploaden…" : "+ bijlage" }), _jsx("input", { ref: input, type: "file", hidden: true, "aria-label": "Bijlage toevoegen", onChange: (e) => {
                                    const f = e.target.files?.[0];
                                    if (f)
                                        upload.mutate(f);
                                    e.target.value = "";
                                } })] }))] }), !item.attachments.length && _jsx("div", { className: "muted small", children: "Geen bijlagen." }), item.attachments.map((a) => (_jsxs("div", { className: "wv-field-row", children: [_jsx("span", { className: "wv-field-value", children: a.fileName }), _jsx("span", { className: "muted small", children: a.sizeName }), _jsxs("span", { className: "wv-field-actions", children: [_jsx("button", { className: "btn btn-ghost", onClick: () => download.mutate(a), disabled: download.isPending, children: "Downloaden" }), !item.deletedDate && (_jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => confirm(`Bijlage "${a.fileName}" verwijderen?`) && remove.mutate(a), disabled: remove.isPending, children: "Verwijderen" }))] })] }, a.id))), _jsx(ErrorBox, { error: download.error ?? upload.error ?? remove.error })] }));
}
// --------------------------------------------------------------------------- bewerken
function ItemEditor({ initial, folders, userKey, onCancel, onSaved, }) {
    const { request } = useWebVault();
    const qc = useQueryClient();
    const [v, setV] = useState(() => structuredClone({ ...initial, raw: null }));
    const isNew = !initial.id;
    const set = (patch) => setV((cur) => ({ ...cur, ...patch }));
    const setLogin = (patch) => setV((cur) => ({ ...cur, login: { ...cur.login, ...patch } }));
    const setCard = (patch) => setV((cur) => ({ ...cur, card: { ...cur.card, ...patch } }));
    const save = useMutation({
        mutationFn: async () => {
            const full = withPasswordHistory({ ...v, raw: initial.raw }, isNew ? null : initial);
            const body = await encryptCipher(full, userKey);
            const res = isNew
                ? await request("POST", "/api/ciphers", body)
                : await request("PUT", `/api/ciphers/${initial.id}`, body);
            await qc.invalidateQueries({ queryKey: SYNC_KEY });
            return res.id;
        },
        onSuccess: onSaved,
    });
    const submit = (e) => {
        e.preventDefault();
        save.mutate();
    };
    return (_jsxs("form", { className: "form wv-editor", onSubmit: submit, children: [_jsx("div", { className: "wv-view-head", children: _jsx("h2", { children: isNew ? `Nieuw item: ${CIPHER_TYPES[v.type].toLowerCase()}` : `${v.name || "Item"} bewerken` }) }), _jsxs("div", { className: "grid-form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { required: true, autoFocus: isNew, value: v.name, onChange: (e) => set({ name: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Map" }), _jsxs("select", { "aria-label": "Map", value: v.folderId ?? "", onChange: (e) => set({ folderId: e.target.value || null }), children: [_jsx("option", { value: "", children: "Geen map" }), folders.map((f) => (_jsx("option", { value: f.id, children: f.name }, f.id)))] })] })] }), v.type === 1 && (_jsxs(_Fragment, { children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Gebruikersnaam" }), _jsx("input", { autoComplete: "off", value: v.login.username, onChange: (e) => setLogin({ username: e.target.value }) })] }), _jsx(PasswordInput, { value: v.login.password, onChange: (password) => setLogin({ password }) }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "TOTP-sleutel (base32 of otpauth://)" }), _jsx("input", { autoComplete: "off", className: "mono", value: v.login.totp, onChange: (e) => setLogin({ totp: e.target.value }) })] }), _jsxs("div", { className: "field", children: [_jsx("span", { children: "Websites" }), v.login.uris.map((u, n) => (_jsxs("div", { className: "row", children: [_jsx("input", { "aria-label": `Website ${n + 1}`, placeholder: "https://", value: u.uri, onChange: (e) => setLogin({ uris: v.login.uris.map((x, i) => (i === n ? { ...x, uri: e.target.value } : x)) }) }), _jsx("button", { type: "button", className: "btn btn-ghost", "aria-label": `Website ${n + 1} weghalen`, onClick: () => setLogin({ uris: v.login.uris.filter((_, i) => i !== n) }), children: "\u00D7" })] }, n))), _jsx("div", { children: _jsx("button", { type: "button", className: "btn-link", onClick: () => setLogin({ uris: [...v.login.uris, { uri: "", match: null }] }), children: "+ website" }) })] })] })), v.type === 3 && (_jsxs("div", { className: "grid-form", children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Kaarthouder" }), _jsx("input", { value: v.card.cardholderName, onChange: (e) => setCard({ cardholderName: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Merk" }), _jsx("select", { "aria-label": "Merk", value: v.card.brand, onChange: (e) => setCard({ brand: e.target.value }), children: [...new Set(["", "Visa", "Mastercard", "Amex", "Maestro", "Bancontact", "Discover", "Other", v.card.brand])].map((b) => (_jsx("option", { value: b, children: b === "" ? "—" : b === "Other" ? "Andere" : b }, b))) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Kaartnummer" }), _jsx("input", { autoComplete: "off", className: "mono", value: v.card.number, onChange: (e) => setCard({ number: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Beveiligingscode" }), _jsx("input", { autoComplete: "off", className: "mono", value: v.card.code, onChange: (e) => setCard({ code: e.target.value }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Vervalmaand" }), _jsxs("select", { "aria-label": "Vervalmaand", value: v.card.expMonth, onChange: (e) => setCard({ expMonth: e.target.value }), children: [_jsx("option", { value: "", children: "\u2014" }), Array.from({ length: 12 }, (_, i) => String(i + 1)).map((m) => (_jsx("option", { value: m, children: m.padStart(2, "0") }, m)))] })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Vervaljaar" }), _jsx("input", { inputMode: "numeric", value: v.card.expYear, onChange: (e) => setCard({ expYear: e.target.value }) })] })] })), v.type === 4 && (_jsx("div", { className: "grid-form", children: IDENTITY_FIELDS.map(([k, label]) => (_jsxs("label", { className: "field", children: [_jsx("span", { children: label }), _jsx("input", { value: v.identity[k], onChange: (e) => set({ identity: { ...v.identity, [k]: e.target.value } }) })] }, k))) })), v.type === 5 && (_jsxs(_Fragment, { children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Private sleutel" }), _jsx("textarea", { rows: 5, className: "mono", value: v.sshKey.privateKey, onChange: (e) => set({ sshKey: { ...v.sshKey, privateKey: e.target.value } }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Publieke sleutel" }), _jsx("textarea", { rows: 2, className: "mono", value: v.sshKey.publicKey, onChange: (e) => set({ sshKey: { ...v.sshKey, publicKey: e.target.value } }) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Vingerafdruk" }), _jsx("input", { className: "mono", value: v.sshKey.keyFingerprint, onChange: (e) => set({ sshKey: { ...v.sshKey, keyFingerprint: e.target.value } }) })] })] })), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Notities" }), _jsx("textarea", { rows: v.type === 2 ? 10 : 4, value: v.notes, onChange: (e) => set({ notes: e.target.value }) })] }), _jsxs("div", { className: "field", children: [_jsx("span", { children: "Eigen velden" }), v.fields.map((f, n) => {
                        const update = (patch) => set({ fields: v.fields.map((x, i) => (i === n ? { ...x, ...patch } : x)) });
                        return (_jsxs("div", { className: "row", children: [_jsxs("select", { "aria-label": `Soort veld ${n + 1}`, value: f.type, onChange: (e) => update({ type: Number(e.target.value), value: "" }), children: [Object.entries(FIELD_TYPES).map(([t, label]) => (_jsx("option", { value: t, children: label }, t))), f.type === 3 && _jsx("option", { value: 3, children: "Gekoppeld" })] }), _jsx("input", { "aria-label": `Naam veld ${n + 1}`, placeholder: "Naam", value: f.name, onChange: (e) => update({ name: e.target.value }) }), f.type === 2 ? (_jsx("input", { type: "checkbox", "aria-label": `Waarde veld ${n + 1}`, checked: f.value === "true", onChange: (e) => update({ value: e.target.checked ? "true" : "false" }) })) : (_jsx("input", { "aria-label": `Waarde veld ${n + 1}`, placeholder: "Waarde", type: f.type === 1 ? "password" : "text", autoComplete: "off", value: f.value, disabled: f.type === 3, onChange: (e) => update({ value: e.target.value }) })), _jsx("button", { type: "button", className: "btn btn-ghost", "aria-label": `Veld ${n + 1} weghalen`, onClick: () => set({ fields: v.fields.filter((_, i) => i !== n) }), children: "\u00D7" })] }, n));
                    }), _jsx("div", { children: _jsx("button", { type: "button", className: "btn-link", onClick: () => set({ fields: [...v.fields, { name: "", value: "", type: 0, linkedId: null }] }), children: "+ veld" }) })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: v.favorite, onChange: (e) => set({ favorite: e.target.checked }) }), "Favoriet"] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: v.reprompt, onChange: (e) => set({ reprompt: e.target.checked }) }), "Master password opnieuw vragen om dit item te bekijken"] }), _jsx(ErrorBox, { error: save.error }), _jsxs("div", { className: "form-actions row", children: [_jsx("button", { type: "button", className: "btn", onClick: onCancel, children: "Annuleren" }), _jsx("button", { className: "btn btn-primary", disabled: save.isPending || !v.name.trim(), children: save.isPending ? "Versleutelen en opslaan…" : "Opslaan" })] })] }));
}
function PasswordInput({ value, onChange }) {
    const [shown, setShown] = useState(false);
    const [opts, setOpts] = useState(GENERATOR_DEFAULTS);
    const [showOpts, setShowOpts] = useState(false);
    const generate = (o = opts) => {
        onChange(generatePassword(o));
        setShown(true);
    };
    const setOpt = (patch) => {
        const next = { ...opts, ...patch };
        setOpts(next);
        generate(next);
    };
    return (_jsxs("div", { className: "field", children: [_jsx("span", { children: "Wachtwoord" }), _jsxs("div", { className: "row", children: [_jsx("input", { "aria-label": "Wachtwoord", type: shown ? "text" : "password", autoComplete: "new-password", className: "mono", value: value, onChange: (e) => onChange(e.target.value) }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setShown(!shown), children: shown ? "Verbergen" : "Tonen" }), _jsx("button", { type: "button", className: "btn", onClick: () => generate(), children: "Genereren" }), _jsx("button", { type: "button", className: "btn btn-ghost", onClick: () => setShowOpts(!showOpts), "aria-expanded": showOpts, children: "\u2699" })] }), showOpts && (_jsxs("div", { className: "row wrap wv-gen small", children: [_jsxs("label", { className: "row", children: ["Lengte", _jsx("input", { type: "number", min: 8, max: 128, value: opts.length, onChange: (e) => setOpt({ length: Number(e.target.value) }), style: { width: 72 } })] }), [
                        ["upper", "A-Z"],
                        ["lower", "a-z"],
                        ["digits", "0-9"],
                        ["symbols", "!@#$"],
                    ].map(([k, label]) => (_jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: opts[k], onChange: (e) => setOpt({ [k]: e.target.checked }) }), label] }, k)))] }))] }));
}
// --------------------------------------------------------------------------- mappen
function FolderDialog({ folder, userKey, onClose, onDeleted, }) {
    const { request } = useWebVault();
    const qc = useQueryClient();
    const [name, setName] = useState("");
    const existing = folder && folder !== "new" ? folder : null;
    useEffect(() => setName(existing?.name ?? ""), [folder, existing?.name]);
    const done = async () => {
        await qc.invalidateQueries({ queryKey: SYNC_KEY });
        onClose();
    };
    const save = useMutation({
        mutationFn: async () => {
            const body = { name: await encryptText(name.trim(), userKey) };
            if (existing)
                await request("PUT", `/api/folders/${existing.id}`, body);
            else
                await request("POST", "/api/folders", body);
        },
        onSuccess: done,
    });
    const remove = useMutation({
        mutationFn: async () => {
            if (!existing)
                return;
            await request("DELETE", `/api/folders/${existing.id}`);
            onDeleted(existing.id);
        },
        onSuccess: done,
    });
    return (_jsx(Modal, { title: existing ? "Map bewerken" : "Nieuwe map", open: folder !== null, onClose: onClose, children: _jsxs("form", { className: "form", onSubmit: (e) => {
                e.preventDefault();
                save.mutate();
            }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { autoFocus: true, required: true, value: name, onChange: (e) => setName(e.target.value) })] }), _jsx(ErrorBox, { error: save.error ?? remove.error }), _jsxs("div", { className: "form-actions row", children: [existing && (_jsx("button", { type: "button", className: "btn btn-ghost btn-danger-text", disabled: remove.isPending, onClick: () => confirm(`Map "${existing.name}" verwijderen? De items blijven bestaan, zonder map.`) && remove.mutate(), children: "Verwijderen" })), _jsx("button", { className: "btn btn-primary", disabled: save.isPending || !name.trim(), children: "Opslaan" })] })] }) }));
}
