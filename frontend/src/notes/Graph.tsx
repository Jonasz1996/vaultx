// Grafiekweergave zoals in Obsidian: notities als bollen, links als lijnen. Een kleine
// force-layout in de browser; slepen om te schuiven, scrollen om te zoomen.

import { useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { isAppLink, type NoteIndex } from "./model";

interface GNode {
  id: string;
  label: string;
  kind: "note" | "ghost" | "tag";
  group: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
  degree: number;
}

interface Options {
  local: boolean;
  depth: number;
  tags: boolean;
  ghosts: boolean;
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

function layout(nodes: GNode[], edges: [number, number][]) {
  const n = nodes.length;
  const iterations = n > 600 ? 60 : n > 250 ? 140 : 300;
  const k = 60;
  for (let it = 0; it < iterations; it++) {
    const alpha = 1 - it / iterations;
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) {
          dx = Math.random() - 0.5;
          dy = Math.random() - 0.5;
          d2 = 0.5;
        }
        if (d2 > 250_000) continue;
        const f = (k * k) / d2;
        a.vx += dx * f * 0.05;
        a.vy += dy * f * 0.05;
        b.vx -= dx * f * 0.05;
        b.vy -= dy * f * 0.05;
      }
    }
    for (const [s, t] of edges) {
      const a = nodes[s];
      const b = nodes[t];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const f = (d - k) * 0.04;
      a.vx += (dx / d) * f;
      a.vy += (dy / d) * f;
      b.vx -= (dx / d) * f;
      b.vy -= (dy / d) * f;
    }
    for (const a of nodes) {
      a.vx -= a.x * 0.01;
      a.vy -= a.y * 0.01;
      const max = 30 * alpha + 1;
      a.x += Math.max(-max, Math.min(max, a.vx));
      a.y += Math.max(-max, Math.min(max, a.vy));
      a.vx *= 0.5;
      a.vy *= 0.5;
    }
  }
}

