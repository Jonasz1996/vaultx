import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { CanvasView } from "../notes/Canvas";
import { serializeCanvas } from "../notes/canvas";
import { useDiagrams } from "../notes/diagrams";
import { Editor } from "../notes/Editor";
import { baseName, isImage, mimeOf, useNoteFiles } from "../notes/files";
import { GraphView } from "../notes/Graph";
import { download, exportZip, planImport } from "../notes/importExport";
import { renderNote } from "../notes/markdown";
import { DAILY_FOLDER, TEMPLATE_FOLDER, applyTemplate, buildIndex, cleanFolder, cleanTitle, docOf, emptyDoc, isoDate, notePath, isCanvas, renameInDoc, slug, splitFrontmatter, toggleTask, uniqueTitle, } from "../notes/model";
import { AttachmentList, FileTree, HistoryModal, MetaPanel, PropertiesTable, QuickSwitcher, SearchResults, TagList, TrashView, } from "../notes/panels";
import { AUTO_LOCK_MINUTES, useVaultKey } from "../notes/session";
import { useNotesStore } from "../notes/store";
const MODE_KEY = "vaultx.notes.mode";
function storedMode() {
    try {
        const m = localStorage.getItem(MODE_KEY);
        if (m === "edit" || m === "split" || m === "read")
            return m;
    }
    catch {
        /* geen opslag beschikbaar */
    }
    return "split";
}
export function NotesPage() {
    const status = useQuery({ queryKey: ["vault"], queryFn: () => api.get("/api/v1/me/vault") });
    const { key } = useVaultKey();
    if (status.isLoading)
        return _jsx(Loading, {});
    if (!status.data)
        return _jsx(ErrorBox, { error: status.error });
    if (!status.data.enrolled)
        return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Notities", subtitle: "Versleutelde Markdown-notities in je persoonlijke kluis." }), _jsx(Card, { children: _jsxs("p", { children: ["Notities worden versleuteld met de sleutels van je kluis. Activeer eerst je kluis op ", _jsx(Link, { to: "/vault", children: "Mijn kluis" }), "."] }) })] }));
    if (!key)
        return _jsx(Unlock, { status: status.data });
    return _jsx(Workspace, { userKey: key });
}
function Unlock({ status }) {
    const { unlock } = useVaultKey();
    const [password, setPassword] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const submit = async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
            await unlock(status, password);
            setPassword("");
        }
        catch (err) {
            setError(err);
        }
        finally {
            setBusy(false);
        }
    };
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Notities", subtitle: "Versleutelde Markdown-notities in je persoonlijke kluis." }), _jsx(Card, { title: "Kluis ontgrendelen", children: _jsxs("form", { className: "form", style: { maxWidth: 460 }, onSubmit: submit, children: [_jsxs("p", { className: "muted small", style: { margin: 0 }, children: ["Je notities zijn versleuteld met je kluissleutel. Je browser leidt die af uit je master password; VaultX ziet het wachtwoord en de inhoud van je notities nooit. Na ", AUTO_LOCK_MINUTES, " minuten zonder activiteit vergrendelt de kluis vanzelf."] }), _jsxs("label", { className: "field", children: [_jsxs("span", { children: ["Master password voor ", status.email] }), _jsx("input", { type: "password", autoComplete: "current-password", autoFocus: true, required: true, value: password, onChange: (e) => setPassword(e.target.value) })] }), _jsx(ErrorBox, { error: error }), _jsx("div", { className: "form-actions", children: _jsx("button", { className: "btn btn-primary", disabled: busy || !password, children: busy ? "Sleutel afleiden…" : "Ontgrendelen" }) })] }) })] }));
}
const WELCOME = `---
tags: [vaultx, handleiding]
aliases: [Handleiding notities]
---
# Welkom in je notities

Alles wat je hier schrijft, wordt **in je browser** versleuteld met je kluissleutel. VaultX bewaart enkel versleutelde tekst.

## Linken en terugvinden
- Link naar een andere notitie met [[Runbook Grafana]]; bestaat ze nog niet, dan maakt een klik haar aan.
- Een stuk van een notitie: [[Runbook Grafana#Herstarten]], of met een eigen tekst: [[Runbook Grafana|het runbook]].
- Een app uit de catalogus: [[app:Grafana]].
- Tags zoals #vaultx of geneste tags zoals #infra/proxmox. Klik erop om te zoeken.
- Ctrl+O opent de snelkiezer, de grafiek toont hoe alles samenhangt.

## Opmaak
==Markeren==, ~~doorhalen~~, \`code\` en tabellen werken zoals in Obsidian.

> [!tip] Callouts
> Gebruik > [!note], > [!warning], > [!danger] … Met > [!info]- klap je ze dicht.

- [x] Notities aanmaken
- [ ] Je Obsidian-kluis importeren (menu ⋯)

Een pincode of herstelcode? Zet ze tussen dubbele strepen: ||1234-5678||. Ze blijft wazig tot je erop klikt.

%%Dit is commentaar: je ziet het alleen tijdens het bewerken.%%
`;
const DAILY_DEFAULT = `# {{date}}

## Taken
- [ ]

## Notities
`;
function Workspace({ userKey }) {
    const store = useNotesStore(userKey);
    const files = useNoteFiles(userKey);
    const diagrams = useDiagrams();
    const { lock } = useVaultKey();
    const navigate = useNavigate();
    const [params, setParams] = useSearchParams();
    const currentId = params.get("n");
    const [view, setView] = useState("note");
    const [mode, setModeState] = useState(storedMode);
    const [query, setQuery] = useState("");
    const [sideTab, setSideTab] = useState("files");
    const [switcher, setSwitcher] = useState(false);
    const [history, setHistory] = useState(false);
    const [menu, setMenu] = useState(false);
    const [toast, setToast] = useState(null);
    const [error, setError] = useState(null);
    const [importPlan, setImportPlan] = useState(null);
    const [importing, setImporting] = useState(null);
    const setMode = (m) => {
        setModeState(m);
        try {
            localStorage.setItem(MODE_KEY, m);
        }
        catch {
            /* geen opslag beschikbaar */
        }
    };
    const all = useMemo(() => (store.notes ? [...store.notes.values()] : []), [store.notes]);
    const index = useMemo(() => buildIndex(all), [all]);
    const current = currentId ? index.byId.get(currentId) ?? null : null;
    const catalog = useQuery({ queryKey: ["catalog", "notes"], queryFn: () => api.get("/api/v1/catalog"), staleTime: 60_000 });
    const apps = useMemo(() => (catalog.data ?? []).map((a) => ({ name: a.name, url: a.url, href: `/catalog/${a.organization_id}/${a.id}` })), [catalog.data]);
    const { find: findFile, dataUrl, request: requestFile, broken: brokenFile, loaded: filesLoaded } = files;
    const { get: getDiagram, request: requestDiagram, version: diagramsDrawn } = diagrams;
    const ctx = useMemo(() => ({
        index,
        apps,
        files: { find: findFile, dataUrl, request: requestFile, broken: brokenFile },
        diagrams: { get: getDiagram, request: requestDiagram },
    }), 
    // filesLoaded en diagramsDrawn: opnieuw renderen zodra een afbeelding of diagram klaar is
    [index, apps, findFile, dataUrl, requestFile, brokenFile, filesLoaded, getDiagram, requestDiagram, diagramsDrawn]);
    const flash = useCallback((msg) => {
        setToast(msg);
        window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 4000);
    }, []);
    const openNote = useCallback((id, heading) => {
        setView("note");
        setParams((p) => {
            const next = new URLSearchParams(p);
            next.set("n", id);
            return next;
        });
        if (heading)
            pendingHeading.current = heading;
    }, [setParams]);
    const pendingHeading = useRef(null);
    // ------------------------------------------------------------ concept en automatisch bewaren
    const [draft, setDraft] = useState(null);
    const [saveState, setSaveState] = useState("saved");
    const [conflict, setConflict] = useState(null);
    const draftRef = useRef(null);
    const timer = useRef(undefined);
    const titleAtFocus = useRef(null);
    const editor = useRef(null);
    // Vaste referentie, zodat flush (en de opruimfunctie die hem bij vergrendelen oproept)
    // niet bij elke render verandert.
    const storeRef = useRef(store);
    storeRef.current = store;
    const flush = useCallback(async () => {
        window.clearTimeout(timer.current);
        const d = draftRef.current;
        if (!d || !d.dirty)
            return;
        d.dirty = false;
        const doc = d.doc;
        setSaveState("saving");
        try {
            const res = await storeRef.current.save(d.id, doc);
            if (!res.ok) {
                setConflict({ mine: doc, theirs: res.current });
                setSaveState("error");
                return;
            }
            if (draftRef.current?.id === d.id && !draftRef.current.dirty)
                setSaveState("saved");
        }
        catch (e) {
            if (draftRef.current?.id === d.id)
                draftRef.current.dirty = true;
            setSaveState("error");
            setError(e);
        }
    }, []);
    const edit = useCallback((patch) => {
        const d = draftRef.current;
        if (!d)
            return;
        d.doc = { ...d.doc, ...patch };
        d.dirty = true;
        setDraft(d.doc);
        setSaveState("dirty");
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void flush(), 800);
    }, [flush]);
    // Andere notitie geopend: eerst de vorige bewaren, dan het concept laden.
    useEffect(() => {
        const prev = draftRef.current;
        if (prev && prev.id !== currentId && prev.dirty)
            void flush();
        if (!current) {
            draftRef.current = null;
            setDraft(null);
            return;
        }
        if (draftRef.current?.id !== current.id) {
            draftRef.current = { id: current.id, doc: docOf(current), dirty: false };
            setDraft(docOf(current));
            setSaveState("saved");
            setConflict(null);
        }
    }, [currentId, current?.id]);
    // Bij vergrendelen of weggaan: wat nog niet bewaard is, toch bewaren.
    useEffect(() => {
        const warn = (e) => {
            if (draftRef.current?.dirty)
                e.preventDefault();
        };
        window.addEventListener("beforeunload", warn);
        return () => {
            window.removeEventListener("beforeunload", warn);
            void flush();
        };
    }, [flush]);
    // ------------------------------------------------------------ acties
    const run = useCallback(async (fn) => {
        setError(null);
        try {
            await fn();
        }
        catch (e) {
            setError(e);
        }
    }, []);
    const createNote = useCallback(async (title, folder = current?.folder ?? "", body = "", kind) => {
        const clean = cleanTitle(title) || "Naamloos";
        const n = await store.create(emptyDoc(uniqueTitle(index, clean, cleanFolder(folder)), folder, body, kind));
        openNote(n.id);
        return n;
    }, [current?.folder, index, openNote, store]);
    const newNote = () => run(async () => {
        await createNote("Naamloos");
        setMode(mode === "read" ? "split" : mode);
        setTimeout(() => {
            const t = document.querySelector(".note-title");
            t?.focus();
            t?.select();
        }, 50);
    });
    const newCanvas = () => run(async () => {
        setMenu(false);
        await createNote("Naamloos canvas", undefined, serializeCanvas({ nodes: [], edges: [] }), "canvas");
        setTimeout(() => {
            const t = document.querySelector(".note-title");
            t?.focus();
            t?.select();
        }, 50);
    });
    const templates = index.notes.filter((n) => n.folder === TEMPLATE_FOLDER || n.folder.startsWith(`${TEMPLATE_FOLDER}/`));
    const daily = () => run(async () => {
        const title = isoDate();
        const existing = index.resolve(`${DAILY_FOLDER}/${title}`);
        if (existing)
            return openNote(existing.id);
        const tpl = templates.find((t) => t.title.toLowerCase() === "dagnotitie");
        await createNote(title, DAILY_FOLDER, applyTemplate(tpl ? tpl.body : DAILY_DEFAULT, title));
    });
    const welcome = () => run(async () => {
        const n = await store.create(emptyDoc("Welkom", "", WELCOME));
        await store.create(emptyDoc("Runbook Grafana", "Runbooks", `#runbook #infra\n\nGrafana draait achter NPM en Authentik, zie [[app:Grafana]].\n\n## Herstarten\n1. Log in op de host\n2. \`systemctl restart grafana-server\`\n\n> [!warning] Let op\n> Eerst [[Welkom]] lezen.\n`));
        openNote(n.id);
        setMode("read");
    });
    const trashCurrent = () => current &&
        run(async () => {
            await flush();
            await store.trash(current.id);
            setParams({});
            flash(`"${current.title}" staat in de prullenbak.`);
        });
    const moveNote = (id, folder) => run(async () => {
        const n = index.byId.get(id);
        if (!n || n.folder === folder)
            return;
        if (draftRef.current?.id === id)
            return edit({ folder });
        await store.update(id, (d) => ({ ...d, folder }));
    });
    const togglePin = () => draft && edit({ pinned: !draft.pinned });
    /** Na een titelwijziging de links in andere notities bijwerken (zoals Obsidian). */
    const commitTitle = () => run(async () => {
        const before = titleAtFocus.current;
        titleAtFocus.current = null;
        const d = draftRef.current;
        if (!before || !d || !current)
            return;
        let title = cleanTitle(d.doc.title) || "Naamloos";
        if (title.toLowerCase() !== before.title.toLowerCase()) {
            const others = { ...index, notes: index.notes.filter((n) => n.id !== current.id) };
            title = uniqueTitle(others, title, d.doc.folder);
        }
        if (title !== d.doc.title)
            edit({ title });
        if (title === before.title)
            return;
        await flush();
        const linking = new Set((index.backlinks.get(current.id) ?? []).map((b) => b.from.id));
        let changed = 0;
        for (const id of linking) {
            const newPath = notePath({ title, folder: d.doc.folder });
            if (await store.update(id, (doc) => renameInDoc(doc, before.title, before.path, title, newPath)))
                changed++;
        }
        if (changed)
            flash(`Links in ${changed} ${changed === 1 ? "notitie" : "notities"} bijgewerkt.`);
    });
    const linkMention = (b) => run(async () => {
        if (!current)
            return;
        const names = [current.title];
        const re = new RegExp(`(^|[^\\p{L}\\p{N}\\[])(${names.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\p{L}\\p{N}])`, "iu");
        if (isCanvas(b.from))
            return flash("Vermeldingen in een canvas pas je in het canvas zelf aan.");
        await store.update(b.from.id, (d) => ({
            ...d,
            body: d.body.replace(re, (_m, pre, word) => word === current.title ? `${pre}[[${current.title}]]` : `${pre}[[${current.title}|${word}]]`),
        }));
    });
    const restoreVersion = (v) => {
        edit({ title: v.title, folder: v.folder, body: v.body });
        setHistory(false);
        flash(`Versie ${v.revision} teruggezet.`);
    };
    const resolveConflict = (keep) => {
        if (!conflict || !draftRef.current)
            return;
        if (keep === "theirs") {
            draftRef.current = { id: conflict.theirs.id, doc: docOf(conflict.theirs), dirty: false };
            setDraft(docOf(conflict.theirs));
            setSaveState("saved");
        }
        else
            edit(conflict.mine);
        setConflict(null);
    };
    const startImport = (list) => list &&
        run(async () => {
            setMenu(false);
            setImportPlan(await planImport([...list], index, (files.files ?? []).map((f) => f.name), files.usage.max));
        });
    const doImport = () => importPlan &&
        run(async () => {
            setImporting(0);
            try {
                let done = 0;
                for (const a of importPlan.attachments) {
                    await files.upload(a.file, a.name);
                    setImporting(++done);
                }
                const created = await store.importMany(importPlan.docs, (k) => setImporting(done + k));
                const n = importPlan.attachments.length;
                flash(`${created.length} notities${n ? ` en ${n} ${n === 1 ? "bijlage" : "bijlagen"}` : ""} geïmporteerd.`);
                setImportPlan(null);
            }
            finally {
                setImporting(null);
            }
        });
    const doExport = () => {
        setMenu(false);
        if (!confirm("De export is NIET versleuteld: iedereen met het zip-bestand kan je notities en bijlagen lezen. Doorgaan?"))
            return;
        void run(async () => {
            const attachments = [];
            for (const f of files.files ?? [])
                attachments.push({ name: f.name, data: await files.bytes(f.id) });
            download(exportZip(all, attachments));
        });
    };
    // ------------------------------------------------------------ bijlagen
    const [uploading, setUploading] = useState(0);
    /** Versleutelt en bewaart bestanden en zet er een ![[link]] naar op de cursor. */
    const attach = (list) => void run(async () => {
        setUploading((n) => n + list.length);
        const links = [];
        try {
            for (const f of list) {
                // Een geplakte schermafbeelding heet gewoon "image.png": geef ze een naam zoals Obsidian.
                const name = /^image\.(png|jpe?g|gif|webp)$/i.test(f.name)
                    ? `Geplakte afbeelding ${new Date().toISOString().replace(/\D/g, "").slice(0, 14)}.${f.name.split(".").pop()}`
                    : f.name.replace(/[\\/[\]|#^]/g, "-");
                const saved = await files.upload(f, name);
                links.push(`![[${saved.name}]]`);
                setUploading((n) => n - 1);
            }
        }
        finally {
            setUploading(0);
            if (links.length)
                editor.current?.insert(links.join("\n"));
        }
    });
    const downloadFile = (f) => void run(async () => download(new Blob([await files.bytes(f.id)], { type: mimeOf(f.name) }), baseName(f.name)));
    const deleteFile = (f, usedIn) => {
        const where = usedIn.length ? `\nGebruikt in: ${usedIn.map((n) => n.title).join(", ")}.` : "";
        if (!confirm(`Bijlage "${f.name}" definitief verwijderen?${where}`))
            return;
        void run(() => files.remove(f.id));
    };
    // ------------------------------------------------------------ sneltoetsen
    useEffect(() => {
        const onKey = (e) => {
            const mod = e.ctrlKey || e.metaKey;
            if (mod && (e.key === "o" || e.key === "k")) {
                e.preventDefault();
                setSwitcher(true);
            }
            else if (mod && e.key === "e" && current) {
                e.preventDefault();
                setMode(mode === "read" ? "split" : "read");
            }
            else if (mod && e.key === "s") {
                e.preventDefault();
                void flush();
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    });
    // ------------------------------------------------------------ voorbeeld
    const rendered = useMemo(() => (draft && !isCanvas(draft) ? renderNote(draft.body, ctx) : null), [draft, ctx]);
    const previewRef = useRef(null);
    const scrollToHeading = useCallback((s) => {
        const el = previewRef.current?.querySelector(`[data-heading="${CSS.escape(s)}"]`);
        el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, []);
    useEffect(() => {
        if (pendingHeading.current && rendered) {
            const h = pendingHeading.current;
            pendingHeading.current = null;
            setTimeout(() => scrollToHeading(h), 50);
        }
    }, [rendered, scrollToHeading]);
    const onPreviewClick = (e) => {
        const t = e.target;
        const a = t.closest("a");
        if (a?.classList.contains("wikilink")) {
            e.preventDefault();
            if (a.dataset.app) {
                // Ruwe HTML in een (geïmporteerde) notitie kan zelf class/data-attributen zetten: enkel eigen paden volgen.
                const href = a.getAttribute("href") ?? "";
                return /^\/(?!\/)/.test(href) ? navigate(href) : undefined;
            }
            if (a.dataset.note)
                return openNote(a.dataset.note, a.dataset.heading);
            if (a.dataset.heading)
                return scrollToHeading(a.dataset.heading);
            if (a.dataset.new)
                void run(() => createNote(a.dataset.new, "", ""));
            return;
        }
        if (t.classList.contains("code-copy")) {
            const code = t.parentElement?.querySelector("pre")?.textContent ?? "";
            void navigator.clipboard?.writeText(code).then(() => flash("Code gekopieerd."));
            return;
        }
        if (a?.classList.contains("file-link")) {
            e.preventDefault();
            const f = files.files?.find((x) => x.id === a.dataset.file);
            if (f)
                downloadFile(f);
            return;
        }
        if (a?.classList.contains("tag")) {
            e.preventDefault();
            setQuery(`#${a.dataset.tag}`);
            return;
        }
        if (t.classList.contains("secret")) {
            t.classList.toggle("revealed");
            return;
        }
        if (t instanceof HTMLInputElement && t.dataset.task !== undefined && draft) {
            const { offset } = splitFrontmatter(draft.body);
            const before = draft.body.slice(0, offset);
            edit({ body: before + toggleTask(draft.body.slice(offset), Number(t.dataset.task)) });
        }
    };
    const suggestions = useMemo(() => [
        ...index.notes.map((n) => ({ insert: n.title, label: n.title, hint: n.folder || undefined })),
        ...apps.map((a) => ({ insert: `app:${a.name}`, label: a.name, hint: "app uit de catalogus" })),
        ...(files.files ?? []).map((f) => ({ insert: f.name, label: f.name, hint: isImage(f.name) ? "afbeelding" : "bijlage" })),
    ], [index, apps, files.files]);
    // ------------------------------------------------------------ weergave
    if (store.loadError)
        return _jsx(ErrorBox, { error: store.loadError });
    if (!store.notes)
        return _jsx(Loading, {});
    const pinned = index.notes.filter((n) => n.pinned);
    const trashCount = all.filter((n) => n.deleted_at).length;
    return (_jsxs("div", { className: "notes-app", children: [_jsxs("aside", { className: "notes-side", children: [_jsxs("div", { className: "notes-side-actions", children: [_jsx("button", { className: "btn btn-primary", onClick: newNote, title: "Nieuwe notitie", children: "+ Nieuw" }), _jsx("button", { className: "btn", onClick: daily, title: "Dagnotitie van vandaag", children: "Vandaag" }), _jsx("button", { className: `btn${view === "graph" ? " selected" : ""}`, onClick: () => setView(view === "graph" ? "note" : "graph"), children: "Grafiek" }), _jsxs("div", { className: "menu-wrap", children: [_jsx("button", { className: "btn btn-ghost", onClick: () => setMenu(!menu), "aria-label": "Meer", children: "\u22EF" }), menu && (_jsxs("div", { className: "menu", onMouseLeave: () => setMenu(false), children: [_jsx("button", { className: "menu-item", onClick: newCanvas, children: "Nieuw canvas" }), _jsxs("label", { className: "menu-item", children: ["Markdown-bestanden importeren", _jsx("input", { type: "file", accept: ".md,.markdown,.txt,.canvas", multiple: true, hidden: true, onChange: (e) => startImport(e.target.files) })] }), _jsxs("label", { className: "menu-item", children: ["Obsidian-kluis (map) importeren", _jsx("input", { type: "file", multiple: true, hidden: true, ref: (el) => el?.setAttribute("webkitdirectory", ""), onChange: (e) => startImport(e.target.files) })] }), _jsx("button", { className: "menu-item", onClick: doExport, children: "Exporteren als zip (Markdown)" }), _jsxs("button", { className: "menu-item", onClick: () => (setMenu(false), setView("trash")), children: ["Prullenbak", trashCount ? ` (${trashCount})` : ""] }), _jsxs("button", { className: "menu-item", onClick: () => (setMenu(false), setSwitcher(true)), children: ["Snelkiezer ", _jsx("span", { className: "muted small", children: "Ctrl+O" })] })] }))] })] }), _jsx("input", { className: "notes-search", type: "search", placeholder: "Zoeken\u2026 (#tag, map:x, -woord)", value: query, onChange: (e) => setQuery(e.target.value) }), query.trim() ? (_jsx(SearchResults, { index: index, query: query, currentId: currentId, onOpen: openNote })) : (_jsxs(_Fragment, { children: [pinned.length > 0 && (_jsxs("div", { className: "side-group", children: [_jsx("div", { className: "side-label", children: "Bladwijzers" }), _jsx("ul", { className: "tree", children: pinned.map((n) => (_jsx("li", { children: _jsxs("button", { className: `tree-item${n.id === currentId ? " active" : ""}`, onClick: () => openNote(n.id), children: ["\u2605 ", n.title] }) }, n.id))) })] })), _jsxs("div", { className: "side-tabs", children: [_jsx("button", { className: sideTab === "files" ? "active" : undefined, onClick: () => setSideTab("files"), title: `${index.notes.length} notities`, children: "Bestanden" }), _jsx("button", { className: sideTab === "tags" ? "active" : undefined, onClick: () => setSideTab("tags"), title: `${index.tags.size} tags`, children: "Tags" }), _jsx("button", { className: sideTab === "attachments" ? "active" : undefined, onClick: () => setSideTab("attachments"), title: `${files.files?.length ?? 0} bijlagen`, children: "Bijlagen" })] }), sideTab === "files" ? (_jsx(FileTree, { index: index, currentId: currentId, onOpen: openNote, onMove: moveNote })) : sideTab === "tags" ? (_jsx(TagList, { index: index, onTag: (t) => setQuery(`#${t}`) })) : (_jsx(AttachmentList, { files: files.files, usage: files.usage, index: index, onInsert: current && mode !== "read" ? (f) => editor.current?.insert(`![[${f.name}]]`) : undefined, onDownload: downloadFile, onDelete: deleteFile, onOpen: openNote }))] })), _jsxs("div", { className: "notes-side-foot", children: [_jsx("span", { className: "muted small", children: "\uD83D\uDD12 End-to-end versleuteld" }), _jsx("button", { className: "btn btn-ghost", onClick: () => (void flush().then(lock)), children: "Vergrendelen" })] })] }), _jsxs("section", { className: "notes-main", children: [toast && _jsx("div", { className: "toast", children: toast }), _jsx(ErrorBox, { error: error }), store.unreadable > 0 && (_jsxs("div", { className: "warn-box small", children: [store.unreadable, " notities konden niet ontsleuteld worden en worden niet getoond."] })), view === "trash" ? (_jsx(TrashView, { notes: all, onRestore: (id) => void run(() => store.restore(id)), onDelete: (ids) => void run(async () => {
                            for (const id of ids)
                                await store.remove(id);
                        }) })) : view === "graph" ? (_jsxs("div", { className: "graph-page", children: [_jsx("h2", { children: "Grafiek" }), _jsx(GraphView, { index: index, currentId: currentId, onOpen: openNote, onTag: (t) => setQuery(`#${t}`) })] })) : !current || !draft ? (_jsx(EmptyState, { count: index.notes.length, onNew: newNote, onWelcome: welcome, onSwitch: () => setSwitcher(true) })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "note-head", children: [_jsxs("div", { className: "note-path", children: [_jsx("input", { className: "note-folder", list: "note-folders", value: draft.folder, placeholder: "(hoofdmap)", "aria-label": "Map", onChange: (e) => edit({ folder: e.target.value }), onBlur: () => draft.folder !== cleanFolder(draft.folder) && edit({ folder: cleanFolder(draft.folder) }) }), _jsx("datalist", { id: "note-folders", children: index.folders.map((f) => (_jsx("option", { value: f }, f))) }), _jsx("span", { className: `save-state save-${saveState}`, children: { saved: "Bewaard", dirty: "Gewijzigd", saving: "Bewaren…", error: "Niet bewaard" }[saveState] }), _jsxs("div", { className: "note-actions", children: [!isCanvas(draft) && _jsx("div", { className: "seg", role: "group", "aria-label": "Weergave", children: ["edit", "split", "read"].map((m) => (_jsx("button", { className: mode === m ? "active" : undefined, onClick: () => setMode(m), children: { edit: "Bewerken", split: "Naast elkaar", read: "Lezen" }[m] }, m))) }), _jsx("button", { className: "btn btn-ghost", onClick: togglePin, title: draft.pinned ? "Bladwijzer weghalen" : "Bladwijzer", children: draft.pinned ? "★" : "☆" }), _jsx("button", { className: "btn btn-ghost", onClick: () => (void flush(), setHistory(true)), children: "Geschiedenis" }), _jsx("button", { className: "btn btn-ghost btn-danger-text", onClick: trashCurrent, children: "Verwijderen" })] })] }), _jsx("div", { className: "note-title-row", children: _jsx("input", { className: "note-title", value: draft.title, "aria-label": "Titel", onFocus: () => (titleAtFocus.current = { title: current.title, path: notePath(current) }), onChange: (e) => edit({ title: e.target.value }), onBlur: commitTitle, onKeyDown: (e) => {
                                                if (e.key === "Enter") {
                                                    e.preventDefault();
                                                    e.target.blur();
                                                    editor.current?.focus();
                                                }
                                            } }) })] }), conflict && (_jsxs("div", { className: "warn-box", children: ["Deze notitie is intussen op een ander tabblad of toestel gewijzigd.", " ", _jsx("button", { className: "btn btn-ghost", onClick: () => resolveConflict("mine"), children: "Mijn versie bewaren" }), _jsx("button", { className: "btn btn-ghost", onClick: () => resolveConflict("theirs"), children: "Andere versie laden" })] })), isCanvas(draft) ? (_jsx(CanvasView, { body: draft.body, onChange: (body) => edit({ body }), ctx: ctx, files: files.files ?? [], onContentClick: onPreviewClick, onOpen: openNote }, current.id)) : (_jsxs(_Fragment, { children: [mode !== "read" && (_jsxs("div", { className: "md-toolbar", children: [_jsx("button", { onClick: () => editor.current?.wrap("**"), title: "Vet (Ctrl+B)", children: _jsx("b", { children: "B" }) }), _jsx("button", { onClick: () => editor.current?.wrap("*"), title: "Cursief (Ctrl+I)", children: _jsx("i", { children: "I" }) }), _jsx("button", { onClick: () => editor.current?.wrap("=="), title: "Markeren", children: _jsx("mark", { children: "M" }) }), _jsx("button", { onClick: () => editor.current?.wrap("[[", "]]"), title: "Link naar notitie", children: "[[ ]]" }), _jsx("button", { onClick: () => editor.current?.insert("\n- [ ] "), title: "Taak", children: "\u2610 Taak" }), _jsx("button", { onClick: () => editor.current?.insert("\n> [!note] Titel\n> "), title: "Callout", children: "Callout" }), _jsx("button", { onClick: () => editor.current?.wrap("||"), title: "Geheim: wazig tot je klikt", children: "Geheim" }), _jsxs("label", { className: "md-toolbar-file", title: "Bijlage toevoegen (of plak/sleep een bestand in de tekst)", children: [uploading ? `Opladen… (${uploading})` : "Bijlage", _jsx("input", { type: "file", multiple: true, hidden: true, onChange: (e) => {
                                                            const list = [...(e.target.files ?? [])];
                                                            e.target.value = "";
                                                            if (list.length)
                                                                attach(list);
                                                        } })] }), templates.length > 0 && (_jsxs("select", { value: "", onChange: (e) => {
                                                    const t = templates.find((x) => x.id === e.target.value);
                                                    if (t)
                                                        editor.current?.insert(applyTemplate(t.body, draft.title));
                                                }, children: [_jsx("option", { value: "", children: "Sjabloon invoegen\u2026" }), templates.map((t) => (_jsx("option", { value: t.id, children: t.title }, t.id)))] }))] })), _jsxs("div", { className: `note-body mode-${mode}`, children: [mode !== "read" && (_jsx(Editor, { ref: editor, value: draft.body, onChange: (body) => edit({ body }), suggestions: suggestions, onSaveNow: () => void flush(), onFiles: attach, placeholder: "Schrijf in Markdown. [[ voor een link, # voor een tag. Plak of sleep een afbeelding om ze toe te voegen." })), mode !== "edit" && rendered && (_jsxs("div", { className: "preview", ref: previewRef, children: [_jsx(PropertiesTable, { props: rendered.props }), mode === "read" && _jsx("h1", { className: "preview-title", children: draft.title }), _jsx("div", { className: "md", onClick: onPreviewClick, dangerouslySetInnerHTML: { __html: rendered.html } })] }))] })] })), _jsx(HistoryModal, { open: history, note: current, load: store.versions, ctx: ctx, onClose: () => setHistory(false), onRestore: restoreVersion })] }))] }), view === "note" && current && draft && (_jsx(MetaPanel, { index: index, note: current, draft: draft, onOpen: openNote, onHeading: (h) => (mode === "read" ? scrollToHeading(slug(h.text)) : editor.current?.scrollToLine(h.line)), onLinkMention: linkMention, onTag: (t) => setQuery(`#${t}`) })), _jsx(QuickSwitcher, { open: switcher, index: index, onClose: () => setSwitcher(false), onOpen: openNote, onCreate: (title) => void run(() => createNote(title)) }), _jsx(ImportDialog, { plan: importPlan, progress: importing, onCancel: () => setImportPlan(null), onConfirm: doImport })] }));
}
function EmptyState({ count, onNew, onWelcome, onSwitch }) {
    return (_jsxs("div", { className: "notes-empty", children: [_jsx("h2", { children: count ? "Kies een notitie" : "Nog geen notities" }), _jsx("p", { className: "muted", children: "Markdown-notities zoals in Obsidian: links tussen notities, backlinks, tags, een grafiek, dagnotities en sjablonen. Alles end-to-end versleuteld met je kluissleutel." }), _jsxs("div", { className: "row wrap", children: [_jsx("button", { className: "btn btn-primary", onClick: onNew, children: "Nieuwe notitie" }), count === 0 && (_jsx("button", { className: "btn", onClick: onWelcome, children: "Voorbeeldnotities maken" })), count > 0 && (_jsx("button", { className: "btn", onClick: onSwitch, children: "Zoek een notitie (Ctrl+O)" }))] }), count === 0 && _jsx("p", { className: "muted small", children: "Een Obsidian-kluis overzetten? Kies \u22EF en daarna \"Obsidian-kluis (map) importeren\"." })] }));
}
function ImportDialog({ plan, progress, onCancel, onConfirm, }) {
    if (!plan)
        return null;
    return (_jsx("div", { className: "overlay", children: _jsxs("div", { className: "modal-card", children: [_jsx("h2", { children: "Importeren" }), _jsxs("p", { children: [plan.docs.length, " ", plan.docs.length === 1 ? "notitie" : "notities", plan.attachments.length > 0 && ` en ${plan.attachments.length} ${plan.attachments.length === 1 ? "bijlage" : "bijlagen"}`, " ", "worden in je browser versleuteld en toegevoegd."] }), plan.skipped.length > 0 && (_jsxs("details", { children: [_jsxs("summary", { children: [plan.skipped.length, " bestanden overgeslagen"] }), _jsx("ul", { className: "small", children: plan.skipped.slice(0, 50).map((s) => (_jsxs("li", { children: [s.name, ": ", s.reason] }, s.name))) })] })), _jsxs("div", { className: "form-actions", style: { gap: 8 }, children: [_jsx("button", { className: "btn", onClick: onCancel, disabled: progress !== null, children: "Annuleren" }), _jsx("button", { className: "btn btn-primary", onClick: onConfirm, disabled: !(plan.docs.length + plan.attachments.length) || progress !== null, children: progress !== null ? `Bezig… ${progress}/${plan.docs.length + plan.attachments.length}` : "Importeren" })] })] }) }));
}
