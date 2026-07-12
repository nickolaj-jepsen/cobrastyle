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

### Fragments (HTMX)

A template rendered as a fragment (an HTMX partial swap) never renders `<head>`, so a
stylesheet only the fragment uses would arrive unlinked. Call `fragment_links()` in the
fragment instead of `links()`:

```jinja
{% cobrastyle styles = "tip.css" %}
{{ cobrastyle.fragment_links() }}
<aside class="{{ styles.tip }}" data-cobrastyle-cloak>...</aside>
```

```django
{% load cobrastyle %}
{% cobrastyle "tip.css" as styles %}
{% cobrastyle_fragment_links %}
<aside class="{{ styles.tip }}" data-cobrastyle-cloak>...</aside>
```

This emits a small out-of-band script (`hx-swap-oob="beforeend:head"`) that appends the fragment's
stylesheet links to the page's `<head>`, skips any the page already has, and removes
itself. HTMX processes out-of-band elements before the main swap, so the CSS starts
loading before the fragment markup lands, and repeated swaps never duplicate links.
Dev and prod behave the same; URLs come from the compiler or the manifest as usual.

Inserted links still take a network round trip to load, so a fragment can paint
unstyled for a moment. The `data-cobrastyle-cloak` attribute on the fragment root (as
above) closes that gap: such elements stay hidden until every stylesheet the script
inserted has loaded, then reveal fully styled — immediately when the CSS was already
present, and after at most 3 seconds if a stylesheet never loads. Only use the
attribute in fragments that call `fragment_links()`; the reveal is part of it.

Two caveats. The markup relies on HTMX's `hx-swap-oob` handling, so a swap done with
plain `fetch()` + `innerHTML` will not execute it. And the script is inline: under a
strict CSP, pass a nonce — `{{ cobrastyle.fragment_links(nonce=nonce) }}` in Jinja,
`{% cobrastyle_fragment_links nonce=request.csp_nonce %}` in DTL. The nonce carries
over to the cloak `<style>` the script creates, so allow it in `style-src` too.

### CSS hot reload (dev)

In dev mode the framework adapters turn on hot reload whenever they are also serving
the CSS (which is the default). `links()` then emits a client script that subscribes to
a server-sent-events endpoint on the dev CSS server and swaps changed `<link>`s in
place: edit a stylesheet — or a file it `@import`s or composes from — and the browser
picks it up within about a second, without a reload, page state intact.

Opt out with `Cobrastyle(app, hot_reload=False)`, `install(..., hot_reload=False)`, or
`COBRASTYLE = {"HOT_RELOAD": False}`. On a bare environment it's off by default;
`configure(env, resolver=..., hot_reload=True)` enables it, provided the dev CSS server
(the WSGI middleware or ASGI app from `cobrastyle.serve`) is mounted at the resolver's
URL prefix, since that's where the events endpoint lives.

Change detection polls the compiled modules' source files a few times a second, per
connection; nothing of this exists in manifest mode. Under WSGI the event stream
occupies a worker thread per open tab. Werkzeug's and Django's dev servers are threaded,
so in practice this only matters on a deliberately single-threaded setup.

### Building (non-Django)

```sh
cobrastyle build myapp:create_environment
```

The target is adapted by type: a `jinja2.Environment`, a Flask app, a `Jinja2Templates`
instance, or a zero-arg factory returning any of these. The output directory — by
default `static/cobrastyle/`, serving under `/static/cobrastyle/`; override with
`--out` and `--url-prefix` — contains content-hashed CSS (plus any `url()` assets,
rewritten) and `manifest.json`, the sole input the prod runtime needs. Builds are
deterministic: unchanged input produces byte-identical output.

### Checking

Because class maps resolve at template compile time, cobrastyle can verify that your
templates and your CSS agree — the guarantee JS bundlers give, as a CI gate:

```sh
cobrastyle check myapp:create_environment          # same target adaptation as `build`
python manage.py cobrastyle_check                  # Django: covers Jinja2 and DTL templates
```

`check` walks every template, resolving class maps the way the target is configured:
a dev-configured environment compiles straight from source (no built manifest needed —
point CI at a dev-configured factory), while a manifest-configured one validates against
its built manifest. `manage.py cobrastyle_check` always compiles from source, like
`cobrastyle_build`. It reports two things:

- **Errors** — references to classes a module doesn't export, with template and line
  number: `index.html:12: styles.buttom — no class 'buttom' in 'page.css' (did you mean
  'button'?)`. These fail the command.
- **Warnings** — exported classes no template references (likely dead CSS). Warnings
  don't fail the command unless you pass `--strict-unused`.

Errors are only raised for accesses that are provably against a class map: a name bound
by `{% cobrastyle %}` in the same template and not shadowed. Anything unverifiable —
`{% set copy = styles %}`, a dynamic subscript `styles[name]`, passing the map into a
macro or include, a rebound or shadowed name — is never flagged, and marks that module's
exports as potentially used so the unused report stays free of false positives too.
Classes referenced only from a child template's block (via `extends`) count as used but
aren't validated. DTL analysis is best-effort in the same spirit: it understands
`{% for %}`/`{% with %}` rebinding and the common `... as var` tags, and errs toward
silence when resolution is dynamic.

`--strict` mirrors the build flag: fail on any template compile error, cobrastyle or not.

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

### Releasing

Both Python packages and the Rust crate share one version, kept in lockstep by
`just bump`. To release:

```sh
just bump X.Y.Z          # sets the version everywhere
git commit -am "release: X.Y.Z"
git tag vX.Y.Z && git push --follow-tags
```

Pushing the tag runs the release workflow, which verifies the tag matches the
package version, builds wheels and sdists, and publishes to PyPI.
