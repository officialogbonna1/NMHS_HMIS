"""
The returning maternity patient.

Every test here exists to hold one line: **a woman who comes back is the same
woman, in the same pregnancy, on a new visit.** The two mistakes it is written
to make impossible are registering her again and starting a second pregnancy
because she walked through the door.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.maternity.models import MaternityEncounter, MaternityVisitType, Pregnancy
from apps.maternity.services import MaternityError, active_pregnancy_for, start_pregnancy
from apps.patients.models import Patient


class Maternity(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="t",
                                                  role="reception")
        self.midwife = User.objects.create_user(username="midwife", password="t", role="nurse",
                                                first_name="Ada", last_name="Bello")
        self.doctor = User.objects.create_user(username="obgyn", password="t", role="doctor")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.mother = Patient.objects.create(first_name="Mary", last_name="Jane", sex="F",
                                             created_by=self.reception)
        self.booking = MaternityVisitType.objects.get(code="anc-booking")
        self.followup = MaternityVisitType.objects.get(code="anc-followup")
        self.labour = MaternityVisitType.objects.get(code="labour-assessment")
        self.emergency = MaternityVisitType.objects.get(code="maternity-emergency")
        self.postnatal = MaternityVisitType.objects.get(code="postnatal")

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def lookup(self, user=None, patient=None):
        return self.api(user or self.reception).get(
            "/api/maternity/lookup/", {"patient": (patient or self.mother).patient_number})

    def pregnant(self, patient=None, **kwargs):
        return start_pregnancy(patient=patient or self.mother, actor=self.midwife, **kwargs)

    def visit(self, pregnancy, visit_type, user=None):
        return self.api(user or self.midwife).post("/api/maternity-encounters/", {
            "pregnancy": pregnancy.pk, "visit_type": visit_type.pk}, format="json")


class TheDeskKnowsWhoSheIs(Maternity):
    """Section 1 and 2: existing patient detection, and active-pregnancy detection."""

    def test_a_known_patient_is_found_by_her_hospital_number(self):
        response = self.lookup()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["known"])
        self.assertEqual(response.data["patient"]["patient_number"],
                         self.mother.patient_number)
        self.assertEqual(response.data["patient"]["name"], "Jane, Mary")

    def test_she_is_found_by_uuid_and_by_id_too(self):
        for value in (str(self.mother.uuid), str(self.mother.pk)):
            response = self.api(self.reception).get("/api/maternity/lookup/", {"patient": value})
            self.assertEqual(response.status_code, 200, value)
            self.assertEqual(response.data["patient"]["id"], self.mother.pk)

    def test_an_unknown_patient_is_a_404_not_a_new_record(self):
        response = self.api(self.reception).get("/api/maternity/lookup/",
                                                {"patient": "NMHS-P999999"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Patient.objects.count(), 1)

    def test_with_no_pregnancy_the_desk_is_offered_start_and_not_continue(self):
        response = self.lookup()
        self.assertIsNone(response.data["active_pregnancy"])
        self.assertTrue(response.data["can_start"])
        self.assertFalse(response.data["can_continue"])
        self.assertFalse(response.data["has_history"])

    def test_with_an_active_pregnancy_it_is_offered_continue_and_not_start(self):
        self.pregnant(lmp=datetime.date.today() - datetime.timedelta(days=200))
        response = self.lookup()
        self.assertTrue(response.data["can_continue"])
        self.assertFalse(response.data["can_start"])
        self.assertEqual(response.data["active_pregnancy"]["number"], 1)

    def test_it_carries_the_summary_the_desk_reads_out(self):
        pregnancy = self.pregnant(lmp=datetime.date.today() - datetime.timedelta(weeks=32),
                                  edd=datetime.date.today() + datetime.timedelta(weeks=8),
                                  gravida=2, para=1)
        MaternityEncounter.objects.create(pregnancy=pregnancy, visit_type=self.followup,
                                          recorded_by=self.midwife)
        active = self.lookup().data["active_pregnancy"]
        self.assertEqual(active["reference"], f"PRG-{pregnancy.pk:06d}")
        self.assertEqual(active["gestation"], "32w 0d")
        self.assertEqual(active["last_encounter"]["type"], "ANC — follow-up")
        self.assertIsNotNone(active["edd"])
        # Her obstetric history is not the desk's. `self.lookup()` reads as
        # reception; a clinician reading the same payload does get it.
        for clinical in ("gravida", "para", "notes"):
            self.assertNotIn(clinical, active)
        clinician = self.api(self.midwife).get(
            "/api/maternity/lookup/",
            {"patient": self.mother.patient_number}).data["active_pregnancy"]
        self.assertEqual(clinician["gravida"], 2)
        self.assertEqual(clinician["para"], 1)

    def test_the_visit_types_come_with_it_so_the_desk_can_pick_one(self):
        offered = {t["code"] for t in self.lookup().data["visit_types"]}
        self.assertIn("anc-followup", offered)
        self.assertIn("labour-assessment", offered)

    def test_a_retired_visit_type_is_not_offered(self):
        MaternityVisitType.objects.filter(code="anc-followup").update(is_active=False)
        offered = {t["code"] for t in self.lookup().data["visit_types"]}
        self.assertNotIn("anc-followup", offered)


class OnePregnancyAtATime(Maternity):
    """Section 2 and 3: an episode is not started because she returned."""

    def test_the_first_pregnancy_is_number_one(self):
        self.assertEqual(self.pregnant().number, 1)

    def test_a_second_active_pregnancy_is_refused(self):
        self.pregnant()
        with self.assertRaises(MaternityError) as refusal:
            self.pregnant()
        self.assertEqual(refusal.exception.code, "pregnancy_already_active")
        self.assertEqual(Pregnancy.objects.count(), 1)

    def test_the_api_refuses_it_too_and_names_the_reason(self):
        self.pregnant()
        response = self.api(self.midwife).post("/api/pregnancies/",
                                               {"patient": self.mother.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "pregnancy_already_active")
        self.assertEqual(Pregnancy.objects.count(), 1)

    def test_the_database_refuses_a_concurrent_second_one(self):
        """The service's check is bypassed, as two interleaved requests would."""
        from django.db import IntegrityError, transaction

        self.pregnant()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pregnancy.objects.create(patient=self.mother, number=2, status="active")
        self.assertEqual(
            Pregnancy.objects.filter(patient=self.mother, status="active").count(), 1)

    def test_another_woman_is_unaffected(self):
        other = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                       created_by=self.reception)
        self.pregnant()
        self.assertEqual(self.pregnant(patient=other).number, 1)
        self.assertEqual(Pregnancy.objects.count(), 2)


