class CssModuleReference:
    name: str

class Local(CssModuleReference):
    pass

class Global(CssModuleReference):
    pass

class Dependency(CssModuleReference):
    specifier: str

class CssModuleExport:
    name: str
    composes: list[CssModuleReference]
    is_referenced: bool

class TransformResult:
    code: str
    exports: dict[str, CssModuleExport] | None

def transform(
    filename: str,
    code: str,
    module: bool = False,
    minify: bool = False,
    module_pattern: str | None = None,
) -> TransformResult: ...
