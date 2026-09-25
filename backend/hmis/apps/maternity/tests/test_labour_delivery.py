"""
Phase 2: labour → delivery → newborn(s) → postpartum.

The rule that runs through all of it: **the pregnancy is the spine**. Labour
belongs to a pregnancy, the delivery to that labour, the babies to that
delivery. Twins are two `Newborn` rows — never two deliveries, and above all
never two pregnancies.
"""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification
from apps.inpatient.models import Admission, Bed, Ward
from apps.maternity.models import (Delivery, LabourEpisode, LabourObservation,
                                   MaternityOption, MaternityVisitType, Newborn,
                                   PostpartumVisit, Pregnancy)
from apps.maternity.services import MaternityError, open_labour, start_pregnancy
from apps.patients.models import Patient


class Labour(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.midwife = User.objects.create_user(username="mid", password="t", role="nurse",
                                                first_name="Ada", last_name="Bello")
        self.doctor = User.objects.create_user(username="obgyn", password="t", role="doctor",
                                               first_name="Femi", last_name="Okon")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.mother = Patient.objects.create(first_name="Mary", last_name="Jane", sex="F",
                                             created_by=self.reception)
        self.pregnancy = start_pregnancy(
            patient=self.mother, actor=self.midwife,
            lmp=datetime.date.today() - datetime.timedelta(weeks=39))
        self.svd = MaternityOption.objects.get(kind="delivery_type", code="svd")
        self.caesarean = MaternityOption.objects.get(kind="delivery_type",
                                                     code="caesarean-emergency")
        self.live_birth = MaternityOption.objects.get(kind="delivery_outcome", code="live-birth")
        self.pph = MaternityOption.objects.get(kind="complication", code="pph")
        self.well = MaternityOption.objects.get(kind="newborn_status", code="alive-well")
        self.stable = MaternityOption.objects.get(kind="mother_condition", code="stable")
        self.implant = MaternityOption.objects.get(kind="family_planning", code="implant")

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def labour(self, **kwargs):
        return open_labour(pregnancy=self.pregnancy, actor=self.midwife, **kwargs)

    def ward_bed(self, number="M01"):
        ward, _ = Ward.objects.get_or_create(name="Maternity Ward")
        return Bed.objects.create(ward=ward, number=number)

    def admit(self, bed=None):
        return Admission.objects.create(patient=self.mother, bed=bed or self.ward_bed(),
                                        admitted_by=self.midwife,
                                        attending_doctor=self.doctor)

    def deliver(self, labour=None, newborns=(), user=None, **extra):
        body = {"delivery_type": self.svd.pk, "outcome": self.live_birth.pk,
                "midwife": self.midwife.pk, "newborns": list(newborns), **extra}
        return self.api(user or self.midwife).post(
            f"/api/labour-episodes/{(labour or self.open_labour).pk}/delivery/",
            body, format="json")


class OpeningLabour(Labour):
    def test_a_labour_belongs_to_the_pregnancy_she_is_already_in(self):
        labour = self.labour()
        self.assertEqual(labour.pregnancy, self.pregnancy)
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 1)
        self.assertEqual(Patient.objects.count(), 1)

    def test_the_api_opens_one(self):
        response = self.api(self.midwife).post("/api/labour-episodes/", {
            "pregnancy": self.pregnancy.pk, "onset": "spontaneous"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "in_progress")
        self.assertEqual(response.data["stage"], "latent")
        self.assertEqual(response.data["patient_number"], self.mother.patient_number)

    def test_it_carries_the_gestation_she_is_at(self):
        response = self.api(self.midwife).post("/api/labour-episodes/", {
            "pregnancy": self.pregnancy.pk}, format="json")
        self.assertEqual(response.data["gestation"], "39w 0d")

    def test_a_second_open_labour_is_refused(self):
        self.labour()
        with self.assertRaises(MaternityError) as refusal:
            self.labour()
        self.assertEqual(refusal.exception.code, "labour_already_open")
        self.assertEqual(LabourEpisode.objects.count(), 1)

    def test_the_database_refuses_a_concurrent_second_one(self):
        from django.db import IntegrityError, transaction

        self.labour()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LabourEpisode.objects.create(pregnancy=self.pregnancy,
                                             started_at=timezone.now(), status="in_progress")

    def test_labour_cannot_be_opened_on_a_closed_pregnancy(self):
        self.api(self.doctor).post(f"/api/pregnancies/{self.pregnancy.pk}/close/",
                                   {"outcome": "delivered"}, format="json")
        response = self.api(self.midwife).post("/api/labour-episodes/", {
            "pregnancy": self.pregnancy.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "pregnancy_not_active")

    def test_the_anc_history_is_untouched_by_labour(self):
        followup = MaternityVisitType.objects.get(code="anc-followup")
        self.api(self.midwife).post("/api/maternity-encounters/", {
            "pregnancy": self.pregnancy.pk, "visit_type": followup.pk}, format="json")
        self.labour()
        self.assertEqual(self.pregnancy.encounters.count(), 1)


class LabourUsesTheHospitalsOwnAdmission(Labour):
    def test_the_ward_and_bed_are_read_through_the_admission(self):
        bed = self.ward_bed("M07")
        labour = self.labour(admission=self.admit(bed))
        self.assertEqual(labour.bed, bed)
        self.assertEqual(labour.ward.name, "Maternity Ward")

    def test_there_are_no_ward_or_bed_columns_on_the_labour(self):
        columns = {field.name for field in LabourEpisode._meta.get_fields()}
        self.assertNotIn("ward", columns)
        self.assertNotIn("bed", columns)

    def test_a_bed_transfer_moves_the_labour_with_it(self):
        admission = self.admit(self.ward_bed("M01"))
        labour = self.labour(admission=admission)
        moved_to = self.ward_bed("M02")
        admission.bed = moved_to
        admission.save(update_fields=["bed"])
        labour.refresh_from_db()
        self.assertEqual(labour.bed, moved_to)

    def test_the_api_links_an_admission_made_by_the_ward(self):
        labour = self.labour()
        admission = self.admit()
        response = self.api(self.midwife).post(f"/api/labour-episodes/{labour.pk}/admit/",
                                               {"admission": admission.pk}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["ward_name"], "Maternity Ward")
        self.assertEqual(response.data["bed_number"], "M01")

    def test_somebody_elses_admission_is_refused(self):
        other = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                       created_by=self.reception)
        theirs = Admission.objects.create(patient=other, bed=self.ward_bed("M09"),
                                          admitted_by=self.midwife)
        labour = self.labour()
        response = self.api(self.midwife).post(f"/api/labour-episodes/{labour.pk}/admit/",
                                               {"admission": theirs.pk}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "admission_patient_mismatch")

    def test_a_labour_with_no_admission_is_still_a_labour(self):
        labour = self.labour()
        self.assertIsNone(labour.admission)
        self.assertIsNone(labour.bed)


class ThePartogramIsRowsNotEdits(Labour):
    def _observe(self, labour, **readings):
        return self.api(self.midwife).post(
            f"/api/labour-episodes/{labour.pk}/observations/", readings, format="json")

    def test_each_check_is_its_own_row(self):
        labour = self.labour()
        self._observe(labour, cervical_dilation_cm=3, fetal_heart_rate=140)
        self._observe(labour, cervical_dilation_cm=6, fetal_heart_rate=138)
        self._observe(labour, cervical_dilation_cm=9, fetal_heart_rate=142)
        self.assertEqual(labour.observations.count(), 3)
        self.assertEqual(list(labour.observations.values_list("cervical_dilation_cm", flat=True)),
                         [3, 6, 9])

    def test_a_later_check_never_overwrites_an_earlier_one(self):
        labour = self.labour()
        first = LabourObservation.objects.get(
            pk=self._observe(labour, cervical_dilation_cm=3,
                             maternal_condition="Comfortable").data["id"])
        self._observe(labour, cervical_dilation_cm=7, maternal_condition="Tiring")
        first.refresh_from_db()
        self.assertEqual(first.cervical_dilation_cm, 3)
        self.assertEqual(first.maternal_condition, "Comfortable")

    def test_nothing_is_mandatory_and_a_blank_is_not_zero(self):
        labour = self.labour()
        observation = LabourObservation.objects.get(
            pk=self._observe(labour, fetal_heart_rate=140).data["id"])
        self.assertEqual(observation.fetal_heart_rate, 140)
        self.assertIsNone(observation.cervical_dilation_cm)
        self.assertIsNone(observation.contractions_per_10min)

    def test_the_stage_follows_the_dilation_forwards_only(self):
        labour = self.labour()
        self._observe(labour, cervical_dilation_cm=2)
        labour.refresh_from_db(); self.assertEqual(labour.stage, "latent")
        self._observe(labour, cervical_dilation_cm=5)
        labour.refresh_from_db(); self.assertEqual(labour.stage, "active")
        self._observe(labour, cervical_dilation_cm=10)
        labour.refresh_from_db(); self.assertEqual(labour.stage, "second")
        # A lower reading is measurement noise, not labour reversing.
        self._observe(labour, cervical_dilation_cm=4)
        labour.refresh_from_db(); self.assertEqual(labour.stage, "second")

    def test_observations_stop_once_the_labour_is_closed(self):
        labour = self.labour()
        self.open_labour = labour
        self.deliver(newborns=[{"sex": "F"}])
        response = self._observe(labour, cervical_dilation_cm=10)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "labour_not_open")

    def test_the_figures_are_the_hospitals_own_vitals(self):
        """No second set of observations: BP and pulse are `clinical.Vitals`."""
        from apps.clinical.models import Vitals

        labour = self.labour()
        vitals = Vitals.objects.create(patient=self.mother, recorded_by=self.midwife,
                                       visit_time=timezone.now(), bp_systolic=120,
                                       bp_diastolic=80, heart_rate=88)
        observation = LabourObservation.objects.create(
            labour=labour, observed_at=timezone.now(), vitals=vitals,
            recorded_by=self.midwife)
        self.assertEqual(observation.vitals.bp_systolic, 120)
        columns = {f.name for f in LabourObservation._meta.get_fields()}
        for absent in ("bp_systolic", "blood_pressure", "temperature_c"):
            self.assertNotIn(absent, columns)


