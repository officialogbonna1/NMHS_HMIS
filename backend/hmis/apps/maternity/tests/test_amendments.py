"""
Correcting a maternity record — and what can never be corrected.

The rule, stated once: a maternity clinical record is **amended, never
overwritten**. A correction archives what the record said first, names a
reason, stamps who and when, and writes an audit row — which is
`clinical/services.py`'s consultation-note pattern (rule 45) applied to
maternity, not a second amendment framework.

And a record's **identity facts** are not correctable at all. Which patient,
which pregnancy, which labour, who entered it, when it was filed: changing one
does not fix a mistake, it turns the row into a different row.

**What this file does not test, because it does not exist**: an amendment to
`clinical.Vitals`. A reading is locked on save and a correction is a *new
reading* (rules 2 and 11) — deliberately, and unchanged by this work.
`VitalsStaysAbsolutelyLocked` holds that.
"""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import Vitals
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.maternity import amendments, services
from apps.maternity.models import (Delivery, LabourEpisode, MaternityAmendment,
                                   MaternityEncounter, MaternityOption,
                                   MaternityVisitType, Newborn, Pregnancy)
from apps.patients.models import Patient


class AMaternityRecord(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.grace = User.objects.create_user(username="grace", password="t",
                                              role="maternity_nurse", first_name="Grace",
                                              last_name="Nwosu")
        self.ada = User.objects.create_user(username="ada", password="t",
                                            role="maternity_nurse", first_name="Ada",
                                            last_name="Okafor")
        self.dara = User.objects.create_user(username="dara", password="t", role="doctor",
                                             first_name="Dara", last_name="Ibe")
        Department.objects.get(code="maternity").staff.add(self.dara)

        self.mother = Patient.objects.create(first_name="Dora", last_name="Williams",
                                             sex="F", created_by=self.reception)
        self.other = Patient.objects.create(first_name="Musa", last_name="Bello",
                                            sex="M", created_by=self.reception)
        services.assign_to_maternity(patient=self.mother, actor=self.reception)
        # Grace enters the pregnancy, so Grace is its author.
        self.pregnancy = services.start_pregnancy(
            patient=self.mother, actor=self.grace,
            lmp=datetime.date.today() - datetime.timedelta(weeks=12))

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def amend(self, user, **body):
        return self.api(user).patch(f"/api/pregnancies/{self.pregnancy.pk}/",
                                    body, format="json")


class AnAuthorisedUserCorrectsTheRecord(AMaternityRecord):
    """Cases 1, 3, 4, 5, 6."""

    def test_the_author_may(self):
        answer = self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo",
                            amendment_detail="Card misread at booking.")
        self.assertEqual(answer.status_code, 200, answer.data)
        self.pregnancy.refresh_from_db()
        self.assertEqual(str(self.pregnancy.lmp), "2026-01-05")

    def test_and_so_may_an_administrator(self):
        self.assertEqual(
            self.amend(self.admin, lmp="2026-01-05", amendment_reason="typo").status_code,
            200)

    def test_the_original_value_stays_recoverable(self):
        was = str(self.pregnancy.lmp)
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        trail = MaternityAmendment.objects.latest("id")
        self.assertEqual(trail.previous["lmp"], was)

    def test_the_trail_names_who_changed_it(self):
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        trail = MaternityAmendment.objects.latest("id")
        self.assertEqual(trail.amended_by, self.grace)
        self.assertEqual(trail.amended_by_name, "Grace Nwosu")

    def test_and_when(self):
        before = timezone.now()
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        trail = MaternityAmendment.objects.latest("id")
        self.assertIsNotNone(trail.created_at)
        self.assertGreaterEqual(trail.created_at, before)

    def test_and_why(self):
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="wrong_value",
                   amendment_detail="Dating scan corrected it.")
        trail = MaternityAmendment.objects.latest("id")
        self.assertEqual(trail.reason, "wrong_value")
        self.assertEqual(trail.detail, "Dating scan corrected it.")

    def test_a_correction_without_a_reason_is_refused(self):
        answer = self.amend(self.grace, lmp="2026-01-05")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.data["code"], "amendment_reason_required")
        self.pregnancy.refresh_from_db()
        self.assertNotEqual(str(self.pregnancy.lmp), "2026-01-05")

    def test_the_refusal_carries_the_choices_so_the_form_never_guesses(self):
        answer = self.amend(self.grace, lmp="2026-01-05")
        self.assertIn({"value": "typo", "label": "Typing or data-entry error"},
                      answer.data["reasons"])

    def test_a_reason_nobody_recognises_is_refused(self):
        answer = self.amend(self.grace, lmp="2026-01-05", amendment_reason="because")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.data["code"], "amendment_reason_required")

    def test_nothing_is_written_when_the_correction_is_refused(self):
        self.amend(self.grace, lmp="2026-01-05")
        self.assertEqual(MaternityAmendment.objects.count(), 0,
                         "a refused save left a trail describing it anyway")

    def test_the_event_goes_to_the_existing_audit_log(self):
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        row = AuditLog.objects.filter(action="maternity.record_amended").latest("id")
        self.assertEqual(row.actor, self.grace)
        self.assertEqual(row.details["record"], "Pregnancy")
        self.assertEqual(row.details["patient_number"], self.mother.patient_number)

    def test_the_diff_says_what_this_correction_alone_changed(self):
        was = str(self.pregnancy.lmp)
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        self.pregnancy.refresh_from_db()
        trail = MaternityAmendment.objects.latest("id")
        changes = amendments.changes_for(trail, self.pregnancy)
        self.assertEqual(changes, [{"field": "lmp", "label": "LMP",
                                    "from": was, "to": "2026-01-05"}])

    def test_two_corrections_are_two_rows_and_the_first_still_reads(self):
        first = str(self.pregnancy.lmp)
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        self.amend(self.grace, lmp="2026-02-05", amendment_reason="wrong_value")
        trail = MaternityAmendment.objects.order_by("id")
        self.assertEqual(trail.count(), 2)
        self.assertEqual(trail[0].previous["lmp"], first)
        self.assertEqual(trail[1].previous["lmp"], "2026-01-05")

    def test_the_history_is_on_the_record(self):
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        answer = self.api(self.grace).get(
            f"/api/pregnancies/{self.pregnancy.pk}/amendments/")
        self.assertEqual(answer.status_code, 200)
        row = answer.data[0]
        self.assertEqual(row["reason_label"], "Typing or data-entry error")
        self.assertEqual(row["amended_by_name"], "Grace Nwosu")
        self.assertTrue(row["changes"])


