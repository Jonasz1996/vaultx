import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import DOMPurify from "dompurify";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { Badge, Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate } from "../format";
const LIMIT = 50;
const COLORS = ["#0ea5e9", "#22c55e", "#eab308", "#f97316", "#ef4444", "#a855f7", "#64748b"];
const archiveLabels = {
    screenshot: "Screenshot",
    pdf: "PDF",
    readable: "Leesbaar",
    page: "Pagina",
    preview: "Voorbeeld",
};
/** Collecties als boom, in volgorde om te tonen (ouder, dan kinderen op naam). */
function flatten(cols) {
    const ids = new Set(cols.map((c) => c.id));
    const children = new Map();
    for (const c of cols) {
        const parent = c.parent_id && ids.has(c.parent_id) ? c.parent_id : null;
        children.set(parent, [...(children.get(parent) ?? []), c]);
    }
    const out = [];
    const walk = (parent, depth) => {
        for (const c of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
            out.push({ col: c, depth });
            walk(c.id, depth + 1);
        }
    };
    walk(null, 0);
    return out;
}
function descendants(cols, id) {
    const out = new Set([id]);
    let grew = true;
    while (grew) {
        grew = false;
        for (const c of cols)
            if (c.parent_id && out.has(c.parent_id) && !out.has(c.id)) {
                out.add(c.id);
                grew = true;
            }
    }
    return out;
}
function hostOf(url) {
    try {
        return new URL(url).hostname;
    }
    catch {
        return url;
    }
}
function archiveUrl(link, kind, download = false) {
    return `/api/v1/links/${link.id}/archives/${kind}${download ? "?download=true" : ""}`;
}
export function LinksPage() {
    const qc = useQueryClient();
    const [params, setParams] = useSearchParams();
    const view = params.get("c") ?? ""; // "" = alles, "unsorted", "pinned" of een collectie-id
    const tag = params.get("tag") ?? "";
    const [q, setQ] = useState("");
    const [sort, setSort] = useState("newest");
    const [editing, setEditing] = useState(null);
    const [editingCol, setEditingCol] = useState(null);
    const [importing, setImporting] = useState(false);
    const [managingTags, setManagingTags] = useState(false);
    const [reading, setReading] = useState(null);
    const [shot, setShot] = useState(null);
    const collections = useQuery({
        queryKey: ["links", "collections"],
        queryFn: () => api.get("/api/v1/links/collections"),
    });
    const tags = useQuery({ queryKey: ["links", "tags"], queryFn: () => api.get("/api/v1/links/tags") });
    const filters = {
        q,
        sort,
        tag,
        collection_id: view && view !== "unsorted" && view !== "pinned" ? view : undefined,
        unsorted: view === "unsorted" ? true : undefined,
        pinned: view === "pinned" ? true : undefined,
    };
    const feed = useInfiniteQuery({
        queryKey: ["links", "list", filters],
        initialPageParam: 0,
        queryFn: ({ pageParam }) => api.get("/api/v1/links", { ...filters, limit: LIMIT, offset: pageParam }),
        getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + LIMIT : undefined),
    });
    const refresh = () => void qc.invalidateQueries({ queryKey: ["links"] });
    const update = useMutation({
        mutationFn: ({ id, body }) => api.patch(`/api/v1/links/${id}`, body),
        onSuccess: refresh,
    });
    const remove = useMutation({
        mutationFn: (id) => api.delete(`/api/v1/links/${id}`),
        onSuccess: refresh,
    });
    const cols = collections.data ?? [];
    const tree = useMemo(() => flatten(cols), [cols]);
    const colById = new Map(cols.map((c) => [c.id, c]));
    const items = feed.data?.pages.flatMap((p) => p.items) ?? [];
    const total = feed.data?.pages[0]?.total ?? 0;
    const current = colById.get(view);
    const go = (c, t = "") => {
        const next = new URLSearchParams();
        if (c)
            next.set("c", c);
        if (t)
            next.set("tag", t);
        setParams(next, { replace: true });
    };
    const title = current
        ? current.name
        : view === "unsorted"
            ? "Zonder collectie"
            : view === "pinned"
                ? "Vastgepind"
                : "Alle links";
    const path = (c) => {
        const parts = [];
        let node = c;
        while (node && parts.length < 10) {
            parts.unshift(node.name);
            node = node.parent_id ? colById.get(node.parent_id) : undefined;
        }
        return parts.join(" / ");
    };
    return (_jsxs("div", { className: "links-app", children: [_jsxs("aside", { className: "notes-side", children: [_jsx("input", { className: "search notes-search", placeholder: "Zoeken in links", value: q, onChange: (e) => setQ(e.target.value) }), _jsxs("div", { className: "notes-side-actions", children: [_jsx("button", { className: "btn btn-primary", onClick: () => setEditing("new"), children: "Link toevoegen" }), _jsx("button", { className: "btn", onClick: () => setEditingCol("new"), children: "Collectie" })] }), _jsxs("ul", { className: "tree", children: [_jsx("li", { children: _jsx("button", { className: `tree-item${view === "" && !tag ? " active" : ""}`, onClick: () => go(""), children: _jsx("span", { children: "Alle links" }) }) }), _jsx("li", { children: _jsx("button", { className: `tree-item${view === "pinned" ? " active" : ""}`, onClick: () => go("pinned"), children: _jsx("span", { children: "\u2605 Vastgepind" }) }) }), _jsx("li", { children: _jsx("button", { className: `tree-item${view === "unsorted" ? " active" : ""}`, onClick: () => go("unsorted"), children: _jsx("span", { children: "Zonder collectie" }) }) })] }), _jsx("div", { className: "side-label", children: "Collecties" }), collections.isLoading ? (_jsx(Loading, {})) : tree.length === 0 ? (_jsx("p", { className: "muted small", children: "Nog geen collecties." })) : (_jsx("ul", { className: "tree", children: tree.map(({ col, depth }) => (_jsx("li", { children: _jsxs("button", { className: `tree-item link-col depth-${Math.min(depth, 6)}${view === col.id ? " active" : ""}`, onClick: () => go(col.id), title: col.description || col.name, children: [_jsxs("span", { className: "truncate", children: [_jsx("svg", { className: "link-dot", viewBox: "0 0 10 10", "aria-hidden": "true", children: _jsx("circle", { cx: "5", cy: "5", r: "5", fill: col.color }) }), col.name] }), _jsx("span", { className: "count", children: col.link_count || "" })] }) }, col.id))) })), _jsxs("div", { className: "side-label row", children: [_jsx("span", { children: "Tags" }), !!tags.data?.length && (_jsx("button", { className: "btn-link small", onClick: () => setManagingTags(true), children: "beheren" }))] }), _jsxs("div", { className: "row wrap", children: [tags.data?.map((t) => (_jsxs("button", { className: `chip${tag === t.name ? " active" : ""}`, onClick: () => go(view, tag === t.name ? "" : t.name), children: ["#", t.name, " ", _jsx("span", { className: "muted", children: t.link_count })] }, t.id))), tags.data?.length === 0 && _jsx("span", { className: "muted small", children: "Nog geen tags." })] }), _jsxs("div", { className: "notes-side-foot", children: [_jsx("button", { className: "btn btn-ghost", onClick: () => setImporting(true), children: "Importeren" }), _jsx("a", { className: "btn btn-ghost", href: "/api/v1/links/export", download: true, children: "Exporteren" })] })] }), _jsxs("section", { className: "links-main", children: [_jsxs("header", { className: "page-header", children: [_jsxs("div", { children: [_jsx("h1", { children: title }), _jsxs("p", { className: "muted", children: [current ? (_jsxs(_Fragment, { children: [path(current) !== current.name && _jsxs("span", { children: [path(current), " \u00B7 "] }), current.description] })) : view === "" && !q && !tag ? (`${total} links in ${cols.length} collecties.`) : null, tag && (_jsxs(_Fragment, { children: [" ", "Met tag ", _jsxs(Badge, { tone: "info", children: ["#", tag] }), " ", _jsx("button", { className: "btn-link small", onClick: () => go(view), children: "wissen" })] }))] })] }), _jsxs("div", { className: "actions", children: [current && (_jsx("button", { className: "btn", onClick: () => setEditingCol(current), children: "Collectie bewerken" })), _jsxs("select", { value: sort, onChange: (e) => setSort(e.target.value), "aria-label": "Sorteren", children: [_jsx("option", { value: "newest", children: "Nieuwste eerst" }), _jsx("option", { value: "oldest", children: "Oudste eerst" }), _jsx("option", { value: "name", children: "Naam A-Z" }), _jsx("option", { value: "name_desc", children: "Naam Z-A" })] })] })] }), _jsx(ErrorBox, { error: feed.error ?? update.error ?? remove.error }), feed.isLoading ? (_jsx(Loading, {})) : items.length === 0 ? (_jsx(Empty, { children: q || tag || view
                            ? "Geen links gevonden."
                            : "Nog geen links. Voeg er een toe of importeer je Linkwarden-export of browserbladwijzers." })) : (_jsxs(_Fragment, { children: [_jsxs("p", { className: "muted small", children: [total, " links"] }), _jsx("ul", { className: "link-list", children: items.map((l) => {
                                    const col = l.collection_id ? colById.get(l.collection_id) : undefined;
                                    const kinds = l.archives.map((a) => a.kind).filter((k) => k !== "preview");
                                    return (_jsxs("li", { className: "link-row", children: [_jsx("img", { className: "link-icon", src: `/icons/${hostOf(l.url)}/icon.png`, alt: "", loading: "lazy", onError: (e) => (e.currentTarget.style.visibility = "hidden") }), _jsxs("div", { className: "link-body", children: [_jsx("a", { className: "link-title", href: l.url, target: "_blank", rel: "noreferrer noopener", children: l.name || l.url }), _jsxs("div", { className: "muted small truncate-line", children: [hostOf(l.url), col && view !== col.id && (_jsxs(_Fragment, { children: [" · ", _jsx("button", { className: "btn-link small", onClick: () => go(col.id), children: col.name })] })), " · ", formatDate(l.created_at)] }), l.description && _jsx("div", { className: "link-desc", children: l.description }), _jsxs("div", { className: "row wrap", children: [l.tags.map((t) => (_jsxs("button", { className: "chip", onClick: () => go(view, t), children: ["#", t] }, t))), kinds.map((k) => k === "readable" ? (_jsx("button", { className: "chip chip-archive", onClick: () => setReading(l), children: archiveLabels[k] }, k)) : k === "screenshot" ? (_jsx("button", { className: "chip chip-archive", onClick: () => setShot(l), children: archiveLabels[k] }, k)) : (_jsx("a", { className: "chip chip-archive", href: archiveUrl(l, k), target: "_blank", rel: "noreferrer noopener", children: archiveLabels[k] }, k)))] })] }), _jsxs("div", { className: "link-actions", children: [_jsx("button", { className: `btn-icon${l.pinned ? " pinned" : ""}`, title: l.pinned ? "Losmaken" : "Vastpinnen", "aria-label": l.pinned ? "Losmaken" : "Vastpinnen", onClick: () => update.mutate({ id: l.id, body: { pinned: !l.pinned } }), children: l.pinned ? "★" : "☆" }), _jsx("button", { className: "btn btn-ghost", onClick: () => setEditing(l), children: "Bewerken" }), _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => {
                                                            if (confirm(`"${l.name || l.url}" verwijderen?`))
                                                                remove.mutate(l.id);
                                                        }, children: "Verwijderen" })] })] }, l.id));
                                }) }), feed.hasNextPage && (_jsx("div", { className: "pager", children: _jsx("button", { className: "btn", onClick: () => void feed.fetchNextPage(), disabled: feed.isFetchingNextPage, children: "Meer laden" }) }))] }))] }), _jsx(Modal, { title: editing === "new" ? "Link toevoegen" : "Link bewerken", open: editing !== null, onClose: () => setEditing(null), children: editing !== null && (_jsx(LinkForm, { link: editing === "new" ? null : editing, tree: tree, defaultCollection: current?.id ?? null, onDone: () => {
                        setEditing(null);
                        refresh();
                    } })) }), _jsx(Modal, { title: editingCol === "new" ? "Nieuwe collectie" : "Collectie bewerken", open: editingCol !== null, onClose: () => setEditingCol(null), children: editingCol !== null && (_jsx(CollectionForm, { col: editingCol === "new" ? null : editingCol, cols: cols, tree: tree, defaultParent: editingCol === "new" ? (current?.id ?? null) : null, onDone: (deleted) => {
                        setEditingCol(null);
                        if (deleted)
                            go("");
                        refresh();
                    } })) }), _jsx(Modal, { title: "Links importeren", open: importing, onClose: () => setImporting(false), wide: true, children: _jsx(ImportForm, { onImported: refresh }) }), _jsx(Modal, { title: "Tags beheren", open: managingTags, onClose: () => setManagingTags(false), children: _jsx(TagManager, { tags: tags.data ?? [], onChanged: refresh }) }), _jsx(Modal, { title: reading?.name || "Leesbare versie", open: reading !== null, onClose: () => setReading(null), wide: true, children: reading && _jsx(Readable, { link: reading }) }), _jsx(Modal, { title: shot?.name || "Screenshot", open: shot !== null, onClose: () => setShot(null), wide: true, children: shot && (_jsxs("div", { className: "stack", children: [_jsx("img", { className: "link-shot", src: archiveUrl(shot, "screenshot"), alt: `Screenshot van ${shot.url}` }), _jsx("a", { className: "btn", href: archiveUrl(shot, "screenshot", true), children: "Downloaden" })] })) })] }));
}
function CollectionSelect({ tree, value, onChange, exclude, emptyLabel, }) {
    return (_jsxs("select", { value: value, onChange: (e) => onChange(e.target.value), children: [_jsx("option", { value: "", children: emptyLabel }), tree
                .filter(({ col }) => !exclude?.has(col.id))
                .map(({ col, depth }) => (_jsx("option", { value: col.id, children: "  ".repeat(depth) + col.name }, col.id)))] }));
}
function LinkForm({ link, tree, defaultCollection, onDone, }) {
    const [url, setUrl] = useState(link?.url ?? "");
    const [name, setName] = useState(link?.name ?? "");
    const [description, setDescription] = useState(link?.description ?? "");
    const [collection, setCollection] = useState(link ? (link.collection_id ?? "") : (defaultCollection ?? ""));
    const [tagText, setTagText] = useState(link?.tags.join(", ") ?? "");
    const [pinned, setPinned] = useState(link?.pinned ?? false);
    const save = useMutation({
        mutationFn: (target) => {
            const body = {
                url: target,
                name,
                description,
                collection_id: collection || null,
                tags: tagText
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                pinned,
            };
            return link ? api.patch(`/api/v1/links/${link.id}`, body) : api.post("/api/v1/links", body);
        },
        onSuccess: onDone,
    });
    const submit = (e) => {
        e.preventDefault();
        let value = url.trim();
        if (value && !/^[a-z][a-z0-9+.-]*:/i.test(value))
            value = `https://${value}`;
        setUrl(value);
        save.mutate(value);
    };
    return (_jsxs("form", { className: "form", onSubmit: submit, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "URL" }), _jsx("input", { value: url, onChange: (e) => setUrl(e.target.value), onBlur: () => {
                            const v = url.trim();
                            if (v && !/^[a-z][a-z0-9+.-]*:/i.test(v))
                                setUrl(`https://${v}`);
                        }, placeholder: "https://", required: true, autoFocus: true })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Titel" }), _jsx("input", { value: name, onChange: (e) => setName(e.target.value), placeholder: "Leeg = de URL" })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Beschrijving" }), _jsx("textarea", { rows: 3, value: description, onChange: (e) => setDescription(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Collectie" }), _jsx(CollectionSelect, { tree: tree, value: collection, onChange: setCollection, emptyLabel: "Zonder collectie" })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Tags (komma's ertussen)" }), _jsx("input", { value: tagText, onChange: (e) => setTagText(e.target.value), placeholder: "homelab, docs" })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: pinned, onChange: (e) => setPinned(e.target.checked) }), " Vastpinnen"] }), _jsx(ErrorBox, { error: save.error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: save.isPending, children: link ? "Bewaren" : "Toevoegen" }) })] }));
}
function CollectionForm({ col, cols, tree, defaultParent, onDone, }) {
    const [name, setName] = useState(col?.name ?? "");
    const [description, setDescription] = useState(col?.description ?? "");
    const [color, setColor] = useState(col?.color ?? COLORS[0]);
    const [parent, setParent] = useState(col ? (col.parent_id ?? "") : (defaultParent ?? ""));
    const below = col ? descendants(cols, col.id) : undefined;
    const save = useMutation({
        mutationFn: () => {
            const body = { name, description, color, parent_id: parent || null };
            return col
                ? api.patch(`/api/v1/links/collections/${col.id}`, body)
                : api.post("/api/v1/links/collections", body);
        },
        onSuccess: () => onDone(),
    });
    const remove = useMutation({
        mutationFn: () => api.delete(`/api/v1/links/collections/${col.id}`),
        onSuccess: () => onDone(true),
    });
    const inside = below ? cols.filter((c) => below.has(c.id)) : [];
    const linkCount = inside.reduce((n, c) => n + c.link_count, 0);
    return (_jsxs("form", { className: "form", onSubmit: (e) => {
            e.preventDefault();
            save.mutate();
        }, children: [_jsxs("label", { className: "field", children: [_jsx("span", { children: "Naam" }), _jsx("input", { value: name, onChange: (e) => setName(e.target.value), required: true, autoFocus: true })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "Beschrijving" }), _jsx("textarea", { rows: 2, value: description, onChange: (e) => setDescription(e.target.value) })] }), _jsxs("label", { className: "field", children: [_jsx("span", { children: "In collectie" }), _jsx(CollectionSelect, { tree: tree, value: parent, onChange: setParent, exclude: below, emptyLabel: "(hoogste niveau)" })] }), _jsxs("div", { className: "field", children: [_jsx("span", { children: "Kleur" }), _jsxs("div", { className: "row wrap", children: [COLORS.map((c) => (_jsx("button", { type: "button", className: `color-swatch${color === c ? " active" : ""}`, "aria-label": c, onClick: () => setColor(c), children: _jsx("svg", { viewBox: "0 0 10 10", "aria-hidden": "true", children: _jsx("circle", { cx: "5", cy: "5", r: "5", fill: c }) }) }, c))), _jsx("input", { type: "color", value: color, onChange: (e) => setColor(e.target.value), "aria-label": "Eigen kleur" })] })] }), _jsx(ErrorBox, { error: save.error ?? remove.error }), _jsxs("div", { className: "form-actions", children: [col && (_jsx("button", { type: "button", className: "btn btn-danger", onClick: () => {
                            const extra = inside.length > 1 ? ` met ${inside.length - 1} subcollecties` : "";
                            if (confirm(`"${col.name}"${extra} en ${linkCount} links verwijderen? Dit kan niet ongedaan worden.`))
                                remove.mutate();
                        }, children: "Verwijderen" })), _jsx("button", { className: "btn btn-primary", disabled: save.isPending, children: col ? "Bewaren" : "Aanmaken" })] })] }));
}
function ImportForm({ onImported }) {
    const [format, setFormat] = useState("linkwarden");
    const [file, setFile] = useState(null);
    const run = useMutation({
        mutationFn: async () => {
            const data = await file.text();
            return api.post("/api/v1/links/import", { format, data });
        },
        onSuccess: onImported,
    });
    const res = run.data;
    return (_jsxs("div", { className: "stack", children: [_jsxs("form", { className: "form", onSubmit: (e) => {
                    e.preventDefault();
                    if (file)
                        run.mutate();
                }, children: [_jsxs("div", { className: "seg", children: [_jsxs("label", { className: "check", children: [_jsx("input", { type: "radio", checked: format === "linkwarden", onChange: () => setFormat("linkwarden") }), " ", "Linkwarden-export (.json)"] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "radio", checked: format === "bookmarks", onChange: () => setFormat("bookmarks") }), " ", "Browserbladwijzers (.html)"] })] }), _jsx("p", { className: "muted small", children: format === "linkwarden"
                            ? "In Linkwarden: Instellingen > Data > Export Data. Collecties (met nesting), links, tags en vastgepinde links komen mee; gearchiveerde bestanden zitten niet in die export. Ook een VaultX-export werkt hier."
                            : "In Edge: Favorieten > ... > Favorieten exporteren. Chrome en Firefox hebben dezelfde export. Mappen worden subcollecties van \"Geïmporteerde bladwijzers\"." }), _jsx("input", { type: "file", accept: format === "linkwarden" ? ".json,application/json" : ".html,.htm,text/html", onChange: (e) => setFile(e.target.files?.[0] ?? null) }), _jsx(ErrorBox, { error: run.error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: !file || run.isPending, children: run.isPending ? "Bezig met importeren…" : "Importeren" }) })] }), res && (_jsxs("div", { className: "good-box", children: [res.collections_created, " collecties en ", res.links_created, " links aangemaakt", res.links_updated ? `, ${res.links_updated} bestaande links aangevuld` : "", ", ", res.tags_created, " nieuwe tags", res.skipped ? `, ${res.skipped} overgeslagen` : "", ". Opnieuw importeren maakt geen dubbels.", res.warnings.length > 0 && (_jsx("ul", { className: "small", children: res.warnings.map((w, i) => (_jsx("li", { children: w }, i))) }))] })), _jsxs("details", { children: [_jsx("summary", { children: "Draait Linkwarden niet meer? Importeer rechtstreeks uit de database" }), _jsxs("p", { className: "small", children: ["Op de VaultX-server, als root. Dit neemt ook de screenshots, pdf's en leesbare versies mee als je de map met Linkwarden-bestanden (STORAGE_FOLDER, standaard ", _jsx("code", { children: "data" }), ") meegeeft. De Linkwarden-database wordt enkel gelezen."] }), _jsx("pre", { className: "wide-pre", children: `set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend
export PGPASSWORD='wachtwoord-van-linkwarden-db'
/opt/vaultx/venv/bin/python -m app.cli import-linkwarden \\
  --dsn postgresql://linkwarden@HOST:5432/linkwarden \\
  --to jouw-vaultx-login --files /pad/naar/linkwarden/data` }), _jsxs("p", { className: "small muted", children: ["Met ", _jsx("code", { children: "--list-users" }), " zie je de Linkwarden-gebruikers, met ", _jsx("code", { children: "--dry-run" }), " enkel wat er gelezen wordt. Opnieuw uitvoeren vult aan zonder dubbels. Zie docs/links.md."] })] })] }));
}
function TagManager({ tags, onChanged }) {
    const rename = useMutation({
        mutationFn: ({ id, name }) => api.patch(`/api/v1/links/tags/${id}`, { name }),
        onSuccess: onChanged,
    });
    const remove = useMutation({
        mutationFn: (id) => api.delete(`/api/v1/links/tags/${id}`),
        onSuccess: onChanged,
    });
    return (_jsxs("div", { className: "stack", children: [_jsx("p", { className: "muted small", children: "Hernoem je een tag naar een naam die al bestaat, dan worden beide samengevoegd." }), _jsx(ErrorBox, { error: rename.error ?? remove.error }), _jsx("ul", { className: "plain-list", children: tags.map((t) => (_jsxs("li", { className: "row", children: [_jsxs("span", { className: "grow", children: ["#", t.name, " ", _jsxs("span", { className: "muted small", children: [t.link_count, " links"] })] }), _jsx("button", { className: "btn btn-ghost", onClick: () => {
                                const name = prompt("Nieuwe naam", t.name);
                                if (name && name.trim() && name.trim() !== t.name)
                                    rename.mutate({ id: t.id, name: name.trim() });
                            }, children: "Hernoemen" }), _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => {
                                if (confirm(`Tag #${t.name} verwijderen? De links blijven.`))
                                    remove.mutate(t.id);
                            }, children: "Verwijderen" })] }, t.id))) })] }));
}
/**
 * Leesbare versie uit het archief: gesaneerd, links openen in een nieuw tabblad en
 * afbeeldingen van buitenaf vallen weg (de CSP zou ze toch blokkeren, en ze verklappen
 * aan die site dat je het artikel leest).
 */
