import re
import shutil
import subprocess

import pytest

from cobrastyle.fragments import CLOAK_ATTRIBUTE, fragment_links_html

SCRIPT = re.compile(r"<script[^>]*>(.*)</script>", re.DOTALL)


def script_body(markup: str) -> str:
    match = SCRIPT.search(markup)
    assert match, markup
    return match.group(1)


def test_no_modules_emits_no_markup():
    assert fragment_links_html([]) == ""


def test_hostile_urls_and_nonces_cannot_break_out_of_the_script():
    markup = fragment_links_html(['/css/a.css?x="</script><img src=x>'], nonce='ab"c')

    assert "</script><img" not in markup
    assert markup.count("</script>") == 1
    assert '<script nonce="ab&quot;c">' in markup


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node to parse the emitted script")
def test_emitted_script_is_valid_javascript():
    """The script is built from nested quote levels in f-strings; a typo would otherwise ship green."""
    for markup in (
        fragment_links_html(["/a.css"]),
        fragment_links_html(["/a.css", "/b.css"], nonce="n0nce"),
        fragment_links_html(["/weird.css?q=1&x=\"'<>"]),
    ):
        node = subprocess.run(
            ["node", "--check", "-"],
            input=script_body(markup),
            capture_output=True,
            text=True,
            check=False,
        )
        assert node.returncode == 0, node.stderr


def test_the_cloak_rule_is_bound_to_the_scripts_still_waiting():
    """Reveal only when the last waiting script drains: see the comment in fragments.py.

    Executing this is what actually pins it (a fragment whose CSS is already present
    must not unveil a sibling still loading); these assertions only keep the counter
    and the teardown from being dropped by accident.
    """
    body = script_body(fragment_links_html(["/a.css"]))

    assert "style.cobrastyle=(style.cobrastyle||0)+1;" in body
    assert "if(--style.cobrastyle)return;style.remove();" in body
    assert body.count(f'removeAttribute("{CLOAK_ATTRIBUTE}")') == 1  # one reveal(), called on install and at zero
