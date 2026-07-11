import pytest

from cobrastyle import cx


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), ""),
        (("btn",), "btn"),
        (("btn", "primary"), "btn primary"),
        (("btn", "", None, False), "btn"),
        (("btn", {"active": True, "disabled": False}), "btn active"),
        (("a", ["b", ["c", None], "d"]), "a b c d"),
        (("a", "a", "b"), "a a b"),  # no dedup — clsx parity
        (({"a b": True, "c": 0},), "a b"),  # multi-class keys kept verbatim
        ((1, "btn", 0), "1 btn"),  # truthy non-strings str()-ified, falsy dropped
        ((("x", {"y": 1}),), "x y"),
    ],
)
def test_cx(args, expected):
    assert cx(*args) == expected