class AnUnauthorisedUserCannot(AMaternityRecord):
    """Cases 2, 12, 13 — and §8: the department is not the permission."""

    def test_another_midwife_may_not_correct_somebody_elses_entry(self):
        answer = self.amend(self.ada, lmp="2026-01-05", amendment_reason="typo")
        self.assertEqual(answer.status_code, 403)
        self.pregnancy.refresh_from_db()
        self.assertNotEqual(str(self.pregnancy.lmp), "2026-01-05")

    def test_a_maternity_doctor_may_not_either(self):
        """
        Case 13. Being authorised in Maternity opens the ward's records; it
        does not make somebody else's entry yours to rewrite.
        """
        from apps.maternity.access import in_maternity_team

        self.assertTrue(in_maternity_team(self.dara))
        self.assertEqual(self.amend(self.dara, lmp="2026-01-05",
                                    amendment_reason="typo").status_code, 403)

    def test_reception_reaches_none_of_it(self):
        self.assertEqual(self.amend(self.reception, lmp="2026-01-05",
                                    amendment_reason="typo").status_code, 403)

    def test_an_unrelated_role_reaches_none_of_it(self):
        for role in ("cashier", "pharmacist", "laboratory"):
            who = User.objects.create_user(username=f"x_{role}", password="t", role=role)
            with self.subTest(role=role):
                self.assertEqual(self.amend(who, lmp="2026-01-05",
                                            amendment_reason="typo").status_code, 403)

    def test_a_doctor_outside_maternity_reaches_none_of_it(self):
        """Case 12 — department authorisation still applies."""
        stranger = User.objects.create_user(username="kemi", password="t", role="doctor")
        self.assertEqual(self.amend(stranger, lmp="2026-01-05",
                                    amendment_reason="typo").status_code, 403)


