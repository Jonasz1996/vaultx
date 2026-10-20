// Webkluis: je items bekijken en bewerken in de browser, zoals de webkluis van Vaultwarden.
// Alles wordt in de browser ont- en versleuteld; VaultX ziet enkel versleutelde items.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { VaultStatus } from "../api/types";
import { Badge, Card, Empty, ErrorBox, Loading, Modal } from "../components/ui";
import { formatDate } from "../format";
import { AUTO_LOCK_MINUTES } from "../notes/session";
import { encryptText, type UserKey } from "../vault/crypto";
import { GENERATOR_DEFAULTS, generatePassword, parseTotp, totpCode, type GeneratorOptions } from "../vault/generate";
import {
  CIPHER_TYPES,
  FIELD_TYPES,
  IDENTITY_FIELDS,
  decryptAttachment,
  decryptCipher,
  decryptFolder,
  emptyItem,
  encryptAttachment,
  encryptCipher,
  iconDomain,
  launchUrl,
  localPath,
  subtitle,
  withPasswordHistory,
  type AttachmentView,
  type CipherType,
  type FolderView,
  type ItemView,
  type RawCipher,
  type SyncResponse,
} from "../vault/items";
import { TwoFactorRequired, useWebVault, type TwoFactorAnswer } from "../vault/webSession";
import { getAssertion, webAuthnSupported } from "../webauthn";

export function WebVault({ status }: { status: VaultStatus }) {
  const { key } = useWebVault();
  if (!key) return <WebUnlock status={status} />;
  return <VaultBrowser userKey={key} />;
}

// --------------------------------------------------------------------------- ontgrendelen

function WebUnlock({ status }: { status: VaultStatus }) {
  const { unlock, lockReason } = useWebVault();
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<TwoFactorRequired | null>(null);
  const [useCode, setUseCode] = useState(false);
  const [code, setCode] = useState("");
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const keyPossible = !!challenge?.securityKey && webAuthnSupported();
  const askCode = !!challenge && (useCode || !keyPossible);

  const attempt = async (answer?: TwoFactorAnswer) => {
    setBusy(true);
    setError(null);
    try {
      await unlock(status, password, answer);
      setPassword("");
      setCode("");
      setChallenge(null);
    } catch (err) {
      if (err instanceof TwoFactorRequired) {
        // Eerste keer: vraag de tweede stap. Daarna: nieuwe challenge, en de fout tonen.
        if (challenge) setError(new Error("Dat werd niet aanvaard. Probeer opnieuw."));
        setChallenge(err);
      } else setError(err);
    } finally {
      setBusy(false);
    }
  };

  const withKey = async () => {
    if (!challenge?.securityKey) return;
    setError(null);
    let answer: Record<string, unknown>;
    try {
      answer = await getAssertion(challenge.securityKey);
    } catch (err) {
      setError(err);
      return;
    }
    await attempt({ securityKey: answer, remember });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!challenge) await attempt();
    else if (askCode) await attempt({ code, remember });
    else await withKey();
  };

  return (
    <Card title="Kluis ontgrendelen">
      <form className="form" style={{ maxWidth: 460 }} onSubmit={submit}>
        {lockReason && <div className="info-box small">{lockReason}</div>}
        <p className="muted small" style={{ margin: 0 }}>
          Je browser leidt je sleutel af uit je master password en ontsleutelt de items zelf; VaultX ziet het
          wachtwoord en de inhoud van je items nooit. Na {AUTO_LOCK_MINUTES} minuten zonder activiteit vergrendelt de
          kluis vanzelf.
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
        {challenge && !askCode && (
          <div className="info-box small">
            Bevestig met je beveiligingssleutel of passkey: klik op de knop en raak je YubiKey aan als hij knippert.
          </div>
        )}
        {challenge && !challenge.securityKey && status.security_keys.length > 0 && (
          <div className="info-box small">
            Je beveiligingssleutel werkt enkel op {status.security_keys_rp_id ?? "de domeinnaam van VaultX"}. Hier
            kan je de code uit je authenticator-app of je herstelcode gebruiken.
          </div>
        )}
        {askCode && (
          <label className="field">
            <span>{challenge.authenticator ? "Code uit je authenticator-app of herstelcode" : "Herstelcode"}</span>
            <input
              autoComplete="one-time-code"
              autoFocus
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
        )}
        {challenge && (
          <label className="check">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Deze browser 30 dagen onthouden
          </label>
        )}
        <ErrorBox error={error} />
        <div className="form-actions">
          <button className="btn btn-primary" disabled={busy || !password || (askCode && !code)}>
            {busy
              ? challenge
                ? "Controleren…"
                : "Sleutel afleiden…"
              : challenge && !askCode
                ? "Beveiligingssleutel gebruiken"
                : "Ontgrendelen"}
          </button>
          {keyPossible && (
            <button type="button" className="btn btn-ghost" onClick={() => setUseCode(!useCode)}>
              {useCode ? "Toch met mijn sleutel" : "Code of herstelcode gebruiken"}
            </button>
          )}
        </div>
      </form>
    </Card>
  );
}

