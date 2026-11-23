// Notities laden, ontsleutelen en versleuteld terugschrijven. Alles wat de server krijgt,
// gaat eerst door encryptText met de user key van de ontgrendelde kluis.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { decryptText, encryptText } from "../vault/crypto";
import { docOf, parseDoc } from "./model";
const BASE = "/api/v1/me/notes";
/**
 * Ontsleutelt een notitie en controleert dat ze bij dit id hoort. Het id zit mee in de
 * versleutelde inhoud, dus de server kan de inhoud van twee notities niet ongemerkt
 * omwisselen. Oudere notities zonder id krijgen het bij de volgende keer bewaren.
 */
async function openDoc(content, id, key) {
    const json = await decryptText(content, key);
    const bound = JSON.parse(json).id;
    if (bound !== undefined && bound !== id)
        throw new Error("Deze inhoud hoort bij een andere notitie");
    return parseDoc(json);
}
async function open(e, key) {
    const doc = await openDoc(e.content, e.id, key);
    return { ...doc, id: e.id, revision: e.revision, created_at: e.created_at, updated_at: e.updated_at, deleted_at: e.deleted_at };
}
async function seal(doc, id, key) {
    return encryptText(JSON.stringify({ ...docOf(doc), id }), key);
}
export function useNotesStore(key) {
    const [notes, setNotes] = useState(null);
    const [loadError, setLoadError] = useState(null);
    const [unreadable, setUnreadable] = useState(0);
    const ref = useRef(new Map());
    const queues = useRef(new Map());
    const put = useCallback((list) => {
        const next = new Map(ref.current);
        for (const n of list)
            next.set(n.id, n);
        ref.current = next;
        setNotes(next);
    }, []);
    const drop = useCallback((id) => {
        const next = new Map(ref.current);
        next.delete(id);
        ref.current = next;
        setNotes(next);
    }, []);
    const reload = useCallback(async () => {
        try {
            const enc = await api.get(BASE);
            const results = await Promise.allSettled(enc.map((e) => open(e, key)));
            const ok = results.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
            setUnreadable(results.length - ok.length);
            ref.current = new Map(ok.map((n) => [n.id, n]));
            setNotes(ref.current);
            setLoadError(null);
        }
        catch (e) {
            setLoadError(e);
        }
    }, [key]);
    useEffect(() => {
        void reload();
    }, [reload]);
    /** Voert acties op dezelfde notitie na elkaar uit, zodat revisies niet door elkaar lopen. */
    const serial = useCallback((id, fn) => {
        const prev = queues.current.get(id) ?? Promise.resolve();
        const run = prev.then(fn, fn);
        queues.current.set(id, run.catch(() => undefined));
        return run;
    }, []);
    const create = useCallback(async (doc) => {
        const id = crypto.randomUUID();
        const n = await open(await api.post(BASE, { id, content: await seal(doc, id, key) }), key);
        put([n]);
        return n;
    }, [key, put]);
    const save = useCallback((id, doc, baseRevision) => serial(id, async () => {
        const current = ref.current.get(id);
        if (!current)
            throw new Error("Notitie niet gevonden");
        try {
            const res = await fetchPut(`${BASE}/${id}`, {
                content: await seal(doc, id, key),
                base_revision: baseRevision ?? current.revision,
            });
            const n = await open(res, key);
            put([n]);
            return { ok: true, note: n };
        }
        catch (e) {
            if (e instanceof ConflictError) {
                const n = await open(e.current, key);
                put([n]);
                return { ok: false, current: n };
            }
            throw e;
        }
    }), [key, put, serial]);
    /**
     * Wijzigt een notitie op basis van haar laatst bewaarde versie (bv. links bijwerken na
     * hernoemen). De wijziging wordt pas berekend als eerdere bewaaracties klaar zijn, zodat
     * ze nooit op een verouderde tekst gebeurt. Bij een conflict wordt opnieuw geprobeerd.
     */
    const update = useCallback((id, change) => serial(id, async () => {
        for (let attempt = 0; attempt < 3; attempt++) {
            const current = ref.current.get(id);
            if (!current)
                return false;
            const next = change(docOf(current));
            if (JSON.stringify(docOf(next)) === JSON.stringify(docOf(current)))
                return false;
            try {
                put([await open(await fetchPut(`${BASE}/${id}`, { content: await seal(next, id, key), base_revision: current.revision }), key)]);
                return true;
            }
            catch (e) {
                if (!(e instanceof ConflictError))
                    throw e;
                put([await open(e.current, key)]);
            }
        }
        throw new Error("Notitie werd telkens tegelijk elders gewijzigd");
    }), [key, put, serial]);
    const trash = useCallback((id) => serial(id, async () => put([await open(await api.post(`${BASE}/${id}/trash`), key)])), [key, put, serial]);
    const restore = useCallback((id) => serial(id, async () => put([await open(await api.post(`${BASE}/${id}/restore`), key)])), [key, put, serial]);
    const remove = useCallback((id) => serial(id, async () => {
        await api.delete(`${BASE}/${id}`);
        drop(id);
    }), [drop, serial]);
    const importMany = useCallback(async (docs, onProgress) => {
        const created = [];
        const sealed = await Promise.all(docs.map(async (d) => {
            const id = crypto.randomUUID();
            return { id, content: await seal(d, id, key) };
        }));
        // Per verzoek hoogstens 100 notities en ongeveer 8 MB, ruim onder de limiet van nginx.
        for (let i = 0; i < sealed.length;) {
            const chunk = [sealed[i++]];
            let bytes = chunk[0].content.length;
            while (i < sealed.length && chunk.length < 100 && bytes + sealed[i].content.length < 8_000_000) {
                bytes += sealed[i].content.length;
                chunk.push(sealed[i++]);
            }
            const res = await api.post(`${BASE}/import`, { notes: chunk });
            const opened = await Promise.all(res.map((e) => open(e, key)));
            put(opened);
            created.push(...opened);
            onProgress?.(created.length);
        }
        return created;
    }, [key, put]);
    const versions = useCallback(async (id) => {
        const list = await api.get(`${BASE}/${id}/versions`);
        return Promise.all(list.map(async (v) => ({ ...(await openDoc(v.content, id, key)), revision: v.revision, saved_at: v.saved_at })));
    }, [key]);
    return { notes, loadError, unreadable, reload, create, save, update, trash, restore, remove, importMany, versions };
}
class ConflictError extends Error {
    current;
    constructor(current) {
        super("Intussen elders gewijzigd");
        this.current = current;
    }
}
/** PUT met de 409-body erbij (de gewone client geeft enkel de foutmelding door). */
async function fetchPut(path, body) {
    const res = await fetch(path, {
        method: "PUT",
        headers: { Accept: "application/json", "Content-Type": "application/json", "X-VaultX-CSRF": "1" },
        credentials: "same-origin",
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 409 && data.current)
        throw new ConflictError(data.current);
    if (!res.ok)
        throw new ApiError(res.status, data.error ?? "error", typeof data.detail === "string" ? data.detail : res.statusText);
    return data;
}
