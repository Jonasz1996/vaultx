// Notities: het formaat in de kluis en alles wat de browser eruit afleidt (links, backlinks,
// tags, eigenschappen, zoeken). De server ziet hier niets van: hij bewaart enkel de
// versleutelde JSON van NoteDoc.
import { canvasText, renameInCanvas } from "./canvas";
export const TEMPLATE_FOLDER = "Sjablonen";
export const DAILY_FOLDER = "Dagboek";
export function emptyDoc(title, folder = "", body = "", kind) {
    return { v: 1, title, folder: cleanFolder(folder), body, pinned: false, ...(kind ? { kind } : {}) };
}
export const isCanvas = (n) => n.kind === "canvas";
/** De Markdown-tekst van een notitie; voor een canvas de tekst van zijn kaarten. */
export function textOf(n) {
    return n.kind === "canvas" ? canvasText(n.body) : n.body;
}
export function parseDoc(json) {
    const d = JSON.parse(json);
    return {
        v: 1,
        title: typeof d.title === "string" ? d.title : "Naamloos",
        folder: typeof d.folder === "string" ? cleanFolder(d.folder) : "",
        body: typeof d.body === "string" ? d.body : "",
        pinned: d.pinned === true,
        ...(d.kind === "canvas" ? { kind: "canvas" } : {}),
    };
}
export function docOf(n) {
    return { v: 1, title: n.title, folder: n.folder, body: n.body, pinned: n.pinned, ...(n.kind ? { kind: n.kind } : {}) };
}
export function cleanFolder(folder) {
    return folder
        .split("/")
        .map((p) => p.trim())
        .filter(Boolean)
        .join("/");
}
/** Tekens die in een titel de links of bestandsnamen zouden breken. */
export function cleanTitle(title) {
    return title.replace(/[[\]|#^\\/:]/g, " ").replace(/\s+/g, " ").trim();
}
export function notePath(n) {
    return n.folder ? `${n.folder}/${n.title}` : n.title;
}
const norm = (s) => s.trim().toLowerCase();
// ---------------------------------------------------------------- tekst zonder code
/**
 * Vervangt code (``` blokken en `inline`) en %%commentaar%% door spaties, zodat links en tags
 * daarin niet meetellen, terwijl posities en regelnummers gelijk blijven.
 */
export function maskCode(body) {
    const blank = (m) => m.replace(/[^\n]/g, " ");
    return body
        .replace(/^(```|~~~)[^\n]*\n[\s\S]*?(?:^\1[^\n]*$|(?![\s\S]))/gm, blank)
        .replace(/`[^`\n]+`/g, blank)
        .replace(/%%[\s\S]*?%%/g, blank);
}
export function splitFrontmatter(body) {
    const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(body);
    if (!m)
        return { props: {}, content: body, offset: 0 };
    return { props: parseYamlLite(m[1]), content: body.slice(m[0].length), offset: m[0].length };
}
/** Genoeg YAML voor Obsidian-eigenschappen: `sleutel: waarde`, `[a, b]` en lijsten met `- `. */
function parseYamlLite(src) {
    const props = {};
    let listKey = null;
    const unquote = (s) => s.trim().replace(/^(["'])(.*)\1$/, "$2");
    for (const line of src.split(/\r?\n/)) {
        const item = /^\s+-\s+(.*)$/.exec(line) ?? /^-\s+(.*)$/.exec(line);
        if (item && listKey) {
            const arr = props[listKey];
            props[listKey] = [...(Array.isArray(arr) ? arr : []), unquote(item[1])];
            continue;
        }
        const kv = /^([A-Za-z0-9_\- ]+):\s*(.*)$/.exec(line);
        if (!kv)
            continue;
        const key = kv[1].trim();
        const value = kv[2].trim();
        listKey = null;
        if (!value) {
            props[key] = [];
            listKey = key;
        }
        else if (value.startsWith("[") && value.endsWith("]")) {
            props[key] = value
                .slice(1, -1)
                .split(",")
                .map(unquote)
                .filter(Boolean);
        }
        else
            props[key] = unquote(value);
    }
    return props;
}
function propList(props, ...keys) {
    for (const k of keys) {
        const v = props[k];
        if (Array.isArray(v))
            return v;
        if (typeof v === "string" && v)
            return v.split(",").map((s) => s.trim()).filter(Boolean);
    }
    return [];
}
export const WIKILINK_RE = /(!?)\[\[([^\][\n]+?)\]\]/g;
/** Lijkt dit doel van een link op een bijlage (foto.png, plan.pdf) en niet op een notitie? */
export const looksLikeFile = (target) => /\.[a-z][a-z0-9]{0,4}$/i.test(target) && !/\.(md|markdown)$/i.test(target);
export function parseLinkInner(inner) {
    const [left, alias] = inner.split("|", 2);
    const hash = left.indexOf("#");
    const target = (hash >= 0 ? left.slice(0, hash) : left).trim();
    const heading = hash >= 0 ? left.slice(hash + 1).trim() || null : null;
    return { target, heading, alias: alias?.trim() || null };
}
export function extractLinks(body) {
    const masked = maskCode(body);
    const out = [];
    for (const m of masked.matchAll(WIKILINK_RE)) {
        const index = m.index ?? 0;
        const line = masked.slice(0, index).split("\n").length - 1;
        out.push({ embed: m[1] === "!", ...parseLinkInner(m[2]), index, line });
    }
    return out;
}
// Zoals Obsidian: letters, cijfers, _ - en / (geneste tags), maar niet enkel cijfers.
export const TAG_RE = /(^|[\s(,;])#([\p{L}\p{N}_\-/]*[\p{L}_\-/][\p{L}\p{N}_\-/]*)/gu;
export function extractTags(body) {
    const { props, content } = splitFrontmatter(body);
    const tags = new Set(propList(props, "tags", "tag").map((t) => t.replace(/^#/, "")));
    for (const m of maskCode(content).matchAll(TAG_RE))
        tags.add(m[2].replace(/\/+$/, ""));
    return [...tags].filter(Boolean);
}
export function aliasesOf(n) {
    if (isCanvas(n))
        return [];
    return propList(splitFrontmatter(n.body).props, "aliases", "alias");
}
export function extractHeadings(body) {
    const out = [];
    maskCode(body)
        .split("\n")
        .forEach((l, i) => {
        const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(l);
        if (m)
            out.push({ level: m[1].length, text: m[2], line: i });
    });
    return out;
}
export function slug(text) {
    return norm(text)
        .replace(/[^\p{L}\p{N}]+/gu, "-")
        .replace(/^-|-$/g, "");
}
/** Het stuk van een notitie onder een kop, tot de volgende kop van hetzelfde of hoger niveau. */
export function sectionOf(body, heading) {
    const lines = body.split("\n");
    const hs = extractHeadings(body);
    const start = hs.findIndex((h) => slug(h.text) === slug(heading));
    if (start < 0)
        return null;
    const end = hs.slice(start + 1).find((h) => h.level <= hs[start].level);
    return lines.slice(hs[start].line, end ? end.line : lines.length).join("\n");
}
export function buildIndex(all) {
    const notes = all
        .filter((n) => !n.deleted_at)
        .sort((a, b) => a.title.localeCompare(b.title, "nl", { sensitivity: "base" }));
    const byId = new Map(notes.map((n) => [n.id, n]));
    const byKey = new Map();
    // Volgorde: volledig pad, dan titel, dan alias. De eerste treffer wint, zoals Obsidian.
    for (const n of notes)
        if (!byKey.has(norm(notePath(n))))
            byKey.set(norm(notePath(n)), n);
    for (const n of notes)
        if (!byKey.has(norm(n.title)))
            byKey.set(norm(n.title), n);
    for (const n of notes)
        for (const a of aliasesOf(n))
            if (!byKey.has(norm(a)))
                byKey.set(norm(a), n);
    const resolve = (target) => byKey.get(norm(target.replace(/\.md$/i, "")));
    const outgoing = new Map();
    const backlinks = new Map();
    const unresolved = new Map();
    const tags = new Map();
    const tagsOf = new Map();
    const folders = new Set();
    for (const n of notes) {
        if (n.folder) {
            const parts = n.folder.split("/");
            parts.forEach((_, i) => folders.add(parts.slice(0, i + 1).join("/")));
        }
        const text = textOf(n);
        const links = extractLinks(text);
        outgoing.set(n.id, links);
        const lines = text.split("\n");
        const seen = new Set();
        for (const l of links) {
            if (!l.target || isAppLink(l.target))
                continue;
            const to = resolve(l.target);
            if (!to) {
                if (looksLikeFile(l.target))
                    continue;
                const key = l.target;
                if (!unresolved.has(key))
                    unresolved.set(key, []);
                if (!unresolved.get(key).includes(n))
                    unresolved.get(key).push(n);
                continue;
            }
            if (to.id === n.id || seen.has(`${to.id}:${l.line}`))
                continue;
            seen.add(`${to.id}:${l.line}`);
            if (!backlinks.has(to.id))
                backlinks.set(to.id, []);
            backlinks.get(to.id).push({ from: n, line: lines[l.line] ?? "" });
        }
        const t = extractTags(text);
        tagsOf.set(n.id, t);
        for (const tag of t) {
            // #a/b telt ook mee voor #a
            const parts = tag.split("/");
            parts.forEach((_, i) => {
                const key = parts.slice(0, i + 1).join("/");
                if (!tags.has(key))
                    tags.set(key, []);
                if (!tags.get(key).includes(n))
                    tags.get(key).push(n);
            });
        }
    }
    return {
        notes,
        byId,
        resolve,
        outgoing,
        backlinks,
        tags,
        tagsOf,
        unresolved,
        folders: [...folders].sort((a, b) => a.localeCompare(b, "nl")),
    };
}
/** Notities die de titel (of een alias) vermelden zonder ernaar te linken. */
export function unlinkedMentions(index, note) {
    const names = [note.title, ...aliasesOf(note)].filter((s) => s.length >= 3);
    if (!names.length)
        return [];
    const escaped = names.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
    const re = new RegExp(`(^|[^\\p{L}\\p{N}\\[])(${escaped})(?![\\p{L}\\p{N}])`, "iu");
    const linked = new Set((index.backlinks.get(note.id) ?? []).map((b) => b.from.id));
    const out = [];
    for (const n of index.notes) {
        if (n.id === note.id || linked.has(n.id))
            continue;
        const text = textOf(n);
        const masked = maskCode(text).replace(WIKILINK_RE, (m) => " ".repeat(m.length));
        const lines = masked.split("\n");
        const i = lines.findIndex((l) => re.test(l));
        if (i >= 0)
            out.push({ from: n, line: text.split("\n")[i] });
    }
    return out;
}
/** Herschrijft [[Oud]], [[Oud|x]] en [[Oud#kop]] naar de nieuwe titel. */
export function renameLinks(body, oldTitle, oldPath, newTitle) {
    const olds = new Set([norm(oldTitle), norm(oldPath)]);
    return body.replace(WIKILINK_RE, (whole, bang, inner) => {
        const { target, heading, alias } = parseLinkInner(inner);
        if (!olds.has(norm(target)))
            return whole;
        return `${bang}[[${newTitle}${heading ? `#${heading}` : ""}${alias ? `|${alias}` : ""}]]`;
    });
}
/** renameLinks voor een hele notitie, ook voor een canvas (file-kaarten en tekstkaarten). */
export function renameInDoc(doc, oldTitle, oldPath, newTitle, newPath) {
    if (!isCanvas(doc))
        return { ...doc, body: renameLinks(doc.body, oldTitle, oldPath, newTitle) };
    const olds = new Set([norm(oldTitle), norm(oldPath)]);
    const body = renameInCanvas(doc.body, (t) => olds.has(norm(t)), newPath, (text) => renameLinks(text, oldTitle, oldPath, newTitle));
    return { ...doc, body };
}
// ---------------------------------------------------------------- catalogus-links
/** [[app:Grafana]] linkt naar een applicatie uit de VaultX-catalogus. */
export function isAppLink(target) {
    return /^app:/i.test(target);
}
export function appLinkName(target) {
    return target.replace(/^app:/i, "").trim();
}
/**
 * Zoekt in titels en tekst. Ondersteunt "exacte zinnen", tag:x of #x, map:x / path:x en -uitsluiten.
 * Alle termen moeten voorkomen.
 */
export function search(index, query) {
    const tokens = query.match(/-?"[^"]+"|\S+/g) ?? [];
    if (!tokens.length)
        return [];
    const filters = [];
    const terms = [];
    for (const raw of tokens) {
        const neg = raw.startsWith("-") && raw.length > 1;
        const tok = (neg ? raw.slice(1) : raw).replace(/^"|"$/g, "");
        let f;
        const tag = /^(?:tag:)?#(.+)$/i.exec(tok) ?? /^tag:(.+)$/i.exec(tok);
        const path = /^(?:map|path|folder):(.+)$/i.exec(tok);
        if (tag) {
            const t = norm(tag[1]);
            f = (n) => (index.tagsOf.get(n.id) ?? []).some((x) => norm(x) === t || norm(x).startsWith(`${t}/`));
        }
        else if (path) {
            const p = norm(path[1]);
            f = (n) => norm(n.folder).includes(p);
        }
        else {
            const t = norm(tok);
            if (!neg)
                terms.push(t);
            f = (n) => norm(n.title).includes(t) || textOf(n).toLowerCase().includes(t);
        }
        filters.push(neg ? (n) => !f(n) : f);
    }
    const hits = index.notes.filter((n) => filters.every((f) => f(n)));
    const score = (n) => (terms.some((t) => norm(n.title).includes(t)) ? 0 : 1);
    return hits
        .sort((a, b) => score(a) - score(b) || b.updated_at.localeCompare(a.updated_at))
        .map((note) => ({ note, snippet: snippetFor(textOf(note), terms) }));
}
function snippetFor(body, terms) {
    const lower = body.toLowerCase();
    for (const t of terms) {
        const i = lower.indexOf(t);
        if (i >= 0) {
            const start = Math.max(0, i - 40);
            return `${start > 0 ? "…" : ""}${body.slice(start, i + t.length + 60).replace(/\s+/g, " ")}…`;
        }
    }
    return null;
}
/** Eenvoudige fuzzy match voor de snelkiezer: letters in volgorde, hogere score is beter. */
export function fuzzyScore(text, query) {
    const t = norm(text);
    const q = norm(query);
    if (!q)
        return 1;
    if (t === q)
        return 1000;
    if (t.startsWith(q))
        return 500 - t.length;
    if (t.includes(q))
        return 300 - t.indexOf(q);
    let ti = 0;
    let score = 0;
    for (const ch of q) {
        const found = t.indexOf(ch, ti);
        if (found < 0)
            return -1;
        score += found === ti ? 3 : 1;
        ti = found + 1;
    }
    return score;
}
// ---------------------------------------------------------------- taken
const TASK_RE = /^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])/;
/** Regelnummers van alle taken (- [ ] ...) buiten code, in volgorde. */
export function taskLines(body) {
    const out = [];
    maskCode(body)
        .split("\n")
        .forEach((l, i) => {
        if (TASK_RE.test(l))
            out.push(i);
    });
    return out;
}
export function toggleTask(body, taskIndex, offsetLines = 0) {
    const line = taskLines(body).filter((l) => l >= offsetLines)[taskIndex];
    if (line === undefined)
        return body;
    const lines = body.split("\n");
    lines[line] = lines[line].replace(TASK_RE, (_, a, mark, c) => `${a}${mark === " " ? "x" : " "}${c}`);
    return lines.join("\n");
}
// ---------------------------------------------------------------- sjablonen en datums
export function isoDate(d = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function applyTemplate(template, title, now = new Date()) {
    const time = now.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
    return template
        .replace(/\{\{\s*title\s*\}\}/gi, title)
        .replace(/\{\{\s*date\s*\}\}/gi, isoDate(now))
        .replace(/\{\{\s*time\s*\}\}/gi, time);
}
export function uniqueTitle(index, wanted, folder) {
    const taken = new Set(index.notes.filter((n) => n.folder === folder).map((n) => norm(n.title)));
    if (!taken.has(norm(wanted)))
        return wanted;
    for (let i = 1;; i++)
        if (!taken.has(norm(`${wanted} ${i}`)))
            return `${wanted} ${i}`;
}
export function wordCount(body) {
    return (splitFrontmatter(body).content.match(/[\p{L}\p{N}]+/gu) ?? []).length;
}