class TheDelivery(Labour):
    def test_it_closes_the_labour_in_one_transaction(self):
        self.open_labour = self.labour()
        response = self.deliver(newborns=[{"sex": "M", "birth_weight_grams": 3200}])
        self.assertEqual(response.status_code, 201, response.data)
        self.open_labour.refresh_from_db()
        self.assertEqual(self.open_labour.status, "delivered")
        self.assertIsNotNone(self.open_labour.ended_at)
        self.assertEqual(self.open_labour.stage, "third")

    def test_one_delivery_per_labour(self):
        self.open_labour = self.labour()
        self.deliver(newborns=[{"sex": "F"}])
        again = self.deliver(newborns=[{"sex": "F"}])
        self.assertEqual(again.status_code, 400)
        self.assertIn(again.data["code"], ("labour_not_open", "delivery_already_recorded"))
        self.assertEqual(Delivery.objects.count(), 1)

    def test_it_cannot_be_created_on_its_own(self):
        response = self.api(self.midwife).post("/api/deliveries/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "use_labour_delivery")

    def test_it_records_who_what_and_the_complications(self):
        self.open_labour = self.labour()
        response = self.deliver(newborns=[{"sex": "F"}], doctor=self.doctor.pk,
                                complications=[self.pph.pk], estimated_blood_loss_ml=650,
                                placenta_complete=True)
        self.assertEqual(response.data["delivery_type_name"], "Normal vaginal delivery")
        self.assertEqual(response.data["outcome_name"], "Live birth")
        self.assertEqual(response.data["doctor_name"], "Femi Okon")
        self.assertEqual(response.data["midwife_name"], "Ada Bello")
        self.assertEqual(response.data["complication_names"], ["Postpartum haemorrhage"])
        self.assertEqual(response.data["estimated_blood_loss_ml"], 650)

    def test_a_delivery_type_is_required(self):
        self.open_labour = self.labour()
        response = self.api(self.midwife).post(
            f"/api/labour-episodes/{self.open_labour.pk}/delivery/",
            {"newborns": [{"sex": "F"}]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "delivery_type_required")
        self.assertEqual(Delivery.objects.count(), 0)

    def test_the_pregnancy_is_reached_through_the_labour_not_stored_again(self):
        self.open_labour = self.labour()
        delivery = Delivery.objects.get(pk=self.deliver(newborns=[{"sex": "F"}]).data["id"])
        self.assertEqual(delivery.pregnancy, self.pregnancy)
        self.assertEqual(delivery.patient, self.mother)
        self.assertNotIn("pregnancy", {f.name for f in Delivery._meta.get_fields()})

    def test_it_has_a_reference_of_its_own(self):
        self.open_labour = self.labour()
        delivery = Delivery.objects.get(pk=self.deliver(newborns=[{"sex": "F"}]).data["id"])
        self.assertEqual(delivery.reference, f"DEL-{delivery.pk:06d}")


class Twins(Labour):
    """
    The case the whole hierarchy is shaped around: **one pregnancy, one
    labour, one delivery, two babies.**
    """

    def setUp(self):
        super().setUp()
        self.open_labour = self.labour()

    def test_two_babies_are_two_newborns_on_one_delivery(self):
        response = self.deliver(newborns=[
            {"sex": "M", "birth_weight_grams": 2400, "apgar_1_min": 8, "apgar_5_min": 9},
            {"sex": "F", "birth_weight_grams": 2250, "apgar_1_min": 7, "apgar_5_min": 9},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data["newborns"]), 2)
        self.assertEqual(Delivery.objects.count(), 1)

    def test_and_never_a_second_pregnancy_or_delivery(self):
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}])
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 1)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(LabourEpisode.objects.count(), 1)
        self.assertEqual(Patient.objects.count(), 1)

    def test_birth_order_tells_baby_a_from_baby_b(self):
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}])
        babies = Newborn.objects.order_by("birth_order")
        self.assertEqual([b.birth_order for b in babies], [1, 2])
        self.assertEqual([b.sex for b in babies], ["M", "F"])

    def test_two_babies_cannot_share_a_birth_order(self):
        from django.db import IntegrityError, transaction

        delivery = Delivery.objects.get(
            pk=self.deliver(newborns=[{"sex": "M"}]).data["id"])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Newborn.objects.create(delivery=delivery, birth_order=1, sex="F")

    def test_a_babys_status_may_be_named_in_the_delivery_itself(self):
        """
        The nested `newborns` list carries ids like every other field, and the
        view resolves them. It used to hand the raw id to the model and answer
        **500** — found by walking a real delivery rather than by a test, which
        is why this one exists.
        """
        response = self.deliver(newborns=[
            {"sex": "F", "birth_weight_grams": 3150, "apgar_1_min": 9,
             "apgar_5_min": 10, "status": self.well.pk},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["newborns"][0]["status_name"], "Alive and well")

    def test_every_baby_of_a_multiple_birth_keeps_its_own_status(self):
        needs_care = MaternityOption.objects.get(kind="newborn_status",
                                                 code="alive-needs-care")
        response = self.deliver(newborns=[
            {"sex": "M", "status": self.well.pk},
            {"sex": "F", "status": needs_care.pk},
        ])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual([baby["status_name"] for baby in response.data["newborns"]],
                         ["Alive and well", "Alive — needs special care"])

    def test_a_status_from_the_wrong_list_is_ignored_rather_than_crashing(self):
        response = self.deliver(newborns=[{"sex": "F", "status": self.pph.pk}])
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["newborns"][0]["status"])

    def test_a_second_twin_can_be_added_afterwards(self):
        delivery = Delivery.objects.get(pk=self.deliver(newborns=[{"sex": "M"}]).data["id"])
        response = self.api(self.midwife).post(f"/api/deliveries/{delivery.pk}/newborns/",
                                               {"sex": "F", "birth_weight_grams": 2250,
                                                "status": self.well.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["birth_order"], 2)
        self.assertEqual(delivery.newborns.count(), 2)

    def test_triplets_work_the_same_way(self):
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}, {"sex": "F"}])
        self.assertEqual(Newborn.objects.count(), 3)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(Pregnancy.objects.count(), 1)

    def test_a_baby_is_not_a_patient_record(self):
        """Registering the newborn is the front desk's, through the existing form."""
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}])
        self.assertEqual(Patient.objects.count(), 1)
        self.assertTrue(all(baby.patient_id is None for baby in Newborn.objects.all()))

    def test_the_babies_read_back_off_the_birth_register(self):
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}])
        listed = self.api(self.midwife).get("/api/newborns/").data
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["mother_name"] == "Jane, Mary" for row in rows))