export function GraphView({
  index,
  currentId,
  onOpen,
  onTag,
  compact = false,
}: {
  index: NoteIndex;
  currentId: string | null;
  onOpen: (id: string) => void;
  onTag?: (tag: string) => void;
  compact?: boolean;
}) {
  const [opts, setOpts] = useState<Options>({ local: compact, depth: 1, tags: false, ghosts: false });
  const [hover, setHover] = useState<number | null>(null);

  const graph = useMemo(() => {
    const ids = new Map<string, number>();
    const nodes: GNode[] = [];
    const edges: [number, number][] = [];
    const groups = new Map<string, number>();
    const add = (id: string, label: string, kind: GNode["kind"], folder = "") => {
      if (ids.has(id)) return ids.get(id)!;
      const top = folder.split("/")[0];
      if (!groups.has(top)) groups.set(top, groups.size);
      const h = hash(id);
      const r = 40 + (h % 400);
      const angle = ((h >>> 8) % 6283) / 1000;
      nodes.push({ id, label, kind, group: kind === "note" ? groups.get(top)! : -1, x: Math.cos(angle) * r, y: Math.sin(angle) * r, vx: 0, vy: 0, degree: 0 });
      ids.set(id, nodes.length - 1);
      return nodes.length - 1;
    };
    const seen = new Set<string>();
    const link = (a: number, b: number) => {
      if (a === b) return;
      const key = a < b ? `${a}-${b}` : `${b}-${a}`;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push([a, b]);
      nodes[a].degree++;
      nodes[b].degree++;
    };

    // Welke notities tonen: alles, of de buurt van de huidige notitie.
    let include: Set<string> | null = null;
    if (opts.local && currentId) {
      include = new Set([currentId]);
      for (let d = 0; d < opts.depth; d++) {
        for (const id of [...include]) {
          for (const l of index.outgoing.get(id) ?? []) {
            const t = index.resolve(l.target);
            if (t) include.add(t.id);
          }
          for (const b of index.backlinks.get(id) ?? []) include.add(b.from.id);
        }
      }
    }
    const shown = index.notes.filter((n) => !include || include.has(n.id));
    for (const n of shown) add(n.id, n.title, "note", n.folder);
    for (const n of shown) {
      const from = ids.get(n.id)!;
      for (const l of index.outgoing.get(n.id) ?? []) {
        if (!l.target || isAppLink(l.target)) continue;
        const t = index.resolve(l.target);
        if (t && ids.has(t.id)) link(from, ids.get(t.id)!);
        else if (!t && opts.ghosts) link(from, add(`ghost:${l.target.toLowerCase()}`, l.target, "ghost"));
      }
      if (opts.tags) for (const tag of index.tagsOf.get(n.id) ?? []) link(from, add(`tag:${tag}`, `#${tag}`, "tag"));
    }
    layout(nodes, edges);
    return { nodes, edges };
  }, [index, currentId, opts]);

  // Pan en zoom via de viewBox
  const bounds = useMemo(() => {
    if (!graph.nodes.length) return { x: -100, y: -100, w: 200, h: 200 };
    const xs = graph.nodes.map((n) => n.x);
    const ys = graph.nodes.map((n) => n.y);
    const pad = 60;
    // Minstens 360 x 240 eenheden, zodat een paar notities niet reusachtig worden.
    const w = Math.max(Math.max(...xs) - Math.min(...xs) + 2 * pad, 360);
    const h = Math.max(Math.max(...ys) - Math.min(...ys) + 2 * pad, 240);
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    return { x: cx - w / 2, y: cy - h / 2, w, h };
  }, [graph]);
  const [view, setView] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const vb = view ?? bounds;
  const drag = useRef<{ x: number; y: number; vb: typeof vb; moved: boolean } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  // Breedte op het scherm, zodat bollen en labels in pixels even groot blijven bij elke zoom.
  const [px, setPx] = useState(compact ? 260 : 900);
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const ro = new ResizeObserver(() => svg.clientWidth && setPx(svg.clientWidth));
    ro.observe(svg);
    return () => ro.disconnect();
  }, []);

  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scale = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    const px = vb.x + ((e.clientX - rect.left) / rect.width) * vb.w;
    const py = vb.y + ((e.clientY - rect.top) / rect.height) * vb.h;
    setView({ x: px - (px - vb.x) * scale, y: py - (py - vb.y) * scale, w: vb.w * scale, h: vb.h * scale });
  };
  const onDown = (e: PointerEvent<SVGSVGElement>) => {
    drag.current = { x: e.clientX, y: e.clientY, vb, moved: false };
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    const svg = svgRef.current;
    if (!d || !svg || e.buttons !== 1) return;
    const rect = svg.getBoundingClientRect();
    const dx = ((e.clientX - d.x) / rect.width) * d.vb.w;
    const dy = ((e.clientY - d.y) / rect.height) * d.vb.h;
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 3) d.moved = true;
    setView({ ...d.vb, x: d.vb.x - dx, y: d.vb.y - dy });
  };

  const neighbours = useMemo(() => {
    if (hover === null) return null;
    const s = new Set([hover]);
    for (const [a, b] of graph.edges) {
      if (a === hover) s.add(b);
      if (b === hover) s.add(a);
    }
    return s;
  }, [hover, graph]);

  // Gebruikerseenheden per pixel (de viewBox behoudt de verhouding, dus de krapste as telt).
  const svgH = svgRef.current?.clientHeight || px * 0.6;
  const scale = Math.max(vb.w / px, vb.h / svgH);
  const showLabels = graph.nodes.length <= 60 || scale < 0.8;
  const toggle = (k: keyof Options) => () => {
    setView(null);
    setOpts((o) => ({ ...o, [k]: !o[k] }));
  };

  return (
    <div className={compact ? "graph graph-compact" : "graph"}>
      <div className="graph-tools">
        {!compact && (
          <label className="check">
            <input type="checkbox" checked={opts.local} disabled={!currentId} onChange={toggle("local")} />
            Enkel rond huidige notitie
          </label>
        )}
        {opts.local && (
          <label className="check">
            Diepte
            <select value={opts.depth} onChange={(e) => (setView(null), setOpts((o) => ({ ...o, depth: Number(e.target.value) })))}>
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={opts.tags} onChange={toggle("tags")} />
          Tags
        </label>
        <label className="check">
          <input type="checkbox" checked={opts.ghosts} onChange={toggle("ghosts")} />
          Niet-bestaande notities
        </label>
        {view && (
          <button className="btn btn-ghost" onClick={() => setView(null)}>
            Passend maken
          </button>
        )}
        <span className="muted small">
          {graph.nodes.length} knopen, {graph.edges.length} links
        </span>
      </div>
      <svg
        ref={svgRef}
        className="graph-svg"
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        onWheel={onWheel}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={() => setTimeout(() => (drag.current = null))}
        role="img"
        aria-label="Grafiek van notities en hun links"
      >
        <g className="graph-edges">
          {graph.edges.map(([a, b], i) => {
            const on = neighbours && neighbours.has(a) && neighbours.has(b);
            return (
              <line
                key={i}
                x1={graph.nodes[a].x}
                y1={graph.nodes[a].y}
                x2={graph.nodes[b].x}
                y2={graph.nodes[b].y}
                className={on ? "on" : neighbours ? "off" : undefined}
                strokeWidth={scale}
              />
            );
          })}
        </g>
        {graph.nodes.map((n, i) => {
          const r = (4 + Math.min(8, Math.sqrt(n.degree) * 2)) * scale;
          const cls = [
            "graph-node",
            `kind-${n.kind}`,
            n.kind === "note" ? `g-${n.group % 8}` : "",
            n.id === currentId ? "current" : "",
            neighbours ? (neighbours.has(i) ? "on" : "off") : "",
          ].join(" ");
          return (
            <g
              key={n.id}
              className={cls}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              onClick={() => {
                if (drag.current?.moved) return;
                if (n.kind === "note") onOpen(n.id);
                else if (n.kind === "tag") onTag?.(n.label.slice(1));
              }}
            >
              <circle cx={n.x} cy={n.y} r={r} />
              {(showLabels || hover === i || n.id === currentId) && (
                <text x={n.x} y={n.y + r + 12 * scale} fontSize={11 * scale} textAnchor="middle">
                  {n.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
