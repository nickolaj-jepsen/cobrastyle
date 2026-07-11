rust_manifest := "packages/cobrastyle-lightningcss/Cargo.toml"

# List available recipes
default:
    @just --list

# Install/refresh the virtualenv, building the Rust extension if needed
sync:
    uv sync --all-packages

# Force-rebuild the Rust extension into the virtualenv
develop:
    uv sync --all-packages --reinstall-package cobrastyle-lightningcss

# Run the test suite
test *args: sync
    uv run pytest {{ args }}

# Lint Python and Rust without modifying anything
lint:
    uv run ruff check .
    uv run ruff format --check .
    cargo fmt --manifest-path {{ rust_manifest }} -- --check
    cargo clippy --manifest-path {{ rust_manifest }} --all-targets -- -D warnings

# Type-check the Python packages
typecheck: sync
    uv run pyrefly check

# Auto-format and auto-fix Python and Rust
fmt:
    uv run ruff check --fix .
    uv run ruff format .
    cargo fmt --manifest-path {{ rust_manifest }}

# Everything CI runs
check: lint typecheck test
