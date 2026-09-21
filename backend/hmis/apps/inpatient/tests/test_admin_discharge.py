"""
The Admin Discharge workspace.

A second **door** onto the ward's discharge, never a second system: every write
here runs `inpatient/services.discharge_patient`, onto the same
`DischargeSummary` row the ward writes, with the same audit action. So the
things worth asserting are the ones that could drift — who may use this door,
that a discharge actually persists, that one admission cannot be discharged
twice, that the letter renders from the record rather than from anything
invented, and that the ward's own discharge is exactly as it was.

That last one matters most and is at the foot of this file: `ward_manager`,
`doctor`, `nurse` and the eye doctor discharge from the bed board as before.
This feature took nothing away from them.
"""
from contextlib import contextmanager
from unittest import mock

from django.test import TestCase, override_settings
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core import email as email_service
from apps.core.models import AuditLog
from apps.inpatient.models import Admission, Bed, DischargeSummary, Ward
from apps.inpatient.services import AlreadyDischarged, discharge_patient
from apps.patients.models import Patient

CONFIGURED = dict(
    RESEND_API_KEY="re_test_key",
    HMIS_EMAIL_FROM="hmis@example-hospital.test",
    HMIS_ADMIN_EMAIL="administrator@example-hospital.test",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "discharge-tests"}},
)

FORM = {
    "diagnosis": "Community-acquired pneumonia, resolved",
    "summary": "Admitted with fever and cough. Treated with IV antibiotics for four days.",
    "instructions": "Complete the oral course. Return if fever returns.",
    "condition": "Recovered",
}


@override_settings(**CONFIGURED)
class Ward5(TestCase):
    """One ward, one bed, one admitted patient."""

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.super_admin = User.objects.create_user(username="root", password="t", role="admin",
                                                    first_name="Ngozi", last_name="Admin")
        self.hospital_admin = User.objects.create_user(username="hadmin", password="t",
                                                       role="hospital_admin")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Okoro", sex="F",
                                              created_by=self.reception)
        self.ward = Ward.objects.create(name="Maternity")
        self.bed = Bed.objects.create(ward=self.ward, number="M1")
        self.admission = Admission.objects.create(
            patient=self.patient, bed=self.bed, admitted_by=self.nurse,
            attending_doctor=self.doctor, diagnosis="Fever for investigation")

    def tearDown(self):
        cache.clear()

    @contextmanager
    def sending(self, **kwargs):
        """`dispatch_admin_email` queues on commit, which a TestCase never
        reaches — without this the email assertions would pass vacuously."""
        with mock.patch.object(email_service, "send_via_resend", **kwargs) as sent:
            with self.captureOnCommitCallbacks(execute=True):
                yield sent

    def as_(self, user):
        api = APIClient()
        api.force_authenticate(user)
        return api

    def discharge(self, user=None, **overrides):
        return self.as_(user or self.super_admin).post(
            "/api/admin-discharges/discharge/",
            {"admission": self.admission.pk, **FORM, **overrides}, format="json")


