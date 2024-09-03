from django.conf import settings
from django.template import Template, Context, Engine, RequestContext
from django.test import RequestFactory

from cobrastyle_core.file_resolver import InMemoryResolver


def _render_template(template: str, context: dict | None = None) -> str:
    settings.configure(
        DEFAULT_CHARSET="utf-8",
        COBRASTYLE_RESOLVER=InMemoryResolver({
            "test.css": ".header { color: red; }",
        }),
        COBRASTYLE_MODULE_PATTERN="[local]",
    )
    engine = Engine(
        libraries={
            "cobrastyle": "cobrastyle_django.templatetags.cobrastyle",
        },
        context_processors=[
            "cobrastyle_django.context_processors.cobrastyle",
        ]
    )
    t = engine.from_string(template)
    req = RequestFactory().get("/")
    c = RequestContext(req, context or {})
    return t.render(c)


#def test_cobrastyle_resolve_class_name():
#    assert _render_template("{% load cobrastyle %}{% cobrastyle 'test.css' as styles %}{{styles.header}}") == "header"


def test_cobrastyle_used_stylesheets():
    assert _render_template("{% load cobrastyle %}{{ cobrastyle.used_stylesheets.0 }}{% cobrastyle 'test.css' as styles %}") == "test.css"