"""
Order → bill → the cash desk is told → the patient pays → the unit sees PAID.

The hospital's workflow is unchanged and none of its parts are new: the
charge is `billing.services.add_charge`, the money is `record_payment`, the
bell is `core.services.notify` and the queue is `PatientRoute`. What these
tests hold is that the *round trip closes* — that a department can read where
the money stands without leaving its own worklist, and that what it reads is
the charge itself rather than a second copy of the arithmetic.

Four statements, and every test here is one of them:

1. **One authoritative status.** Every department reads `Charge
   .settlement_status` through `billing/status.py`. Nothing computes a
   balance of its own, and a waiver is never shown as an unpaid bill.
2. **Each service keeps its own.** A patient who settles the FBC and not the
   malaria test has paid for one of them, and both say so.
3. **Both directions are announced once.** The cash desk hears that a bill
   was raised (rule 35); the unit holding the patient hears when it clears —
   and nobody else does either time.
4. **It is a reading, never a gate** (rules 24 and 51). An unpaid request is
   accepted, started and completed in exactly the calls it always was. The
   brief asked for a check before a department may proceed "where the
   hospital workflow requires payment"; here it does not, so nothing refuses
   and `TheUnitIsNeverGatedByTheMoney` holds that it stays that way.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing import status as billing_status
from apps.billing.models import BillingItem, Charge
from apps.billing.services import (add_charge, apply_percentage_discount, record_payment,
                                   waive_charge)
from apps.core.models import AuditLog, Notification
from apps.departments.models import Department
from apps.laboratory.models import LabOrder
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, RouteService, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class Hospital(TestCase):
    """One patient, a doctor who orders, a bench, a scanner room and a till."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier",
                                                first_name="Tolu", last_name="Bello")
        self.accountant = User.objects.create_user(username="acct", password="t", role="accountant")
        self.admin = User.objects.create_user(username="adm", password="t", role="admin")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor",
                                               first_name="Chidi", last_name="Nwosu")
        self.scientist = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.radiographer = User.objects.create_user(username="rad", password="t", role="radiology",
                                                     first_name="Bisi", last_name="Ade")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")

        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                              created_by=self.reception)
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)
        self.imaging = Department.objects.get(code="radiology")
        self.laboratory = Department.objects.get(code="laboratory")
        self.abdominal = BillingItem.objects.create(category="ultrasound", price=Decimal("8000"),
                                                    name="Abdominal Ultrasound (test)")
        self.doppler = BillingItem.objects.create(category="ultrasound", price=Decimal("12000"),
                                                  name="Doppler (test)")

    # --- helpers ---------------------------------------------------------

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def scan_referral(self, *items, assigned_to=None):
        """A doctor refers for imaging and names the examinations, which is
        what raises the charges (rule 51)."""
        route = PatientRoute.objects.create(
            visit=self.visit, department=self.imaging, purpose="ultrasound",
            routed_by=self.doctor, assigned_to=assigned_to, notes="RUQ pain")
        if items:
            response = self.as_(self.doctor).post(
                f"/api/patient-routes/{route.pk}/request-services/",
                {"services": [item.pk for item in items]}, format="json")
            self.assertIn(response.status_code, (200, 201), response.data)
        route.refresh_from_db()
        return route

    def lab_referral(self, *codes):
        route = PatientRoute.objects.create(
            visit=self.visit, department=self.laboratory, purpose="laboratory",
            routed_by=self.doctor, notes="Fever, query malaria")
        order = self.as_(self.doctor).post("/api/lab-orders/for-route/",
                                           {"route": route.pk}, format="json").data
        if codes:
            self.as_(self.doctor).post(f"/api/lab-orders/{order['id']}/add-tests/",
                                       {"test_codes": list(codes)}, format="json")
        return LabOrder.objects.get(pk=order["id"]), route

    def route_row(self, user, route):
        """The queue row this user actually sees."""
        response = self.as_(user).get("/api/patient-routes/", {"page_size": 200})
        self.assertEqual(response.status_code, 200, response.data)
        return next(row for row in rows(response) if row["id"] == route.pk)


# =====================================================================
# 1. One authoritative status
# =====================================================================

