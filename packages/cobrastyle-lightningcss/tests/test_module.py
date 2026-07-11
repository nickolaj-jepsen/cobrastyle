import pytest

from cobrastyle_lightningcss import CssModuleReference, TransformError, transform


def _remove_whitespace(text: str) -> str:
    return "".join(text.split())


def test_transform():
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(filename="test.css", code=css)
    assert _remove_whitespace(result.code) == _remove_whitespace(css)
    assert result.exports is None


def test_transform_minify():
    css = """
    .hello-world {
      color: red
    }
    """
    result = transform(filename="test.css", code=css, minify=True)
    assert result.code == ".hello-world{color:red}"


def test_module():
    css = """
    .hello-world {
      color: red;
    }
    """
    result = transform(
        filename="filename.css", code=css, module=True, module_pattern="test-[name]-[local]", minify=True
    )
    assert result.code == ".test-filename-hello-world{color:red}"
    assert result.exports is not None
    assert result.exports["hello-world"].name == "test-filename-hello-world"


def test_module_composes():
    css = """
    .base { color: black; }
    .button { composes: base; background: red; }
    """
    result = transform(filename="test.css", code=css, module=True, module_pattern="[local]")
    assert result.exports is not None
    (reference,) = result.exports["button"].composes
    assert isinstance(reference, CssModuleReference.Local)
    assert reference.name == "base"


def test_targets_adds_vendor_prefixes():
    css = ".a { user-select: none; }"
    result = transform(filename="test.css", code=css, minify=True, targets=["safari >= 13"])
    assert "-webkit-user-select" in result.code


def test_parse_error():
    # An invalid selector is a hard parse error (invalid declarations are just skipped)
    with pytest.raises(TransformError, match="parse"):
        transform(filename="test.css", code="..a { color: red; }")
    assert issubclass(TransformError, ValueError)


def test_invalid_module_pattern():
    with pytest.raises(TransformError, match="pattern"):
        transform(filename="test.css", code=".a {}", module=True, module_pattern="[unknown]")


def test_invalid_targets():
    with pytest.raises(TransformError, match="targets"):
        transform(filename="test.css", code=".a {}", targets=["not a real browser query %%"])
