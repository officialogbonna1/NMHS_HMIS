"""
The Eye Doctor Desk is the existing desk: the dashboard, the /eye station,
the chart, referrals and billing — reached by the eye doctor for their own
patients, with nothing eye-specific in the money or the routing.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import Charge
from apps.clinical.models import ConsultationNote
from apps.core.models import AuditLog, Notification
from apps.departments.models import Department
from apps.laboratory.models import LabOrder, LabOrderTest, LabTest
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class EyeDoctorDeskTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="eye", password="t", role="ophthalmologist")
        self.other_eye = User.objects.create_user(username="eye2", password="t", role="ophthalmologist")
        self.optometrist = User.objects.create_user(username="opto", password="t", role="optometrist")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.department = Department.objects.create(code="eye-unit-test", name="Eye Unit (test)")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F", created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        self.route = PatientRoute.objects.create(visit=self.visit, department=self.department, purpose="eye",
                                                 assigned_to=self.eye_doctor, routed_by=self.reception,
                                                 status="in_progress")
        self.stranger = Patient.objects.create(first_name="Bo", last_name="Eze", sex="M", created_by=self.reception)
        self.stranger_visit = Visit.objects.create(patient=self.stranger, opened_by=self.reception,
                                                   attending_doctor=self.doctor)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def dashboard(self, user):
        response = self.as_(user).get("/api/dashboard/")
        self.assertEqual(response.status_code, 200)
        return response.data

    # --- the desk ---------------------------------------------------------------

    def test_the_desk_counts_their_own_patients_referrals_and_consultations(self):
        ConsultationNote.objects.create(patient=self.patient, doctor=self.eye_doctor,
                                        visit_time=timezone.now(), reason_for_visit="Review")
        cards = {c["key"]: c for c in self.dashboard(self.eye_doctor)["cards"] if "key" in c}
        self.assertEqual(cards["my_patients"]["value"], 1)
        self.assertEqual(cards["eye_consultations_today"]["value"], 1)
        self.assertEqual(cards["referrals_in_progress"]["value"], 1)
        self.assertEqual(cards["my_queue"]["href"], "/eye")
        # Nobody else's desk changed.
        self.assertNotIn("eye_consultations_today",
                         {c.get("key") for c in self.dashboard(self.doctor)["cards"]})
        self.assertNotIn("eye_consultations_today",
                         {c.get("key") for c in self.dashboard(self.optometrist)["cards"]})

    def test_a_claimed_referral_opens_the_chart_and_an_unclaimed_one_the_station(self):
        waiting = PatientRoute.objects.create(visit=self.stranger_visit, department=self.department,
                                              purpose="eye", routed_by=self.reception)
        tasks = {task["id"]: task["href"] for task in self.dashboard(self.eye_doctor)["tasks"]}
        self.assertEqual(tasks[self.route.id], f"/patients/{self.patient.uuid}")
        self.assertEqual(tasks[waiting.id], "/eye")

    def test_reception_routes_an_eye_patient_to_the_shared_queue_or_to_a_named_eye_doctor(self):
        front_desk = self.as_(self.reception)
        pooled = front_desk.post("/api/patient-routes/", {
            "visit": self.stranger_visit.id, "department": self.department.id, "purpose": "eye",
            "notes": "Painful red eye"}, format="json")
        self.assertEqual(pooled.status_code, 201, pooled.data)
        self.assertEqual(Notification.objects.get(recipient=self.eye_doctor).action_url, "/eye")

        Notification.objects.all().delete()
        named = front_desk.post("/api/patient-routes/", {
            "visit": self.visit.id, "department": self.department.id, "purpose": "eye",
            "assigned_to": self.other_eye.id}, format="json")
        self.assertEqual(named.status_code, 201, named.data)
        self.assertEqual(Notification.objects.get(recipient=self.other_eye).action_url,
                         f"/patients/{self.patient.uuid}")
        self.assertFalse(Notification.objects.filter(recipient=self.eye_doctor).exists())

    # --- referrals and money ------------------------------------------------------

    def test_a_laboratory_referral_is_billed_by_the_existing_laboratory_path(self):
        api = self.as_(self.eye_doctor)
        referral = api.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": "laboratory",
            "notes": "Fasting glucose before cataract surgery"}, format="json")
        self.assertEqual(referral.status_code, 201, referral.data)
        self.assertTrue(Notification.objects.filter(recipient=self.lab, category="routing").exists())

        order = api.post("/api/lab-orders/for-route/", {"route": referral.data["id"]}, format="json")
        self.assertEqual(order.status_code, 200, order.data)
        test = LabTest.objects.filter(is_active=True, price__gt=0).first()
        added = api.post(f"/api/lab-orders/{order.data['id']}/add-tests/", {"tests": [test.id]}, format="json")
        self.assertEqual(added.status_code, 200, added.data)

        line = LabOrderTest.objects.get(order_id=order.data["id"])
        charge = line.charge
        self.assertEqual((charge.patient, charge.source_type, charge.amount, charge.department.code),
                         (self.patient, "lab_test", line.unit_price, "laboratory"))
        self.assertEqual(LabOrder.objects.get(pk=order.data["id"]).requested_by, self.eye_doctor)
        # The eye doctor reads their own patient's order back on the chart.
        self.assertEqual(len(rows(api.get("/api/lab-orders/", {"patient": self.patient.id, "detail": "1"}))), 1)

    def test_no_referral_or_order_for_a_patient_who_is_not_theirs(self):
        api = self.as_(self.eye_doctor)
        refused = api.post("/api/patient-routes/refer/", {"patient": self.stranger.id,
                                                          "purpose": "laboratory"}, format="json")
        self.assertEqual((refused.status_code, refused.data["code"]), (403, "not_your_patient"))
        self.assertFalse(self.stranger_visit.routes.exists())

        doctors_referral = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.stranger.id, "purpose": "laboratory"}, format="json")
        self.assertEqual(doctors_referral.status_code, 201, doctors_referral.data)
        self.assertEqual(api.post("/api/lab-orders/for-route/", {"route": doctors_referral.data["id"]},
                                  format="json").status_code, 403)
        self.assertFalse(LabOrder.objects.exists())
        self.assertFalse(Charge.objects.filter(patient=self.stranger).exists())

    def test_the_optometrist_still_does_not_refer_or_prescribe(self):
        api = self.as_(self.optometrist)
        self.assertEqual(api.post("/api/patient-routes/refer/", {"patient": self.patient.id,
                                                                 "purpose": "laboratory"}, format="json").status_code, 403)
        self.assertEqual(api.post("/api/prescriptions/bulk/", {"patient": self.patient.id, "lines": []},
                                  format="json").status_code, 403)


class ReceptionRoutesToOphthalmologyTests(TestCase):
    """
    The front desk's own workflow, end to end: continue the patient's visit,
    send them to the eye clinic, name an eye clinician, and the existing
    notification and queue carry it. Nothing here is eye-specific — it is the
    one `PatientRoute` mechanism with `purpose="eye"`.
    """

    def setUp(self):
        self.reception = User.objects.create_user(username="front", password="t", role="reception",
                                                  first_name="Ngozi", last_name="Desk")
        self.eye_doctor = User.objects.create_user(username="drjohn", password="t",
                                                   role="ophthalmologist",
                                                   first_name="John", last_name="Doe")
        self.optometrist = User.objects.create_user(username="opto", password="t", role="optometrist")
        self.cashier = User.objects.create_user(username="till", password="t", role="cashier")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        # The seeded Eye Clinic, not a department this test invented — the
        # registry's `code` is what reception's screen pairs the purpose with.
        self.eye_department = Department.objects.get(code="eye")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          reason="Blurred vision for two weeks")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def route(self, **overrides):
        body = {"visit": self.visit.id, "department": self.eye_department.id, "purpose": "eye",
                "notes": "Ophthalmology consultation"}
        body.update(overrides)
        return self.as_(self.reception).post("/api/patient-routes/", body, format="json")

    def test_reception_routes_to_ophthalmology_and_assigns_an_eye_doctor(self):
        response = self.route(assigned_to=self.eye_doctor.id, priority="urgent")
        self.assertEqual(response.status_code, 201, response.data)
        created = PatientRoute.objects.get(pk=response.data["id"])
        self.assertEqual(
            (created.purpose, created.department, created.assigned_to, created.routed_by, created.status),
            ("eye", self.eye_department, self.eye_doctor, self.reception, "queued"))
        # The visit is the patient's existing one — routing opens no second record.
        self.assertEqual(created.visit, self.visit)

    def test_the_optometrist_is_eligible_too(self):
        self.assertEqual(self.route(assigned_to=self.optometrist.id).status_code, 201)

    def test_an_ineligible_staff_member_cannot_be_put_on_the_ophthalmology_queue(self):
        for ineligible in (self.cashier, self.nurse):
            with self.subTest(role=ineligible.role):
                refused = self.route(assigned_to=ineligible.id)
                self.assertEqual(refused.status_code, 400, refused.data)
                self.assertIn("assigned_to", refused.data)
        self.assertFalse(PatientRoute.objects.exists())

    def test_the_assigned_eye_doctor_is_notified_with_the_patient_and_who_sent_them(self):
        self.route(assigned_to=self.eye_doctor.id)
        note = Notification.objects.get(recipient=self.eye_doctor)
        self.assertEqual(note.category, "routing")
        self.assertIn("Eye clinic requested", note.title)
        self.assertIn(self.patient.display_name, note.title)
        for expected in (self.patient.patient_number, "Ngozi Desk", "John Doe",
                         "Ophthalmology consultation"):
            self.assertIn(expected, note.message)
        # Named work goes to that person, and to nobody else (rule 14).
        self.assertFalse(Notification.objects.filter(recipient=self.optometrist).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.reception).exists())

    def test_unassigned_ophthalmology_work_reaches_the_whole_eye_clinic(self):
        self.route()
        told = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertEqual(told, {self.eye_doctor.pk, self.optometrist.pk})
        self.assertIn("Unassigned", Notification.objects.first().message)

    def test_the_patient_appears_in_the_ophthalmology_queue_with_what_the_clinic_needs(self):
        self.route(assigned_to=self.eye_doctor.id)
        queue = rows(self.as_(self.eye_doctor).get("/api/patient-routes/"))
        self.assertEqual(len(queue), 1)
        row = queue[0]
        self.assertEqual(
            (row["purpose"], row["patient_name"], row["patient_number"], row["assigned_to_name"],
             row["routed_by_name"], row["visit_reason"], row["status_label"]),
            ("eye", self.patient.display_name, self.patient.patient_number, "John Doe",
             "Ngozi Desk", "Blurred vision for two weeks", "Queued"))
        self.assertEqual(row["patient_sex"], "Female")

    def test_a_finished_clinic_is_readable_by_asking_for_it_and_is_not_the_default(self):
        self.route(assigned_to=self.eye_doctor.id)
        PatientRoute.objects.update(status="completed")
        api = self.as_(self.eye_doctor)
        self.assertEqual(rows(api.get("/api/patient-routes/")), [])
        done = rows(api.get("/api/patient-routes/", {"status": "completed"}))
        self.assertEqual([r["status"] for r in done], ["completed"])

    def test_reception_never_moves_the_clinical_work_along(self):
        created = self.route(assigned_to=self.eye_doctor.id)
        route_id = created.data["id"]
        front_desk = self.as_(self.reception)
        for step in ("start", "complete", "record-result"):
            with self.subTest(step=step):
                self.assertEqual(
                    front_desk.post(f"/api/patient-routes/{route_id}/{step}/", {}, format="json").status_code,
                    403)
        # A status PATCHed straight onto the row is read-only on the serializer.
        front_desk.patch(f"/api/patient-routes/{route_id}/", {"status": "completed"}, format="json")
        self.assertEqual(PatientRoute.objects.get(pk=route_id).status, "queued")
        # Calling it off is reception's, because it claims no clinical work happened.
        self.assertEqual(front_desk.post(f"/api/patient-routes/{route_id}/cancel/").status_code, 200)


class OphthalmologyRoutesOnwardTests(TestCase):
    """The eye doctor sends the patient to other departments, never back to
    their own queue — through the referral mechanism every doctor uses."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec2", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="eye3", password="t", role="ophthalmologist")
        self.lab = User.objects.create_user(username="lab2", password="t", role="laboratory")
        self.radiographer = User.objects.create_user(username="rad", password="t", role="radiology")
        self.department = Department.objects.get(code="eye")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        PatientRoute.objects.create(visit=self.visit, department=self.department, purpose="eye",
                                    assigned_to=self.eye_doctor, routed_by=self.reception,
                                    status="in_progress")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def refer(self, purpose, **extra):
        return self.as_(self.eye_doctor).post(
            "/api/patient-routes/refer/", {"patient": self.patient.id, "purpose": purpose, **extra},
            format="json")

    def test_the_eye_clinic_refers_to_the_laboratory_and_to_imaging(self):
        for purpose, recipient in (("laboratory", self.lab), ("ultrasound", self.radiographer)):
            with self.subTest(purpose=purpose):
                response = self.refer(purpose, notes="Diabetic review")
                self.assertEqual(response.status_code, 201, response.data)
                self.assertTrue(Notification.objects.filter(
                    recipient=recipient, category="routing").exists())
        # The eye consultation stays open — a referral is work done during it.
        self.assertTrue(self.visit.routes.filter(purpose="eye", status="in_progress").exists())

    def test_the_eye_clinic_does_not_refer_to_itself(self):
        refused = self.refer("eye")
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertEqual(refused.data["code"], "self_referral")
        self.assertEqual(self.visit.routes.filter(purpose="eye").count(), 1)
        # The general doctor's own referrals are untouched by that guard.
        doctor = User.objects.create_user(username="gp", password="t", role="doctor")
        Visit.objects.filter(pk=self.visit.pk).update(attending_doctor=doctor)
        self.assertEqual(self.as_(doctor).post(
            "/api/patient-routes/refer/",
            {"patient": self.patient.id, "purpose": "procedure"}, format="json").status_code, 201)

    def test_the_onward_work_is_on_the_patients_own_history(self):
        self.refer("laboratory", notes="Fasting glucose")
        overview = self.as_(self.eye_doctor).get(f"/api/patients/{self.patient.uuid}/overview/")
        self.assertEqual(overview.status_code, 200, overview.data)
        purposes = {r["purpose"] for v in overview.data["visits"] for r in v["routes"]}
        self.assertEqual(purposes, {"Eye clinic", "Laboratory"})

    def test_the_eye_doctor_gains_no_money_powers_by_working_the_clinic(self):
        api = self.as_(self.eye_doctor)
        for path in ("/api/finance/report/", "/api/refunds/", "/api/adjustments/"):
            with self.subTest(path=path):
                self.assertEqual(api.get(path).status_code, 403)