// --------------------------------------------------------------------------- overzicht

type Filter =
  | { kind: "all" }
  | { kind: "favorites" }
  | { kind: "trash" }
  | { kind: "type"; type: CipherType }
  | { kind: "folder"; id: string | null };

interface Decrypted {
  items: ItemView[];
  folders: FolderView[];
}

const SYNC_KEY = ["webvault", "sync"];

function sameFilter(a: Filter, b: Filter): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function VaultBrowser({ userKey }: { userKey: UserKey }) {
  const { request, lock } = useWebVault();
  const qc = useQueryClient();
  const sync = useQuery({
    queryKey: SYNC_KEY,
    queryFn: async (): Promise<Decrypted> => {
      const data = await request<SyncResponse>("GET", "/api/sync?excludeDomains=true");
      const [folders, items] = await Promise.all([
        Promise.all(data.folders.map((f) => decryptFolder(f, userKey))),
        Promise.all(data.ciphers.map((c) => decryptCipher(c, userKey))),
      ]);
      folders.sort((a, b) => a.name.localeCompare(b.name, "nl"));
      items.sort((a, b) => a.name.localeCompare(b.name, "nl", { sensitivity: "base" }));
      return { folders, items };
    },
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: SYNC_KEY });

  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<ItemView | null>(null);
  const [folderDialog, setFolderDialog] = useState<FolderView | "new" | null>(null);

  const items = sync.data?.items ?? [];
  const folders = sync.data?.folders ?? [];
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((i) => {
      if (filter.kind === "trash") {
        if (!i.deletedDate) return false;
      } else if (i.deletedDate) return false;
      if (filter.kind === "favorites" && !i.favorite) return false;
      if (filter.kind === "type" && i.type !== filter.type) return false;
      if (filter.kind === "folder" && i.folderId !== filter.id) return false;
      if (!q) return true;
      return [i.name, i.login.username, subtitle(i), ...i.login.uris.map((u) => u.uri)].some((s) =>
        s.toLowerCase().includes(q),
      );
    });
  }, [items, filter, search]);
  const selected = items.find((i) => i.id === selectedId) ?? null;
  const counts = useMemo(() => {
    const live = items.filter((i) => !i.deletedDate);
    return {
      all: live.length,
      favorites: live.filter((i) => i.favorite).length,
      trash: items.length - live.length,
      type: (t: CipherType) => live.filter((i) => i.type === t).length,
      folder: (id: string | null) => live.filter((i) => i.folderId === id).length,
    };
  }, [items]);

  const newItem = (type: CipherType) =>
    setEditing(emptyItem(type, filter.kind === "folder" ? filter.id : null));

  if (sync.isLoading) return <Loading />;
  if (!sync.data) return <ErrorBox error={sync.error} />;

  const nav = (f: Filter, label: ReactNode, count?: number) => (
    <button
      key={JSON.stringify(f)}
      className={sameFilter(filter, f) ? "active" : undefined}
      onClick={() => {
        setFilter(f);
        setSelectedId(null);
      }}
    >
      <span>{label}</span>
      {count !== undefined && <span className="muted">{count}</span>}
    </button>
  );

  return (
    <div className="wv">
      <aside className="wv-side">
        <div className="wv-nav">
          {nav({ kind: "all" }, "Alle items", counts.all)}
          {nav({ kind: "favorites" }, "Favorieten", counts.favorites)}
        </div>
        <div className="wv-nav">
          <div className="wv-nav-head">Soort</div>
          {(Object.keys(CIPHER_TYPES).map(Number) as CipherType[]).map((t) =>
            nav({ kind: "type", type: t }, CIPHER_TYPES[t], counts.type(t)),
          )}
        </div>
        <div className="wv-nav">
          <div className="wv-nav-head">
            Mappen
            <button className="btn-link" onClick={() => setFolderDialog("new")} title="Nieuwe map">
              + map
            </button>
          </div>
          {folders.map((f) => (
            <div key={f.id} className="wv-folder">
              {nav({ kind: "folder", id: f.id }, f.name, counts.folder(f.id))}
              <button className="btn-link" onClick={() => setFolderDialog(f)} aria-label={`Map ${f.name} bewerken`}>
                ✎
              </button>
            </div>
          ))}
          {nav({ kind: "folder", id: null }, "Geen map", counts.folder(null))}
        </div>
        <div className="wv-nav">{nav({ kind: "trash" }, "Prullenbak", counts.trash)}</div>
        <div className="wv-side-foot">
          <button className="btn btn-ghost" onClick={() => void refresh()} disabled={sync.isFetching}>
            {sync.isFetching ? "Synchroniseren…" : "Synchroniseren"}
          </button>
          <button className="btn btn-ghost" onClick={() => lock()}>
            Vergrendelen
          </button>
        </div>
      </aside>

      <section className="wv-list">
        <div className="wv-list-head">
          <input
            type="search"
            placeholder="Zoeken in de kluis"
            aria-label="Zoeken in de kluis"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <NewItemMenu onPick={newItem} />
        </div>
        <ErrorBox error={sync.error} />
        {!visible.length ? (
          <Empty>{filter.kind === "trash" ? "De prullenbak is leeg." : search ? "Niets gevonden." : "Nog geen items."}</Empty>
        ) : (
          <ul className="wv-items">
            {visible.map((i) => (
              <li key={i.id}>
                <button
                  className={i.id === selectedId ? "active" : undefined}
                  onClick={() => {
                    setSelectedId(i.id);
                    setEditing(null);
                  }}
                >
                  <ItemIcon item={i} />
                  <span className="wv-item-text">
                    <span className="wv-item-name">
                      {i.name}
                      {i.favorite && <span className="wv-star" aria-label="favoriet">★</span>}
                    </span>
                    <span className="muted small">{subtitle(i)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="wv-detail">
        {editing ? (
          <ItemEditor
            key={editing.id || `new-${editing.type}`}
            initial={editing}
            folders={folders}
            userKey={userKey}
            onCancel={() => setEditing(null)}
            onSaved={(id) => {
              setEditing(null);
              setSelectedId(id);
            }}
          />
        ) : selected ? (
          <ItemDetail
            key={selected.id}
            item={selected}
            folders={folders}
            userKey={userKey}
            onEdit={() => setEditing(selected)}
            onGone={() => setSelectedId(null)}
          />
        ) : (
          <div className="wv-placeholder muted">Kies een item, of maak een nieuw item aan.</div>
        )}
      </section>

      <FolderDialog folder={folderDialog} userKey={userKey} onClose={() => setFolderDialog(null)} onDeleted={(id) => {
        if (filter.kind === "folder" && filter.id === id) setFilter({ kind: "all" });
      }} />
    </div>
  );
}

function NewItemMenu({ onPick }: { onPick: (t: CipherType) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: PointerEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div className="wv-menu" ref={ref}>
      <button className="btn btn-primary" onClick={() => setOpen(!open)} aria-expanded={open}>
        + Nieuw
      </button>
      {open && (
        <div className="wv-menu-list" role="menu">
          {(Object.keys(CIPHER_TYPES).map(Number) as CipherType[])
            .filter((t) => t !== 5)
            .map((t) => (
              <button
                key={t}
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onPick(t);
                }}
              >
                {CIPHER_TYPES[t]}
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

const TYPE_GLYPH: Record<CipherType, string> = { 1: "🔑", 2: "📝", 3: "💳", 4: "🪪", 5: "⌘" };

function ItemIcon({ item }: { item: ItemView }) {
  const domain = item.type === 1 ? iconDomain(item) : null;
  const [failed, setFailed] = useState(false);
  if (domain && !failed)
    return (
      <img
        className="wv-icon"
        src={`/icons/${encodeURIComponent(domain)}/icon.png`}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
      />
    );
  return (
    <span className="wv-icon wv-icon-glyph" aria-hidden>
      {TYPE_GLYPH[item.type]}
    </span>
  );
}

// --------------------------------------------------------------------------- bekijken

const CLIPBOARD_CLEAR_MS = 30_000;

function useCopy() {
  const [copied, setCopied] = useState<string | null>(null);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(null), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);
  const copy = async (label: string, value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(label);
    // Zoals Bitwarden: het klembord na 30 seconden leegmaken, zodat een gekopieerd wachtwoord
    // of TOTP-code er niet blijft staan na het vergrendelen. Faalt stil zonder focus.
    window.setTimeout(() => void navigator.clipboard.writeText("").catch(() => {}), CLIPBOARD_CLEAR_MS);
  };
  return { copied, copy };
}

function FieldRow({
  label,
  value,
  secret = false,
  mono = false,
  multiline = false,
  link = null,
  copy,
  copied,
}: {
  label: string;
  value: string;
  secret?: boolean;
  mono?: boolean;
  multiline?: boolean;
  link?: string | null;
  copy: (label: string, value: string) => Promise<void>;
  copied: string | null;
}) {
  const [shown, setShown] = useState(false);
  if (!value) return null;
  const display = secret && !shown ? "••••••••••••" : value;
  return (
    <div className="wv-field">
      <div className="wv-field-label muted small">{label}</div>
      <div className="wv-field-row">
        {link ? (
          <a className={`wv-field-value${mono ? " mono" : ""}`} href={link} target="_blank" rel="noopener noreferrer">
            {display}
          </a>
        ) : multiline ? (
          <pre className={`wv-field-value wv-pre${mono ? " mono" : ""}`}>{display}</pre>
        ) : (
          <span className={`wv-field-value${mono || secret ? " mono" : ""}`}>{display}</span>
        )}
        <span className="wv-field-actions">
          {secret && (
            <button className="btn btn-ghost" onClick={() => setShown(!shown)}>
              {shown ? "Verbergen" : "Tonen"}
            </button>
          )}
          <button className="btn btn-ghost" onClick={() => void copy(label, value)} aria-label={`${label} kopiëren`}>
            {copied === label ? "Gekopieerd" : "Kopiëren"}
          </button>
        </span>
      </div>
    </div>
  );
}

function Totp({ secret, copy, copied }: { secret: string; copy: (l: string, v: string) => Promise<void>; copied: string | null }) {
  const params = useMemo(() => parseTotp(secret), [secret]);
  const [code, setCode] = useState("");
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (!params) return;
    let alive = true;
    const tick = async () => {
      const now = Date.now();
      const c = await totpCode(params, now);
      if (!alive) return;
      setCode(c);
      setLeft(params.period - (Math.floor(now / 1000) % params.period));
    };
    void tick();
    const t = window.setInterval(() => void tick(), 1000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [params]);
  if (!params) return <FieldRow label="TOTP-sleutel" value={secret} secret mono copy={copy} copied={copied} />;
  const pretty = code.length === 6 ? `${code.slice(0, 3)} ${code.slice(3)}` : code;
  return (
    <div className="wv-field">
      <div className="wv-field-label muted small">Verificatiecode (TOTP)</div>
      <div className="wv-field-row">
        <span className="wv-field-value mono wv-totp">{pretty}</span>
        <span className="muted small">{left}s</span>
        <span className="wv-field-actions">
          <button className="btn btn-ghost" onClick={() => void copy("Verificatiecode", code)} aria-label="Verificatiecode kopiëren">
            {copied === "Verificatiecode" ? "Gekopieerd" : "Kopiëren"}
          </button>
        </span>
      </div>
    </div>
  );
}

function ItemDetail({
  item,
  folders,
  userKey,
  onEdit,
  onGone,
}: {
  item: ItemView;
  folders: FolderView[];
  userKey: UserKey;
  onEdit: () => void;
  onGone: () => void;
}) {
  const { request, verifyMasterPassword } = useWebVault();
  const qc = useQueryClient();
  const { copied, copy } = useCopy();
  const [unlocked, setUnlocked] = useState(!item.reprompt);
  const refresh = () => qc.invalidateQueries({ queryKey: SYNC_KEY });
  const act = useMutation({
    mutationFn: async (action: "trash" | "restore" | "delete" | "favorite") => {
      if (action === "trash") await request("PUT", `/api/ciphers/${item.id}/delete`);
      if (action === "restore") await request("PUT", `/api/ciphers/${item.id}/restore`);
      if (action === "delete") await request("DELETE", `/api/ciphers/${item.id}`);
      if (action === "favorite")
        await request("PUT", `/api/ciphers/${item.id}/partial`, { folderId: item.folderId, favorite: !item.favorite });
      return action;
    },
    onSuccess: async (action) => {
      await refresh();
      if (action !== "favorite") onGone();
    },
  });
  const folder = folders.find((f) => f.id === item.folderId);
  const row = (label: string, value: string, opts: Partial<Parameters<typeof FieldRow>[0]> = {}) => (
    <FieldRow key={label} label={label} value={value} copy={copy} copied={copied} {...opts} />
  );

  return (
    <div className="wv-view">
      <div className="wv-view-head">
        <ItemIcon item={item} />
        <div>
          <h2>{item.name}</h2>
          <div className="muted small">
            {CIPHER_TYPES[item.type]}
            {folder && <> · {folder.name}</>}
            {item.deletedDate && <Badge tone="warn">in prullenbak</Badge>}
            {item.broken && <Badge tone="bad">deels niet te ontsleutelen</Badge>}
          </div>
        </div>
        <div className="wv-view-actions">
          {item.deletedDate ? (
            <>
              <button className="btn" disabled={act.isPending} onClick={() => act.mutate("restore")}>
                Terugzetten
              </button>
              <button
                className="btn btn-danger"
                disabled={act.isPending}
                onClick={() => confirm(`"${item.name}" definitief verwijderen?`) && act.mutate("delete")}
              >
                Definitief verwijderen
              </button>
            </>
          ) : (
            <>
              <button
                className="btn btn-ghost"
                disabled={act.isPending}
                onClick={() => act.mutate("favorite")}
                aria-label={item.favorite ? "Geen favoriet meer" : "Favoriet maken"}
                title={item.favorite ? "Geen favoriet meer" : "Favoriet maken"}
              >
                {item.favorite ? "★" : "☆"}
              </button>
              <button className="btn" onClick={onEdit} disabled={!unlocked || item.broken}>
                Bewerken
              </button>
              <button className="btn btn-ghost btn-danger-text" disabled={act.isPending} onClick={() => act.mutate("trash")}>
                Naar prullenbak
              </button>
            </>
          )}
        </div>
      </div>
      <ErrorBox error={act.error} />

      {!unlocked ? (
        <Reprompt verify={verifyMasterPassword} onOk={() => setUnlocked(true)} />
      ) : (
        <>
          {item.type === 1 && (
            <div className="wv-group">
              {row("Gebruikersnaam", item.login.username)}
              {row("Wachtwoord", item.login.password, { secret: true })}
              {item.login.totp && <Totp secret={item.login.totp} copy={copy} copied={copied} />}
            </div>
          )}
          {item.type === 1 && item.login.uris.some((u) => u.uri) && (
            <div className="wv-group">
              {item.login.uris
                .filter((u) => u.uri)
                .map((u, n) => (
                  <FieldRow
                    key={n}
                    label={n === 0 ? "Website" : `Website ${n + 1}`}
                    value={u.uri}
                    link={launchUrl(u.uri)}
                    copy={copy}
                    copied={copied}
                  />
                ))}
            </div>
          )}
          {item.type === 3 && (
            <div className="wv-group">
              {row("Kaarthouder", item.card.cardholderName)}
              {row("Merk", item.card.brand)}
              {row("Kaartnummer", item.card.number, { secret: true })}
              {row(
                "Vervaldatum",
                [item.card.expMonth.padStart(item.card.expMonth ? 2 : 0, "0"), item.card.expYear].filter(Boolean).join(" / "),
              )}
              {row("Beveiligingscode", item.card.code, { secret: true })}
            </div>
          )}
          {item.type === 4 && (
            <div className="wv-group">{IDENTITY_FIELDS.map(([k, label]) => row(label, item.identity[k]))}</div>
          )}
          {item.type === 5 && (
            <div className="wv-group">
              {row("Vingerafdruk", item.sshKey.keyFingerprint, { mono: true })}
              {row("Publieke sleutel", item.sshKey.publicKey, { mono: true, multiline: true })}
              {row("Private sleutel", item.sshKey.privateKey, { secret: true, multiline: true })}
            </div>
          )}
          {item.notes && <div className="wv-group">{row("Notities", item.notes, { multiline: true })}</div>}
          {item.fields.length > 0 && (
            <div className="wv-group">
              <div className="wv-group-head">Eigen velden</div>
              {item.fields.map((f, n) => (
                <FieldRow
                  key={n}
                  label={f.name || `Veld ${n + 1}`}
                  value={f.type === 2 ? (f.value === "true" ? "Ja" : "Nee") : f.value}
                  secret={f.type === 1}
                  copy={copy}
                  copied={copied}
                />
              ))}
            </div>
          )}
          <Attachments item={item} userKey={userKey} />
          {item.passwordHistory.length > 0 && <PasswordHistory item={item} copy={copy} copied={copied} />}
        </>
      )}
      <div className="muted small wv-dates">
        Laatst gewijzigd {formatDate(item.revisionDate)} · aangemaakt {formatDate(item.creationDate)}
        {item.login.passwordRevisionDate && <> · wachtwoord gewijzigd {formatDate(item.login.passwordRevisionDate)}</>}
      </div>
    </div>
  );
}

function Reprompt({ verify, onOk }: { verify: (pw: string) => Promise<boolean>; onOk: () => void }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [wrong, setWrong] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const ok = await verify(pw).finally(() => setBusy(false));
    if (ok) onOk();
    else setWrong(true);
  };
  return (
    <form className="form wv-group" onSubmit={submit} style={{ maxWidth: 420 }}>
      <p className="muted small" style={{ margin: 0 }}>
        Dit item vraagt je master password opnieuw voor je het kan bekijken.
      </p>
      <label className="field">
        <span>Master password</span>
        <input type="password" autoComplete="current-password" autoFocus value={pw} onChange={(e) => setPw(e.target.value)} />
      </label>
      {wrong && <div className="error-box">Master password is onjuist</div>}
      <div className="form-actions">
        <button className="btn btn-primary" disabled={busy || !pw}>
          {busy ? "Controleren…" : "Tonen"}
        </button>
      </div>
    </form>
  );
}

function PasswordHistory({ item, copy, copied }: { item: ItemView; copy: (l: string, v: string) => Promise<void>; copied: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="wv-group">
      <button className="btn-link" onClick={() => setOpen(!open)}>
        Wachtwoordgeschiedenis ({item.passwordHistory.length})
      </button>
      {open &&
        item.passwordHistory.map((h, n) => (
          <FieldRow
            key={n}
            label={`Gebruikt tot ${formatDate(h.lastUsedDate)}`}
            value={h.password}
            secret
            copy={copy}
            copied={copied}
          />
        ))}
    </div>
  );
}

function Attachments({ item, userKey }: { item: ItemView; userKey: UserKey }) {
  const { fetchRaw, request } = useWebVault();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const download = useMutation({
    mutationFn: async (att: AttachmentView) => {
      const res = await fetchRaw(localPath(att.url));
      if (!res.ok) throw new Error("Bijlage downloaden mislukt");
      const plain = await decryptAttachment(item, att, userKey, new Uint8Array(await res.arrayBuffer()));
      const url = URL.createObjectURL(new Blob([plain]));
      const a = document.createElement("a");
      a.href = url;
      a.download = att.fileName;
      a.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
    },
  });
  const upload = useMutation({
    mutationFn: async (file: File) => {
      const enc = await encryptAttachment(item, userKey, file.name, new Uint8Array(await file.arrayBuffer()));
      const res = await request<{ url: string }>("POST", `/api/ciphers/${item.id}/attachment/v2`, {
        fileName: enc.fileName,
        key: enc.key,
        fileSize: enc.data.length,
      });
      const form = new FormData();
      form.append("data", new Blob([enc.data]), enc.fileName);
      const up = await fetchRaw(localPath(res.url), { method: "POST", body: form });
      if (!up.ok) {
        const data = await up.json().catch(() => ({}));
        throw new Error((data as { message?: string }).message ?? "Uploaden mislukt");
      }
    },
    onSettled: () => qc.invalidateQueries({ queryKey: SYNC_KEY }),
  });
  const remove = useMutation({
    mutationFn: (att: AttachmentView) => request("DELETE", `/api/ciphers/${item.id}/attachment/${att.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: SYNC_KEY }),
  });
  if (item.deletedDate && !item.attachments.length) return null;
  return (
    <div className="wv-group">
      <div className="wv-group-head">
        Bijlagen
        {!item.deletedDate && (
          <>
            <button className="btn-link" onClick={() => input.current?.click()} disabled={upload.isPending}>
              {upload.isPending ? "Versleutelen en uploaden…" : "+ bijlage"}
            </button>
            <input
              ref={input}
              type="file"
              hidden
              aria-label="Bijlage toevoegen"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
          </>
        )}
      </div>
      {!item.attachments.length && <div className="muted small">Geen bijlagen.</div>}
      {item.attachments.map((a) => (
        <div key={a.id} className="wv-field-row">
          <span className="wv-field-value">{a.fileName}</span>
          <span className="muted small">{a.sizeName}</span>
          <span className="wv-field-actions">
            <button className="btn btn-ghost" onClick={() => download.mutate(a)} disabled={download.isPending}>
              Downloaden
            </button>
            {!item.deletedDate && (
              <button
                className="btn btn-ghost btn-danger-text"
                onClick={() => confirm(`Bijlage "${a.fileName}" verwijderen?`) && remove.mutate(a)}
                disabled={remove.isPending}
              >
                Verwijderen
              </button>
            )}
          </span>
        </div>
      ))}
      <ErrorBox error={download.error ?? upload.error ?? remove.error} />
    </div>
  );
}

// --------------------------------------------------------------------------- bewerken

function ItemEditor({
  initial,
  folders,
  userKey,
  onCancel,
  onSaved,
}: {
  initial: ItemView;
  folders: FolderView[];
  userKey: UserKey;
  onCancel: () => void;
  onSaved: (id: string) => void;
}) {
  const { request } = useWebVault();
  const qc = useQueryClient();
  const [v, setV] = useState<ItemView>(() => structuredClone({ ...initial, raw: null }));
  const isNew = !initial.id;
  const set = (patch: Partial<ItemView>) => setV((cur) => ({ ...cur, ...patch }));
  const setLogin = (patch: Partial<ItemView["login"]>) => setV((cur) => ({ ...cur, login: { ...cur.login, ...patch } }));
  const setCard = (patch: Partial<ItemView["card"]>) => setV((cur) => ({ ...cur, card: { ...cur.card, ...patch } }));

  const save = useMutation({
    mutationFn: async () => {
      const full = withPasswordHistory({ ...v, raw: initial.raw }, isNew ? null : initial);
      const body = await encryptCipher(full, userKey);
      const res = isNew
        ? await request<RawCipher>("POST", "/api/ciphers", body)
        : await request<RawCipher>("PUT", `/api/ciphers/${initial.id}`, body);
      await qc.invalidateQueries({ queryKey: SYNC_KEY });
      return res.id;
    },
    onSuccess: onSaved,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="form wv-editor" onSubmit={submit}>
      <div className="wv-view-head">
        <h2>{isNew ? `Nieuw item: ${CIPHER_TYPES[v.type].toLowerCase()}` : `${v.name || "Item"} bewerken`}</h2>
      </div>
      <div className="grid-form">
        <label className="field">
          <span>Naam</span>
          <input required autoFocus={isNew} value={v.name} onChange={(e) => set({ name: e.target.value })} />
        </label>
        <label className="field">
          <span>Map</span>
          <select aria-label="Map" value={v.folderId ?? ""} onChange={(e) => set({ folderId: e.target.value || null })}>
            <option value="">Geen map</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {v.type === 1 && (
        <>
          <label className="field">
            <span>Gebruikersnaam</span>
            <input autoComplete="off" value={v.login.username} onChange={(e) => setLogin({ username: e.target.value })} />
          </label>
          <PasswordInput value={v.login.password} onChange={(password) => setLogin({ password })} />
          <label className="field">
            <span>TOTP-sleutel (base32 of otpauth://)</span>
            <input autoComplete="off" className="mono" value={v.login.totp} onChange={(e) => setLogin({ totp: e.target.value })} />
          </label>
          <div className="field">
            <span>Websites</span>
            {v.login.uris.map((u, n) => (
              <div key={n} className="row">
                <input
                  aria-label={`Website ${n + 1}`}
                  placeholder="https://"
                  value={u.uri}
                  onChange={(e) =>
                    setLogin({ uris: v.login.uris.map((x, i) => (i === n ? { ...x, uri: e.target.value } : x)) })
                  }
                />
                <button
                  type="button"
                  className="btn btn-ghost"
                  aria-label={`Website ${n + 1} weghalen`}
                  onClick={() => setLogin({ uris: v.login.uris.filter((_, i) => i !== n) })}
                >
                  ×
                </button>
              </div>
            ))}
            <div>
              <button type="button" className="btn-link" onClick={() => setLogin({ uris: [...v.login.uris, { uri: "", match: null }] })}>
                + website
              </button>
            </div>
          </div>
        </>
      )}

      {v.type === 3 && (
        <div className="grid-form">
          <label className="field">
            <span>Kaarthouder</span>
            <input value={v.card.cardholderName} onChange={(e) => setCard({ cardholderName: e.target.value })} />
          </label>
          <label className="field">
            <span>Merk</span>
            <select aria-label="Merk" value={v.card.brand} onChange={(e) => setCard({ brand: e.target.value })}>
              {[...new Set(["", "Visa", "Mastercard", "Amex", "Maestro", "Bancontact", "Discover", "Other", v.card.brand])].map((b) => (
                <option key={b} value={b}>
                  {b === "" ? "—" : b === "Other" ? "Andere" : b}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Kaartnummer</span>
            <input autoComplete="off" className="mono" value={v.card.number} onChange={(e) => setCard({ number: e.target.value })} />
          </label>
          <label className="field">
            <span>Beveiligingscode</span>
            <input autoComplete="off" className="mono" value={v.card.code} onChange={(e) => setCard({ code: e.target.value })} />
          </label>
          <label className="field">
            <span>Vervalmaand</span>
            <select aria-label="Vervalmaand" value={v.card.expMonth} onChange={(e) => setCard({ expMonth: e.target.value })}>
              <option value="">—</option>
              {Array.from({ length: 12 }, (_, i) => String(i + 1)).map((m) => (
                <option key={m} value={m}>
                  {m.padStart(2, "0")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Vervaljaar</span>
            <input inputMode="numeric" value={v.card.expYear} onChange={(e) => setCard({ expYear: e.target.value })} />
          </label>
        </div>
      )}

      {v.type === 4 && (
        <div className="grid-form">
          {IDENTITY_FIELDS.map(([k, label]) => (
            <label key={k} className="field">
              <span>{label}</span>
              <input value={v.identity[k]} onChange={(e) => set({ identity: { ...v.identity, [k]: e.target.value } })} />
            </label>
          ))}
        </div>
      )}

      {v.type === 5 && (
        <>
          <label className="field">
            <span>Private sleutel</span>
            <textarea rows={5} className="mono" value={v.sshKey.privateKey} onChange={(e) => set({ sshKey: { ...v.sshKey, privateKey: e.target.value } })} />
          </label>
          <label className="field">
            <span>Publieke sleutel</span>
            <textarea rows={2} className="mono" value={v.sshKey.publicKey} onChange={(e) => set({ sshKey: { ...v.sshKey, publicKey: e.target.value } })} />
          </label>
          <label className="field">
            <span>Vingerafdruk</span>
            <input className="mono" value={v.sshKey.keyFingerprint} onChange={(e) => set({ sshKey: { ...v.sshKey, keyFingerprint: e.target.value } })} />
          </label>
        </>
      )}

      <label className="field">
        <span>Notities</span>
        <textarea rows={v.type === 2 ? 10 : 4} value={v.notes} onChange={(e) => set({ notes: e.target.value })} />
      </label>

      <div className="field">
        <span>Eigen velden</span>
        {v.fields.map((f, n) => {
          const update = (patch: Partial<typeof f>) => set({ fields: v.fields.map((x, i) => (i === n ? { ...x, ...patch } : x)) });
          return (
            <div key={n} className="row">
              <select aria-label={`Soort veld ${n + 1}`} value={f.type} onChange={(e) => update({ type: Number(e.target.value), value: "" })}>
                {Object.entries(FIELD_TYPES).map(([t, label]) => (
                  <option key={t} value={t}>
                    {label}
                  </option>
                ))}
                {f.type === 3 && <option value={3}>Gekoppeld</option>}
              </select>
              <input aria-label={`Naam veld ${n + 1}`} placeholder="Naam" value={f.name} onChange={(e) => update({ name: e.target.value })} />
              {f.type === 2 ? (
                <input
                  type="checkbox"
                  aria-label={`Waarde veld ${n + 1}`}
                  checked={f.value === "true"}
                  onChange={(e) => update({ value: e.target.checked ? "true" : "false" })}
                />
              ) : (
                <input
                  aria-label={`Waarde veld ${n + 1}`}
                  placeholder="Waarde"
                  type={f.type === 1 ? "password" : "text"}
                  autoComplete="off"
                  value={f.value}
                  disabled={f.type === 3}
                  onChange={(e) => update({ value: e.target.value })}
                />
              )}
              <button type="button" className="btn btn-ghost" aria-label={`Veld ${n + 1} weghalen`} onClick={() => set({ fields: v.fields.filter((_, i) => i !== n) })}>
                ×
              </button>
            </div>
          );
        })}
        <div>
          <button type="button" className="btn-link" onClick={() => set({ fields: [...v.fields, { name: "", value: "", type: 0, linkedId: null }] })}>
            + veld
          </button>
        </div>
      </div>

      <label className="check">
        <input type="checkbox" checked={v.favorite} onChange={(e) => set({ favorite: e.target.checked })} />
        Favoriet
      </label>
      <label className="check">
        <input type="checkbox" checked={v.reprompt} onChange={(e) => set({ reprompt: e.target.checked })} />
        Master password opnieuw vragen om dit item te bekijken
      </label>

      <ErrorBox error={save.error} />
      <div className="form-actions row">
        <button type="button" className="btn" onClick={onCancel}>
          Annuleren
        </button>
        <button className="btn btn-primary" disabled={save.isPending || !v.name.trim()}>
          {save.isPending ? "Versleutelen en opslaan…" : "Opslaan"}
        </button>
      </div>
    </form>
  );
}

function PasswordInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [shown, setShown] = useState(false);
  const [opts, setOpts] = useState<GeneratorOptions>(GENERATOR_DEFAULTS);
  const [showOpts, setShowOpts] = useState(false);
  const generate = (o = opts) => {
    onChange(generatePassword(o));
    setShown(true);
  };
  const setOpt = (patch: Partial<GeneratorOptions>) => {
    const next = { ...opts, ...patch };
    setOpts(next);
    generate(next);
  };
  return (
    <div className="field">
      <span>Wachtwoord</span>
      <div className="row">
        <input
          aria-label="Wachtwoord"
          type={shown ? "text" : "password"}
          autoComplete="new-password"
          className="mono"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button type="button" className="btn btn-ghost" onClick={() => setShown(!shown)}>
          {shown ? "Verbergen" : "Tonen"}
        </button>
        <button type="button" className="btn" onClick={() => generate()}>
          Genereren
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => setShowOpts(!showOpts)} aria-expanded={showOpts}>
          ⚙
        </button>
      </div>
      {showOpts && (
        <div className="row wrap wv-gen small">
          <label className="row">
            Lengte
            <input
              type="number"
              min={8}
              max={128}
              value={opts.length}
              onChange={(e) => setOpt({ length: Number(e.target.value) })}
              style={{ width: 72 }}
            />
          </label>
          {(
            [
              ["upper", "A-Z"],
              ["lower", "a-z"],
              ["digits", "0-9"],
              ["symbols", "!@#$"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} className="check">
              <input type="checkbox" checked={opts[k]} onChange={(e) => setOpt({ [k]: e.target.checked })} />
              {label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- mappen

function FolderDialog({
  folder,
  userKey,
  onClose,
  onDeleted,
}: {
  folder: FolderView | "new" | null;
  userKey: UserKey;
  onClose: () => void;
  onDeleted: (id: string) => void;
}) {
  const { request } = useWebVault();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const existing = folder && folder !== "new" ? folder : null;
  useEffect(() => setName(existing?.name ?? ""), [folder, existing?.name]);
  const done = async () => {
    await qc.invalidateQueries({ queryKey: SYNC_KEY });
    onClose();
  };
  const save = useMutation({
    mutationFn: async () => {
      const body = { name: await encryptText(name.trim(), userKey) };
      if (existing) await request("PUT", `/api/folders/${existing.id}`, body);
      else await request("POST", "/api/folders", body);
    },
    onSuccess: done,
  });
  const remove = useMutation({
    mutationFn: async () => {
      if (!existing) return;
      await request("DELETE", `/api/folders/${existing.id}`);
      onDeleted(existing.id);
    },
    onSuccess: done,
  });
  return (
    <Modal title={existing ? "Map bewerken" : "Nieuwe map"} open={folder !== null} onClose={onClose}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="field">
          <span>Naam</span>
          <input autoFocus required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <ErrorBox error={save.error ?? remove.error} />
        <div className="form-actions row">
          {existing && (
            <button
              type="button"
              className="btn btn-ghost btn-danger-text"
              disabled={remove.isPending}
              onClick={() => confirm(`Map "${existing.name}" verwijderen? De items blijven bestaan, zonder map.`) && remove.mutate()}
            >
              Verwijderen
            </button>
          )}
          <button className="btn btn-primary" disabled={save.isPending || !name.trim()}>
            Opslaan
          </button>
        </div>
      </form>
    </Modal>
  );
}
