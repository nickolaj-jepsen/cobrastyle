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

Build for production with `manage.py cobrastyle_build` (covers Jinja2 and DTL templates);
register the output via a prefixed `STATICFILES_DIRS` entry so `collectstatic` ships it:

```python
STATICFILES_DIRS = [("cobrastyle", BASE_DIR / "cobrastyle_static" / "cobrastyle")]
```

Cobrastyle owns its URLs (`STATIC_URL + "cobrastyle/"`); keep the already-hashed output out
of `ManifestStaticFilesStorage` re-hashing.

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

The dev environment is managed with [Nix](https://nixos.org/) (`nix develop`) and
[uv](https://docs.astral.sh/uv/). Common tasks are wrapped in a [justfile](./justfile):

```sh
just sync       # set up the virtualenv (builds the Rust extension)
just test       # run the test suite
just lint       # ruff + rustfmt + clippy
just typecheck  # pyrefly
just fmt        # auto-format everything
just check      # everything CI runs
```