class PregnanciesAreSeparateEpisodes(Maternity):
    """Section 3 and 21: starting #2 must never modify #1."""

    def _finish(self, pregnancy, outcome="delivered"):
        return self.api(self.doctor).post(f"/api/pregnancies/{pregnancy.pk}/close/",
                                          {"outcome": outcome}, format="json")

    def test_a_new_pregnancy_is_numbered_next_and_leaves_the_last_alone(self):
        first = self.pregnant(lmp=datetime.date(2024, 1, 1), gravida=1)
        MaternityEncounter.objects.create(pregnancy=first, visit_type=self.booking,
                                          recorded_by=self.midwife)
        before = Pregnancy.objects.get(pk=first.pk).__dict__.copy()
        self._finish(first)

        second = self.pregnant(lmp=datetime.date(2026, 1, 1), gravida=2)
        self.assertEqual(second.number, 2)

        first.refresh_from_db()
        self.assertEqual(first.lmp, before["lmp"])
        self.assertEqual(first.gravida, before["gravida"])
        self.assertEqual(first.encounters.count(), 1)
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 2)

    def test_the_history_shows_both_and_names_the_active_one(self):
        first = self.pregnant()
        self._finish(first)
        second = self.pregnant()
        data = self.lookup().data
        self.assertEqual([p["number"] for p in data["history"]], [2, 1])
        self.assertEqual(data["active_pregnancy"]["number"], second.number)
        self.assertEqual([p["is_active"] for p in data["history"]], [True, False])

    def test_a_completed_pregnancy_records_its_outcome(self):
        pregnancy = self.pregnant()
        response = self._finish(pregnancy, outcome="delivered")
        self.assertEqual(response.status_code, 200, response.data)
        pregnancy.refresh_from_db()
        self.assertEqual(pregnancy.status, "completed")
        self.assertEqual(pregnancy.outcome, "delivered")
        self.assertIsNotNone(pregnancy.ended_on)

    def test_a_loss_ends_rather_than_completes_it(self):
        pregnancy = self.pregnant()
        self._finish(pregnancy, outcome="miscarriage")
        pregnancy.refresh_from_db()
        self.assertEqual(pregnancy.status, "ended")
        self.assertEqual(pregnancy.outcome, "miscarriage")

    def test_an_outcome_nobody_recognises_is_refused(self):
        pregnancy = self.pregnant()
        response = self._finish(pregnancy, outcome="something else")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "unknown_outcome")
        pregnancy.refresh_from_db()
        self.assertTrue(pregnancy.is_active)

    def test_closing_it_twice_is_refused(self):
        pregnancy = self.pregnant()
        self._finish(pregnancy)
        self.assertEqual(self._finish(pregnancy).data["code"], "pregnancy_not_active")

    def test_closing_keeps_every_visit_in_it(self):
        pregnancy = self.pregnant()
        for kind in (self.booking, self.followup, self.followup):
            MaternityEncounter.objects.create(pregnancy=pregnancy, visit_type=kind,
                                              recorded_by=self.midwife)
        self._finish(pregnancy)
        self.assertEqual(pregnancy.encounters.count(), 3)


