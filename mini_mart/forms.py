from decimal import Decimal

from django import forms

from .models import Customer, ExistingDebt, Product, ProductUnit, Sale


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "quantity",
            "base_unit",
            "cost_price",
            "selling_price",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter product name",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Optional: Size, color, pack size etc.",
                }
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "inputmode": "numeric",
                }
            ),
            "base_unit": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Cup, Kg, Piece",
                }
            ),
            "cost_price": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "inputmode": "decimal",
                }
            ),
            "selling_price": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "inputmode": "decimal",
                }
            ),
        }


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name",
            "phone_number",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter customer name",
                }
            ),
            "phone_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "0803...",
                    "inputmode": "tel",
                }
            ),
        }


class SaleForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        empty_label="Walk-in Customer",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(
            is_available=True,
            quantity__gt=0,
        ).order_by("name"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    quantity = forms.IntegerField(
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Qty",
                "inputmode": "numeric",
            }
        ),
    )

    amount_paid = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "Amount paid now. Leave 0 for credit.",
                "inputmode": "decimal",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()

        product = cleaned.get("product")
        quantity = cleaned.get("quantity")
        amount_paid = cleaned.get("amount_paid") or Decimal("0.00")

        if product and quantity:
            if quantity > product.quantity:
                self.add_error(
                    "quantity",
                    f"Only {product.quantity} item(s) available in stock.",
                )

            total = product.selling_price * quantity

            if amount_paid > total:
                self.add_error(
                    "amount_paid",
                    f"Cannot pay more than ₦{total:,.2f}.",
                )

        return cleaned


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount Received",
        widget=forms.NumberInput(
            attrs={
                "class": "form-control form-control-lg text-center fw-bold",
                "placeholder": "0.00",
                "inputmode": "decimal",
                "autofocus": True,
            }
        ),
    )

    def __init__(self, *args, sale=None, **kwargs):
        super()._init_(*args, **kwargs)
        self.sale = sale

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if self.sale and amount > self.sale.balance:
            raise forms.ValidationError(
                f"Cannot collect more than the balance (₦{self.sale.balance:,.2f})."
            )

        return amount


class SalePaymentForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ["discount_amount"]

        widgets = {
            "discount_amount": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "min": "0",
                    "value": "0",
                }
            )
        }


class ExistingDebtForm(forms.ModelForm):
    class Meta:
        model = ExistingDebt
        fields = ["customer", "amount", "description"]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0.01", "inputmode": "decimal"}
            ),
            "description": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "What is this debt for?"}
            ),
        }


class ProductUnitForm(forms.ModelForm):
    class Meta:
        model = ProductUnit
        fields = ["name", "conversion_quantity", "selling_price"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Mudu, Bag, Crate"}),
            "conversion_quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "selling_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        }