// Notities laden, ontsleutelen en versleuteld terugschrijven. Alles wat de server krijgt,
// gaat eerst door encryptText met de user key van de ontgrendelde kluis.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { EncryptedNote, EncryptedNoteVersion } from "../api/types";
import { decryptText, encryptText, type UserKey } from "../vault/crypto";
import { docOf, parseDoc, type Note, type NoteDoc } from "./model";

const BASE = "/api/v1/me/notes";

export type SaveResult = { ok: true; note: Note } | { ok: false; current: Note };

export interface NoteVersion extends NoteDoc {
  revision: number;
  saved_at: string;
}

/**
 * Ontsleutelt een notitie en controleert dat ze bij dit id hoort. Het id zit mee in de
 * versleutelde inhoud, dus de server kan de inhoud van twee notities niet ongemerkt
 * omwisselen. Oudere notities zonder id krijgen het bij de volgende keer bewaren.
 */
async function openDoc(content: string, id: string, key: UserKey): Promise<NoteDoc> {
  const json = await decryptText(content, key);
  const bound = (JSON.parse(json) as { id?: unknown }).id;
  if (bound !== undefined && bound !== id) throw new Error("Deze inhoud hoort bij een andere notitie");
  return parseDoc(json);
}

async function open(e: EncryptedNote, key: UserKey): Promise<Note> {
  const doc = await openDoc(e.content, e.id, key);
  return { ...doc, id: e.id, revision: e.revision, created_at: e.created_at, updated_at: e.updated_at, deleted_at: e.deleted_at };
}

async function seal(doc: NoteDoc, id: string, key: UserKey): Promise<string> {
  return encryptText(JSON.stringify({ ...docOf(doc), id }), key);
}

