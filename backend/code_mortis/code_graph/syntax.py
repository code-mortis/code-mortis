"""Selected syntax capabilities and conservative file resolution, without grammar imports."""

from pathlib import PurePosixPath

# shortcut: syntax probes cover selected node kinds, not complete language semantics;
# add language-specific acceptance fixtures before advertising further relationships.
LANGUAGES = {
    ".py": ("python", "function_definition class_definition", "call", "import_statement import_from_statement"),
    ".js": ("javascript", "function_declaration class_declaration method_definition", "call_expression", "import_statement export_statement"),
    ".jsx": ("javascript", "function_declaration class_declaration method_definition", "call_expression", "import_statement export_statement"),
    ".ts": ("typescript", "function_declaration class_declaration method_definition interface_declaration type_alias_declaration", "call_expression", "import_statement export_statement"),
    ".tsx": ("tsx", "function_declaration class_declaration method_definition interface_declaration type_alias_declaration", "call_expression", "import_statement export_statement"),
    ".go": ("go", "function_declaration method_declaration type_spec", "call_expression", "import_declaration"),
    ".rs": ("rust", "function_item struct_item enum_item trait_item", "call_expression", "use_declaration"),
    ".java": ("java", "class_declaration interface_declaration method_declaration constructor_declaration", "method_invocation", "import_declaration"),
    ".c": ("c", "function_definition struct_specifier", "call_expression", "preproc_include"),
    ".cpp": ("cpp", "function_definition class_specifier struct_specifier", "call_expression", "preproc_include"),
    ".rb": ("ruby", "method singleton_method class module", "call", ""),
    ".cs": ("c_sharp", "class_declaration interface_declaration method_declaration", "invocation_expression", "using_directive"),
    ".php": ("php", "function_definition class_declaration method_declaration", "function_call_expression", "require_expression include_expression"),
    ".sh": ("bash", "function_definition", "command", ""),
}


def descendants(node):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def python_module_targets(paths):
    """Index exact dotted modules from the repository root and regular package roots."""
    paths = set(paths)
    targets = {}
    for name in paths:
        path = PurePosixPath(name)
        if path.suffix != ".py":
            continue
        suffix = () if path.stem == "__init__" else (path.stem,)
        names = [path.parent.parts + suffix]
        root = path.parent
        while root.parts and str(root / "__init__.py") in paths:
            root = root.parent
        if root != path.parent:
            names.append(path.parent.relative_to(root).parts + suffix)
        for parts in names:
            if parts and all(part.isidentifier() for part in parts):
                module = ".".join(parts)
                if targets.setdefault(module, name) != name:
                    targets[module] = None
    # A uniquely named leaf may still be inaccessible through a shadowed parent.
    for module, target in targets.items():
        if target is None:
            continue
        prefix = module.rpartition(".")[0]
        while prefix:
            if prefix in targets:
                parent = targets[prefix]
                if (parent is None or not parent.endswith("/__init__.py")
                    or not target.startswith(parent.rsplit("/", 1)[0] + "/")):
                    targets[module] = None
                    break
            prefix = prefix.rpartition(".")[0]
    return targets


def relative_target(path, specifier, available, python=False, level=0):
    """Resolve only unambiguous, in-corpus relative file dependencies."""
    if python:
        if not level:
            return None
        base = path.parent
        for _ in range(level - 1):
            if base == PurePosixPath("."):
                return None
            base = base.parent
        base = base.joinpath(*specifier.split(".")) if specifier else base
        candidates = [str(base) + ".py", str(base / "__init__.py")]
    else:
        if not specifier.startswith("."):
            return None
        parts = list(path.parent.parts)
        for part in specifier.split("/"):
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        base = PurePosixPath(*parts)
        candidates = [str(base)] if base.suffix else [
            str(base) + ext for ext in (".ts", ".tsx", ".js", ".jsx")
        ] + [str(base / ("index" + ext)) for ext in (".ts", ".tsx", ".js", ".jsx")]
    matches = [name for name in candidates if name in available]
    return matches[0] if len(matches) == 1 else None


def import_target(path, specifier, language, available, *, python_modules=None):
    if language == "python":
        level = len(specifier) - len(specifier.lstrip("."))
        if level:
            return relative_target(PurePosixPath(path), specifier[level:], available, python=True, level=level)
        target = python_modules.get(specifier) if python_modules is not None else None
        return target if target in available else None
    if language in {"javascript", "typescript", "tsx"}:
        # Escaped literals and aliases need a language resolver, not filename guessing.
        if "\\" in specifier or not specifier.startswith(("./", "../")):
            return None
        return relative_target(PurePosixPath(path), specifier, available)
    return None
