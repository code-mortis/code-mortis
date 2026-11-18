"""Explain saved transport diagnostics without reading retired runtime sessions."""


def explanation(diagnostics):
    origin = diagnostics.get("origin") if isinstance(diagnostics, dict) else None
    if not isinstance(origin, str):
        return ""
    return {
        "gateway_incomplete": "The gateway returned incomplete or malformed completion arguments.",
        "ocr_mismatch": "The parsed completion differs from the gateway response.",
        "arguments_preserved": "The gateway and review engine recorded identical completion arguments.",
        "unavailable": "The gateway comparison was unavailable; the origin could not be determined.",
    }.get(origin, "")
