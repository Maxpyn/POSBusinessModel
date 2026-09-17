from decimal import Decimal
import json

from django.db.models.deletion import ProtectedError
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.test import override_settings

from .models import Customer, ExistingDebt, Product, ProductPriceHistory, Sale, SaleItem, Tenant, TenantMembership


class PosWorkflowTests(TestCase):
	def setUp(self):
		self.user = get_user_model().objects.create_user(
			username="platform-owner",
			password="test-password",
		)
		self.tenant = Tenant.objects.get(slug="legacy")
		TenantMembership.objects.create(
			tenant=self.tenant,
			user=self.user,
			role=TenantMembership.Role.OWNER,
		)
		self.client.login(username="platform-owner", password="test-password")
		self.product = Product.objects.create(
			name="Rice",
			quantity=10,
			cost_price=Decimal("100.00"),
			selling_price=Decimal("150.00"),
		)

	def test_alternative_unit_sale_uses_alternative_price_and_base_stock(self):
		self.product.has_alternative_unit = True
		self.product.alternative_unit = "Mudu"
		self.product.alternative_unit_quantity = 18
		self.product.alternative_selling_price = Decimal("1800.00")
		self.product.quantity = 36
		self.product.save()

		response = self.client.post(
			reverse("mini_mart:new_sale"),
			{"cart": [f"{self.product.pk}:2:alternative"]},
		)

		sale = Sale.objects.get()
		item = sale.items.get()
		self.assertRedirects(response, reverse("mini_mart:record_payment", args=[sale.pk]))
		self.assertEqual(sale.total_amount, Decimal("3600.00"))
		self.assertEqual(item.units_sold, 2)
		self.assertEqual(item.sale_unit, "Mudu")
		self.assertEqual(item.quantity, 36)
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 0)

	def test_product_price_changes_create_a_history_snapshot(self):
		self.assertEqual(ProductPriceHistory.objects.filter(product=self.product).count(), 1)
		self.product.selling_price = Decimal("175.00")
		self.product.save()

		self.assertEqual(ProductPriceHistory.objects.filter(product=self.product).count(), 2)
		self.assertEqual(
			ProductPriceHistory.objects.filter(product=self.product).latest("changed_at").selling_price,
			Decimal("175.00"),
		)

	def test_new_sale_contains_remove_item_control(self):
		response = self.client.get(reverse("mini_mart:new_sale"))
		self.assertContains(response, "remove-cart-item")

	def test_owner_can_view_financial_kpis(self):
		response = self.client.get(reverse("mini_mart:dashboard"))

		self.assertTrue(response.context["can_view_financials"])
		self.assertIn("potential_revenue", response.context)

	def test_staff_without_financial_permission_cannot_view_financial_kpis(self):
		staff = get_user_model().objects.create_user(
			username="staff-user",
			password="test-password",
		)
		TenantMembership.objects.create(
			tenant=self.tenant,
			user=staff,
			role=TenantMembership.Role.STAFF,
		)
		self.client.force_login(staff)

		response = self.client.get(reverse("mini_mart:dashboard"))

		self.assertFalse(response.context["can_view_financials"])
		self.assertNotIn("inventory_value", response.context)
		self.assertNotIn("potential_revenue", response.context)
		self.assertNotContains(response, "Potential revenue")
		self.assertNotContains(response, "Potential Profit")

	@override_settings(FINANCIAL_KPI_PIN="4826")
	def test_pos_can_unlock_financial_kpis_with_owner_pin(self):
		self.client.logout()
		response = self.client.get(reverse("mini_mart:dashboard"))
		self.assertNotContains(response, "Potential revenue")

		response = self.client.post(
			reverse("mini_mart:dashboard"),
			{"financial_kpi_pin": "4826"},
		)
		self.assertRedirects(response, reverse("mini_mart:dashboard"))
		self.assertContains(self.client.get(reverse("mini_mart:dashboard")), "Potential revenue")

	def test_pos_dashboard_does_not_require_login(self):
		self.client.logout()
		response = self.client.get(reverse("mini_mart:dashboard"))
		self.assertEqual(response.status_code, 200)

	def test_product_form_shows_and_saves_alternative_unit_fields(self):
		form_response = self.client.get(reverse("mini_mart:product_add"))

		self.assertContains(form_response, 'name="base_unit"')
		self.assertContains(form_response, 'name="has_alternative_unit"')
		self.assertContains(form_response, 'name="alternative_unit"')
		self.assertContains(form_response, 'name="alternative_unit_quantity"')
		self.assertContains(form_response, 'name="alternative_selling_price"')

		response = self.client.post(
			reverse("mini_mart:product_add"),
			{
				"name": "Rice Cup",
				"description": "Sold by cup or mudu",
				"quantity": 540,
				"base_unit": "Cup",
				"cost_price": "84.00",
				"selling_price": "100.00",
				"has_alternative_unit": "on",
				"alternative_unit": "Mudu",
				"alternative_unit_quantity": 18,
				"alternative_selling_price": "1800.00",
			},
		)

		self.assertRedirects(response, reverse("mini_mart:product_list"))
		product = Product.objects.get(name="Rice Cup")
		self.assertEqual(product.base_unit, "Cup")
		self.assertEqual(product.alternative_unit, "Mudu")
		self.assertEqual(product.alternative_unit_quantity, 18)
		self.assertEqual(product.alternative_selling_price, Decimal("1800.00"))

	def test_checkout_consolidates_cart_and_reduces_stock(self):
		response = self.client.post(
			reverse("mini_mart:new_sale"),
			{"cart": [f"{self.product.pk}:2", f"{self.product.pk}:3"]},
		)

		sale = Sale.objects.get()
		self.assertRedirects(response, reverse("mini_mart:record_payment", args=[sale.pk]))
		self.assertEqual(sale.total_amount, Decimal("750.00"))
		self.assertEqual(sale.items.get().quantity, 5)
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 5)

	def test_checkout_rejects_insufficient_stock_without_creating_sale(self):
		response = self.client.post(
			reverse("mini_mart:new_sale"),
			{"cart": [f"{self.product.pk}:11"]},
		)

		self.assertRedirects(response, reverse("mini_mart:new_sale"))
		self.assertFalse(Sale.objects.exists())
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 10)

	def test_payment_rejects_overpayment(self):
		sale = Sale.objects.create(
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)

		response = self.client.post(
			reverse("mini_mart:record_payment", args=[sale.pk]),
			{"amount": "151.00", "discount_amount": "0"},
		)

		sale.refresh_from_db()
		self.assertEqual(sale.amount_paid, Decimal("0.00"))
		self.assertEqual(response.status_code, 200)

	def test_customer_debt_views_use_sale_balances(self):
		customer = Customer.objects.create(name="Ada")
		Sale.objects.create(
			customer=customer,
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)

		response = self.client.get(reverse("mini_mart:debts_hub"))

		self.assertContains(response, "₦150.00")
		self.assertEqual(response.context["total_outstanding"], Decimal("150.00"))

	def test_existing_debt_is_added_and_paid_through_customer_debt_flow(self):
		customer = Customer.objects.create(name="Ada")

		response = self.client.post(
			reverse("mini_mart:add_existing_debt"),
			{"customer": customer.pk, "amount": "275.00", "description": "Old balance"},
		)

		self.assertRedirects(response, reverse("mini_mart:debts_hub"))
		debt = ExistingDebt.objects.get(customer=customer)
		self.assertEqual(debt.balance, Decimal("275.00"))
		self.assertEqual(customer.total_debt, Decimal("275.00"))

		self.client.post(
			reverse("mini_mart:pay_customer_debt", args=[customer.pk]),
			{"amount": "100.00"},
		)
		debt.refresh_from_db()
		self.assertEqual(debt.amount_paid, Decimal("100.00"))
		self.assertEqual(debt.balance, Decimal("175.00"))

	def test_debts_hub_search_matches_customer_name_or_phone(self):
		matching_customer = Customer.objects.create(name="Ada Lovelace", phone_number="08012345678")
		other_customer = Customer.objects.create(name="Grace Hopper", phone_number="09087654321")
		for customer in (matching_customer, other_customer):
			Sale.objects.create(
				customer=customer,
				total_amount=Decimal("150.00"),
				cost_amount=Decimal("100.00"),
			)

		name_response = self.client.get(reverse("mini_mart:debts_hub"), {"q": "lovelace"})
		phone_response = self.client.get(reverse("mini_mart:debts_hub"), {"q": "09087654321"})

		self.assertEqual(list(name_response.context["debtors"]), [matching_customer])
		self.assertEqual(list(phone_response.context["debtors"]), [other_customer])

	def test_debtors_list_search_matches_customer_phone_sale_or_product(self):
		customer = Customer.objects.create(name="Ada Lovelace", phone_number="08023456789")
		other_customer = Customer.objects.create(name="Grace Hopper", phone_number="09087654320")
		other_product = Product.objects.create(
			name="Beans",
			quantity=10,
			cost_price=Decimal("80.00"),
			selling_price=Decimal("120.00"),
		)
		matching_sale = Sale.objects.create(
			customer=customer,
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)
		other_sale = Sale.objects.create(
			customer=other_customer,
			total_amount=Decimal("200.00"),
			cost_amount=Decimal("120.00"),
		)
		SaleItem.objects.create(
			sale=matching_sale,
			product=self.product,
			quantity=1,
			unit_price=Decimal("150.00"),
		)
		SaleItem.objects.create(
			sale=other_sale,
			product=other_product,
			quantity=1,
			unit_price=Decimal("120.00"),
		)

		for query in ("lovelace", "08023456789", str(matching_sale.pk), "rice"):
			with self.subTest(query=query):
				response = self.client.get(reverse("mini_mart:debtors_list"), {"q": query})
				self.assertEqual(list(response.context["debts"]), [matching_sale])

		response = self.client.get(reverse("mini_mart:debtors_list"), {"q": "lovelace"})
		self.assertEqual(response.context["total_owed"], Decimal("150.00"))

	def test_sales_history_search_matches_product_name(self):
		sale = Sale.objects.create(
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)
		SaleItem.objects.create(
			sale=sale,
			product=self.product,
			quantity=1,
			unit_price=Decimal("150.00"),
		)

		response = self.client.get(reverse("mini_mart:sales_history"), {"q": "rice"})

		self.assertEqual(response.status_code, 200)
		self.assertIn(sale, response.context["sales"])

	def test_sale_delete_restores_stock_and_removes_credit_from_history(self):
		customer = Customer.objects.create(name="Ada")
		sale = Sale.objects.create(
			customer=customer,
			total_amount=Decimal("300.00"),
			cost_amount=Decimal("200.00"),
		)
		SaleItem.objects.create(
			sale=sale,
			product=self.product,
			quantity=2,
			unit_price=Decimal("150.00"),
		)
		self.product.quantity = 8
		self.product.save(update_fields=["quantity"])

		response = self.client.post(reverse("mini_mart:sale_delete", args=[sale.pk]))

		self.assertRedirects(response, reverse("mini_mart:sales_list"))
		self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 10)
		self.assertEqual(
			Sale.objects.filter(customer=customer, balance__gt=0).count(),
			0,
		)

	def test_sale_delete_requires_post(self):
		sale = Sale.objects.create(
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)

		response = self.client.get(reverse("mini_mart:sale_delete", args=[sale.pk]))

		self.assertEqual(response.status_code, 405)
		self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

	def test_product_with_sale_history_cannot_be_deleted(self):
		sale = Sale.objects.create(
			total_amount=Decimal("150.00"),
			cost_amount=Decimal("100.00"),
		)
		SaleItem.objects.create(
			sale=sale,
			product=self.product,
			quantity=1,
			unit_price=Decimal("150.00"),
		)

		with self.assertRaises(ProtectedError):
			self.product.delete()

	def test_offline_sale_sync_records_paid_sale_and_reduces_stock(self):
		response = self.client.post(
			reverse("mini_mart:sync_offline_sale"),
			data=json.dumps({"items": [{"product_id": self.product.pk, "quantity": 2}]}),
			content_type="application/json",
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["status"], "synced")
		sale = Sale.objects.get()
		self.assertEqual(sale.amount_paid, Decimal("300.00"))
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 8)

	def test_offline_sale_sync_rejects_insufficient_stock(self):
		response = self.client.post(
			reverse("mini_mart:sync_offline_sale"),
			data=json.dumps({"items": [{"product_id": self.product.pk, "quantity": 11}]}),
			content_type="application/json",
		)

		self.assertEqual(response.status_code, 400)
		self.assertFalse(Sale.objects.exists())
		self.assertEqual(Product.objects.get(pk=self.product.pk).quantity, 10)
