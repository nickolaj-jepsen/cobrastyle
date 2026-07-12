# cobrastyle

CSS modules for Python template engines, powered by [LightningCSS](https://lightningcss.dev/).

## Installation

```sh
pip install cobrastyle          # core only
pip install cobrastyle[jinja2]  # with the Jinja2 extension
```

## Usage with Jinja2

```python
from jinja2 import Environment, FileSystemLoader
from cobrastyle import FileSystemResolver
from cobrastyle.jinja2 import CobrastyleExtension, configure

env = Environment(loader=FileSystemLoader("templates"), extensions=[CobrastyleExtension])
configure(env, resolver=FileSystemResolver("static/css", url_prefix="/static/css/"))
```

In a template:

```jinja
{% cobrastyle styles = "button.css" %}
<button class="{{ styles.button }}">Click me</button>
```

And in your layout's `<head>`, link every stylesheet the current page uses:

```jinja
{{ cobrastyle.links() }}
```

CSS modules are compiled once per environment (at template compile time) and
`links()` reflects exactly the stylesheets imported by the page being rendered,
including through `{% extends %}`.

### Configuration

Options for `configure()` (equivalently: set `cobrastyle_*`-prefixed attributes on the environment):

| Option                | Default | Description                                              |
| --------------------- | ------- | -------------------------------------------------------- |
| `resolver`            | —       | Required. Resolves stylesheet paths to content/URLs.     |
| `minify`              | `False` | Minify dev-served CSS (the build minifies by default; `--no-minify` opts out). |
| `underscore_aliases`  | `True`  | Also expose `hello-world` as `hello_world`.              |
| `module_pattern`      | `None`  | Class name pattern; `None` means readable `[name]_[local]_[hash]` in dev and compact `[hash]_[local]` in the build. |
| `targets`             | `None`  | Browserslist queries for vendor prefixing.               |
| `source_map`          | `True`  | Serve dev CSS with an inline source map.                 |

### Caveats

- The stylesheet path must be a constant string — imports are resolved at
  template compile time.
- Stylesheets imported by `{% include %}`d templates are never seen by
  `links()` in the including page (includes render in their own context).
  Import page-level stylesheets in the page template itself, or link the
  partial's stylesheet explicitly.