export function useNotesStore(key: UserKey) {
  const [notes, setNotes] = useState<Map<string, Note> | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [unreadable, setUnreadable] = useState(0);
  const ref = useRef(new Map<string, Note>());
  const queues = useRef(new Map<string, Promise<unknown>>());

  const put = useCallback((list: Note[]) => {
    const next = new Map(ref.current);
    for (const n of list) next.set(n.id, n);
    ref.current = next;
    setNotes(next);
  }, []);

  const drop = useCallback((id: string) => {
    const next = new Map(ref.current);
    next.delete(id);
    ref.current = next;
    setNotes(next);
  }, []);

  const reload = useCallback(async () => {
    try {
      const enc = await api.get<EncryptedNote[]>(BASE);
      const results = await Promise.allSettled(enc.map((e) => open(e, key)));
      const ok = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
      setUnreadable(results.length - ok.length);
      ref.current = new Map(ok.map((n) => [n.id, n]));
      setNotes(ref.current);
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, [key]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Voert acties op dezelfde notitie na elkaar uit, zodat revisies niet door elkaar lopen. */
  const serial = useCallback(<T,>(id: string, fn: () => Promise<T>): Promise<T> => {
    const prev = queues.current.get(id) ?? Promise.resolve();
    const run = prev.then(fn, fn);
    queues.current.set(
      id,
      run.catch(() => undefined),
    );
    return run;
  }, []);

  const create = useCallback(
    async (doc: NoteDoc): Promise<Note> => {
      const id = crypto.randomUUID();
      const n = await open(await api.post<EncryptedNote>(BASE, { id, content: await seal(doc, id, key) }), key);
      put([n]);
      return n;
    },
    [key, put],
  );

  const save = useCallback(
    (id: string, doc: NoteDoc, baseRevision?: number): Promise<SaveResult> =>
      serial(id, async () => {
        const current = ref.current.get(id);
        if (!current) throw new Error("Notitie niet gevonden");
        try {
          const res = await fetchPut(`${BASE}/${id}`, {
            content: await seal(doc, id, key),
            base_revision: baseRevision ?? current.revision,
          });
          const n = await open(res, key);
          put([n]);
          return { ok: true as const, note: n };
        } catch (e) {
          if (e instanceof ConflictError) {
            const n = await open(e.current, key);
            put([n]);
            return { ok: false as const, current: n };
          }
          throw e;
        }
      }),
    [key, put, serial],
  );

  /**
   * Wijzigt een notitie op basis van haar laatst bewaarde versie (bv. links bijwerken na
   * hernoemen). De wijziging wordt pas berekend als eerdere bewaaracties klaar zijn, zodat
   * ze nooit op een verouderde tekst gebeurt. Bij een conflict wordt opnieuw geprobeerd.
   */
  const update = useCallback(
    (id: string, change: (doc: NoteDoc) => NoteDoc): Promise<boolean> =>
      serial(id, async () => {
        for (let attempt = 0; attempt < 3; attempt++) {
          const current = ref.current.get(id);
          if (!current) return false;
          const next = change(docOf(current));
          if (JSON.stringify(docOf(next)) === JSON.stringify(docOf(current))) return false;
          try {
            put([await open(await fetchPut(`${BASE}/${id}`, { content: await seal(next, id, key), base_revision: current.revision }), key)]);
            return true;
          } catch (e) {
            if (!(e instanceof ConflictError)) throw e;
            put([await open(e.current, key)]);
          }
        }
        throw new Error("Notitie werd telkens tegelijk elders gewijzigd");
      }),
    [key, put, serial],
  );

  const trash = useCallback(
    (id: string) => serial(id, async () => put([await open(await api.post<EncryptedNote>(`${BASE}/${id}/trash`), key)])),
    [key, put, serial],
  );
  const restore = useCallback(
    (id: string) =>
      serial(id, async () => put([await open(await api.post<EncryptedNote>(`${BASE}/${id}/restore`), key)])),
    [key, put, serial],
  );
  const remove = useCallback(
    (id: string) =>
      serial(id, async () => {
        await api.delete(`${BASE}/${id}`);
        drop(id);
      }),
    [drop, serial],
  );

  const importMany = useCallback(
    async (docs: NoteDoc[], onProgress?: (done: number) => void): Promise<Note[]> => {
      const created: Note[] = [];
      const sealed = await Promise.all(
        docs.map(async (d) => {
          const id = crypto.randomUUID();
          return { id, content: await seal(d, id, key) };
        }),
      );
      // Per verzoek hoogstens 100 notities en ongeveer 8 MB, ruim onder de limiet van nginx.
      for (let i = 0; i < sealed.length; ) {
        const chunk = [sealed[i++]];
        let bytes = chunk[0].content.length;
        while (i < sealed.length && chunk.length < 100 && bytes + sealed[i].content.length < 8_000_000) {
          bytes += sealed[i].content.length;
          chunk.push(sealed[i++]);
        }
        const res = await api.post<EncryptedNote[]>(`${BASE}/import`, { notes: chunk });
        const opened = await Promise.all(res.map((e) => open(e, key)));
        put(opened);
        created.push(...opened);
        onProgress?.(created.length);
      }
      return created;
    },
    [key, put],
  );

  const versions = useCallback(
    async (id: string): Promise<NoteVersion[]> => {
      const list = await api.get<EncryptedNoteVersion[]>(`${BASE}/${id}/versions`);
      return Promise.all(
        list.map(async (v) => ({ ...(await openDoc(v.content, id, key)), revision: v.revision, saved_at: v.saved_at })),
      );
    },
    [key],
  );

  return { notes, loadError, unreadable, reload, create, save, update, trash, restore, remove, importMany, versions };
}

export type NotesStore = ReturnType<typeof useNotesStore>;

class ConflictError extends Error {
  constructor(public current: EncryptedNote) {
    super("Intussen elders gewijzigd");
  }
}

/** PUT met de 409-body erbij (de gewone client geeft enkel de foutmelding door). */
async function fetchPut(path: string, body: unknown): Promise<EncryptedNote> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-VaultX-CSRF": "1" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 409 && data.current) throw new ConflictError(data.current as EncryptedNote);
  if (!res.ok) throw new ApiError(res.status, data.error ?? "error", typeof data.detail === "string" ? data.detail : res.statusText);
  return data as EncryptedNote;
}
