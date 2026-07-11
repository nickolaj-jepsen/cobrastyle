from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1


class ManifestError(Exception):
    """The manifest is missing, malformed, or from an incompatible cobrastyle."""


@dataclass(frozen=True)
class ModuleEntry:
    file: str
    url: str
    classes: dict[str, str]
    assets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AssetEntry:
    file: str
    url: str


@dataclass
class Manifest:
    """The build → prod-runtime contract.

    ``modules`` maps resolver-relative module paths to their built output;
    ``pages`` maps template names to the module paths they statically import.
    """

    generator: dict[str, str] = field(default_factory=dict)
    modules: dict[str, ModuleEntry] = field(default_factory=dict)
    assets: dict[str, AssetEntry] = field(default_factory=dict)
    pages: dict[str, list[str]] = field(default_factory=dict)
    version: int = MANIFEST_VERSION

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        if not isinstance(data, dict):
            raise ManifestError(f"Malformed manifest: expected a JSON object, got {type(data).__name__}")
        version = data.get("version")
        if version != MANIFEST_VERSION:
            raise ManifestError(
                f"Unsupported manifest version {version!r} (expected {MANIFEST_VERSION}); "
                "rebuild with a matching cobrastyle."
            )
        try:
            return cls(
                version=version,
                generator=dict(data.get("generator", {})),
                modules={path: ModuleEntry(**entry) for path, entry in data["modules"].items()},
                assets={path: AssetEntry(**entry) for path, entry in data.get("assets", {}).items()},
                pages={name: list(paths) for name, paths in data.get("pages", {}).items()},
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ManifestError(f"Malformed manifest: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generator": dict(sorted(self.generator.items())),
            "modules": {path: asdict(self.modules[path]) for path in sorted(self.modules)},
            "assets": {path: asdict(self.assets[path]) for path in sorted(self.assets)},
            "pages": {name: list(self.pages[name]) for name in sorted(self.pages)},
        }

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ManifestError(f"Manifest not found at {path}; did you run `cobrastyle build`?") from exc
        except json.JSONDecodeError as exc:
            raise ManifestError(f"Manifest at {path} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    def dump(self, path: str | Path) -> None:
        """Write the manifest as deterministic, diff-friendly JSON."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
