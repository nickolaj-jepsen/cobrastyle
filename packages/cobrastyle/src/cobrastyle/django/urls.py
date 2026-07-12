from django.urls import path

from cobrastyle.django.views import stylesheet

urlpatterns = [
    path("<path:path>", stylesheet, name="cobrastyle"),
]