class ReturningForAnotherAncVisit(Maternity):
    """Section 4, 6 and 13 — the case this whole app exists for."""

    def test_a_return_visit_creates_an_encounter_not_a_patient_or_a_pregnancy(self):
        pregnancy = self.pregnant()
        patients_before = Patient.objects.count()

        response = self.visit(pregnancy, self.followup)

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Patient.objects.count(), patients_before)
        self.assertEqual(Pregnancy.objects.count(), 1)
        self.assertEqual(pregnancy.encounters.count(), 1)

    def test_four_visits_are_four_rows_and_none_overwrites_another(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.booking)
        for _ in range(3):
            self.visit(pregnancy, self.followup)

        self.assertEqual(pregnancy.encounters.count(), 4)
        kinds = list(pregnancy.encounters.order_by("pk").values_list("visit_type__code",
                                                                     flat=True))
        self.assertEqual(kinds, ["anc-booking", "anc-followup", "anc-followup", "anc-followup"])

    def test_an_earlier_visit_keeps_what_was_written_on_it(self):
        pregnancy = self.pregnant()
        first = MaternityEncounter.objects.create(
            pregnancy=pregnancy, visit_type=self.booking, recorded_by=self.midwife,
            summary="Booking. BP 110/70. No complaints.")
        self.visit(pregnancy, self.followup)
        first.refresh_from_db()
        self.assertEqual(first.summary, "Booking. BP 110/70. No complaints.")

    def test_today_is_not_prefilled_from_the_last_visit(self):
        """Section 13: history is read, never copied into today as editable data."""
        pregnancy = self.pregnant()
        MaternityEncounter.objects.create(pregnancy=pregnancy, visit_type=self.booking,
                                          summary="Booking findings", recorded_by=self.midwife)
        today = MaternityEncounter.objects.get(pk=self.visit(pregnancy, self.followup).data["id"])
        self.assertEqual(today.summary, "")

    def test_a_past_visit_cannot_be_deleted_or_replaced_through_the_api(self):
        pregnancy = self.pregnant()
        encounter_id = self.visit(pregnancy, self.followup).data["id"]
        client = self.api(self.midwife)
        self.assertEqual(client.delete(f"/api/maternity-encounters/{encounter_id}/").status_code,
                         405)
        self.assertEqual(client.put(f"/api/maternity-encounters/{encounter_id}/",
                                    {}, format="json").status_code, 405)

    def test_a_visit_cannot_be_moved_to_another_pregnancy(self):
        pregnancy = self.pregnant()
        encounter_id = self.visit(pregnancy, self.followup).data["id"]
        other = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                       created_by=self.reception)
        elsewhere = self.pregnant(patient=other)
        self.api(self.midwife).patch(f"/api/maternity-encounters/{encounter_id}/",
                                     {"pregnancy": elsewhere.pk}, format="json")
        self.assertEqual(MaternityEncounter.objects.get(pk=encounter_id).pregnancy_id,
                         pregnancy.pk)

    def test_the_gestation_on_each_visit_is_derived_from_her_dates(self):
        pregnancy = self.pregnant(lmp=datetime.date.today() - datetime.timedelta(weeks=30,
                                                                                days=2))
        encounter = MaternityEncounter.objects.create(pregnancy=pregnancy,
                                                      visit_type=self.followup,
                                                      recorded_by=self.midwife)
        self.assertEqual(encounter.gestation, (30, 2))

    def test_a_visit_is_refused_once_the_pregnancy_has_ended(self):
        pregnancy = self.pregnant()
        self.api(self.doctor).post(f"/api/pregnancies/{pregnancy.pk}/close/",
                                   {"outcome": "delivered"}, format="json")
        response = self.visit(pregnancy, self.followup)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "pregnancy_not_active")

    def test_a_retired_visit_type_cannot_be_booked(self):
        pregnancy = self.pregnant()
        MaternityVisitType.objects.filter(pk=self.followup.pk).update(is_active=False)
        self.followup.refresh_from_db()
        response = self.visit(pregnancy, self.followup)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "visit_type_not_available")