function cleanArticle(content) {
    const frag = DOMPurify.sanitize(content, {
        FORBID_TAGS: ["style", "form", "input", "button", "iframe", "object", "embed", "video", "audio", "source"],
        FORBID_ATTR: ["style", "srcset"],
        RETURN_DOM_FRAGMENT: true,
    });
    frag.querySelectorAll("img").forEach((img) => {
        if (!img.getAttribute("src")?.startsWith("data:image/"))
            img.remove();
    });
    frag.querySelectorAll("a[href]").forEach((a) => {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noreferrer noopener");
    });
    const box = document.createElement("div");
    box.append(frag);
    return box.innerHTML;
}
function Readable({ link }) {
    const doc = useQuery({
        queryKey: ["links", "readable", link.id],
        queryFn: async () => {
            const res = await fetch(archiveUrl(link, "readable"), { credentials: "same-origin" });
            if (!res.ok)
                throw new Error("De leesbare versie kon niet geladen worden");
            return (await res.json());
        },
    });
    const html = useMemo(() => (doc.data?.content ? cleanArticle(doc.data.content) : ""), [doc.data]);
    if (doc.isLoading)
        return _jsx(Loading, {});
    if (doc.error)
        return _jsx(ErrorBox, { error: doc.error });
    return (_jsxs("article", { className: "md link-readable", children: [_jsxs("p", { className: "muted small", children: [_jsx("a", { href: link.url, target: "_blank", rel: "noreferrer noopener", children: link.url }), doc.data?.byline ? ` · ${doc.data.byline}` : ""] }), _jsx("div", { dangerouslySetInnerHTML: { __html: html } })] }));
}
