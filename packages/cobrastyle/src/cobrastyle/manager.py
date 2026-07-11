from typing import NamedTuple

from cobrastyle.resolvers import FileResolver
from cobrastyle_lightningcss import CssModuleExport, CssModuleReference, transform


class Stylesheet(NamedTuple):
    path: str
    url: str
    code: str
    classes: dict[str, str]


class CobrastyleManager:
    """Compiles CSS modules through a resolver and caches the results by path."""

    def __init__(
        self,
        resolver: FileResolver,
        *,
        minify: bool = True,
        module_pattern: str | None = None,
        rewrite_class_names: bool = True,
        targets: list[str] | None = None,
    ):
        self.resolver = resolver
        self.minify = minify
        self.module_pattern = module_pattern
        self.rewrite_class_names = rewrite_class_names
        self.targets = targets
        self._cache: dict[str, Stylesheet] = {}

    def import_module(self, path: str) -> Stylesheet:
        """Resolve, compile and cache the CSS module at ``path``."""
        if cached := self._cache.get(path):
            return cached

        resolved = self.resolver.resolve(path)
        result = transform(
            filename=path,
            code=resolved.content,
            module=True,
            module_pattern=self.module_pattern,
            minify=self.minify,
            targets=self.targets,
        )
        stylesheet = Stylesheet(
            path=path,
            url=resolved.url,
            code=result.code,
            classes=self._class_map(result.exports or {}),
        )
        self._cache[path] = stylesheet
        return stylesheet

    def get(self, path: str) -> Stylesheet | None:
        """Return the compiled stylesheet for ``path``, if it has been imported."""
        return self._cache.get(path)

    @property
    def stylesheets(self) -> list[Stylesheet]:
        return list(self._cache.values())

    def _class_map(self, exports: dict[str, CssModuleExport]) -> dict[str, str]:
        classes = {name: self._class_value(export) for name, export in exports.items()}
        if self.rewrite_class_names:
            # Expose `.hello-world` as both styles["hello-world"] and styles.hello_world
            for name, value in list(classes.items()):
                if "-" in name:
                    classes.setdefault(name.replace("-", "_"), value)
        return classes

    def _class_value(self, export: CssModuleExport) -> str:
        names = [export.name]
        for reference in export.composes:
            match reference:
                case CssModuleReference.Local(name=name) | CssModuleReference.Global(name=name):
                    names.append(name)
                case CssModuleReference.Dependency():
                    raise NotImplementedError("composes from other files is not supported yet")
        return " ".join(names)
