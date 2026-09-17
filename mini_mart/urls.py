from django.urls import path
from . import views

app_name = "mini_mart"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Products
    path("products/", views.product_list, name="product_list"),
    path("products/add/", views.product_add, name="product_add"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),

    # Customers
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/add/", views.customer_add, name="customer_add"),
    path("customer/<int:pk>/", views.customer_detail, name="customer_detail"),

    # Sales
    path("sell/", views.new_sale, name="new_sale"),
    path("sell/hold/", views.hold_order, name="hold_order"),
    path("sell/held/<int:pk>/", views.resume_order, name="resume_order"),
    path("sell/held/<int:pk>/discard/", views.discard_order, name="discard_order"),
    path("offline-sell/", views.offline_pos, name="offline_pos"),
    path("offline-sell/sync/", views.sync_offline_sale, name="sync_offline_sale"),
    path("sales/", views.sales_history, name="sales_history"),
    path("sales/list/", views.sales_history, name="sales_list"),
    path("sale/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("sale/<int:pk>/delete/", views.sale_delete, name="sale_delete"),
    path(
        "sale/<int:pk>/add-customer/",
        views.add_customer_to_sale,
        name="add_customer_to_sale",
    ),
    path(
        "sale/<int:pk>/pay/",
        views.record_payment,
        name="record_payment",
    ),
    path("cart/add/", views.add_to_cart_ajax, name="add_to_cart_ajax"),

    # Debtors
    path("debtors/", views.debtors_list, name="debtors_list"),
    path("debts/", views.debts_hub, name="debts_hub"),
    path("debts/add/", views.add_existing_debt, name="add_existing_debt"),
    path(
        "debts/customer/<int:pk>/pay/",
        views.pay_customer_debt,
        name="pay_customer_debt",
    ),

]