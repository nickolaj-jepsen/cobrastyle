from cobrastyle_lightningcss import TransformResult, transform

from cobrastyle_core.file_resolver import FileResolver


class CobrastyleManager:
    def __init__(self, resolver: FileResolver, rewrite_class_names: bool = True, module_pattern: str | None = None):
        self.resolver = resolver
        self.rewrite_class_names = rewrite_class_names
        self.module_pattern = module_pattern
        self.modules: dict[str, TransformResult] = {}

    def _resolve_class_map(self, compiled: TransformResult) -> dict[str, str]:
        exports = {key: value.name for key, value in compiled.exports.items()}
        if self.rewrite_class_names:
            for key, value in list(exports.items()):
                if "-" in key:
                    snake_case = key.replace("-", "_")
                    exports[snake_case] = value
        return exports

    def _compile_styles(self, code: str, path: str) -> TransformResult:
        if path in self.modules:
            return self.modules[path]
        result = transform(
            filename=path,
            code=code,
            module=True,
            minify=True,
            module_pattern=self.module_pattern,
        )
        self.modules[path] = result
        return result

    def used_stylesheets(self) -> list[str]:
        return list(self.modules.keys())

    def links(self) -> str:
        return "".join(f'<link rel="stylesheet" href="{url}" />' for url in self.used_stylesheets())

    def import_module(self, path: str) -> dict[str, str]:
        content, real_path = self.resolver.resolve(path)
        result = self._compile_styles(content, real_path)
        return self._resolve_class_map(result)
