# cobrastyle-lightningcss

Python bindings for [LightningCSS](https://lightningcss.dev/), a CSS parser, transformer and minifier written in Rust.

This is primarily for internal use by the [cobrastyle](https://github.com/nickolaj-jepsen/cobrastyle) project, and might therefore be missing features that are present in LightningCSS itself.

## Usage

```python
from cobrastyle_lightningcss import transform

result = transform(
    filename="button.css",
    code=".button { user-select: none; }",
    module=True,                  # parse as a CSS module
    module_pattern="[hash]-[local]",
    minify=True,
    targets=["defaults"],         # browserslist queries
)

result.code     # the compiled CSS
result.exports  # CSS module exports (class name mapping)
```

Errors raise `cobrastyle_lightningcss.TransformError` (a subclass of `ValueError`).
