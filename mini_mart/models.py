from decimal import Decimal
from contextvars import ContextVar

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models
from django.db.models.deletion import PROTECT


current_tenant = ContextVar("current_tenant", default=None)


class Tenant(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class TenantMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        STAFF = "staff", "Staff"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STAFF)
    can_view_financials = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def can_view_financial_kpis(self):
        return self.role == self.Role.OWNER or self.can_view_financials

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenant", "user"), name="unique_tenant_membership"),
        ]


class TenantScopedManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        tenant = current_tenant.get()
        if tenant is not None:
            return queryset.filter(tenant=tenant)
        return queryset


def legacy_tenant():
    tenant, _ = Tenant.objects.get_or_create(
        slug="legacy",
        defaults={"name": "Legacy workspace"},
    )
    return tenant


class Product(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="products", null=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    # Stock is stored in the smallest sellable base unit. For example,
    # store 540 cups rather than 30 mudus.
    quantity = models.PositiveIntegerField(default=0)
    initial_quantity = models.PositiveIntegerField(default=0)

    base_unit = models.CharField(max_length=50, default="Unit")

    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)

    has_alternative_unit = models.BooleanField(default=False)
    alternative_unit = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )
    alternative_unit_quantity = models.PositiveIntegerField(default=1)
    alternative_selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
    )

    date_added = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_available = models.BooleanField(default=True)

    objects = TenantScopedManager()

    @property
    def profit_per_unit(self):
        return self.selling_price - self.cost_price

    @property
    def stock_value(self):
        return self.quantity * self.selling_price

    @property
    def potential_revenue(self):
        return self.initial_quantity * self.selling_price

    @property
    def potential_profit(self):
        return self.initial_quantity * (self.selling_price - self.cost_price)

    @property
    def alternative_unit_cost_price(self):
        if not self.has_alternative_unit:
            return None
        return self.cost_price * self.alternative_unit_quantity

    @property
    def alternative_unit_stock(self):
        if not self.has_alternative_unit or self.alternative_unit_quantity <= 0:
            return 0
        return self.quantity // self.alternative_unit_quantity

    def clean(self):
        super().clean()

        if not self.has_alternative_unit:
            return

        if not self.alternative_unit:
            raise ValidationError("Alternative unit name is required.")

        if self.alternative_unit_quantity <= 0:
            raise ValidationError(
                "Alternative unit quantity must be greater than zero."
            )

        if self.alternative_selling_price is None:
            raise ValidationError("Alternative selling price is required.")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        previous = None
        if not is_new and self.pk:
            previous = type(self)._base_manager.get(pk=self.pk)
        if self.tenant_id is None:
            self.tenant = current_tenant.get() or legacy_tenant()
        if self._state.adding and not self.initial_quantity:
            self.initial_quantity = self.quantity
        self.full_clean()
        super().save(*args, **kwargs)
        price_changed = previous and any(
            getattr(previous, field) != getattr(self, field)
            for field in (
                "cost_price",
                "selling_price",
                "alternative_selling_price",
            )
        )
        if is_new or price_changed:
            ProductPriceHistory.objects.create(
                product=self,
                cost_price=self.cost_price,
                selling_price=self.selling_price,
                alternative_selling_price=self.alternative_selling_price,
            )

    @property
    def sale_units(self):
        units = [
            ProductUnitData(
                name=self.base_unit,
                conversion_quantity=1,
                selling_price=self.selling_price,
                code="base",
            )
        ]
        configured_units = list(self.sale_units_config.all())
        if configured_units:
            return units + configured_units
        return units + ([
            ProductUnitData(
                name=self.alternative_unit,
                conversion_quantity=self.alternative_unit_quantity,
                selling_price=self.alternative_selling_price,
                code="alternative",
            )
        ] if self.has_alternative_unit else [])


class ProductUnitData:
    def __init__(self, name, conversion_quantity, selling_price, code):
        self.name = name
        self.conversion_quantity = conversion_quantity
        self.selling_price = selling_price
        self.code = code


