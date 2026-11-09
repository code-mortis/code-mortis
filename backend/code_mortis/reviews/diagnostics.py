"""Explain saved transport diagnostics without reading retired runtime sessions."""


def explanation(diagnostics):
    origin = diagnostics.get("origin") if isinstance(diagnostics, dict) else None
    if not isinstance(origin, str):
        return ""
    return {
        "gateway_incomplete": "The gateway returned incomplete or malformed completion arguments.",
        "ocr_mismatch": "OCR's parsed completion differs from the gateway response.",
        "arguments_preserved": "The gateway and OCR recorded identical completion arguments.",
        "unavailable": "Gateway/OCR comparison was unavailable; the origin could not be determined.",
    }.get(origin, "")
