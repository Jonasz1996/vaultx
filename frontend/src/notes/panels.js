import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Onderdelen van de notitiepagina: bestandsboom, tags, zoekresultaten, zijpaneel met
// backlinks en inhoud, snelkiezer, versiegeschiedenis en prullenbak.
import { useEffect, useMemo, useRef, useState } from "react";
import { Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate, relative } from "../format";
import { GraphView } from "./Graph";
import { renderNote } from "./markdown";
import { extractHeadings, fuzzyScore, isAppLink, notePath, search, textOf, unlinkedMentions, wordCount, } from "./model";
import { baseName, formatSize, isImage } from "./files";
function buildTree(index) {
    const root = { name: "", path: "", folders: [], notes: [] };
    const byPath = new Map([["", root]]);
    const folderFor = (path) => {
        const existing = byPath.get(path);
        if (existing)
            return existing;
        const cut = path.lastIndexOf("/");
        const parent = folderFor(cut < 0 ? "" : path.slice(0, cut));
        const f = { name: path.slice(cut + 1), path, folders: [], notes: [] };
        parent.folders.push(f);
        byPath.set(path, f);
        return f;
    };
    for (const p of index.folders)
        folderFor(p);
    for (const n of index.notes)
        folderFor(n.folder).notes.push(n);
    const sort = (f) => {
        f.folders.sort((a, b) => a.name.localeCompare(b.name, "nl"));
        f.folders.forEach(sort);
    };
    sort(root);
    return root;
}
export function FileTree({ index, currentId, onOpen, onMove, }) {
    const tree = useMemo(() => buildTree(index), [index]);
    const [collapsed, setCollapsed] = useState(new Set());
    const [over, setOver] = useState(null);
    const toggle = (p) => setCollapsed((c) => {
        const n = new Set(c);
        if (n.has(p))
            n.delete(p);
        else
            n.add(p);
        return n;
    });
    const dropProps = (path) => ({
        onDragOver: (e) => {
            if (!e.dataTransfer.types.includes("application/x-vaultx-note"))
                return;
            e.preventDefault();
            setOver(path);
        },
        onDragLeave: () => setOver((o) => (o === path ? null : o)),
        onDrop: (e) => {
            e.preventDefault();
            setOver(null);
            const id = e.dataTransfer.getData("application/x-vaultx-note");
            if (id)
                onMove(id, path);
        },
    });
    const renderFolder = (f, depth) => (_jsxs(_Fragment, { children: [f.folders.map((sub) => (_jsxs("li", { children: [_jsxs("button", { className: `tree-item tree-folder${over === sub.path ? " drop" : ""}`, style: { paddingLeft: 8 + depth * 14 }, onClick: () => toggle(sub.path), ...dropProps(sub.path), children: [_jsx("span", { className: "tree-caret", children: collapsed.has(sub.path) ? "▸" : "▾" }), sub.name] }), !collapsed.has(sub.path) && _jsx("ul", { children: renderFolder(sub, depth + 1) })] }, `d:${sub.path}`))), f.notes.map((n) => (_jsx("li", { children: _jsx("button", { className: `tree-item${n.id === currentId ? " active" : ""}`, style: { paddingLeft: 22 + depth * 14 }, draggable: true, onDragStart: (e) => e.dataTransfer.setData("application/x-vaultx-note", n.id), onClick: () => onOpen(n.id), title: notePath(n), children: _jsxs("span", { children: [n.kind === "canvas" && _jsx("span", { className: "canvas-icon", title: "Canvas", children: "\u25A6 " }), n.title] }) }) }, n.id)))] }));
    return (_jsx("div", { className: `tree${over === "" ? " drop" : ""}`, ...dropProps(""), children: _jsx("ul", { children: renderFolder(tree, 0) }) }));
}
export function TagList({ index, onTag }) {
    const tags = [...index.tags.entries()].sort((a, b) => a[0].localeCompare(b[0], "nl"));
    if (!tags.length)
        return _jsx("p", { className: "muted small pad", children: "Nog geen tags. Typ #tag in een notitie." });
    return (_jsx("ul", { className: "tag-list", children: tags.map(([tag, notes]) => (_jsx("li", { style: { paddingLeft: tag.split("/").length * 10 - 10 }, children: _jsxs("button", { className: "tree-item", onClick: () => onTag(tag), children: [_jsxs("span", { children: ["#", tag.split("/").pop()] }), _jsx("span", { className: "count", children: notes.length })] }) }, tag))) }));
}
// ---------------------------------------------------------------- bijlagen
/** Notities die naar een bijlage verwijzen (op naam of bestandsnaam, zoals Obsidian). */
export function notesUsing(index, f) {
    const names = [f.name.toLowerCase(), baseName(f.name).toLowerCase()];
    return index.notes.filter((n) => {
        const body = textOf(n).toLowerCase();
        return names.some((x) => body.includes(`[[${x}`) || body.includes(`](${x}`) || body.includes(`](${encodeURI(x)}`));
    });
}
export function AttachmentList({ files, usage, index, onInsert, onDownload, onDelete, onOpen, }) {
    const [open, setOpen] = useState(null);
    if (!files)
        return _jsx(Loading, {});
    const sorted = [...files].sort((a, b) => a.name.localeCompare(b.name, "nl"));
    return (_jsxs("div", { className: "attachments", children: [_jsxs("p", { className: "muted small pad", children: [formatSize(usage.used), " van ", formatSize(usage.quota), " gebruikt. Plak of sleep een bestand in een notitie om het toe te voegen."] }), !sorted.length && _jsx("p", { className: "muted small pad", children: "Nog geen bijlagen." }), _jsx("ul", { className: "tree", children: sorted.map((f) => {
                    const used = notesUsing(index, f);
                    return (_jsxs("li", { className: "attachment", children: [_jsxs("button", { className: "tree-item", onClick: () => setOpen(open === f.id ? null : f.id), title: f.name, children: [_jsxs("span", { className: "attachment-name", children: [isImage(f.name) ? "🖼" : "📎", " ", f.name] }), _jsx("span", { className: "count", title: "Gebruikt in zoveel notities", children: used.length })] }), open === f.id && (_jsxs("div", { className: "attachment-detail small", children: [_jsx("div", { className: "muted", children: formatSize(f.size) }), used.length > 0 && (_jsx("ul", { children: used.map((n) => (_jsx("li", { children: _jsx("a", { className: "wikilink", onClick: () => onOpen(n.id), children: n.title }) }, n.id))) })), _jsxs("div", { className: "row wrap", children: [onInsert && (_jsx("button", { className: "btn btn-ghost small", onClick: () => onInsert(f), children: "Invoegen" })), _jsx("button", { className: "btn btn-ghost small", onClick: () => onDownload(f), children: "Downloaden" }), _jsx("button", { className: "btn btn-ghost small btn-danger-text", onClick: () => onDelete(f, used), children: "Verwijderen" })] })] }))] }, f.id));
                }) })] }));
}
export function SearchResults({ index, query, currentId, onOpen, }) {
    const hits = useMemo(() => search(index, query), [index, query]);
    if (!hits.length)
        return _jsx("p", { className: "muted small pad", children: "Niets gevonden." });
    return (_jsxs("ul", { className: "results", children: [_jsxs("li", { className: "muted small pad", children: [hits.length, " ", hits.length === 1 ? "notitie" : "notities"] }), hits.map((h) => (_jsx("li", { children: _jsxs("button", { className: `result${h.note.id === currentId ? " active" : ""}`, onClick: () => onOpen(h.note.id), children: [_jsx("span", { className: "result-title", children: h.note.title }), h.note.folder && _jsx("span", { className: "muted small", children: h.note.folder }), h.snippet && _jsx("span", { className: "result-snippet small", children: h.snippet })] }) }, h.note.id)))] }));
}
// ---------------------------------------------------------------- zijpaneel rechts
function Section({ title, count, children, initiallyOpen = true }) {
    const [open, setOpen] = useState(initiallyOpen);
    return (_jsxs("section", { className: "meta-section", children: [_jsxs("button", { className: "meta-head", onClick: () => setOpen(!open), children: [_jsxs("span", { children: [open ? "▾" : "▸", " ", title] }), count !== undefined && _jsx("span", { className: "count", children: count })] }), open && _jsx("div", { className: "meta-body", children: children })] }));
}
function LinkList({ items, onOpen, action }) {
    return (_jsx("ul", { className: "backlinks", children: items.map((b, i) => (_jsxs("li", { children: [_jsx("button", { className: "link-btn", onClick: () => onOpen(b.from.id), children: b.from.title }), _jsx("div", { className: "context small", children: b.line.trim().slice(0, 160) }), action?.(b)] }, `${b.from.id}-${i}`))) }));
}
export function PropertiesTable({ props }) {
    const entries = Object.entries(props);
    if (!entries.length)
        return null;
    return (_jsx("dl", { className: "props", children: entries.map(([k, v]) => (_jsxs("div", { className: "prop", children: [_jsx("dt", { children: k }), _jsx("dd", { children: Array.isArray(v) ? v.map((x) => _jsx("span", { className: "chip", children: x }, x)) : v })] }, k))) }));
}
export function MetaPanel({ index, note, draft, onOpen, onHeading, onLinkMention, onTag, }) {
    const backlinks = index.backlinks.get(note.id) ?? [];
    const mentions = useMemo(() => unlinkedMentions(index, note), [index, note]);
    const headings = useMemo(() => extractHeadings(textOf(draft)), [draft]);
    const outgoing = (index.outgoing.get(note.id) ?? []).filter((l) => l.target && !isAppLink(l.target));
    const tags = index.tagsOf.get(note.id) ?? [];
    const uniqueOut = [...new Map(outgoing.map((l) => [l.target.toLowerCase(), l])).values()];
    return (_jsxs("aside", { className: "notes-meta", children: [_jsx(Section, { title: "Lokale grafiek", children: _jsx(GraphView, { index: index, currentId: note.id, onOpen: onOpen, onTag: onTag, compact: true }) }), _jsx(Section, { title: "Backlinks", count: backlinks.length, children: backlinks.length ? _jsx(LinkList, { items: backlinks, onOpen: onOpen }) : _jsx("p", { className: "muted small", children: "Geen notities linken hierheen." }) }), _jsx(Section, { title: "Niet-gelinkte vermeldingen", count: mentions.length, initiallyOpen: mentions.length > 0, children: mentions.length ? (_jsx(LinkList, { items: mentions, onOpen: onOpen, action: (b) => (_jsx("button", { className: "btn btn-ghost small", onClick: () => onLinkMention(b), children: "Link maken" })) })) : (_jsx("p", { className: "muted small", children: "Geen." })) }), _jsx(Section, { title: "Uitgaande links", count: uniqueOut.length, children: uniqueOut.length ? (_jsx("ul", { className: "plain-links", children: uniqueOut.map((l) => {
                        const t = index.resolve(l.target);
                        return (_jsx("li", { children: t ? (_jsx("button", { className: "link-btn", onClick: () => onOpen(t.id), children: t.title })) : (_jsx("span", { className: "muted", title: "Bestaat nog niet", children: l.target })) }, l.target));
                    }) })) : (_jsx("p", { className: "muted small", children: "Geen." })) }), headings.length > 0 && (_jsx(Section, { title: "Inhoud", children: _jsx("ul", { className: "outline", children: headings.map((h, i) => (_jsx("li", { style: { paddingLeft: (h.level - 1) * 12 }, children: _jsx("button", { className: "link-btn", onClick: () => onHeading(h), children: h.text }) }, i))) }) })), _jsx(Section, { title: "Info", children: _jsxs("dl", { className: "kv kv-small", children: [_jsx("dt", { children: "Woorden" }), _jsx("dd", { children: wordCount(textOf(draft)).toLocaleString("nl-BE") }), _jsx("dt", { children: "Tags" }), _jsx("dd", { children: tags.length
                                ? tags.map((t) => (_jsxs("button", { className: "tag", onClick: () => onTag(t), children: ["#", t] }, t)))
                                : "—" }), _jsx("dt", { children: "Aangemaakt" }), _jsx("dd", { children: formatDate(note.created_at) }), _jsx("dt", { children: "Gewijzigd" }), _jsx("dd", { children: relative(note.updated_at) }), _jsx("dt", { children: "Versie" }), _jsx("dd", { children: note.revision })] }) })] }));
}
// ---------------------------------------------------------------- snelkiezer
export function QuickSwitcher({ open, index, onClose, onOpen, onCreate, }) {
    const [q, setQ] = useState("");
    const [active, setActive] = useState(0);
    const input = useRef(null);
    useEffect(() => {
        if (open) {
            setQ("");
            setActive(0);
            setTimeout(() => input.current?.focus());
        }
    }, [open]);
    const list = useMemo(() => index.notes
        .map((n) => ({ n, s: Math.max(fuzzyScore(n.title, q), fuzzyScore(notePath(n), q) - 1) }))
        .filter((x) => x.s >= 0)
        .sort((a, b) => b.s - a.s || b.n.updated_at.localeCompare(a.n.updated_at))
        .slice(0, 12)
        .map((x) => x.n), [index, q]);
    const exact = list.some((n) => n.title.toLowerCase() === q.trim().toLowerCase());
    const total = list.length + (q.trim() && !exact ? 1 : 0);
    const pick = (i) => {
        if (i < list.length)
            onOpen(list[i].id);
        else
            onCreate(q.trim());
        onClose();
    };
    return (_jsx(Modal, { title: "Notitie openen", open: open, onClose: onClose, children: _jsxs("div", { className: "picker", children: [_jsx("input", { ref: input, value: q, placeholder: "Typ een titel\u2026 (Enter opent, of maakt aan)", onChange: (e) => {
                        setQ(e.target.value);
                        setActive(0);
                    }, onKeyDown: (e) => {
                        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                            e.preventDefault();
                            if (total)
                                setActive((a) => (a + (e.key === "ArrowDown" ? 1 : total - 1)) % total);
                        }
                        else if (e.key === "Enter" && total) {
                            e.preventDefault();
                            pick(active);
                        }
                    } }), _jsxs("ul", { className: "picker-list", children: [list.map((n, i) => (_jsx("li", { className: i === active ? "active" : undefined, children: _jsxs("button", { onClick: () => pick(i), children: [_jsx("span", { children: n.title }), n.folder && _jsx("span", { className: "muted small", children: n.folder })] }) }, n.id))), q.trim() && !exact && (_jsx("li", { className: active === list.length ? "active" : undefined, children: _jsx("button", { onClick: () => pick(list.length), children: _jsxs("span", { children: ["Nieuwe notitie ", _jsx("b", { children: q.trim() })] }) }) })), !total && _jsx("li", { className: "muted", children: "Nog geen notities." })] }), _jsx("p", { className: "muted small", children: "Sneltoets: Ctrl+O of Ctrl+K (\u2318 op Mac)." })] }) }));
}
// ---------------------------------------------------------------- versiegeschiedenis
export function HistoryModal({ open, note, load, ctx, onClose, onRestore, }) {
    const [versions, setVersions] = useState(null);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(0);
    useEffect(() => {
        if (!open)
            return;
        setVersions(null);
        setSelected(0);
        load(note.id).then(setVersions, setError);
    }, [open, note.id, load]);
    const v = versions?.[selected];
    const html = useMemo(() => (v ? renderNote(textOf(v), ctx).html : ""), [v, ctx]);
    return (_jsxs(Modal, { title: `Versiegeschiedenis: ${note.title}`, open: open, onClose: onClose, wide: true, children: [_jsx(ErrorBox, { error: error }), !versions ? (_jsx(Loading, {})) : !versions.length ? (_jsx(Empty, { children: "Nog geen eerdere versies. VaultX bewaart een versie wanneer je een notitie na minstens 10 minuten opnieuw wijzigt." })) : (_jsxs("div", { className: "history", children: [_jsx("ul", { className: "history-list", children: versions.map((x, i) => (_jsx("li", { children: _jsxs("button", { className: i === selected ? "active" : undefined, onClick: () => setSelected(i), children: [_jsx("span", { children: formatDate(x.saved_at) }), _jsxs("span", { className: "muted small", children: ["versie ", x.revision, " \u00B7 ", wordCount(textOf(x)), " woorden", x.title !== note.title ? ` · "${x.title}"` : ""] })] }) }, x.revision))) }), _jsxs("div", { className: "history-preview", children: [_jsx("div", { className: "md", dangerouslySetInnerHTML: { __html: html } }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", onClick: () => v && onRestore(v), children: "Deze versie terugzetten" }) })] })] }))] }));
}
// ---------------------------------------------------------------- prullenbak
export function TrashView({ notes, onRestore, onDelete, }) {
    const trashed = notes.filter((n) => n.deleted_at).sort((a, b) => (b.deleted_at ?? "").localeCompare(a.deleted_at ?? ""));
    return (_jsxs("div", { className: "trash", children: [_jsxs("div", { className: "row", style: { justifyContent: "space-between" }, children: [_jsx("h2", { children: "Prullenbak" }), trashed.length > 0 && (_jsx("button", { className: "btn btn-danger", onClick: () => {
                            if (confirm(`${trashed.length} notities definitief verwijderen? Dit kan niet ongedaan worden.`))
                                onDelete(trashed.map((n) => n.id));
                        }, children: "Prullenbak leegmaken" }))] }), !trashed.length ? (_jsx(Empty, { children: "De prullenbak is leeg." })) : (_jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Notitie" }), _jsx("th", { children: "Verwijderd" }), _jsx("th", {})] }) }), _jsx("tbody", { children: trashed.map((n) => (_jsxs("tr", { children: [_jsxs("td", { children: [n.title, n.folder && _jsx("div", { className: "muted small", children: n.folder })] }), _jsx("td", { children: relative(n.deleted_at) }), _jsxs("td", { className: "right nowrap", children: [_jsx("button", { className: "btn btn-ghost", onClick: () => onRestore(n.id), children: "Terugzetten" }), _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: () => confirm(`"${n.title}" definitief verwijderen?`) && onDelete([n.id]), children: "Definitief verwijderen" })] })] }, n.id))) })] }))] }));
}