class WhoMayUseThisDoor(Ward5):
    def test_the_super_admin_discharges(self):
        with self.sending():
            response = self.discharge()
        self.assertEqual(response.status_code, 201)

    def test_the_ordinary_hospital_admin_discharges_too(self):
        """
        `IsAdmin`, not `IsSuperAdmin`: **both** administrators work this door.

        It was Super Admin only, the boundary rule 37 draws on permanently
        deleting a patient. That was widened deliberately — a discharge is a
        record written once, keeping its reference, its audit row and its
        letter, so running the wards is ordinary hospital administration
        rather than an irreversible act.
        """
        with self.sending():
            response = self.discharge(self.hospital_admin)
        self.assertEqual(response.status_code, 201, response.data)
        record = DischargeSummary.objects.get()
        self.assertEqual(record.completed_by, self.hospital_admin)

    def test_the_hospital_admin_reads_the_register_and_the_ward(self):
        api = self.as_(self.hospital_admin)
        self.assertEqual(api.get("/api/admin-discharges/").status_code, 200)
        self.assertEqual(api.get("/api/admin-discharges/dischargeable/").status_code, 200)

    def test_the_hospital_admin_prints_the_letter(self):
        with self.sending():
            self.discharge()
        record = DischargeSummary.objects.get()
        response = self.as_(self.hospital_admin).get(
            f"/api/admin-discharges/{record.pk}/letter/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["reference"], record.reference)

    def test_widening_it_to_the_administrators_let_no_clinical_role_in(self):
        """The gate moved from one administrator to two, and not one step
        further: `IsAdmin` is those two and nobody else."""
        for user in (self.nurse, self.doctor, self.reception, self.cashier):
            with self.subTest(role=user.role):
                self.assertEqual(self.discharge(user).status_code, 403)
        self.assertFalse(DischargeSummary.objects.exists())

    def test_every_other_role_is_refused_on_every_route(self):
        routes = [
            ("get", "/api/admin-discharges/"),
            ("get", "/api/admin-discharges/dischargeable/"),
            ("post", "/api/admin-discharges/discharge/"),
        ]
        for user in (self.nurse, self.doctor, self.reception, self.cashier):
            for method, url in routes:
                with self.subTest(role=user.role, url=url):
                    api = self.as_(user)
                    response = getattr(api, method)(url, {}, format="json")
                    self.assertEqual(response.status_code, 403)

    def test_a_manipulated_payload_does_not_get_a_non_admin_through(self):
        """Backend authorisation is authoritative: nothing in the body helps."""
        response = self.as_(self.nurse).post(
            "/api/admin-discharges/discharge/",
            {"admission": self.admission.pk, **FORM,
             "completed_by": self.super_admin.pk, "role": "admin", "is_admin": True},
            format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DischargeSummary.objects.exists())

    def test_an_anonymous_caller_reaches_nothing(self):
        response = APIClient().post("/api/admin-discharges/discharge/", {}, format="json")
        self.assertIn(response.status_code, (401, 403))


class TheDischargeItself(Ward5):
    def test_it_persists_the_record_and_releases_the_bed(self):
        with self.sending():
            response = self.discharge()

        record = DischargeSummary.objects.get()
        self.admission.refresh_from_db()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(record.diagnosis, FORM["diagnosis"])
        self.assertEqual(record.summary, FORM["summary"])
        self.assertEqual(record.condition, "Recovered")
        self.assertEqual(record.completed_by, self.super_admin)
        self.assertEqual(self.admission.status, "discharged")
        self.assertIsNotNone(self.admission.discharged_at)
        # The bed is free for the next patient.
        self.assertFalse(self.bed.admissions.filter(status="admitted").exists())

    def test_it_carries_a_discharge_reference(self):
        with self.sending():
            response = self.discharge()
        record = DischargeSummary.objects.get()
        self.assertEqual(record.reference, f"DCH-{record.pk:06d}")
        self.assertEqual(response.data["reference"], record.reference)

    def test_a_diagnosis_and_a_summary_are_required(self):
        for missing in ("diagnosis", "summary"):
            with self.subTest(field=missing):
                response = self.discharge(**{missing: "   "})
                self.assertEqual(response.status_code, 400)
                self.assertFalse(DischargeSummary.objects.exists())

    def test_the_discharge_is_audited(self):
        with self.sending():
            self.discharge()

        entry = AuditLog.objects.get(action="admission.discharged")
        record = DischargeSummary.objects.get()
        self.assertEqual(entry.actor, self.super_admin)
        self.assertEqual(entry.object_id, record.pk)
        self.assertEqual(entry.details["patient_number"], self.patient.patient_number)
        self.assertEqual(entry.details["discharge_reference"], record.reference)
        self.assertEqual(entry.details["ward"], "Maternity")
        self.assertIsNotNone(entry.details["discharged_at"])

    def test_it_emails_the_administrator_after_the_discharge_lands(self):
        with self.sending() as sent:
            self.discharge()

        sent.assert_called_once()
        call = sent.call_args.kwargs
        self.assertEqual(call["subject"], "Patient Discharged — NMHS")
        self.assertEqual(call["to"], "administrator@example-hospital.test")
        html = call["html"]
        record = DischargeSummary.objects.get()
        self.assertIn(record.reference, html)
        self.assertIn(self.patient.patient_number, html)
        self.assertIn("Ngozi Admin", html)            # who discharged them
        # Administrative only: the clinical detail stays on the letter.
        self.assertNotIn(FORM["summary"], html)
        self.assertNotIn(FORM["diagnosis"], html)

    def test_an_email_failure_leaves_the_patient_discharged(self):
        import httpx

        with self.sending(side_effect=httpx.ReadTimeout("resend timed out")):
            response = self.discharge()

        self.admission.refresh_from_db()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.admission.status, "discharged")
        self.assertEqual(DischargeSummary.objects.count(), 1)


