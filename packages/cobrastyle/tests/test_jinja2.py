from textwrap import dedent

import pytest
from jinja2 import DictLoader, Environment, TemplateSyntaxError

from cobrastyle import InMemoryResolver
from cobrastyle.jinja2 import CobrastyleExtension, configure


def _clean(template: str) -> str:
    return "\n".join(line for line in dedent(template).splitlines() if line.strip()).strip()


def make_environment(
    resources: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    rewrite_class_names: bool = True,
    **kwargs,
) -> Environment:
    jinja = Environment(loader=DictLoader(templates or {}), extensions=[CobrastyleExtension], **kwargs)
    configure(
        jinja,
        resolver=InMemoryResolver(resources or {}),
        minify=False,
        module_pattern="[local]",
        rewrite_class_names=rewrite_class_names,
    )
    return jinja


def render_jinja(
    template: str,
    resources: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    rewrite_class_names: bool = True,
    **kwargs,
) -> str:
    jinja = make_environment(resources, templates, rewrite_class_names, **kwargs)
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
        rewrite_class_names=True,
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
        rewrite_class_names=False,
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
    with pytest.raises(KeyError):
        jinja.from_string('{% cobrastyle styles = "missing.css" %}')


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
