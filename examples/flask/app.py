import os
from pathlib import Path

from flask import Flask, render_template

from cobrastyle.flask import Cobrastyle

BASE_DIR = Path(__file__).resolve().parent


def create_app() -> Flask:
    app = Flask(__name__)
    if os.environ.get("COBRASTYLE_ENV") == "prod":
        # Built by `cobrastyle build` into static/, so Flask's own /static/ route serves it
        Cobrastyle(app, manifest=BASE_DIR / "static" / "cobrastyle" / "manifest.json")
    else:
        Cobrastyle(app)  # compiles styles/ on demand, served at /cobrastyle/

    @app.get("/")
    def index():
        return render_template("index.html", featured=True)

    @app.get("/about")
    def about():
        return render_template("about.html")

    return app
