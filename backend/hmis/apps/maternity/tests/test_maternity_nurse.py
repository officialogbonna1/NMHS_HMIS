"""
The maternity nurse — her own role, her own dashboard, and nothing else.

Phase 1 recorded a known breadth: `MATERNITY_ROLES` was doctor + nurse, so
every nurse in the hospital could work maternity. `maternity_nurse` is the
midwife named, so the labour ward is hers — and this file is what holds the
"and nothing else".
"""
import datetime
from decimal import Decimal

from django.contrib.auth import authenticate
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.clinical.models import Vitals
from apps.maternity.models import (Delivery, LabourEpisode, MaternityOption,
                                   MaternityVisitType, Newborn, Pregnancy)
from apps.maternity.services import open_labour, record_delivery, start_pregnancy
from apps.patients.models import Patient


class Midwifery(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.midwife = User.objects.create_user(username="grace", password="t",
                                                role="maternity_nurse",
                                                first_name="Grace", last_name="Nwosu")
        self.nurse = User.objects.create_user(username="nia", password="t", role="nurse")
        self.doctor = User.objects.create_user(username="femi", password="t", role="doctor")
        self.mother = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                             created_by=self.reception)
        self.pregnancy = start_pregnancy(
            patient=self.mother, actor=self.midwife,
            lmp=datetime.date.today() - datetime.timedelta(weeks=38))
        self.svd = MaternityOption.objects.get(kind="delivery_type", code="svd")
        self.well = MaternityOption.objects.get(kind="newborn_status", code="alive-well")

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class TheRoleExists(Midwifery):
    def test_it_is_in_the_hospitals_role_list(self):
        self.assertIn(("maternity_nurse", "Maternity"), Role.choices)

    def test_django_admin_offers_it_on_the_user_form(self):
        """
        `role` is a model field with choices, so both admin forms carry it —
        which is how an administrator creates a midwife without editing code.
        """
        from django.contrib.admin.sites import site

        admin = site._registry[User]
        self.assertIn("role", admin.get_form(None)().fields)
        field = User._meta.get_field("role")
        self.assertIn("maternity_nurse", dict(field.choices))

    def test_she_authenticates_like_any_other_member_of_staff(self):
        """No second login path: Django's own authentication, one account."""
        self.midwife.set_password("a-throwaway-value")
        self.midwife.save()
        self.assertIsNotNone(authenticate(username="grace", password="a-throwaway-value"))
        self.assertIsNone(authenticate(username="grace", password="wrong"))

    def test_auth_me_reports_her_role(self):
        response = self.api(self.midwife).get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "maternity_nurse")

    def test_she_is_numbered_like_every_other_member_of_staff(self):
        self.midwife.refresh_from_db()
        self.assertTrue(self.midwife.staff_number.startswith("NMHS-S"))


