import type { Schema } from "../api";

const warningReasons: Record<string, string> = {
  syntax_error: "could only be partly parsed; some definitions may be missing",
  invalid_encoding: "could not be read as UTF-8",
  parse_error: "could not be parsed",
  file_too_large: "exceeded the indexing size limit",
};

export function CodeGraphCoverage({ coverage }: { coverage?: Schema["GraphCoverage"] | null }) {
  if (!coverage) return null;
  return <div aria-label="Index coverage" className="space-y-1 text-xs leading-relaxed text-muted-foreground">
    <p>{coverage.parsed} {coverage.parsed === 1 ? "file" : "files"} fully parsed.
      {coverage.unsupported > 0 && <> {coverage.unsupported} {coverage.unsupported === 1 ? "file uses a format" : "files use formats"} without structural support.</>}</p>
    {(coverage.oversized_files?.length ?? 0) > 0 && <details>
      <summary className="cursor-pointer">Oversized files{coverage.too_large > 20 ? " (first 20)" : ""}</summary>
      <ul className="mt-2 space-y-1">
        {coverage.oversized_files?.map((file) => <li key={file.path} className="break-words">
          <code>{file.path}</code>: {(file.size_bytes / 1048576).toFixed(2)} MiB; limit {(file.limit_bytes / 1048576).toLocaleString()} MiB.
        </li>)}
      </ul>
    </details>}
    {Object.entries(warningReasons).map(([reason, text]) => {
      const count = coverage.reasons?.[reason] ?? 0;
      return count > 0 && <p key={reason}>{count} {count === 1 ? "file" : "files"} {text}.</p>;
    })}
  </div>;
}