class Postpartum(Labour):
    def setUp(self):
        super().setUp()
        self.open_labour = self.labour()
        self.delivery = Delivery.objects.get(
            pk=self.deliver(newborns=[{"sex": "F"}]).data["id"])

    def _check(self, **findings):
        return self.api(self.midwife).post(
            f"/api/deliveries/{self.delivery.pk}/postpartum/", findings, format="json")

    def test_a_check_is_recorded_against_the_delivery(self):
        response = self._check(bleeding="Normal lochia", mother_condition=self.stable.pk,
                               breastfeeding_established=True)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["mother_condition_name"], "Stable")

    def test_postpartum_care_is_a_course_not_a_moment(self):
        self._check(bleeding="Heavy")
        self._check(bleeding="Settling")
        self._check(bleeding="Normal", family_planning=self.implant.pk)
        self.assertEqual(self.delivery.postpartum_visits.count(), 3)

    def test_an_earlier_check_is_never_overwritten(self):
        first = PostpartumVisit.objects.get(pk=self._check(bleeding="Heavy").data["id"])
        self._check(bleeding="Settling")
        first.refresh_from_db()
        self.assertEqual(first.bleeding, "Heavy")

    def test_it_stays_on_the_same_pregnancy(self):
        self._check(bleeding="Normal")
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 1)
        self.assertEqual(self.delivery.pregnancy, self.pregnancy)

    def test_her_blood_pressure_is_the_hospitals_vitals_not_a_column(self):
        from apps.clinical.models import Vitals

        vitals = Vitals.objects.create(patient=self.mother, recorded_by=self.midwife,
                                       visit_time=timezone.now(), bp_systolic=118,
                                       bp_diastolic=74)
        visit = PostpartumVisit.objects.create(delivery=self.delivery,
                                               seen_at=timezone.now(), vitals=vitals)
        self.assertEqual(visit.vitals.bp_diastolic, 74)
        columns = {f.name for f in PostpartumVisit._meta.get_fields()}
        self.assertNotIn("bp_systolic", columns)

    def test_family_planning_comes_from_the_configured_list(self):
        response = self._check(family_planning=self.implant.pk)
        self.assertEqual(response.data["family_planning_name"], "Implant")


