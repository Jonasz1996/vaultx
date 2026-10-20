import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import DOMPurify from "dompurify";
import { useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import type {
  LinkArchiveKind,
  LinkCollection,
  LinkImportResult,
  LinkItem,
  LinkPage,
  LinkTag,
} from "../api/types";
import { Badge, Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate } from "../format";

const LIMIT = 50;
const COLORS = ["#0ea5e9", "#22c55e", "#eab308", "#f97316", "#ef4444", "#a855f7", "#64748b"];

const archiveLabels: Record<LinkArchiveKind, string> = {
  screenshot: "Screenshot",
  pdf: "PDF",
  readable: "Leesbaar",
  page: "Pagina",
  preview: "Voorbeeld",
};

type Sort = "newest" | "oldest" | "name" | "name_desc";

interface TreeNode {
  col: LinkCollection;
  depth: number;
}

/** Collecties als boom, in volgorde om te tonen (ouder, dan kinderen op naam). */
function flatten(cols: LinkCollection[]): TreeNode[] {
  const ids = new Set(cols.map((c) => c.id));
  const children = new Map<string | null, LinkCollection[]>();
  for (const c of cols) {
    const parent = c.parent_id && ids.has(c.parent_id) ? c.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), c]);
  }
  const out: TreeNode[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const c of (children.get(parent) ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      out.push({ col: c, depth });
      walk(c.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

function descendants(cols: LinkCollection[], id: string): Set<string> {
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

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

function archiveUrl(link: LinkItem, kind: LinkArchiveKind, download = false): string {
  return `/api/v1/links/${link.id}/archives/${kind}${download ? "?download=true" : ""}`;
}

export function LinksPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = params.get("c") ?? ""; // "" = alles, "unsorted", "pinned" of een collectie-id
  const tag = params.get("tag") ?? "";
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("newest");
  const [editing, setEditing] = useState<LinkItem | "new" | null>(null);
  const [editingCol, setEditingCol] = useState<LinkCollection | "new" | null>(null);
  const [importing, setImporting] = useState(false);
  const [managingTags, setManagingTags] = useState(false);
  const [reading, setReading] = useState<LinkItem | null>(null);
  const [shot, setShot] = useState<LinkItem | null>(null);

  const collections = useQuery({
    queryKey: ["links", "collections"],
    queryFn: () => api.get<LinkCollection[]>("/api/v1/links/collections"),
  });
  const tags = useQuery({ queryKey: ["links", "tags"], queryFn: () => api.get<LinkTag[]>("/api/v1/links/tags") });
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
    queryFn: ({ pageParam }) => api.get<LinkPage>("/api/v1/links", { ...filters, limit: LIMIT, offset: pageParam }),
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + LIMIT : undefined),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["links"] });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch<LinkItem>(`/api/v1/links/${id}`, body),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/links/${id}`),
    onSuccess: refresh,
  });

  const cols = collections.data ?? [];
  const tree = useMemo(() => flatten(cols), [cols]);
  const colById = new Map(cols.map((c) => [c.id, c]));
  const items = feed.data?.pages.flatMap((p) => p.items) ?? [];
  const total = feed.data?.pages[0]?.total ?? 0;
  const current = colById.get(view);

  const go = (c: string, t = "") => {
    const next = new URLSearchParams();
    if (c) next.set("c", c);
    if (t) next.set("tag", t);
    setParams(next, { replace: true });
  };

  const title = current
    ? current.name
    : view === "unsorted"
      ? "Zonder collectie"
      : view === "pinned"
        ? "Vastgepind"
        : "Alle links";

  const path = (c: LinkCollection | undefined): string => {
    const parts: string[] = [];
    let node = c;
    while (node && parts.length < 10) {
      parts.unshift(node.name);
      node = node.parent_id ? colById.get(node.parent_id) : undefined;
    }
    return parts.join(" / ");
  };

  return (
    <div className="links-app">
      <aside className="notes-side">
        <input className="search notes-search" placeholder="Zoeken in links" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="notes-side-actions">
          <button className="btn btn-primary" onClick={() => setEditing("new")}>
            Link toevoegen
          </button>
          <button className="btn" onClick={() => setEditingCol("new")}>
            Collectie
          </button>
        </div>
        <ul className="tree">
          <li>
            <button className={`tree-item${view === "" && !tag ? " active" : ""}`} onClick={() => go("")}>
              <span>Alle links</span>
            </button>
          </li>
          <li>
            <button className={`tree-item${view === "pinned" ? " active" : ""}`} onClick={() => go("pinned")}>
              <span>★ Vastgepind</span>
            </button>
          </li>
          <li>
            <button className={`tree-item${view === "unsorted" ? " active" : ""}`} onClick={() => go("unsorted")}>
              <span>Zonder collectie</span>
            </button>
          </li>
        </ul>
        <div className="side-label">Collecties</div>
        {collections.isLoading ? (
          <Loading />
        ) : tree.length === 0 ? (
          <p className="muted small">Nog geen collecties.</p>
        ) : (
          <ul className="tree">
            {tree.map(({ col, depth }) => (
              <li key={col.id}>
                <button
                  className={`tree-item link-col depth-${Math.min(depth, 6)}${view === col.id ? " active" : ""}`}
                  onClick={() => go(col.id)}
                  title={col.description || col.name}
                >
                  <span className="truncate">
                    <svg className="link-dot" viewBox="0 0 10 10" aria-hidden="true">
                      <circle cx="5" cy="5" r="5" fill={col.color} />
                    </svg>
                    {col.name}
                  </span>
                  <span className="count">{col.link_count || ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="side-label row">
          <span>Tags</span>
          {!!tags.data?.length && (
            <button className="btn-link small" onClick={() => setManagingTags(true)}>
              beheren
            </button>
          )}
        </div>
        <div className="row wrap">
          {tags.data?.map((t) => (
            <button
              key={t.id}
              className={`chip${tag === t.name ? " active" : ""}`}
              onClick={() => go(view, tag === t.name ? "" : t.name)}
            >
              #{t.name} <span className="muted">{t.link_count}</span>
            </button>
          ))}
          {tags.data?.length === 0 && <span className="muted small">Nog geen tags.</span>}
        </div>
        <div className="notes-side-foot">
          <button className="btn btn-ghost" onClick={() => setImporting(true)}>
            Importeren
          </button>
          <a className="btn btn-ghost" href="/api/v1/links/export" download>
            Exporteren
          </a>
        </div>
      </aside>

      <section className="links-main">
        <header className="page-header">
          <div>
            <h1>{title}</h1>
            <p className="muted">
              {current ? (
                <>
                  {path(current) !== current.name && <span>{path(current)} · </span>}
                  {current.description}
                </>
              ) : view === "" && !q && !tag ? (
                `${total} links in ${cols.length} collecties.`
              ) : null}
              {tag && (
                <>
                  {" "}
                  Met tag <Badge tone="info">#{tag}</Badge>{" "}
                  <button className="btn-link small" onClick={() => go(view)}>
                    wissen
                  </button>
                </>
              )}
            </p>
          </div>
          <div className="actions">
            {current && (
              <button className="btn" onClick={() => setEditingCol(current)}>
                Collectie bewerken
              </button>
            )}
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sorteren">
              <option value="newest">Nieuwste eerst</option>
              <option value="oldest">Oudste eerst</option>
              <option value="name">Naam A-Z</option>
              <option value="name_desc">Naam Z-A</option>
            </select>
          </div>
        </header>
        <ErrorBox error={feed.error ?? update.error ?? remove.error} />
        {feed.isLoading ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty>
            {q || tag || view
              ? "Geen links gevonden."
              : "Nog geen links. Voeg er een toe of importeer je Linkwarden-export of browserbladwijzers."}
          </Empty>
        ) : (
          <>
            <p className="muted small">{total} links</p>
            <ul className="link-list">
              {items.map((l) => {
                const col = l.collection_id ? colById.get(l.collection_id) : undefined;
                const kinds = l.archives.map((a) => a.kind).filter((k) => k !== "preview");
                return (
                  <li key={l.id} className="link-row">
                    <img
                      className="link-icon"
                      src={`/icons/${hostOf(l.url)}/icon.png`}
                      alt=""
                      loading="lazy"
                      onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                    />
                    <div className="link-body">
                      <a className="link-title" href={l.url} target="_blank" rel="noreferrer noopener">
                        {l.name || l.url}
                      </a>
                      <div className="muted small truncate-line">
                        {hostOf(l.url)}
                        {col && view !== col.id && (
                          <>
                            {" · "}
                            <button className="btn-link small" onClick={() => go(col.id)}>
                              {col.name}
                            </button>
                          </>
                        )}
                        {" · "}
                        {formatDate(l.created_at)}
                      </div>
                      {l.description && <div className="link-desc">{l.description}</div>}
                      <div className="row wrap">
                        {l.tags.map((t) => (
                          <button key={t} className="chip" onClick={() => go(view, t)}>
                            #{t}
                          </button>
                        ))}
                        {kinds.map((k) =>
                          k === "readable" ? (
                            <button key={k} className="chip chip-archive" onClick={() => setReading(l)}>
                              {archiveLabels[k]}
                            </button>
                          ) : k === "screenshot" ? (
                            <button key={k} className="chip chip-archive" onClick={() => setShot(l)}>
                              {archiveLabels[k]}
                            </button>
                          ) : (
                            <a
                              key={k}
                              className="chip chip-archive"
                              href={archiveUrl(l, k)}
                              target="_blank"
                              rel="noreferrer noopener"
                            >
                              {archiveLabels[k]}
                            </a>
                          ),
                        )}
                      </div>
                    </div>
                    <div className="link-actions">
                      <button
                        className={`btn-icon${l.pinned ? " pinned" : ""}`}
                        title={l.pinned ? "Losmaken" : "Vastpinnen"}
                        aria-label={l.pinned ? "Losmaken" : "Vastpinnen"}
                        onClick={() => update.mutate({ id: l.id, body: { pinned: !l.pinned } })}
                      >
                        {l.pinned ? "★" : "☆"}
                      </button>
                      <button className="btn btn-ghost" onClick={() => setEditing(l)}>
                        Bewerken
                      </button>
                      <button
                        className="btn btn-ghost btn-danger-text"
                        onClick={() => {
                          if (confirm(`"${l.name || l.url}" verwijderen?`)) remove.mutate(l.id);
                        }}
                      >
                        Verwijderen
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
            {feed.hasNextPage && (
              <div className="pager">
                <button className="btn" onClick={() => void feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
                  Meer laden
                </button>
              </div>
            )}
          </>
        )}
      </section>

      <Modal
        title={editing === "new" ? "Link toevoegen" : "Link bewerken"}
        open={editing !== null}
        onClose={() => setEditing(null)}
      >
        {editing !== null && (
          <LinkForm
            link={editing === "new" ? null : editing}
            tree={tree}
            defaultCollection={current?.id ?? null}
            onDone={() => {
              setEditing(null);
              refresh();
            }}
          />
        )}
      </Modal>
      <Modal
        title={editingCol === "new" ? "Nieuwe collectie" : "Collectie bewerken"}
        open={editingCol !== null}
        onClose={() => setEditingCol(null)}
      >
        {editingCol !== null && (
          <CollectionForm
            col={editingCol === "new" ? null : editingCol}
            cols={cols}
            tree={tree}
            defaultParent={editingCol === "new" ? (current?.id ?? null) : null}
            onDone={(deleted) => {
              setEditingCol(null);
              if (deleted) go("");
              refresh();
            }}
          />
        )}
      </Modal>
      <Modal title="Links importeren" open={importing} onClose={() => setImporting(false)} wide>
        <ImportForm onImported={refresh} />
      </Modal>
      <Modal title="Tags beheren" open={managingTags} onClose={() => setManagingTags(false)}>
        <TagManager tags={tags.data ?? []} onChanged={refresh} />
      </Modal>
      <Modal title={reading?.name || "Leesbare versie"} open={reading !== null} onClose={() => setReading(null)} wide>
        {reading && <Readable link={reading} />}
      </Modal>
      <Modal title={shot?.name || "Screenshot"} open={shot !== null} onClose={() => setShot(null)} wide>
        {shot && (
          <div className="stack">
            <img className="link-shot" src={archiveUrl(shot, "screenshot")} alt={`Screenshot van ${shot.url}`} />
            <a className="btn" href={archiveUrl(shot, "screenshot", true)}>
              Downloaden
            </a>
          </div>
        )}
      </Modal>
    </div>
  );
}

function CollectionSelect({
  tree,
  value,
  onChange,
  exclude,
  emptyLabel,
}: {
  tree: TreeNode[];
  value: string;
  onChange: (v: string) => void;
  exclude?: Set<string>;
  emptyLabel: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}</option>
      {tree
        .filter(({ col }) => !exclude?.has(col.id))
        .map(({ col, depth }) => (
          <option key={col.id} value={col.id}>
            {"  ".repeat(depth) + col.name}
          </option>
        ))}
    </select>
  );
}

function LinkForm({
  link,
  tree,
  defaultCollection,
  onDone,
}: {
  link: LinkItem | null;
  tree: TreeNode[];
  defaultCollection: string | null;
  onDone: () => void;
}) {
  const [url, setUrl] = useState(link?.url ?? "");
  const [name, setName] = useState(link?.name ?? "");
  const [description, setDescription] = useState(link?.description ?? "");
  const [collection, setCollection] = useState(link ? (link.collection_id ?? "") : (defaultCollection ?? ""));
  const [tagText, setTagText] = useState(link?.tags.join(", ") ?? "");
  const [pinned, setPinned] = useState(link?.pinned ?? false);
  const save = useMutation({
    mutationFn: (target: string) => {
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
      return link ? api.patch<LinkItem>(`/api/v1/links/${link.id}`, body) : api.post<LinkItem>("/api/v1/links", body);
    },
    onSuccess: onDone,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    let value = url.trim();
    if (value && !/^[a-z][a-z0-9+.-]*:/i.test(value)) value = `https://${value}`;
    setUrl(value);
    save.mutate(value);
  };
  return (
    <form className="form" onSubmit={submit}>
      <label className="field">
        <span>URL</span>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onBlur={() => {
            const v = url.trim();
            if (v && !/^[a-z][a-z0-9+.-]*:/i.test(v)) setUrl(`https://${v}`);
          }}
          placeholder="https://"
          required
          autoFocus
        />
      </label>
      <label className="field">
        <span>Titel</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Leeg = de URL" />
      </label>
      <label className="field">
        <span>Beschrijving</span>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="field">
        <span>Collectie</span>
        <CollectionSelect tree={tree} value={collection} onChange={setCollection} emptyLabel="Zonder collectie" />
      </label>
      <label className="field">
        <span>Tags (komma's ertussen)</span>
        <input value={tagText} onChange={(e) => setTagText(e.target.value)} placeholder="homelab, docs" />
      </label>
      <label className="check">
        <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Vastpinnen
      </label>
      <ErrorBox error={save.error} />
      <div className="form-actions">
        <button className="btn btn-primary" disabled={save.isPending}>
          {link ? "Bewaren" : "Toevoegen"}
        </button>
      </div>
    </form>
  );
}

