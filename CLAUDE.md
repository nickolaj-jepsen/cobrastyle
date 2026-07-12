# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cobrastyle brings CSS modules to Python template engines, compiling CSS through
[LightningCSS](https://lightningcss.dev/) via a Rust extension. A uv workspace with two packages:

- `packages/cobrastyle` — the Python library (Jinja2 extension, Flask/FastAPI/Django adapters, build pipeline, CLI)
- `packages/cobrastyle-lightningcss` — PyO3/maturin bindings for LightningCSS (`src/lib.rs`)

Runnable example apps (Flask, FastAPI, Django) live in `examples/` and are exercised by the test suite.

## Commands

The dev environment is a Nix flake (`nix develop`: Rust toolchain, Python 3.12, uv, just, prek).
Everything routes through the justfile:

```sh
just sync       # uv sync --all-packages (rebuilds the Rust extension when Rust sources change)
just develop    # force-rebuild the Rust extension (escape hatch; sync normally suffices)
just test       # pytest; pass args through: just test packages/cobrastyle/tests/test_manager.py -k composes
just watch      # re-run tests on file changes (watchexec)
just lint       # ruff check + format --check, versions lockstep, cargo fmt --check, clippy -D warnings
just typecheck  # pyrefly
just fmt        # auto-fix/format Python and Rust
just check      # everything CI runs (lint + typecheck + test)
just example flask|fastapi|django [dev|prod]   # run an example app
```

Ruff: line length 120, target py312. Rust: edition 2024, clippy pedantic. Editing `lib.rs` needs no
special step — cache-keys in the extension's pyproject make `sync` rebuild it (dev profile inside the
Nix shell via `MATURIN_PEP517_ARGS`; CI and released wheels are release builds).

## Architecture

The load-bearing design decision is the **dev/prod split**; nearly every module sits on one side of it:

- **Dev mode**: `CobrastyleManager` (`manager.py`) compiles modules on demand through a `FileResolver`
  (`resolvers.py`), caching by path with mtime revalidation so edits show on refresh. CSS is served
  from memory by `serve.py`, which owns the conditional-GET/ETag contract and provides WSGI middleware
  + an ASGI app that the framework adapters wrap.
- **Prod mode**: `build.py` walks every template in an environment, compiles all stylesheets, and emits
  content-hashed CSS + `manifest.json`. `manifest.py` defines that build→runtime contract (versioned,
  deterministically serialized). The prod runtime reads only the manifest and must never import the
  compiler — hence the lazy `__getattr__` in `cobrastyle/__init__.py` and deferred
  `from cobrastyle.manager import ...` throughout. Don't add eager imports of `manager` or
  `cobrastyle_lightningcss` to modules on the prod path.

`source.py` (`StyleSource`) is the engine-agnostic seam between the two modes: both the Jinja2
extension and the Django DTL runtime resolve class maps and URLs through it, translating
`StylesheetNotFoundError` into their engine's error type.

Other invariants worth knowing before touching related code:

- **Class maps bake in at template compile time.** `CobrastyleExtension.parse()` replaces
  `{% cobrastyle styles = "x.css" %}` with a constant dict, and `preprocess()` injects a page-tracking
  call so `cobrastyle.links()` in an inherited `<head>` sees the child template's stylesheets. This is
  why dev mode rejects a `bytecode_cache` (cache hits skip compilation and break tracking) and why the
  manifest carries a `pages` map (workers that load templates from a cache never compiled them).
- **Builds are deterministic**: identical input must produce byte-identical output (manifest keys
  sorted, exports sorted before hashing). Tests assert this.
- **Class names hash the file path, not its content** — a recompile keeps existing baked class maps valid.
- **lightningcss is exact-pinned** in Cargo.toml because class hashes are not stable across its versions.

The Django integration (`cobrastyle/django/`) supports both the Jinja2 backend (an `environment()`
factory configured from the `COBRASTYLE` settings dict, dev/prod following `DEBUG`) and native DTL
(`templatetags/cobrastyle.py` plus a settings-driven `DTLRuntime` singleton in `runtime.py`).
`manage.py cobrastyle_build` builds both template kinds; non-Django apps use `cobrastyle build
package.module:attribute` (`cli.py`), whose target adapter accepts an Environment, Flask app,
`Jinja2Templates`, or a zero-arg factory.
