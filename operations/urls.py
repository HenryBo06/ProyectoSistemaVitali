"""Human-facing local routes."""

from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("configuracion-inicial/", views.setup, name="setup"),
    path("entrar/", views.SmartLoginView.as_view(), name="login"),
    path("salir/", LogoutView.as_view(next_page="login"), name="logout"),
    path("ventas/", views.sales_upload, name="sales_upload"),
    path("inventario/", views.inventory_upload, name="inventory_upload"),
    path("inventario/plantilla/", views.inventory_template_download, name="inventory_template"),
    path("usuarios/", views.users, name="users"),
    path("asignaciones/", views.users, name="assignments"),
    path("recomendaciones/", views.recommendations, name="recommendations"),
    path("pedidos/nuevo/", views.order_create, name="order_create"),
    path("pedidos/", views.orders, name="orders"),
    path("pedidos/<int:order_id>/", views.order_detail, name="order_detail"),
    path("pedidos/<int:order_id>/cambiar/", views.sale_change, name="sale_change"),
    path("pedidos/<int:order_id>/revisar/", views.order_review, name="order_review"),
    path('pedidos/<int:order_id>/odoo/', views.order_odoo, name='order_odoo'),
    path('odoo/', views.odoo_settings, name='odoo_settings'),
    path('resultados/', views.operational_reports, name='operational_reports'),
    path("pedidos/<int:order_id>/revalidar/", views.order_revalidate, name="order_revalidate"),
    path("produccion/exportar/", views.export_batch, name="export_batch"),
    path("correcciones/", views.correction_requests, name="correction_requests"),
]
