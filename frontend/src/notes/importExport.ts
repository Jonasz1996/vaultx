// Importeren uit en exporteren naar een gewone map met Markdown-bestanden (zoals een
// Obsidian-kluis). Alles gebeurt in de browser; de server krijgt enkel versleutelde notities.

import { zipSync, strToU8 } from "fflate";
import { cleanFolder, cleanTitle, emptyDoc, isoDate, notePath, type Note, type NoteDoc, type NoteIndex } from "./model";

export interface ImportPlan {
  docs: NoteDoc[];
  skipped: { name: string; reason: string }[];
}

const MAX_NOTE_BYTES = 1_000_000;

/**
 * Leest .md-bestanden. Bij een map (webkitdirectory) wordt de mapnaam van de kluis zelf
 * weggelaten en worden .obsidian, .trash en andere verborgen mappen overgeslagen.
 */
export async function planImport(files: File[], index: NoteIndex): Promise<ImportPlan> {
  const skipped: ImportPlan["skipped"] = [];
  const docs: NoteDoc[] = [];
  const taken = new Set(index.notes.map((n) => notePath(n).toLowerCase()));
  for (const f of files) {
    const rel = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name;
    const parts = rel.split("/");
    if (parts.length > 1 && (f as File & { webkitRelativePath?: string }).webkitRelativePath) parts.shift();
    if (parts.some((p) => p.startsWith("."))) continue;
    const name = parts.pop()!;
    if (!/\.(md|markdown|txt)$/i.test(name)) {
      skipped.push({ name: rel, reason: "geen Markdown (bijlagen volgen later)" });
      continue;
    }
    if (f.size > MAX_NOTE_BYTES) {
      skipped.push({ name: rel, reason: "groter dan 1 MB" });
      continue;
    }
    const title = cleanTitle(name.replace(/\.(md|markdown|txt)$/i, "")) || "Naamloos";
    const folder = cleanFolder(parts.join("/"));
    const key = notePath({ title, folder }).toLowerCase();
    if (taken.has(key)) {
      skipped.push({ name: rel, reason: "bestaat al" });
      continue;
    }
    taken.add(key);
    docs.push(emptyDoc(title, folder, await f.text()));
  }
  return { docs, skipped };
}

const safeName = (s: string) => s.replace(/[\\/:*?"<>|]/g, "-").trim() || "Naamloos";

/** Zip met één .md-bestand per notitie, in de mappenstructuur van de notities. */
export function exportZip(notes: Note[]): Blob {
  const files: Record<string, Uint8Array> = {};
  for (const n of notes.filter((x) => !x.deleted_at)) {
    const dir = n.folder ? n.folder.split("/").map(safeName).join("/") + "/" : "";
    let path = `${dir}${safeName(n.title)}.md`;
    for (let i = 1; files[path]; i++) path = `${dir}${safeName(n.title)} ${i}.md`;
    files[path] = strToU8(n.body);
  }
  const zip = zipSync(files, { level: 6 });
  return new Blob([zip], { type: "application/zip" });
}

export function download(blob: Blob, name = `vaultx-notities-${isoDate()}.zip`) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
