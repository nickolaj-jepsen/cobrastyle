from pathlib import Path
from typing import Protocol, Unpack, overload

from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, ConfigureOptions, configure
from cobrastyle.manifest import Manifest
from cobrastyle.resolvers import FileResolver, FileSystemResolver, HasUrlPrefix
from cobrastyle.serve import CobrastyleASGIApp


class SupportsMount(Protocol):
    """The slice of Starlette/FastAPI :func:`install` needs — no starlette import required."""

    def mount(self, path: str, app: CobrastyleASGIApp) -> None: ...


@overload
def install(
    target: object,
    app: SupportsMount | None = None,
    *,
    resolver: FileResolver,
    url_prefix: str = ...,
    serve: bool = ...,
    **options: Unpack[ConfigureOptions],
) -> None: ...
@overload
def install(
    target: object,
    app: SupportsMount | None = None,
    *,
    root: str | Path,
    url_prefix: str = ...,
    serve: bool = ...,
    **options: Unpack[ConfigureOptions],
) -> None: ...
@overload
def install(
    target: object,
    app: SupportsMount | None = None,
    *,
    manifest: Manifest | str | Path,
    **options: Unpack[ConfigureOptions],
) -> None: ...
def install(
    target: object,
    app: SupportsMount | None = None,
    *,
    resolver: FileResolver | None = None,
    manifest: Manifest | str | Path | None = None,
    root: str | Path | None = None,
    url_prefix: str = "/cobrastyle/",
    serve: bool = True,
    **options: Unpack[ConfigureOptions],
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
        prefix = (resolver.url_prefix if isinstance(resolver, HasUrlPrefix) else url_prefix).rstrip("/")
        app.mount(prefix, CobrastyleASGIApp(extension.manager))