function CollectionForm({
  col,
  cols,
  tree,
  defaultParent,
  onDone,
}: {
  col: LinkCollection | null;
  cols: LinkCollection[];
  tree: TreeNode[];
  defaultParent: string | null;
  onDone: (deleted?: boolean) => void;
}) {
  const [name, setName] = useState(col?.name ?? "");
  const [description, setDescription] = useState(col?.description ?? "");
  const [color, setColor] = useState(col?.color ?? COLORS[0]);
  const [parent, setParent] = useState(col ? (col.parent_id ?? "") : (defaultParent ?? ""));
  const below = col ? descendants(cols, col.id) : undefined;
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description, color, parent_id: parent || null };
      return col
        ? api.patch<LinkCollection>(`/api/v1/links/collections/${col.id}`, body)
        : api.post<LinkCollection>("/api/v1/links/collections", body);
    },
    onSuccess: () => onDone(),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/v1/links/collections/${col!.id}`),
    onSuccess: () => onDone(true),
  });
  const inside = below ? cols.filter((c) => below.has(c.id)) : [];
  const linkCount = inside.reduce((n, c) => n + c.link_count, 0);
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="field">
        <span>Naam</span>
        <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
      </label>
      <label className="field">
        <span>Beschrijving</span>
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="field">
        <span>In collectie</span>
        <CollectionSelect
          tree={tree}
          value={parent}
          onChange={setParent}
          exclude={below}
          emptyLabel="(hoogste niveau)"
        />
      </label>
      <div className="field">
        <span>Kleur</span>
        <div className="row wrap">
          {COLORS.map((c) => (
            <button
              type="button"
              key={c}
              className={`color-swatch${color === c ? " active" : ""}`}
              aria-label={c}
              onClick={() => setColor(c)}
            >
              <svg viewBox="0 0 10 10" aria-hidden="true">
                <circle cx="5" cy="5" r="5" fill={c} />
              </svg>
            </button>
          ))}
          <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label="Eigen kleur" />
        </div>
      </div>
      <ErrorBox error={save.error ?? remove.error} />
      <div className="form-actions">
        {col && (
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => {
              const extra =
                inside.length > 1 ? ` met ${inside.length - 1} subcollecties` : "";
              if (confirm(`"${col.name}"${extra} en ${linkCount} links verwijderen? Dit kan niet ongedaan worden.`))
                remove.mutate();
            }}
          >
            Verwijderen
          </button>
        )}
        <button className="btn btn-primary" disabled={save.isPending}>
          {col ? "Bewaren" : "Aanmaken"}
        </button>
      </div>
    </form>
  );
}

function ImportForm({ onImported }: { onImported: () => void }) {
  const [format, setFormat] = useState<"linkwarden" | "bookmarks">("linkwarden");
  const [file, setFile] = useState<File | null>(null);
  const run = useMutation({
    mutationFn: async () => {
      const data = await file!.text();
      return api.post<LinkImportResult>("/api/v1/links/import", { format, data });
    },
    onSuccess: onImported,
  });
  const res = run.data;
  return (
    <div className="stack">
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) run.mutate();
        }}
      >
        <div className="seg">
          <label className="check">
            <input type="radio" checked={format === "linkwarden"} onChange={() => setFormat("linkwarden")} />{" "}
            Linkwarden-export (.json)
          </label>
          <label className="check">
            <input type="radio" checked={format === "bookmarks"} onChange={() => setFormat("bookmarks")} />{" "}
            Browserbladwijzers (.html)
          </label>
        </div>
        <p className="muted small">
          {format === "linkwarden"
            ? "In Linkwarden: Instellingen > Data > Export Data. Collecties (met nesting), links, tags en vastgepinde links komen mee; gearchiveerde bestanden zitten niet in die export. Ook een VaultX-export werkt hier."
            : "In Edge: Favorieten > ... > Favorieten exporteren. Chrome en Firefox hebben dezelfde export. Mappen worden subcollecties van \"Geïmporteerde bladwijzers\"."}
        </p>
        <input
          type="file"
          accept={format === "linkwarden" ? ".json,application/json" : ".html,.htm,text/html"}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <ErrorBox error={run.error} />
        <div className="form-actions">
          <button className="btn btn-primary" disabled={!file || run.isPending}>
            {run.isPending ? "Bezig met importeren…" : "Importeren"}
          </button>
        </div>
      </form>
      {res && (
        <div className="good-box">
          {res.collections_created} collecties en {res.links_created} links aangemaakt
          {res.links_updated ? `, ${res.links_updated} bestaande links aangevuld` : ""}, {res.tags_created} nieuwe
          tags{res.skipped ? `, ${res.skipped} overgeslagen` : ""}. Opnieuw importeren maakt geen dubbels.
          {res.warnings.length > 0 && (
            <ul className="small">
              {res.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <details>
        <summary>Draait Linkwarden niet meer? Importeer rechtstreeks uit de database</summary>
        <p className="small">
          Op de VaultX-server, als root. Dit neemt ook de screenshots, pdf's en leesbare versies mee als je de map met
          Linkwarden-bestanden (STORAGE_FOLDER, standaard <code>data</code>) meegeeft. De Linkwarden-database wordt enkel
          gelezen.
        </p>
        <pre className="wide-pre">{`set -a; . /etc/vaultx/vaultx.env; set +a; cd /opt/vaultx/backend