class NobodyIsDischargedTwice(Ward5):
    def test_a_second_discharge_is_refused_and_shows_the_first(self):
        with self.sending():
            self.discharge()

        again = self.discharge()
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.data["code"], "already_discharged")
        # The existing record comes back, so the screen can offer its letter.
        self.assertEqual(again.data["discharge"]["reference"],
                         DischargeSummary.objects.get().reference)
        self.assertEqual(DischargeSummary.objects.count(), 1)

    def test_reopening_a_discharge_sends_no_second_email(self):
        with self.sending() as first:
            self.discharge()
        self.assertEqual(first.call_count, 1)

        with self.sending() as second:
            self.discharge()          # refused
            self.as_(self.super_admin).get(f"/api/admin-discharges/{DischargeSummary.objects.get().pk}/")
            self.as_(self.super_admin).get(f"/api/admin-discharges/{DischargeSummary.objects.get().pk}/letter/")
        second.assert_not_called()

    def test_the_service_raises_rather_than_writing_a_duplicate(self):
        with self.captureOnCommitCallbacks(execute=True):
            discharge_patient(admission=self.admission, actor=self.super_admin,
                              diagnosis="d", summary="s", notify=False)
        with self.assertRaises(AlreadyDischarged):
            discharge_patient(admission=self.admission, actor=self.super_admin,
                              diagnosis="d", summary="s", notify=False)
        self.assertEqual(DischargeSummary.objects.count(), 1)

    def test_a_patient_may_be_admitted_and_discharged_again_later(self):
        """Rule 9: multiple admissions over time are separate records."""
        with self.sending():
            self.discharge()

        second_admission = Admission.objects.create(
            patient=self.patient, bed=self.bed, admitted_by=self.nurse)
        with self.sending():
            response = self.discharge(admission=second_admission.pk)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(DischargeSummary.objects.count(), 2)