class WhatSheMayDo(Midwifery):
    def test_she_finds_a_mother(self):
        response = self.api(self.midwife).get("/api/maternity/lookup/",
                                              {"patient": self.mother.patient_number})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_pregnancy"]["number"], 1)

    def test_she_reads_the_patient_list(self):
        self.assertEqual(self.api(self.midwife).get("/api/patients/").status_code, 200)

    def test_she_works_the_whole_maternity_record(self):
        client = self.api(self.midwife)
        followup = MaternityVisitType.objects.get(code="anc-followup")

        # ANC
        self.assertEqual(client.post("/api/maternity-encounters/",
                                     {"pregnancy": self.pregnancy.pk,
                                      "visit_type": followup.pk},
                                     format="json").status_code, 201)
        # Labour
        labour = client.post("/api/labour-episodes/", {"pregnancy": self.pregnancy.pk},
                             format="json")
        self.assertEqual(labour.status_code, 201)
        labour_id = labour.data["id"]
        # Partogram
        self.assertEqual(client.post(f"/api/labour-episodes/{labour_id}/observations/",
                                     {"cervical_dilation_cm": 5},
                                     format="json").status_code, 201)
        # Delivery + baby
        delivery = client.post(f"/api/labour-episodes/{labour_id}/delivery/",
                               {"delivery_type": self.svd.pk,
                                "newborns": [{"sex": "F", "status": self.well.pk}]},
                               format="json")
        self.assertEqual(delivery.status_code, 201, delivery.data)
        # Postpartum
        self.assertEqual(client.post(f"/api/deliveries/{delivery.data['id']}/postpartum/",
                                     {"bleeding": "Normal"}, format="json").status_code, 201)

    def test_she_reads_the_ward_and_bed_through_the_labour(self):
        """No admissions endpoint needed: the labour row carries where she lies."""
        from apps.inpatient.models import Admission, Bed, Ward

        ward = Ward.objects.create(name="Maternity Ward")
        bed = Bed.objects.create(ward=ward, number="M01")
        admission = Admission.objects.create(patient=self.mother, bed=bed,
                                             admitted_by=self.doctor)
        labour = open_labour(pregnancy=self.pregnancy, actor=self.midwife)
        self.api(self.midwife).post(f"/api/labour-episodes/{labour.pk}/admit/",
                                    {"admission": admission.pk}, format="json")
        row = self.api(self.midwife).get(f"/api/labour-episodes/{labour.pk}/").data
        self.assertEqual(row["ward_name"], "Maternity Ward")
        self.assertEqual(row["bed_number"], "M01")

    def test_she_reads_the_configured_vocabulary_and_does_not_change_it(self):
        client = self.api(self.midwife)
        self.assertEqual(client.get("/api/maternity-options/").status_code, 200)
        self.assertEqual(client.post("/api/maternity-options/",
                                     {"kind": "complication", "code": "x", "name": "X"},
                                     format="json").status_code, 403)


class WhatSheMayNotDo(Midwifery):
    """The boundary, written down so it cannot widen by accident."""

    def test_she_reaches_no_chart(self):
        response = self.api(self.midwife).get(f"/api/patients/{self.mother.uuid}/overview/")
        self.assertEqual(response.status_code, 403)

    def test_she_reaches_no_money(self):
        client = self.api(self.midwife)
        for path in ("/api/finance/report/", "/api/charges/", "/api/payments/",
                     "/api/adjustments/", "/api/refunds/", "/api/ledgers/"):
            self.assertEqual(client.get(path).status_code, 403, path)

    def test_the_appointments_queue_is_hers_and_holds_nothing_of_anybody_elses(self):
        """
        `/api/appointments/` opened to her when the hospital configured
        Maternity's price list: `booking._provider_roles()` is "every role a
        bookable category maps to", and Maternity became a billing category
        while `PURPOSE_ROLE["maternity"]` already named the midwife. That is
        the module's stated rule — "it grows when the hospital gains a category
        and a role for it" — and an ANC follow-up booked with a named midwife
        is a real thing a maternity hospital does.

        **It is not a widening of what she sees.** A provider's queryset is
        `filter(doctor=user)`, so the list is the appointments queued to her
        and nobody else's, and she still cannot create, edit or delete one.
        """
        client = self.api(self.midwife)
        answer = client.get("/api/appointments/")
        self.assertEqual(answer.status_code, 200)
        rows = answer.data.get("results", answer.data)
        self.assertEqual(rows, [], "she is seeing somebody else's appointments")
        # Booking is still reception's, and editing still an administrator's.
        self.assertEqual(client.post("/api/appointments/", {}, format="json").status_code,
                         403)

    def test_she_cannot_raise_waive_or_refund_anything(self):
        from apps.billing.services import add_charge

        charge = add_charge(patient=self.mother, description="Delivery package",
                            amount=Decimal("45000"), created_by=self.reception,
                            source_type="procedure")
        client = self.api(self.midwife)
        self.assertEqual(client.post("/api/charges/", {"patient": self.mother.pk,
                                                       "description": "x", "amount": "1"},
                                     format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/charges/{charge.pk}/waive/",
                                     {"amount": "1", "reason": "x"},
                                     format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/charges/{charge.pk}/cancel/",
                                     {"reason": "x"}, format="json").status_code, 403)

    def test_she_reaches_no_other_department(self):
        client = self.api(self.midwife)
        # `/api/appointments/` is deliberately absent: it opened to her when
        # Maternity gained a price list, and the test above holds the boundary
        # that matters — she reads her own queue and nobody else's.
        for path in ("/api/lab-orders/", "/api/prescriptions/", "/api/items/",
                     "/api/stock-records/", "/api/sales/"):
            self.assertEqual(client.get(path).status_code, 403, path)

    def test_she_cannot_delete_a_patient(self):
        response = self.api(self.midwife).delete(f"/api/patients/{self.mother.uuid}/",
                                                 {"confirm": self.mother.patient_number},
                                                 format="json")
        self.assertIn(response.status_code, (403, 405))
        self.assertTrue(Patient.objects.filter(pk=self.mother.pk).exists())

    def test_she_has_no_django_admin_access(self):
        self.assertFalse(self.midwife.is_staff)
        self.assertFalse(self.midwife.is_superuser)
        self.assertFalse(self.midwife.is_admin)

    def test_she_reads_back_no_reading_for_a_patient_who_is_not_hers(self):
        """
        She records vitals now — that is deliberate, and the whole of what the
        triage work added (`NURSING_ROLES`). What she still may not do is read
        the hospital's readings: the queryset is `patient_queryset_for`, so a
        patient with no maternity connection is not on her list and a reading
        taken for one is not hers to see.

        `test_maternity_triage.py` holds the recording side; this holds the
        boundary that did not move.
        """
        from django.utils import timezone

        elsewhere = Patient.objects.create(first_name="Musa", last_name="Bello",
                                           sex="M", created_by=self.reception)
        Vitals.objects.create(patient=elsewhere, recorded_by=self.nurse,
                              visit_time=timezone.now(), bp_systolic=120,
                              bp_diastolic=80)
        rows = self.api(self.midwife).get("/api/vitals/",
                                          {"patient": elsewhere.pk}).data
        self.assertEqual(rows.get("results", rows), [])


