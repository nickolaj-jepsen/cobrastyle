# cobrastyle-jinja2
_CSS modules for jinja2_

## Example

```css
/* ./test.css */
.hello_world {
    color: red;
}
```

```jinja2
{# ./template.jinja2 #}
<html>
    <head>
        {% for stylesheet in cobrastyle.used_stylesheets() %}
            <link rel="stylesheet" href="{{ stylesheet }}">
        {% endfor %}
    </head>
    <body>
        {% cobrastyle styles = "test.css" %}
        <h1 class="{{ styles.hello_world }}">Hello World</h1>
    </body>
</html>
```

```python
from pathlib import Path
from jinja2 import Environment
from cobrastyle_jinja2 import CobrastyleExtension
from cobrastyle_core.file_resolver import InMemoryResolver

css_file = Path("./test.css").read_text()
template_file = Path("./template.jinja2").read_text()

jinja = Environment(extensions=[CobrastyleExtension])
jinja.cobrastyle_resolver = InMemoryResolver({"test.css": css_file})
template = jinja.from_string(template_file)
print(template.render())
```

```html
<html>
  <head>
    <link rel="stylesheet" href="test.css">
  </head>
  <body>
    <h1 class="EgL3uq_hello_world">Hello World</h1>
  </body>
</html>
```