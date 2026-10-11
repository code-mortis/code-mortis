import { useMemo } from "react";
import { createTwoFilesPatch, FILE_HEADERS_ONLY } from "diff";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import go from "highlight.js/lib/languages/go";
import ini from "highlight.js/lib/languages/ini";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

for (const [name, grammar] of Object.entries({
  bash, css, diff, dockerfile, go, ini, javascript, json, markdown, python, rust, sql, typescript, xml, yaml,
})) hljs.registerLanguage(name, grammar);

const extensions: Record<string, string> = {
  py: "python", pyi: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", mts: "typescript", cts: "typescript", tsx: "typescript",
  json: "json", yaml: "yaml", yml: "yaml", toml: "ini", ini: "ini",
  html: "xml", htm: "xml", xml: "xml", css: "css", sql: "sql", md: "markdown",
  sh: "bash", bash: "bash", zsh: "bash", go: "go", rs: "rust", dockerfile: "dockerfile",
};

function fileLanguage(path = "") {
  const name = path.split("/").pop()!.toLowerCase();
  return name === "dockerfile" ? "dockerfile" : extensions[name.split(".").pop()!] ?? "text";
}

export function Code({ children, path, language, label = "Code snippet" }: {
  children: string;
  path?: string;
  language?: string;
  label?: string;
}) {
  const grammar = language?.toLowerCase() || fileLanguage(path);
  const highlighted = useMemo(() => {
    // ponytail: large snippets stay plain; use a worker if larger highlighted blocks become necessary.
    if (children.length > 60000 || !hljs.getLanguage(grammar)) return null;
    try {
      return hljs.highlight(children, { language: grammar, ignoreIllegals: true }).value;
    } catch {
      return null;
    }
  }, [children, grammar]);
  return (
    <pre
      className="code-block max-w-full overflow-x-auto rounded-md border bg-muted p-4 text-xs leading-relaxed"
      tabIndex={0}
      role="region"
      aria-label={label}
    >
      {highlighted === null ? <code>{children}</code> : (
        // Highlight.js escapes the source before adding its own token spans.
        <code className={`hljs language-${grammar}`} dangerouslySetInnerHTML={{ __html: highlighted }} />
      )}
    </pre>
  );
}

export function CodeComparison({ original, suggested, path }: {
  original: string;
  suggested: string;
  path: string;
}) {
  const patch = useMemo(() => {
    if (!original || !suggested || original === suggested || original.length + suggested.length > 60000) return;
    return createTwoFilesPatch("original", "suggested", original, suggested, undefined, undefined, {
      context: 3,
      timeout: 50,
      headerOptions: FILE_HEADERS_ONLY,
    });
  }, [original, suggested]);
  if (patch) {
    const lines = patch.split("\n").slice(2).filter((line) => line !== "\\ No newline at end of file");
    return (
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">Suggested change</h3>
          <span className="flex gap-2 font-mono text-xs tabular-nums" aria-label="Changed lines">
            <span className="text-[var(--code-added)]">+{lines.filter((line) => line.startsWith("+")).length}</span>
            <span className="text-[var(--code-removed)]">−{lines.filter((line) => line.startsWith("-")).length}</span>
          </span>
        </div>
        <div className="overflow-hidden rounded-md border [&_.code-block]:rounded-none [&_.code-block]:border-0 [&_.code-block]:bg-background [&_.code-block]:p-3">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-muted px-3 py-2 text-xs">
            <span className="min-w-0 break-all font-mono">{path.slice(path.lastIndexOf("/") + 1)}</span>
            <span className="text-muted-foreground">Original → Suggested</span>
          </div>
          <Code language="diff" label="Suggested change diff">{lines.join("\n")}</Code>
        </div>
      </section>
    );
  }
  return <>
    {original && original !== suggested && (
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Code at detection</h3>
        <Code path={path} label="Code at detection">{original}</Code>
      </div>
    )}
    {suggested && (
      <div className="space-y-2">
        <h3 className="text-sm font-medium">Suggested change</h3>
        <Code path={path} label="Suggested change">{suggested}</Code>
      </div>
    )}
  </>;
}