class TheVisitTypeSaysWhatKindOfAttendanceItIs(Maternity):
    """Sections 5, 8 and 9: labour and an emergency are not ANC follow-ups."""

    def test_labour_is_recorded_as_labour(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.booking)
        response = self.visit(pregnancy, self.labour)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["visit_type_code"], "labour-assessment")
        # And the ANC history it arrives with is intact.
        self.assertEqual(pregnancy.encounters.count(), 2)
        self.assertEqual(Pregnancy.objects.count(), 1)

    def test_an_emergency_is_recorded_as_an_emergency(self):
        pregnancy = self.pregnant()
        response = self.visit(pregnancy, self.emergency)
        self.assertEqual(response.data["visit_type_code"], "maternity-emergency")

    def test_postnatal_care_is_not_a_new_pregnancy(self):
        """Section 8's edge case: she comes back after delivering."""
        pregnancy = self.pregnant()
        response = self.visit(pregnancy, self.postnatal)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 1)
        self.assertEqual(response.data["visit_type_code"], "postnatal")

    def test_the_booking_visit_is_the_one_marked_as_such(self):
        self.assertTrue(self.booking.is_booking)
        self.assertFalse(self.followup.is_booking)
        self.assertFalse(self.labour.is_booking)


class TheTimeline(Maternity):
    """Section 11: one pregnancy, every visit, in order."""

    def test_it_lists_every_visit_of_the_pregnancy(self):
        pregnancy = self.pregnant(lmp=datetime.date.today() - datetime.timedelta(weeks=20))
        self.visit(pregnancy, self.booking)
        self.visit(pregnancy, self.followup)

        response = self.api(self.midwife).get(f"/api/pregnancies/{pregnancy.pk}/timeline/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["encounters"]), 2)
        self.assertEqual(response.data["encounter_count"], 2)

    def test_each_row_carries_what_the_table_shows(self):
        pregnancy = self.pregnant(lmp=datetime.date.today() - datetime.timedelta(weeks=24))
        self.visit(pregnancy, self.followup)
        row = self.api(self.midwife).get(
            f"/api/pregnancies/{pregnancy.pk}/timeline/").data["encounters"][0]
        for key in ("seen_on", "visit_type_name", "gestation", "provider_name", "status"):
            self.assertIn(key, row)
        self.assertEqual(row["gestation"], "24w 0d")
        self.assertEqual(row["provider_name"], "Ada Bello")
        self.assertEqual(row["status"], "in_progress")

    def test_a_pregnancy_with_no_dates_says_nothing_rather_than_guessing(self):
        pregnancy = self.pregnant()
        self.assertIsNone(pregnancy.gestation_on())
        self.assertEqual(self.lookup().data["active_pregnancy"]["gestation"], "")


class MaternityReusesTheHospital(Maternity):
    """Sections 12, 14, 15, 18: nothing here is a second system."""

    def test_no_second_vitals_model_exists(self):
        """
        The app owns the obstetric spine and nothing else. Phase 2 added the
        labour → delivery → newborn → postpartum half of it; what this holds
        is that none of it became a second copy of a hospital system the HMIS
        already has.
        """
        from django.apps import apps as django_apps

        maternity_models = {m.__name__ for m in django_apps.get_app_config("maternity").get_models()}
        self.assertEqual(maternity_models, {
            # Phase 1 — the spine.
            "MaternityVisitType", "Pregnancy", "MaternityEncounter",
            # Phase 2 — the episode, and the hospital's own maternity words.
            "MaternityOption", "LabourEpisode", "LabourObservation",
            "Delivery", "Newborn", "PostpartumVisit",
            # Phase 3.4 — the correction trail. One model over every maternity
            # clinical record, the consultation note's pattern (rule 45); not
            # a second audit system and not a clinical record of its own.
            "MaternityAmendment",
        })
        for absent in ("MaternityVitals", "MaternityLabTest", "MaternityUltrasound",
                       "MaternityBilling", "MaternityPatient", "MaternityAdmission",
                       "MaternityWard", "MaternityBed", "MaternityDischarge",
                       "MaternityNotification"):
            self.assertNotIn(absent, maternity_models)

    def test_the_encounter_hangs_off_the_hospital_s_own_visit(self):
        from apps.workflow.models import Visit

        pregnancy = self.pregnant()
        # `Visit` is the hospital's own attendance record — the department is
        # named on the `PatientRoute` raised from it, not here.
        visit = Visit.objects.create(patient=self.mother, opened_by=self.reception,
                                     reason="ANC")
        encounter = MaternityEncounter.objects.create(
            pregnancy=pregnancy, visit_type=self.followup, visit=visit,
            recorded_by=self.midwife)
        self.assertEqual(encounter.visit, visit)
        # And the triage taken at that visit is the hospital's own Vitals.
        from apps.clinical.models import Vitals
        import django.utils.timezone as tz
        Vitals.objects.create(patient=self.mother, recorded_by=self.midwife,
                              visit_time=tz.now(), bp_systolic=118, bp_diastolic=76,
                              temperature_c=36.8, heart_rate=84)
        self.assertEqual(Vitals.objects.filter(patient=self.mother).count(), 1)

    def test_a_maternity_charge_is_an_ordinary_charge(self):
        from apps.billing.services import add_charge

        self.pregnant()
        charge = add_charge(patient=self.mother, description="ANC consultation",
                            amount=Decimal("3000"), created_by=self.reception,
                            source_type="consultation")
        self.assertEqual(charge.department.code, "consultation")
        self.assertEqual(charge.settlement_status, "unpaid")
        self.assertEqual(self.mother.ledger.outstanding_balance, Decimal("3000"))

    def test_maternity_is_a_department_and_now_a_revenue_one(self):
        from apps.billing.departments import DEPARTMENT_CODES

        department = Department.objects.get(code="maternity")
        self.assertTrue(department.is_active)
        # **It is one now**, and that is the point of the assertion rather
        # than an exception to it. Maternity was kept out of the revenue
        # registry while the ward raised no charges of its own — "an entry
        # there would be a permanently empty column in every financial
        # report". The hospital has since configured the ward's price list
        # (`billing/0023`: the booking visit, the delivery package, the
        # postnatal check), so the column is not empty and the registry, which
        # is the subset of departments that takes money, now holds it.
        self.assertIn("maternity", DEPARTMENT_CODES)
        self.assertEqual(len(DEPARTMENT_CODES), 8)
        # And the ward's money is attributed to it rather than falling into
        # "Other / Unclassified", which is the whole reason it joined.
        from apps.billing.departments import department_for_source

        self.assertEqual(department_for_source("maternity").name, "Maternity")


class WhoMayWorkMaternity(Maternity):
    """Section 23: the existing RBAC, and no Django groups."""

    def test_the_desk_reads_the_lookup(self):
        for user in (self.reception, self.midwife, self.doctor):
            self.assertEqual(self.lookup(user).status_code, 200, user.role)

    def test_a_cashier_does_not(self):
        self.assertEqual(self.lookup(self.cashier).status_code, 403)

    def test_reception_reads_but_does_not_open_a_pregnancy(self):
        response = self.api(self.reception).post("/api/pregnancies/",
                                                 {"patient": self.mother.pk}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Pregnancy.objects.count(), 0)

    def test_reception_does_not_record_a_visit(self):
        pregnancy = self.pregnant()
        self.assertEqual(self.visit(pregnancy, self.followup, user=self.reception).status_code,
                         403)

    def test_only_an_admin_configures_the_visit_types(self):
        admin = User.objects.create_user(username="boss", password="t", role="admin")
        body = {"code": "twin-clinic", "name": "Twin clinic"}
        self.assertEqual(self.api(self.midwife).post("/api/maternity-visit-types/", body,
                                                     format="json").status_code, 403)
        self.assertEqual(self.api(admin).post("/api/maternity-visit-types/", body,
                                              format="json").status_code, 201)

    def test_an_anonymous_caller_reaches_nothing(self):
        for path in ("/api/maternity/lookup/", "/api/pregnancies/",
                     "/api/maternity-encounters/", "/api/maternity-visit-types/"):
            self.assertIn(APIClient().get(path).status_code, (401, 403), path)


class ConfigurationKeepsItsHistory(Maternity):
    """Section 24: deactivate, never destroy."""

    def test_a_visit_type_with_attendances_cannot_be_deleted(self):
        admin = User.objects.create_user(username="boss2", password="t", role="admin")
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.followup)
        response = self.api(admin).delete(f"/api/maternity-visit-types/{self.followup.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(MaternityVisitType.objects.filter(pk=self.followup.pk).exists())

    def test_an_unused_one_may_be_deleted(self):
        admin = User.objects.create_user(username="boss3", password="t", role="admin")
        spare = MaternityVisitType.objects.create(code="spare", name="Spare clinic")
        self.assertEqual(
            self.api(admin).delete(f"/api/maternity-visit-types/{spare.pk}/").status_code, 204)

    def test_retiring_one_keeps_the_visits_filed_under_it(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.followup)
        MaternityVisitType.objects.filter(pk=self.followup.pk).update(is_active=False)
        self.assertEqual(pregnancy.encounters.filter(visit_type=self.followup).count(), 1)


class ItIsAudited(Maternity):
    """Section 22's audit half: the existing AuditLog, not a second one."""

    def test_starting_a_pregnancy_is_audited(self):
        pregnancy = self.pregnant()
        entry = AuditLog.objects.filter(action="maternity.pregnancy_started").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.details["number"], pregnancy.number)

    def test_opening_a_visit_is_audited(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.followup)
        entry = AuditLog.objects.filter(action="maternity.encounter_opened").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.details["type"], "anc-followup")

    def test_closing_a_pregnancy_is_audited(self):
        pregnancy = self.pregnant()
        self.api(self.doctor).post(f"/api/pregnancies/{pregnancy.pk}/close/",
                                   {"outcome": "delivered"}, format="json")
        self.assertTrue(AuditLog.objects.filter(action="maternity.pregnancy_closed").exists())


