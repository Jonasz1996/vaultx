// Markdown-editor: een textarea met de handigste Obsidian-gewoontes. [[ geeft suggesties
// van notities en catalogus-apps, Enter zet lijsten voort, Tab springt in, Ctrl+B/I maakt
// vet/cursief.

import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { fuzzyScore } from "./model";

export interface Suggestion {
  insert: string;
  label: string;
  hint?: string;
}

export interface EditorHandle {
  focus: () => void;
  /** Vervangt de selectie (of voegt in op de cursor). */
  insert: (text: string) => void;
  wrap: (before: string, after?: string) => void;
  scrollToLine: (line: number) => void;
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  suggestions: Suggestion[];
  onSaveNow?: () => void;
  placeholder?: string;
}

const LIST_RE = /^(\s*)([-*+]|\d+[.)])(\s+)(\[[ xX]\]\s+)?/;

/** Pixelpositie van de cursor in een textarea, via een onzichtbare kopie (mirror). */
function caretPosition(el: HTMLTextAreaElement, pos: number): { top: number; left: number } {
  const style = getComputedStyle(el);
  const div = document.createElement("div");
  for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingLeft", "paddingRight", "borderTopWidth", "borderLeftWidth", "tabSize"] as const)
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

export const Editor = forwardRef<EditorHandle, Props>(function Editor({ value, onChange, suggestions, onSaveNow, placeholder }, ref) {
  const ta = useRef<HTMLTextAreaElement>(null);
  const [query, setQuery] = useState<{ text: string; start: number; top: number; left: number } | null>(null);
  const [active, setActive] = useState(0);

  // De cursor zetten zodra de nieuwe tekst in de DOM staat, nog voor de volgende toetsaanslag.
  const pendingSel = useRef<[number, number] | null>(null);
  useLayoutEffect(() => {
    const el = ta.current;
    const sel = pendingSel.current;
    if (!el || !sel) return;
    pendingSel.current = null;
    el.focus();
    el.setSelectionRange(sel[0], sel[1]);
  }, [value]);

  const apply = (next: string, selStart: number, selEnd = selStart) => {
    pendingSel.current = [selStart, selEnd];
    onChange(next);
  };

  useImperativeHandle(ref, () => ({
    focus: () => ta.current?.focus(),
    insert: (text: string) => {
      const el = ta.current;
      if (!el) return;
      const { selectionStart: s, selectionEnd: e } = el;
      apply(value.slice(0, s) + text + value.slice(e), s + text.length);
    },
    wrap: (before: string, after = before) => {
      const el = ta.current;
      if (!el) return;
      const { selectionStart: s, selectionEnd: e } = el;
      apply(value.slice(0, s) + before + value.slice(s, e) + after + value.slice(e), s + before.length, e + before.length);
    },
    scrollToLine: (line: number) => {
      const el = ta.current;
      if (!el) return;
      const pos = value.split("\n").slice(0, line).join("\n").length + (line > 0 ? 1 : 0);
      el.focus();
      el.setSelectionRange(pos, pos);
      el.scrollTop = Math.max(0, caretPosition(el, pos).top + el.scrollTop - 80);
    },
  }));

  const matches = useMemo(() => {
    if (!query) return [];
    return suggestions
      .map((s) => ({ s, score: Math.max(fuzzyScore(s.label, query.text), fuzzyScore(s.insert, query.text)) }))
      .filter((x) => x.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8)
      .map((x) => x.s);
  }, [query, suggestions]);

  const updateQuery = () => {
    const el = ta.current;
    if (!el || el.selectionStart !== el.selectionEnd) return setQuery(null);
    const before = el.value.slice(0, el.selectionStart);
    const m = /\[\[([^\][\n|#]*)$/.exec(before);
    if (!m) return setQuery(null);
    const pos = caretPosition(el, el.selectionStart);
    setQuery({ text: m[1], start: el.selectionStart - m[1].length, ...pos });
    setActive(0);
  };

  const choose = (s: Suggestion) => {
    const el = ta.current;
    if (!el || !query) return;
    const end = el.selectionStart;
    const closing = value.slice(end, end + 2) === "]]" ? "" : "]]";
    const next = value.slice(0, query.start) + s.insert + closing + value.slice(end);
    setQuery(null);
    apply(next, query.start + s.insert.length + 2);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
        if (remove) apply(value.slice(0, lineStart) + value.slice(lineStart + remove), Math.max(lineStart, s - remove));
      } else apply(value.slice(0, lineStart) + "  " + value.slice(lineStart), s + 2, en + 2);
      return;
    }
    if (e.key === "Enter" && !e.shiftKey && !mod && s === en) {
      const m = LIST_RE.exec(line);
      if (!m) return;
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

  return (
    <div className="editor-wrap">
      <textarea
        ref={ta}
        className="editor"
        value={value}
        placeholder={placeholder}
        spellCheck
        onChange={(e) => {
          onChange(e.target.value);
          requestAnimationFrame(updateQuery);
        }}
        onKeyDown={onKeyDown}
        onKeyUp={(e) => {
          if (e.key === "ArrowLeft" || e.key === "ArrowRight") updateQuery();
        }}
        onClick={updateQuery}
        onBlur={() => setTimeout(() => setQuery(null), 150)}
      />
      {query && matches.length > 0 && (
        <ul className="suggest" style={{ top: query.top + 4, left: Math.min(query.left, 9999) }} role="listbox">
          {matches.map((m, i) => (
            <li key={m.insert} className={i === active ? "active" : undefined} role="option" aria-selected={i === active}>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(m);
                }}
              >
                <span>{m.label}</span>
                {m.hint && <span className="muted small">{m.hint}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
});
