import re

from cobrastyle_core import CobrastyleManager
from jinja2 import nodes
from jinja2.ext import Environment, Extension
from jinja2.parser import Parser


class CobrastyleExtension(Extension):
    tags = {"cobrastyle"}
    RE_COBRASTYLE = re.compile(r"{% cobrastyle styles\s*=\s*\"(.+?)\" %}")

    def __init__(self, environment: Environment):
        super().__init__(environment)
        environment.extend(
            cobrastyle_resolver=None,
            cobrastyle_minify=True,
            cobrastyle_rewrite_class_names=True,
            cobrastyle_module_pattern=None,
        )

    def preprocess(self, source: str, name: str | None, filename: str | None = None) -> str:
        manager = CobrastyleManager(
            resolver=self.environment.cobrastyle_resolver,
            rewrite_class_names=self.environment.cobrastyle_rewrite_class_names,
            module_pattern=self.environment.cobrastyle_module_pattern,
        )

        # find all cobrastyle tags, and import them into the cobrastyle manager
        for match in self.RE_COBRASTYLE.finditer(source):
            path = match.group(1)
            manager.import_module(path)

        self.environment.globals["cobrastyle"] = manager
        return source

    def parse(self, parser: Parser) -> nodes.Node | list[nodes.Node]:
        lineno = next(parser.stream).lineno
        manager: CobrastyleManager = self.environment.globals["cobrastyle"]
        variable = parser.parse_assign_target()
        parser.stream.expect("assign")
        file_expression = parser.parse_expression()
        result = manager.import_module(file_expression.value)

        return nodes.Assign(nodes.Name(variable.name, "store"), nodes.Const(result)).set_lineno(lineno)
