from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def cx(*args: Any) -> str:
    """Join CSS class names conditionally, in the style of JS ``clsx``.

    Each argument is one of: a string (kept verbatim when truthy), a mapping of
    class name to a truthiness flag (keys whose value is truthy are kept), an
    iterable of any of these (flattened recursively), or any other value (dropped
    when falsy, otherwise ``str()``-ified). Order is preserved, duplicates are
    kept, and the kept names are joined by single spaces.
    """
    classes: list[str] = []
    _collect(classes, args)
    return " ".join(classes)


def _collect(out: list[str], value: Any) -> None:
    if not value:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, (bytes, bytearray)):
        # Iterating would yield integers; treat bytes as UTF-8 text instead
        out.append(value.decode())
    elif isinstance(value, Mapping):
        out.extend(key if isinstance(key, str) else str(key) for key, active in value.items() if active)
    elif isinstance(value, Iterable):
        for item in value:
            _collect(out, item)
    else:
        out.append(str(value))
