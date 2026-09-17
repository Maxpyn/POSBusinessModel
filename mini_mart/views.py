from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db import transaction
from django.forms import inlineformset_factory
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.db.models import Sum, F, DecimalField, Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from decimal import Decimal
from django.conf import settings
from .forms import ExistingDebtForm, ProductForm, ProductUnitForm, CustomerForm # remove SaleForm, PaymentForm if you don't use them
from .models import ExistingDebt, Product, ProductUnit, Sale, SaleItem, Customer

ProductUnitFormSet = inlineformset_factory(
    Product,
    ProductUnit,
    form=ProductUnitForm,
    extra=3,
    can_delete=True,
)


def product_unit_data(product):
    return [
        {
            "code": unit.code if hasattr(unit, "code") else str(unit.pk),
            "name": unit.name,
            "conversion_quantity": unit.conversion_quantity,
            "price": str(unit.selling_price),
        }
        for unit in product.sale_units
    ]

def dashboard(request):
    today = timezone.now().date()
    if request.method == 'POST' and request.POST.get('financial_kpi_pin'):
        if settings.FINANCIAL_KPI_PIN and request.POST['financial_kpi_pin'] == settings.FINANCIAL_KPI_PIN:
            request.session['financial_kpis_unlocked'] = True
        else:
            messages.error(request, 'The financial KPI PIN is invalid.')
        return redirect('mini_mart:dashboard')

    can_view_financials = bool(
        request.session.get('financial_kpis_unlocked')
        or (
            request.tenant_membership
            and request.tenant_membership.can_view_financial_kpis
        )
    )

    products_count = Product.objects.count()
    customers_count = Customer.objects.count()

    # Today's Sales
    today_sales = Sale.objects.filter(created_at__date=today)
    today_sales_count = today_sales.count()

    # =====================================================
    # DEBT KPIs
    # =====================================================

    unpaid_sales = Sale.objects.filter(balance__gt=0)
    existing_debts = ExistingDebt.objects.filter(balance__gt=0)
    debt_records = unpaid_sales.count()
    total_debt = unpaid_sales.aggregate(
        total=Sum('balance')
    )['total'] or Decimal('0.00')
    total_debt += existing_debts.aggregate(total=Sum('balance'))['total'] or Decimal('0.00')
    debtors_count = unpaid_sales.exclude(
        customer__isnull=True
    ).values('customer').distinct().count()
    debtors_count += existing_debts.values('customer').distinct().exclude(
        customer__in=unpaid_sales.values('customer')
    ).count()
    debt_records += existing_debts.count()

    context = {
        # Counts
        "products_count": products_count,
        "customers_count": customers_count,

        "can_view_financials": can_view_financials,

        "today_sales_count": today_sales_count,

        # Debt KPIs
        "total_debt": total_debt,
        "debt_records": debt_records,
        "debtors_count": debtors_count,
    }

    if can_view_financials:
        inventory_value = Product.objects.aggregate(
            total=Sum(
                F('quantity') * F('cost_price'),
                output_field=DecimalField(),
            )
        )['total'] or Decimal('0.00')
        potential_revenue = Product.objects.aggregate(
            total=Sum(
                F('quantity') * F('selling_price'),
                output_field=DecimalField(),
            )
        )['total'] or Decimal('0.00')
        sales_summary = Sale.objects.aggregate(
            actual_revenue=Sum('final_amount'),
            actual_profit=Sum('profit'),
            total_discounts=Sum('discount_amount'),
        )
        context.update({
            "inventory_value": inventory_value,
            "potential_revenue": potential_revenue,
            "potential_profit": potential_revenue - inventory_value,
            "today_revenue": today_sales.aggregate(total=Sum('final_amount'))['total'] or Decimal('0.00'),
            "actual_revenue": sales_summary['actual_revenue'] or Decimal('0.00'),
            "actual_profit": sales_summary['actual_profit'] or Decimal('0.00'),
            "total_discounts": sales_summary['total_discounts'] or Decimal('0.00'),
        })

    return render(request, "dashboard.html", context)


