from textwrap import dedent

from cobrastyle_core.file_resolver import InMemoryResolver
from cobrastyle_jinja2.ext import CobrastyleExtension
from jinja2 import DictLoader, Environment


def _clean(template: str) -> str:
    # dedent + remove empty lines
    return "\n".join(line for line in dedent(template).splitlines() if line.strip()).strip()


def render_jinja(
    template: str,
    resources: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
    rewrite_class_names: bool = True,
    **kwargs,
) -> str:
    loader = DictLoader(templates or {})
    jinja = Environment(loader=loader, extensions=[CobrastyleExtension], **kwargs)
    jinja.cobrastyle_minify = False
    jinja.cobrastyle_module_pattern = "[local]"
    jinja.cobrastyle_resolver = InMemoryResolver(resources or {})
    jinja.cobrastyle_rewrite_class_names = rewrite_class_names
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

    assert result == _clean("""
    header
    """)


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


def test_context():
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


def test_context_multiple():
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


def test_context_idempotent():
    jinja = Environment(extensions=[CobrastyleExtension])
    jinja.cobrastyle_minify = False
    jinja.cobrastyle_module_pattern = "[local]"
    jinja.cobrastyle_resolver = InMemoryResolver({"test.css": ".header { color: red; }"})

    for _ in range(10):
        result = _clean(
            jinja.from_string(
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
            ).render()
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


def test_context_nested_templates():
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
