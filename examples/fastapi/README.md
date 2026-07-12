# FastAPI example

Run from this directory (after `just sync` at the repo root).

## Dev mode

```sh
uv run uvicorn app:app --reload
```

Open http://127.0.0.1:8000/ — stylesheets in `styles/` compile on demand and are served
at `/cobrastyle/`. Edit a `.css` file and the styles update in the open page without a reload.

## Prod mode

```sh
uv run cobrastyle build app:templates --clean
COBRASTYLE_ENV=prod uv run uvicorn app:app
```

The build writes hashed CSS, the rewritten `url()` asset, and `manifest.json` into
`static/cobrastyle/`. In prod mode the app reads only the manifest — the compiler is never
imported — and a plain `StaticFiles` mount serves the built files.