class TheVocabularyIsConfiguration(Labour):
    def test_the_lists_are_rows_not_code(self):
        for kind in ("delivery_type", "delivery_outcome", "complication",
                     "newborn_status", "mother_condition", "family_planning"):
            self.assertTrue(MaternityOption.objects.filter(kind=kind, is_active=True).exists(),
                            kind)

    def test_an_admin_adds_one_without_a_deployment(self):
        admin = User.objects.create_user(username="boss", password="t", role="admin")
        response = self.api(admin).post("/api/maternity-options/", {
            "kind": "delivery_type", "code": "water-birth", "name": "Water birth"},
            format="json")
        self.assertEqual(response.status_code, 201, response.data)

    def test_maternity_staff_read_it_and_do_not_change_it(self):
        self.assertEqual(self.api(self.midwife).get("/api/maternity-options/").status_code, 200)
        self.assertEqual(self.api(self.midwife).post("/api/maternity-options/", {
            "kind": "complication", "code": "x", "name": "X"}, format="json").status_code, 403)

    def test_a_word_a_delivery_used_cannot_be_deleted(self):
        admin = User.objects.create_user(username="boss2", password="t", role="admin")
        self.open_labour = self.labour()
        self.deliver(newborns=[{"sex": "F"}])
        response = self.api(admin).delete(f"/api/maternity-options/{self.svd.pk}/")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(MaternityOption.objects.filter(pk=self.svd.pk).exists())

    def test_retiring_one_keeps_the_delivery_readable(self):
        self.open_labour = self.labour()
        delivery = Delivery.objects.get(pk=self.deliver(newborns=[{"sex": "F"}]).data["id"])
        MaternityOption.objects.filter(pk=self.svd.pk).update(is_active=False)
        delivery.refresh_from_db()
        self.assertEqual(delivery.delivery_type.name, "Normal vaginal delivery")

    def test_the_filter_narrows_to_one_list(self):
        rows = self.api(self.midwife).get("/api/maternity-options/",
                                          {"kind": "delivery_type"}).data
        rows = rows["results"] if isinstance(rows, dict) else rows
        self.assertTrue(all(row["kind"] == "delivery_type" for row in rows))


