import type { Schema } from "../api";
import { FileCode2 } from "lucide-react";
import { Status, TechnicalDetails } from "./common";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "./ui/dialog";

const warningReasons: Record<string, string> = {
  syntax_error: "could only be partly parsed; some definitions may be missing",
  invalid_encoding: "could not be read as UTF-8",
  parse_error: "could not be parsed",
  file_too_large: "exceeded the indexing size limit",
};

const fileReasons: Record<string, string> = {
  syntax_error: "The parser could not read some syntax. Some definitions may be missing.",
  invalid_encoding: "The file could not be read as UTF-8.",
  parse_error: "The parser could not process this file.",
};

type Coverage = Schema["GraphCoverage"] | null | undefined;

export function CodeGraphWarnings({ coverage, previous = false, inline = false }: { coverage: Coverage; previous?: boolean; inline?: boolean }) {
  if (!coverage) return null;
  const files = coverage.warning_files ?? [];
  const oversized = coverage.oversized_files ?? [];
  if (!files.length && !oversized.length) return null;
  const total = coverage.partial + coverage.failed + coverage.too_large;
  return <Dialog>
    <DialogTrigger asChild><Button type="button" variant="link" size={inline ? "xs" : "sm"} className={inline
      ? "px-0 text-xs font-normal no-underline hover:no-underline"
      : "h-auto min-h-11 w-fit px-0 underline underline-offset-4 sm:min-h-8"}>View {total} {previous ? "previous " : ""}{total === 1 ? "warning" : "warnings"}</Button></DialogTrigger>
    <DialogContent className="flex h-[min(42rem,90dvh)] flex-col overflow-hidden sm:max-w-2xl">
      <DialogHeader className="pr-6"><DialogTitle>{previous ? "Previous index warnings" : "Indexing warnings"}</DialogTitle><DialogDescription>Files the parser could not fully index.</DialogDescription></DialogHeader>
      <div className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
    {total > files.length + oversized.length && <p className="px-4 py-3 text-xs text-muted-foreground sm:px-5">Showing {files.length + oversized.length} of {total} affected files.</p>}
    <ul className="divide-y rounded-lg border">{files.map((file) => {
      const diagnostic = file.diagnostic;
      const context = diagnostic?.context.replaceAll("_", " ");
      const explanation = diagnostic ? diagnostic.missing_token
        ? `The parser expected ${JSON.stringify(diagnostic.missing_token)} within ${context}.`
        : `The parser could not read ${JSON.stringify(diagnostic.token)} within ${context}.` : null;
      return <li key={file.path} data-slot="coverage-file" className="flex min-w-0 items-start gap-2 px-4 py-3 sm:px-5">
        <FileCode2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-start justify-between gap-2">
            <code className="min-w-0 break-words text-xs leading-5 [overflow-wrap:anywhere]">{file.path}</code>
            <Status value={file.reason === "syntax_error" ? "partial" : "failed"} label={file.reason === "syntax_error" ? "Partly parsed" : "Not parsed"} />
          </div>
          {diagnostic && <p className="text-xs text-muted-foreground">Line {diagnostic.line}, column {diagnostic.column}</p>}
          <p className="max-w-[75ch] break-words text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{explanation ?? fileReasons[file.reason]}</p>
          {diagnostic && explanation && <TechnicalDetails compact name={file.path} error={`${file.path}\nLine ${diagnostic.line}, column ${diagnostic.column}\n${explanation}\nValid syntax can also be unsupported by the parser.\nColumn positions count UTF-8 bytes.`} />}
        </div>
      </li>;
    })}{oversized.map((file) => <li key={file.path} data-slot="coverage-file" className="flex min-w-0 items-start gap-2 px-4 py-3 sm:px-5">
      <FileCode2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 flex-1 space-y-1">
        <code className="block break-words text-xs leading-5 [overflow-wrap:anywhere]">{file.path}</code>
        <p className="max-w-[75ch] break-words text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{(file.size_bytes / 1048576).toFixed(2)} MiB exceeds the indexing limit of {(file.limit_bytes / 1048576).toLocaleString()} MiB.</p>
      </div>
      <Status value="not_reviewed" label="Not indexed" />
    </li>)}</ul>
      </div>
      <DialogFooter showCloseButton />
    </DialogContent>
  </Dialog>;
}

export function CodeGraphCoverage({ coverage }: { coverage?: Coverage }) {
  if (!coverage) return null;
  return <div aria-label="Index coverage" className="space-y-3 text-xs leading-relaxed text-muted-foreground">
      <p>{coverage.parsed} {coverage.parsed === 1 ? "file" : "files"} fully parsed.
        {coverage.unsupported > 0 && <> {coverage.unsupported} {coverage.unsupported === 1 ? "file uses a format" : "files use formats"} without structural support.</>}</p>
      {Object.entries(warningReasons).map(([reason, text]) => {
        const count = coverage.reasons?.[reason] ?? 0;
        return count > 0 && <p key={reason}>{count} {count === 1 ? "file" : "files"} {text}.</p>;
      })}
  </div>;
}
