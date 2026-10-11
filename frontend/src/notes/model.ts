// Notities: het formaat in de kluis en alles wat de browser eruit afleidt (links, backlinks,
// tags, eigenschappen, zoeken). De server ziet hier niets van: hij bewaart enkel de
// versleutelde JSON van NoteDoc.

export interface NoteDoc {
  v: 1;
  title: string;
  /** Map als pad, bv. "Werk/Runbooks"; leeg = hoofdmap. */
  folder: string;
  body: string;
  /** Bladwijzer (zoals "Bookmarks" in Obsidian). */
  pinned: boolean;
}

export interface Note extends NoteDoc {
  id: string;
  revision: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export const TEMPLATE_FOLDER = "Sjablonen";
export const DAILY_FOLDER = "Dagboek";

export function emptyDoc(title: string, folder = "", body = ""): NoteDoc {
  return { v: 1, title, folder: cleanFolder(folder), body, pinned: false };
}

export function parseDoc(json: string): NoteDoc {
  const d = JSON.parse(json) as Partial<NoteDoc>;
  return {
    v: 1,
    title: typeof d.title === "string" ? d.title : "Naamloos",
    folder: typeof d.folder === "string" ? cleanFolder(d.folder) : "",
    body: typeof d.body === "string" ? d.body : "",
    pinned: d.pinned === true,
  };
}

export function docOf(n: NoteDoc): NoteDoc {
  return { v: 1, title: n.title, folder: n.folder, body: n.body, pinned: n.pinned };
}

export function cleanFolder(folder: string): string {
  return folder
    .split("/")
    .map((p) => p.trim())
    .filter(Boolean)
    .join("/");
}

/** Tekens die in een titel de links of bestandsnamen zouden breken. */
export function cleanTitle(title: string): string {
  return title.replace(/[[\]|#^\\/:]/g, " ").replace(/\s+/g, " ").trim();
}

export function notePath(n: Pick<NoteDoc, "title" | "folder">): string {
  return n.folder ? `${n.folder}/${n.title}` : n.title;
}

const norm = (s: string) => s.trim().toLowerCase();

// ---------------------------------------------------------------- tekst zonder code

/**
 * Vervangt code (``` blokken en `inline`) en %%commentaar%% door spaties, zodat links en tags
 * daarin niet meetellen, terwijl posities en regelnummers gelijk blijven.
 */
export function maskCode(body: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, " ");
  return body
    .replace(/^(```|~~~)[^\n]*\n[\s\S]*?(?:^\1[^\n]*$|(?![\s\S]))/gm, blank)
    .replace(/`[^`\n]+`/g, blank)
    .replace(/%%[\s\S]*?%%/g, blank);
}

// ---------------------------------------------------------------- eigenschappen (frontmatter)

export type Properties = Record<string, string | string[]>;

export function splitFrontmatter(body: string): { props: Properties; content: string; offset: number } {
  const m = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/.exec(body);
  if (!m) return { props: {}, content: body, offset: 0 };
  return { props: parseYamlLite(m[1]), content: body.slice(m[0].length), offset: m[0].length };
}

/** Genoeg YAML voor Obsidian-eigenschappen: `sleutel: waarde`, `[a, b]` en lijsten met `- `. */
function parseYamlLite(src: string): Properties {
  const props: Properties = {};
  let listKey: string | null = null;
  const unquote = (s: string) => s.trim().replace(/^(["'])(.*)\1$/, "$2");
  for (const line of src.split(/\r?\n/)) {
    const item = /^\s+-\s+(.*)$/.exec(line) ?? /^-\s+(.*)$/.exec(line);
    if (item && listKey) {
      const arr = props[listKey];
      props[listKey] = [...(Array.isArray(arr) ? arr : []), unquote(item[1])];
      continue;
    }
    const kv = /^([A-Za-z0-9_\- ]+):\s*(.*)$/.exec(line);
    if (!kv) continue;
    const key = kv[1].trim();
    const value = kv[2].trim();
    listKey = null;
    if (!value) {
      props[key] = [];
      listKey = key;
    } else if (value.startsWith("[") && value.endsWith("]")) {
      props[key] = value
        .slice(1, -1)
        .split(",")
        .map(unquote)
        .filter(Boolean);
    } else props[key] = unquote(value);
  }
  return props;
}

function propList(props: Properties, ...keys: string[]): string[] {
  for (const k of keys) {
    const v = props[k];
    if (Array.isArray(v)) return v;
    if (typeof v === "string" && v) return v.split(",").map((s) => s.trim()).filter(Boolean);
  }
  return [];
}

// ---------------------------------------------------------------- links en tags

export interface WikiLink {
  embed: boolean;
  target: string;
  heading: string | null;
  alias: string | null;
  /** Begin in de tekst en regelnummer (0-gebaseerd), voor context bij backlinks. */
  index: number;
  line: number;
}

export const WIKILINK_RE = /(!?)\[\[([^\][\n]+?)\]\]/g;

/** Lijkt dit doel van een link op een bijlage (foto.png, plan.pdf) en niet op een notitie? */
export const looksLikeFile = (target: string) => /\.[a-z][a-z0-9]{0,4}$/i.test(target) && !/\.(md|markdown)$/i.test(target);

export function parseLinkInner(inner: string): Pick<WikiLink, "target" | "heading" | "alias"> {
  const [left, alias] = inner.split("|", 2);
  const hash = left.indexOf("#");
  const target = (hash >= 0 ? left.slice(0, hash) : left).trim();
  const heading = hash >= 0 ? left.slice(hash + 1).trim() || null : null;
  return { target, heading, alias: alias?.trim() || null };
}

export function extractLinks(body: string): WikiLink[] {
  const masked = maskCode(body);
  const out: WikiLink[] = [];
  for (const m of masked.matchAll(WIKILINK_RE)) {
    const index = m.index ?? 0;
    const line = masked.slice(0, index).split("\n").length - 1;
    out.push({ embed: m[1] === "!", ...parseLinkInner(m[2]), index, line });
  }
  return out;
}

// Zoals Obsidian: letters, cijfers, _ - en / (geneste tags), maar niet enkel cijfers.
export const TAG_RE = /(^|[\s(,;])#([\p{L}\p{N}_\-/]*[\p{L}_\-/][\p{L}\p{N}_\-/]*)/gu;

export function extractTags(body: string): string[] {
  const { props, content } = splitFrontmatter(body);
  const tags = new Set(propList(props, "tags", "tag").map((t) => t.replace(/^#/, "")));
  for (const m of maskCode(content).matchAll(TAG_RE)) tags.add(m[2].replace(/\/+$/, ""));
  return [...tags].filter(Boolean);
}

export function aliasesOf(n: NoteDoc): string[] {
  return propList(splitFrontmatter(n.body).props, "aliases", "alias");
}

export interface Heading {
  level: number;
  text: string;
  line: number;
}

export function extractHeadings(body: string): Heading[] {
  const out: Heading[] = [];
  maskCode(body)
    .split("\n")
    .forEach((l, i) => {
      const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(l);
      if (m) out.push({ level: m[1].length, text: m[2], line: i });
    });
  return out;
}

export function slug(text: string): string {
  return norm(text)
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "");
}

/** Het stuk van een notitie onder een kop, tot de volgende kop van hetzelfde of hoger niveau. */
export function sectionOf(body: string, heading: string): string | null {
  const lines = body.split("\n");
  const hs = extractHeadings(body);
  const start = hs.findIndex((h) => slug(h.text) === slug(heading));
  if (start < 0) return null;
  const end = hs.slice(start + 1).find((h) => h.level <= hs[start].level);
  return lines.slice(hs[start].line, end ? end.line : lines.length).join("\n");
}

// ---------------------------------------------------------------- index over alle notities

export interface Backlink {
  from: Note;
  line: string;
}

export interface NoteIndex {
  notes: Note[];
  byId: Map<string, Note>;
  resolve: (target: string) => Note | undefined;
  outgoing: Map<string, WikiLink[]>;
  backlinks: Map<string, Backlink[]>;
  tags: Map<string, Note[]>;
  tagsOf: Map<string, string[]>;
  /** Links naar notities die (nog) niet bestaan: titel -> notities die ernaar linken. */
  unresolved: Map<string, Note[]>;
  folders: string[];
}

export function buildIndex(all: Note[]): NoteIndex {
  const notes = all
    .filter((n) => !n.deleted_at)
    .sort((a, b) => a.title.localeCompare(b.title, "nl", { sensitivity: "base" }));
  const byId = new Map(notes.map((n) => [n.id, n]));
  const byKey = new Map<string, Note>();
  // Volgorde: volledig pad, dan titel, dan alias. De eerste treffer wint, zoals Obsidian.
  for (const n of notes) if (!byKey.has(norm(notePath(n)))) byKey.set(norm(notePath(n)), n);
  for (const n of notes) if (!byKey.has(norm(n.title))) byKey.set(norm(n.title), n);
  for (const n of notes) for (const a of aliasesOf(n)) if (!byKey.has(norm(a))) byKey.set(norm(a), n);
  const resolve = (target: string) => byKey.get(norm(target.replace(/\.md$/i, "")));

  const outgoing = new Map<string, WikiLink[]>();
  const backlinks = new Map<string, Backlink[]>();
  const unresolved = new Map<string, Note[]>();
  const tags = new Map<string, Note[]>();
  const tagsOf = new Map<string, string[]>();
  const folders = new Set<string>();

  for (const n of notes) {
    if (n.folder) {
      const parts = n.folder.split("/");
      parts.forEach((_, i) => folders.add(parts.slice(0, i + 1).join("/")));
    }
    const links = extractLinks(n.body);
    outgoing.set(n.id, links);
    const lines = n.body.split("\n");
    const seen = new Set<string>();
    for (const l of links) {
      if (!l.target || isAppLink(l.target)) continue;
      const to = resolve(l.target);
      if (!to) {
        if (looksLikeFile(l.target)) continue;
        const key = l.target;
        if (!unresolved.has(key)) unresolved.set(key, []);
        if (!unresolved.get(key)!.includes(n)) unresolved.get(key)!.push(n);
        continue;
      }
      if (to.id === n.id || seen.has(`${to.id}:${l.line}`)) continue;
      seen.add(`${to.id}:${l.line}`);
      if (!backlinks.has(to.id)) backlinks.set(to.id, []);
      backlinks.get(to.id)!.push({ from: n, line: lines[l.line] ?? "" });
    }
    const t = extractTags(n.body);
    tagsOf.set(n.id, t);
    for (const tag of t) {
      // #a/b telt ook mee voor #a
      const parts = tag.split("/");
      parts.forEach((_, i) => {
        const key = parts.slice(0, i + 1).join("/");
        if (!tags.has(key)) tags.set(key, []);
        if (!tags.get(key)!.includes(n)) tags.get(key)!.push(n);
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
export function unlinkedMentions(index: NoteIndex, note: Note): Backlink[] {
  const names = [note.title, ...aliasesOf(note)].filter((s) => s.length >= 3);
  if (!names.length) return [];
  const escaped = names.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`(^|[^\\p{L}\\p{N}\\[])(${escaped})(?![\\p{L}\\p{N}])`, "iu");
  const linked = new Set((index.backlinks.get(note.id) ?? []).map((b) => b.from.id));
  const out: Backlink[] = [];
  for (const n of index.notes) {
    if (n.id === note.id || linked.has(n.id)) continue;
    const masked = maskCode(n.body).replace(WIKILINK_RE, (m) => " ".repeat(m.length));
    const lines = masked.split("\n");
    const i = lines.findIndex((l) => re.test(l));
    if (i >= 0) out.push({ from: n, line: n.body.split("\n")[i] });
  }
  return out;
}

/** Herschrijft [[Oud]], [[Oud|x]] en [[Oud#kop]] naar de nieuwe titel. */
export function renameLinks(body: string, oldTitle: string, oldPath: string, newTitle: string): string {
  const olds = new Set([norm(oldTitle), norm(oldPath)]);
  return body.replace(WIKILINK_RE, (whole, bang: string, inner: string) => {
    const { target, heading, alias } = parseLinkInner(inner);
    if (!olds.has(norm(target))) return whole;
    return `${bang}[[${newTitle}${heading ? `#${heading}` : ""}${alias ? `|${alias}` : ""}]]`;
  });
}

// ---------------------------------------------------------------- catalogus-links

/** [[app:Grafana]] linkt naar een applicatie uit de VaultX-catalogus. */
export function isAppLink(target: string): boolean {
  return /^app:/i.test(target);
}

export function appLinkName(target: string): string {
  return target.replace(/^app:/i, "").trim();
}

// ---------------------------------------------------------------- zoeken

export interface SearchHit {
  note: Note;
  snippet: string | null;
}

/**
 * Zoekt in titels en tekst. Ondersteunt "exacte zinnen", tag:x of #x, map:x / path:x en -uitsluiten.
 * Alle termen moeten voorkomen.
 */
export function search(index: NoteIndex, query: string): SearchHit[] {
  const tokens = query.match(/-?"[^"]+"|\S+/g) ?? [];
  if (!tokens.length) return [];
  const filters: ((n: Note) => boolean)[] = [];
  const terms: string[] = [];
  for (const raw of tokens) {
    const neg = raw.startsWith("-") && raw.length > 1;
    const tok = (neg ? raw.slice(1) : raw).replace(/^"|"$/g, "");
    let f: (n: Note) => boolean;
    const tag = /^(?:tag:)?#(.+)$/i.exec(tok) ?? /^tag:(.+)$/i.exec(tok);
    const path = /^(?:map|path|folder):(.+)$/i.exec(tok);
    if (tag) {
      const t = norm(tag[1]);
      f = (n) => (index.tagsOf.get(n.id) ?? []).some((x) => norm(x) === t || norm(x).startsWith(`${t}/`));
    } else if (path) {
      const p = norm(path[1]);
      f = (n) => norm(n.folder).includes(p);
    } else {
      const t = norm(tok);
      if (!neg) terms.push(t);
      f = (n) => norm(n.title).includes(t) || n.body.toLowerCase().includes(t);
    }
    filters.push(neg ? (n) => !f(n) : f);
  }
  const hits = index.notes.filter((n) => filters.every((f) => f(n)));
  const score = (n: Note) => (terms.some((t) => norm(n.title).includes(t)) ? 0 : 1);
  return hits
    .sort((a, b) => score(a) - score(b) || b.updated_at.localeCompare(a.updated_at))
    .map((note) => ({ note, snippet: snippetFor(note.body, terms) }));
}

function snippetFor(body: string, terms: string[]): string | null {
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
export function fuzzyScore(text: string, query: string): number {
  const t = norm(text);
  const q = norm(query);
  if (!q) return 1;
  if (t === q) return 1000;
  if (t.startsWith(q)) return 500 - t.length;
  if (t.includes(q)) return 300 - t.indexOf(q);
  let ti = 0;
  let score = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, ti);
    if (found < 0) return -1;
    score += found === ti ? 3 : 1;
    ti = found + 1;
  }
  return score;
}

// ---------------------------------------------------------------- taken

const TASK_RE = /^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])/;

/** Regelnummers van alle taken (- [ ] ...) buiten code, in volgorde. */
export function taskLines(body: string): number[] {
  const out: number[] = [];
  maskCode(body)
    .split("\n")
    .forEach((l, i) => {
      if (TASK_RE.test(l)) out.push(i);
    });
  return out;
}

export function toggleTask(body: string, taskIndex: number, offsetLines = 0): string {
  const line = taskLines(body).filter((l) => l >= offsetLines)[taskIndex];
  if (line === undefined) return body;
  const lines = body.split("\n");
  lines[line] = lines[line].replace(TASK_RE, (_, a: string, mark: string, c: string) => `${a}${mark === " " ? "x" : " "}${c}`);
  return lines.join("\n");
}

// ---------------------------------------------------------------- sjablonen en datums

export function isoDate(d = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function applyTemplate(template: string, title: string, now = new Date()): string {
  const time = now.toLocaleTimeString("nl-BE", { hour: "2-digit", minute: "2-digit" });
  return template
    .replace(/\{\{\s*title\s*\}\}/gi, title)
    .replace(/\{\{\s*date\s*\}\}/gi, isoDate(now))
    .replace(/\{\{\s*time\s*\}\}/gi, time);
}

export function uniqueTitle(index: NoteIndex, wanted: string, folder: string): string {
  const taken = new Set(index.notes.filter((n) => n.folder === folder).map((n) => norm(n.title)));
  if (!taken.has(norm(wanted))) return wanted;
  for (let i = 1; ; i++) if (!taken.has(norm(`${wanted} ${i}`))) return `${wanted} ${i}`;
}

export function wordCount(body: string): number {
  return (splitFrontmatter(body).content.match(/[\p{L}\p{N}]+/gu) ?? []).length;
}
