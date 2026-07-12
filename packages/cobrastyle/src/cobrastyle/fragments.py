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

    Render it *after* the fragment's own markup. htmx parses a partial inside a
    ``<template>``, where the first start tag fixes the parser's insertion mode,
    so this carrier in front of a ``<tr>`` makes the parser throw the row away.
    htmx collects out-of-band elements wherever they sit, so trailing costs nothing.

    The script also reveals elements carrying ``data-cobrastyle-cloak`` once
    every fragment stylesheet in flight has loaded — put the attribute on the
    fragment root to avoid a flash of unstyled content while its CSS loads.

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
    #
    # The rule outlives the script that installs it, so a counter on the <style>
    # bounds its life to the scripts still waiting: the last one out removes it.
    # Revealing only at zero stops a fragment whose CSS is already present from
    # unveiling a slower sibling unstyled (its links are in the DOM, so they count
    # as "have", and nothing else would make it wait for them). A cloak attribute
    # that arrives any other way — a fragment linking no stylesheet, an htmx
    # history restore, which snapshots the body cloak and all — is then inert
    # rather than hidden forever, and the sweep on install drops such leftovers
    # before the rule goes back up.
    script = (
        "(function(){var script=document.currentScript;var head=document.head;"
        f'function reveal(){{document.querySelectorAll("[{CLOAK_ATTRIBUTE}]").forEach('
        f'function(el){{el.removeAttribute("{CLOAK_ATTRIBUTE}");}});}}'
        'var style=document.getElementById("cobrastyle-cloak");'
        "if(!style){"
        'style=document.createElement("style");style.id="cobrastyle-cloak";'
        "style.nonce=script.nonce;"
        f'style.textContent="[{CLOAK_ATTRIBUTE}]{{display:none !important}}";'
        "reveal();head.appendChild(style);}"
        "style.cobrastyle=(style.cobrastyle||0)+1;"
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
        "]).then(function(){if(--style.cobrastyle)return;style.remove();reveal();});"
        "script.remove();})();"
    )
    nonce_attr = f' nonce="{html.escape(nonce, quote=True)}"' if nonce else ""
    # For non-inline swap styles htmx inserts the OOB element's *children* into the
    # target, so the script must ride inside a carrier element, which htmx discards.
    return f'<div hx-swap-oob="beforeend:head"><script{nonce_attr}>{script}</script></div>'
