// Onderdelen van de notitiepagina: bestandsboom, tags, zoekresultaten, zijpaneel met
// backlinks en inhoud, snelkiezer, versiegeschiedenis en prullenbak.

import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate, relative } from "../format";
import { GraphView } from "./Graph";
import { renderNote, type RenderContext } from "./markdown";
import {
  extractHeadings,
  fuzzyScore,
  isAppLink,
  notePath,
  search,
  unlinkedMentions,
  wordCount,
  type Backlink,
  type Heading,
  type Note,
  type NoteDoc,
  type NoteIndex,
  type Properties,
} from "./model";
import type { NoteVersion } from "./store";
import { baseName, formatSize, isImage, type NoteFile } from "./files";

// ---------------------------------------------------------------- bestandsboom

interface TreeFolder {
  name: string;
  path: string;
  folders: TreeFolder[];
  notes: Note[];
}

function buildTree(index: NoteIndex): TreeFolder {
  const root: TreeFolder = { name: "", path: "", folders: [], notes: [] };
  const byPath = new Map<string, TreeFolder>([["", root]]);
  const folderFor = (path: string): TreeFolder => {
    const existing = byPath.get(path);
    if (existing) return existing;
    const cut = path.lastIndexOf("/");
    const parent = folderFor(cut < 0 ? "" : path.slice(0, cut));
    const f: TreeFolder = { name: path.slice(cut + 1), path, folders: [], notes: [] };
    parent.folders.push(f);
    byPath.set(path, f);
    return f;
  };
  for (const p of index.folders) folderFor(p);
  for (const n of index.notes) folderFor(n.folder).notes.push(n);
  const sort = (f: TreeFolder) => {
    f.folders.sort((a, b) => a.name.localeCompare(b.name, "nl"));
    f.folders.forEach(sort);
  };
  sort(root);
  return root;
}

export function FileTree({
  index,
  currentId,
  onOpen,
  onMove,
}: {
  index: NoteIndex;
  currentId: string | null;
  onOpen: (id: string) => void;
  onMove: (id: string, folder: string) => void;
}) {
  const tree = useMemo(() => buildTree(index), [index]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [over, setOver] = useState<string | null>(null);
  const toggle = (p: string) =>
    setCollapsed((c) => {
      const n = new Set(c);
      if (n.has(p)) n.delete(p);
      else n.add(p);
      return n;
    });
  const dropProps = (path: string) => ({
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes("application/x-vaultx-note")) return;
      e.preventDefault();
      setOver(path);
    },
    onDragLeave: () => setOver((o) => (o === path ? null : o)),
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      setOver(null);
      const id = e.dataTransfer.getData("application/x-vaultx-note");
      if (id) onMove(id, path);
    },
  });

  const renderFolder = (f: TreeFolder, depth: number): ReactNode => (
    <>
      {f.folders.map((sub) => (
        <li key={`d:${sub.path}`}>
          <button
            className={`tree-item tree-folder${over === sub.path ? " drop" : ""}`}
            style={{ paddingLeft: 8 + depth * 14 }}
            onClick={() => toggle(sub.path)}
            {...dropProps(sub.path)}
          >
            <span className="tree-caret">{collapsed.has(sub.path) ? "▸" : "▾"}</span>
            {sub.name}
          </button>
          {!collapsed.has(sub.path) && <ul>{renderFolder(sub, depth + 1)}</ul>}
        </li>
      ))}
      {f.notes.map((n) => (
        <li key={n.id}>
          <button
            className={`tree-item${n.id === currentId ? " active" : ""}`}
            style={{ paddingLeft: 22 + depth * 14 }}
            draggable
            onDragStart={(e) => e.dataTransfer.setData("application/x-vaultx-note", n.id)}
            onClick={() => onOpen(n.id)}
            title={notePath(n)}
          >
            {n.title}
          </button>
        </li>
      ))}
    </>
  );

  return (
    <div className={`tree${over === "" ? " drop" : ""}`} {...dropProps("")}>
      <ul>{renderFolder(tree, 0)}</ul>
    </div>
  );
}

