"""
The browser addresses a patient by UUID; the database still addresses the row
by its integer pk. This holds the seam between the two.

Two properties matter here and nowhere else says them:

1. **A row that drives a link to a chart carries `patient_uuid`.** A queue
   entry, a ledger, a recorded reading and an adjustment all mention a patient
   without being one, and the page that renders them has to be able to link
   somewhere without sending the reader to an integer. They keep `patient_id` /
   `patient` beside it, because that is what every `?patient=` filter takes.
2. **A chart link we raise ourselves is a UUID link.** Notification
   `action_url`s and dashboard task hrefs are what a person clicks. Rows
   written before the URL moved keep their integer and reach the chart through
   the compatibility redirect in `PatientDetail`, which is why
   `test_notification_links.routable()` still matches both shapes.
"""
import re

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.services import add_charge, apply_percentage_discount
from apps.clinical.models import Vitals
from apps.departments.models import Department
from apps.core.models import Notification
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
CHART_UUID_URL = re.compile(
    r"^/patients/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(/[a-z]+)?$", re.I)


def rows_of(response):
    data = response.data
    return data.get("results", []) if isinstance(data, dict) else data


class RowsThatLinkToAChartTests(TestCase):
    """Every payload a `/patients/<…>` link is built from carries the UUID."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.admin = User.objects.create_user(username="adm", password="t", role="hospital_admin")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Okonkwo", sex="F",
                                              created_by=self.reception)
        self.department = Department.objects.create(code="nursing", name="Nursing")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.route = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                                 purpose="vitals", routed_by=self.reception,
                                                 assigned_to=self.nurse)

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user); return c

    def test_a_queue_row_carries_both_identifiers(self):
        rows = rows_of(self._client(self.nurse).get("/api/patient-routes/"))
        self.assertTrue(rows)
        for row in rows:
            self.assertRegex(str(row["patient_uuid"]), UUID_RE)
            self.assertEqual(str(row["patient_uuid"]), str(self.patient.uuid))
            # The pk stays: `?patient=` and the FK bodies still take it.
            self.assertEqual(row["patient_id"], self.patient.pk)

    def test_a_recorded_reading_carries_both_identifiers(self):
        Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                              visit_time=timezone.now(), temperature_c=37)
        rows = rows_of(self._client(self.nurse).get("/api/vitals/recorded-today/"))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(str(row["patient_uuid"]), str(self.patient.uuid))
            self.assertEqual(row["patient_id"], self.patient.pk)

    def test_a_ledger_row_carries_both_identifiers(self):
        add_charge(patient=self.patient, description="Card", amount=500, created_by=self.reception)
        rows = rows_of(self._client(self.cashier).get("/api/ledgers/"))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(str(row["patient_uuid"]), str(self.patient.uuid))
            self.assertEqual(row["patient"], self.patient.pk)

    def test_an_adjustment_row_carries_both_identifiers(self):
        charge = add_charge(patient=self.patient, description="Card", amount=500,
                           created_by=self.reception)
        apply_percentage_discount(charge=charge, percent=10, reason="Staff",
                                  approved_by=self.cashier)
        rows = rows_of(self._client(self.cashier).get("/api/adjustments/"))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(str(row["patient_uuid"]), str(self.patient.uuid))
            self.assertEqual(row["patient"], self.patient.pk)

    def test_the_uuid_on_a_row_is_still_not_permission(self):
        """The whole reason this is safe to publish on a row."""
        stranger = User.objects.create_user(username="passing", password="t", role="doctor")
        client = self._client(stranger)
        self.assertEqual(client.get(f"/api/patients/{self.patient.uuid}/").status_code, 404)
        self.assertEqual(client.get(f"/api/patients/{self.patient.pk}/").status_code, 404)


class ChartLinksWeRaiseTests(TestCase):
    """A notification or dashboard link to a chart addresses it by UUID."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec2", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc2", password="t", role="doctor")
        self.nurse = User.objects.create_user(username="nur2", password="t", role="nurse")
        self.patient = Patient.objects.create(first_name="Ngozi", last_name="Ike", sex="F",
                                              created_by=self.reception)
        self.nursing = Department.objects.create(code="nursing", name="Nursing")

    def _chart_links(self):
        return [n.action_url for n in Notification.objects.all()
                if n.action_url.startswith("/patients/")]

    def test_a_reading_landing_on_a_chart_links_by_uuid(self):
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                     attending_doctor=self.doctor)
        PatientRoute.objects.create(visit=visit, department=self.nursing, purpose="consultation",
                                    routed_by=self.reception, assigned_to=self.doctor,
                                    status="in_progress")
        client = APIClient(); client.force_authenticate(self.nurse)
        response = client.post("/api/vitals/", {
            "patient": self.patient.pk, "visit_time": timezone.now().isoformat(),
            "temperature_c": "37.2",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        links = self._chart_links()
        self.assertTrue(links, "expected the doctor to be told about the reading")
        for url in links:
            self.assertRegex(url, CHART_UUID_URL)
            self.assertIn(str(self.patient.uuid), url)

    def test_a_referral_result_links_the_doctor_to_the_chart_by_uuid(self):
        client = APIClient(); client.force_authenticate(self.doctor)
        Visit.objects.create(patient=self.patient, opened_by=self.reception,
                             attending_doctor=self.doctor)
        referred = client.post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "ultrasound", "notes": "Scan please.",
        }, format="json")
        self.assertEqual(referred.status_code, 201, referred.data)
        radiology = User.objects.create_user(username="radio", password="t", role="radiology")
        bench = APIClient(); bench.force_authenticate(radiology)
        Notification.objects.all().delete()
        done = bench.post(f"/api/patient-routes/{referred.data['id']}/record-result/",
                          {"result": "Normal study."}, format="json")
        self.assertEqual(done.status_code, 200, done.data)
        links = self._chart_links()
        self.assertTrue(links, "expected the referring doctor to be told")
        for url in links:
            self.assertRegex(url, CHART_UUID_URL)

    def test_no_chart_link_we_raise_is_built_from_the_integer_pk(self):
        client = APIClient(); client.force_authenticate(self.reception)
        visit = client.post("/api/visits/", {"patient": self.patient.pk, "visit_type": "opd",
                                             "reason": "Fever"}, format="json")
        client.post("/api/patient-routes/", {
            "visit": visit.data["id"], "department": self.nursing.id,
            "purpose": "consultation", "assigned_to": self.doctor.id,
        }, format="json")
        for url in self._chart_links():
            # Anchored to the end of the segment: a UUID can begin with digits,
            # so a bare `\d+` matches one and proves nothing.
            self.assertNotRegex(url, r"^/patients/\d+(?:/|$)")
