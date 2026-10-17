// Markdown naar HTML met de Obsidian-uitbreidingen: [[wikilinks]], ![[embeds]], #tags,
// ==markeren==, %%commentaar%%, > [!callouts], taken, eigenschappen, voetnoten, $wiskunde$,
// Mermaid en code-kleuren. Daarbovenop een eigen ||geheim|| dat wazig blijft tot je erop
// klikt. Alles gaat door DOMPurify.

import DOMPurify from "dompurify";
import katex from "katex";
import { Marked, type Tokens } from "marked";
import markedFootnote from "marked-footnote";
import type { DiagramResolver } from "./diagrams";
import { highlight } from "./highlight";
import {
  appLinkName,
  isAppLink,
  parseLinkInner,
  sectionOf,
  slug,
  splitFrontmatter,
  type NoteIndex,
  type Properties,
} from "./model";
import { formatSize, isImage, looksLikeFile, type FileResolver } from "./files";

export interface AppRef {
  name: string;
  href: string;
  url: string | null;
}

export interface RenderContext {
  index: NoteIndex;
  apps: AppRef[];
  /** Bijlagen; zonder resolver worden ze als ontbrekend getoond (bv. in de versiegeschiedenis). */
  files?: FileResolver;
  /** Mermaid-diagrammen; zonder resolver wordt de broncode getoond. */
  diagrams?: DiagramResolver;
  /** Diepte van ingesloten notities; stopt bij 2 (en bij lussen). */
  depth?: number;
  trail?: string[];
}

const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const CALLOUTS: Record<string, string> = {
  note: "Notitie",
  info: "Info",
  tip: "Tip",
  hint: "Tip",
  important: "Belangrijk",
  success: "Gelukt",
  check: "Gelukt",
  done: "Klaar",
  question: "Vraag",
  help: "Vraag",
  faq: "Vraag",
  warning: "Let op",
  caution: "Let op",
  attention: "Let op",
  failure: "Mislukt",
  fail: "Mislukt",
  missing: "Ontbreekt",
  danger: "Gevaar",
  error: "Fout",
  bug: "Bug",
  example: "Voorbeeld",
  quote: "Citaat",
  cite: "Citaat",
  abstract: "Samenvatting",
  summary: "Samenvatting",
  tldr: "Samenvatting",
  todo: "Te doen",
};

function calloutTone(type: string): string {
  if (["warning", "caution", "attention", "question", "help", "faq"].includes(type)) return "warn";
  if (["failure", "fail", "missing", "danger", "error", "bug"].includes(type)) return "bad";
  if (["success", "check", "done"].includes(type)) return "good";
  if (["example", "quote", "cite", "abstract", "summary", "tldr"].includes(type)) return "neutral";
  return "info";
}

/** Breedte uit Obsidian-syntax: ![[foto.png|300]] of ![[foto.png|300x200]]. */
function widthAttr(alias: string | null | undefined): string {
  const m = /^(\d{1,4})(?:x(\d{1,4}))?$/.exec(alias?.trim() ?? "");
  return m ? ` width="${m[1]}"${m[2] ? ` height="${m[2]}"` : ""}` : "";
}

function fileHtml(target: string, ctx: RenderContext, embed: boolean, alias?: string | null): string | null {
  const f = ctx.files?.find(target);
  if (!f) {
    if (!looksLikeFile(target)) return null;
    return `<span class="embed-missing">Bijlage ${esc(target)} niet gevonden.</span>`;
  }
  if (embed && isImage(f.name)) {
    const url = ctx.files!.dataUrl(f.id);
    if (url) return `<img class="note-img" src="${esc(url)}" alt="${esc(alias && !widthAttr(alias) ? alias : f.name)}"${widthAttr(alias)}>`;
    if (ctx.files!.broken(f.id)) return `<span class="embed-missing">Afbeelding ${esc(f.name)} kon niet ontsleuteld worden.</span>`;
    ctx.files!.request(f.id);
    return `<span class="file-loading">Afbeelding ${esc(f.name)} ontsleutelen…</span>`;
  }
  const label = alias && !widthAttr(alias) ? alias : f.name;
  return `<a class="file-link" data-file="${esc(f.id)}" title="Ontsleutelen en downloaden (${esc(formatSize(f.size))})">📎 ${esc(label)}</a>`;
}

