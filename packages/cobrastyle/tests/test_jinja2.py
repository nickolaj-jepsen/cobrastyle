import re
from textwrap import dedent

import pytest
from jinja2 import DictLoader, Environment, TemplateSyntaxError

from cobrastyle import InMemoryResolver
from cobrastyle.errors import StylesheetNotFoundError
from cobrastyle.fragments import fragment_links_html
from cobrastyle.jinja2 import CobrastyleExtension, configure


def _clean(template: str) -> str:
    return "\n".join(line for line in dedent(template).splitlines() if line.strip()).strip()


def _hrefs(rendered: str) -> list[str]:
    """The stylesheet URLs a render linked, in order."""
    return re.findall(r'<link rel="stylesheet" href="([^"]+)"', rendered)


def make_environment(
    resources: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    underscore_aliases: bool = True,
    **kwargs,
) -> Environment:
    jinja = Environment(loader=DictLoader(templates or {}), extensions=[CobrastyleExtension], **kwargs)
    configure(
        jinja,
        resolver=InMemoryResolver(resources or {}),
        minify=False,
        module_pattern="[local]",
        underscore_aliases=underscore_aliases,
    )
    return jinja


def render_jinja(
    template: str,
    resources: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    underscore_aliases: bool = True,
    **kwargs,
) -> str:
    jinja = make_environment(resources, templates, underscore_aliases, **kwargs)
    return _clean(jinja.from_string(template).render())


def test_simple():
    resources = {"test.css": ".header { color: red; }"}
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css"  %}
    {{ styles.header }}
    """,
        resources,
    )

    assert result == "header"


def test_multiple():
    resources = {"test.css": ".header { color: red; } .footer { color: blue; }"}
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css"  %}
    {{ styles.header }}
    {{ styles.footer }}
    """,
        resources,
    )

    assert result == _clean("""
    header
    footer""")


def test_dash_in_class_name_rewrite():
    resources = {"test.css": ".header-title { color: red; }"}
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css"  %}
    1:{{ styles.header_title }}
    2:{{ styles["header-title"] }}
    """,
        resources,
        underscore_aliases=True,
    )

    assert result == _clean("""
    1:header-title
    2:header-title
    """)


def test_disabled_class_name_rewrite():
    resources = {"test.css": ".header-title { color: red; }"}
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css"  %}
    1:{{ styles.header_title }}
    2:{{ styles["header-title"] }}
    """,
        resources,
        underscore_aliases=False,
    )

    assert result == _clean("""
    1:
    2:header-title
    """)


