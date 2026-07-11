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
    uv run python scripts/versions.py check
    cargo fmt --manifest-path {{ rust_manifest }} -- --check
    cargo clippy --manifest-path {{ rust_manifest }} --all-targets -- -D warnings

# Type-check the Python packages
typecheck: sync
    uv run pyrefly check

# Set the lockstep version across both Python packages and the Rust crate
bump version:
    uv run python scripts/versions.py set {{ version }}

# Auto-format and auto-fix Python and Rust
fmt:
    uv run ruff check --fix .
    uv run ruff format .
    cargo fmt --manifest-path {{ rust_manifest }}

# Everything CI runs
check: lint typecheck test

# Run an example app (flask | fastapi | django) in dev or prod mode
example name mode="dev": sync
    #!/usr/bin/env bash
    set -euo pipefail
    cd examples/{{ name }} 2>/dev/null || {
        echo "usage: just example (flask|fastapi|django) [dev|prod]" >&2
        exit 1
    }
    case "{{ name }}:{{ mode }}" in
        flask:dev) uv run flask --app app run ;;
        flask:prod)
            uv run cobrastyle build app:create_app -o static/cobrastyle --url-prefix /static/cobrastyle/ --clean
            COBRASTYLE_ENV=prod uv run flask --app app run ;;
        fastapi:dev) uv run uvicorn app:app --reload ;;
        fastapi:prod)
            uv run cobrastyle build app:templates -o static/cobrastyle --url-prefix /static/cobrastyle/ --clean
            COBRASTYLE_ENV=prod uv run uvicorn app:app ;;
        django:dev) uv run python manage.py runserver ;;
        django:prod)
            uv run python manage.py cobrastyle_build --clean
            DJANGO_DEBUG=0 uv run python manage.py runserver --insecure ;;
        *)
            echo "usage: just example (flask|fastapi|django) [dev|prod]" >&2
            exit 1 ;;
    esac