class TheStatusComesFromTheChargeAndNowhereElse(Hospital):
    def test_the_status_module_reads_the_charge_rather_than_recomputing_it(self):
        charge = add_charge(patient=self.patient, description="Ultrasound: Abdominal",
                            amount=Decimal("8000"), created_by=self.doctor,
                            source_type="ultrasound")
        block = billing_status.service_billing(charge)
        self.assertEqual(block["status"], charge.settlement_status)
        self.assertEqual(block["label"], "UNPAID")
        self.assertTrue(block["requires_payment"])
        self.assertEqual(block["outstanding"], "8000.00")

    def test_unpaid_partial_paid_are_the_charge_s_own_arithmetic(self):
        route = self.scan_referral(self.abdominal)
        charge = route.services.get().charge

        self.assertEqual(billing_status.service_billing(charge)["label"], "UNPAID")

        record_payment(patient=self.patient, amount=Decimal("5000"), received_by=self.cashier)
        charge.refresh_from_db()
        block = billing_status.service_billing(charge)
        self.assertEqual(block["label"], "PARTIALLY PAID")
        self.assertEqual(block["paid"], "5000.00")
        self.assertEqual(block["outstanding"], "3000.00")
        self.assertTrue(block["requires_payment"])

        record_payment(patient=self.patient, amount=Decimal("3000"), received_by=self.cashier)
        charge.refresh_from_db()
        block = billing_status.service_billing(charge)
        self.assertEqual(block["label"], "PAID")
        self.assertEqual(block["outstanding"], "0.00")
        self.assertFalse(block["requires_payment"])

    def test_a_waived_service_is_never_shown_as_an_unpaid_bill(self):
        """Rule 14 of the brief, and the whole reason a waiver has its own
        column: "UNPAID ₦8,000" against a bill nobody will collect sends the
        patient to a counter that has nothing to take."""
        route = self.scan_referral(self.abdominal)
        charge = route.services.get().charge
        waive_charge(charge=charge, reason="Staff dependant", approved_by=self.accountant)
        charge.refresh_from_db()

        block = billing_status.service_billing(charge)
        self.assertEqual(block["status"], "waived")
        self.assertEqual(block["label"], "NO PAYMENT REQUIRED")
        self.assertFalse(block["requires_payment"])
        self.assertEqual(block["outstanding"], "0.00")

    def test_a_discount_moves_the_payable_and_the_status_follows_it(self):
        """Rule 14: the status is judged on what is actually due, not the
        face value."""
        charge = add_charge(patient=self.patient, description="Ultrasound: Doppler",
                            amount=Decimal("10000"), created_by=self.doctor,
                            source_type="ultrasound")
        apply_percentage_discount(charge=charge, percent=Decimal("20"), reason="Hardship",
                                  approved_by=self.accountant)
        charge.refresh_from_db()
        block = billing_status.service_billing(charge)
        self.assertEqual(block["amount"], "10000.00")
        self.assertEqual(block["discounted"], "2000.00")
        self.assertEqual(block["payable"], "8000.00")
        self.assertEqual(block["outstanding"], "8000.00")

        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        charge.refresh_from_db()
        self.assertEqual(billing_status.service_billing(charge)["label"], "PAID")

    def test_a_cancelled_service_owes_nothing(self):
        charge = add_charge(patient=self.patient, description="Ultrasound: Abdominal",
                            amount=Decimal("8000"), created_by=self.doctor,
                            source_type="ultrasound")
        charge.status = "cancelled"
        charge.save(update_fields=["status"])
        block = billing_status.service_billing(charge)
        self.assertEqual(block["label"], "CANCELLED")
        self.assertFalse(block["requires_payment"])

    def test_a_referral_with_no_priced_service_is_not_billed_rather_than_unpaid(self):
        route = self.scan_referral()
        block = billing_status.route_billing(route)
        self.assertEqual(block["status"], "unbilled")
        self.assertFalse(block["requires_payment"])


# =====================================================================
# 2. Each service keeps its own
# =====================================================================

