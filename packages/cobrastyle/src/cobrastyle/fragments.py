"""Markup for fragment responses (HTMX partial swaps) that link their own stylesheets."""

from __future__ import annotations

import html
import json

CLOAK_ATTRIBUTE = "data-cobrastyle-cloak"

# Never leave content hidden: a stylesheet that hangs longer than this reveals anyway.
_CLOAK_TIMEOUT_MS = 3000


def fragment_links_html(urls: list[str], *, nonce: str | None = None) -> str:
    """The out-of-band <script> adding any of ``urls`` missing from the page's <head>.

    Empty when ``urls`` is; ``nonce`` feeds a CSP script-src nonce attribute.
    The returned markup is safe to mark safe: everything interpolated is escaped.

    The script also reveals elements carrying ``data-cobrastyle-cloak`` once
    every inserted stylesheet has loaded — put the attribute on the fragment
    root to avoid a flash of unstyled content while its CSS is in flight.

    With CSP nonces, htmx 2 is required (or htmx 1.x with
    ``htmx.config.inlineScriptNonce``): htmx 1.x re-creates OOB scripts after
    DOM insertion, by which point the browser has hidden the nonce attribute,
    so CSP blocks the script and the fragment stays unstyled.
    """
    if not urls:
        return ""
    # <-escape so a pathological URL can't close the script element early
    payload = json.dumps(urls).replace("<", "\\u003c")
    # hx-swap-oob is processed before the main content swap, in the same task, so
    # the links and the cloak rule are in place before the fragment can paint.
    # Dedup happens here, client-side — the server can't know which links the page
    # already has; split("?") so a hot-reload cache-busted link still counts as
    # present. With nothing to load, Promise.all resolves on a microtask after the
    # swap task, still ahead of the next paint: cloaked content never flashes.
    script = (
        "(function(){var script=document.currentScript;var head=document.head;"
        'if(!document.getElementById("cobrastyle-cloak")){'
        'var style=document.createElement("style");style.id="cobrastyle-cloak";'
        "style.nonce=script.nonce;"
        f'style.textContent="[{CLOAK_ATTRIBUTE}]{{display:none !important}}";'
        "head.appendChild(style);}"
        "var have={};"
        "document.querySelectorAll('link[rel=\"stylesheet\"]').forEach(function(link){"
        'have[(link.getAttribute("href")||"").split("?")[0]]=true;});'
        "var pending=[];"
        f'{payload}.forEach(function(url){{if(have[url.split("?")[0]])return;'
        'var link=document.createElement("link");link.rel="stylesheet";link.href=url;'
        "pending.push(new Promise(function(resolve){link.onload=link.onerror=resolve;}));"
        "head.appendChild(link);});"
        "Promise.race([Promise.all(pending),"
        f"new Promise(function(resolve){{setTimeout(resolve,{_CLOAK_TIMEOUT_MS});}})"
        f']).then(function(){{document.querySelectorAll("[{CLOAK_ATTRIBUTE}]").forEach('
        f'function(el){{el.removeAttribute("{CLOAK_ATTRIBUTE}");}});}});'
        "script.remove();})();"
    )
    nonce_attr = f' nonce="{html.escape(nonce, quote=True)}"' if nonce else ""
    # For non-inline swap styles htmx inserts the OOB element's *children* into the
    # target, so the script must ride inside a carrier element, which htmx discards.
    return f'<div hx-swap-oob="beforeend:head"><script{nonce_attr}>{script}</script></div>'