class TheEdgeCasesYouNamed(Maternity):
    """Section 26, case by case."""

    def _close(self, pregnancy, outcome="delivered"):
        self.api(self.doctor).post(f"/api/pregnancies/{pregnancy.pk}/close/",
                                   {"outcome": outcome}, format="json")

    def test_case_1_active_pregnancy_and_an_anc_return(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.booking)
        self.assertTrue(self.lookup().data["can_continue"])
        self.visit(pregnancy, self.followup)
        self.assertEqual(Pregnancy.objects.count(), 1)
        self.assertEqual(pregnancy.encounters.count(), 2)

    def test_case_2_completed_pregnancy_and_no_active_one(self):
        first = self.pregnant()
        self._close(first)
        data = self.lookup().data
        self.assertTrue(data["can_start"])
        self.assertFalse(data["can_continue"])
        self.assertTrue(data["has_history"])

    def test_case_3_previous_and_new_pregnancy_both_preserved(self):
        first = self.pregnant()
        self.visit(first, self.booking)
        self._close(first)
        second = self.pregnant()
        self.visit(second, self.booking)

        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 2)
        self.assertEqual(first.encounters.count(), 1)
        self.assertEqual(second.encounters.count(), 1)
        self.assertEqual(active_pregnancy_for(self.mother), second)

    def test_case_4_labour_is_an_episode_not_an_anc_follow_up(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.followup)
        self.visit(pregnancy, self.labour)
        kinds = list(pregnancy.encounters.values_list("visit_type__code", flat=True))
        self.assertIn("labour-assessment", kinds)
        self.assertEqual(kinds.count("anc-followup"), 1)

    def test_case_5_an_emergency_is_its_own_encounter(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.emergency)
        self.assertEqual(pregnancy.encounters.first().visit_type.code, "maternity-emergency")

    def test_case_7_an_outstanding_bill_creates_no_duplicate_record(self):
        from apps.billing.services import add_charge

        pregnancy = self.pregnant()
        add_charge(patient=self.mother, description="ANC consultation",
                   amount=Decimal("3000"), created_by=self.reception,
                   source_type="consultation")
        # She comes back owing. Nothing about that forks her record.
        self.visit(pregnancy, self.followup)
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(Pregnancy.objects.count(), 1)
        self.assertEqual(self.mother.ledger.outstanding_balance, Decimal("3000"))

    def test_case_8_returning_after_delivery_is_postnatal_not_a_pregnancy(self):
        pregnancy = self.pregnant()
        self.visit(pregnancy, self.postnatal)
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 1)
        self.assertTrue(self.lookup().data["can_continue"])


