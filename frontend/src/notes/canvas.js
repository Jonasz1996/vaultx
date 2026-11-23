// Canvas: een oneindig bord met kaarten en pijlen, in het JSON Canvas-formaat van Obsidian
// (jsoncanvas.org). Een canvas is een notitie met kind "canvas" en deze JSON als tekst, dus
// even versleuteld, met versies en prullenbak, en uitwisselbaar met .canvas-bestanden.
export const SIDES = ["top", "right", "bottom", "left"];
const num = (v, d) => (typeof v === "number" && Number.isFinite(v) ? v : d);
const str = (v) => (typeof v === "string" ? v : undefined);
const side = (v) => (SIDES.includes(v) ? v : undefined);
/** Leest een canvas; onbekende of kapotte stukken worden overgeslagen, nooit een fout. */
export function parseCanvas(body) {
    let raw = {};
    try {
        raw = body.trim() ? JSON.parse(body) : {};
    }
    catch {
        return { nodes: [], edges: [] };
    }
    const nodes = [];
    for (const n of Array.isArray(raw.nodes) ? raw.nodes : []) {
        if (!n || typeof n !== "object")
            continue;
        const o = n;
        const type = o.type;
        if (type !== "text" && type !== "file" && type !== "link" && type !== "group")
            continue;
        if (typeof o.id !== "string")
            continue;
        nodes.push({
            id: o.id,
            type,
            x: num(o.x, 0),
            y: num(o.y, 0),
            width: Math.max(40, num(o.width, 250)),
            height: Math.max(30, num(o.height, 60)),
            color: str(o.color),
            text: str(o.text),
            file: str(o.file),
            subpath: str(o.subpath),
            url: str(o.url),
            label: str(o.label),
        });
    }
    const ids = new Set(nodes.map((n) => n.id));
    const edges = [];
    for (const e of Array.isArray(raw.edges) ? raw.edges : []) {
        if (!e || typeof e !== "object")
            continue;
        const o = e;
        if (typeof o.id !== "string" || !ids.has(o.fromNode) || !ids.has(o.toNode))
            continue;
        edges.push({
            id: o.id,
            fromNode: o.fromNode,
            toNode: o.toNode,
            fromSide: side(o.fromSide),
            toSide: side(o.toSide),
            fromEnd: o.fromEnd === "arrow" ? "arrow" : undefined,
            toEnd: o.toEnd === "none" ? "none" : undefined,
            color: str(o.color),
            label: str(o.label),
        });
    }
    return { nodes, edges };
}
/** JSON zoals Obsidian het schrijft (tabs), zonder lege velden. */
export function serializeCanvas(data) {
    const clean = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined && v !== ""));
    return JSON.stringify({ nodes: data.nodes.map(clean), edges: data.edges.map(clean) }, null, "\t");
}
export const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), (b) => b.toString(16).padStart(2, "0")).join("");
/** Notitiepad uit een file-kaart: "Map/Titel.md" -> "Map/Titel". */
export const fileTarget = (file) => file.replace(/\.md$/i, "");
/**
 * De tekst van een canvas zoals de rest van de notities hem ziet: tekstkaarten plus een
 * [[link]] per kaart met een notitie of bijlage. Zo tellen links, backlinks, tags en
 * zoeken ook voor canvassen.
 */
export function canvasText(body) {
    const { nodes } = parseCanvas(body);
    const out = [];
    for (const n of nodes) {
        if (n.type === "text" && n.text)
            out.push(n.text);
        else if (n.type === "file" && n.file)
            out.push(`[[${fileTarget(n.file)}${n.subpath ?? ""}]]`);
        else if (n.type === "group" && n.label)
            out.push(n.label);
        else if (n.type === "link" && n.url)
            out.push(n.url);
    }
    return out.join("\n\n");
}
/** Past file-kaarten en [[links]] in tekstkaarten aan na het hernoemen van een notitie. */
export function renameInCanvas(body, matches, newPath, renameText) {
    const data = parseCanvas(body);
    let changed = false;
    for (const n of data.nodes) {
        if (n.type === "file" && n.file && matches(fileTarget(n.file))) {
            n.file = `${newPath}.md`;
            changed = true;
        }
        else if (n.type === "text" && n.text) {
            const t = renameText(n.text);
            if (t !== n.text) {
                n.text = t;
                changed = true;
            }
        }
    }
    return changed ? serializeCanvas(data) : body;
}
/** Punt op de rand van een kaart waar een pijl vertrekt of aankomt. */
export function sidePoint(n, s) {
    switch (s) {
        case "top":
            return { x: n.x + n.width / 2, y: n.y };
        case "bottom":
            return { x: n.x + n.width / 2, y: n.y + n.height };
        case "left":
            return { x: n.x, y: n.y + n.height / 2 };
        default:
            return { x: n.x + n.width, y: n.y + n.height / 2 };
    }
}
/** Kant van `n` die het dichtst bij een punt ligt. */
export function nearestSide(n, p) {
    let best = "top";
    let dist = Infinity;
    for (const s of SIDES) {
        const q = sidePoint(n, s);
        const d = (q.x - p.x) ** 2 + (q.y - p.y) ** 2;
        if (d < dist)
            [best, dist] = [s, d];
    }
    return best;
}
const NORMAL = { top: [0, -1], right: [1, 0], bottom: [0, 1], left: [-1, 0] };
/** Gebogen pad (SVG) tussen twee kaarten, plus het midden voor het label. */
export function edgePath(from, to, e) {
    const fs = e.fromSide ?? nearestSide(from, { x: to.x + to.width / 2, y: to.y + to.height / 2 });
    const ts = e.toSide ?? nearestSide(to, { x: from.x + from.width / 2, y: from.y + from.height / 2 });
    const a = sidePoint(from, fs);
    const b = sidePoint(to, ts);
    return curve(a, fs, b, ts);
}
export function curve(a, fs, b, ts) {
    const k = Math.max(40, Math.hypot(b.x - a.x, b.y - a.y) / 3);
    const c1 = { x: a.x + NORMAL[fs][0] * k, y: a.y + NORMAL[fs][1] * k };
    const c2 = ts ? { x: b.x + NORMAL[ts][0] * k, y: b.y + NORMAL[ts][1] * k } : b;
    const d = `M ${a.x} ${a.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${b.x} ${b.y}`;
    // Midden van de Bézier-curve (t = 0,5)
    const mid = { x: (a.x + 3 * c1.x + 3 * c2.x + b.x) / 8, y: (a.y + 3 * c1.y + 3 * c2.y + b.y) / 8 };
    return { d, mid };
}
export function bounds(nodes) {
    if (!nodes.length)
        return { x: 0, y: 0, width: 0, height: 0 };
    const x = Math.min(...nodes.map((n) => n.x));
    const y = Math.min(...nodes.map((n) => n.y));
    const r = Math.max(...nodes.map((n) => n.x + n.width));
    const b = Math.max(...nodes.map((n) => n.y + n.height));
    return { x, y, width: r - x, height: b - y };
}