class IdentityFactsCannotBeChanged(AMaternityRecord):
    """Cases 7, 8, 9 — and the worst hole this closed."""

    def test_a_pregnancy_cannot_be_moved_to_another_patient(self):
        answer = self.amend(self.grace, patient=self.other.pk, amendment_reason="typo")
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.data["code"], "immutable_field")
        self.assertEqual(answer.data["fields"], ["patient"])
        self.pregnancy.refresh_from_db()
        self.assertEqual(self.pregnancy.patient, self.mother)

    def test_not_even_by_an_administrator(self):
        answer = self.amend(self.admin, patient=self.other.pk, amendment_reason="typo")
        self.assertEqual(answer.status_code, 400)
        self.pregnancy.refresh_from_db()
        self.assertEqual(self.pregnancy.patient, self.mother)

    def test_a_protected_field_does_not_half_succeed(self):
        """A PATCH carrying one good field and one protected one writes neither."""
        was = str(self.pregnancy.lmp)
        answer = self.amend(self.grace, patient=self.other.pk, lmp="2026-01-05",
                            amendment_reason="typo")
        self.assertEqual(answer.status_code, 400)
        self.pregnancy.refresh_from_db()
        self.assertEqual(str(self.pregnancy.lmp), was)
        self.assertEqual(self.pregnancy.patient, self.mother)
        self.assertEqual(MaternityAmendment.objects.count(), 0)

    def test_naming_a_protected_field_with_its_own_value_is_not_a_change(self):
        answer = self.amend(self.grace, patient=self.mother.pk, lmp="2026-01-05",
                            amendment_reason="typo")
        self.assertEqual(answer.status_code, 200, answer.data)

    def test_a_record_cannot_be_deleted_through_the_api(self):
        answer = self.api(self.admin).delete(f"/api/pregnancies/{self.pregnancy.pk}/")
        self.assertEqual(answer.status_code, 405)
        self.assertTrue(Pregnancy.objects.filter(pk=self.pregnancy.pk).exists())

    def test_a_whole_record_replace_is_refused_too(self):
        """No PUT: replacing a record wholesale is not a correction."""
        answer = self.api(self.grace).put(f"/api/pregnancies/{self.pregnancy.pk}/",
                                          {"patient": self.other.pk}, format="json")
        self.assertEqual(answer.status_code, 405)

    def test_the_author_column_cannot_be_reassigned(self):
        answer = self.amend(self.grace, opened_by=self.ada.pk, amendment_reason="typo")
        # `opened_by` is read-only on the serializer *and* protected here, so
        # it is either ignored or refused — never written.
        self.pregnancy.refresh_from_db()
        self.assertEqual(self.pregnancy.opened_by, self.grace)
        self.assertIn(answer.status_code, (200, 400))


class EveryAmendableRecordFollowsTheSameRule(AMaternityRecord):
    """Case 6 across the whole clinical record, not just the pregnancy."""

    def setUp(self):
        super().setUp()
        self.encounter = services.open_encounter(
            pregnancy=self.pregnancy, actor=self.grace,
            visit_type=MaternityVisitType.objects.filter(is_active=True).first(),
            summary="Fundal height 30cm.")
        self.labour = services.open_labour(pregnancy=self.pregnancy, actor=self.grace)
        self.delivery = services.record_delivery(
            labour=self.labour, actor=self.grace,
            delivery_type=MaternityOption.objects.get(kind="delivery_type", code="svd"),
            newborns=[{"sex": "F"}, {"sex": "M"}])

    def paths(self):
        return {
            "encounter": (f"/api/maternity-encounters/{self.encounter.pk}/",
                          {"summary": "Fundal height 32cm."}, "pregnancy",
                          self.pregnancy.pk),
            "labour": (f"/api/labour-episodes/{self.labour.pk}/",
                       {"notes": "Corrected."}, "pregnancy", self.pregnancy.pk),
            "delivery": (f"/api/deliveries/{self.delivery.pk}/",
                         {"notes": "Corrected."}, "labour", self.labour.pk),
        }

    def test_each_needs_a_reason(self):
        for name, (path, body, _, _) in self.paths().items():
            with self.subTest(record=name):
                answer = self.api(self.grace).patch(path, body, format="json")
                self.assertEqual(answer.status_code, 400)
                self.assertEqual(answer.data["code"], "amendment_reason_required")

    def test_each_writes_a_trail_when_corrected(self):
        for name, (path, body, _, _) in self.paths().items():
            with self.subTest(record=name):
                before = MaternityAmendment.objects.count()
                answer = self.api(self.grace).patch(
                    path, {**body, "amendment_reason": "typo"}, format="json")
                self.assertEqual(answer.status_code, 200, answer.data)
                self.assertEqual(MaternityAmendment.objects.count(), before + 1)

    def test_each_refuses_to_be_re_parented(self):
        for name, (path, _, field, _) in self.paths().items():
            with self.subTest(record=name):
                answer = self.api(self.grace).patch(
                    path, {field: 999999, "amendment_reason": "typo"}, format="json")
                self.assertEqual(answer.status_code, 400)
                self.assertEqual(answer.data["code"], "immutable_field")

    def test_none_of_them_can_be_deleted(self):
        for name, (path, _, _, _) in self.paths().items():
            with self.subTest(record=name):
                self.assertEqual(self.api(self.admin).delete(path).status_code, 405)

    def test_the_babies_were_never_editable_and_still_are_not(self):
        """
        `NewbornViewSet` is read-only and stays so — a birth record is corrected
        through the delivery that produced it, not by typing over a baby.
        """
        baby = self.delivery.newborns.first()
        for method in ("patch", "put", "delete"):
            with self.subTest(method=method):
                answer = getattr(self.api(self.grace), method)(
                    f"/api/newborns/{baby.pk}/", {"sex": "M"}, format="json")
                self.assertEqual(answer.status_code, 405)

    def test_both_babies_still_read_after_the_delivery_is_corrected(self):
        """Case 10 — existing records stay readable after an amendment."""
        self.api(self.grace).patch(f"/api/deliveries/{self.delivery.pk}/",
                                   {"notes": "Corrected.", "amendment_reason": "typo"},
                                   format="json")
        self.assertEqual(self.delivery.newborns.count(), 2)
        answer = self.api(self.grace).get("/api/newborns/", {"delivery": self.delivery.pk})
        rows = answer.data.get("results", answer.data)
        self.assertEqual(len(rows), 2)


