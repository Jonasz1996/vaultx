import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api } from "../api/client";
import type { Application, VaultStatus } from "../api/types";
import { Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { Editor, type EditorHandle, type Suggestion } from "../notes/Editor";
import { GraphView } from "../notes/Graph";
import { download, exportZip, planImport, type ImportPlan } from "../notes/importExport";
import { renderNote, type AppRef, type RenderContext } from "../notes/markdown";
import {
  DAILY_FOLDER,
  TEMPLATE_FOLDER,
  applyTemplate,
  buildIndex,
  cleanFolder,
  cleanTitle,
  docOf,
  emptyDoc,
  isoDate,
  notePath,
  renameLinks,
  slug,
  splitFrontmatter,
  toggleTask,
  uniqueTitle,
  type Backlink,
  type Heading,
  type Note,
  type NoteDoc,
} from "../notes/model";
import { FileTree, HistoryModal, MetaPanel, PropertiesTable, QuickSwitcher, SearchResults, TagList, TrashView } from "../notes/panels";
import { AUTO_LOCK_MINUTES, useVaultKey } from "../notes/session";
import { useNotesStore, type NoteVersion } from "../notes/store";
import type { UserKey } from "../vault/crypto";

type Mode = "edit" | "split" | "read";
type View = "note" | "graph" | "trash";

const MODE_KEY = "vaultx.notes.mode";

function storedMode(): Mode {
  try {
    const m = localStorage.getItem(MODE_KEY);
    if (m === "edit" || m === "split" || m === "read") return m;
  } catch {
    /* geen opslag beschikbaar */
  }
  return "split";
}

export function NotesPage() {
  const status = useQuery({ queryKey: ["vault"], queryFn: () => api.get<VaultStatus>("/api/v1/me/vault") });
  const { key } = useVaultKey();
  if (status.isLoading) return <Loading />;
  if (!status.data) return <ErrorBox error={status.error} />;
  if (!status.data.enrolled)
    return (
      <>
        <PageHeader title="Notities" subtitle="Versleutelde Markdown-notities in je persoonlijke kluis." />
        <Card>
          <p>
            Notities worden versleuteld met de sleutels van je kluis. Activeer eerst je kluis op <Link to="/vault">Mijn kluis</Link>.
          </p>
        </Card>
      </>
    );
  if (!key) return <Unlock status={status.data} />;
  return <Workspace userKey={key} />;
}

function Unlock({ status }: { status: VaultStatus }) {
  const { unlock } = useVaultKey();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await unlock(status, password);
      setPassword("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHeader title="Notities" subtitle="Versleutelde Markdown-notities in je persoonlijke kluis." />
      <Card title="Kluis ontgrendelen">
        <form className="form" style={{ maxWidth: 460 }} onSubmit={submit}>
          <p className="muted small" style={{ margin: 0 }}>
            Je notities zijn versleuteld met je kluissleutel. Je browser leidt die af uit je master password;
            VaultX ziet het wachtwoord en de inhoud van je notities nooit. Na {AUTO_LOCK_MINUTES} minuten zonder
            activiteit vergrendelt de kluis vanzelf.
          </p>
          <label className="field">
            <span>Master password voor {status.email}</span>
            <input
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <ErrorBox error={error} />
          <div className="form-actions">
            <button className="btn btn-primary" disabled={busy || !password}>
              {busy ? "Sleutel afleiden…" : "Ontgrendelen"}
            </button>
          </div>
        </form>
      </Card>
    </>
  );
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

function Workspace({ userKey }: { userKey: UserKey }) {
  const store = useNotesStore(userKey);
  const { lock } = useVaultKey();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const currentId = params.get("n");
  const [view, setView] = useState<View>("note");
  const [mode, setModeState] = useState<Mode>(storedMode);
  const [query, setQuery] = useState("");
  const [sideTab, setSideTab] = useState<"files" | "tags">("files");
  const [switcher, setSwitcher] = useState(false);
  const [history, setHistory] = useState(false);
  const [menu, setMenu] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [importPlan, setImportPlan] = useState<ImportPlan | null>(null);
  const [importing, setImporting] = useState<number | null>(null);

  const setMode = (m: Mode) => {
    setModeState(m);
    try {
      localStorage.setItem(MODE_KEY, m);
    } catch {
      /* geen opslag beschikbaar */
    }
  };

  const all = useMemo(() => (store.notes ? [...store.notes.values()] : []), [store.notes]);
  const index = useMemo(() => buildIndex(all), [all]);
  const current = currentId ? index.byId.get(currentId) ?? null : null;

  const catalog = useQuery({ queryKey: ["catalog", "notes"], queryFn: () => api.get<Application[]>("/api/v1/catalog"), staleTime: 60_000 });
  const apps: AppRef[] = useMemo(
    () => (catalog.data ?? []).map((a) => ({ name: a.name, url: a.url, href: `/catalog/${a.organization_id}/${a.id}` })),
    [catalog.data],
  );
  const ctx: RenderContext = useMemo(() => ({ index, apps }), [index, apps]);

  const flash = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 4000);
  }, []);

  const openNote = useCallback(
    (id: string, heading?: string) => {
      setView("note");
      setParams((p) => {
        const next = new URLSearchParams(p);
        next.set("n", id);
        return next;
      });
      if (heading) pendingHeading.current = heading;
    },
    [setParams],
  );
  const pendingHeading = useRef<string | null>(null);

  // ------------------------------------------------------------ concept en automatisch bewaren

  const [draft, setDraft] = useState<NoteDoc | null>(null);
  const [saveState, setSaveState] = useState<"saved" | "dirty" | "saving" | "error">("saved");
  const [conflict, setConflict] = useState<{ mine: NoteDoc; theirs: Note } | null>(null);
  const draftRef = useRef<{ id: string; doc: NoteDoc; dirty: boolean } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const titleAtFocus = useRef<{ title: string; path: string } | null>(null);
  const editor = useRef<EditorHandle>(null);

  // Vaste referentie, zodat flush (en de opruimfunctie die hem bij vergrendelen oproept)
  // niet bij elke render verandert.
  const storeRef = useRef(store);
  storeRef.current = store;

  const flush = useCallback(async () => {
    window.clearTimeout(timer.current);
    const d = draftRef.current;
    if (!d || !d.dirty) return;
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
      if (draftRef.current?.id === d.id && !draftRef.current.dirty) setSaveState("saved");
    } catch (e) {
      if (draftRef.current?.id === d.id) draftRef.current.dirty = true;
      setSaveState("error");
      setError(e);
    }
  }, []);

  const edit = useCallback(
    (patch: Partial<NoteDoc>) => {
      const d = draftRef.current;
      if (!d) return;
      d.doc = { ...d.doc, ...patch };
      d.dirty = true;
      setDraft(d.doc);
      setSaveState("dirty");
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void flush(), 800);
    },
    [flush],
  );

  // Andere notitie geopend: eerst de vorige bewaren, dan het concept laden.
  useEffect(() => {
    const prev = draftRef.current;
    if (prev && prev.id !== currentId && prev.dirty) void flush();
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
    const warn = (e: BeforeUnloadEvent) => {
      if (draftRef.current?.dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      void flush();
    };
  }, [flush]);

  // ------------------------------------------------------------ acties

  const run = useCallback(
    async (fn: () => Promise<unknown>) => {
      setError(null);
      try {
        await fn();
      } catch (e) {
        setError(e);
      }
    },
    [],
  );

  const createNote = useCallback(
    async (title: string, folder = current?.folder ?? "", body = "") => {
      const clean = cleanTitle(title) || "Naamloos";
      const n = await store.create(emptyDoc(uniqueTitle(index, clean, cleanFolder(folder)), folder, body));
      openNote(n.id);
      return n;
    },
    [current?.folder, index, openNote, store],
  );

  const newNote = () =>
    run(async () => {
      await createNote("Naamloos");
      setMode(mode === "read" ? "split" : mode);
      setTimeout(() => {
        const t = document.querySelector<HTMLInputElement>(".note-title");
        t?.focus();
        t?.select();
      }, 50);
    });

  const templates = index.notes.filter((n) => n.folder === TEMPLATE_FOLDER || n.folder.startsWith(`${TEMPLATE_FOLDER}/`));

  const daily = () =>
    run(async () => {
      const title = isoDate();
      const existing = index.resolve(`${DAILY_FOLDER}/${title}`);
      if (existing) return openNote(existing.id);
      const tpl = templates.find((t) => t.title.toLowerCase() === "dagnotitie");
      await createNote(title, DAILY_FOLDER, applyTemplate(tpl ? tpl.body : DAILY_DEFAULT, title));
    });

  const welcome = () =>
    run(async () => {
      const n = await store.create(emptyDoc("Welkom", "", WELCOME));
      await store.create(
        emptyDoc(
          "Runbook Grafana",
          "Runbooks",
          `#runbook #infra\n\nGrafana draait achter NPM en Authentik, zie [[app:Grafana]].\n\n## Herstarten\n1. Log in op de host\n2. \`systemctl restart grafana-server\`\n\n> [!warning] Let op\n> Eerst [[Welkom]] lezen.\n`,
        ),
      );
      openNote(n.id);
      setMode("read");
    });

  const trashCurrent = () =>
    current &&
    run(async () => {
      await flush();
      await store.trash(current.id);
      setParams({});
      flash(`"${current.title}" staat in de prullenbak.`);
    });

  const moveNote = (id: string, folder: string) =>
    run(async () => {
      const n = index.byId.get(id);
      if (!n || n.folder === folder) return;
      if (draftRef.current?.id === id) return edit({ folder });
      await store.update(id, (d) => ({ ...d, folder }));
    });

  const togglePin = () => draft && edit({ pinned: !draft.pinned });

  /** Na een titelwijziging de links in andere notities bijwerken (zoals Obsidian). */
  const commitTitle = () =>
    run(async () => {
      const before = titleAtFocus.current;
      titleAtFocus.current = null;
      const d = draftRef.current;
      if (!before || !d || !current) return;
      let title = cleanTitle(d.doc.title) || "Naamloos";
      if (title.toLowerCase() !== before.title.toLowerCase()) {
        const others = { ...index, notes: index.notes.filter((n) => n.id !== current.id) };
        title = uniqueTitle(others, title, d.doc.folder);
      }
      if (title !== d.doc.title) edit({ title });
      if (title === before.title) return;
      await flush();
      const linking = new Set((index.backlinks.get(current.id) ?? []).map((b) => b.from.id));
      let changed = 0;
      for (const id of linking) {
        if (await store.update(id, (d) => ({ ...d, body: renameLinks(d.body, before.title, before.path, title) }))) changed++;
      }
      if (changed) flash(`Links in ${changed} ${changed === 1 ? "notitie" : "notities"} bijgewerkt.`);
    });

  const linkMention = (b: Backlink) =>
    run(async () => {
      if (!current) return;
      const names = [current.title];
      const re = new RegExp(`(^|[^\\p{L}\\p{N}\\[])(${names.map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\p{L}\\p{N}])`, "iu");
      await store.update(b.from.id, (d) => ({
        ...d,
        body: d.body.replace(re, (_m, pre: string, word: string) =>
          word === current.title ? `${pre}[[${current.title}]]` : `${pre}[[${current.title}|${word}]]`,
        ),
      }));
    });

  const restoreVersion = (v: NoteVersion) => {
    edit({ title: v.title, folder: v.folder, body: v.body });
    setHistory(false);
    flash(`Versie ${v.revision} teruggezet.`);
  };

  const resolveConflict = (keep: "mine" | "theirs") => {
    if (!conflict || !draftRef.current) return;
    if (keep === "theirs") {
      draftRef.current = { id: conflict.theirs.id, doc: docOf(conflict.theirs), dirty: false };
      setDraft(docOf(conflict.theirs));
      setSaveState("saved");
    } else edit(conflict.mine);
    setConflict(null);
  };

  const startImport = (files: FileList | null) =>
    files &&
    run(async () => {
      setMenu(false);
      setImportPlan(await planImport([...files], index));
    });

  const doImport = () =>
    importPlan &&
    run(async () => {
      setImporting(0);
      try {
        const created = await store.importMany(importPlan.docs, setImporting);
        flash(`${created.length} notities geïmporteerd.`);
        setImportPlan(null);
      } finally {
        setImporting(null);
      }
    });

  const doExport = () => {
    setMenu(false);
    if (!confirm("De export is NIET versleuteld: iedereen met het zip-bestand kan je notities lezen. Doorgaan?")) return;
    download(exportZip(all));
  };

  // ------------------------------------------------------------ sneltoetsen

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && (e.key === "o" || e.key === "k")) {
        e.preventDefault();
        setSwitcher(true);
      } else if (mod && e.key === "e" && current) {
        e.preventDefault();
        setMode(mode === "read" ? "split" : "read");
      } else if (mod && e.key === "s") {
        e.preventDefault();
        void flush();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // ------------------------------------------------------------ voorbeeld

  const rendered = useMemo(() => (draft ? renderNote(draft.body, ctx) : null), [draft, ctx]);
  const previewRef = useRef<HTMLDivElement>(null);

  const scrollToHeading = useCallback((s: string) => {
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

  const onPreviewClick = (e: MouseEvent<HTMLDivElement>) => {
    const t = e.target as HTMLElement;
    const a = t.closest("a");
    if (a?.classList.contains("wikilink")) {
      e.preventDefault();
      if (a.dataset.app) return navigate(a.getAttribute("href")!);
      if (a.dataset.note) return openNote(a.dataset.note, a.dataset.heading);
      if (a.dataset.heading) return scrollToHeading(a.dataset.heading);
      if (a.dataset.new) void run(() => createNote(a.dataset.new!, "", ""));
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

  const suggestions: Suggestion[] = useMemo(
    () => [
      ...index.notes.map((n) => ({ insert: n.title, label: n.title, hint: n.folder || undefined })),
      ...apps.map((a) => ({ insert: `app:${a.name}`, label: a.name, hint: "app uit de catalogus" })),
    ],
    [index, apps],
  );

  // ------------------------------------------------------------ weergave

  if (store.loadError) return <ErrorBox error={store.loadError} />;
  if (!store.notes) return <Loading />;

  const pinned = index.notes.filter((n) => n.pinned);
  const trashCount = all.filter((n) => n.deleted_at).length;

  return (
    <div className="notes-app">
      <aside className="notes-side">
        <div className="notes-side-actions">
          <button className="btn btn-primary" onClick={newNote} title="Nieuwe notitie">
            + Nieuw
          </button>
          <button className="btn" onClick={daily} title="Dagnotitie van vandaag">
            Vandaag
          </button>
          <button className={`btn${view === "graph" ? " selected" : ""}`} onClick={() => setView(view === "graph" ? "note" : "graph")}>
            Grafiek
          </button>
          <div className="menu-wrap">
            <button className="btn btn-ghost" onClick={() => setMenu(!menu)} aria-label="Meer">
              ⋯
            </button>
            {menu && (
              <div className="menu" onMouseLeave={() => setMenu(false)}>
                <label className="menu-item">
                  Markdown-bestanden importeren
                  <input type="file" accept=".md,.markdown,.txt" multiple hidden onChange={(e) => startImport(e.target.files)} />
                </label>
                <label className="menu-item">
                  Obsidian-kluis (map) importeren
                  <input
                    type="file"
                    multiple
                    hidden
                    ref={(el) => el?.setAttribute("webkitdirectory", "")}
                    onChange={(e) => startImport(e.target.files)}
                  />
                </label>
                <button className="menu-item" onClick={doExport}>
                  Exporteren als zip (Markdown)
                </button>
                <button className="menu-item" onClick={() => (setMenu(false), setView("trash"))}>
                  Prullenbak{trashCount ? ` (${trashCount})` : ""}
                </button>
                <button className="menu-item" onClick={() => (setMenu(false), setSwitcher(true))}>
                  Snelkiezer <span className="muted small">Ctrl+O</span>
                </button>
              </div>
            )}
          </div>
        </div>
        <input
          className="notes-search"
          type="search"
          placeholder="Zoeken… (#tag, map:x, -woord)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query.trim() ? (
          <SearchResults index={index} query={query} currentId={currentId} onOpen={openNote} />
        ) : (
          <>
            {pinned.length > 0 && (
              <div className="side-group">
                <div className="side-label">Bladwijzers</div>
                <ul className="tree">
                  {pinned.map((n) => (
                    <li key={n.id}>
                      <button className={`tree-item${n.id === currentId ? " active" : ""}`} onClick={() => openNote(n.id)}>
                        ★ {n.title}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="side-tabs">
              <button className={sideTab === "files" ? "active" : undefined} onClick={() => setSideTab("files")}>
                Bestanden <span className="count">{index.notes.length}</span>
              </button>
              <button className={sideTab === "tags" ? "active" : undefined} onClick={() => setSideTab("tags")}>
                Tags <span className="count">{index.tags.size}</span>
              </button>
            </div>
            {sideTab === "files" ? (
              <FileTree index={index} currentId={currentId} onOpen={openNote} onMove={moveNote} />
            ) : (
              <TagList index={index} onTag={(t) => setQuery(`#${t}`)} />
            )}
          </>
        )}
        <div className="notes-side-foot">
          <span className="muted small">🔒 End-to-end versleuteld</span>
          <button className="btn btn-ghost" onClick={() => (void flush().then(lock))}>
            Vergrendelen
          </button>
        </div>
      </aside>

      <section className="notes-main">
        {toast && <div className="toast">{toast}</div>}
        <ErrorBox error={error} />
        {store.unreadable > 0 && (
          <div className="warn-box small">{store.unreadable} notities konden niet ontsleuteld worden en worden niet getoond.</div>
        )}
        {view === "trash" ? (
          <TrashView
            notes={all}
            onRestore={(id) => void run(() => store.restore(id))}
            onDelete={(ids) => void run(async () => {
              for (const id of ids) await store.remove(id);
            })}
          />
        ) : view === "graph" ? (
          <div className="graph-page">
            <h2>Grafiek</h2>
            <GraphView index={index} currentId={currentId} onOpen={openNote} onTag={(t) => setQuery(`#${t}`)} />
          </div>
        ) : !current || !draft ? (
          <EmptyState count={index.notes.length} onNew={newNote} onWelcome={welcome} onSwitch={() => setSwitcher(true)} />
        ) : (
          <>
            <div className="note-head">
              <div className="note-path">
                <input
                  className="note-folder"
                  list="note-folders"
                  value={draft.folder}
                  placeholder="(hoofdmap)"
                  aria-label="Map"
                  onChange={(e) => edit({ folder: e.target.value })}
                  onBlur={() => draft.folder !== cleanFolder(draft.folder) && edit({ folder: cleanFolder(draft.folder) })}
                />
                <datalist id="note-folders">
                  {index.folders.map((f) => (
                    <option key={f} value={f} />
                  ))}
                </datalist>
                <span className={`save-state save-${saveState}`}>
                  {{ saved: "Bewaard", dirty: "Gewijzigd", saving: "Bewaren…", error: "Niet bewaard" }[saveState]}
                </span>
                <div className="note-actions">
                  <div className="seg" role="group" aria-label="Weergave">
                    {(["edit", "split", "read"] as Mode[]).map((m) => (
                      <button key={m} className={mode === m ? "active" : undefined} onClick={() => setMode(m)}>
                        {{ edit: "Bewerken", split: "Naast elkaar", read: "Lezen" }[m]}
                      </button>
                    ))}
                  </div>
                  <button className="btn btn-ghost" onClick={togglePin} title={draft.pinned ? "Bladwijzer weghalen" : "Bladwijzer"}>
                    {draft.pinned ? "★" : "☆"}
                  </button>
                  <button className="btn btn-ghost" onClick={() => (void flush(), setHistory(true))}>
                    Geschiedenis
                  </button>
                  <button className="btn btn-ghost btn-danger-text" onClick={trashCurrent}>
                    Verwijderen
                  </button>
                </div>
              </div>
              <div className="note-title-row">
                <input
                  className="note-title"
                  value={draft.title}
                  aria-label="Titel"
                  onFocus={() => (titleAtFocus.current = { title: current.title, path: notePath(current) })}
                  onChange={(e) => edit({ title: e.target.value })}
                  onBlur={commitTitle}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      (e.target as HTMLInputElement).blur();
                      editor.current?.focus();
                    }
                  }}
                />
              </div>
            </div>
            {conflict && (
              <div className="warn-box">
                Deze notitie is intussen op een ander tabblad of toestel gewijzigd.{" "}
                <button className="btn btn-ghost" onClick={() => resolveConflict("mine")}>
                  Mijn versie bewaren
                </button>
                <button className="btn btn-ghost" onClick={() => resolveConflict("theirs")}>
                  Andere versie laden
                </button>
              </div>
            )}
            {mode !== "read" && (
              <div className="md-toolbar">
                <button onClick={() => editor.current?.wrap("**")} title="Vet (Ctrl+B)">
                  <b>B</b>
                </button>
                <button onClick={() => editor.current?.wrap("*")} title="Cursief (Ctrl+I)">
                  <i>I</i>
                </button>
                <button onClick={() => editor.current?.wrap("==")} title="Markeren">
                  <mark>M</mark>
                </button>
                <button onClick={() => editor.current?.wrap("[[", "]]")} title="Link naar notitie">
                  [[ ]]
                </button>
                <button onClick={() => editor.current?.insert("\n- [ ] ")} title="Taak">
                  ☐ Taak
                </button>
                <button onClick={() => editor.current?.insert("\n> [!note] Titel\n> ")} title="Callout">
                  Callout
                </button>
                <button onClick={() => editor.current?.wrap("||")} title="Geheim: wazig tot je klikt">
                  Geheim
                </button>
                {templates.length > 0 && (
                  <select
                    value=""
                    onChange={(e) => {
                      const t = templates.find((x) => x.id === e.target.value);
                      if (t) editor.current?.insert(applyTemplate(t.body, draft.title));
                    }}
                  >
                    <option value="">Sjabloon invoegen…</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}
            <div className={`note-body mode-${mode}`}>
              {mode !== "read" && (
                <Editor
                  ref={editor}
                  value={draft.body}
                  onChange={(body) => edit({ body })}
                  suggestions={suggestions}
                  onSaveNow={() => void flush()}
                  placeholder="Schrijf in Markdown. [[ voor een link, # voor een tag."
                />
              )}
              {mode !== "edit" && rendered && (
                <div className="preview" ref={previewRef}>
                  <PropertiesTable props={rendered.props} />
                  {mode === "read" && <h1 className="preview-title">{draft.title}</h1>}
                  <div className="md" onClick={onPreviewClick} dangerouslySetInnerHTML={{ __html: rendered.html }} />
                </div>
              )}
            </div>
            <HistoryModal
              open={history}
              note={current}
              load={store.versions}
              ctx={ctx}
              onClose={() => setHistory(false)}
              onRestore={restoreVersion}
            />
          </>
        )}
      </section>

      {view === "note" && current && draft && (
        <MetaPanel
          index={index}
          note={current}
          draft={draft}
          onOpen={openNote}
          onHeading={(h: Heading) => (mode === "read" ? scrollToHeading(slug(h.text)) : editor.current?.scrollToLine(h.line))}
          onLinkMention={linkMention}
          onTag={(t) => setQuery(`#${t}`)}
        />
      )}

      <QuickSwitcher
        open={switcher}
        index={index}
        onClose={() => setSwitcher(false)}
        onOpen={openNote}
        onCreate={(title) => void run(() => createNote(title))}
      />
      <ImportDialog plan={importPlan} progress={importing} onCancel={() => setImportPlan(null)} onConfirm={doImport} />
    </div>
  );
}

function EmptyState({ count, onNew, onWelcome, onSwitch }: { count: number; onNew: () => void; onWelcome: () => void; onSwitch: () => void }) {
  return (
    <div className="notes-empty">
      <h2>{count ? "Kies een notitie" : "Nog geen notities"}</h2>
      <p className="muted">
        Markdown-notities zoals in Obsidian: links tussen notities, backlinks, tags, een grafiek, dagnotities en
        sjablonen. Alles end-to-end versleuteld met je kluissleutel.
      </p>
      <div className="row wrap">
        <button className="btn btn-primary" onClick={onNew}>
          Nieuwe notitie
        </button>
        {count === 0 && (
          <button className="btn" onClick={onWelcome}>
            Voorbeeldnotities maken
          </button>
        )}
        {count > 0 && (
          <button className="btn" onClick={onSwitch}>
            Zoek een notitie (Ctrl+O)
          </button>
        )}
      </div>
      {count === 0 && <p className="muted small">Een Obsidian-kluis overzetten? Kies ⋯ en daarna "Obsidian-kluis (map) importeren".</p>}
    </div>
  );
}

function ImportDialog({
  plan,
  progress,
  onCancel,
  onConfirm,
}: {
  plan: ImportPlan | null;
  progress: number | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!plan) return null;
  return (
    <div className="overlay">
      <div className="modal-card">
        <h2>Importeren</h2>
        <p>
          {plan.docs.length} {plan.docs.length === 1 ? "notitie wordt" : "notities worden"} in je browser versleuteld en
          toegevoegd.
        </p>
        {plan.skipped.length > 0 && (
          <details>
            <summary>{plan.skipped.length} bestanden overgeslagen</summary>
            <ul className="small">
              {plan.skipped.slice(0, 50).map((s) => (
                <li key={s.name}>
                  {s.name}: {s.reason}
                </li>
              ))}
            </ul>
          </details>
        )}
        <div className="form-actions" style={{ gap: 8 }}>
          <button className="btn" onClick={onCancel} disabled={progress !== null}>
            Annuleren
          </button>
          <button className="btn btn-primary" onClick={onConfirm} disabled={!plan.docs.length || progress !== null}>
            {progress !== null ? `Bezig… ${progress}/${plan.docs.length}` : "Importeren"}
          </button>
        </div>
      </div>
    </div>
  );
}
