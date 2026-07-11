import posixpath
import threading
from typing import NamedTuple

from cobrastyle.paths import normalize_path
from cobrastyle.resolvers import FileResolver
from cobrastyle_lightningcss import CssModuleExport, CssModuleReference, Dependency, transform


class Stylesheet(NamedTuple):
    path: str
    url: str
    code: str
    classes: dict[str, str]
    mtime: float | None = None
    # url()/@import dependencies (only when analyze_dependencies is on)
    dependencies: tuple[Dependency, ...] = ()
    # Module paths this module composes classes from
    composes: tuple[str, ...] = ()


class CobrastyleManager:
    """Compiles CSS modules through a resolver and caches the results by path.

    Cached entries are revalidated against the resolver's freshness token
    (``mtime``) on every import, so edited files recompile. Class names hash
    the file *path*, not its content — a recompile keeps existing class maps
    valid; only adding/removing classes requires reloading templates that
    baked the old map in.

    ``composes: name from "./other.css"`` imports the other module
    recursively; the referenced modules are recorded in
    :attr:`Stylesheet.composes` so pages can link them too.
    """

    def __init__(
        self,
        resolver: FileResolver,
        *,
        minify: bool = True,
        module_pattern: str | None = None,
        rewrite_class_names: bool = True,
        targets: list[str] | None = None,
        analyze_dependencies: bool = False,
    ):
        self.resolver = resolver
        self.minify = minify
        self.module_pattern = module_pattern
        self.rewrite_class_names = rewrite_class_names
        self.targets = targets
        self.analyze_dependencies = analyze_dependencies
        self._cache: dict[str, Stylesheet] = {}
        self._lock = threading.RLock()
        self._visiting = threading.local()

    def import_module(self, path: str) -> Stylesheet:
        """Resolve, compile and cache the CSS module at ``path``."""
        path = normalize_path(path)
        with self._lock:
            if (cached := self._cache.get(path)) and cached.mtime == self.resolver.mtime(path):
                return cached

            visiting: set[str] = getattr(self._visiting, "paths", None) or set()
            self._visiting.paths = visiting
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

    def _compile(self, path: str) -> Stylesheet:
        resolved = self.resolver.resolve(path)
        result = transform(
            filename=path,
            code=resolved.content,
            module=True,
            module_pattern=self.module_pattern,
            minify=self.minify,
            targets=self.targets,
            analyze_dependencies=self.analyze_dependencies,
        )
        composes: list[str] = []
        classes = self._class_map(path, result.exports or {}, composes)
        return Stylesheet(
            path=path,
            url=resolved.url,
            code=result.code,
            classes=classes,
            mtime=resolved.mtime,
            dependencies=tuple(result.dependencies or ()),
            composes=tuple(composes),
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