class EachServiceKeepsItsOwnStatus(Hospital):
    def test_paying_for_one_service_does_not_mark_the_others_paid(self):
        """Rule 12 of the brief. Payments allocate oldest-first, so ₦8,000
        against a ₦20,000 basket settles the first examination and leaves the
        second exactly where it was."""
        route = self.scan_referral(self.abdominal, self.doppler)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)

        first, second = list(route.services.order_by("created_at", "id"))
        first.charge.refresh_from_db()
        second.charge.refresh_from_db()
        self.assertEqual(billing_status.service_billing(first.charge)["label"], "PAID")
        self.assertEqual(billing_status.service_billing(second.charge)["label"], "UNPAID")
        self.assertEqual(billing_status.service_billing(second.charge)["outstanding"], "12000.00")

    def test_the_headline_for_a_basket_is_the_worst_of_it(self):
        """An order that read PAID because most of it was, is how an unpaid
        service gets through."""
        route = self.scan_referral(self.abdominal, self.doppler)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        summary = billing_status.route_billing(route)
        self.assertEqual(summary["label"], "PARTIALLY PAID")
        self.assertEqual(summary["total"], "20000.00")
        self.assertEqual(summary["paid"], "8000.00")
        self.assertEqual(summary["outstanding"], "12000.00")
        self.assertTrue(summary["requires_payment"])

    def test_every_service_is_carried_separately_on_the_wire(self):
        route = self.scan_referral(self.abdominal, self.doppler)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        row = self.route_row(self.radiographer, route)
        by_name = {s["name"]: s["billing"] for s in row["services"]}
        self.assertEqual(by_name["Abdominal Ultrasound (test)"]["label"], "PAID")
        self.assertEqual(by_name["Doppler (test)"]["label"], "UNPAID")
        self.assertEqual(by_name["Doppler (test)"]["outstanding"], "12000.00")


# =====================================================================
# 3. Announced once, in both directions
# =====================================================================

class TheCashDeskIsToldWhenAServiceIsOrdered(Hospital):
    """Rule 35, unchanged — these hold that it still works, which is half of
    what the brief asks for and was already built."""

    def bills(self, user):
        return Notification.objects.filter(recipient=user, category="billing")

    def test_front_desk_cashier_and_admin_are_all_told(self):
        self.scan_referral(self.abdominal)
        for staff in (self.reception, self.cashier, self.accountant, self.admin):
            self.assertTrue(self.bills(staff).filter(title__startswith="To collect").exists(),
                            f"{staff.role} was not told")

    def test_the_notification_carries_the_service_the_amount_and_the_patient(self):
        self.scan_referral(self.abdominal)
        note = self.bills(self.cashier).get(title__startswith="To collect")
        self.assertIn(self.patient.display_name, note.title)
        self.assertIn(self.patient.patient_number, note.message)
        self.assertIn("8,000.00", note.message)
        self.assertEqual(note.action_url, "/billing")

    def test_no_clinical_role_is_told_about_a_bill_being_raised(self):
        self.scan_referral(self.abdominal)
        for staff in (self.doctor, self.scientist, self.radiographer, self.nurse):
            self.assertFalse(self.bills(staff).filter(title__startswith="To collect").exists(),
                             f"{staff.role} should not read the cash desk's bell")

    def test_a_basket_of_services_is_one_notification_not_three(self):
        """Rule 35's "once per decision": a referral for two scans is one line
        on the desk's bell."""
        self.scan_referral(self.abdominal, self.doppler)
        self.assertEqual(self.bills(self.cashier).filter(title__startswith="To collect").count(), 1)