function linkHtml(inner: string, ctx: RenderContext): string {
  const { target, heading, alias } = parseLinkInner(inner);
  const label = esc(alias ?? (heading && target ? `${target} › ${heading}` : heading ?? target));
  if (isAppLink(target)) {
    const name = appLinkName(target);
    const app = ctx.apps.find((a) => a.name.toLowerCase() === name.toLowerCase());
    if (!app) return `<span class="wikilink unresolved" title="Niet in de catalogus">${esc(alias ?? name)}</span>`;
    return `<a class="wikilink app-link" href="${esc(app.href)}" data-app="1">${esc(alias ?? app.name)}</a>`;
  }
  if (target && !ctx.index.resolve(target)) {
    const file = fileHtml(target, ctx, false, alias);
    if (file) return file;
  }
  if (!target && heading) return `<a class="wikilink" data-heading="${esc(slug(heading))}">${label}</a>`;
  const note = ctx.index.resolve(target);
  if (!note) return `<a class="wikilink unresolved" data-new="${esc(target)}" title="Bestaat nog niet: klik om aan te maken">${label}</a>`;
  return `<a class="wikilink" data-note="${esc(note.id)}"${heading ? ` data-heading="${esc(slug(heading))}"` : ""}>${label}</a>`;
}

function embedHtml(inner: string, ctx: RenderContext): string {
  const { target, heading, alias } = parseLinkInner(inner);
  const note = ctx.index.resolve(target);
  if (!note) {
    const file = fileHtml(target, ctx, true, alias);
    if (file) return file;
  }
  if (!note) return linkHtml(inner, ctx);
  const trail = ctx.trail ?? [];
  if ((ctx.depth ?? 0) >= 2 || trail.includes(note.id)) return linkHtml(inner, ctx);
  const src = heading ? sectionOf(note.body, heading) : note.body;
  const body = renderBody(src ?? `*Kop "${heading}" niet gevonden.*`, {
    ...ctx,
    depth: (ctx.depth ?? 0) + 1,
    trail: [...trail, note.id],
  });
  // Taken in een ingesloten notitie vink je af in die notitie zelf.
  const readOnly = body.replace(/data-task="\d+"/g, "disabled");
  return `<div class="embed"><div class="embed-title"><a class="wikilink" data-note="${esc(note.id)}">${esc(note.title)}${heading ? ` › ${esc(heading)}` : ""}</a></div>${readOnly}</div>`;
}

/** Wiskunde als MathML: de browser zet het zelf op, zonder stylesheet of inline stijlen. */
function mathHtml(tex: string, display: boolean): string {
  return katex.renderToString(tex, { output: "mathml", displayMode: display, throwOnError: false, strict: "ignore", trust: false });
}

function codeHtml(text: string, lang: string | undefined, ctx: RenderContext): string {
  const l = (lang ?? "").trim().split(/\s+/)[0] ?? "";
  if (l.toLowerCase() === "mermaid" && ctx.diagrams) {
    const d = ctx.diagrams.get(text);
    if (!d) {
      ctx.diagrams.request(text);
      return `<div class="diagram diagram-loading">Diagram tekenen…</div>`;
    }
    if ("url" in d) return `<div class="diagram"><img src="${esc(d.url)}" alt="Mermaid-diagram"></div>`;
    return `<div class="diagram diagram-error"><div class="small">Mermaid: ${esc(d.error)}</div><pre><code>${esc(text)}</code></pre></div>`;
  }
  if (l.toLowerCase() === "math" || l.toLowerCase() === "latex") return `<div class="math-block">${mathHtml(text, true)}</div>`;
  const colored = highlight(text, l);
  const label = l ? `<span class="code-lang">${esc(l)}</span>` : "";
  return `<div class="code-block">${label}<button type="button" class="code-copy" title="Kopiëren">Kopiëren</button><pre><code class="hljs${l ? ` language-${esc(l)}` : ""}">${colored ?? esc(text)}</code></pre></div>\n`;
}