class ProductUnit(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="sale_units_config",
    )
    name = models.CharField(max_length=50)
    conversion_quantity = models.PositiveIntegerField(
        help_text="Number of base units consumed when one of these is sold."
    )
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("product", "name"), name="unique_product_sale_unit"),
        ]

    @property
    def code(self):
        return str(self.pk)

    def __str__(self):
        return f"{self.product.name} - {self.name}"


class ProductPriceHistory(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="price_history",
    )
    cost_price = models.DecimalField(max_digits=10, decimal_places=2)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    alternative_selling_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    @property
    def tenant_id(self):
        return self.product.tenant_id


class Customer(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="customers", null=True)
    name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=15, blank=True)
    address = models.TextField(blank=True)

    outstanding_debt = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant = current_tenant.get() or legacy_tenant()
        super().save(*args, **kwargs)

    @property
    def total_debt(self):
        sale_debt = sum(s.balance for s in self.sale_set.filter(balance__gt=0))
        existing_debt = sum(
            debt.balance for debt in self.existingdebt_set.filter(balance__gt=0)
        )
        return sale_debt + existing_debt

    @property
    def is_debtor(self):
        return self.total_debt > 0

    def __str__(self):
        return self.name


class ExistingDebt(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="existing_debts", null=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    def clean(self):
        if self.amount <= 0:
            raise ValidationError("Debt amount must be greater than zero.")
        if self.amount_paid < 0 or self.amount_paid > self.amount:
            raise ValidationError("Amount paid must be between zero and the debt amount.")

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant = self.customer.tenant or current_tenant.get() or legacy_tenant()
        self.balance = self.amount - self.amount_paid
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Debt for {self.customer}"


class Sale(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="sales", null=True)
    customer = models.ForeignKey(
        Customer,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    )

    total_amount = models.DecimalField(max_digits=12, decimal_places=2)  
    cost_amount = models.DecimalField(max_digits=12, decimal_places=2)  

    discount_amount = models.DecimalField(  
        max_digits=12,  
        decimal_places=2,  
        default=Decimal("0.00"),  
    )  

    final_amount = models.DecimalField(  
        max_digits=12,  
        decimal_places=2,  
        editable=False,  
    )  

    profit = models.DecimalField(  
        max_digits=12,  
        decimal_places=2,  
        editable=False,  
    )  

    amount_paid = models.DecimalField(  
        max_digits=12,  
        decimal_places=2,  
        default=Decimal("0.00"),  
    )  

    balance = models.DecimalField(  
        max_digits=12,  
        decimal_places=2,  
        default=Decimal("0.00"),  
        editable=False,  
    )  

    is_paid = models.BooleanField(  
        default=False,  
        editable=False,  
    )  

    created_at = models.DateTimeField(auto_now_add=True)  

    objects = TenantScopedManager()

    def clean(self):  
        if self.discount_amount > self.total_amount:  
            raise ValidationError(  
                "Discount cannot be greater than total amount."  
            )  

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant = (self.customer.tenant if self.customer_id else None) or current_tenant.get() or legacy_tenant()
        self.final_amount = self.total_amount - self.discount_amount  
        self.profit = self.final_amount - self.cost_amount  
        self.balance = self.final_amount - self.amount_paid  
        self.is_paid = self.balance <= 0  

        super().save(*args, **kwargs)

    @property
    def is_credit(self):
        return self.balance > 0

    def __str__(self):
        return f"Sale #{self.pk}"

class SaleItem(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="sale_items", null=True)
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items",
    )

    product = models.ForeignKey(  
        Product,  
        on_delete=PROTECT,
    )  

    quantity = models.PositiveIntegerField()  

    units_sold = models.PositiveIntegerField(default=1)
    sale_unit = models.CharField(max_length=50, default="Unit")

    unit_price = models.DecimalField(  
        max_digits=10,  
        decimal_places=2,  
    )  

    objects = TenantScopedManager()

    def save(self, *args, **kwargs):
        if self.tenant_id is None:
            self.tenant = self.sale.tenant or current_tenant.get() or legacy_tenant()
        super().save(*args, **kwargs)

    @property  
    def subtotal(self):  
        return self.quantity * self.unit_price  

    @property  
    def profit(self):  
        return (  
            (self.unit_price - self.product.cost_price)  
            * self.quantity  
        )  

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"