class ItReusesTheHospital(Labour):
    def test_maternity_still_owns_only_its_own_models(self):
        from django.apps import apps as django_apps

        names = {m.__name__ for m in django_apps.get_app_config("maternity").get_models()}
        self.assertEqual(names, {"MaternityVisitType", "Pregnancy", "MaternityEncounter",
                                 "MaternityOption", "LabourEpisode", "LabourObservation",
                                 "Delivery", "Newborn", "PostpartumVisit",
                                 # The correction trail — one model over every
                                 # maternity record, not a record of its own.
                                 "MaternityAmendment"})
        for absent in ("MaternityVitals", "MaternityBilling", "MaternityAdmission",
                       "MaternityWard", "MaternityBed", "MaternityNotification",
                       "MaternityLabTest", "MaternityPatient", "MaternityDischarge"):
            self.assertNotIn(absent, names)

    def test_a_delivery_charge_is_an_ordinary_charge(self):
        from apps.billing.services import add_charge

        self.open_labour = self.labour()
        self.deliver(newborns=[{"sex": "F"}])
        charge = add_charge(patient=self.mother, description="Delivery package",
                            amount=Decimal("45000"), created_by=self.reception,
                            source_type="procedure")
        self.assertEqual(charge.department.code, "theatre")
        self.assertEqual(charge.settlement_status, "unpaid")
        self.assertEqual(
            Patient.objects.get(pk=self.mother.pk).ledger.outstanding_balance,
            Decimal("45000"))

    def test_discharge_is_the_hospitals_own(self):
        from apps.inpatient.services import discharge_patient

        admission = self.admit()
        labour = self.labour(admission=admission)
        self.open_labour = labour
        self.deliver(newborns=[{"sex": "F"}])
        summary = discharge_patient(admission=admission, actor=self.doctor,
                                    diagnosis="Term delivery", summary="Mother and baby well",
                                    notify=False)
        admission.refresh_from_db()
        self.assertEqual(admission.status, "discharged")
        self.assertTrue(summary.reference.startswith("DCH-"))


