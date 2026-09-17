from django.contrib import admin

from .models import (
    ExistingDebt,
    Product,
    Customer,
    Sale,
    SaleItem,
    ProductPriceHistory,
    ProductUnit,
    Tenant,
    TenantMembership,
)


@admin.register(ProductPriceHistory)
class ProductPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("product", "cost_price", "selling_price", "alternative_selling_price", "changed_at")
    list_filter = ("changed_at",)
    search_fields = ("product__name",)
    readonly_fields = ("product", "cost_price", "selling_price", "alternative_selling_price", "changed_at")


@admin.register(ProductUnit)
class ProductUnitAdmin(admin.ModelAdmin):
    list_display = ("product", "name", "conversion_quantity", "selling_price")
    search_fields = ("product__name", "name")


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("tenant", "user", "role", "can_view_financials", "created_at")
    list_filter = ("tenant", "role")
    search_fields = ("tenant__name", "user__username", "user__email")


@admin.register(ExistingDebt)
class ExistingDebtAdmin(admin.ModelAdmin):
    list_display = ("customer", "description", "amount", "amount_paid", "balance", "created_at")
    search_fields = ("customer__name", "customer__phone_number", "description")
    readonly_fields = ("balance", "created_at")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "selling_price",
        "quantity",
        "is_available",
    )
    list_filter = ("is_available",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone_number",
        "address",
        "outstanding_debt",
    )
    search_fields = (
        "name",
        "phone_number",
        "address",
    )
    ordering = ("name",)


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0

    readonly_fields = (
        "product",
        "quantity",
        "unit_price",
        "line_total",
    )

    def line_total(self, obj):
        return obj.subtotal

    line_total.short_description = "Total"


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "customer_name",
        "total_amount",
        "discount_amount",
        "final_amount",
        "amount_paid",
        "balance",
        "profit",
        "is_paid",
        "created_at",
    )

    list_filter = (
        "is_paid",
        "created_at",
    )

    search_fields = (
        "id",
        "customer__name",
        "customer__phone_number",
    )

    readonly_fields = (
        "final_amount",
        "profit",
        "balance",
        "is_paid",
        "created_at",
    )

    date_hierarchy = "created_at"

    ordering = ("-created_at",)

    inlines = [SaleItemInline]

    @admin.display(description="Customer", ordering="customer__name")
    def customer_name(self, obj):
        return obj.customer.name if obj.customer else "Walk-in"

    def has_change_permission(self, request, obj=None):
        return obj is None or not obj.is_paid


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = (
        "sale",
        "product",
        "quantity",
        "unit_price",
    )

    list_filter = (
        "product",
        "sale__created_at",
    )

    search_fields = (
        "sale__id",
        "product__name",
    )

    ordering = ("sale",)