class TheRegisterAndTheLetter(Ward5):
    def setUp(self):
        super().setUp()
        with self.sending():
            self.discharge()
        self.record = DischargeSummary.objects.get()
        self.admission.refresh_from_db()

    def test_the_discharged_patient_appears_in_the_list(self):
        response = self.as_(self.super_admin).get("/api/admin-discharges/")
        rows = response.data["results"] if "results" in response.data else response.data

        self.assertEqual(response.status_code, 200)
        row = next(r for r in rows if r["reference"] == self.record.reference)
        self.assertEqual(row["patient_name"], self.patient.display_name)
        self.assertEqual(row["patient_number"], self.patient.patient_number)
        self.assertEqual(row["admission_reference"], f"ADM-{self.admission.pk:06d}")
        self.assertEqual(row["ward_name"], "Maternity")
        self.assertEqual(row["completed_by_name"], "Ngozi Admin")
        self.assertEqual(row["admission_status"], "discharged")
        self.assertIsNotNone(row["discharged_at"])
        self.assertIsNotNone(row["admitted_at"])

    def test_the_list_can_be_searched_and_date_filtered(self):
        api = self.as_(self.super_admin)

        found = api.get("/api/admin-discharges/", {"search": self.patient.patient_number})
        self.assertEqual(len(found.data["results"]), 1)

        missing = api.get("/api/admin-discharges/", {"search": "nobody-by-that-name"})
        self.assertEqual(len(missing.data["results"]), 0)

        today = self.admission.discharged_at.date().isoformat()
        in_range = api.get("/api/admin-discharges/", {"discharged_from": today,
                                                      "discharged_to": today})
        self.assertEqual(len(in_range.data["results"]), 1)

        out_of_range = api.get("/api/admin-discharges/", {"discharged_from": "2099-01-01"})
        self.assertEqual(len(out_of_range.data["results"]), 0)

    def test_a_date_filter_never_hides_the_record_from_its_own_detail_route(self):
        """Rule 21: a browsing default belongs to `list`, never `get_object`."""
        api = self.as_(self.super_admin)
        self.assertEqual(api.get(f"/api/admin-discharges/{self.record.pk}/").status_code, 200)
        self.assertEqual(api.get(f"/api/admin-discharges/{self.record.pk}/letter/").status_code, 200)

    def test_the_letter_renders_from_the_record(self):
        response = self.as_(self.super_admin).get(
            f"/api/admin-discharges/{self.record.pk}/letter/")
        letter = response.data

        self.assertEqual(response.status_code, 200)
        self.assertEqual(letter["reference"], self.record.reference)
        self.assertEqual(letter["patient"]["patient_number"], self.patient.patient_number)
        self.assertEqual(letter["patient"]["uuid"], str(self.patient.uuid))
        self.assertEqual(letter["admission"]["ward"], "Maternity")
        self.assertEqual(letter["admission"]["bed"], "M1")
        self.assertEqual(letter["admission"]["attending_doctor"], self.doctor.username)
        self.assertEqual(letter["admission"]["admission_diagnosis"], "Fever for investigation")
        self.assertEqual(letter["discharge"]["diagnosis"], FORM["diagnosis"])
        self.assertEqual(letter["discharge"]["summary"], FORM["summary"])
        self.assertEqual(letter["discharge"]["instructions"], FORM["instructions"])
        self.assertEqual(letter["discharge"]["condition"], "Recovered")
        self.assertEqual(letter["discharge"]["completed_by"], "Ngozi Admin")
        self.assertIsNotNone(letter["discharge"]["completed_at"])

    def test_the_letter_is_refused_to_everybody_else(self):
        """The two administrators print it; a clinical role does not. The
        letter carries the whole discharge, so it follows the workspace's own
        gate rather than the ward's."""
        for user in (self.nurse, self.doctor, self.cashier, self.reception):
            with self.subTest(role=user.role):
                response = self.as_(user).get(
                    f"/api/admin-discharges/{self.record.pk}/letter/")
                self.assertEqual(response.status_code, 403)

    def test_the_dischargeable_list_no_longer_offers_this_patient(self):
        response = self.as_(self.super_admin).get("/api/admin-discharges/dischargeable/")
        rows = response.data["results"] if "results" in response.data else response.data
        self.assertNotIn(self.admission.pk, [r["id"] for r in rows])


