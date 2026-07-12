from pathlib import Path
from typing import Protocol, Unpack, overload

from jinja2 import Environment

from cobrastyle.jinja2 import CobrastyleExtension, ConfigureOptions, configure, configure_dev
from cobrastyle.manifest import Manifest
from cobrastyle.resolvers import FileResolver
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
    hot_reload: bool | None = ...,
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
    hot_reload: bool | None = ...,
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
    hot_reload: bool | None = None,
    **options: Unpack[ConfigureOptions],
) -> None:
    """Wire cobrastyle into a Starlette/FastAPI ``Jinja2Templates`` (or bare Environment).

    ``target`` is a ``Jinja2Templates`` instance or a ``jinja2.Environment``.
    Dev mode: provide either ``resolver`` or ``root`` (styles directory,
    served at ``url_prefix``); when ``app`` is given and ``serve`` is on, the
    dev CSS server is mounted at the resolver's URL prefix, with CSS hot
    reload on (``hot_reload=`` overrides either way). Prod mode: pass
    ``manifest=`` instead — nothing is mounted, the built files are static.
    """
    environment = target if isinstance(target, Environment) else getattr(target, "env", None)
    if not isinstance(environment, Environment):
        raise TypeError(f"Expected a jinja2.Environment or Jinja2Templates, got {type(target).__name__}")

    if manifest is not None:
        environment.add_extension(CobrastyleExtension)
        configure(environment, manifest=manifest, **options)
        return

    serving = app is not None and serve
    extension, prefix = configure_dev(
        environment,
        resolver=resolver,
        root=root,
        url_prefix=url_prefix,
        hot_reload=serving if hot_reload is None else hot_reload,
        **options,
    )
    if serving:
        assert app is not None
        app.mount(prefix.rstrip("/"), CobrastyleASGIApp(extension.manager))
