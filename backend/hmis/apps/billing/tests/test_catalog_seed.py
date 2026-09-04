"""
The service categories arrive usable.

Card and Consultation were priced by hand, so the four service categories
added later opened as empty dropdowns at the counter. The migration gives
each one a starter, and leaves alone any category somebody has priced
themselves.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillingItem
from apps.patients.models import Patient

SERVICE_CATEGORIES = ["laboratory", "ultrasound", "eye", "procedure"]


class CatalogStartersTests(TestCase):
    def test_every_service_category_has_something_priced(self):
        for category in SERVICE_CATEGORIES:
            with self.subTest(category=category):
                items = BillingItem.objects.filter(category=category, is_active=True)
                self.assertTrue(items.exists(), f"{category} opens as an empty dropdown")
                self.assertGreater(items.first().price, Decimal("0"))

    def test_the_starters_are_billable_end_to_end(self):
        reception = User.objects.create_user(username="reception", password="test", role="reception")
        patient = Patient.objects.create(first_name="Jane", last_name="Doe", sex="F",
                                         created_by=reception)
        client = APIClient(); client.force_authenticate(reception)

        for category in SERVICE_CATEGORIES:
            with self.subTest(category=category):
                listed = client.get("/api/billing-items/", {"category": category, "is_active": True})
                self.assertEqual(listed.status_code, 200)
                item = listed.data["results"][0]
                charged = client.post("/api/charges/", {
                    "patient": patient.id, "description": item["name"], "amount": item["price"],
                }, format="json")
                self.assertEqual(charged.status_code, 201, charged.data)

    def test_a_starter_can_be_repriced_like_any_other_item(self):
        cashier = User.objects.create_user(username="cashier", password="test", role="cashier")
        client = APIClient(); client.force_authenticate(cashier)
        item = BillingItem.objects.filter(category="laboratory").first()

        response = client.patch(f"/api/billing-items/{item.id}/", {"price": "4200"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.price, Decimal("4200"))
