from typing import Protocol, final

class TransformError(ValueError):
    """Raised when a stylesheet cannot be parsed, minified or printed.

    For errors with a source location, ``filename``, ``line`` (1-based) and
    ``column`` (1-based) are set; otherwise all three are None.
    """

    filename: str | None
    line: int | None
    column: int | None

class CssModuleReference:
    @final
    class Local(CssModuleReference):
        name: str

        def __init__(self, name: str) -> None: ...

    @final
    class Global(CssModuleReference):
        name: str

        def __init__(self, name: str) -> None: ...

    @final
    class Dependency(CssModuleReference):
        name: str
        specifier: str

        def __init__(self, name: str, specifier: str) -> None: ...

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
    map: str | None

@final
class BundleResult:
    code: str
    exports: dict[str, CssModuleExport] | None
    dependencies: list[Dependency] | None
    files: list[str]
    map: str | None

class BundleProvider(Protocol):
    """File access for :func:`bundle`. Both methods may be called from non-main threads."""

    def read(self, path: str, /) -> str:
        """Return the contents of ``path`` (as produced by :meth:`resolve`)."""

    def resolve(self, specifier: str, from_path: str, /) -> str:
        """Resolve an ``@import`` specifier, relative to the importing file, to a path for :meth:`read`."""

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
    source_map: bool = False,
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
        source_map: Return a source map (JSON, sources content embedded) in
            ``TransformResult.map``.

    Raises:
        TransformError: If the stylesheet or any option cannot be processed.
    """

def bundle(
    filename: str,
    provider: BundleProvider,
    *,
    module: bool = False,
    module_pattern: str | None = None,
    minify: bool = False,
    targets: list[str] | None = None,
    analyze_dependencies: bool = False,
    source_map: bool = False,
) -> BundleResult:
    """Bundle a stylesheet and its ``@import``\\ s into one via :func:`transform`'s pipeline.

    Non-external imports are inlined (deduplicated, honoring media/supports/
    layer conditions); scheme/protocol-relative imports stay external. Every
    file is read through ``provider``; the paths it read are reported in
    ``BundleResult.files``. With ``module``, each file's class names hash
    that file's resolved path and only the entry's exports are returned.

    Args:
        filename: Entry path, resolvable by ``provider.read``.
        provider: File access; see :class:`BundleProvider`.
        module: Parse the stylesheets as CSS modules.
        module_pattern: CSS module class name pattern, e.g. ``[hash]-[local]``.
        minify: Emit minified CSS.
        targets: Browserslist queries used for vendor prefixing and syntax lowering.
        analyze_dependencies: Replace ``url()`` (and external ``@import``)
            references with placeholder tokens and report them in
            ``BundleResult.dependencies``.
        source_map: Return a source map (JSON, sources content embedded) in
            ``BundleResult.map``.

    Raises:
        TransformError: If any stylesheet cannot be parsed, minified or
            printed, or an ``@import`` condition is unsupported. Exceptions
            raised by ``provider`` methods propagate unchanged.
    """
