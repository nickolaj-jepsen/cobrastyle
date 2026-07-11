# Flask example

Run from this directory (after `just sync` at the repo root).

## Dev mode

```sh
uv run flask --app app run
```

Open http://127.0.0.1:5000/ — stylesheets in `styles/` compile on demand and are served
at `/cobrastyle/`. Edit a `.css` file and refresh.

## Prod mode

```sh
uv run cobrastyle build app:create_app -o static/cobrastyle --url-prefix /static/cobrastyle/ --clean
COBRASTYLE_ENV=prod uv run flask --app app run
```

The build writes hashed CSS, the rewritten `url()` asset, and `manifest.json` into
`static/cobrastyle/`. In prod mode the app reads only the manifest — the compiler is never
imported — and Flask's regular `/static/` route serves the built files.
