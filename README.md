# cobrastyle
_CSS modules for Python_

> [!WARNING]
> In active development! APIs may still change before 1.0.

## Description

Cobrastyle provides CSS modules support for Python template engines and frameworks, with
excellent performance provided by [LightningCSS](https://lightningcss.dev/).

It has two modes:

- **Dev mode** — stylesheets compile ad-hoc when a page renders, served from memory, fresh
  on every refresh (edits to `@import`ed and composed-from files included). No build step,
  no watcher. Output is readable and carries an inline source map, so devtools point at
  the file you actually wrote.
- **Prod mode** — `cobrastyle build` walks every template, compiles all stylesheets into a
  content-hashed static directory plus a `manifest.json`. The production runtime reads only
  the manifest: no compiler, no Rust wheel, near-zero overhead.

## Packages

- [cobrastyle](./packages/cobrastyle): CSS modules for Python. Extras: `[jinja2]`, `[flask]`,
  `[fastapi]`, `[django]`, `[cli]`
- [cobrastyle-lightningcss](./packages/cobrastyle-lightningcss): Python bindings for LightningCSS

## Examples

Runnable example apps live in [examples/](./examples): [Flask](./examples/flask),
[FastAPI](./examples/fastapi), and [Django](./examples/django) (Jinja2 and DTL side by
side). Each runs in dev mode and has a one-command production build.

## Usage

### Jinja2

```python
from jinja2 import Environment, FileSystemLoader
from cobrastyle import FileSystemResolver
from cobrastyle.jinja2 import CobrastyleExtension, configure

env = Environment(loader=FileSystemLoader("templates"), extensions=[CobrastyleExtension])
configure(env, resolver=FileSystemResolver("styles"))          # dev
# configure(env, manifest="dist/manifest.json")                # prod
```

```jinja
<head>{{ cobrastyle.links() }}</head>
{% cobrastyle styles = "button.css" %}
<button class="{{ styles.button }}">Click me</button>
```

Class maps bake into the compiled template at load time; `cobrastyle.links()` renders
`<link>` tags for every module the page (including inheriting children) imports, parent
templates' modules first so page rules win the cascade. `composes: name from "./other.css"`
works across files, and `@import` works between modules — both bundle the referenced
file's rules into the importing module's CSS.

### Flask

```python
from cobrastyle.flask import Cobrastyle

app = Flask(__name__)
Cobrastyle(app)                                   # dev: <root>/styles served at /cobrastyle/
# Cobrastyle(app, manifest="dist/manifest.json")  # prod
```

### FastAPI / Starlette

```python
from cobrastyle.fastapi import install

templates = Jinja2Templates(directory="templates")
install(templates, app, root="styles")                     # dev: mounts a CSS server at /cobrastyle/
# install(templates, app, manifest="dist/manifest.json")   # prod
```

### Django

```python
# settings.py
TEMPLATES = [{
    "BACKEND": "django.template.backends.jinja2.Jinja2",
    "DIRS": [BASE_DIR / "templates"],
    "OPTIONS": {"environment": "cobrastyle.django.environment"},
}]
INSTALLED_APPS = [..., "cobrastyle.django"]
COBRASTYLE = {"ROOT": BASE_DIR / "styles"}   # dev/prod follows DEBUG; override with "DEV"

# urls.py (dev serving, DEBUG only)
path("cobrastyle/", include("cobrastyle.django.urls")),
```

Django Template Language works too — same modules, same build:

```django
{% load cobrastyle %}
{% cobrastyle "button.css" as styles %}
<head>{% cobrastyle_links %}</head>
<button class="{{ styles.button }}"></button>
```

Deploying is two steps: build, then collect. The finder hands the build output to
`collectstatic` — except `manifest.json`, which the app reads from the output directory
at startup, so ship that directory with the app even when static files live elsewhere:

```python
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "cobrastyle.django.finders.CobrastyleFinder",
]
```

```sh
manage.py cobrastyle_build   # covers Jinja2 and DTL templates in one pass
manage.py collectstatic
```

In prod, `<link>` URLs resolve through Django's `static()` and `url()` references inside
built CSS are relative — hashed storages (`ManifestStaticFilesStorage`, WhiteNoise's
compressed variant) and CDN `STATIC_URL`s apply with no further config. To keep the URLs
baked at build time instead, set `COBRASTYLE["BUILD_URL_PREFIX"]` (or
`"STATIC_PREFIX": None` to disable the staticfiles integration entirely).

With [WhiteNoise](https://whitenoise.readthedocs.io/) on **plain** static storage, add
far-future caching for cobrastyle's content-hashed files:

```python
from cobrastyle.django import immutable_file_test
WHITENOISE_IMMUTABLE_FILE_TEST = immutable_file_test
```

(Hashed storages need no help — WhiteNoise already recognizes their names.)

### Building (non-Django)

```sh
cobrastyle build myapp:create_environment --out dist --url-prefix /static/
```

The target is adapted by type: a `jinja2.Environment`, a Flask app, a `Jinja2Templates`
instance, or a zero-arg factory returning any of these. The output directory contains
content-hashed CSS (plus any `url()` assets, rewritten) and `manifest.json` — the sole
input the prod runtime needs. Builds are deterministic: unchanged input produces
byte-identical output.

### Known limitations

- Stylesheets imported inside `{% include %}`d templates aren't seen by `links()` in the
  including page's head; pass them explicitly (`{% cobrastyle_links "shared/nav.css" %}` in
  DTL) or import them from the page template.

## Development

The dev environment is managed with [Nix](https://nixos.org/): `nix develop` provides the
Rust toolchain, Python, [uv](https://docs.astral.sh/uv/), just, and watchexec, syncs the
virtualenv, and installs the git hooks. Without Nix, install uv and
[just](https://github.com/casey/just) yourself; rustup picks the toolchain from
`rust-toolchain.toml`.

Common tasks are wrapped in a [justfile](./justfile):

```sh
just sync       # set up the virtualenv (builds the Rust extension)
just test       # run the test suite
just watch      # re-run tests on file changes
just lint       # ruff + rustfmt + clippy
just typecheck  # pyrefly
just fmt        # auto-format everything
just check      # everything CI runs
just example flask dev   # run an example app (flask|fastapi|django, dev|prod)
```

Editing Rust sources is covered by `just sync`/`just test` — uv rebuilds the extension
whenever they change.