class OphthalmologyAcceptanceWalkthroughTests(TestCase):
    """
    The whole errand in one test, in the order it happens at the hospital:
    reception routes and assigns → the eye doctor is told → the patient is in
    the eye clinic's queue → the consultation is documented → the laboratory
    and the pharmacy are reached through their own existing workflows → it is
    all on the patient's history → and none of it is reachable by a role that
    has no business in it.

    Every step is an existing endpoint. If this test ever needs a new one,
    something has been duplicated.
    """

    def setUp(self):
        self.reception = User.objects.create_user(username="desk", password="t", role="reception")
        self.eye_doctor = User.objects.create_user(username="drjohn2", password="t",
                                                   role="ophthalmologist",
                                                   first_name="John", last_name="Doe")
        self.lab = User.objects.create_user(username="bench", password="t", role="laboratory")
        self.pharmacist = User.objects.create_user(username="pharm", password="t", role="pharmacist")
        self.nurse = User.objects.create_user(username="ward", password="t", role="nurse")
        self.eye_department = Department.objects.get(code="eye")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_reception_to_ophthalmology_to_the_lab_and_the_pharmacy(self):
        from apps.inventory.testing import product, stock_the_pharmacy
        from apps.pharmacy.models import Prescription

        front_desk = self.as_(self.reception)

        # 1 — the desk opens the visit and routes it, naming the eye doctor.
        visit = front_desk.post("/api/visits/", {
            "patient": self.patient.id, "visit_type": "opd",
            "reason": "Blurred vision"}, format="json")
        self.assertEqual(visit.status_code, 201, visit.data)
        routed = front_desk.post("/api/patient-routes/", {
            "visit": visit.data["id"], "department": self.eye_department.id, "purpose": "eye",
            "assigned_to": self.eye_doctor.id, "notes": "Ophthalmology consultation"}, format="json")
        self.assertEqual(routed.status_code, 201, routed.data)
        route_id = routed.data["id"]

        # 2 — the notification the eye doctor actually receives.
        note = Notification.objects.get(recipient=self.eye_doctor)
        self.assertIn(self.patient.display_name, note.title)
        self.assertIn(self.patient.patient_number, note.message)
        self.assertEqual(note.action_url, f"/patients/{self.patient.uuid}")

        # 3 — and the queue they work from.
        eye = self.as_(self.eye_doctor)
        queue = rows(eye.get("/api/patient-routes/"))
        self.assertEqual([r["id"] for r in queue], [route_id])
        self.assertEqual(queue[0]["patient_number"], self.patient.patient_number)

        started = eye.post(f"/api/patient-routes/{route_id}/start/")
        self.assertEqual(started.status_code, 200, started.data)

        # 4 — the consultation, with most of the examination left blank.
        consultation = eye.post("/api/notes/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "reason_for_visit": "Blurred vision", "diagnosis": "Immature cataract, both eyes",
            "plan": "FBS, then list for surgery. Chloramphenicol drops.",
            "eye_examination": {"complaints": ["blurred_vision"], "distance_right": "6/24",
                                "iop_right": "16", "lens_left": "NS 3+"},
        }, format="json")
        self.assertEqual(consultation.status_code, 201, consultation.data)

        # 5 — the laboratory, through the laboratory's own workflow.
        referral = eye.post("/api/patient-routes/refer/", {
            "patient": self.patient.id, "purpose": "laboratory",
            "notes": "Fasting blood sugar before surgery"}, format="json")
        self.assertEqual(referral.status_code, 201, referral.data)
        self.assertTrue(Notification.objects.filter(recipient=self.lab, category="routing").exists())
        order = eye.post("/api/lab-orders/for-route/", {"route": referral.data["id"]}, format="json")
        self.assertEqual(order.status_code, 200, order.data)
        test = LabTest.objects.filter(is_active=True, price__gt=0).first()
        self.assertEqual(eye.post(f"/api/lab-orders/{order.data['id']}/add-tests/",
                                  {"tests": [test.id]}, format="json").status_code, 200)
        # Ordering raised the charge the way it always has (rule 24).
        self.assertEqual(Charge.objects.get(source_type="lab_test").patient, self.patient)

        # 6 — the pharmacy, through the existing prescription workflow.
        drops = product("Chloramphenicol eye drops")
        stock_the_pharmacy(item=drops, quantity=50, actor=self.pharmacist)
        script = eye.post("/api/prescriptions/bulk/", {
            "patient": self.patient.id,
            "lines": [{"item": drops.id, "quantity": 1, "dosage_instructions": "1 drop 6 hourly"}],
        }, format="json")
        self.assertEqual(script.status_code, 201, script.data)
        self.assertEqual(Prescription.objects.get(patient=self.patient).status, "pending")
        self.assertTrue(Notification.objects.filter(recipient=self.pharmacist).exists())

        # 7 — the whole errand reads back off the patient's own history.
        overview = eye.get(f"/api/patients/{self.patient.uuid}/overview/").data
        self.assertEqual({r["purpose"] for v in overview["visits"] for r in v["routes"]},
                         {"Eye clinic", "Laboratory"})
        self.assertTrue(all(r["created_at"] for v in overview["visits"] for r in v["routes"]))
        self.assertEqual(overview["consultation_notes"][0]["diagnosis"], "Immature cataract, both eyes")
        self.assertEqual(overview["prescriptions"][0]["item"], "Chloramphenicol eye drops")
        self.assertTrue(AuditLog.objects.filter(action="patient.routed").exists())
        self.assertTrue(AuditLog.objects.filter(action="patient.referred").exists())

        # 8 — and nobody else can do any of it.
        desk_attempts = (
            ("post", "/api/notes/", {"patient": self.patient.id,
                                     "visit_time": timezone.now().isoformat(),
                                     "reason_for_visit": "Edit"}),
            ("get", f"/api/patients/{self.patient.uuid}/overview/", None),
            ("post", f"/api/patient-routes/{route_id}/complete/", {}),
        )
        for method, path, body in desk_attempts:
            with self.subTest(who="reception", path=path):
                call = getattr(front_desk, method)
                response = call(path, body, format="json") if body is not None else call(path)
                self.assertEqual(response.status_code, 403, f"{path}: {response.data}")
        # A nurse is not an eye clinician: no note, no eye examination.
        self.assertEqual(self.as_(self.nurse).post("/api/notes/", {
            "patient": self.patient.id, "visit_time": timezone.now().isoformat(),
            "reason_for_visit": "x"}, format="json").status_code, 403)
        # And the eye doctor's own consultation note is locked to them.
        self.assertEqual(
            self.as_(self.lab).get(f"/api/notes/{consultation.data['id']}/").status_code, 403)
