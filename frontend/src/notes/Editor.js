import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
// Markdown-editor: een textarea met de handigste Obsidian-gewoontes. [[ geeft suggesties
// van notities en catalogus-apps, Enter zet lijsten voort, Tab springt in, Ctrl+B/I maakt
// vet/cursief, plakken of slepen van bestanden maakt er versleutelde bijlagen van.
import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { fuzzyScore } from "./model";
const LIST_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;
/** Pixelpositie van de cursor in een textarea, via een onzichtbare kopie (mirror). */
function caretPosition(el, pos) {
    const style = getComputedStyle(el);
    const div = document.createElement("div");
    for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingLeft", "paddingRight", "borderTopWidth", "borderLeftWidth", "tabSize"])
        div.style[p] = style[p];
    div.style.position = "absolute";
    div.style.visibility = "hidden";
    div.style.whiteSpace = "pre-wrap";
    div.style.overflowWrap = "break-word";
    div.style.width = `${el.clientWidth}px`;
    div.textContent = el.value.slice(0, pos);
    const span = document.createElement("span");
    span.textContent = "​";
    div.appendChild(span);
    document.body.appendChild(div);
    const out = { top: span.offsetTop - el.scrollTop + parseFloat(style.lineHeight || "20"), left: span.offsetLeft - el.scrollLeft };
    div.remove();
    return out;
}
export const Editor = forwardRef(function Editor({ value, onChange, suggestions, onSaveNow, onFiles, placeholder }, ref) {
    const ta = useRef(null);
    const [query, setQuery] = useState(null);
    const [active, setActive] = useState(0);
    // De cursor zetten zodra de nieuwe tekst in de DOM staat, nog voor de volgende toetsaanslag.
    const pendingSel = useRef(null);
    useLayoutEffect(() => {
        const el = ta.current;
        const sel = pendingSel.current;
        if (!el || !sel)
            return;
        pendingSel.current = null;
        el.focus();
        el.setSelectionRange(sel[0], sel[1]);
    }, [value]);
    const apply = (next, selStart, selEnd = selStart) => {
        pendingSel.current = [selStart, selEnd];
        onChange(next);
    };
    useImperativeHandle(ref, () => ({
        focus: () => ta.current?.focus(),
        insert: (text) => {
            const el = ta.current;
            if (!el)
                return;
            const { selectionStart: s, selectionEnd: e } = el;
            apply(value.slice(0, s) + text + value.slice(e), s + text.length);
        },
        wrap: (before, after = before) => {
            const el = ta.current;
            if (!el)
                return;
            const { selectionStart: s, selectionEnd: e } = el;
            apply(value.slice(0, s) + before + value.slice(s, e) + after + value.slice(e), s + before.length, e + before.length);
        },
        scrollToLine: (line) => {
            const el = ta.current;
            if (!el)
                return;
            const pos = value.split("\n").slice(0, line).join("\n").length + (line > 0 ? 1 : 0);
            el.focus();
            el.setSelectionRange(pos, pos);
            el.scrollTop = Math.max(0, caretPosition(el, pos).top + el.scrollTop - 80);
        },
    }));
    const matches = useMemo(() => {
        if (!query)
            return [];
        return suggestions
            .map((s) => ({ s, score: Math.max(fuzzyScore(s.label, query.text), fuzzyScore(s.insert, query.text)) }))
            .filter((x) => x.score >= 0)
            .sort((a, b) => b.score - a.score)
            .slice(0, 8)
            .map((x) => x.s);
    }, [query, suggestions]);
    const updateQuery = () => {
        const el = ta.current;
        if (!el || el.selectionStart !== el.selectionEnd)
            return setQuery(null);
        const before = el.value.slice(0, el.selectionStart);
        const m = /\[\[([^\][\n|#]*)$/.exec(before);
        if (!m)
            return setQuery(null);
        const pos = caretPosition(el, el.selectionStart);
        setQuery({ text: m[1], start: el.selectionStart - m[1].length, ...pos });
        setActive(0);
    };
    const choose = (s) => {
        const el = ta.current;
        if (!el || !query)
            return;
        const end = el.selectionStart;
        const closing = value.slice(end, end + 2) === "]]" ? "" : "]]";
        const next = value.slice(0, query.start) + s.insert + closing + value.slice(end);
        setQuery(null);
        apply(next, query.start + s.insert.length + 2);
    };
    const onKeyDown = (e) => {
        const el = e.currentTarget;
        const mod = e.ctrlKey || e.metaKey;
        if (query && matches.length) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => (a + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length);
                return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
                e.preventDefault();
                choose(matches[active]);
                return;
            }
            if (e.key === "Escape") {
                e.preventDefault();
                setQuery(null);
                return;
            }
        }
        if (mod && (e.key === "b" || e.key === "i")) {
            e.preventDefault();
            const mark = e.key === "b" ? "**" : "*";
            const { selectionStart: s, selectionEnd: en } = el;
            apply(value.slice(0, s) + mark + value.slice(s, en) + mark + value.slice(en), s + mark.length, en + mark.length);
            return;
        }
        if (mod && e.key === "s") {
            e.preventDefault();
            onSaveNow?.();
            return;
        }
        const { selectionStart: s, selectionEnd: en } = el;
        const lineStart = value.lastIndexOf("\n", s - 1) + 1;
        const line = value.slice(lineStart, value.indexOf("\n", s) < 0 ? value.length : value.indexOf("\n", s));
        if (e.key === "Tab" && !mod) {
            e.preventDefault();
            if (e.shiftKey) {
                const remove = /^( {1,2}|\t)/.exec(line)?.[0].length ?? 0;
                if (remove)
                    apply(value.slice(0, lineStart) + value.slice(lineStart + remove), Math.max(lineStart, s - remove));
            }
            else
                apply(value.slice(0, lineStart) + "  " + value.slice(lineStart), s + 2, en + 2);
            return;
        }
        if (e.key === "Enter" && !e.shiftKey && !mod && s === en) {
            const m = LIST_RE.exec(line);
            if (!m)
                return;
            e.preventDefault();
            if (line.trim() === m[0].trim()) {
                // Lege opsomming: Enter beëindigt de lijst
                apply(value.slice(0, lineStart) + value.slice(lineStart + line.length), lineStart);
                return;
            }
            const bullet = /^\d+/.test(m[2]) ? `${parseInt(m[2], 10) + 1}${m[2].slice(-1)}` : m[2];
            const prefix = `\n${m[1]}${bullet}${m[3]}${m[4] ? "[ ] " : ""}`;
            apply(value.slice(0, s) + prefix + value.slice(en), s + prefix.length);
        }
    };
    return (_jsxs("div", { className: "editor-wrap", children: [_jsx("textarea", { ref: ta, className: "editor", value: value, placeholder: placeholder, spellCheck: true, onChange: (e) => {
                    onChange(e.target.value);
                    requestAnimationFrame(updateQuery);
                }, onKeyDown: onKeyDown, onPaste: (e) => {
                    const files = [...e.clipboardData.files];
                    if (!files.length || !onFiles)
                        return;
                    e.preventDefault();
                    onFiles(files);
                }, onDragOver: (e) => {
                    if (onFiles && e.dataTransfer.types.includes("Files"))
                        e.preventDefault();
                }, onDrop: (e) => {
                    const files = [...e.dataTransfer.files];
                    if (!files.length || !onFiles)
                        return;
                    e.preventDefault();
                    // Cursor op de plek waar losgelaten werd, zodat de link daar terechtkomt.
                    const el = e.currentTarget;
                    const doc = document;
                    const pos = doc.caretPositionFromPoint?.(e.clientX, e.clientY);
                    if (pos && pos.offset <= el.value.length)
                        el.setSelectionRange(pos.offset, pos.offset);
                    el.focus();
                    onFiles(files);
                }, onKeyUp: (e) => {
                    if (e.key === "ArrowLeft" || e.key === "ArrowRight")
                        updateQuery();
                }, onClick: updateQuery, onBlur: () => setTimeout(() => setQuery(null), 150) }), query && matches.length > 0 && (_jsx("ul", { className: "suggest", style: { top: query.top + 4, left: Math.min(query.left, 9999) }, role: "listbox", children: matches.map((m, i) => (_jsx("li", { className: i === active ? "active" : undefined, role: "option", "aria-selected": i === active, children: _jsxs("button", { type: "button", onMouseDown: (e) => {
                            e.preventDefault();
                            choose(m);
                        }, children: [_jsx("span", { children: m.label }), m.hint && _jsx("span", { className: "muted small", children: m.hint })] }) }, m.insert))) }))] }));
});