class VitalsStaysAbsolutelyLocked(AMaternityRecord):
    """
    Case 14. The brief's example — BP 120/80 amended to 125/80 — describes
    something this HMIS deliberately does not do.

    `clinical.Vitals` is `LockedRecordMixin`: it locks on save and has no edit
    path at all, because a correction is a **new reading** (rules 2 and 11).
    Both readings stay on the chart, which is what the triage history shows.
    Nothing in the maternity amendment work gave it one.
    """

    def reading(self, **figures):
        return self.api(self.grace).post("/api/vitals/", {
            "patient": self.mother.pk, "visit_time": timezone.now().isoformat(),
            "bp_systolic": 120, "bp_diastolic": 80, **figures})

    def test_a_reading_cannot_be_amended(self):
        """
        Refused, and the figure does not move. The status is the *lock's* —
        `LockedRecordMixin.save()` raises `PermissionDenied` (403), and a PUT
        fails validation before it gets that far (400). What matters is that
        no path writes over a recorded reading, so this asserts the reading
        rather than a status code.
        """
        saved = self.reading()
        self.assertEqual(saved.status_code, 201, saved.data)
        for method in ("patch", "put"):
            with self.subTest(method=method):
                answer = getattr(self.api(self.grace), method)(
                    f"/api/vitals/{saved.data['id']}/",
                    {"bp_systolic": 125, "amendment_reason": "typo"}, format="json")
                self.assertIn(answer.status_code, (400, 403, 405))
                self.assertEqual(Vitals.objects.get(pk=saved.data["id"]).bp_systolic, 120)

    def test_and_an_admin_cannot_amend_one_either_through_this_work(self):
        saved = self.reading()
        self.api(self.admin).patch(f"/api/vitals/{saved.data['id']}/",
                                   {"bp_systolic": 125, "amendment_reason": "typo"},
                                   format="json")
        # Whatever the admin override does, it writes no maternity trail —
        # vitals are not part of this amendment mechanism.
        self.assertEqual(MaternityAmendment.objects.count(), 0)

    def test_a_correction_is_a_second_reading_and_both_stay(self):
        self.reading(bp_systolic=120)
        self.reading(bp_systolic=125)
        readings = Vitals.objects.filter(patient=self.mother).order_by("id")
        self.assertEqual([r.bp_systolic for r in readings], [120, 125])

    def test_there_is_no_vitals_amendment_model(self):
        from django.apps import apps as django_apps

        for app in ("clinical", "maternity"):
            names = {m.__name__ for m in django_apps.get_app_config(app).get_models()}
            self.assertNotIn("VitalsAmendment", names)


class NothingElseWasBuilt(AMaternityRecord):
    """Case 15, and §1's "no second framework"."""

    def test_the_trail_is_one_model_over_every_maternity_record(self):
        from django.apps import apps as django_apps

        names = {m.__name__ for m in django_apps.get_app_config("maternity").get_models()}
        self.assertIn("MaternityAmendment", names)
        for invented in ("PregnancyAmendment", "DeliveryAmendment",
                         "LabourAmendment", "MaternityAuditLog"):
            self.assertNotIn(invented, names)

    def test_the_audit_row_is_the_existing_audit_log(self):
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        self.assertTrue(
            AuditLog.objects.filter(action="maternity.record_amended").exists())

    def test_the_configuration_models_stay_editable(self):
        """§6 — a catalogue is not a patient record."""
        visit_type = MaternityVisitType.objects.filter(is_active=True).first()
        answer = self.api(self.admin).patch(
            f"/api/maternity-visit-types/{visit_type.pk}/", {"name": "ANC — booking"},
            format="json")
        self.assertEqual(answer.status_code, 200, answer.data)

    def test_the_patient_and_her_history_are_untouched(self):
        """Case 11."""
        before = {"patients": Patient.objects.count(),
                  "pregnancies": Pregnancy.objects.count(),
                  "number": self.pregnancy.number}
        self.amend(self.grace, lmp="2026-01-05", amendment_reason="typo")
        self.pregnancy.refresh_from_db()
        self.assertEqual({"patients": Patient.objects.count(),
                          "pregnancies": Pregnancy.objects.count(),
                          "number": self.pregnancy.number}, before)
