from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, request

from cobrastyle.jinja2 import CobrastyleExtension, configure
from cobrastyle.manifest import Manifest
from cobrastyle.resolvers import FileResolver, FileSystemResolver
from cobrastyle.serve import serve


class Cobrastyle:
    """Flask extension wiring cobrastyle into ``app.jinja_env``.

    Dev mode (default): registers :class:`CobrastyleExtension`, configures it
    with the given resolver (default: ``<app root>/styles`` served at
    ``url_prefix``), and adds a serve route at the resolver's URL prefix.
    Avoid ``/static/`` as the prefix — Flask's own static route already owns it.

    Prod mode (``manifest=``): class maps and URLs come from the manifest;
    nothing is registered for serving — the built files are static.
    """

    def __init__(
        self,
        app: Flask | None = None,
        *,
        resolver: FileResolver | None = None,
        manifest: Manifest | str | Path | None = None,
        root: str | Path | None = None,
        url_prefix: str = "/cobrastyle/",
        serve: bool = True,
        **options: Any,
    ) -> None:
        self._resolver = resolver
        self._manifest = manifest
        self._root = root
        self._url_prefix = url_prefix
        self._serve = serve
        self._options = options
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        app.jinja_env.add_extension(CobrastyleExtension)

        if self._manifest is not None:
            configure(app.jinja_env, manifest=self._manifest, **self._options)
        else:
            resolver = self._resolver or FileSystemResolver(
                self._root or Path(app.root_path) / "styles", url_prefix=self._url_prefix
            )
            configure(app.jinja_env, resolver=resolver, **self._options)
            if self._serve:
                extension = CobrastyleExtension.get(app.jinja_env)
                assert extension is not None  # add_extension above guarantees it
                prefix = getattr(resolver, "url_prefix", self._url_prefix)

                def serve_css(filename: str) -> Response:
                    result = serve(
                        extension.manager,
                        filename,
                        method=request.method,
                        if_none_match=request.headers.get("If-None-Match"),
                    )
                    if result is None:
                        abort(404)
                    return Response(result.body, status=result.status, headers=result.headers)

                app.add_url_rule(f"{prefix.rstrip('/')}/<path:filename>", endpoint="cobrastyle", view_func=serve_css)

        app.extensions["cobrastyle"] = self


def init_app(app: Flask, **kwargs: Any) -> Cobrastyle:
    """Shorthand for ``Cobrastyle(app, **kwargs)``."""
    return Cobrastyle(app, **kwargs)
