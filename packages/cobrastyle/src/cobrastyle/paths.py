import posixpath


def normalize_path(path: str) -> str:
    """Normalize a resolver-relative stylesheet path to a canonical POSIX form.

    Class-name hashes and manifest keys are derived from this path, so
    equivalent spellings ("./a.css", "a.css") must collapse to one form.
    Raises ValueError for absolute paths, paths escaping the resolver root,
    and paths that don't name a file.
    """
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"Stylesheet path must be relative to the resolver root: {path!r}")
    if normalized == ".":
        raise ValueError(f"Stylesheet path must name a file: {path!r}")
    return normalized
