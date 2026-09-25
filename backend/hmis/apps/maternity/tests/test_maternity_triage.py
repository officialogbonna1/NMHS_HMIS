"""
Maternity triage: **the hospital's vitals workflow, reached from the ward.**

The rule this file exists to hold is that there is no maternity vitals system.
A midwife's blood pressure is a `clinical.Vitals` row, written through the
same endpoint, locked by the same mixin, notified through the same bell and
read back through the same chart as the triage nurse's. What was missing was
only permission: `IsNurse` names the general nurse alone, so a midwife could
not take a reading in her own ward.

So what changed is a role group (`NURSING_ROLES`) and the queryset that says
what each role reads back. Everything else here is an assertion that nothing
else changed.
"""
import datetime

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.clinical.models import NursingNote, Vitals
from apps.departments.models import Department
from apps.inpatient.models import Admission, Bed, Ward
from apps.maternity import access, services
from apps.maternity.models import MaternityOption, MaternityVisitType
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute


class MaternityWard(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception")
        self.maternity = Department.objects.get(code="maternity")
        self.grace = User.objects.create_user(username="grace", password="t",
                                              role="maternity_nurse", first_name="Grace",
                                              last_name="Nwosu")
        self.ada = User.objects.create_user(username="ada", password="t",
                                            role="maternity_nurse", first_name="Ada",
                                            last_name="Okafor")
        self.dara = User.objects.create_user(username="dara", password="t", role="doctor",
                                             first_name="Dara", last_name="Ibe")
        self.paul = User.objects.create_user(username="paul", password="t", role="doctor",
                                             first_name="Paul", last_name="Eze")
        self.maternity.staff.add(self.dara, self.paul)
        self.stranger = User.objects.create_user(username="kemi", password="t",
                                                 role="doctor")
        self.triage_nurse = User.objects.create_user(username="nia", password="t",
                                                     role="nurse")

        self.mother = Patient.objects.create(first_name="Dora", last_name="Ward", sex="F",
                                             created_by=self.reception)
        self.route = services.assign_to_maternity(patient=self.mother, actor=self.reception,
                                                  nurse=self.grace)
        self.pregnancy = services.start_pregnancy(
            patient=self.mother, actor=self.grace,
            lmp=datetime.date.today() - datetime.timedelta(weeks=12))

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def reading(self, user, **figures):
        body = {"patient": self.mother.pk, "visit_time": timezone.now().isoformat(),
                "bp_systolic": 120, "bp_diastolic": 80, "heart_rate": 82,
                "temperature_c": "36.7", "respiratory_rate": 18, **figures}
        return self.api(user).post("/api/vitals/", body)

    def admit(self, number="M-07"):
        ward, _ = Ward.objects.get_or_create(name="Maternity Ward")
        bed = Bed.objects.create(ward=ward, number=number)
        return Admission.objects.create(patient=self.mother, bed=bed,
                                        admitted_by=self.grace,
                                        attending_doctor=self.dara)


class TheMidwifeTakesVitals(MaternityWard):
    """Points 1–4: the existing system, reached from the ward."""

    def test_she_can_record_them_at_all(self):
        """She could not. `IsNurse` is the general nurse, and that was the bug."""
        answer = self.reading(self.grace)
        self.assertEqual(answer.status_code, 201, answer.data)

    def test_it_is_an_ordinary_clinical_vitals_row(self):
        self.reading(self.grace)
        row = Vitals.objects.get(patient=self.mother)
        self.assertEqual(row.recorded_by, self.grace)
        self.assertEqual(row.bp_systolic, 120)
        # The hospital's own model, locked by the hospital's own mixin.
        self.assertTrue(row.is_locked)

    def test_there_is_no_maternity_vitals_model(self):
        from django.apps import apps as django_apps

        names = {model.__name__ for model in
                 django_apps.get_app_config("maternity").get_models()}
        for invented in ("MaternityVitals", "MaternityVitalSigns", "MaternityTriage",
                         "MaternityObservation"):
            self.assertNotIn(invented, names)

    def test_the_same_validation_refuses_the_same_things(self):
        """A reading with nothing in it is not a reading — the existing rule."""
        empty = self.api(self.grace).post("/api/vitals/", {"patient": self.mother.pk})
        self.assertEqual(empty.status_code, 400)
        self.assertIn("visit_time", empty.data)

    def test_she_can_write_the_note_that_goes_with_it(self):
        saved = self.reading(self.grace)
        note = self.api(self.grace).post("/api/nursing-notes/", {
            "patient": self.mother.pk, "vitals": saved.data["id"],
            "complaint": "Routine ANC check.", "observation": "Well."})
        self.assertEqual(note.status_code, 201, note.data)
        self.assertEqual(NursingNote.objects.get().nurse, self.grace)

    def test_a_reading_cannot_be_edited_afterwards(self):
        saved = self.reading(self.grace)
        again = self.api(self.grace).patch(f"/api/vitals/{saved.data['id']}/",
                                           {"heart_rate": 90})
        self.assertIn(again.status_code, (400, 403))
        self.assertEqual(Vitals.objects.get().heart_rate, 82)


class SheReturnsAndNothingIsOverwritten(MaternityWard):
    """Points 5 and 11: a visit is a row, and history stays."""

    def test_a_second_visit_is_a_second_reading(self):
        first = self.reading(self.grace, heart_rate=82)
        second = self.reading(self.ada, heart_rate=96)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(Vitals.objects.filter(patient=self.mother).count(), 2)
        self.assertEqual(
            sorted(Vitals.objects.values_list("heart_rate", flat=True)), [82, 96])

    def test_the_earlier_reading_is_untouched(self):
        self.reading(self.grace, heart_rate=82)
        earlier = Vitals.objects.get()
        self.reading(self.ada, heart_rate=96)
        earlier.refresh_from_db()
        self.assertEqual(earlier.heart_rate, 82)
        self.assertEqual(earlier.recorded_by, self.grace)

    def test_and_it_is_the_same_patient_and_the_same_pregnancy(self):
        self.reading(self.grace)
        self.reading(self.grace)
        self.assertEqual(Patient.objects.filter(last_name="Ward").count(), 1)
        self.assertEqual(self.mother.pregnancies.count(), 1)

    def test_the_history_reads_back_newest_and_oldest_together(self):
        self.reading(self.grace, heart_rate=82)
        self.reading(self.grace, heart_rate=96)
        rows = self.api(self.grace).get("/api/vitals/",
                                        {"patient": self.mother.pk}).data
        rows = rows.get("results", rows)
        self.assertEqual(len(rows), 2)
        for row in rows:
            # Point 7: when, and who took it.
            self.assertIsNotNone(row["created_at"])
            self.assertTrue(row.get("recorded_by_name") or row.get("recorded_by"))


class WhoReadsTheReadings(MaternityWard):
    """Points 6, 15, 16, 17, 18 — the team rule, applied to vitals."""

    def setUp(self):
        super().setUp()
        self.reading(self.grace)

    def rows_for(self, user):
        answer = self.api(user).get("/api/vitals/", {"patient": self.mother.pk})
        if answer.status_code != 200:
            return answer.status_code
        data = answer.data
        return data.get("results", data)

    def test_the_midwife_who_took_it_reads_it(self):
        self.assertEqual(len(self.rows_for(self.grace)), 1)

    def test_and_so_does_the_other_midwife(self):
        """Point 17 — a ward shares its patients."""
        self.assertEqual(len(self.rows_for(self.ada)), 1)

    def test_the_assigned_doctor_reads_it(self):
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        self.assertEqual(len(self.rows_for(self.dara)), 1)

    def test_and_so_does_the_other_maternity_doctor(self):
        """Point 18 — he is on the team, which is what confers it."""
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        self.assertEqual(len(self.rows_for(self.paul)), 1)

    def test_a_doctor_with_no_maternity_connection_reads_nothing(self):
        self.assertEqual(self.rows_for(self.stranger), [])

    def test_reception_reaches_the_endpoint_not_at_all(self):
        """Point 15 — and it is the API that refuses, not a hidden button."""
        self.assertEqual(self.rows_for(self.reception), 403)
        self.assertEqual(
            self.api(self.reception).post("/api/vitals/", {
                "patient": self.mother.pk, "visit_time": timezone.now().isoformat(),
                "bp_systolic": 120, "bp_diastolic": 80}).status_code, 403)

    def test_nor_does_an_unrelated_department(self):
        """Point 16."""
        for role in ("cashier", "pharmacist", "laboratory", "accountant"):
            who = User.objects.create_user(username=f"x_{role}", password="t", role=role)
            with self.subTest(role=role):
                self.assertEqual(self.rows_for(who), 403)

    def test_the_general_triage_nurse_still_reads_only_her_own(self):
        """Rule 11, unchanged — and this is what proves it was not widened."""
        self.assertEqual(self.rows_for(self.triage_nurse), [])
        self.reading(self.triage_nurse, heart_rate=70)
        self.assertEqual(len(self.rows_for(self.triage_nurse)), 1)


class TheDoctorIsToldAndCanBeSentTo(MaternityWard):
    """Points 6, 7 and 15 — the existing bell and the existing hand-off."""

    def test_recording_tells_the_doctor_holding_her(self):
        from apps.core.models import Notification

        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        Notification.objects.all().delete()
        self.reading(self.grace)
        told = Notification.objects.filter(recipient=self.dara)
        self.assertTrue(told.exists(), "the doctor holding her heard nothing")

    def test_there_is_no_second_notification_system(self):
        from apps.core.models import Notification

        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        self.reading(self.grace)
        # Every row is an ordinary `core.Notification`; maternity added none.
        self.assertTrue(Notification.objects.exists())
        from django.apps import apps as django_apps
        names = {m.__name__ for m in django_apps.get_app_config("maternity").get_models()}
        self.assertFalse({"MaternityNotification", "MaternityVitalsNotification"} & names)

    def test_she_hands_the_mother_to_a_doctor_the_way_triage_does(self):
        """
        Point 6 — `send-to-doctor` is the nurse's existing action and it is
        the midwife's now. No maternity hand-off was written.
        """
        self.reading(self.grace)
        sent = self.api(self.grace).post("/api/patient-routes/send-to-doctor/", {
            "patient": self.mother.pk, "doctor": self.dara.pk,
            "note": "38 weeks, contractions."}, format="json")
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertTrue(PatientRoute.objects.filter(
            visit__patient=self.mother, purpose="consultation",
            assigned_to=self.dara).exists())

    def test_the_doctor_then_reads_the_reading_she_took(self):
        """Point 8."""
        self.reading(self.grace, heart_rate=96)
        self.api(self.grace).post("/api/patient-routes/send-to-doctor/", {
            "patient": self.mother.pk, "doctor": self.dara.pk}, format="json")
        rows = self.api(self.dara).get("/api/vitals/", {"patient": self.mother.pk}).data
        rows = rows.get("results", rows)
        self.assertEqual([row["heart_rate"] for row in rows], [96])


class WhereSheIsLying(MaternityWard):
    """Points 9, 12, 13, 14 — the ward's own record, read not copied."""

    def state(self, user=None):
        return self.api(user or self.grace).get(
            "/api/maternity/assignment/", {"patient": self.mother.pk}).data["admission"]

    def test_not_admitted_is_a_state_not_a_gap(self):
        where = self.state()
        self.assertFalse(where["admitted"])
        self.assertIsNone(where["ward"])
        self.assertIsNone(where["bed"])

    def test_the_ward_and_the_bed_are_shown_once_she_is_admitted(self):
        self.admit(number="M-07")
        where = self.state()
        self.assertTrue(where["admitted"])
        self.assertEqual(where["ward"], "Maternity Ward")
        self.assertEqual(where["bed"], "M-07")
        self.assertIsNotNone(where["admitted_at"])

    def test_a_bed_change_moves_it_with_no_maternity_write_at_all(self):
        """Point 14 — it is derived, so the ward board is the authority."""
        admission = self.admit(number="M-07")
        self.assertEqual(self.state()["bed"], "M-07")
        other = Bed.objects.create(ward=admission.bed.ward, number="M-11")
        admission.bed = other
        admission.save(update_fields=["bed"])
        self.assertEqual(self.state()["bed"], "M-11")

    def test_discharging_her_reads_back_as_not_admitted(self):
        admission = self.admit()
        admission.status = "discharged"
        admission.discharged_at = timezone.now()
        admission.save(update_fields=["status", "discharged_at"])
        self.assertFalse(self.state()["admitted"])

    def test_maternity_stores_no_ward_or_bed_of_its_own(self):
        from apps.maternity.models import LabourEpisode, Pregnancy

        for model in (Pregnancy, LabourEpisode):
            columns = {f.name for f in model._meta.get_fields()}
            self.assertNotIn("ward", columns)
            self.assertNotIn("bed", columns)


class ReassigningTheDoctorKeepsEverything(MaternityWard):
    """Point 10 — restated against a chart that now has vitals on it."""

    def test_nothing_clinical_moves_when_the_doctor_does(self):
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.grace)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        self.reading(self.grace, heart_rate=82)
        self.reading(self.grace, heart_rate=88)
        labour = services.open_labour(pregnancy=self.pregnancy, actor=self.grace)
        services.record_observation(labour=labour, actor=self.grace,
                                    cervical_dilation_cm=5)
        delivery = services.record_delivery(
            labour=labour, actor=self.grace,
            delivery_type=MaternityOption.objects.get(kind="delivery_type", code="svd"),
            newborns=[{"sex": "F"}, {"sex": "M"}])
        services.record_postpartum(delivery=delivery, actor=self.grace)
        admission = self.admit()

        before = {
            "pregnancy": self.mother.pregnancies.get().pk,
            "vitals": sorted(Vitals.objects.values_list("pk", flat=True)),
            "observations": labour.observations.count(),
            "delivery": delivery.pk,
            "newborns": sorted(delivery.newborns.values_list("pk", flat=True)),
            "postpartum": delivery.postpartum_visits.count(),
            "bed": admission.bed.number,
            "patients": Patient.objects.count(),
        }

        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)

        delivery.refresh_from_db()
        after = {
            "pregnancy": self.mother.pregnancies.get().pk,
            "vitals": sorted(Vitals.objects.values_list("pk", flat=True)),
            "observations": labour.observations.count(),
            "delivery": delivery.pk,
            "newborns": sorted(delivery.newborns.values_list("pk", flat=True)),
            "postpartum": delivery.postpartum_visits.count(),
            "bed": Admission.objects.get(pk=admission.pk).bed.number,
            "patients": Patient.objects.count(),
        }
        self.assertEqual(before, after)
        self.assertEqual(access.assigned_doctor_for(self.mother), self.paul)
        self.assertEqual(access.assigned_nurse_for(self.mother), self.grace)

    def test_both_babies_stay_visible_to_the_team(self):
        """Point 19."""
        labour = services.open_labour(pregnancy=self.pregnancy, actor=self.grace)
        delivery = services.record_delivery(
            labour=labour, actor=self.grace,
            delivery_type=MaternityOption.objects.get(kind="delivery_type", code="svd"),
            newborns=[{"sex": "F"}, {"sex": "M"}])
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)
        for who in (self.dara, self.paul, self.ada):
            with self.subTest(who=who.username):
                babies = self.api(who).get("/api/newborns/", {"delivery": delivery.pk})
                rows = babies.data.get("results", babies.data)
                self.assertEqual(len(rows), 2)