class HerDashboard(Midwifery):
    def test_it_is_the_labour_ward_and_not_the_triage_queue(self):
        cards = self.api(self.midwife).get("/api/dashboard/").data["cards"]
        keys = [card["key"] for card in cards]
        self.assertIn("maternity_active_pregnancies", keys)
        self.assertIn("maternity_in_labour", keys)
        # A midwife is never routed a patient, so the vitals cards would read
        # zero forever — the reason the cash desk got its own set too.
        self.assertNotIn("vitals_waiting", keys)

    def test_every_card_opens_the_page_it_counts(self):
        cards = self.api(self.midwife).get("/api/dashboard/").data["cards"]
        for card in cards:
            self.assertIn(card["href"], ("/maternity", "/notifications"), card["key"])

    def test_the_counts_are_the_records_themselves(self):
        labour = open_labour(pregnancy=self.pregnancy, actor=self.midwife)
        record_delivery(labour=labour, actor=self.midwife, delivery_type=self.svd,
                        newborns=[{"sex": "M"}, {"sex": "F"}])
        cards = {card["key"]: card["value"]
                 for card in self.api(self.midwife).get("/api/dashboard/").data["cards"]}
        self.assertEqual(cards["maternity_deliveries_today"], 1)
        # Twins count two babies, not one delivery.
        self.assertEqual(cards["maternity_newborns_week"], 2)

    def test_a_general_nurse_keeps_the_vitals_dashboard(self):
        keys = [card["key"] for card in self.api(self.nurse).get("/api/dashboard/").data["cards"]]
        self.assertIn("vitals_waiting", keys)
        self.assertNotIn("maternity_in_labour", keys)


