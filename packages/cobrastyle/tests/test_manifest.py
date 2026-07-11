import pytest

from cobrastyle.manifest import Manifest, ManifestError, ModuleEntry


def make_manifest() -> Manifest:
    return Manifest(
        generator={"cobrastyle": "0.1.0", "lightningcss": "1.30.1"},
        modules={
            "styles/button.css": ModuleEntry(
                file="styles/button.4f2a1c9b.css",
                url="/static/styles/button.4f2a1c9b.css",
                classes={"button": "EgL3uq_button"},
            )
        },
        pages={"shop/product.html": ["styles/button.css"]},
    )


def test_round_trip(tmp_path):
    manifest = make_manifest()
    manifest.dump(tmp_path / "manifest.json")
    loaded = Manifest.load(tmp_path / "manifest.json")

    assert loaded == manifest


def test_dump_is_deterministic(tmp_path):
    manifest = make_manifest()
    manifest.dump(tmp_path / "one.json")
    manifest.dump(tmp_path / "two.json")

    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()


def test_missing_file():
    with pytest.raises(ManifestError, match="cobrastyle build"):
        Manifest.load("/nonexistent/manifest.json")


def test_unsupported_version():
    with pytest.raises(ManifestError, match="version"):
        Manifest.from_dict({"version": 99, "modules": {}})


def test_malformed():
    with pytest.raises(ManifestError, match="Malformed"):
        Manifest.from_dict({"version": 1, "modules": {"a.css": {"nope": True}}})