function makeMarked(ctx: RenderContext): Marked {
  let task = 0;
  const m = new Marked({ gfm: true, breaks: false });
  m.use(markedFootnote({ description: "Voetnoten", prefixId: "voetnoot-" }));
  m.use({
    extensions: [
      {
        name: "mathBlock",
        level: "block",
        start: (src: string) => src.match(/^\$\$/m)?.index,
        tokenizer(src: string) {
          const r = /^\$\$([\s\S]+?)\$\$[ \t]*(?:\n|$)/.exec(src);
          if (r) return { type: "mathBlock", raw: r[0], text: r[1].trim() };
          return undefined;
        },
        renderer: (t) => `<div class="math-block">${mathHtml((t as unknown as { text: string }).text, true)}</div>\n`,
      },
      {
        name: "math",
        level: "inline",
        start: (src: string) => src.indexOf("$"),
        tokenizer(src: string) {
          const r = /^\$\$([^$]+?)\$\$/.exec(src) ?? /^\$(?![\s$])([^$\n]*?[^\s\\$])\$(?!\d)/.exec(src);
          if (r) return { type: "math", raw: r[0], text: r[1], display: r[0].startsWith("$$") };
          return undefined;
        },
        renderer: (t) => {
          const { text, display } = t as unknown as { text: string; display: boolean };
          return mathHtml(text, display);
        },
      },
      {
        name: "embed",
        level: "inline",
        start: (src: string) => src.indexOf("![["),
        tokenizer(src: string) {
          const r = /^!\[\[([^\][\n]+?)\]\]/.exec(src);
          if (r) return { type: "embed", raw: r[0], inner: r[1] };
          return undefined;
        },
        renderer: (t) => embedHtml((t as unknown as { inner: string }).inner, ctx),
      },
      {
        name: "wikilink",
        level: "inline",
        start: (src: string) => src.indexOf("[["),
        tokenizer(src: string) {
          const r = /^\[\[([^\][\n]+?)\]\]/.exec(src);
          if (r) return { type: "wikilink", raw: r[0], inner: r[1] };
          return undefined;
        },
        renderer: (t) => linkHtml((t as unknown as { inner: string }).inner, ctx),
      },
      {
        name: "tag",
        level: "inline",
        start(src: string) {
          const r = /(^|[\s(,;])#[\p{L}\p{N}_\-/]*[\p{L}_\-/]/u.exec(src);
          return r ? r.index + r[1].length : undefined;
        },
        tokenizer(src: string) {
          const r = /^#([\p{L}\p{N}_\-/]*[\p{L}_\-/][\p{L}\p{N}_\-/]*)/u.exec(src);
          if (r) return { type: "tag", raw: r[0], tag: r[1].replace(/\/+$/, "") };
          return undefined;
        },
        renderer: (t) => {
          const tag = (t as unknown as { tag: string }).tag;
          return `<a class="tag" data-tag="${esc(tag)}">#${esc(tag)}</a>`;
        },
      },
      {
        name: "highlight",
        level: "inline",
        start: (src: string) => src.indexOf("=="),
        tokenizer(this: { lexer: { inlineTokens: (s: string) => Tokens.Generic[] } }, src: string) {
          const r = /^==(?=\S)([\s\S]*?\S)==/.exec(src);
          if (r) return { type: "highlight", raw: r[0], tokens: this.lexer.inlineTokens(r[1]) };
          return undefined;
        },
        renderer(this: { parser: { parseInline: (t: Tokens.Generic[]) => string } }, t) {
          return `<mark>${this.parser.parseInline((t as Tokens.Generic).tokens ?? [])}</mark>`;
        },
      },
      {
        name: "secret",
        level: "inline",
        start: (src: string) => src.indexOf("||"),
        tokenizer(src: string) {
          const r = /^\|\|(?=\S)([^\n]*?\S)\|\|/.exec(src);
          if (r) return { type: "secret", raw: r[0], text: r[1] };
          return undefined;
        },
        renderer: (t) =>
          `<span class="secret" tabindex="0" title="Klik om te tonen">${esc((t as unknown as { text: string }).text)}</span>`,
      },
    ],
    renderer: {
      code({ text, lang }) {
        return codeHtml(text, lang, ctx);
      },
      heading({ tokens, depth, text }) {
        return `<h${depth} data-heading="${esc(slug(text))}">${this.parser.parseInline(tokens)}</h${depth}>\n`;
      },
      checkbox({ checked }) {
        return `<input type="checkbox" data-task="${task++}"${checked ? " checked" : ""}>`;
      },
      link({ href, title, tokens }) {
        const text = this.parser.parseInline(tokens);
        // "//host" is protocol-relatief en dus extern, ook al begint het met een slash.
        const external = /^(https?:)?\/\//i.test(href);
        if (!/^(https?:|mailto:|\/|#)/i.test(href)) return text;
        return `<a href="${esc(href)}"${title ? ` title="${esc(title)}"` : ""}${external ? ' target="_blank" rel="noopener noreferrer"' : ""}>${text}</a>`;
      },
      image({ href, text }) {
        // Enkel eigen bijlagen. De CSP van VaultX laadt geen externe afbeeldingen (geen lekken naar derden).
        if (!/^[a-z]+:/i.test(href)) {
          let name = href;
          try {
            name = decodeURIComponent(href);
          } catch {
            /* geen geldige URL-codering: naam zoals hij is */
          }
          const file = fileHtml(name, ctx, true, text);
          if (file) return file;
        }
        return `<span class="embed-missing">Afbeelding ${esc(text || href)} wordt niet geladen.</span>`;
      },
      blockquote({ tokens }) {
        const inner = this.parser.parse(tokens);
        const c = /^<p>\[!([A-Za-z-]+)\]([+-]?)[ \t]*([^\n<]*)(?:\n|<\/p>\n?)/.exec(inner);
        if (!c) return `<blockquote>\n${inner}</blockquote>\n`;
        const type = c[1].toLowerCase();
        // c[3] komt uit HTML die marked al ontsnapt heeft: niet nog eens esc() (anders "Q&amp;A").
        const title = c[3].trim() || esc(CALLOUTS[type] || c[1]);
        let rest = inner.slice(c[0].length);
        if (!c[0].endsWith("</p>") && !c[0].endsWith("</p>\n")) rest = `<p>${rest}`;
        rest = rest.replace(/^<p><\/p>\n?/, "");
        const tag = c[2] ? "details" : "div";
        const open = c[2] === "+" ? " open" : "";
        const head = c[2] ? `<summary class="callout-title">${title}</summary>` : `<div class="callout-title">${title}</div>`;
        return `<${tag} class="callout callout-${calloutTone(type)}"${open}>${head}<div class="callout-body">${rest}</div></${tag}>\n`;
      },
    },
  });
  return m;
}

/** Rendert notitietekst (zonder eigenschappen) naar veilige HTML. */
export function renderBody(content: string, ctx: RenderContext): string {
  const withoutComments = content.replace(/%%[\s\S]*?%%/g, "");
  const html = makeMarked(ctx).parse(withoutComments, { async: false }) as string;
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "tabindex", "encoding", "displaystyle", "mathvariant", "stretchy", "fence", "separator", "lspace", "rspace", "accent", "columnalign"],
    ADD_TAGS: ["details", "summary"],
    FORBID_TAGS: ["style", "form", "iframe", "object", "embed"],
  });
}

export function renderNote(body: string, ctx: RenderContext): { props: Properties; html: string } {
  const { props, content } = splitFrontmatter(body);
  return { props, html: renderBody(content, ctx) };
}
