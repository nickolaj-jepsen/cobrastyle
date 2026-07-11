from django.shortcuts import render


def index(request):
    return render(request, "index.html")  # found in the Jinja2 backend's dirs


def about(request):
    return render(request, "about.html", {"emphasis": True})  # found in the DTL backend's dirs