@override_settings(**CONFIGURED)
class TheAdmissionIsReadableBeforeItIsDischarged(Ward5):
    """
    Steps 1–5 of the workspace: see who is admitted, narrow the list, and read
    the admission before acting on it.

    Everything asserted here is **derived** — the reference off the primary
    key, the stay off the two timestamps — so there is no column to backfill
    and no second place either could be computed differently. The bed history
    is the existing `BedTransfer` rows read through the existing endpoint.
    """

    def on_ward(self, user=None, **params):
        response = self.as_(user or self.super_admin).get(
            "/api/admin-discharges/dischargeable/", params)
        self.assertEqual(response.status_code, 200, response.data)
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_the_admitted_patient_is_listed_with_what_the_desk_needs(self):
        row = next(r for r in self.on_ward() if r["id"] == self.admission.pk)
        self.assertEqual(row["patient_name"], self.patient.display_name)
        self.assertEqual(row["patient_number"], self.patient.patient_number)
        self.assertEqual(row["reference"], f"ADM-{self.admission.pk:06d}")
        self.assertEqual(row["ward_name"], "Maternity")
        self.assertEqual(row["bed_number"], "M1")
        self.assertEqual(row["status"], "admitted")
        self.assertEqual(row["status_label"], "Admitted")
        self.assertEqual(row["attending_doctor_name"], self.doctor.username)
        self.assertEqual(row["length_of_stay"], "Same day")
        self.assertEqual(row["length_of_stay_days"], 0)
        # Nothing has closed it yet, so there is no letter to offer.
        self.assertEqual(row["discharge_reference"], "")
        self.assertIsNone(row["discharge_id"])

    def test_the_stay_stops_counting_on_the_day_the_patient_went_home(self):
        from datetime import timedelta

        from django.utils import timezone

        Admission.objects.filter(pk=self.admission.pk).update(
            admitted_at=timezone.now() - timedelta(days=10))
        with self.sending():
            self.discharge()
        self.admission.refresh_from_db()

        record = DischargeSummary.objects.get()
        detail = self.as_(self.super_admin).get(f"/api/admissions/{self.admission.pk}/").data
        self.assertEqual(detail["length_of_stay"], "10 days")
        self.assertEqual(detail["discharge_reference"], record.reference)
        self.assertEqual(detail["discharge_id"], record.pk)

        # And it does not keep ageing afterwards.
        Admission.objects.filter(pk=self.admission.pk).update(
            discharged_at=self.admission.admitted_at + timedelta(days=3))
        again = self.as_(self.super_admin).get(f"/api/admissions/{self.admission.pk}/").data
        self.assertEqual(again["length_of_stay"], "3 days")

    def test_the_list_is_searched_by_name_number_reference_and_ward(self):
        other_ward = Ward.objects.create(name="Male Medical")
        other_bed = Bed.objects.create(ward=other_ward, number="MM4")
        other_patient = Patient.objects.create(first_name="Bola", last_name="Eze", sex="M",
                                               created_by=self.reception)
        other = Admission.objects.create(patient=other_patient, bed=other_bed,
                                         admitted_by=self.nurse)

        def ids(**params):
            return {row["id"] for row in self.on_ward(**params)}

        self.assertEqual(ids(), {self.admission.pk, other.pk})
        self.assertEqual(ids(search="Okoro"), {self.admission.pk})
        self.assertEqual(ids(search=self.patient.patient_number), {self.admission.pk})
        self.assertEqual(ids(search="Male Medical"), {other.pk})
        # The reference, however it is typed — it is derived from the primary
        # key, so it is matched by reading the number back out.
        self.assertEqual(ids(search=f"ADM-{other.pk:06d}"), {other.pk})
        self.assertEqual(ids(search=str(other.pk)), {other.pk})
        self.assertEqual(ids(search="nobody-at-all"), set())

    def test_the_list_is_filtered_by_ward(self):
        other_ward = Ward.objects.create(name="Male Medical")
        other_bed = Bed.objects.create(ward=other_ward, number="MM4")
        other_patient = Patient.objects.create(first_name="Bola", last_name="Eze", sex="M",
                                               created_by=self.reception)
        other = Admission.objects.create(patient=other_patient, bed=other_bed,
                                         admitted_by=self.nurse)

        self.assertEqual({r["id"] for r in self.on_ward(ward=self.ward.pk)}, {self.admission.pk})
        self.assertEqual({r["id"] for r in self.on_ward(ward=other_ward.pk)}, {other.pk})

    def test_the_bed_history_is_the_existing_transfer_rows(self):
        """No new model and no new endpoint: the moves the ward already records,
        read one admission at a time."""
        second = Bed.objects.create(ward=self.ward, number="M2")
        moved = self.as_(self.nurse).post("/api/bed-transfers/", {
            "admission": self.admission.pk, "to_bed": second.pk, "reason": "Closer to the station",
        }, format="json")
        self.assertEqual(moved.status_code, 201, moved.data)

        response = self.as_(self.super_admin).get("/api/bed-transfers/",
                                                  {"admission": self.admission.pk})
        self.assertEqual(response.status_code, 200)
        data = response.data
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["from_bed_number"], "M1")
        self.assertEqual(rows[0]["to_bed_number"], "M2")
        self.assertEqual(rows[0]["from_ward_name"], "Maternity")
        self.assertEqual(rows[0]["to_ward_name"], "Maternity")
        self.assertEqual(rows[0]["transferred_by_name"], self.nurse.username)

    def test_the_transfer_filter_narrows_to_one_admission(self):
        other_bed = Bed.objects.create(ward=self.ward, number="M9")
        other_patient = Patient.objects.create(first_name="Bola", last_name="Eze", sex="M",
                                               created_by=self.reception)
        other = Admission.objects.create(patient=other_patient, bed=other_bed,
                                         admitted_by=self.nurse)
        spare = Bed.objects.create(ward=self.ward, number="M10")
        self.as_(self.nurse).post("/api/bed-transfers/", {
            "admission": other.pk, "to_bed": spare.pk}, format="json")

        response = self.as_(self.super_admin).get("/api/bed-transfers/",
                                                  {"admission": self.admission.pk})
        data = response.data
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        self.assertEqual(rows, [])

    def test_a_non_admin_still_cannot_read_the_ward_list(self):
        """The new filters widen nothing: the cashier is refused exactly as
        before, and the eye doctor is still narrowed to their own patients."""
        for user in (self.cashier, self.reception):
            with self.subTest(role=user.role):
                self.assertEqual(
                    self.as_(user).get("/api/bed-transfers/").status_code, 403)
                self.assertEqual(
                    self.as_(user).get("/api/admin-discharges/dischargeable/").status_code, 403)


