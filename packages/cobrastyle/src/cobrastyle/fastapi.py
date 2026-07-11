from pathlib import Path
from typing import Any

from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manifest import Manifest
from cobrastyle.resolvers import FileResolver, FileSystemResolver
from cobrastyle.serve import CobrastyleASGIApp


def install(
    target: Any,
    app: Any = None,
    *,
    resolver: FileResolver | None = None,
    manifest: Manifest | str | Path | None = None,
    root: str | Path | None = None,
    url_prefix: str = "/cobrastyle/",
    serve: bool = True,
    **options: Any,
) -> None:
    """Wire cobrastyle into a Starlette/FastAPI ``Jinja2Templates`` (or bare Environment).

    ``target`` is a ``Jinja2Templates`` instance or a ``jinja2.Environment``.
    Dev mode: provide either ``resolver`` or ``root`` (styles directory,
    served at ``url_prefix``); when ``app`` is given and ``serve`` is on, the
    dev CSS server is mounted at the resolver's URL prefix. Prod mode: pass
    ``manifest=`` instead — nothing is mounted, the built files are static.
    """
    environment = target if isinstance(target, Environment) else getattr(target, "env", None)
    if not isinstance(environment, Environment):
        raise TypeError(f"Expected a jinja2.Environment or Jinja2Templates, got {type(target).__name__}")

    environment.add_extension(CobrastyleExtension)

    if manifest is not None:
        configure(environment, manifest=manifest, **options)
        return

    if resolver is None:
        if root is None:
            raise TypeError("Provide either resolver=, root= (the styles directory), or manifest= (prod)")
        resolver = FileSystemResolver(root, url_prefix=url_prefix)
    configure(environment, resolver=resolver, **options)

    if app is not None and serve:
        extension = CobrastyleExtension.get(environment)
        assert extension is not None  # add_extension above guarantees it
        prefix = getattr(resolver, "url_prefix", url_prefix).rstrip("/")
        app.mount(prefix, CobrastyleASGIApp(extension.manager))
