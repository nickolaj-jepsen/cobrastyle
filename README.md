# cobrastyle
_CSS modules for Python_

> [!WARNING]
> In active development! Still in exploratory phase, APIs **WILL** change.

## Description

Cobrastyle provides CSS modules support for Python and various Python based template engines and frameworks, with excellent performance provided by [LightningCSS](https://lightningcss.dev/).

## Current packages:

- [cobrastyle-jinja2](./packages/cobrastyle-jinja2): Jinja2 extension for Cobrastyle
- [cobrastyle-lightningcss](./packages/cobrastyle-lightningcss): Python bindings for LightningCSS
- [cobrastyle-core](./packages/cobrastyle-core): Core functionality for Cobrastyle

## Missing features:
- [ ] Production builds, where CSS is minified and bundled in a build step before deployment
- [ ] Support for other template engines and frameworks
  - [ ] Django
  - [ ] Flask (via Jinja2)
  - [ ] FastAPI (via Jinja2)
- [ ] Support for inline CSS modules in templates