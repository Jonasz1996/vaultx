// Mermaid-diagrammen in notities. Mermaid wordt pas geladen als een notitie er een bevat.
// Het resultaat (SVG) wordt als data:-afbeelding getoond: de CSP van VaultX laat geen
// inline stijlen toe, en binnen een afbeelding gelden die regels niet. Een SVG in een
// <img> kan bovendien geen scripts uitvoeren of iets laden.

import { useCallback, useRef, useState } from "react";

export interface DiagramResolver {
  /** data:-URL van het diagram, of een foutmelding van Mermaid. */
  get: (src: string) => { url: string } | { error: string } | undefined;
  request: (src: string) => void;
}

let counter = 0;
type MermaidApi = (typeof import("mermaid"))["default"];
let loading: Promise<MermaidApi> | null = null;

function loadMermaid(): Promise<MermaidApi> {
  loading ??= import("mermaid").then(({ default: mermaid }) => {
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: dark ? "dark" : "default",
      htmlLabels: false,
      flowchart: { htmlLabels: false },
      fontFamily: "system-ui, sans-serif",
    });
    return mermaid;
  });
  return loading;
}

function svgToDataUrl(svg: string): string {
  // Mermaid geeft width="100%" en een max-width-stijl; als afbeelding is een vaste maat
  // (uit de viewBox) nodig, anders wordt het diagram piepklein.
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = doc.documentElement;
  const vb = root.getAttribute("viewBox")?.split(/[\s,]+/).map(Number);
  if (vb && vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
    root.setAttribute("width", String(Math.ceil(vb[2])));
    root.setAttribute("height", String(Math.ceil(vb[3])));
    root.removeAttribute("style");
  }
  const bytes = new TextEncoder().encode(new XMLSerializer().serializeToString(root));
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:image/svg+xml;base64,${btoa(bin)}`;
}

export function useDiagrams() {
  const cache = useRef(new Map<string, { url: string } | { error: string }>());
  const pending = useRef(new Set<string>());
  const [version, setVersion] = useState(0);

  const get = useCallback((src: string) => cache.current.get(src), []);

  const request = useCallback((src: string) => {
    if (cache.current.has(src) || pending.current.has(src)) return;
    pending.current.add(src);
    // Niet tijdens het renderen zelf beginnen.
    queueMicrotask(async () => {
      try {
        const mermaid = await loadMermaid();
        const { svg } = await mermaid.render(`vaultx-mermaid-${++counter}`, src);
        cache.current.set(src, { url: svgToDataUrl(svg) });
      } catch (e) {
        cache.current.set(src, { error: e instanceof Error ? e.message : String(e) });
        // Mermaid laat bij een fout soms een leeg element achter in de pagina.
        document.querySelectorAll(`[id^="dvaultx-mermaid-"]`).forEach((el) => el.remove());
      } finally {
        pending.current.delete(src);
        setVersion((v) => v + 1);
      }
    });
  }, []);

  return { get, request, version };
}
