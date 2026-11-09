"""Stable finding identity and consumption readers, including historical OCR reports."""

import json
import re

from code_mortis.core.security import digest


def report_tokens(report):
    summary = (report or {}).get("summary")
    return summary.get("total_tokens") if isinstance(summary, dict) else None


def fingerprint(comment):
    def text(value):
        return " ".join(value.split()).casefold()

    anchor = text(comment.get("existing_code", "")) or f"{comment['start_line']}:{comment['end_line']}"
    return digest(json.dumps([comment["path"], comment.get("category", "other"), anchor,
                              text(comment["content"])], ensure_ascii=False))


def evidence_bundle(context):
    # Keep code verbatim instead of JSON-escaping every newline/quote. Full source is focused on
    # the finding; the permitted cross-file diff remains available without duplicating whole files.
    parts = ["# Code Mortis verification evidence", json.dumps({
        key: value for key, value in context.items() if key not in {"sources", "diff"}
    }, indent=2)]
    blocks = [("Subsequent Git diff", context["diff"])]
    blocks.extend((f"{revision} source: {json.dumps(path)} (source line numbers before |)",
                   "\n".join(f"{number} | {line}" for number, line in enumerate(source.splitlines(), 1)))
                  for revision, sources in context["sources"].items() for path, source in sources.items())
    for title, source in blocks:
        fence = "`" * max(3, 1 + max((len(value) for value in re.findall(r"`+", source)), default=0))
        parts.extend([title, fence, source, fence])
    return "\n\n".join(parts)