def test_composes():
    resources = {"test.css": ".base { color: black; } .button { composes: base; background: red; }"}
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css"  %}
    {{ styles.button }}
    """,
        resources,
    )

    assert result == "button base"


def test_links():
    resources = {"test.css": ".header { color: red; }"}
    result = render_jinja(
        """
    <html>
        <head>
            {{ cobrastyle.links() }}
        </head>
        <body>
            {% cobrastyle styles = "test.css"  %}
            <h1 class="{{ styles.header }}">Hello World</h1>
        </body>
    </html>
    """,
        resources,
    )

    assert result == _clean(
        """
    <html>
        <head>
            <link rel="stylesheet" href="test.css" />
        </head>
        <body>
            <h1 class="header">Hello World</h1>
        </body>
    </html>
    """
    )


def test_links_multiple():
    resources = {
        "test.css": ".header { color: red; }",
        "test2.css": ".footer { color: blue; }",
    }
    result = render_jinja(
        """
    <html>
        <head>
            {{ cobrastyle.links() }}
        </head>
        <body>
            {% cobrastyle styles = "test.css"  %}
            {% cobrastyle styles2 = "test2.css"  %}
            <h1 class="{{ styles.header }}">Hello World</h1>
            <h2 class="{{ styles2.footer }}">Hello World</h2>
        </body>
    </html>
    """,
        resources,
    )

    assert result == _clean(
        """
    <html>
        <head>
            <link rel="stylesheet" href="test.css" /><link rel="stylesheet" href="test2.css" />
        </head>
        <body>
            <h1 class="header">Hello World</h1>
            <h2 class="footer">Hello World</h2>
        </body>
    </html>
    """
    )


def test_links_idempotent():
    jinja = make_environment({"test.css": ".header { color: red; }"})
    template = jinja.from_string(
        """
    <html>
        <head>
            {{ cobrastyle.links() }}
        </head>
        <body>
            {% cobrastyle styles = "test.css"  %}
            <h1 class="{{ styles.header }}">Hello World</h1>
        </body>
    </html>
    """,
    )

    for _ in range(10):
        assert _clean(template.render()) == _clean(
            """
        <html>
            <head>
                <link rel="stylesheet" href="test.css" />
            </head>
            <body>
                <h1 class="header">Hello World</h1>
            </body>
        </html>
        """
        )


def test_links_are_per_render():
    """Two pages in the same environment only link their own stylesheets."""
    resources = {
        "one.css": ".one { color: red; }",
        "two.css": ".two { color: blue; }",
    }
    templates = {
        "one.html": '{% cobrastyle styles = "one.css" %}{{ cobrastyle.links() }}',
        "two.html": '{% cobrastyle styles = "two.css" %}{{ cobrastyle.links() }}',
    }
    jinja = make_environment(resources, templates)

    assert jinja.get_template("one.html").render() == '<link rel="stylesheet" href="one.css" />'
    assert jinja.get_template("two.html").render() == '<link rel="stylesheet" href="two.css" />'
    # And again, to make sure cached templates behave the same
    assert jinja.get_template("one.html").render() == '<link rel="stylesheet" href="one.css" />'


def test_links_with_inherited_head():
    """A child's stylesheets show up in links() rendered by the parent's <head>."""
    resources = {
        "test.css": ".header { color: red; }",
    }
    parent = _clean("""
    <html>
        <head>
            {{ cobrastyle.links() }}
        </head>
        <body>
            {% block content %}{% endblock %}
        </body>
    </html>
    """)

    result = render_jinja(
        """
    {% extends "parent.html" %}
    {% block content %}
        {% cobrastyle styles = "test.css"  %}
        <h1 class="{{ styles.header }}">Hello World</h1>
    {% endblock %}
    """,
        resources,
        templates={"parent.html": parent},
    )

    assert result == _clean("""
    <html>
        <head>
            <link rel="stylesheet" href="test.css" />
        </head>
        <body>
            <h1 class="header">Hello World</h1>
        </body>
    </html>
    """)


def test_include_styles_reach_links():
    """An included partial's stylesheet is linked by the page's <head>, which never names it."""
    templates = {
        "page.html": '{{ cobrastyle.links() }}{% include "_partial.html" %}',
        "_partial.html": '{% cobrastyle styles = "partial.css" %}<span class="{{ styles.x }}"></span>',
    }
    jinja = make_environment({"partial.css": ".x { color: red; }"}, templates)

    result = jinja.get_template("page.html").render()

    assert 'class="x"' in result
    assert '<link rel="stylesheet" href="partial.css" />' in result


def test_links_cover_the_whole_static_graph_layouts_first():
    templates = {
        "base.html": '{% cobrastyle base = "base.css" %}{{ cobrastyle.links() }}{% block body %}{% endblock %}',
        "mid.html": '{% extends "base.html" %}{% cobrastyle mid = "mid.css" %}',
        "page.html": (
            '{% extends "mid.html" %}{% cobrastyle page = "page.css" %}'
            '{% block body %}{% include "_partial.html" %}{% endblock %}'
        ),
        "_partial.html": '{% cobrastyle partial = "partial.css" %}',
    }
    jinja = make_environment(
        dict.fromkeys(("base.css", "mid.css", "page.css", "partial.css"), ".x { color: red; }"), templates
    )

    result = jinja.get_template("page.html").render()

    assert _hrefs(result) == ["base.css", "mid.css", "page.css", "partial.css"]


def test_a_page_with_no_tags_of_its_own_still_links_what_it_includes():
    templates = {
        "page.html": '{{ cobrastyle.links() }}{% include "_partial.html" %}',
        "_partial.html": '{% cobrastyle styles = "partial.css" %}',
    }
    jinja = make_environment({"partial.css": ".x { color: red; }"}, templates)

    assert _hrefs(jinja.get_template("page.html").render()) == ["partial.css"]


def test_a_dynamically_included_partial_is_not_seen():
    """Only statically named templates join the graph; a variable include needs an explicit path."""
    templates = {
        "page.html": "{{ cobrastyle.links() }}{% include partial %}",
        "_partial.html": '{% cobrastyle styles = "partial.css" %}',
    }
    jinja = make_environment({"partial.css": ".x { color: red; }"}, templates)

    assert _hrefs(jinja.get_template("page.html").render(partial="_partial.html")) == []


def test_stylesheet_url():
    jinja = make_environment({"print.css": ".p { color: red; }"})
    template = jinja.from_string('<link rel="preload" href="{{ cobrastyle.stylesheet_url("print.css") }}">')

    assert template.render() == '<link rel="preload" href="print.css">'


def test_stylesheet_url_rejects_an_unknown_module():
    jinja = make_environment({"print.css": ".p { color: red; }"})
    template = jinja.from_string('{{ cobrastyle.stylesheet_url("nope.css") }}')

    with pytest.raises(StylesheetNotFoundError, match="nope"):
        template.render()


def test_dynamic_path_is_rejected():
    jinja = make_environment({"test.css": ".a {}"})
    with pytest.raises(TemplateSyntaxError, match="constant string"):
        jinja.from_string('{% cobrastyle styles = "test" + ".css" %}')


def test_bytecode_cache_rejected(tmp_path):
    from jinja2.bccache import FileSystemBytecodeCache

    jinja = Environment(
        loader=DictLoader({}),
        extensions=[CobrastyleExtension],
        bytecode_cache=FileSystemBytecodeCache(str(tmp_path)),
    )
    with pytest.raises(RuntimeError, match="bytecode_cache"):
        configure(jinja, resolver=InMemoryResolver({}))


def test_missing_resolver():
    jinja = Environment(extensions=[CobrastyleExtension])
    with pytest.raises(RuntimeError, match="resolver"):
        jinja.from_string('{% cobrastyle styles = "test.css" %}')


def test_unknown_stylesheet():
    jinja = make_environment({})
    with pytest.raises(TemplateSyntaxError, match=r"'missing\.css' not found"):
        jinja.from_string('{% cobrastyle styles = "missing.css" %}')


def test_invalid_stylesheet_path():
    jinja = make_environment({})
    with pytest.raises(TemplateSyntaxError, match="relative to the resolver root"):
        jinja.from_string('{% cobrastyle styles = "../escape.css" %}')


def test_recompiling_does_not_blank_page_tracking_mid_compile():
    source = '{% cobrastyle styles = "test.css" %}'
    jinja = make_environment({"test.css": ".a { color: red; }"}, templates={"page.html": source})
    jinja.get_template("page.html")
    extension = CobrastyleExtension.get(jinja)
    assert extension is not None
    assert extension.page_modules("page.html") == ["test.css"]

    # A concurrent compile of the same page has preprocessed but not yet parsed the tag
    extension.preprocess(source, "page.html")

    assert extension.page_modules("page.html") == ["test.css"]


def test_line_statement_tags_are_tracked():
    jinja = Environment(
        loader=DictLoader({"page.html": '<head>{{ cobrastyle.links() }}</head>\n% cobrastyle styles = "page.css"\nx'}),
        extensions=[CobrastyleExtension],
        line_statement_prefix="%",
    )
    configure(jinja, resolver=InMemoryResolver({"page.css": ".a { color: red; }"}), module_pattern="[local]")

    html = jinja.get_template("page.html").render()

    assert '<link rel="stylesheet" href="page.css" />' in html


def test_commenting_out_the_tag_drops_page_tracking():
    source = '{% cobrastyle styles = "test.css" %}{{ cobrastyle.links() }}'
    jinja = make_environment({"test.css": ".a {}"}, templates={"page.html": source})
    jinja.get_template("page.html")
    extension = CobrastyleExtension.get(jinja)
    assert extension is not None
    assert extension.page_modules("page.html") == ["test.css"]

    # The tag text survives inside a comment, but the lexer sees no tag: [] is final
    extension.preprocess('{# {% cobrastyle styles = "test.css" %} #}{{ cobrastyle.links() }}', "page.html")

    assert extension.page_modules("page.html") == []


def test_failed_recompile_keeps_the_previous_page_entry():
    good = '{% cobrastyle a = "a.css" %}{% cobrastyle b = "b.css" %}'
    jinja = make_environment({"a.css": ".a {}", "b.css": ".b {}"}, templates={"page.html": good})
    jinja.get_template("page.html")
    extension = CobrastyleExtension.get(jinja)
    assert extension is not None
    assert extension.page_modules("page.html") == ["a.css", "b.css"]

    # The second tag fails mid-compile: the partial list must never be published
    broken = '{% cobrastyle a = "a.css" %}{% cobrastyle b = "missing.css" %}'
    with pytest.raises(TemplateSyntaxError):
        jinja.compile(broken, name="page.html", filename="page.html")

    assert extension.page_modules("page.html") == ["a.css", "b.css"]


def test_reconfigure_takes_effect():
    jinja = make_environment({"a.css": ".a { color: red; }"})
    jinja.from_string('{% cobrastyle styles = "a.css" %}').render()

    configure(jinja, resolver=InMemoryResolver({"b.css": ".b { color: blue; }"}), module_pattern="[local]", minify=True)

    extension = CobrastyleExtension.get(jinja)
    assert extension is not None
    assert extension.manager.minify is True
    template = jinja.from_string('{% cobrastyle styles = "b.css" %}{{ styles.b }}')  # resolved by the new resolver
    assert template.render() == "b"


def test_fragment_links_dedup_ignores_query_strings():
    html = fragment_links_html(["/static/a.css?v=123"])

    # Both sides of the client-side dedup compare without query strings
    assert 'have[url.split("?")[0]]' in html


def test_cx_global():
    result = render_jinja(
        """
    {% cobrastyle styles = "test.css" %}
    {{ cx(styles.header, {styles.footer: True, "hidden": False}) }}
    """,
        {"test.css": ".header { color: red; } .footer { color: blue; }"},
    )
    assert result == "header footer"


def test_cx_autoescaped():
    jinja = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension], autoescape=True)
    configure(jinja, resolver=InMemoryResolver({}))
    result = jinja.from_string("{{ cx(evil) }}").render(evil='a" onload="x')
    assert result == "a&#34; onload=&#34;x"


def test_configure_forwards_compile_options():
    jinja = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension])
    configure(
        jinja,
        resolver=InMemoryResolver({"test.css": ".a { user-select: none; }"}),
        minify=True,
        targets=["safari >= 13"],
        source_map=False,
    )
    extension = CobrastyleExtension.get(jinja)
    assert extension is not None

    stylesheet = extension.manager.import_module("test.css")

    assert "-webkit-user-select" in stylesheet.code
    assert stylesheet.map is None
    assert "\n" not in stylesheet.code.strip()  # minified


def test_links_include_the_parents_own_styles_first():
    """A layout's own stylesheet is linked before the extending child's, so page rules win the cascade."""
    resources = {"layout.css": ".shell { color: black; }", "page.css": ".title { color: red; }"}
    templates = {
        "base.html": '{% cobrastyle base = "layout.css" %}<head>{{ cobrastyle.links() }}</head>'
        "{% block content %}{% endblock %}",
        "child.html": '{% extends "base.html" %}{% block content %}'
        '{% cobrastyle styles = "page.css" %}{{ styles.title }}{% endblock %}',
    }
    html = make_environment(resources, templates).get_template("child.html").render()

    assert "layout.css" in html
    assert "page.css" in html
    assert html.index("layout.css") < html.index("page.css")


def test_links_cover_every_level_of_an_extends_chain():
    resources = {"grand.css": ".g {}", "mid.css": ".m {}", "page.css": ".p {}"}
    templates = {
        "grand.html": '{% cobrastyle g = "grand.css" %}{{ cobrastyle.links() }}{% block a %}{% endblock %}',
        "mid.html": '{% extends "grand.html" %}{% cobrastyle m = "mid.css" %}'
        "{% block a %}{{ m.m }}{% block b %}{% endblock %}{% endblock %}",
        "page.html": '{% extends "mid.html" %}{% block b %}{% cobrastyle p = "page.css" %}{{ p.p }}{% endblock %}',
    }
    html = make_environment(resources, templates).get_template("page.html").render()

    assert html.index("grand.css") < html.index("mid.css") < html.index("page.css")


def test_custom_delimiters_keep_page_tracking_working():
    jinja = Environment(
        loader=DictLoader({}),
        extensions=[CobrastyleExtension],
        block_start_string="<%",
        block_end_string="%>",
        variable_start_string="<<",
        variable_end_string=">>",
    )
    configure(jinja, resolver=InMemoryResolver({"a.css": ".x { color: red; }"}), module_pattern="[local]")

    html = jinja.from_string('<% cobrastyle s = "a.css" %><< cobrastyle.links() >><< s.x >>').render()

    assert "{% set" not in html  # the injected tracking statement must never leak into output
    assert html == '<link rel="stylesheet" href="a.css" />x'


def test_from_string_recompiles_reuse_one_page_registry_entry():
    jinja = make_environment({"a.css": ".x {}"}, {})
    extension = CobrastyleExtension.get(jinja)
    assert extension is not None

    for _ in range(5):
        jinja.from_string('{% cobrastyle s = "a.css" %}{{ cobrastyle.links() }}').render()

    assert len(extension._pages) == 1


def test_fragment_links_emits_oob_script():
    resources = {"test.css": ".header { color: red; }"}
    result = render_jinja(
        '{% cobrastyle styles = "test.css" %}{{ cobrastyle.fragment_links() }}<div class="{{ styles.header }}"></div>',
        resources,
    )

    assert '<div hx-swap-oob="beforeend:head"><script>' in result
    assert '["test.css"]' in result
    assert '<div class="header"></div>' in result


def test_fragment_links_composed_module_is_bundled_not_listed():
    resources = {
        "base.css": ".base { color: black; }",
        "button.css": '.button { composes: base from "./base.css"; background: red; }',
    }
    result = render_jinja('{% cobrastyle styles = "button.css" %}{{ cobrastyle.fragment_links() }}', resources)

    assert '["button.css"]' in result
    assert "base.css" not in result


def test_fragment_links_empty_without_modules():
    result = render_jinja("x{{ cobrastyle.fragment_links() }}y", {})

    assert result == "xy"


def test_fragment_links_escapes_the_nonce():
    resources = {"test.css": ".header { color: red; }"}
    result = render_jinja(
        '{% cobrastyle styles = "test.css" %}{{ cobrastyle.fragment_links(nonce=\'ab"c\') }}',
        resources,
    )

    assert '<script nonce="ab&quot;c">' in result
    assert 'hx-swap-oob="beforeend:head"' in result


def test_fragment_links_uses_manifest_urls():
    from cobrastyle.manifest import Manifest, ModuleEntry

    manifest = Manifest(
        modules={"page.css": ModuleEntry(file="page.abc.css", url="/static/page.abc.css", classes={"title": "t"})}
    )
    jinja = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension])
    configure(jinja, manifest=manifest)

    result = jinja.from_string('{% cobrastyle styles = "page.css" %}{{ cobrastyle.fragment_links() }}').render()

    assert '["/static/page.abc.css"]' in result


def test_links_emit_hot_reload_script_when_configured():
    jinja = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension])
    configure(jinja, resolver=InMemoryResolver({"a.css": ".a { color: red; }"}), hot_reload="/styles/")

    result = jinja.from_string('{% cobrastyle s = "a.css" %}{{ cobrastyle.links() }}').render()

    assert '<script src="/styles/__client__.js" data-events="/styles/__events__" defer></script>' in result


def test_links_hot_reload_defaults_off():
    result = render_jinja('{% cobrastyle s = "a.css" %}{{ cobrastyle.links() }}', {"a.css": ".a { color: red; }"})

    assert "__client__.js" not in result


def test_hot_reload_prefix_comes_from_the_resolver(tmp_path):
    from cobrastyle import FileSystemResolver

    (tmp_path / "a.css").write_text(".a { color: red; }")
    jinja = Environment(loader=DictLoader({}), extensions=[CobrastyleExtension])
    configure(jinja, resolver=FileSystemResolver(tmp_path, url_prefix="/x/"), hot_reload=True)

    result = jinja.from_string('{% cobrastyle s = "a.css" %}{{ cobrastyle.links() }}').render()

    assert 'src="/x/__client__.js"' in result


def test_hot_reload_true_requires_a_url_prefix():
    jinja = Environment(extensions=[CobrastyleExtension])

    with pytest.raises(TypeError, match="url_prefix"):
        configure(jinja, resolver=InMemoryResolver({}), hot_reload=True)


def test_hot_reload_is_rejected_in_manifest_mode():
    from typing import Any, cast

    from cobrastyle.manifest import Manifest

    jinja = Environment(extensions=[CobrastyleExtension])
    untyped_configure = cast(Any, configure)  # the overloads make this call unwritable in typed code

    with pytest.raises(TypeError, match="dev-only"):
        untyped_configure(jinja, manifest=Manifest(modules={}), hot_reload=True)


def test_fragment_links_carries_the_cloak_machinery():
    resources = {"test.css": ".header { color: red; }"}
    result = render_jinja('{% cobrastyle styles = "test.css" %}{{ cobrastyle.fragment_links() }}', resources)

    # The reveal runs whether stylesheets loaded, errored, or timed out
    assert "[data-cobrastyle-cloak]{display:none !important}" in result
    assert 'removeAttribute("data-cobrastyle-cloak")' in result
    assert "link.onload=link.onerror=" in result
    assert "setTimeout(resolve,3000)" in result
