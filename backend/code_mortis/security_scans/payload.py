"""Lossless references to saved quotes already present in complete source blocks."""

from mortis_engine.security_scans.payload import compact_evidence as compact_evidence

from code_mortis.core import file_policy


def excluded_source_paths(value, ai_policy=None, *, additional_only=False):
    paths = set()

    def collect(value):
        if isinstance(value, dict):
            for field, child in value.items():
                if field == "sources" and isinstance(child, dict):
                    paths.update(path for sources in child.values() if isinstance(sources, dict) for path in sources)
                elif (field == "context_paths" or ai_policy and field in {"available_paths", "required_paths"}) and isinstance(child, list):
                    paths.update(path for path in child if isinstance(path, str))
                elif field in {"path", "from_path", "assigned_path", "current_path"} and isinstance(child, str):
                    paths.add(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(value)
    excluded = file_policy.additionally_excluded if additional_only else file_policy.excluded
    return sorted(path for path in paths if excluded(path, ai_policy))


def reusable_source_context(checkpoint, ai_policy=None):
    paths = checkpoint.get("context_paths")
    return (not excluded_source_paths(checkpoint, ai_policy)
            and (checkpoint.get("source_packing") is not True or isinstance(paths, list) and bool(paths)))