export function TagList({ index, onTag }: { index: NoteIndex; onTag: (t: string) => void }) {
  const tags = [...index.tags.entries()].sort((a, b) => a[0].localeCompare(b[0], "nl"));
  if (!tags.length) return <p className="muted small pad">Nog geen tags. Typ #tag in een notitie.</p>;
  return (
    <ul className="tag-list">
      {tags.map(([tag, notes]) => (
        <li key={tag} style={{ paddingLeft: tag.split("/").length * 10 - 10 }}>
          <button className="tree-item" onClick={() => onTag(tag)}>
            <span>#{tag.split("/").pop()}</span>
            <span className="count">{notes.length}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- bijlagen

/** Notities die naar een bijlage verwijzen (op naam of bestandsnaam, zoals Obsidian). */
export function notesUsing(index: NoteIndex, f: NoteFile): Note[] {
  const names = [f.name.toLowerCase(), baseName(f.name).toLowerCase()];
  return index.notes.filter((n) => {
    const body = n.body.toLowerCase();
    return names.some((x) => body.includes(`[[${x}`) || body.includes(`](${x}`) || body.includes(`](${encodeURI(x)}`));
  });
}

export function AttachmentList({
  files,
  usage,
  index,
  onInsert,
  onDownload,
  onDelete,
  onOpen,
}: {
  files: NoteFile[] | null;
  usage: { used: number; quota: number };
  index: NoteIndex;
  onInsert?: (f: NoteFile) => void;
  onDownload: (f: NoteFile) => void;
  onDelete: (f: NoteFile, usedIn: Note[]) => void;
  onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  if (!files) return <Loading />;
  const sorted = [...files].sort((a, b) => a.name.localeCompare(b.name, "nl"));
  return (
    <div className="attachments">
      <p className="muted small pad">
        {formatSize(usage.used)} van {formatSize(usage.quota)} gebruikt. Plak of sleep een bestand in een notitie om het toe te
        voegen.
      </p>
      {!sorted.length && <p className="muted small pad">Nog geen bijlagen.</p>}
      <ul className="tree">
        {sorted.map((f) => {
          const used = notesUsing(index, f);
          return (
            <li key={f.id} className="attachment">
              <button className="tree-item" onClick={() => setOpen(open === f.id ? null : f.id)} title={f.name}>
                <span className="attachment-name">
                  {isImage(f.name) ? "🖼" : "📎"} {f.name}
                </span>
                <span className="count" title="Gebruikt in zoveel notities">
                  {used.length}
                </span>
              </button>
              {open === f.id && (
                <div className="attachment-detail small">
                  <div className="muted">{formatSize(f.size)}</div>
                  {used.length > 0 && (
                    <ul>
                      {used.map((n) => (
                        <li key={n.id}>
                          <a className="wikilink" onClick={() => onOpen(n.id)}>
                            {n.title}
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                  <div className="row wrap">
                    {onInsert && (
                      <button className="btn btn-ghost small" onClick={() => onInsert(f)}>
                        Invoegen
                      </button>
                    )}
                    <button className="btn btn-ghost small" onClick={() => onDownload(f)}>
                      Downloaden
                    </button>
                    <button className="btn btn-ghost small btn-danger-text" onClick={() => onDelete(f, used)}>
                      Verwijderen
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function SearchResults({
  index,
  query,
  currentId,
  onOpen,
}: {
  index: NoteIndex;
  query: string;
  currentId: string | null;
  onOpen: (id: string) => void;
}) {
  const hits = useMemo(() => search(index, query), [index, query]);
  if (!hits.length) return <p className="muted small pad">Niets gevonden.</p>;
  return (
    <ul className="results">
      <li className="muted small pad">
        {hits.length} {hits.length === 1 ? "notitie" : "notities"}
      </li>
      {hits.map((h) => (
        <li key={h.note.id}>
          <button className={`result${h.note.id === currentId ? " active" : ""}`} onClick={() => onOpen(h.note.id)}>
            <span className="result-title">{h.note.title}</span>
            {h.note.folder && <span className="muted small">{h.note.folder}</span>}
            {h.snippet && <span className="result-snippet small">{h.snippet}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------- zijpaneel rechts

function Section({ title, count, children, initiallyOpen = true }: { title: string; count?: number; children: ReactNode; initiallyOpen?: boolean }) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <section className="meta-section">
      <button className="meta-head" onClick={() => setOpen(!open)}>
        <span>{open ? "▾" : "▸"} {title}</span>
        {count !== undefined && <span className="count">{count}</span>}
      </button>
      {open && <div className="meta-body">{children}</div>}
    </section>
  );
}

function LinkList({ items, onOpen, action }: { items: Backlink[]; onOpen: (id: string) => void; action?: (b: Backlink) => ReactNode }) {
  return (
    <ul className="backlinks">
      {items.map((b, i) => (
        <li key={`${b.from.id}-${i}`}>
          <button className="link-btn" onClick={() => onOpen(b.from.id)}>
            {b.from.title}
          </button>
          <div className="context small">{b.line.trim().slice(0, 160)}</div>
          {action?.(b)}
        </li>
      ))}
    </ul>
  );
}

export function PropertiesTable({ props }: { props: Properties }) {
  const entries = Object.entries(props);
  if (!entries.length) return null;
  return (
    <dl className="props">
      {entries.map(([k, v]) => (
        <div key={k} className="prop">
          <dt>{k}</dt>
          <dd>{Array.isArray(v) ? v.map((x) => <span key={x} className="chip">{x}</span>) : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function MetaPanel({
  index,
  note,
  draft,
  onOpen,
  onHeading,
  onLinkMention,
  onTag,
}: {
  index: NoteIndex;
  note: Note;
  draft: NoteDoc;
  onOpen: (id: string) => void;
  onHeading: (h: Heading) => void;
  onLinkMention: (b: Backlink) => void;
  onTag: (t: string) => void;
}) {
  const backlinks = index.backlinks.get(note.id) ?? [];
  const mentions = useMemo(() => unlinkedMentions(index, note), [index, note]);
  const headings = useMemo(() => extractHeadings(draft.body), [draft.body]);
  const outgoing = (index.outgoing.get(note.id) ?? []).filter((l) => l.target && !isAppLink(l.target));
  const tags = index.tagsOf.get(note.id) ?? [];
  const uniqueOut = [...new Map(outgoing.map((l) => [l.target.toLowerCase(), l])).values()];

  return (
    <aside className="notes-meta">
      <Section title="Lokale grafiek">
        <GraphView index={index} currentId={note.id} onOpen={onOpen} onTag={onTag} compact />
      </Section>
      <Section title="Backlinks" count={backlinks.length}>
        {backlinks.length ? <LinkList items={backlinks} onOpen={onOpen} /> : <p className="muted small">Geen notities linken hierheen.</p>}
      </Section>
      <Section title="Niet-gelinkte vermeldingen" count={mentions.length} initiallyOpen={mentions.length > 0}>
        {mentions.length ? (
          <LinkList
            items={mentions}
            onOpen={onOpen}
            action={(b) => (
              <button className="btn btn-ghost small" onClick={() => onLinkMention(b)}>
                Link maken
              </button>
            )}
          />
        ) : (
          <p className="muted small">Geen.</p>
        )}
      </Section>
      <Section title="Uitgaande links" count={uniqueOut.length}>
        {uniqueOut.length ? (
          <ul className="plain-links">
            {uniqueOut.map((l) => {
              const t = index.resolve(l.target);
              return (
                <li key={l.target}>
                  {t ? (
                    <button className="link-btn" onClick={() => onOpen(t.id)}>
                      {t.title}
                    </button>
                  ) : (
                    <span className="muted" title="Bestaat nog niet">
                      {l.target}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted small">Geen.</p>
        )}
      </Section>
      {headings.length > 0 && (
        <Section title="Inhoud">
          <ul className="outline">
            {headings.map((h, i) => (
              <li key={i} style={{ paddingLeft: (h.level - 1) * 12 }}>
                <button className="link-btn" onClick={() => onHeading(h)}>
                  {h.text}
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <Section title="Info">
        <dl className="kv kv-small">
          <dt>Woorden</dt>
          <dd>{wordCount(draft.body).toLocaleString("nl-BE")}</dd>
          <dt>Tags</dt>
          <dd>
            {tags.length
              ? tags.map((t) => (
                  <button key={t} className="tag" onClick={() => onTag(t)}>
                    #{t}
                  </button>
                ))
              : "—"}
          </dd>
          <dt>Aangemaakt</dt>
          <dd>{formatDate(note.created_at)}</dd>
          <dt>Gewijzigd</dt>
          <dd>{relative(note.updated_at)}</dd>
          <dt>Versie</dt>
          <dd>{note.revision}</dd>
        </dl>
      </Section>
    </aside>
  );
}

// ---------------------------------------------------------------- snelkiezer

export function QuickSwitcher({
  open,
  index,
  onClose,
  onOpen,
  onCreate,
}: {
  open: boolean;
  index: NoteIndex;
  onClose: () => void;
  onOpen: (id: string) => void;
  onCreate: (title: string) => void;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      setTimeout(() => input.current?.focus());
    }
  }, [open]);
  const list = useMemo(
    () =>
      index.notes
        .map((n) => ({ n, s: Math.max(fuzzyScore(n.title, q), fuzzyScore(notePath(n), q) - 1) }))
        .filter((x) => x.s >= 0)
        .sort((a, b) => b.s - a.s || b.n.updated_at.localeCompare(a.n.updated_at))
        .slice(0, 12)
        .map((x) => x.n),
    [index, q],
  );
  const exact = list.some((n) => n.title.toLowerCase() === q.trim().toLowerCase());
  const total = list.length + (q.trim() && !exact ? 1 : 0);
  const pick = (i: number) => {
    if (i < list.length) onOpen(list[i].id);
    else onCreate(q.trim());
    onClose();
  };
  return (
    <Modal title="Notitie openen" open={open} onClose={onClose}>
      <div className="picker">
        <input
          ref={input}
          value={q}
          placeholder="Typ een titel… (Enter opent, of maakt aan)"
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              if (total) setActive((a) => (a + (e.key === "ArrowDown" ? 1 : total - 1)) % total);
            } else if (e.key === "Enter" && total) {
              e.preventDefault();
              pick(active);
            }
          }}
        />
        <ul className="picker-list">
          {list.map((n, i) => (
            <li key={n.id} className={i === active ? "active" : undefined}>
              <button onClick={() => pick(i)}>
                <span>{n.title}</span>
                {n.folder && <span className="muted small">{n.folder}</span>}
              </button>
            </li>
          ))}
          {q.trim() && !exact && (
            <li className={active === list.length ? "active" : undefined}>
              <button onClick={() => pick(list.length)}>
                <span>
                  Nieuwe notitie <b>{q.trim()}</b>
                </span>
              </button>
            </li>
          )}
          {!total && <li className="muted">Nog geen notities.</li>}
        </ul>
        <p className="muted small">Sneltoets: Ctrl+O of Ctrl+K (⌘ op Mac).</p>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- versiegeschiedenis

export function HistoryModal({
  open,
  note,
  load,
  ctx,
  onClose,
  onRestore,
}: {
  open: boolean;
  note: Note;
  load: (id: string) => Promise<NoteVersion[]>;
  ctx: RenderContext;
  onClose: () => void;
  onRestore: (v: NoteVersion) => void;
}) {
  const [versions, setVersions] = useState<NoteVersion[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState(0);
  useEffect(() => {
    if (!open) return;
    setVersions(null);
    setSelected(0);
    load(note.id).then(setVersions, setError);
  }, [open, note.id, load]);
  const v = versions?.[selected];
  const html = useMemo(() => (v ? renderNote(v.body, ctx).html : ""), [v, ctx]);
  return (
    <Modal title={`Versiegeschiedenis: ${note.title}`} open={open} onClose={onClose} wide>
      <ErrorBox error={error} />
      {!versions ? (
        <Loading />
      ) : !versions.length ? (
        <Empty>Nog geen eerdere versies. VaultX bewaart een versie wanneer je een notitie na minstens 10 minuten opnieuw wijzigt.</Empty>
      ) : (
        <div className="history">
          <ul className="history-list">
            {versions.map((x, i) => (
              <li key={x.revision}>
                <button className={i === selected ? "active" : undefined} onClick={() => setSelected(i)}>
                  <span>{formatDate(x.saved_at)}</span>
                  <span className="muted small">
                    versie {x.revision} · {wordCount(x.body)} woorden{x.title !== note.title ? ` · "${x.title}"` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <div className="history-preview">
            <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
            <div className="form-actions">
              <button className="btn btn-primary" onClick={() => v && onRestore(v)}>
                Deze versie terugzetten
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- prullenbak

export function TrashView({
  notes,
  onRestore,
  onDelete,
}: {
  notes: Note[];
  onRestore: (id: string) => void;
  onDelete: (ids: string[]) => void;
}) {
  const trashed = notes.filter((n) => n.deleted_at).sort((a, b) => (b.deleted_at ?? "").localeCompare(a.deleted_at ?? ""));
  return (
    <div className="trash">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Prullenbak</h2>
        {trashed.length > 0 && (
          <button
            className="btn btn-danger"
            onClick={() => {
              if (confirm(`${trashed.length} notities definitief verwijderen? Dit kan niet ongedaan worden.`))
                onDelete(trashed.map((n) => n.id));
            }}
          >
            Prullenbak leegmaken
          </button>
        )}
      </div>
      {!trashed.length ? (
        <Empty>De prullenbak is leeg.</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Notitie</th>
              <th>Verwijderd</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {trashed.map((n) => (
              <tr key={n.id}>
                <td>
                  {n.title}
                  {n.folder && <div className="muted small">{n.folder}</div>}
                </td>
                <td>{relative(n.deleted_at!)}</td>
                <td className="right nowrap">
                  <button className="btn btn-ghost" onClick={() => onRestore(n.id)}>
                    Terugzetten
                  </button>
                  <button
                    className="btn btn-ghost btn-danger-text"
                    onClick={() => confirm(`"${n.title}" definitief verwijderen?`) && onDelete([n.id])}
                  >
                    Definitief verwijderen
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
