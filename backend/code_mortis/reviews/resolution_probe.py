"""Standalone example for validating the finding-resolution workflow."""


def average_duration_ms(durations: list[float]) -> float:
    """Return the mean duration, or 0.0 when there are no samples."""
    return 0.0 if not durations else sum(durations) / len(durations)
