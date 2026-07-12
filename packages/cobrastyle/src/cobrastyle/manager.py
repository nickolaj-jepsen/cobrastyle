import base64
import hashlib
import posixpath
import re
import threading
from typing import NamedTuple

from cobrastyle.errors import (
    CircularComposesError,
    ComposesExportError,
    StylesheetDecodeError,
    StylesheetNotFoundError,
    StylesheetPathError,
)
from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver
from cobrastyle_lightningcss import CssModuleExport, CssModuleReference, Dependency, bundle

# `button_primary_2x4fBq` reads back to its source in devtools; builds swap in the compact lightningcss default
DEV_MODULE_PATTERN = "[name]_[local]_[hash]"


class Stylesheet(NamedTuple):
    path: str
    url: str
    code: str
    classes: dict[str, str]
    mtime: float | None = None
    # url() and external-@import dependencies (only when analyze_dependencies is on)
    dependencies: tuple[Dependency, ...] = ()
    # Module paths this module composes classes from
    composes: tuple[str, ...] = ()
    # Source map JSON (only when source_map is on)
    map: str | None = None
    # Freshness snapshot: (path, mtime at compile) for every file the compile depended on
    dep_mtimes: tuple[tuple[str, float | None], ...] = ()
    # Dev-serving payload, source map inlined: precomputed so a request is a cache lookup
    body: bytes = b""
    # Weak ETag over the body. Content-hashed, never mtime-based: the entry's mtime
    # misses edits to @imported files, and same-length edits keep the length stable too.
    etag: str = ""


class _BundleProvider:
    """``cobrastyle_lightningcss.bundle()`` file access backed by a :class:`FileResolver`.

    The bundler may call this from non-main threads; resolvers must tolerate
    concurrent reads.
    """

    def __init__(self, resolver: FileResolver, entry: str, entry_content: str):
        self.resolver = resolver
        self.entry = entry
        self.entry_content = entry_content

    def read(self, path: str) -> str:
        # The manager already resolved the entry (for its URL and mtime)
        if path == self.entry:
            return self.entry_content
        try:
            return self.resolver.resolve(path).content
        except (KeyError, OSError) as exc:
            raise StylesheetNotFoundError(f"Stylesheet {path!r} not found by the resolver", path=path) from exc
        except UnicodeDecodeError as exc:
            raise StylesheetDecodeError(f"Stylesheet {path!r} is not valid UTF-8: {exc}") from exc

    def resolve(self, specifier: str, from_path: str) -> str:
        return normalize_path(posixpath.join(posixpath.dirname(from_path), specifier))


