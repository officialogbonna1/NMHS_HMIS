"""
The eye doctor prescribes through the pharmacy workflow that already exists
(rule 6), and prescribing is never dispensing:

    eye doctor → POST /prescriptions/bulk/ → pending, no stock touched
    pharmacist → /dispense/ → FEFO off the pharmacy shelf → StockMovement
               → stock decreases → the existing Pharmacy charge
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.core.models import Notification
from apps.departments.models import Department
from apps.inventory.models import PHARMACY, StockMovement, StockRecord
from apps.inventory.testing import product, stock_the_pharmacy
from apps.patients.models import Patient
from apps.pharmacy.models import Prescription
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class EyePrescribingTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="eye", password="t", role="ophthalmologist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.pharmacist = User.objects.create_user(username="ph", password="t", role="pharmacist")
        department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.reception)
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=department, purpose="eye",
                                    assigned_to=self.eye_doctor, routed_by=self.reception, status="in_progress")
        self.stranger = Patient.objects.create(first_name="Bo", last_name="Eze", sex="M", created_by=self.reception)

        self.drops = product("Timolol 0.5% eye drops", unit_name="bottle", category_name="Ophthalmic")
        self.early = stock_the_pharmacy(item=self.drops, quantity=3, actor=self.pharmacist,
                                        batch_no="TIM-EARLY", sale_price="1500", expiry_days=60)
        self.late = stock_the_pharmacy(item=self.drops, quantity=10, actor=self.pharmacist,
                                       batch_no="TIM-LATE", sale_price="1600", expiry_days=400)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def held(self, batch):
        return StockRecord.objects.get(batch=batch, location__code=PHARMACY).quantity

    def prescribe(self, user=None, patient=None, quantity=4):
        return self.as_(user or self.eye_doctor).post("/api/prescriptions/bulk/", {
            "patient": (patient or self.patient).id,
            "lines": [{"item": self.drops.id, "quantity": quantity, "dosage_instructions": "1 drop",
                       "frequency": "Twice daily", "duration": "4 weeks", "notes": "Right eye"}],
        }, format="json")

    def test_the_script_is_queued_for_the_pharmacy_and_moves_no_stock(self):
        movements = StockMovement.objects.count()
        response = self.prescribe()
        self.assertEqual(response.status_code, 201, response.data)
        prescription = Prescription.objects.get(pk=response.data[0]["id"])
        self.assertEqual((prescription.status, prescription.doctor, prescription.quantity),
                         ("pending", self.eye_doctor, 4))

        self.assertEqual((self.held(self.early), self.held(self.late)), (3, 10))
        self.assertEqual(StockMovement.objects.count(), movements)
        self.assertFalse(Charge.objects.filter(patient=self.patient).exists())

        note = Notification.objects.get(recipient=self.pharmacist, category="pharmacy")
        self.assertEqual(note.action_url, "/pharmacy")
        queue = rows(self.as_(self.pharmacist).get("/api/prescriptions/", {"status": "pending"}))
        self.assertEqual([row["id"] for row in queue], [prescription.id])

    def test_the_pharmacist_dispenses_it_first_expiry_first_and_the_charge_is_raised(self):
        prescription_id = self.prescribe(quantity=4).data[0]["id"]
        response = self.as_(self.pharmacist).post(f"/api/prescriptions/{prescription_id}/dispense/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "dispensed")

        # 3 off the earlier lot, 1 off the later — the shelf's FEFO.
        self.assertEqual((self.held(self.early), self.held(self.late)), (0, 9))
        self.assertEqual(
            sorted(StockMovement.objects.filter(reference=f"prescription:{prescription_id}")
                   .values_list("batch__batch_no", "change", "location__code", "reason")),
            [("TIM-EARLY", -3, PHARMACY, "prescription"), ("TIM-LATE", -1, PHARMACY, "prescription")])

        charge = Charge.objects.get(source_type="prescription", source_id=prescription_id)
        self.assertEqual((charge.patient, charge.amount, charge.department.code, charge.status),
                         (self.patient, 3 * 1500 + 1 * 1600, "pharmacy", "unpaid"))
        self.assertTrue(Notification.objects.filter(recipient=self.eye_doctor, category="pharmacy").exists())

    def test_prescribing_is_never_permission_to_dispense(self):
        prescription_id = self.prescribe().data[0]["id"]
        self.assertEqual(self.as_(self.eye_doctor).post(f"/api/prescriptions/{prescription_id}/dispense/").status_code, 403)
        self.assertEqual(Prescription.objects.get(pk=prescription_id).status, "pending")
        self.assertEqual((self.held(self.early), self.held(self.late)), (3, 10))

    def test_a_patient_who_is_not_theirs_gets_no_prescription(self):
        response = self.prescribe(patient=self.stranger)
        self.assertEqual((response.status_code, response.data["code"]), (403, "not_your_patient"))
        single = self.as_(self.eye_doctor).post("/api/prescriptions/", {
            "patient": self.stranger.id, "item": self.drops.id, "quantity": 1}, format="json")
        self.assertEqual(single.status_code, 403)
        self.assertFalse(Prescription.objects.exists())

    def test_the_drug_picker_shows_availability_and_never_a_count(self):
        api = self.as_(self.eye_doctor)
        response = api.get("/api/items/", {"search": "Timolol"})
        self.assertEqual(response.status_code, 200)
        row = next(r for r in rows(response) if r["id"] == self.drops.id)
        self.assertTrue(row["available"])
        self.assertEqual([key for key in row if "quantity" in key or "stock" in key], [])
        self.assertEqual(api.get(f"/api/items/{self.drops.id}/").status_code, 403)

    def test_the_eye_doctor_cannot_touch_inventory(self):
        api = self.as_(self.eye_doctor)
        self.assertEqual(api.post("/api/items/", {"name": "Atropine"}, format="json").status_code, 403)
        self.assertEqual(api.patch(f"/api/items/{self.drops.id}/", {"name": "x"}, format="json").status_code, 403)
        for path in ("/api/batches/", "/api/stock-records/", "/api/stock-movements/",
                     "/api/stock-transfers/", "/api/stock-counts/"):
            with self.subTest(path=path):
                self.assertEqual(api.get(path).status_code, 403)
                self.assertEqual(api.post(path, {}, format="json").status_code, 403)
        self.assertEqual((self.held(self.early), self.held(self.late)), (3, 10))

    def test_they_read_and_cancel_only_their_own_scripts(self):
        mine = self.prescribe(quantity=1).data[0]["id"]
        theirs = self.prescribe(user=self.doctor, quantity=1).data[0]["id"]
        api = self.as_(self.eye_doctor)
        self.assertEqual([row["id"] for row in rows(api.get("/api/prescriptions/"))], [mine])
        self.assertEqual(api.post(f"/api/prescriptions/{theirs}/cancel/", {"reason": "x"}, format="json").status_code, 404)
        cancelled = api.post(f"/api/prescriptions/{mine}/cancel/", {"reason": "Changed to drops"}, format="json")
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(Prescription.objects.get(pk=theirs).status, "pending")
        self.assertEqual((self.held(self.early), self.held(self.late)), (3, 10))

    def test_the_general_doctors_prescribing_is_unchanged(self):
        # The general doctor's writes were never held to their own list, and
        # admitting the eye doctor did not change that (patients.access
        # .ASSIGNED_WRITE_ROLES).
        response = self.prescribe(user=self.doctor, patient=self.stranger, quantity=2)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Prescription.objects.get(pk=response.data[0]["id"]).status, "pending")