class MultipleBirths(Midwifery):
    """
    One pregnancy → one labour → one delivery → **N newborns**, whatever N is.
    No Twin model, no second pregnancy, no second labour.
    """

    def _deliver(self, babies):
        labour = open_labour(pregnancy=self.pregnancy, actor=self.midwife)
        return self.api(self.midwife).post(
            f"/api/labour-episodes/{labour.pk}/delivery/",
            {"delivery_type": self.svd.pk, "newborns": babies}, format="json")

    def _counts(self):
        return (Pregnancy.objects.filter(patient=self.mother).count(),
                LabourEpisode.objects.count(), Delivery.objects.count(),
                Newborn.objects.count())

    def test_a_singleton(self):
        self._deliver([{"sex": "F"}])
        self.assertEqual(self._counts(), (1, 1, 1, 1))

    def test_twins(self):
        self._deliver([{"sex": "M"}, {"sex": "F"}])
        self.assertEqual(self._counts(), (1, 1, 1, 2))

    def test_triplets(self):
        self._deliver([{"sex": "M"}, {"sex": "F"}, {"sex": "F"}])
        self.assertEqual(self._counts(), (1, 1, 1, 3))

    def test_quadruplets_because_nothing_caps_it_at_two(self):
        self._deliver([{"sex": "M"}, {"sex": "F"}, {"sex": "F"}, {"sex": "M"}])
        self.assertEqual(self._counts(), (1, 1, 1, 4))
        self.assertEqual(list(Newborn.objects.order_by("birth_order")
                              .values_list("birth_order", flat=True)), [1, 2, 3, 4])

    def test_each_baby_keeps_its_own_status(self):
        needs_care = MaternityOption.objects.get(kind="newborn_status",
                                                 code="alive-needs-care")
        resus = MaternityOption.objects.get(kind="newborn_status", code="resuscitated")
        response = self._deliver([
            {"sex": "M", "status": self.well.pk, "birth_weight_grams": 2400},
            {"sex": "F", "status": needs_care.pk, "birth_weight_grams": 2100},
            {"sex": "F", "status": resus.pk, "birth_weight_grams": 1950},
        ])
        self.assertEqual([baby["status_name"] for baby in response.data["newborns"]],
                         ["Alive and well", "Alive — needs special care", "Resuscitated"])
        self.assertEqual([baby["birth_weight_grams"] for baby in response.data["newborns"]],
                         [2400, 2100, 1950])

    def test_recording_baby_two_does_not_overwrite_baby_one(self):
        delivery = Delivery.objects.get(
            pk=self._deliver([{"sex": "M", "birth_weight_grams": 2400,
                               "apgar_1_min": 8}]).data["id"])
        first = delivery.newborns.get(birth_order=1)
        self.api(self.midwife).post(f"/api/deliveries/{delivery.pk}/newborns/",
                                    {"sex": "F", "birth_weight_grams": 2250,
                                     "apgar_1_min": 6}, format="json")
        first.refresh_from_db()
        self.assertEqual(first.birth_weight_grams, 2400)
        self.assertEqual(first.apgar_1_min, 8)
        self.assertEqual(delivery.newborns.count(), 2)

    def test_a_duplicate_birth_order_is_refused_and_leaves_no_orphan(self):
        from django.db import IntegrityError, transaction

        delivery = Delivery.objects.get(pk=self._deliver([{"sex": "M"}]).data["id"])
        before = Newborn.objects.count()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Newborn.objects.create(delivery=delivery, birth_order=1, sex="F")
        self.assertEqual(Newborn.objects.count(), before)

    def test_a_failed_delivery_leaves_no_partial_record(self):
        """The whole thing is one transaction (rule 56)."""
        labour = open_labour(pregnancy=self.pregnancy, actor=self.midwife)
        response = self.api(self.midwife).post(
            f"/api/labour-episodes/{labour.pk}/delivery/",
            {"newborns": [{"sex": "F"}, {"sex": "M"}]}, format="json")   # no delivery type
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertEqual(Newborn.objects.count(), 0)
        labour.refresh_from_db()
        self.assertEqual(labour.status, "in_progress")

    def test_the_postpartum_course_stays_the_mothers_however_many_babies(self):
        delivery = Delivery.objects.get(
            pk=self._deliver([{"sex": "M"}, {"sex": "F"}, {"sex": "F"}]).data["id"])
        for bleeding in ("Heavy", "Settling"):
            self.api(self.midwife).post(f"/api/deliveries/{delivery.pk}/postpartum/",
                                        {"bleeding": bleeding}, format="json")
        # Two checks on one mother — not one per baby.
        self.assertEqual(delivery.postpartum_visits.count(), 2)

    def test_no_twin_or_multiple_birth_model_was_invented(self):
        from django.apps import apps as django_apps

        names = {m.__name__ for m in django_apps.get_app_config("maternity").get_models()}
        for absent in ("Twin", "Triplet", "MultipleBirth", "BirthGroup"):
            self.assertNotIn(absent, names)
