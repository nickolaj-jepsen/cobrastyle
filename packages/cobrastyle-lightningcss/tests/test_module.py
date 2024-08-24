from cobrastyle_lightningcss import transform


def _remove_whitespace(text: str) -> str:
    return "".join(text.split())


def test_compile():
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(filename="test.css", code=css, minify=False)
    assert _remove_whitespace(result.code) == _remove_whitespace(css)


def test_compile_minify():
    css = """
    .hello-world {
      color: red
    }
    """
    result = transform(filename="test.css", code=css, minify=True)
    assert result.code == ".hello-world{color:red}"


def test_module(snapshot):
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(filename="filename.css", code=css, module=True, module_pattern="test-[name]-[local]")
    assert result.code == ".test-filename-hello-world{color:red}"
    assert result.exports["hello-world"].name == "test-filename-hello-world"