class MaternityGrantsNothingElse(Maternity):
    """
    The boundary of `MATERNITY_ROLES`, written down so it cannot widen by
    accident.

    **Every nurse and every doctor in the hospital may work maternity**, and
    that is deliberate rather than an oversight: this HMIS has no midwife
    role — a midwife *is* a `nurse` here — and it is how every other unit
    already works (any nurse records vitals for any patient, any doctor writes
    a consultation note). What matters is that the group buys nothing beyond
    maternity, which is what the rest of this class holds.
    """

    def test_the_group_is_exactly_these_roles(self):
        """
        Written down so it cannot widen by accident. `maternity_nurse` is the
        midwife's own role (Phase 3) — the general nurse and the maternity
        doctor keep what they had, because a hospital this size covers
        maternity with whoever is on.
        """
        from apps.accounts.permissions import MATERNITY_DESK_ROLES, MATERNITY_ROLES

        self.assertEqual(MATERNITY_ROLES, ["doctor", "nurse", "maternity_nurse"])
        self.assertEqual(MATERNITY_DESK_ROLES,
                         ["doctor", "nurse", "maternity_nurse", "reception"])

    def test_the_midwife_is_the_one_role_maternity_added(self):
        """
        Phase 1 added none; Phase 3 added exactly one, deliberately, using the
        same enum every other role lives in — not a second RBAC and not a
        Django group.
        """
        from apps.accounts.models import Role

        roles = [value for value, _ in Role.choices]
        self.assertIn("maternity_nurse", roles)
        for invented in ("midwife", "maternity_doctor", "labour_nurse", "obstetrician"):
            self.assertNotIn(invented, roles)

    def test_every_other_role_is_refused_the_whole_surface(self):
        outsiders = ["pharmacist", "laboratory", "radiology", "cashier", "accountant",
                     "ward_manager", "inventory_manager", "optometrist", "ophthalmologist"]
        for role in outsiders:
            user = User.objects.create_user(username=f"x_{role}", password="t", role=role)
            client = self.api(user)
            self.assertEqual(client.get("/api/maternity/lookup/",
                                        {"patient": self.mother.patient_number}).status_code,
                             403, role)
            self.assertEqual(client.get("/api/pregnancies/").status_code, 403, role)
            self.assertEqual(client.get("/api/maternity-encounters/").status_code, 403, role)

    def test_a_maternity_nurse_gains_no_chart_access(self):
        """
        The group lets a nurse work maternity. It does not let her read a
        chart, which stays `ClinicalRecordAccess` (rule 10) — so nothing here
        widened what a nurse already reached.
        """
        response = self.api(self.midwife).get(f"/api/patients/{self.mother.uuid}/overview/")
        self.assertEqual(response.status_code, 403)

    def test_it_gains_no_money_no_stock_and_no_ward_board(self):
        client = self.api(self.midwife)
        self.assertEqual(client.get("/api/finance/report/").status_code, 403)
        self.assertEqual(client.get("/api/stock-records/").status_code, 403)
        self.assertEqual(client.post("/api/adjustments/", {}, format="json").status_code, 403)

    def test_reception_reads_maternity_and_writes_none_of_it(self):
        pregnancy = self.pregnant()
        client = self.api(self.reception)
        # Reads — the desk has to know which of the two situations this is.
        self.assertEqual(client.get("/api/maternity/lookup/",
                                    {"patient": self.mother.patient_number}).status_code, 200)
        self.assertEqual(client.get("/api/pregnancies/").status_code, 200)
        # And stops there. The timeline is the pregnancy's *visit list* —
        # a clinician's summary of each attendance — so it is the chart, not
        # the desk's, the same boundary rule 17 draws on a referral's notes.
        self.assertEqual(client.get(f"/api/pregnancies/{pregnancy.pk}/timeline/").status_code,
                         403)
        # Writes — none.
        self.assertEqual(client.post("/api/pregnancies/", {"patient": self.mother.pk},
                                     format="json").status_code, 403)
        self.assertEqual(client.post("/api/maternity-encounters/",
                                     {"pregnancy": pregnancy.pk,
                                      "visit_type": self.followup.pk},
                                     format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/pregnancies/{pregnancy.pk}/close/",
                                     {"outcome": "delivered"}, format="json").status_code, 403)

    def test_the_maternity_department_is_not_staffed_by_default(self):
        """
        Access is by role today, not by `Department.staff`. Narrowing it to
        the maternity department's own staff list is a decision that would
        strand the unit the day nobody remembered to fill the list in
        (rule 16's warning), so it is left as a deliberate, recorded choice.
        """
        self.assertEqual(Department.objects.get(code="maternity").staff.count(), 0)
        self.assertEqual(self.lookup(self.midwife).status_code, 200)
