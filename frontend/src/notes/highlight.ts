// Code-kleuren met highlight.js, enkel de talen die in een homelab-kluis voorkomen.

import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import nginx from "highlight.js/lib/languages/nginx";
import php from "highlight.js/lib/languages/php";
import powershell from "highlight.js/lib/languages/powershell";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import shell from "highlight.js/lib/languages/shell";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

const LANGS = { bash, c, cpp, csharp, css, diff, dockerfile, go, ini, java, javascript, json, markdown, nginx, php, powershell, python, rust, shell, sql, typescript, xml, yaml };
for (const [name, lang] of Object.entries(LANGS)) hljs.registerLanguage(name, lang);
hljs.registerAliases(["sh", "zsh", "console"], { languageName: "bash" });
hljs.registerAliases(["toml", "conf", "env"], { languageName: "ini" });
hljs.registerAliases(["html", "svg"], { languageName: "xml" });
hljs.registerAliases(["compose"], { languageName: "yaml" });

/** HTML met hljs-klassen, of null als de taal onbekend is (dan gewone tekst). */
export function highlight(code: string, lang: string): string | null {
  const l = lang.toLowerCase();
  if (!l || !hljs.getLanguage(l)) return null;
  return hljs.highlight(code, { language: l, ignoreIllegals: true }).value;
}