def new_sale(request):
    """POS: AJAX search + session cart checkout"""

    query = request.GET.get('q')

    products = Product.objects.filter(
        quantity__gt=0,
        is_available=True
    ).order_by('name')

    if query:
        products = products.filter(
            name__icontains=query
        )[:10]

    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        data = [
            {
                'id': p.id,
                'name': p.name,
                'price': float(p.selling_price),
                'stock': p.quantity,
                'base_unit': p.base_unit,
                'alternative_unit': p.alternative_unit if p.has_alternative_unit else None,
                'alternative_price': float(p.alternative_selling_price) if p.has_alternative_unit else None,
                'alternative_quantity': p.alternative_unit_quantity if p.has_alternative_unit else None,
                'units': product_unit_data(p),
            }
            for p in products
        ]
        return JsonResponse(data, safe=False)

    if request.method == 'POST':
        cart = request.POST.getlist('cart')

        if not cart:
            messages.error(request, "Cart is empty")
            return redirect('mini_mart:new_sale')

        try:
            with transaction.atomic():
                total_amount = Decimal('0.00')
                cost_amount = Decimal('0.00')
                items_data = []
                quantities = {}

                for item in cart:
                    parts = item.split(':')
                    product_id, qty = parts[:2]
                    unit = parts[2] if len(parts) > 2 else 'base'
                    qty = int(qty)
                    if qty > 0:
                        key = (product_id, unit)
                        quantities[key] = quantities.get(key, 0) + qty

                if not quantities:
                    raise ValueError('Cart is empty.')

                products_by_id = {
                    str(product.id): product
                    for product in Product.objects.select_for_update().filter(
                        id__in=[product_id for product_id, _ in quantities],
                        is_available=True,
                    )
                }
                if len(products_by_id) != len({product_id for product_id, _ in quantities}):
                    raise ValueError('One or more products are no longer available.')

                for (product_id, unit), units_sold in quantities.items():
                    product = products_by_id[product_id]
                    selected_unit = next(
                        (sale_unit for sale_unit in product.sale_units if sale_unit.code == unit),
                        None,
                    )
                    if selected_unit is None:
                        raise ValueError(f'{product.name} has no selling unit named {unit}.')
                    stock_quantity = units_sold * selected_unit.conversion_quantity
                    sale_price = selected_unit.selling_price
                    sale_unit = selected_unit.name
                    if stock_quantity > product.quantity:
                        raise ValueError(f'Not enough stock for {product.name}.')
                    total_amount += sale_price * units_sold
                    cost_amount += product.cost_price * stock_quantity
                    base_price = sale_price / selected_unit.conversion_quantity
                    items_data.append((product, stock_quantity, units_sold, base_price, sale_unit))

                sale = Sale.objects.create(
                    customer=None,
                    total_amount=total_amount,
                    cost_amount=cost_amount,
                    amount_paid=Decimal('0.00')
                )

                for product, stock_quantity, units_sold, base_price, sale_unit in items_data:
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        quantity=stock_quantity,
                        units_sold=units_sold,
                        sale_unit=sale_unit,
                        unit_price=base_price,
                    )
                    product.quantity -= stock_quantity
                    product.save(update_fields=['quantity'])
        except (TypeError, ValueError):
            messages.error(request, 'The cart contains invalid or unavailable items.')
            return redirect('mini_mart:new_sale')

        messages.success(
            request,
            f'Sale #{sale.id} created. '
            f'Total: ₦{sale.total_amount:,.2f}'
        )

        return redirect(
            'mini_mart:record_payment',
            pk=sale.id
        )

    return render(
        request,
        'new_sale.html',
        {'products': products}
    )


def offline_pos(request):
    products = Product.objects.filter(
        quantity__gt=0,
        is_available=True,
    ).order_by('name')
    product_data = [
        {
            'id': product.id,
            'name': product.name,
            'price': str(product.selling_price),
            'stock': product.quantity,
            'base_unit': product.base_unit,
            'alternative_unit': product.alternative_unit if product.has_alternative_unit else None,
            'alternative_price': str(product.alternative_selling_price) if product.has_alternative_unit else None,
            'alternative_quantity': product.alternative_unit_quantity if product.has_alternative_unit else None,
            'units': product_unit_data(product),
        }
        for product in products
    ]
    return render(request, 'offline_pos.html', {'products': product_data})


