# Django example

One project, both template engines: the home page renders through the Jinja2 backend,
the About page through the Django Template Language tag library. Both share the CSS
modules in `styles/`. Run from this directory (after `just sync` at the repo root).

## Dev mode

```sh
uv run python manage.py runserver
```

Open http://127.0.0.1:8000/ — stylesheets compile on demand and are served at
`/cobrastyle/` (DEBUG only). Edit a `.css` file and the styles update in the open page
without a reload. The About page's "Load a tip over HTMX" button fetches a DTL fragment
whose stylesheet the page never linked; `{% cobrastyle_fragment_links %}` adds it
out-of-band before the swap.

## Prod mode

```sh
uv run python manage.py cobrastyle_build --clean
DJANGO_DEBUG=0 uv run python manage.py runserver --insecure
```

The build covers Jinja2 and DTL templates in one pass, writing hashed CSS and
`manifest.json` into `cobrastyle_static/cobrastyle/`. With `DEBUG=0` the app reads only
the manifest, and `<link>` URLs resolve through Django's `static()`, so hashed storages
and CDN `STATIC_URL`s apply automatically. `--insecure` makes runserver serve static
files for the demo; a real deployment runs `manage.py collectstatic` after the build
(the `CobrastyleFinder` entry in `STATICFILES_FINDERS` ships the output, minus
`manifest.json`) and serves `static_root/` from a web server.
