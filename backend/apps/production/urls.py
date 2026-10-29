from django.urls import path

from apps.production import views

app_name = "production"

urlpatterns = [
    path("dashboard/", views.commissary_dashboard, name="dashboard"),
    path("record/", views.record_production, name="record"),
    path("batches/", views.batch_management, name="batch_management"),
    path("batches/<int:record_id>/delete/", views.delete_production_record, name="delete_record"),
    path("transfers/", views.stock_transfers, name="stock_transfers"),
    path(
        "transfers/<int:order_id>/advance/",
        views.advance_stock_transfer,
        name="advance_stock_transfer",
    ),
]
