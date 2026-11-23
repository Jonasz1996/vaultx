import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Canvas-weergave: kaarten slepen, schalen en verbinden, zoals Obsidian Canvas.
// Pannen door de achtergrond te slepen of met het scrollwiel, zoomen met Ctrl+scroll.
// Dubbelklik op de achtergrond maakt een tekstkaart, dubbelklik op een kaart bewerkt ze.
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { bounds, curve, edgePath, fileTarget, nearestSide, newId, parseCanvas, serializeCanvas, sidePoint, SIDES, } from "./canvas";
import { isImage } from "./files";
import { renderBody } from "./markdown";
import { notePath, splitFrontmatter, textOf } from "./model";
const COLORS = ["1", "2", "3", "4", "5", "6"];
const COLOR_NAMES = { "1": "rood", "2": "oranje", "3": "geel", "4": "groen", "5": "cyaan", "6": "paars" };
const MAX_UNDO = 50;
function colorClass(c) {
    return c && COLORS.includes(c) ? ` canvas-c${c}` : "";
}
/** Kaarten in een groep schuiven mee met de groep. */
function insideGroup(g, n) {
    return n.id !== g.id && n.x >= g.x && n.y >= g.y && n.x + n.width <= g.x + g.width && n.y + n.height <= g.y + g.height;
}
export function CanvasView({ body, onChange, ctx, files, onContentClick, onOpen }) {
    const data = useMemo(() => parseCanvas(body), [body]);
    const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
    const [selected, setSelected] = useState(new Set());
    const [selectedEdge, setSelectedEdge] = useState(null);
    const [editing, setEditing] = useState(null);
    const [drag, setDrag] = useState(null);
    // Tijdens het slepen een lokale kopie tonen; pas bij loslaten bewaren.
    const [live, setLive] = useState(null);
    const [picker, setPicker] = useState(null);
    const board = useRef(null);
    const undo = useRef([]);
    const redo = useRef([]);
    const shown = live ?? data;
    const commit = useCallback((next) => {
        const s = serializeCanvas(next);
        if (s === body)
            return;
        undo.current = [...undo.current.slice(-MAX_UNDO + 1), body];
        redo.current = [];
        onChange(s);
    }, [body, onChange]);
    // ------------------------------------------------------------ coördinaten
    const toWorld = useCallback((clientX, clientY) => {
        const r = board.current.getBoundingClientRect();
        return { x: (clientX - r.left - view.x) / view.zoom, y: (clientY - r.top - view.y) / view.zoom };
    }, [view]);
    const fit = useCallback(() => {
        const el = board.current;
        if (!el)
            return;
        const b = bounds(data.nodes);
        if (!b.width)
            return setView({ x: el.clientWidth / 2, y: el.clientHeight / 2, zoom: 1 });
        const zoom = Math.min(1.5, Math.max(0.1, Math.min((el.clientWidth - 80) / b.width, (el.clientHeight - 80) / b.height)));
        setView({
            zoom,
            x: (el.clientWidth - b.width * zoom) / 2 - b.x * zoom,
            y: (el.clientHeight - b.height * zoom) / 2 - b.y * zoom,
        });
    }, [data.nodes]);
    // Bij het openen alles in beeld brengen.
    const fitted = useRef(false);
    useLayoutEffect(() => {
        if (!fitted.current) {
            fitted.current = true;
            fit();
        }
    }, [fit]);
    // Scrollwiel: pannen, met Ctrl/Cmd zoomen rond de muis. Niet-passief om de pagina niet mee te scrollen.
    useEffect(() => {
        const el = board.current;
        if (!el)
            return;
        const onWheel = (e) => {
            if (e.target.closest(".canvas-node-content.scrollable") && !e.ctrlKey && !e.metaKey)
                return;
            e.preventDefault();
            if (e.ctrlKey || e.metaKey) {
                const r = el.getBoundingClientRect();
                const px = e.clientX - r.left;
                const py = e.clientY - r.top;
                setView((v) => {
                    const zoom = Math.min(3, Math.max(0.1, v.zoom * Math.exp(-e.deltaY / 300)));
                    return { zoom, x: px - ((px - v.x) * zoom) / v.zoom, y: py - ((py - v.y) * zoom) / v.zoom };
                });
            }
            else
                setView((v) => ({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY }));
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
    }, []);
    // ------------------------------------------------------------ toevoegen
    const center = () => {
        const el = board.current;
        return toWorld(el.getBoundingClientRect().left + el.clientWidth / 2, el.getBoundingClientRect().top + el.clientHeight / 2);
    };
    const add = (node, edit = false) => {
        const c = center();
        const n = { id: newId(), x: Math.round(c.x - node.width / 2), y: Math.round(c.y - node.height / 2), ...node };
        commit({ ...data, nodes: node.type === "group" ? [n, ...data.nodes] : [...data.nodes, n] });
        setSelected(new Set([n.id]));
        setSelectedEdge(null);
        if (edit)
            setEditing(n.id);
    };
    const addNote = (n) => {
        setPicker(null);
        add({ type: "file", file: `${notePath(n)}.md`, width: 400, height: 400 });
    };
    const addFile = (f) => {
        setPicker(null);
        add({ type: "file", file: f.name, width: isImage(f.name) ? 400 : 260, height: isImage(f.name) ? 300 : 60 });
    };
    const addLink = () => {
        const url = prompt("Webadres (https://…)");
        if (!url || !/^https?:\/\//i.test(url.trim()))
            return;
        add({ type: "link", url: url.trim(), width: 400, height: 80 });
    };
    // ------------------------------------------------------------ verwijderen, kleur, ongedaan maken
    const removeSelected = useCallback(() => {
        if (selectedEdge) {
            commit({ ...data, edges: data.edges.filter((e) => e.id !== selectedEdge) });
            setSelectedEdge(null);
            return;
        }
        if (!selected.size)
            return;
        commit({
            nodes: data.nodes.filter((n) => !selected.has(n.id)),
            edges: data.edges.filter((e) => !selected.has(e.fromNode) && !selected.has(e.toNode)),
        });
        setSelected(new Set());
    }, [commit, data, selected, selectedEdge]);
    const setColor = (color) => {
        if (selectedEdge)
            commit({ ...data, edges: data.edges.map((e) => (e.id === selectedEdge ? { ...e, color } : e)) });
        else
            commit({ ...data, nodes: data.nodes.map((n) => (selected.has(n.id) ? { ...n, color } : n)) });
    };
    const undoLast = useCallback(() => {
        const prev = undo.current.pop();
        if (prev === undefined)
            return;
        redo.current.push(body);
        onChange(prev);
    }, [body, onChange]);
    const redoLast = useCallback(() => {
        const next = redo.current.pop();
        if (next === undefined)
            return;
        undo.current.push(body);
        onChange(next);
    }, [body, onChange]);
    useEffect(() => {
        const onKey = (e) => {
            const t = e.target;
            if (editing || t.matches("input, textarea, select"))
                return;
            const mod = e.ctrlKey || e.metaKey;
            if (e.key === "Delete" || e.key === "Backspace") {
                e.preventDefault();
                removeSelected();
            }
            else if (mod && e.key.toLowerCase() === "z") {
                e.preventDefault();
                if (e.shiftKey)
                    redoLast();
                else
                    undoLast();
            }
            else if (mod && e.key.toLowerCase() === "y") {
                e.preventDefault();
                redoLast();
            }
            else if (mod && e.key.toLowerCase() === "a" && board.current?.contains(document.activeElement)) {
                e.preventDefault();
                setSelected(new Set(data.nodes.map((n) => n.id)));
            }
            else if (e.key === "Escape") {
                setSelected(new Set());
                setSelectedEdge(null);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [data.nodes, editing, redoLast, removeSelected, undoLast]);
    // ------------------------------------------------------------ slepen
    const onBoardPointerDown = (e) => {
        if (e.button !== 0 || e.target.closest(".canvas-node, .canvas-edge-hit, .canvas-edge-label"))
            return;
        board.current?.focus();
        setSelected(new Set());
        setSelectedEdge(null);
        setEditing(null);
        setPicker(null);
        setDrag({ kind: "pan", start: { x: e.clientX, y: e.clientY }, view });
        e.currentTarget.setPointerCapture(e.pointerId);
    };
    const onNodePointerDown = (e, n) => {
        if (e.button !== 0 || editing === n.id)
            return;
        const t = e.target;
        // Knoppen in een kaart blijven gewone knoppen. Links slepen mee; zonder slepen is het
        // gewoon een klik op de link.
        if (t.closest("button, input, textarea, select, summary"))
            return;
        e.stopPropagation();
        // Geen tekstselectie of native slepen van tekst/links: dat breekt het verslepen af.
        e.preventDefault();
        setSelectedEdge(null);
        let sel = selected;
        if (e.shiftKey) {
            sel = new Set(selected);
            if (sel.has(n.id))
                sel.delete(n.id);
            else
                sel.add(n.id);
        }
        else if (!selected.has(n.id))
            sel = new Set([n.id]);
        setSelected(sel);
        // Groepen nemen hun inhoud mee.
        const moving = new Set(sel);
        for (const id of sel) {
            const g = data.nodes.find((x) => x.id === id);
            if (g?.type === "group")
                for (const x of data.nodes)
                    if (insideGroup(g, x))
                        moving.add(x.id);
        }
        const origin = new Map(data.nodes.filter((x) => moving.has(x.id)).map((x) => [x.id, { x: x.x, y: x.y }]));
        // Pas vastgrijpen als er echt gesleept wordt: anders gaat een dubbelklik naar het bord.
        setDrag({ kind: "move", start: toWorld(e.clientX, e.clientY), origin, moved: false });
    };
    const onResizeDown = (e, n) => {
        e.stopPropagation();
        setDrag({ kind: "resize", id: n.id, start: toWorld(e.clientX, e.clientY), size: { width: n.width, height: n.height } });
        board.current?.setPointerCapture(e.pointerId);
    };
    const onHandleDown = (e, n, side) => {
        e.stopPropagation();
        setDrag({ kind: "connect", from: n.id, side, to: toWorld(e.clientX, e.clientY) });
        board.current?.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e) => {
        if (!drag)
            return;
        if (drag.kind === "pan") {
            setView({ ...drag.view, x: drag.view.x + e.clientX - drag.start.x, y: drag.view.y + e.clientY - drag.start.y });
            return;
        }
        const p = toWorld(e.clientX, e.clientY);
        if (drag.kind === "move") {
            const dx = Math.round(p.x - drag.start.x);
            const dy = Math.round(p.y - drag.start.y);
            if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 3)
                return;
            if (!drag.moved) {
                setDrag({ ...drag, moved: true });
                board.current?.setPointerCapture(e.pointerId);
            }
            setLive({
                ...data,
                nodes: data.nodes.map((n) => {
                    const o = drag.origin.get(n.id);
                    return o ? { ...n, x: o.x + dx, y: o.y + dy } : n;
                }),
            });
        }
        else if (drag.kind === "resize") {
            const width = Math.max(80, Math.round(drag.size.width + p.x - drag.start.x));
            const height = Math.max(40, Math.round(drag.size.height + p.y - drag.start.y));
            setLive({ ...data, nodes: data.nodes.map((n) => (n.id === drag.id ? { ...n, width, height } : n)) });
        }
        else if (drag.kind === "connect")
            setDrag({ ...drag, to: p });
    };
    const onPointerUp = (e) => {
        if (!drag)
            return;
        if (drag.kind === "connect") {
            const el = document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-node]");
            const target = el && data.nodes.find((n) => n.id === el.dataset.node);
            const p = toWorld(e.clientX, e.clientY);
            if (target && target.id !== drag.from) {
                const edge = { id: newId(), fromNode: drag.from, fromSide: drag.side, toNode: target.id, toSide: nearestSide(target, p) };
                commit({ ...data, edges: [...data.edges, edge] });
                setSelectedEdge(edge.id);
                setSelected(new Set());
            }
            else if (!target) {
                // Pijl naar een lege plek: nieuwe tekstkaart daar, zoals in Obsidian.
                const n = { id: newId(), type: "text", text: "", x: Math.round(p.x - 125), y: Math.round(p.y - 30), width: 250, height: 60 };
                const edge = { id: newId(), fromNode: drag.from, fromSide: drag.side, toNode: n.id, toSide: nearestSide(n, sidePoint(data.nodes.find((x) => x.id === drag.from), drag.side)) };
                commit({ nodes: [...data.nodes, n], edges: [...data.edges, edge] });
                setSelected(new Set([n.id]));
                setEditing(n.id);
            }
        }
        else if (live)
            commit(live);
        setLive(null);
        setDrag(null);
    };
    const onBoardDoubleClick = (e) => {
        if (e.target.closest(".canvas-node, .canvas-edge-hit, .canvas-edge-label"))
            return;
        const p = toWorld(e.clientX, e.clientY);
        const n = { id: newId(), type: "text", text: "", x: Math.round(p.x - 125), y: Math.round(p.y - 30), width: 250, height: 60 };
        commit({ ...data, nodes: [...data.nodes, n] });
        setSelected(new Set([n.id]));
        setEditing(n.id);
    };
    // ------------------------------------------------------------ bewerken
    const updateNode = (id, patch) => commit({ ...data, nodes: data.nodes.map((n) => (n.id === id ? { ...n, ...patch } : n)) });
    const editEdgeLabel = (edge) => {
        const label = prompt("Label van de pijl", edge.label ?? "");
        if (label === null)
            return;
        commit({ ...data, edges: data.edges.map((e) => (e.id === edge.id ? { ...e, label: label.trim() || undefined } : e)) });
    };
    // ------------------------------------------------------------ weergave
    const byId = useMemo(() => new Map(shown.nodes.map((n) => [n.id, n])), [shown.nodes]);
    const groups = shown.nodes.filter((n) => n.type === "group");
    const cards = shown.nodes.filter((n) => n.type !== "group");
    const single = selected.size === 1 ? data.nodes.find((n) => selected.has(n.id)) : undefined;
    return (_jsxs("div", { className: "canvas-wrap", children: [_jsxs("div", { className: "md-toolbar canvas-toolbar", children: [_jsx("button", { onClick: () => add({ type: "text", text: "", width: 250, height: 60 }, true), children: "+ Tekst" }), _jsxs("div", { className: "menu-wrap", children: [_jsx("button", { onClick: () => setPicker(picker === "note" ? null : "note"), children: "+ Notitie" }), picker === "note" && _jsx(Picker, { items: ctx.index.notes.map((n) => ({ key: n.id, label: notePath(n), item: n })), onPick: addNote, onClose: () => setPicker(null) })] }), _jsxs("div", { className: "menu-wrap", children: [_jsx("button", { onClick: () => setPicker(picker === "file" ? null : "file"), children: "+ Bijlage" }), picker === "file" && _jsx(Picker, { items: files.map((f) => ({ key: f.id, label: f.name, item: f })), onPick: addFile, onClose: () => setPicker(null), empty: "Nog geen bijlagen." })] }), _jsx("button", { onClick: addLink, children: "+ Link" }), _jsx("button", { onClick: () => add({ type: "group", label: "Groep", width: 600, height: 400 }), children: "+ Groep" }), _jsx("span", { className: "canvas-sep" }), (selected.size > 0 || selectedEdge) && (_jsxs(_Fragment, { children: [_jsxs("span", { className: "canvas-colors", role: "group", "aria-label": "Kleur", children: [_jsx("button", { className: "canvas-swatch", title: "Geen kleur", onClick: () => setColor(undefined), children: "\u2205" }), COLORS.map((c) => (_jsx("button", { className: `canvas-swatch canvas-c${c}`, title: COLOR_NAMES[c], "aria-label": COLOR_NAMES[c], onClick: () => setColor(c) }, c)))] }), single?.type === "group" && (_jsx("button", { onClick: () => {
                                    const label = prompt("Naam van de groep", single.label ?? "");
                                    if (label !== null)
                                        updateNode(single.id, { label: label.trim() || undefined });
                                }, children: "Naam\u2026" })), _jsx("button", { className: "btn-danger-text", onClick: removeSelected, title: "Verwijderen (Delete)", children: "Verwijderen" })] })), _jsx("span", { className: "canvas-spacer" }), _jsx("button", { onClick: undoLast, title: "Ongedaan maken (Ctrl+Z)", disabled: !undo.current.length, children: "\u21B6" }), _jsx("button", { onClick: redoLast, title: "Opnieuw (Ctrl+Shift+Z)", disabled: !redo.current.length, children: "\u21B7" }), _jsx("button", { onClick: () => setView((v) => ({ ...v, zoom: Math.max(0.1, v.zoom / 1.25) })), title: "Uitzoomen", children: "\u2212" }), _jsxs("span", { className: "muted small canvas-zoom", children: [Math.round(view.zoom * 100), "%"] }), _jsx("button", { onClick: () => setView((v) => ({ ...v, zoom: Math.min(3, v.zoom * 1.25) })), title: "Inzoomen", children: "+" }), _jsx("button", { onClick: fit, title: "Alles in beeld", children: "Passend" })] }), _jsxs("div", { ref: board, className: `canvas-board${drag?.kind === "pan" ? " panning" : ""}`, tabIndex: 0, onPointerDown: onBoardPointerDown, onPointerMove: onPointerMove, onPointerUp: onPointerUp, onPointerCancel: () => (setDrag(null), setLive(null)), onDoubleClick: onBoardDoubleClick, children: [_jsxs("div", { className: "canvas-layer", style: { transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }, children: [groups.map((g) => (_jsxs("div", { "data-node": g.id, className: `canvas-node canvas-group${colorClass(g.color)}${selected.has(g.id) ? " selected" : ""}`, style: { left: g.x, top: g.y, width: g.width, height: g.height, borderColor: hexColor(g.color) }, onPointerDown: (e) => onNodePointerDown(e, g), children: [g.label && _jsx("div", { className: "canvas-group-label", children: g.label }), _jsx(Handles, { node: g, onDown: onHandleDown, onResize: onResizeDown })] }, g.id))), _jsxs("svg", { className: "canvas-edges", width: "1", height: "1", children: [_jsx("defs", { children: _jsx("marker", { id: "canvas-arrow", viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse", children: _jsx("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "context-stroke" }) }) }), shown.edges.map((edge) => {
                                        const from = byId.get(edge.fromNode);
                                        const to = byId.get(edge.toNode);
                                        if (!from || !to)
                                            return null;
                                        const { d } = edgePath(from, to, edge);
                                        return (_jsxs("g", { className: `canvas-edge${colorClass(edge.color)}${selectedEdge === edge.id ? " selected" : ""}`, children: [_jsx("path", { d: d, className: "canvas-edge-line", style: { stroke: hexColor(edge.color) }, markerEnd: edge.toEnd === "none" ? undefined : "url(#canvas-arrow)", markerStart: edge.fromEnd === "arrow" ? "url(#canvas-arrow)" : undefined }), _jsx("path", { d: d, className: "canvas-edge-hit", onPointerDown: (e) => {
                                                        e.stopPropagation();
                                                        setSelected(new Set());
                                                        setSelectedEdge(edge.id);
                                                    }, onDoubleClick: () => editEdgeLabel(edge) })] }, edge.id));
                                    }), drag?.kind === "connect" &&
                                        (() => {
                                            const from = byId.get(drag.from);
                                            if (!from)
                                                return null;
                                            return _jsx("path", { className: "canvas-edge-line canvas-edge-draft", d: curve(sidePoint(from, drag.side), drag.side, drag.to).d, markerEnd: "url(#canvas-arrow)" });
                                        })()] }), shown.edges.map((edge) => {
                                const from = byId.get(edge.fromNode);
                                const to = byId.get(edge.toNode);
                                if (!from || !to || !edge.label)
                                    return null;
                                const { mid } = edgePath(from, to, edge);
                                return (_jsx("div", { className: "canvas-edge-label", style: { left: mid.x, top: mid.y }, onPointerDown: (e) => {
                                        e.stopPropagation();
                                        setSelected(new Set());
                                        setSelectedEdge(edge.id);
                                    }, onDoubleClick: () => editEdgeLabel(edge), children: edge.label }, edge.id));
                            }), cards.map((n) => (_jsxs("div", { "data-node": n.id, className: `canvas-node canvas-${n.type}${colorClass(n.color)}${selected.has(n.id) ? " selected" : ""}`, style: { left: n.x, top: n.y, width: n.width, height: n.height, borderColor: hexColor(n.color) }, onPointerDown: (e) => onNodePointerDown(e, n), onDoubleClick: (e) => {
                                    e.stopPropagation();
                                    if (n.type === "text")
                                        setEditing(n.id);
                                    else if (n.type === "link" && n.url) {
                                        const url = prompt("Webadres", n.url);
                                        if (url && /^https?:\/\//i.test(url.trim()))
                                            updateNode(n.id, { url: url.trim() });
                                    }
                                }, children: [editing === n.id && n.type === "text" ? (_jsx(TextEditor, { value: n.text ?? "", onDone: (text) => {
                                            setEditing(null);
                                            if (text !== (n.text ?? ""))
                                                updateNode(n.id, { text });
                                        } })) : (_jsx(NodeContent, { node: n, ctx: ctx, files: files, onClick: onContentClick, onOpen: onOpen })), _jsx(Handles, { node: n, onDown: onHandleDown, onResize: onResizeDown })] }, n.id)))] }), !data.nodes.length && (_jsx("div", { className: "canvas-hint muted", children: "Dubbelklik om een kaart te maken, of gebruik de knoppen hierboven. Sleep vanaf een bolletje op de rand om kaarten te verbinden." }))] })] }));
}
function hexColor(c) {
    return c && /^#[0-9a-f]{3,8}$/i.test(c) ? c : undefined;
}
function Handles({ node, onDown, onResize, }) {
    return (_jsxs(_Fragment, { children: [SIDES.map((s) => (_jsx("div", { className: `canvas-handle canvas-handle-${s}`, title: "Sleep om te verbinden", onPointerDown: (e) => onDown(e, node, s) }, s))), _jsx("div", { className: "canvas-resize", title: "Sleep om de grootte te wijzigen", onPointerDown: (e) => onResize(e, node) })] }));
}
function TextEditor({ value, onDone }) {
    const [text, setText] = useState(value);
    const ref = useRef(null);
    useEffect(() => {
        ref.current?.focus();
        ref.current?.setSelectionRange(text.length, text.length);
    }, []);
    return (_jsx("textarea", { ref: ref, className: "canvas-text-editor", value: text, placeholder: "Markdown\u2026", onChange: (e) => setText(e.target.value), onPointerDown: (e) => e.stopPropagation(), onBlur: () => onDone(text), onKeyDown: (e) => {
            if (e.key === "Escape" || ((e.ctrlKey || e.metaKey) && e.key === "Enter")) {
                e.preventDefault();
                e.target.blur();
            }
        } }));
}
function NodeContent({ node, ctx, files, onClick, onOpen, }) {
    const note = node.type === "file" && node.file ? ctx.index.resolve(fileTarget(node.file)) ?? ctx.index.resolve(fileTarget(node.file).split("/").pop()) : undefined;
    const file = node.type === "file" && node.file && !note ? files.find((f) => f.name.toLowerCase() === node.file.toLowerCase()) ?? ctx.files?.find(node.file) ?? undefined : undefined;
    const html = useMemo(() => {
        // Taken in kaarten zijn alleen-lezen; afvinken doe je in de notitie zelf.
        const ro = (h) => h.replace(/data-task="\d+"/g, "disabled");
        if (node.type === "text")
            return ro(renderBody(node.text ?? "", ctx));
        if (note) {
            const src = node.subpath ? `![[${notePath(note)}${node.subpath}]]` : splitFrontmatter(textOf(note)).content;
            return ro(renderBody(src, { ...ctx, depth: 1, trail: [note.id] }));
        }
        if (file)
            return renderBody(`![[${file.name}]]`, ctx);
        return null;
    }, [node, note, file, ctx]);
    if (node.type === "link")
        return (_jsxs("div", { className: "canvas-node-content canvas-link-card", children: [_jsxs("a", { href: node.url, target: "_blank", rel: "noopener noreferrer", children: ["\uD83D\uDD17 ", safeHost(node.url)] }), _jsx("div", { className: "muted small canvas-url", children: node.url })] }));
    if (node.type === "file" && !note && !file)
        return _jsxs("div", { className: "canvas-node-content muted small", children: ["\"", node.file, "\" niet gevonden."] });
    return (_jsxs(_Fragment, { children: [note && (_jsx("div", { className: "canvas-file-title", children: _jsx("button", { className: "wikilink", onClick: () => onOpen(note.id), title: "Notitie openen", children: note.title }) })), _jsx("div", { className: `canvas-node-content md${note ? " scrollable" : ""}`, onClick: onClick, dangerouslySetInnerHTML: { __html: html ?? "" } })] }));
}
function safeHost(url) {
    try {
        return new URL(url ?? "").host;
    }
    catch {
        return url ?? "";
    }
}
function Picker({ items, onPick, onClose, empty = "Niets gevonden." }) {
    const [q, setQ] = useState("");
    const shown = items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase())).slice(0, 30);
    return (_jsxs("div", { className: "menu canvas-picker", children: [_jsx("input", { autoFocus: true, placeholder: "Zoeken\u2026", value: q, onChange: (e) => setQ(e.target.value), onKeyDown: (e) => {
                    if (e.key === "Escape")
                        onClose();
                    if (e.key === "Enter" && shown[0])
                        onPick(shown[0].item);
                } }), !shown.length && _jsx("div", { className: "muted small pad", children: empty }), shown.map((i) => (_jsx("button", { className: "menu-item", onClick: () => onPick(i.item), children: i.label }, i.key)))] }));
}
