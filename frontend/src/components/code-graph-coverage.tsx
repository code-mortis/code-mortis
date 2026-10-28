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
    {Object.entries(warningReasons).map(([reason, text]) => {
      const count = coverage.reasons?.[reason] ?? 0;
      return count > 0 && <p key={reason}>{count} {count === 1 ? "file" : "files"} {text}.</p>;
    })}
  </div>;
}
