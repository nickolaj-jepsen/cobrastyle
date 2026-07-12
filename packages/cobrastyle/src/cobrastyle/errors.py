"""The cobrastyle exception hierarchy. Safe on the prod path: never imports the compiler."""


class CobrastyleError(Exception):
    """Base for every exception cobrastyle raises."""


class BuildError(CobrastyleError):
    """The build cannot produce a complete, correct manifest."""


class ManifestError(CobrastyleError):
    """The manifest is missing, malformed, or from an incompatible cobrastyle."""


class StylesheetNotFoundError(CobrastyleError, LookupError):
    """A module path that resolves to nothing: unknown to the resolver (dev) or missing from the manifest (prod)."""

    def __init__(self, message: str, *, path: str | None = None):
        super().__init__(message)
        self.path = path


class StylesheetPathError(CobrastyleError, ValueError):
    """A module path that is invalid: absolute, escaping the resolver root, or not naming a file."""


class StylesheetDecodeError(CobrastyleError, ValueError):
    """A stylesheet's bytes are not valid UTF-8."""


class CircularComposesError(CobrastyleError, ValueError):
    """CSS modules ``composes`` each other in a cycle."""


class ComposesExportError(CobrastyleError, LookupError):
    """A ``composes`` reference names a class its target module does not export."""