@csrf_exempt
@require_POST
def sync_offline_sale(request):
    try:
        payload = request.body.decode('utf-8')
        import json
        data = json.loads(payload)
        items = data.get('items', [])
        if not items:
            raise ValueError('The sale has no items.')

        with transaction.atomic():
            total_amount = Decimal('0.00')
            cost_amount = Decimal('0.00')
            sale_items = []
            quantities = {}

            for item in items:
                product_id = str(item['product_id'])
                quantity = int(item['quantity'])
                unit = item.get('unit', 'base')
                if quantity <= 0:
                    raise ValueError('Invalid quantity.')
                key = (product_id, unit)
                quantities[key] = quantities.get(key, 0) + quantity

            products = {
                str(product.id): product
                for product in Product.objects.select_for_update().filter(
                    id__in=[product_id for product_id, _ in quantities],
                    is_available=True,
                )
            }
            if len(products) != len({product_id for product_id, _ in quantities}):
                raise ValueError('A product is no longer available.')

            for (product_id, unit), units_sold in quantities.items():
                product = products[product_id]
                selected_unit = next(
                    (sale_unit for sale_unit in product.sale_units if sale_unit.code == unit),
                    None,
                )
                if selected_unit is None:
                    raise ValueError(f'{product.name} has no selling unit named {unit}.')
                stock_quantity = units_sold * selected_unit.conversion_quantity
                sale_price = selected_unit.selling_price
                sale_unit = selected_unit.name
                base_price = sale_price / selected_unit.conversion_quantity
                if stock_quantity > product.quantity:
                    raise ValueError(f'Not enough stock for {product.name}.')
                total_amount += sale_price * units_sold
                cost_amount += product.cost_price * stock_quantity
                sale_items.append((product, stock_quantity, units_sold, base_price, sale_unit))

            sale = Sale.objects.create(
                customer=None,
                total_amount=total_amount,
                cost_amount=cost_amount,
                amount_paid=total_amount,
            )
            for product, stock_quantity, units_sold, base_price, sale_unit in sale_items:
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    quantity=stock_quantity,
                    units_sold=units_sold,
                    sale_unit=sale_unit,
                    unit_price=base_price,
                )
                product.quantity -= stock_quantity
                product.save(update_fields=['quantity'])

        return JsonResponse({'status': 'synced', 'sale_id': sale.id})
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return JsonResponse({'error': str(error)}, status=400)

def record_payment(request, pk):
    """Single source of truth for payments. Uses Sale.balance from model."""
    sale = get_object_or_404(Sale, pk=pk)

    if request.method == 'POST':
        try:
            # Read and apply discount if provided
            discount = Decimal(request.POST.get('discount_amount', '0') or '0')
            if discount < 0 or discount > sale.total_amount:
                raise ValueError("Discount must be between zero and the sale total.")
            if sale.amount_paid > 0 and discount != sale.discount_amount:
                raise ValueError("The discount cannot be changed after payment.")

            amount_paid = Decimal(request.POST.get('amount', '0') or '0')
            if amount_paid < 0 or amount_paid > sale.balance:
                raise ValueError("Amount must be between zero and the outstanding balance.")

            sale.discount_amount = discount
            sale.full_clean()

            sale.amount_paid += amount_paid
            sale.save() # balance + is_paid auto update in model.save()

            messages.success(request, f'₦{amount_paid:,.2f} received. Balance: ₦{sale.balance:,.2f}')

            if sale.balance > 0: # Still owes
                # Only send to add-customer page for anonymous credit sales
                if sale.customer is None:
                    return redirect('mini_mart:add_customer_to_sale', pk=sale.id)
                # If sale already has a customer, go back to debts hub
                return redirect('mini_mart:debtors_list')
            else: # Paid full
                return redirect('mini_mart:dashboard')

        except (ValueError, ArithmeticError) as e:
            messages.error(request, f'Invalid amount: {e}')

    return render(request, 'record_payment.html', {'sale': sale})

