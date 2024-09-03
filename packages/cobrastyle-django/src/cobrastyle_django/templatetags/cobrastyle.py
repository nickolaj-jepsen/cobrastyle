import re

from django import template
from django.template import Context
from django.template.base import Parser, Token

from cobrastyle_core import CobrastyleManager

register = template.Library()


class Cobrastyle(template.Node):
    def __init__(self, path, var_name):
        self.path = path
        self.var_name = var_name

    def render(self, context: Context) -> str:
        manager: CobrastyleManager | None = context.get("cobrastyle")
        if manager is None:
            raise template.TemplateSyntaxError(
                "cobrastyle tag requires cobrastyle context processor"
            )

        context[self.var_name] = manager.import_module(self.path)
        print(context)
        return ""


@register.tag
def cobrastyle(parser: Parser, token: Token) -> Cobrastyle:
    # This version uses a regular expression to parse tag contents.
    try:
        # Splitting by None == splitting by spaces.
        tag_name, arg = token.contents.split(None, 1)
    except ValueError:
        tag_name = token.contents.split()[0]
        raise template.TemplateSyntaxError(
            f"{tag_name} tag requires arguments"
        )
    m = re.search(r"(.*?) as (\w+)", arg)
    if not m:
        raise template.TemplateSyntaxError(f"{tag_name} tag had invalid arguments")
    format_string, var_name = m.groups()
    if not (format_string[0] == format_string[-1] and format_string[0] in ('"', "'")):
        raise template.TemplateSyntaxError(
            f"{tag_name} tag's argument should be in quotes"
        )
    return Cobrastyle(format_string[1:-1], var_name)