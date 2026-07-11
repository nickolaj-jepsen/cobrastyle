from typing import final

class TransformError(ValueError):
    """Raised when a stylesheet cannot be parsed, minified or printed."""

class CssModuleReference:
    @final
    class Local(CssModuleReference):
        name: str

    @final
    class Global(CssModuleReference):
        name: str

    @final
    class Dependency(CssModuleReference):
        name: str
        specifier: str

@final
class CssModuleExport:
    name: str
    composes: list[CssModuleReference]
    is_referenced: bool

@final
class SourceRange:
    file_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

class Dependency:
    @final
    class Url(Dependency):
        url: str
        placeholder: str
        loc: SourceRange

    @final
    class Import(Dependency):
        url: str
        placeholder: str
        supports: str | None
        media: str | None
        loc: SourceRange

@final
class TransformResult:
    code: str
    exports: dict[str, CssModuleExport] | None
    dependencies: list[Dependency] | None

LIGHTNINGCSS_VERSION: str

def transform(
    filename: str,
    code: str,
    *,
    module: bool = False,
    module_pattern: str | None = None,
    minify: bool = False,
    targets: list[str] | None = None,
    analyze_dependencies: bool = False,
    remove_imports: bool = False,
) -> TransformResult:
    """Parse, minify and print a stylesheet, optionally as a CSS module.

    Args:
        filename: Name used in error messages and CSS module patterns.
        code: The CSS source.
        module: Parse the stylesheet as a CSS module.
        module_pattern: CSS module class name pattern, e.g. ``[hash]-[local]``.
        minify: Emit minified CSS.
        targets: Browserslist queries used for vendor prefixing and syntax lowering.
        analyze_dependencies: Replace ``url()``/``@import`` references with
            placeholder tokens and report them in ``TransformResult.dependencies``.
        remove_imports: Drop ``@import`` rules from the output (only with
            ``analyze_dependencies``).

    Raises:
        TransformError: If the stylesheet or any option cannot be processed.
    """
