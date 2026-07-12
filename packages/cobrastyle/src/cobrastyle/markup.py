"""The ``<link>`` markup both template engines render, and the dev serving prefix's reserved names.

Split from :mod:`cobrastyle.serve` so the prod render path never imports the dev CSS server.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

from cobrastyle.paths import url_prefix as normalize_url_prefix

# Reserved names under the dev serving prefix (hot reload); never valid stylesheet paths.
EVENTS_PATH = "__events__"
CLIENT_SCRIPT_PATH = "__client__.js"


def hot_reload_script_html(url_prefix: str) -> str:
    """The dev-only script tag loading the hot-reload client from the serving prefix."""
    prefix = html.escape(normalize_url_prefix(url_prefix), quote=True)
    return f'<script src="{prefix}{CLIENT_SCRIPT_PATH}" data-events="{prefix}{EVENTS_PATH}" defer></script>'


def stylesheet_links_html(urls: Iterable[str], *, hot_reload_prefix: str | None = None) -> str:
    """``<link>`` markup for ``urls``, plus the hot-reload client script when a prefix is set.

    The one implementation of the links markup both template engines render;
    everything interpolated is escaped, so the result is safe to mark safe.
    """
    links = "".join(f'<link rel="stylesheet" href="{html.escape(url, quote=True)}" />' for url in urls)
    if hot_reload_prefix is not None:
        links += hot_reload_script_html(hot_reload_prefix)
    return links
