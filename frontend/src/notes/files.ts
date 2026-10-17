// Bijlagen bij notities: afbeeldingen, pdf's en andere bestanden. De browser versleutelt
// inhoud en naam met de user key; de server bewaart enkel blobs. Ontsleutelde afbeeldingen
// worden als data:-URL getoond (de CSP van VaultX laat geen blob:-afbeeldingen toe).

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { EncryptedNoteFile, EncryptedNoteFiles } from "../api/types";
export { looksLikeFile } from "./model";
import { decryptBytes, decryptText, encryptBytes, encryptText, type UserKey } from "../vault/crypto";

const BASE = "/api/v1/me/notes/files";

export interface NoteFile {
  id: string;
  /** Bestandsnaam, eventueel met map (bv. "bijlagen/schema.png" na een Obsidian-import). */
  name: string;
  /** Grootte van de versleutelde blob (≈ het bestand + 65 bytes). */
  size: number;
  created_at: string;
}

const MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  bmp: "image/bmp",
  ico: "image/x-icon",
  svg: "image/svg+xml",
  pdf: "application/pdf",
  txt: "text/plain",
  csv: "text/csv",
  json: "application/json",
  zip: "application/zip",
  mp3: "audio/mpeg",
  mp4: "video/mp4",
};

export const extOf = (name: string) => /\.([a-z0-9]+)$/i.exec(name)?.[1].toLowerCase() ?? "";
export const mimeOf = (name: string) => MIME[extOf(name)] ?? "application/octet-stream";
export const isImage = (name: string) => mimeOf(name).startsWith("image/");
export const baseName = (name: string) => name.split("/").pop() ?? name;

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} kB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/** Wat de Markdown-weergave nodig heeft om bijlagen te tonen. */
export interface FileResolver {
  find: (name: string) => NoteFile | null;
  dataUrl: (id: string) => string | undefined;
  /** Vraagt het ontsleutelen aan; de weergave wordt opnieuw opgebouwd zodra het klaar is. */
  request: (id: string) => void;
  /** Laden of ontsleutelen mislukt. */
  broken: (id: string) => boolean;
}

function toDataUrl(bytes: Uint8Array<ArrayBuffer>, type: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(new Blob([bytes], { type }));
  });
}

export function useNoteFiles(key: UserKey) {
  const [files, setFiles] = useState<NoteFile[] | null>(null);
  const [usage, setUsage] = useState({ used: 0, quota: 0, max: 25 * 1024 * 1024 });
  const [loaded, setLoaded] = useState(0);
  const urls = useRef(new Map<string, string>());
  const failed = useRef(new Set<string>());
  const pending = useRef(new Set<string>());
  const ref = useRef<NoteFile[]>([]);

  const set = (list: NoteFile[]) => {
    ref.current = list;
    setFiles(list);
  };

  const reload = useCallback(async () => {
    const res = await api.get<EncryptedNoteFiles>(BASE);
    const opened = await Promise.allSettled(
      res.files.map(async (f: EncryptedNoteFile) => ({ ...f, name: await decryptText(f.name, key) })),
    );
    set(opened.flatMap((r) => (r.status === "fulfilled" ? [r.value] : [])));
    setUsage({ used: res.used_bytes, quota: res.quota_bytes, max: res.max_file_bytes });
  }, [key]);

  useEffect(() => {
    reload().catch(() => set([]));
  }, [reload]);

  const find = useCallback(
    (name: string): NoteFile | null => {
      const n = name.trim().toLowerCase();
      const list = ref.current;
      return list.find((f) => f.name.toLowerCase() === n) ?? list.find((f) => baseName(f.name).toLowerCase() === baseName(n)) ?? null;
    },
    // files als afhankelijkheid: een nieuwe find na elke wijziging laat de weergave opnieuw renderen
    [files],
  );

  /** Ontsleutelde inhoud van een bijlage. */
  const bytes = useCallback(
    async (id: string): Promise<Uint8Array<ArrayBuffer>> => {
      const res = await fetch(`${BASE}/${id}`, { credentials: "same-origin" });
      if (!res.ok) throw new ApiError(res.status, "error", res.status === 404 ? "Bijlage niet gevonden" : res.statusText);
      return decryptBytes(new Uint8Array(await res.arrayBuffer()), key);
    },
    [key],
  );

  const request = useCallback(
    (id: string) => {
      if (urls.current.has(id) || pending.current.has(id) || failed.current.has(id)) return;
      pending.current.add(id);
      const f = ref.current.find((x) => x.id === id);
      void bytes(id)
        .then((b) => toDataUrl(b, mimeOf(f?.name ?? "")))
        .then((u) => urls.current.set(id, u))
        .catch(() => failed.current.add(id))
        .finally(() => {
          pending.current.delete(id);
          setLoaded((x) => x + 1);
        });
    },
    [bytes],
  );

  const dataUrl = useCallback((id: string) => urls.current.get(id), []);
  const broken = useCallback((id: string) => failed.current.has(id), []);

  /** Vrije naam: "schema.png", anders "schema 1.png", "schema 2.png", ... */
  const uniqueName = useCallback((name: string) => {
    const taken = new Set(ref.current.map((f) => f.name.toLowerCase()));
    if (!taken.has(name.toLowerCase())) return name;
    const m = /^(.*?)(\.[^.]*)?$/.exec(name)!;
    for (let i = 1; ; i++) {
      const candidate = `${m[1]} ${i}${m[2] ?? ""}`;
      if (!taken.has(candidate.toLowerCase())) return candidate;
    }
  }, []);

  const upload = useCallback(
    async (blob: Blob, wanted: string): Promise<NoteFile> => {
      if (blob.size > usage.max) throw new Error(`${wanted} is groter dan ${formatSize(usage.max)}`);
      const name = uniqueName(wanted);
      const plain = new Uint8Array(await blob.arrayBuffer());
      const sealed = await encryptBytes(plain, key);
      const res = await fetch(BASE, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/octet-stream",
          "X-VaultX-CSRF": "1",
          "X-VaultX-File-Name": await encryptText(name, key),
        },
        body: sealed,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = res.status === 413 && typeof data.detail !== "string" ? "Bestand te groot voor de server" : data.detail;
        throw new ApiError(res.status, data.error ?? "error", typeof detail === "string" ? detail : res.statusText);
      }
      const f: NoteFile = { ...(data as EncryptedNoteFile), name };
      // Zelf opgeladen: de inhoud is al bekend, dus meteen tonen zonder opnieuw te downloaden.
      if (isImage(name)) urls.current.set(f.id, await toDataUrl(plain, mimeOf(name)));
      set([...ref.current, f]);
      setUsage((u) => ({ ...u, used: u.used + f.size }));
      return f;
    },
    [key, uniqueName, usage.max],
  );

  const remove = useCallback(async (id: string) => {
    await api.delete(`${BASE}/${id}`);
    const f = ref.current.find((x) => x.id === id);
    urls.current.delete(id);
    set(ref.current.filter((x) => x.id !== id));
    if (f) setUsage((u) => ({ ...u, used: Math.max(0, u.used - f.size) }));
  }, []);

  return { files, usage, loaded, find, dataUrl, broken, request, bytes, upload, remove, uniqueName, reload };
}

export type NoteFiles = ReturnType<typeof useNoteFiles>;
