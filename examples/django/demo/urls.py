from django.urls import include, path

from demo import views

urlpatterns = [
    path("", views.index),
    path("about/", views.about),
    path("cobrastyle/", include("cobrastyle.django.urls")),  # dev CSS serving, DEBUG only
]
