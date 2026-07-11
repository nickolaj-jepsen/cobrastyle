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
