import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "demo-only-not-a-secret"
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "cobrastyle.django",
]

ROOT_URLCONF = "demo.urls"

# Both backends share the same CSS modules; render() picks whichever engine has the template
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "templates" / "jinja2"],
        "APP_DIRS": False,
        "OPTIONS": {"environment": "cobrastyle.django.environment"},
    },
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates" / "dtl"],
        "APP_DIRS": False,
        "OPTIONS": {},
    },
]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static_root"
# The cobrastyle finder hands the build output to collectstatic (and runserver --insecure)
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "cobrastyle.django.finders.CobrastyleFinder",
]

# COBRASTYLE defaults do the rest: dev compiles BASE_DIR/styles when DEBUG,
# prod reads BASE_DIR/cobrastyle_static/cobrastyle/manifest.json