class TheUnitIsToldWhenTheBillClears(Hospital):
    def cleared(self, user):
        return Notification.objects.filter(recipient=user, title__startswith="Payment cleared")

    def test_the_unit_holding_the_patient_is_told(self):
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        self.assertFalse(self.cleared(self.radiographer).exists())

        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)

        note = self.cleared(self.radiographer).get()
        self.assertIn(self.patient.display_name, note.title)
        self.assertIn("8,000.00", note.message)
        self.assertEqual(note.action_url, "/ultrasound")

    def test_unclaimed_work_reaches_the_whole_pool_and_nobody_outside_it(self):
        self.scan_referral(self.abdominal)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)

        self.assertTrue(self.cleared(self.radiographer).exists())
        for outsider in (self.scientist, self.nurse, self.reception, self.cashier):
            self.assertFalse(self.cleared(outsider).exists(),
                             f"{outsider.role} has no work waiting on this bill")

    def test_the_doctor_who_ordered_it_is_not_pinged(self):
        """Rule 14: she wrote the referral, she is not the one who performs
        it, and a bell full of your own actions stops being read."""
        self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        self.assertFalse(self.cleared(self.doctor).exists())

    def test_a_part_payment_tells_nobody(self):
        """The service is not cleared, so there is nothing new for the unit to
        act on — and a bell that rings on every ₦1,000 is one nobody reads."""
        self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        record_payment(patient=self.patient, amount=Decimal("3000"), received_by=self.cashier)
        self.assertFalse(self.cleared(self.radiographer).exists())

    def test_a_waiver_tells_the_unit_there_is_nothing_to_wait_for(self):
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        waive_charge(charge=route.services.get().charge, reason="Staff dependant",
                     approved_by=self.accountant)
        note = Notification.objects.get(recipient=self.radiographer,
                                        title__startswith="No payment required")
        self.assertIn("written off", note.message)

    def test_a_full_discount_clears_it_the_same_way(self):
        """A charge discounted to nothing settles as "paid" in the ledger —
        it is `amount_discounted`, not `amount_waived` — but no money was
        taken, so the unit is told the same thing it is told about a waiver.
        "Payment cleared · ₦0.00 paid" would read as a mistake."""
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        apply_percentage_discount(charge=route.services.get().charge, percent=Decimal("100"),
                                  reason="Hospital staff", approved_by=self.accountant)
        note = Notification.objects.get(recipient=self.radiographer,
                                        title__startswith="No payment required")
        self.assertIn("8,000.00 written off", note.message)

    def test_settling_one_service_of_two_tells_the_unit_once(self):
        route = self.scan_referral(self.abdominal, self.doppler, assigned_to=self.radiographer)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        self.assertEqual(self.cleared(self.radiographer).count(), 1)

    def test_a_charge_with_no_unit_behind_it_tells_nobody(self):
        """A consultation fee, a card, a walk-in sale: nobody is holding the
        patient waiting on it, so there is nobody to tell."""
        add_charge(patient=self.patient, description="Consultation fee",
                   amount=Decimal("2000"), created_by=self.reception,
                   source_type="consultation")
        record_payment(patient=self.patient, amount=Decimal("2000"), received_by=self.cashier)
        self.assertFalse(Notification.objects.filter(title__startswith="Payment cleared").exists())
        self.assertFalse(Notification.objects.filter(
            title__startswith="No payment required").exists())

    def test_finished_work_is_not_pinged(self):
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        route.status = "completed"
        route.save(update_fields=["status"])
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        self.assertFalse(self.cleared(self.radiographer).exists())


# =====================================================================
# 4. Departments read it; they are not gated by it
# =====================================================================

class TheWorklistSaysWhereTheMoneyStands(Hospital):
    def test_the_imaging_queue_row_carries_the_status_without_opening_anything(self):
        route = self.scan_referral(self.abdominal)
        row = self.route_row(self.radiographer, route)
        self.assertEqual(row["billing"]["label"], "UNPAID")
        self.assertEqual(row["billing"]["outstanding"], "8000.00")

        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        row = self.route_row(self.radiographer, route)
        self.assertEqual(row["billing"]["label"], "PAID")
        self.assertEqual(row["billing"]["outstanding"], "0.00")

    def test_the_laboratory_queue_row_carries_it_too_from_the_order_s_charges(self):
        """The lab's money hangs off `LabOrderTest`, not off the route — the
        station must not have to know that."""
        order, route = self.lab_referral("mp")
        row = self.route_row(self.scientist, route)
        self.assertTrue(row["billing"]["billed"])
        self.assertEqual(row["billing"]["label"], "UNPAID")

        record_payment(patient=self.patient,
                       amount=order.items.get().charge.amount, received_by=self.cashier)
        row = self.route_row(self.scientist, route)
        self.assertEqual(row["billing"]["label"], "PAID")

    def test_it_updates_from_the_charge_with_no_flag_to_refresh(self):
        """Rule 11 of the brief: nothing is stored, so nothing can go stale."""
        route = self.scan_referral(self.abdominal)
        waive_charge(charge=route.services.get().charge, reason="Indigent",
                     approved_by=self.accountant)
        row = self.route_row(self.radiographer, route)
        self.assertEqual(row["billing"]["label"], "NO PAYMENT REQUIRED")
        self.assertFalse(row["billing"]["requires_payment"])