def add_customer_to_sale(request, pk):
    """Attach customer to a credit sale. Only 1 version."""
    sale = get_object_or_404(Sale, pk=pk, customer__isnull=True, balance__gt=0)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        if not name or not phone_number:
            messages.error(request, "Name and phone number required")
        else:
            customer, created = Customer.objects.get_or_create(
                phone_number=phone_number, defaults={'name': name}
            )
            sale.customer = customer
            sale.save(update_fields=['customer'])
            messages.success(request, f'Debt of ₦{sale.balance:,.2f} attached to {customer.name}')
            return redirect('mini_mart:debtors_list')
    
    return render(request, 'add_customer_to_sale.html', {'sale': sale})

def sales_list(request):
    sales = Sale.objects.select_related('customer').order_by('-created_at')[:100]
    return render(request, 'sales_list.html', {'sales': sales})

def sales_history(request):
    """Display and search sales history."""

    q = request.GET.get('q', '').strip()

    sales = (
        Sale.objects
        .select_related('customer')
        .prefetch_related('items__product')
        .order_by('-created_at')
    )

    if q:
        sales = sales.filter(
            Q(id__iexact=q) |
            Q(customer__name__icontains=q) |
            Q(customer__phone_number__icontains=q) |
            Q(items__product__name__icontains=q)
        ).distinct()

    context = {
        'sales': sales,
        'q': q,
    }

    return render(request, 'sales_history.html', context)

def sale_detail(request, pk):
    sale = get_object_or_404(Sale, pk=pk)
    items = sale.items.select_related('product') # using related_name="items"
    return render(request, 'sale_detail.html', {'sale': sale, 'items': items})

@require_POST
def sale_delete(request, pk):
    with transaction.atomic():
        sale = get_object_or_404(
            Sale.objects.select_for_update(),
            pk=pk,
        )
        items = list(sale.items.select_related('product').select_for_update())
        for item in items:
            product = Product.objects.select_for_update().get(pk=item.product_id)
            product.quantity += item.quantity
            product.save(update_fields=['quantity'])

        sale_id = sale.pk
        sale.delete()

    messages.success(request, f'Sale #{sale_id} deleted and stock restored.')
    return redirect('mini_mart:sales_list')

def customer_list(request):
    customers = Customer.objects.all().order_by('name')
    total_debt = Sale.objects.filter(balance__gt=0).aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    total_debt += ExistingDebt.objects.filter(balance__gt=0).aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    return render(request, 'customer_list.html', {'customers': customers, 'total_debt': total_debt})

def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    sales = customer.sale_set.all().order_by('-created_at')
    existing_debts = customer.existingdebt_set.all().order_by('-created_at')
    total_spent = sales.aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00')
    total_debt = customer.total_debt
    return render(request, 'customer_detail.html', {
        'customer': customer, 'sales': sales, 'existing_debts': existing_debts,
        'total_spent': total_spent, 'total_debt': total_debt,
    })

def customer_add(request):
    if request.method == 'POST':
        Customer.objects.create(
            name=request.POST['name'],
            phone_number=request.POST.get('phone_number', ''),
            address=request.POST.get('address', ''),
        )
        messages.success(request, 'Customer added successfully.')
        return redirect('mini_mart:customer_list')
    return render(request, 'customer_form.html', {'action': 'Add'})

def product_list(request):
    query = request.GET.get('q')
    products = Product.objects.all()
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))
    products = products.order_by('-id')
    total_stock_value = sum(p.stock_value for p in products)
    return render(request, 'product_list.html', {
        'products': products, 'total_stock_value': total_stock_value, 'query': query or ''
    })

