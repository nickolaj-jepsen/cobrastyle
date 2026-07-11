import posixpath


def normalize_path(path: str) -> str:
    """Normalize a resolver-relative stylesheet path to a canonical POSIX form.

    Class-name hashes and manifest keys are derived from this path, so
    equivalent spellings ("./a.css", "a.css") must collapse to one form.
    Raises ValueError for absolute paths or paths escaping the resolver root.
    """
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized.startswith("/") or normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"Stylesheet path must be relative to the resolver root: {path!r}")
    return normalized
