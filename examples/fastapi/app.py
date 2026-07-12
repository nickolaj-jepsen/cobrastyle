import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cobrastyle.fastapi import install

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

if os.environ.get("COBRASTYLE_ENV") == "prod":
    install(templates, manifest=BASE_DIR / "static" / "cobrastyle" / "manifest.json")
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
else:
    install(templates, app, root=BASE_DIR / "styles")  # mounts a dev CSS server at /cobrastyle/


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"featured": True})


@app.get("/about")
def about(request: Request):
    return templates.TemplateResponse(request, "about.html")


@app.get("/fragments/tip")
def tip_fragment(request: Request):
    return templates.TemplateResponse(request, "_tip.html")
