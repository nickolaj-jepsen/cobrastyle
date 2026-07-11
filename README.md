# cobrastyle
_CSS modules for Python_

> [!WARNING]
> In active development! Still in exploratory phase, APIs **WILL** change.

## Description

Cobrastyle provides CSS modules support for Python and various Python based template engines and frameworks, with excellent performance provided by [LightningCSS](https://lightningcss.dev/).

## Current packages:

- [cobrastyle](./packages/cobrastyle): CSS modules for Python, with a Jinja2 extension (`cobrastyle[jinja2]`)
- [cobrastyle-lightningcss](./packages/cobrastyle-lightningcss): Python bindings for LightningCSS

## Development

The dev environment is managed with [Nix](https://nixos.org/) (`nix develop`) and [uv](https://docs.astral.sh/uv/). Common tasks are wrapped in a [justfile](./justfile):

```sh
just sync       # set up the virtualenv (builds the Rust extension)
just test       # run the test suite
just lint       # ruff + rustfmt + clippy
just typecheck  # pyrefly
just fmt        # auto-format everything
just check      # everything CI runs
```

## Missing features:
- [ ] Serving compiled CSS (dev middleware / production build step)
- [ ] Support for other template engines and frameworks
  - [ ] Django
  - [ ] Flask (via Jinja2)
  - [ ] FastAPI (via Jinja2)
- [ ] Support for inline CSS modules in templates
- [ ] `composes` from other files