class TheUnitIsNeverGatedByTheMoney(Hospital):
    """Rules 24 and 51, restated from the money's side.

    §10 of the brief asked for a backend check before a department may
    proceed — "where the hospital workflow requires payment before service
    delivery". In this hospital it does not, and that is deliberate: a sample
    already drawn is run, a patient already on the couch is scanned, and the
    cash desk chases the balance. `test_radiology_workflow.py`'s
    `test_an_unpaid_examination_is_shown_and_never_blocks_the_work` is the
    same statement from the workflow's side, and it stays true.

    So what this feature changed is that the unit *knows*. Nothing refuses.
    """

    def complete(self, route, user, **extra):
        return self.as_(user).post(f"/api/patient-routes/{route.pk}/complete/",
                                   {"result": "Normal study.", **extra}, format="json")

    def test_an_unpaid_request_is_completed_in_one_call(self):
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        self.assertTrue(billing_status.route_billing(route)["requires_payment"])

        response = self.complete(route, self.radiographer)
        self.assertEqual(response.status_code, 200, response.data)
        route.refresh_from_db()
        self.assertEqual(route.status, "completed")
        self.assertEqual(route.result, "Normal study.")

    def test_accepting_and_starting_never_consult_the_bill_either(self):
        route = self.scan_referral(self.abdominal)
        accepted = self.as_(self.radiographer).post(
            f"/api/patient-routes/{route.pk}/accept/")
        self.assertEqual(accepted.status_code, 200, accepted.data)

    def test_the_bill_is_still_owed_afterwards(self):
        """Doing the work settles nothing: the charge is untouched and the
        desk still has it to collect."""
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        self.complete(route, self.radiographer)
        charge = route.services.get().charge
        charge.refresh_from_db()
        self.assertEqual(billing_status.service_billing(charge)["label"], "UNPAID")
        self.assertEqual(charge.outstanding, Decimal("8000.00"))

    def test_the_status_is_visible_on_the_row_the_whole_time(self):
        """Which is the actual change: not a refusal, but that nobody has to
        guess."""
        route = self.scan_referral(self.abdominal, assigned_to=self.radiographer)
        self.assertEqual(self.route_row(self.radiographer, route)["billing"]["label"], "UNPAID")
        self.complete(route, self.radiographer)
        row = next(r for r in rows(self.as_(self.radiographer).get(
            "/api/patient-routes/", {"status": "completed", "page_size": 200}))
            if r["id"] == route.pk)
        self.assertEqual(row["billing"]["label"], "UNPAID")


class TheDoctorReadsItOnTheChart(Hospital):
    def overview(self, user=None):
        response = self.as_(user or self.doctor).get(
            f"/api/patients/{self.patient.uuid}/overview/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def routes_in(self, payload):
        return [route for visit in payload["visits"] for route in visit["routes"]]

    def test_a_doctor_sees_the_payment_status_of_what_they_ordered(self):
        self.scan_referral(self.abdominal)
        route = self.routes_in(self.overview())[0]
        self.assertEqual(route["billing"]["label"], "UNPAID")
        self.assertEqual(route["billing"]["outstanding"], "8000.00")
        self.assertEqual(route["services"][0]["name"], "Abdominal Ultrasound (test)")
        self.assertEqual(route["services"][0]["billing"]["label"], "UNPAID")

    def test_it_reads_paid_once_the_counter_has_taken_the_money(self):
        self.scan_referral(self.abdominal)
        record_payment(patient=self.patient, amount=Decimal("8000"), received_by=self.cashier)
        route = self.routes_in(self.overview())[0]
        self.assertEqual(route["billing"]["label"], "PAID")
        self.assertEqual(route["billing"]["outstanding"], "0.00")

    def test_the_chart_still_shows_the_doctor_no_ledger(self):
        """Section 8 of the brief: the services relevant to this patient's
        workflow, and not the hospital's money. The ledger block stays behind
        the roles that read money."""
        self.scan_referral(self.abdominal)
        self.assertNotIn("billing", self.overview())