@override_settings(**CONFIGURED)
class TheWardKeepsItsOwnDischarge(Ward5):
    """
    The regression that matters most: this feature took nothing away.

    `/api/discharges/` is unchanged — same roles, same behaviour — and is held
    by `test_ward_flow.py` and `test_eye_ward_scope.py` as well. Repeated here
    because it is the specific thing the Admin workspace could have broken.
    """

    def test_a_nurse_still_discharges_from_the_bed_board(self):
        with self.sending():
            response = self.as_(self.nurse).post("/api/discharges/", {
                "admission": self.admission.pk,
                "diagnosis": "Resolved", "summary": "Uneventful stay.",
            }, format="json")

        self.admission.refresh_from_db()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.admission.status, "discharged")

    def test_the_ward_route_goes_through_the_same_service_and_is_audited(self):
        with self.sending() as sent:
            self.as_(self.nurse).post("/api/discharges/", {
                "admission": self.admission.pk,
                "diagnosis": "Resolved", "summary": "Uneventful stay.",
            }, format="json")

        record = DischargeSummary.objects.get()
        self.assertTrue(record.reference.startswith("DCH-"))
        self.assertEqual(AuditLog.objects.filter(action="admission.discharged").count(), 1)
        # One discharge, one notification, whichever door it came through.
        sent.assert_called_once()

    def test_the_ward_cannot_discharge_twice_either(self):
        with self.sending():
            first = self.as_(self.nurse).post("/api/discharges/", {
                "admission": self.admission.pk, "diagnosis": "d", "summary": "s",
            }, format="json")
        self.assertEqual(first.status_code, 201)

        second = self.as_(self.nurse).post("/api/discharges/", {
            "admission": self.admission.pk, "diagnosis": "d", "summary": "s",
        }, format="json")
        # Used to be a 500 out of the OneToOneField's IntegrityError.
        self.assertEqual(second.status_code, 400)
        self.assertEqual(DischargeSummary.objects.count(), 1)

    def test_the_ward_does_not_reach_the_admin_workspace(self):
        self.assertEqual(
            self.as_(self.nurse).get("/api/admin-discharges/").status_code, 403)
