import pytest

from cobrastyle.paths import normalize_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a.css", "a.css"),
        ("./a.css", "a.css"),
        ("dir/../a.css", "a.css"),
        ("dir/./sub/x.css", "dir/sub/x.css"),
        ("dir\\a.css", "dir/a.css"),
    ],
)
def test_equivalent_spellings_collapse(path, expected):
    assert normalize_path(path) == expected


@pytest.mark.parametrize("path", ["/abs.css", "..", "../up.css", "a/../../up.css"])
def test_absolute_and_escaping_paths_are_rejected(path):
    with pytest.raises(ValueError, match="relative"):
        normalize_path(path)


@pytest.mark.parametrize("path", ["", ".", "./"])
def test_paths_that_name_no_file_are_rejected(path):
    with pytest.raises(ValueError, match="name a file"):
        normalize_path(path)