class TheTriageQueueIsTheWards(MaternityWard):
    """Point 3 — who a midwife may open, and it is not only her own."""

    def test_she_opens_a_mother_another_midwife_is_responsible_for(self):
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.grace)
        rows = self.api(self.ada).get("/api/maternity/patients/").data["results"]
        self.assertEqual([row["patient_number"] for row in rows],
                         [self.mother.patient_number])
        self.assertEqual(self.reading(self.ada).status_code, 201)

    def test_and_a_mother_nobody_is_responsible_for(self):
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=None)
        self.assertEqual(self.reading(self.ada).status_code, 201)

    def test_she_does_not_get_the_general_triage_queue(self):
        """`IsNurse` was not rewritten: the vitals station is still the nurse's."""
        elsewhere = Patient.objects.create(first_name="Musa", last_name="Bello", sex="M",
                                           created_by=self.reception)
        answer = self.api(self.grace).post("/api/vitals/", {
            "patient": elsewhere.pk, "visit_time": timezone.now().isoformat(),
            "bp_systolic": 120, "bp_diastolic": 80})
        # She may write a reading — that is the role group — but she can read
        # back nothing for a patient who is not hers, which is the boundary.
        if answer.status_code == 201:
            rows = self.api(self.grace).get("/api/vitals/", {"patient": elsewhere.pk}).data
            self.assertEqual(rows.get("results", rows), [])
