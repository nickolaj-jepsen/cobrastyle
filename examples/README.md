# Examples

Three self-contained example apps, each a small two-page site that runs in dev mode
(stylesheets compile on demand, edits show up on refresh) and builds for production
(hashed CSS + `manifest.json`, no compiler at runtime):

- [flask/](./flask) — the `Cobrastyle` Flask extension, prod files served by Flask's `/static/` route
- [fastapi/](./fastapi) — `cobrastyle.fastapi.install` on `Jinja2Templates`, prod files via `StaticFiles`
- [django/](./django) — one project running the Jinja2 backend and the DTL tag library side by side,
  built with `manage.py cobrastyle_build` and shipped through `collectstatic`

All three demonstrate cross-file `composes` (a shared `base.css` primitive) and a `url()`
asset flowing through dev serving and the hashed prod build.

Setup once from the repo root (builds the Rust extension):

```sh
just sync            # or: uv sync --all-packages
```

Then follow the README inside each example, or run one straight from the repo root:

```sh
just example flask           # dev mode (also: fastapi, django)
just example django prod     # build, then serve the production app
```

The examples are exercised by the test suite (`examples/tests`), so they stay working.
