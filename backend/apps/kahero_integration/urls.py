from django.urls import path

from apps.kahero_integration import views

app_name = "kahero"

urlpatterns = [
    path("upload/", views.upload_batch, name="upload"),
    path("dashboard/", views.batch_dashboard, name="dashboard"),
    path("batch/<int:batch_id>/", views.batch_detail, name="batch_detail"),
]