class ItIsAuditedAndAnnounced(Labour):
    def test_every_step_writes_an_audit_row(self):
        labour = self.labour()
        self.open_labour = labour
        self.api(self.midwife).post(f"/api/labour-episodes/{labour.pk}/observations/",
                                    {"cervical_dilation_cm": 5}, format="json")
        delivery = Delivery.objects.get(pk=self.deliver(newborns=[{"sex": "F"}]).data["id"])
        self.api(self.midwife).post(f"/api/deliveries/{delivery.pk}/newborns/",
                                    {"sex": "M"}, format="json")
        self.api(self.midwife).post(f"/api/deliveries/{delivery.pk}/postpartum/",
                                    {"bleeding": "Normal"}, format="json")
        for action in ("maternity.labour_opened", "maternity.delivery_recorded",
                       "maternity.newborn_recorded", "maternity.postpartum_recorded"):
            self.assertTrue(AuditLog.objects.filter(action=action).exists(), action)

    def test_the_bell_reaches_whoever_is_holding_her_and_not_the_hospital(self):
        """Rule 14: the clinicians on this pregnancy, never every nurse."""
        followup = MaternityVisitType.objects.get(code="anc-followup")
        self.api(self.doctor).post("/api/maternity-encounters/", {
            "pregnancy": self.pregnancy.pk, "visit_type": followup.pk}, format="json")
        stranger = User.objects.create_user(username="other_nurse", password="t", role="nurse")

        self.labour()
        told = set(Notification.objects.values_list("recipient_id", flat=True))
        self.assertIn(self.doctor.pk, told)          # she has seen him this pregnancy
        self.assertNotIn(stranger.pk, told)          # he has never met her
        self.assertNotIn(self.midwife.pk, told)      # she opened it herself

    def test_the_delivery_is_announced_with_how_many_babies(self):
        followup = MaternityVisitType.objects.get(code="anc-followup")
        self.api(self.doctor).post("/api/maternity-encounters/", {
            "pregnancy": self.pregnancy.pk, "visit_type": followup.pk}, format="json")
        self.open_labour = self.labour()
        Notification.objects.all().delete()
        self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}])
        note = Notification.objects.filter(recipient=self.doctor).first()
        self.assertIsNotNone(note)
        self.assertIn("Delivered", note.title)
        self.assertIn("2", note.message)
        self.assertEqual(note.action_url, "/maternity")


