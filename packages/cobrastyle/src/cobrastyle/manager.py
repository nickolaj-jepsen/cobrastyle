import posixpath
import threading
from typing import NamedTuple

from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver
from cobrastyle_lightningcss import CssModuleExport, CssModuleReference, Dependency, bundle

# `button_primary_2x4fBq` reads back to its source in devtools; builds swap in the compact lightningcss default
DEV_MODULE_PATTERN = "[name]_[local]_[hash]"


class _VisitingState(threading.local):
    """The compose-chain paths currently being compiled, per thread (cycle detection)."""

    def __init__(self) -> None:
        self.paths: set[str] = set()


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
        return self.resolver.resolve(path).content

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
        rewrite_class_names: bool = True,
        targets: list[str] | None = None,
        analyze_dependencies: bool = False,
        source_map: bool = True,
    ):
        self.resolver = resolver
        self.minify = minify
        self.module_pattern = DEV_MODULE_PATTERN if module_pattern is None else module_pattern
        self.rewrite_class_names = rewrite_class_names
        self.targets = targets
        self.analyze_dependencies = analyze_dependencies
        self.source_map = source_map
        self._cache: dict[str, Stylesheet] = {}
        self._lock = threading.RLock()
        self._visiting = _VisitingState()

    def import_module(self, path: str) -> Stylesheet:
        """Resolve, compile and cache the CSS module at ``path``."""
        path = normalize_path(path)
        with self._lock:
            if (cached := self._cache.get(path)) and self._is_fresh(cached):
                return cached

            visiting = self._visiting.paths
            if path in visiting:
                chain = " -> ".join([*sorted(visiting), path])
                raise ValueError(f"Circular composes chain between CSS modules: {chain}")
            visiting.add(path)
            try:
                stylesheet = self._compile(path)
            finally:
                visiting.discard(path)
            self._cache[path] = stylesheet
            return stylesheet

    def _is_fresh(self, stylesheet: Stylesheet) -> bool:
        try:
            return all(self.resolver.mtime(path) == mtime for path, mtime in stylesheet.dep_mtimes)
        except (KeyError, OSError):
            # A dependency disappeared; recompiling raises the real error
            return False

    def _compile(self, path: str) -> Stylesheet:
        resolved = self.resolver.resolve(path)
        result = bundle(
            filename=path,
            provider=_BundleProvider(self.resolver, path, resolved.content),
            module=True,
            module_pattern=self.module_pattern,
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
        if self.rewrite_class_names:
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
                        raise KeyError(
                            f"{dependency_path!r} does not export a class named {name!r} (composes in {path!r})"
                        )
                    names.append(dependency.classes[name])
                    # Transitive dependencies first, so pages link base styles before derived ones
                    for transitive in (*dependency.composes, dependency_path):
                        if transitive not in composes:
                            composes.append(transitive)
        return " ".join(names)