export PGPASSWORD='wachtwoord-van-linkwarden-db'
/opt/vaultx/venv/bin/python -m app.cli import-linkwarden \\
  --dsn postgresql://linkwarden@HOST:5432/linkwarden \\
  --to jouw-vaultx-login --files /pad/naar/linkwarden/data`}</pre>
        <p className="small muted">
          Met <code>--list-users</code> zie je de Linkwarden-gebruikers, met <code>--dry-run</code> enkel wat er gelezen
          wordt. Opnieuw uitvoeren vult aan zonder dubbels. Zie docs/links.md.
        </p>
      </details>
    </div>
  );
}

function TagManager({ tags, onChanged }: { tags: LinkTag[]; onChanged: () => void }) {
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.patch(`/api/v1/links/tags/${id}`, { name }),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/links/tags/${id}`),
    onSuccess: onChanged,
  });
  return (
    <div className="stack">
      <p className="muted small">Hernoem je een tag naar een naam die al bestaat, dan worden beide samengevoegd.</p>
      <ErrorBox error={rename.error ?? remove.error} />
      <ul className="plain-list">
        {tags.map((t) => (
          <li key={t.id} className="row">
            <span className="grow">
              #{t.name} <span className="muted small">{t.link_count} links</span>
            </span>
            <button
              className="btn btn-ghost"
              onClick={() => {
                const name = prompt("Nieuwe naam", t.name);
                if (name && name.trim() && name.trim() !== t.name) rename.mutate({ id: t.id, name: name.trim() });
              }}
            >
              Hernoemen
            </button>
            <button
              className="btn btn-ghost btn-danger-text"
              onClick={() => {
                if (confirm(`Tag #${t.name} verwijderen? De links blijven.`)) remove.mutate(t.id);
              }}
            >
              Verwijderen
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Leesbare versie uit het archief: gesaneerd, links openen in een nieuw tabblad en
 * afbeeldingen van buitenaf vallen weg (de CSP zou ze toch blokkeren, en ze verklappen
 * aan die site dat je het artikel leest).
 */
function cleanArticle(content: string): string {
  const frag = DOMPurify.sanitize(content, {
    FORBID_TAGS: ["style", "form", "input", "button", "iframe", "object", "embed", "video", "audio", "source"],
    FORBID_ATTR: ["style", "srcset"],
    RETURN_DOM_FRAGMENT: true,
  });
  frag.querySelectorAll("img").forEach((img) => {
    if (!img.getAttribute("src")?.startsWith("data:image/")) img.remove();
  });
  frag.querySelectorAll("a[href]").forEach((a) => {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noreferrer noopener");
  });
  const box = document.createElement("div");
  box.append(frag);
  return box.innerHTML;
}

function Readable({ link }: { link: LinkItem }) {
  const doc = useQuery({
    queryKey: ["links", "readable", link.id],
    queryFn: async () => {
      const res = await fetch(archiveUrl(link, "readable"), { credentials: "same-origin" });
      if (!res.ok) throw new Error("De leesbare versie kon niet geladen worden");
      return (await res.json()) as { title?: string; content?: string; byline?: string };
    },
  });
  const html = useMemo(() => (doc.data?.content ? cleanArticle(doc.data.content) : ""), [doc.data]);
  if (doc.isLoading) return <Loading />;
  if (doc.error) return <ErrorBox error={doc.error} />;
  return (
    <article className="md link-readable">
      <p className="muted small">
        <a href={link.url} target="_blank" rel="noreferrer noopener">
          {link.url}
        </a>
        {doc.data?.byline ? ` · ${doc.data.byline}` : ""}
      </p>
      {/* Gesaneerd in cleanArticle. */}
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </article>
  );
}