class WhoMayWorkIt(Labour):
    def test_the_desk_reads_none_of_it_and_writes_none_of_it(self):
        """
        The labour, the partogram, the delivery and the birth register are the
        clinical record, and the front desk reads no chart. It used to read
        every one of them — a partogram on the reception screen — which the
        maternity access work closed.
        """
        labour = self.labour()
        client = self.api(self.reception)
        self.assertEqual(client.get("/api/labour-episodes/").status_code, 403)
        self.assertEqual(client.get("/api/deliveries/").status_code, 403)
        self.assertEqual(client.get("/api/newborns/").status_code, 403)
        self.assertEqual(client.get(f"/api/labour-episodes/{labour.pk}/").status_code, 403)
        self.assertEqual(client.post("/api/labour-episodes/",
                                     {"pregnancy": self.pregnancy.pk},
                                     format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/labour-episodes/{labour.pk}/observations/",
                                     {"cervical_dilation_cm": 4},
                                     format="json").status_code, 403)

    def test_what_the_desk_does_keep(self):
        """Who she is, and whether she is pregnant. Its whole job here."""
        client = self.api(self.reception)
        self.assertEqual(client.get("/api/maternity/lookup/",
                                    {"patient": self.mother.patient_number}).status_code, 200)
        self.assertEqual(client.get("/api/maternity/patients/").status_code, 200)

    def test_an_unrelated_role_reaches_none_of_it(self):
        for role in ("cashier", "pharmacist", "laboratory", "radiology"):
            user = User.objects.create_user(username=f"x_{role}", password="t", role=role)
            client = self.api(user)
            for path in ("/api/labour-episodes/", "/api/deliveries/", "/api/newborns/",
                         "/api/maternity-options/"):
                self.assertEqual(client.get(path).status_code, 403, (role, path))

    def test_an_anonymous_caller_reaches_none_of_it(self):
        for path in ("/api/labour-episodes/", "/api/deliveries/", "/api/newborns/",
                     "/api/maternity-options/"):
            self.assertIn(APIClient().get(path).status_code, (401, 403), path)


class HistoryStaysIntact(Labour):
    def test_a_later_pregnancy_leaves_the_first_delivery_alone(self):
        self.open_labour = self.labour()
        first_delivery = Delivery.objects.get(
            pk=self.deliver(newborns=[{"sex": "M"}, {"sex": "F"}]).data["id"])
        self.api(self.doctor).post(f"/api/pregnancies/{self.pregnancy.pk}/close/",
                                   {"outcome": "delivered"}, format="json")

        second = start_pregnancy(patient=self.mother, actor=self.midwife)
        open_labour(pregnancy=second, actor=self.midwife)

        first_delivery.refresh_from_db()
        self.assertEqual(first_delivery.newborns.count(), 2)
        self.assertEqual(first_delivery.labour.pregnancy, self.pregnancy)
        self.assertEqual(Pregnancy.objects.filter(patient=self.mother).count(), 2)
        self.assertEqual(Patient.objects.count(), 1)

    def test_the_labour_detail_carries_the_whole_partogram_and_the_delivery(self):
        labour = self.labour()
        self.open_labour = labour
        for dilation in (3, 6, 10):
            self.api(self.midwife).post(f"/api/labour-episodes/{labour.pk}/observations/",
                                        {"cervical_dilation_cm": dilation}, format="json")
        self.deliver(newborns=[{"sex": "F"}])
        detail = self.api(self.midwife).get(f"/api/labour-episodes/{labour.pk}/").data
        self.assertEqual(len(detail["observations"]), 3)
        self.assertEqual(detail["delivery"]["delivery_type_name"], "Normal vaginal delivery")
        self.assertEqual(len(detail["delivery"]["newborns"]), 1)
