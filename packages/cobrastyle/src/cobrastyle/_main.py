"""Console-script entry point: a friendly exit when the CLI extras are missing.

Kept separate from cobrastyle.cli so programmatic importers of that module get
a normal, catchable ImportError instead of SystemExit.
"""


def main() -> None:
    try:
        from cobrastyle.cli import cli
    except ImportError as exc:
        raise SystemExit(
            "The cobrastyle CLI requires the 'cli' extra — install with: pip install 'cobrastyle[cli]'"
        ) from exc
    cli()