def product_add(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            units_formset = ProductUnitFormSet(request.POST, instance=product)
            if not units_formset.is_valid():
                product.delete()
            else:
                units_formset.save()
                messages.success(request, 'Product added successfully.')
                return redirect('mini_mart:product_list')
        else:
            units_formset = ProductUnitFormSet(request.POST)
    else:
        form = ProductForm()
        units_formset = ProductUnitFormSet()
    return render(request, 'product_form.html', {'form': form, 'units_formset': units_formset, 'action': 'Add'})

def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        units_formset = ProductUnitFormSet(request.POST, instance=product)
        if form.is_valid() and units_formset.is_valid():
            form.save()
            units_formset.save()
            messages.success(request, 'Product added successfully.')
            return redirect('mini_mart:product_list')
    else:
        form = ProductForm(instance=product)
        units_formset = ProductUnitFormSet(instance=product)
    return render(request, 'product_form.html', {'form': form, 'units_formset': units_formset, 'action': 'Edit', 'product': product})

def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        messages.success(request, 'Product deleted.')
        return redirect('mini_mart:product_list')
    return render(request, 'product_confirm_delete.html', {'product': product})

def debts_hub(request):
    q = request.GET.get('q', '').strip()
    debtors = Customer.objects.filter(
        Q(sale__balance__gt=0) | Q(existingdebt__balance__gt=0)
    )
    if q:
        debtors = debtors.filter(
            Q(name__icontains=q) | Q(phone_number__icontains=q)
        )
    debtors = debtors.distinct().order_by('name')
    total_outstanding = Sale.objects.filter(balance__gt=0, customer__isnull=False).aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    total_outstanding += ExistingDebt.objects.filter(balance__gt=0).aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    return render(request, 'debts_hub.html', {
        'debtors': debtors,
        'total_outstanding': total_outstanding,
        'q': q,
    })

def pay_customer_debt(request, pk):
    """Applies payment to the customer's oldest outstanding records first."""
    customer = get_object_or_404(Customer, pk=pk)
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
        except ArithmeticError:
            amount = Decimal('0')
        if amount <= 0:
            messages.error(request, 'Payment must be greater than zero.')
            return redirect('mini_mart:debts_hub')
        remaining = amount
        records = list(customer.sale_set.filter(balance__gt=0))
        records += list(customer.existingdebt_set.filter(balance__gt=0))
        records.sort(key=lambda record: record.created_at)
        for record in records:
            if remaining <= 0: break
            pay = min(remaining, record.balance)
            record.amount_paid += pay
            record.save()
            remaining -= pay
        messages.success(request, f'₦{amount:,.2f} received from {customer.name}')
    return redirect('mini_mart:debts_hub')

def debtors_list(request):
    q = request.GET.get('q', '').strip()
    debts = Sale.objects.select_related('customer').filter(balance__gt=0)
    if q:
        debts = debts.filter(
            Q(customer__name__icontains=q) |
            Q(customer__phone_number__icontains=q) |
            Q(id__icontains=q) |
            Q(items__product__name__icontains=q)
        ).distinct()
    debts = debts.order_by('created_at')
    total_owed = debts.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    existing_debts = ExistingDebt.objects.select_related('customer').filter(balance__gt=0)
    if q:
        existing_debts = existing_debts.filter(
            Q(customer__name__icontains=q) |
            Q(customer__phone_number__icontains=q) |
            Q(description__icontains=q)
        )
    existing_debts = existing_debts.order_by('created_at')
    total_owed += existing_debts.aggregate(t=Sum('balance'))['t'] or Decimal('0.00')
    return render(request, 'debtors.html', {
        'debts': debts,
        'existing_debts': existing_debts,
        'total_owed': total_owed,
        'q': q,
    })

def add_existing_debt(request):
    if request.method == 'POST':
        form = ExistingDebtForm(request.POST)
        if form.is_valid():
            debt = form.save()
            messages.success(request, f'Debt of ₦{debt.amount:,.2f} added for {debt.customer.name}.')
            return redirect('mini_mart:debts_hub')
    else:
        form = ExistingDebtForm()
    return render(request, 'existing_debt_form.html', {'form': form})

@require_POST 
def add_to_cart_ajax(request):
    """For rush-hour POS. Stores cart in session."""
    product_id = request.POST.get('product_id')
    qty = int(request.POST.get('qty', 1))
    cart = request.session.get('cart', {})
    cart[product_id] = cart.get(product_id, 0) + qty
    request.session['cart'] = cart
    request.session.modified = True
    total_items = sum(cart.values())
    return JsonResponse({'status': 'ok', 'total_items': total_items})

