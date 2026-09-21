"""
Django admin as the back office's door onto the clinical note.

It is the **same** note, the same amendment trail and the same audit rows as
the chart (rule 31: one set of models, two administration interfaces). So the
things that matter here are the things that could drift: that correcting a
note through the admin archives what it said, that it is refused without a
reason, that the author never moves, that a note written here is an ordinary
note afterwards — and that the records which must stay locked still are.
"""
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import (ConsultationNote, ConsultationNoteAmendment,
                                  NursingNote, Vitals)
from apps.core.models import AuditLog
from apps.patients.models import Patient
from apps.workflow.models import Visit

ADD = "admin:clinical_consultationnote_add"
LIST = "admin:clinical_consultationnote_changelist"
CHANGE = "admin:clinical_consultationnote_change"


class NoteAdminTests(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.records = User.objects.create_superuser(
            username="records", password="t", role="hospital_admin", email="r@nmhs.test",
            first_name="Ngozi", last_name="Records")
        self.smith = User.objects.create_user(username="smith", password="t", role="doctor",
                                              first_name="John", last_name="Smith")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M",
                                              created_by=self.reception)
        Visit.objects.create(patient=self.patient, opened_by=self.reception,
                             attending_doctor=self.smith)
        self.note = ConsultationNote.objects.create(
            patient=self.patient, doctor=self.smith, visit_time=timezone.now(),
            reason_for_visit="Blurred vision", chief_complaint="Two weeks",
            diagnosis="Refractive error", plan="Refraction and follow-up.")

    # --- form plumbing ----------------------------------------------------------

    def form_data(self, **overrides):
        """A complete admin POST, inline management form included."""
        when = timezone.localtime(self.note.visit_time)
        data = {
            "patient": self.patient.pk,
            "visit_time_0": when.strftime("%Y-%m-%d"),
            "visit_time_1": when.strftime("%H:%M:%S"),
            "reason_for_visit": self.note.reason_for_visit,
            "chief_complaint": self.note.chief_complaint,
            "note_text": "",
            "diagnosis": self.note.diagnosis,
            "plan": self.note.plan,
            "eye_examination": "",
            "amendment_reason": "",
            "amendment_detail": "",
            "amendments-TOTAL_FORMS": "0",
            "amendments-INITIAL_FORMS": str(self.note.amendments.count()),
            "amendments-MIN_NUM_FORMS": "0",
            "amendments-MAX_NUM_FORMS": "1000",
            "_continue": "Save and continue editing",
        }
        data.update(overrides)
        return data

    def change(self, **overrides):
        self.client.force_login(self.records)
        return self.client.post(reverse(CHANGE, args=[self.note.pk]),
                                self.form_data(**overrides), follow=True)

    # --- viewing -----------------------------------------------------------------

    def test_an_administrator_reads_the_notes_and_one_note_s_page(self):
        self.client.force_login(self.records)
        listing = self.client.get(reverse(LIST))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, self.patient.patient_number)

        page = self.client.get(reverse(CHANGE, args=[self.note.pk]))
        self.assertEqual(page.status_code, 200)
        # The author and the encounter are on the page, by hospital identifiers.
        self.assertContains(page, "John Smith")
        self.assertContains(page, "Amendment history")

    def test_the_changelist_says_whether_a_note_has_been_amended(self):
        self.client.force_login(self.records)
        # Both boolean columns ("Eye exam", "Amended") read false to begin
        # with, so no tick is on the page at all.
        before = self.client.get(reverse(LIST))
        self.assertNotContains(before, "icon-yes.svg")

        self.change(diagnosis="Myopia", amendment_reason="correction")

        after = self.client.get(reverse(LIST))
        self.assertContains(after, "icon-yes.svg")
        # And "Last amended by" names who corrected it. Counted, because the
        # administrator's own name is also in the admin's header.
        self.assertContains(after, "Ngozi Records", count=2)

    # --- adding ------------------------------------------------------------------

    def test_an_administrator_adds_a_note_and_it_is_an_ordinary_note(self):
        self.client.force_login(self.records)
        when = timezone.localtime()
        response = self.client.post(reverse(ADD), {
            **self.form_data(),
            "reason_for_visit": "Records-office entry",
            "diagnosis": "Recorded from paper notes",
            "visit_time_0": when.strftime("%Y-%m-%d"),
            "visit_time_1": when.strftime("%H:%M:%S"),
            "amendments-INITIAL_FORMS": "0",
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        note = ConsultationNote.objects.get(reason_for_visit="Records-office entry")
        # Same model, same lock, same author rule.
        self.assertEqual(note.patient, self.patient)
        self.assertEqual(note.doctor, self.records)
        self.assertTrue(note.is_locked)
        # It reads back through the ordinary clinical API, on the patient's chart.
        api = APIClient()
        api.force_authenticate(self.smith)
        rows = api.get("/api/notes/", {"patient": self.patient.pk}).data
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertIn("Records-office entry", [r["reason_for_visit"] for r in rows])

    def test_adding_writes_the_same_audit_row_the_hmis_writes(self):
        self.client.force_login(self.records)
        self.client.post(reverse(ADD), {
            **self.form_data(), "reason_for_visit": "Paper note", "amendments-INITIAL_FORMS": "0",
        }, follow=True)
        entry = AuditLog.objects.get(action="note.created")
        self.assertEqual(entry.actor, self.records)
        self.assertEqual(entry.details["source"], "django-admin")
        self.assertEqual(entry.details["patient_number"], self.patient.patient_number)

    def test_the_author_is_the_signed_in_administrator_and_cannot_be_chosen(self):
        """An administrator must not be able to file a note under a clinician's
        name — so `doctor` is not on the form at all."""
        self.client.force_login(self.records)
        form = self.client.get(reverse(ADD)).context["adminform"].form
        self.assertNotIn("doctor", form.fields)

        self.client.post(reverse(ADD), {
            **self.form_data(), "reason_for_visit": "Impersonation attempt",
            "doctor": self.smith.pk, "amendments-INITIAL_FORMS": "0",
        }, follow=True)
        note = ConsultationNote.objects.get(reason_for_visit="Impersonation attempt")
        self.assertEqual(note.doctor, self.records)

    # --- amending ----------------------------------------------------------------

    def test_correcting_a_note_archives_what_it_said_and_names_who_and_why(self):
        response = self.change(diagnosis="Myopia with astigmatism",
                               amendment_reason="correction",
                               amendment_detail="Transcribed from the paper note incorrectly.")
        self.assertEqual(response.status_code, 200)

        self.note.refresh_from_db()
        self.assertEqual(self.note.diagnosis, "Myopia with astigmatism")
        amendment = ConsultationNoteAmendment.objects.get(note=self.note)
        self.assertEqual(
            (amendment.amended_by, amendment.reason, amendment.detail,
             amendment.previous_diagnosis, amendment.previous_reason_for_visit),
            (self.records, "correction", "Transcribed from the paper note incorrectly.",
             "Refractive error", "Blurred vision"))

    def test_the_original_author_is_never_replaced_by_the_amender(self):
        self.change(diagnosis="Myopia", amendment_reason="correction")
        self.note.refresh_from_db()
        self.assertEqual(self.note.doctor, self.smith)

    def test_a_correction_with_no_reason_is_refused_and_changes_nothing(self):
        response = self.change(diagnosis="Myopia", amendment_reason="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Say why this note is being amended")

        self.note.refresh_from_db()
        self.assertEqual(self.note.diagnosis, "Refractive error")
        self.assertFalse(ConsultationNoteAmendment.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="note.amended").exists())

    def test_several_corrections_stay_in_order_and_each_keeps_its_own_before(self):
        self.change(diagnosis="Myopia", amendment_reason="correction")
        self.note.refresh_from_db()
        self.change(plan="Refer to optometry.", amendment_reason="clarification",
                    diagnosis="Myopia")

        trail = list(ConsultationNoteAmendment.objects.filter(note=self.note))  # -created_at
        self.assertEqual([a.reason for a in trail], ["clarification", "correction"])
        self.assertEqual(trail[0].previous_plan, "Refraction and follow-up.")
        self.assertEqual(trail[1].previous_diagnosis, "Refractive error")
        # The whole chain is recoverable: the oldest snapshot is the original.
        self.assertEqual(trail[-1].previous_diagnosis, "Refractive error")

    def test_amending_writes_the_audit_row_naming_the_door_it_came_through(self):
        self.change(diagnosis="Myopia", amendment_reason="documentation",
                    amendment_detail="Filed wrongly.")
        entry = AuditLog.objects.get(action="note.amended")
        self.assertEqual((entry.actor, entry.details["source"], entry.details["reason"]),
                         (self.records, "django-admin", "documentation"))
        # Whose note was corrected, as well as who corrected it.
        self.assertEqual(entry.details["author"], "John Smith")

    def test_an_eye_examination_typed_here_is_held_to_the_same_definition(self):
        refused = self.change(amendment_reason="correction",
                              eye_examination='{"iop_right": 999}')
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "iop_right")
        self.note.refresh_from_db()
        self.assertIsNone(self.note.eye_examination)

        ok = self.change(amendment_reason="correction", eye_examination='{"iop_right": 16}')
        self.assertEqual(ok.status_code, 200)
        self.note.refresh_from_db()
        self.assertEqual(self.note.eye_examination, {"iop_right": 16})

    # --- who may use it -----------------------------------------------------------

    def test_a_clinician_with_admin_access_but_no_administrative_role_is_refused(self):
        """
        Django's `is_staff` opens the admin site; the HMIS role decides what
        may be administered in it. A doctor let into the admin gets neither
        the add form nor the change form for a clinical note.
        """
        self.smith.is_staff = True
        self.smith.save(update_fields=["is_staff"])
        self.client.force_login(self.smith)

        self.assertEqual(self.client.get(reverse(ADD)).status_code, 403)
        posted = self.client.post(reverse(CHANGE, args=[self.note.pk]),
                                  self.form_data(diagnosis="Sneaked in",
                                                 amendment_reason="correction"))
        self.assertEqual(posted.status_code, 403)
        self.note.refresh_from_db()
        self.assertEqual(self.note.diagnosis, "Refractive error")

    def test_a_django_permission_does_not_open_the_clinical_record(self):
        """
        The HMIS role decides who reads a note, and nothing else does. Granting
        `view_consultationnote` to a staff account is a *second* RBAC beside
        the one every other clinical screen is held to, so it buys nothing —
        neither the notes nor the archive of what they used to say, which holds
        the same clinical text.
        """
        self.smith.is_staff = True
        self.smith.save(update_fields=["is_staff"])
        self.smith.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="clinical",
            codename__in=["view_consultationnote", "view_consultationnoteamendment"]))
        # Permissions are cached on the instance once read; log in with a fresh one.
        granted = User.objects.get(pk=self.smith.pk)
        # Asserted so this test can never pass because the grant silently failed.
        self.assertTrue(granted.has_perm("clinical.view_consultationnote"))
        self.client.force_login(granted)

        for url in (reverse(LIST), reverse(CHANGE, args=[self.note.pk]),
                    reverse("admin:clinical_consultationnoteamendment_changelist")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

        # And the admin index does not advertise what it cannot open.
        index = self.client.get(reverse("admin:index"))
        self.assertNotContains(index, reverse(LIST))

    def test_a_note_can_never_be_deleted_from_the_admin(self):
        site_admin = admin.site._registry[ConsultationNote]
        request = type("R", (), {"user": self.records})()
        self.assertFalse(site_admin.has_delete_permission(request))
        self.assertFalse(site_admin.has_delete_permission(request, self.note))

    def test_an_anonymous_visitor_reaches_nothing(self):
        for url in (reverse(LIST), reverse(ADD), reverse(CHANGE, args=[self.note.pk])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (301, 302))
                self.assertIn("/admin/login", response.url)

    # --- what must not have moved --------------------------------------------------

    def test_vitals_and_nursing_notes_are_still_read_only_in_the_admin(self):
        """Only the consultation note gained an amendment door."""
        nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        Vitals.objects.create(patient=self.patient, recorded_by=nurse,
                              visit_time=timezone.now(), heart_rate=80)
        NursingNote.objects.create(patient=self.patient, nurse=nurse, observation="Settled.")
        request = type("R", (), {"user": self.records})()
        for model in (Vitals, NursingNote, ConsultationNoteAmendment):
            with self.subTest(model=model.__name__):
                site_admin = admin.site._registry[model]
                self.assertFalse(site_admin.has_add_permission(request))
                self.assertFalse(site_admin.has_change_permission(request))
                self.assertFalse(site_admin.has_delete_permission(request))

    def test_the_clinician_s_own_amendment_workflow_still_works(self):
        """The chart's door is unchanged by the admin's existing."""
        api = APIClient()
        api.force_authenticate(self.smith)
        response = api.patch(f"/api/notes/{self.note.pk}/",
                             {"diagnosis": "Myopia", "amendment_reason": "correction"},
                             format="json")
        self.assertEqual(response.status_code, 200, response.data)
        amendment = ConsultationNoteAmendment.objects.get(note=self.note)
        self.assertEqual((amendment.amended_by, amendment.previous_diagnosis),
                         (self.smith, "Refractive error"))
        self.assertEqual(AuditLog.objects.get(action="note.amended").details["source"], "hmis")