class CobrastyleManager:
    """Compiles CSS modules through a resolver and caches the results by path.

    Modules compile through the bundler: non-external ``@import``s are
    inlined into the importing module's output (module-scoped, like the
    importer). Cached entries are revalidated against the resolver's
    freshness token (``mtime``) for every file the compile depended on —
    the module itself, its ``@import``s and its ``composes`` targets — so
    any edit recompiles on the next import. Class names default to the
    readable :data:`DEV_MODULE_PATTERN` (``module_pattern=None``); their
    ``[hash]`` covers the file *path*, not its content — a recompile
    keeps existing class maps valid; only adding/removing classes
    requires reloading templates that baked the old map in.

    ``composes: name from "./other.css"`` imports the other module
    recursively; the referenced modules are recorded in
    :attr:`Stylesheet.composes` so pages can link them too.
    """

    def __init__(
        self,
        resolver: FileResolver,
        *,
        minify: bool = False,
        module_pattern: str | None = None,
        underscore_aliases: bool = True,
        targets: list[str] | None = None,
        analyze_dependencies: bool = False,
        source_map: bool = True,
    ):
        self.resolver = resolver
        self.minify = minify
        self.module_pattern = DEV_MODULE_PATTERN if module_pattern is None else module_pattern
        self.underscore_aliases = underscore_aliases
        self.targets = targets
        self.analyze_dependencies = analyze_dependencies
        self.source_map = source_map
        self._cache: dict[str, Stylesheet] = {}
        self._lock = threading.RLock()
        # Compose-chain paths currently compiling, in call order (cycle detection).
        # A plain list suffices: compiles are fully serialized by the RLock.
        self._visiting: list[str] = []

    def import_module(self, path: str) -> Stylesheet:
        """Resolve, compile and cache the CSS module at ``path``.

        Raises StylesheetNotFoundError when the resolver has no such module,
        CircularComposesError / ComposesExportError for broken ``composes``
        chains, and the compiler's TransformError for CSS that won't compile.
        """
        path = normalize_path(path)
        with self._lock:
            if (cached := self._cache.get(path)) and self._is_fresh(cached):
                return cached

            if path in self._visiting:
                chain = " -> ".join([*self._visiting[self._visiting.index(path) :], path])
                raise CircularComposesError(f"Circular composes chain between CSS modules: {chain}")
            self._visiting.append(path)
            try:
                stylesheet = self._compile(path)
            finally:
                self._visiting.pop()
            self._cache[path] = stylesheet
            return stylesheet

    def _is_fresh(self, stylesheet: Stylesheet) -> bool:
        try:
            return all(self.resolver.mtime(path) == mtime for path, mtime in stylesheet.dep_mtimes)
        except (KeyError, OSError):
            # A dependency disappeared; recompiling raises the real error
            return False

    def _module_pattern_for(self, path: str) -> str:
        if "[name]" not in self.module_pattern:
            return self.module_pattern
        # [name] embeds the stem verbatim; whitespace in it would split the emitted class attribute in two
        stem = re.sub(r"\s+", "_", posixpath.splitext(posixpath.basename(path))[0])
        return self.module_pattern.replace("[name]", stem)

    def _compile(self, path: str) -> Stylesheet:
        try:
            resolved = self.resolver.resolve(path)
        except (KeyError, OSError, StylesheetPathError) as exc:
            # StylesheetPathError here means the *entry* escapes the root (e.g. via a
            # symlink) — a miss, unlike an escaping @import raised mid-bundle.
            raise StylesheetNotFoundError(f"Stylesheet {path!r} not found by the resolver", path=path) from exc
        except UnicodeDecodeError as exc:
            raise StylesheetDecodeError(f"Stylesheet {path!r} is not valid UTF-8: {exc}") from exc
        result = bundle(
            filename=path,
            provider=_BundleProvider(self.resolver, path, resolved.content),
            module=True,
            module_pattern=self._module_pattern_for(path),
            minify=self.minify,
            targets=self.targets,
            analyze_dependencies=self.analyze_dependencies,
            source_map=self.source_map,
        )
        composes: list[str] = []
        classes = self._class_map(path, result.exports or {}, composes)
        dep_mtimes = {file: resolved.mtime if file == path else self.resolver.mtime(file) for file in result.files}
        for composed in composes:
            dep_mtimes.setdefault(composed, self.resolver.mtime(composed))
        served = result.code
        if result.map is not None:
            encoded = base64.b64encode(result.map.encode()).decode()
            served = f"{served}\n/*# sourceMappingURL=data:application/json;base64,{encoded} */"
        body = served.encode()
        return Stylesheet(
            path=path,
            url=resolved.url,
            code=result.code,
            classes=classes,
            mtime=resolved.mtime,
            dependencies=tuple(result.dependencies or ()),
            composes=tuple(composes),
            map=result.map,
            dep_mtimes=tuple(sorted(dep_mtimes.items())),
            body=body,
            etag=f'W/"{hashlib.sha256(body).hexdigest()[:16]}"',
        )

    def get(self, path: str) -> Stylesheet | None:
        """Return the compiled stylesheet for ``path``, if it has been imported."""
        return self._cache.get(normalize_path(path))

    @property
    def stylesheets(self) -> list[Stylesheet]:
        return list(self._cache.values())

    def _class_map(self, path: str, exports: dict[str, CssModuleExport], composes: list[str]) -> dict[str, str]:
        # sorted: exports come from a Rust HashMap, whose order would leak nondeterminism into the manifest
        classes = {name: self._class_value(path, exports[name], composes) for name in sorted(exports)}
        if self.underscore_aliases:
            # Expose `.hello-world` as both styles["hello-world"] and styles.hello_world
            for name, value in list(classes.items()):
                if "-" in name:
                    classes.setdefault(name.replace("-", "_"), value)
        return classes

    def _class_value(self, path: str, export: CssModuleExport, composes: list[str]) -> str:
        names = [export.name]
        for reference in export.composes:
            match reference:
                case CssModuleReference.Local(name=name) | CssModuleReference.Global(name=name):
                    names.append(name)
                case CssModuleReference.Dependency(name=name, specifier=specifier):
                    dependency_path = normalize_path(posixpath.join(posixpath.dirname(path), specifier))
                    dependency = self.import_module(dependency_path)
                    if name not in dependency.classes:
                        raise ComposesExportError(
                            f"{dependency_path!r} does not export a class named {name!r} (composes in {path!r})"
                        )
                    names.append(dependency.classes[name])
                    # Transitive dependencies first, so pages link base styles before derived ones
                    for transitive in (*dependency.composes, dependency_path):
                        if transitive not in composes:
                            composes.append(transitive)
        return " ".join(names)
