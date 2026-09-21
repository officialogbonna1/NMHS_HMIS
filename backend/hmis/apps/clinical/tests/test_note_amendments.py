"""
A consultation note as a longitudinal medical record.

Two rules, and confusing them is the failure this file exists to prevent:

- **A visit is a note.** The patient returns, a *new* `ConsultationNote` is
  written, and nothing recorded before is touched.
- **A correction is an amendment.** The note's own author (or an admin)
  corrects *that* note, and what it said before is archived in
  `ConsultationNoteAmendment` — the trail that already existed — with who
  changed it and why.

The note still locks on save (`LockedRecordMixin`); an amendment is the only
way past that, and it always leaves a row behind. `Vitals` and `NursingNote`
are untouched and stay absolutely locked.
"""
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import (ConsultationNote, ConsultationNoteAmendment,
                                  NursingNote, Vitals)
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class NoteAmendmentTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.smith = User.objects.create_user(username="smith", password="t", role="doctor",
                                              first_name="John", last_name="Smith")
        self.jane = User.objects.create_user(username="jane", password="t", role="doctor",
                                             first_name="Jane", last_name="Doe")
        self.eye_doctor = User.objects.create_user(username="eye", password="t",
                                                   role="ophthalmologist",
                                                   first_name="Ada", last_name="Eze")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.nurse = User.objects.create_user(username="nurse", password="t", role="nurse")

        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.smith)
        # Jane covers the same patient, so she can *read* the chart — which is
        # what makes "she may read the note but never amend it" a real test
        # rather than a 404 from the queryset scoping.
        PatientRoute.objects.create(
            visit=self.visit, department=Department.objects.get(code="consultation"),
            purpose="consultation", assigned_to=self.jane, routed_by=self.reception)

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def write(self, user=None, **fields):
        body = {"patient": self.patient.id, "visit_time": timezone.now().isoformat(),
                "reason_for_visit": "Blurred vision", **fields}
        return self.as_(user or self.smith).post("/api/notes/", body, format="json")

    def amend(self, note_id, user=None, reason="correction", **fields):
        body = {**fields}
        if reason is not None:
            body["amendment_reason"] = reason
        return self.as_(user or self.smith).patch(f"/api/notes/{note_id}/", body, format="json")

    # --- creation ---------------------------------------------------------------

    def test_the_author_and_the_time_are_recorded_on_the_note(self):
        response = self.write(diagnosis="Refractive error", plan="Refraction and follow-up.")
        self.assertEqual(response.status_code, 201, response.data)
        note = ConsultationNote.objects.get(pk=response.data["id"])
        self.assertEqual(note.doctor, self.smith)
        self.assertIsNotNone(note.created_at)
        self.assertTrue(note.is_locked)
        self.assertEqual(
            (response.data["doctor_name"], response.data["doctor_staff_number"],
             response.data["is_amended"], response.data["can_amend"]),
            ("John Smith", self.smith.staff_number, False, True))

    def test_creating_a_note_writes_an_audit_row(self):
        self.write()
        entry = AuditLog.objects.get(action="note.created")
        self.assertEqual(entry.actor, self.smith)
        self.assertEqual(entry.details["patient_number"], self.patient.patient_number)

    # --- who may amend ----------------------------------------------------------

    def test_the_author_amends_their_own_note(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        response = self.amend(note_id, diagnosis="Myopia with astigmatism",
                              amendment_detail="Entered incorrectly.")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).diagnosis,
                         "Myopia with astigmatism")

    def test_another_doctor_may_read_the_note_and_may_never_amend_it(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        reading = self.as_(self.jane).get(f"/api/notes/{note_id}/")
        self.assertEqual(reading.status_code, 200)
        self.assertFalse(reading.data["can_amend"])

        refused = self.amend(note_id, user=self.jane, diagnosis="Hijacked")
        self.assertEqual(refused.status_code, 403, refused.data)
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).diagnosis, "Refractive error")
        self.assertFalse(ConsultationNoteAmendment.objects.exists())

    def test_an_admin_still_amends_anybody_s_note(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        response = self.amend(note_id, user=self.admin, diagnosis="Corrected by records")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(ConsultationNoteAmendment.objects.get().amended_by, self.admin)

    def test_the_eye_doctor_amends_their_own_eye_note_by_the_same_mechanism(self):
        PatientRoute.objects.create(
            visit=self.visit, department=Department.objects.get(code="eye"),
            purpose="eye", assigned_to=self.eye_doctor, routed_by=self.reception)
        note_id = self.write(user=self.eye_doctor,
                             eye_examination={"iop_right": 16, "distance_right": "6/12"}).data["id"]
        response = self.amend(note_id, user=self.eye_doctor,
                              eye_examination={"iop_right": 16, "distance_right": "6/9"})
        self.assertEqual(response.status_code, 200, response.data)
        # One amendment model, not an ophthalmology one.
        amendment = ConsultationNoteAmendment.objects.get()
        self.assertEqual(amendment.previous_eye_examination["distance_right"], "6/12")
        self.assertEqual(ConsultationNote.objects.get(pk=note_id)
                         .eye_examination["distance_right"], "6/9")

    # --- the reason -------------------------------------------------------------

    def test_an_amendment_without_a_reason_is_refused_and_writes_nothing(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        refused = self.amend(note_id, reason=None, diagnosis="Myopia")
        self.assertEqual(refused.status_code, 400, refused.data)
        self.assertEqual(refused.data["code"], "amendment_reason_required")
        # The form is told what it may choose from rather than guessing.
        self.assertTrue(refused.data["choices"])
        self.assertEqual(ConsultationNote.objects.get(pk=note_id).diagnosis, "Refractive error")
        self.assertFalse(ConsultationNoteAmendment.objects.exists())

    def test_a_reason_off_the_list_is_refused(self):
        note_id = self.write().data["id"]
        refused = self.amend(note_id, reason="because", diagnosis="X")
        self.assertEqual((refused.status_code, refused.data["code"]),
                         (400, "amendment_reason_required"))

    # --- the trail --------------------------------------------------------------

    def test_the_previous_version_is_archived_with_who_changed_it_and_why(self):
        note_id = self.write(reason_for_visit="Blurred vision", chief_complaint="Two weeks",
                             diagnosis="Refractive error", plan="Refraction").data["id"]
        self.amend(note_id, diagnosis="Myopia with astigmatism",
                   amendment_detail="The diagnosis was entered incorrectly.")

        amendment = ConsultationNoteAmendment.objects.get()
        self.assertEqual(
            (amendment.amended_by, amendment.reason, amendment.detail),
            (self.smith, "correction", "The diagnosis was entered incorrectly."))
        # Every field the note carries is in the snapshot, including the two
        # that used not to be.
        self.assertEqual(amendment.previous_diagnosis, "Refractive error")
        self.assertEqual(amendment.previous_reason_for_visit, "Blurred vision")
        self.assertEqual(amendment.previous_chief_complaint, "Two weeks")
        self.assertEqual(amendment.previous_plan, "Refraction")

    def test_the_note_reports_that_it_was_amended_and_by_whom(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        self.amend(note_id, diagnosis="Myopia")
        data = self.as_(self.smith).get(f"/api/notes/{note_id}/").data
        self.assertTrue(data["is_amended"])
        self.assertEqual(data["last_amended_by_name"], "John Smith")
        self.assertEqual(data["amendment_count"], 1)
        self.assertIsNotNone(data["last_amended_at"])
        # The author never moves: the record still says who documented it.
        self.assertEqual(data["doctor_name"], "John Smith")

    def test_the_history_says_which_field_changed_from_what_to_what(self):
        note_id = self.write(diagnosis="Refractive error").data["id"]
        self.amend(note_id, diagnosis="Myopia with astigmatism")
        amendment = self.as_(self.smith).get(f"/api/notes/{note_id}/").data["amendments"][0]
        self.assertEqual(amendment["changes"], [{
            "field": "diagnosis", "label": "Diagnosis",
            "previous": "Refractive error", "current": "Myopia with astigmatism"}])
        self.assertEqual(amendment["reason_label"], "Correction of error")

    def test_several_amendments_chain_and_each_says_what_it_alone_changed(self):
        note_id = self.write(diagnosis="Refractive error", plan="Refraction").data["id"]
        self.amend(note_id, diagnosis="Myopia", reason="correction")
        self.amend(note_id, plan="Refer to optometry.", reason="clarification")

        data = self.as_(self.smith).get(f"/api/notes/{note_id}/").data
        self.assertEqual(data["amendment_count"], 2)
        newest, oldest = data["amendments"]
        # Newest first. Each diff is scoped to its own correction, because the
        # "after" side is read from the next amendment, not from the note.
        self.assertEqual([(c["label"], c["previous"], c["current"]) for c in newest["changes"]],
                         [("Plan", "Refraction", "Refer to optometry.")])
        self.assertEqual([(c["label"], c["previous"], c["current"]) for c in oldest["changes"]],
                         [("Diagnosis", "Refractive error", "Myopia")])

    def test_an_eye_examination_change_is_reported_finding_by_finding(self):
        PatientRoute.objects.create(
            visit=self.visit, department=Department.objects.get(code="eye"),
            purpose="eye", assigned_to=self.eye_doctor, routed_by=self.reception)
        note_id = self.write(user=self.eye_doctor,
                             eye_examination={"distance_right": "6/12"}).data["id"]
        self.amend(note_id, user=self.eye_doctor, eye_examination={"distance_right": "6/9"})
        changes = self.as_(self.eye_doctor).get(f"/api/notes/{note_id}/").data["amendments"][0]["changes"]
        self.assertEqual(changes, [{
            "field": "distance_right", "label": "Distance (unaided) — right eye",
            "previous": "6/12", "current": "6/9"}])

    def test_amending_writes_an_audit_row_naming_the_reason(self):
        note_id = self.write().data["id"]
        self.amend(note_id, diagnosis="Myopia", amendment_detail="Mistyped.")
        entry = AuditLog.objects.get(action="note.amended")
        self.assertEqual((entry.actor, entry.details["reason"], entry.details["detail"]),
                         (self.smith, "correction", "Mistyped."))

    # --- a visit is not an amendment -------------------------------------------

    def test_a_second_visit_is_a_second_note_and_leaves_the_first_alone(self):
        first = self.write(reason_for_visit="Blurred vision", diagnosis="Refractive error",
                           plan="Refraction and follow-up.").data["id"]
        second = self.write(reason_for_visit="Follow-up", diagnosis="Continue monitoring").data["id"]

        self.assertNotEqual(first, second)
        self.assertEqual(ConsultationNote.objects.filter(patient=self.patient).count(), 2)
        visit_one = ConsultationNote.objects.get(pk=first)
        self.assertEqual((visit_one.reason_for_visit, visit_one.diagnosis, visit_one.plan),
                         ("Blurred vision", "Refractive error", "Refraction and follow-up."))
        # A new visit is not a correction, so it archives nothing.
        self.assertFalse(ConsultationNoteAmendment.objects.exists())

    def test_amending_one_visit_never_touches_another(self):
        first = self.write(reason_for_visit="Visit 1", diagnosis="Refractive error").data["id"]
        second = self.write(reason_for_visit="Visit 2", diagnosis="Continue monitoring").data["id"]
        self.amend(first, diagnosis="Myopia")

        self.assertEqual(ConsultationNote.objects.get(pk=second).diagnosis, "Continue monitoring")
        self.assertEqual(ConsultationNoteAmendment.objects.filter(note_id=second).count(), 0)
        self.assertEqual(ConsultationNoteAmendment.objects.filter(note_id=first).count(), 1)

    def test_every_note_a_patient_has_is_listed_newest_first(self):
        self.write(reason_for_visit="Visit 1")
        self.write(reason_for_visit="Visit 2")
        self.write(reason_for_visit="Visit 3")
        listing = self.as_(self.smith).get("/api/notes/", {"patient": self.patient.id}).data
        rows = listing["results"] if isinstance(listing, dict) else listing
        self.assertEqual([r["reason_for_visit"] for r in rows], ["Visit 3", "Visit 2", "Visit 1"])

    # --- what must not have moved ----------------------------------------------

    def test_vitals_and_nursing_notes_are_still_absolutely_locked(self):
        """The lock was widened for consultation notes alone."""
        vitals = Vitals.objects.create(patient=self.patient, recorded_by=self.nurse,
                                       visit_time=timezone.now(), heart_rate=80)
        nursing = NursingNote.objects.create(patient=self.patient, nurse=self.nurse,
                                             observation="Comfortable.")
        for url in (f"/api/vitals/{vitals.pk}/", f"/api/nursing-notes/{nursing.pk}/"):
            with self.subTest(url=url):
                response = self.as_(self.nurse).patch(url, {"observation": "x"}, format="json")
                self.assertIn(response.status_code, (403, 405), f"{url}: {response.status_code}")
        vitals.refresh_from_db()
        nursing.refresh_from_db()
        self.assertEqual(vitals.heart_rate, 80)
        self.assertEqual(nursing.observation, "Comfortable.")

    def test_a_nurse_cannot_reach_a_consultation_note_at_all(self):
        note_id = self.write().data["id"]
        api = self.as_(self.nurse)
        self.assertEqual(api.get(f"/api/notes/{note_id}/").status_code, 403)
        self.assertEqual(self.amend(note_id, user=self.nurse, diagnosis="x").status_code, 403)
