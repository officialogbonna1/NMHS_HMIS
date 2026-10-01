"""
The Procedure Department (rule 59): a doctor refers → the department's staff
see it on their board → one claims it → the procedure is performed and
documented, materials included → completed → it is on the patient's record
and prints.

Nothing here is a second system. The referral is `PatientRoute`
(`purpose="procedure"`), the record is the route's `result` / `result_data`
plus the permanent `MedicalTest` copy, the money is `billing.services`, the bell
is `core.services.notify`, the trail is `AuditLog`. What these tests hold is
the two things that are new: **who the procedure staff are** (doctors and
nurses who are members of the Procedure department — never Theatre's, and
nobody else), and
**the materials** documented on the record, with the server enforcing
received = used + remaining + wastage.
"""
import json
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillingItem, Charge, PatientLedger
from apps.billing.services import (apply_percentage_discount, cancel_charge, record_payment,
                                   refund_payment)
from apps.core.models import AuditLog, Notification
from apps.departments.models import Department
from apps.patients.models import MedicalTest, Patient
from apps.workflow.models import PatientRoute, RouteService, Visit

D = Decimal


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class ProcedureCase(TestCase):
    def setUp(self):
        make = User.objects.create_user
        self.reception = make(username="rec", password="t", role="reception")
        self.referrer = make(username="ref", password="t", role="doctor",
                             first_name="Chidi", last_name="Nwosu")
        self.proc_doctor = make(username="pdoc", password="t", role="doctor",
                                first_name="Ifeoma", last_name="Eze")
        self.proc_nurse = make(username="pnur", password="t", role="nurse",
                               first_name="Bola", last_name="Ade")
        self.other_doctor = make(username="odoc", password="t", role="doctor")
        self.other_nurse = make(username="onur", password="t", role="nurse")
        self.posted_cashier = make(username="pcash", password="t", role="cashier")
        self.cashier = make(username="cash", password="t", role="cashier")
        self.pharmacist = make(username="ph", password="t", role="pharmacist")
        self.admin = make(username="boss", password="t", role="hospital_admin")

        # Procedure is its own department (`departments/0007`); membership is
        # the existing `Department.staff` an administrator sets.
        self.procedure = Department.objects.get(code="procedure")
        self.procedure.staff.add(self.proc_doctor, self.proc_nurse, self.posted_cashier)
        # Theatre is a different department with its own staff.
        self.theatre = Department.objects.get(code="theatre")
        self.theatre_doctor = make(username="tdoc", password="t", role="doctor")
        self.theatre_nurse = make(username="tnur", password="t", role="nurse")
        self.theatre.staff.add(self.theatre_doctor, self.theatre_nurse)
        self.elsewhere = Department.objects.get(code="consultation")

        self.patient = Patient.objects.create(first_name="Michael", last_name="John", sex="M")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.referrer)
        self.dressing = BillingItem.objects.create(category="procedure", price=D("2500"),
                                                   name="Wound Dressing (test)")
        self.drainage = BillingItem.objects.create(category="procedure", price=D("5000"),
                                                   name="Incision & Drainage (test)")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def refer(self, user=None, **extra):
        body = {"patient": self.patient.pk, "purpose": "procedure",
                "notes": "Abscess left forearm — I&D please", **extra}
        return self.as_(user or self.referrer).post("/api/patient-routes/refer/", body, format="json")

    def referral(self, **extra):
        response = self.refer(**extra)
        self.assertEqual(response.status_code, 201, response.data)
        return PatientRoute.objects.get(pk=response.data["id"])

    def queue_ids(self, user, board=True):
        return {row["id"] for row in rows(self.as_(user).get("/api/patient-routes/"))}

    def record(self, route, user, report, *, complete=False):
        action = "complete" if complete else "record-result"
        return self.as_(user).post(f"/api/patient-routes/{route.pk}/{action}/",
                                   {"report": report}, format="json")

    REPORT = {
        "procedure_performed": "Incision and drainage, left forearm abscess",
        "clinical_notes": "Local anaesthesia, 2% lignocaine. 10 ml pus drained.",
        "findings": "4 cm fluctuant abscess.",
        "outcome": "Tolerated well.",
        "follow_up": "Dressing change daily; review in 48 hours.",
        "materials": [
            {"name": "Gauze swab", "category": "Consumable", "unit": "piece",
             "quantity_received": "10", "quantity_used": "6", "quantity_remaining": "3",
             "wastage": "1"},
            {"name": "Lignocaine 2%", "unit": "ml", "quantity_received": "5",
             "quantity_used": "4.5", "quantity_remaining": "0.5", "notes": "1 vial"},
        ],
    }


class TheReferralTests(ProcedureCase):
    def test_a_doctor_refers_into_the_procedure_department_whatever_was_picked(self):
        route = self.referral(department=self.elsewhere.pk)
        self.assertEqual((route.purpose, route.department, route.routed_by, route.status),
                         ("procedure", self.procedure, self.referrer, "queued"))
        self.assertNotEqual(route.department, self.theatre)
        self.assertEqual(route.visit.patient, self.patient)

    def test_only_a_clinician_may_refer(self):
        for user in (self.proc_nurse, self.reception, self.cashier, self.pharmacist):
            with self.subTest(role=user.role):
                self.assertEqual(self.refer(user).status_code, 403)
        self.assertFalse(PatientRoute.objects.filter(purpose="procedure").exists())

    def test_the_department_staff_are_told_and_nobody_else(self):
        response = self.refer()
        told = set(Notification.objects.filter(category="routing")
                   .values_list("recipient__username", flat=True))
        self.assertEqual(told, {"pdoc", "pnur"})          # not the posted cashier, not outsiders
        self.assertEqual(sorted(response.data["notified"]), ["Bola Ade", "Ifeoma Eze"])
        note = Notification.objects.filter(recipient=self.proc_nurse).first()
        self.assertEqual(note.action_url, "/procedures")

    def test_with_nobody_posted_the_referrer_is_told_who_to_ask(self):
        self.procedure.staff.clear()
        response = self.refer()
        self.assertEqual(response.data["notified"], [])
        self.assertIn("no staff are posted", response.data["notice"])

    def test_a_named_assignee_must_be_procedure_staff(self):
        refused = self.refer(assigned_to=self.other_doctor.pk)
        self.assertEqual(refused.status_code, 400)
        refused = self.refer(assigned_to=self.posted_cashier.pk)
        self.assertEqual(refused.status_code, 400)
        route = self.referral(assigned_to=self.proc_nurse.pk)
        self.assertEqual(route.assigned_to, self.proc_nurse)

    def test_the_front_desk_route_is_filed_under_the_department_and_checked_too(self):
        desk = self.as_(self.reception)
        refused = desk.post("/api/patient-routes/", {
            "visit": self.visit.pk, "department": self.elsewhere.pk, "purpose": "procedure",
            "assigned_to": self.other_nurse.pk}, format="json")
        self.assertEqual(refused.status_code, 400)
        made = desk.post("/api/patient-routes/", {
            "visit": self.visit.pk, "department": self.elsewhere.pk, "purpose": "procedure"},
            format="json")
        self.assertEqual(made.status_code, 201, made.data)
        self.assertEqual(PatientRoute.objects.get(pk=made.data["id"]).department, self.procedure)


class WhoWorksItTests(ProcedureCase):
    def test_the_board_is_the_department_staffs_alone(self):
        route = self.referral()
        for user in (self.proc_doctor, self.proc_nurse, self.admin):
            with self.subTest(user=user.username):
                self.assertIn(route.pk, self.queue_ids(user))
        for user in (self.other_doctor, self.other_nurse, self.posted_cashier,
                     self.theatre_doctor, self.theatre_nurse):
            with self.subTest(user=user.username):
                response = self.as_(user).get("/api/patient-routes/")
                ids = {row["id"] for row in rows(response)} if response.status_code == 200 else set()
                self.assertNotIn(route.pk, ids)

    def test_a_colleagues_claimed_procedure_stays_visible_but_is_not_workable(self):
        route = self.referral()
        accepted = self.as_(self.proc_nurse).post(f"/api/patient-routes/{route.pk}/accept/")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        self.assertIn(route.pk, self.queue_ids(self.proc_doctor))
        row = next(r for r in rows(self.as_(self.proc_doctor).get("/api/patient-routes/"))
                   if r["id"] == route.pk)
        self.assertFalse(row["can_work"])
        self.assertEqual(self.as_(self.proc_doctor).post(f"/api/patient-routes/{route.pk}/accept/")
                         .status_code, 409)
        self.assertEqual(self.record(route, self.proc_doctor, self.REPORT).status_code, 403)

    def test_an_outsider_cannot_claim_or_write_it(self):
        route = self.referral()
        for user in (self.other_doctor, self.other_nurse, self.theatre_doctor, self.theatre_nurse):
            with self.subTest(user=user.username):
                self.assertEqual(self.as_(user).post(f"/api/patient-routes/{route.pk}/accept/")
                                 .status_code, 403)
                self.assertIn(self.record(route, user, self.REPORT).status_code, (403, 404))
        route.refresh_from_db()
        self.assertEqual((route.assigned_to, route.result), (None, ""))

    def test_other_units_routing_is_unchanged(self):
        """A consultation still reaches doctors by role; procedure staff gain no lab work."""
        lab = self.as_(self.referrer).post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "laboratory", "notes": "FBC"}, format="json")
        self.assertEqual(lab.status_code, 201, lab.data)
        self.assertNotIn(lab.data["id"], self.queue_ids(self.proc_nurse))
        self.assertEqual(self.as_(self.proc_nurse).post(f"/api/patient-routes/{lab.data['id']}/accept/")
                         .status_code, 403)


class TheRecordTests(ProcedureCase):
    def claimed(self, user=None):
        route = self.referral()
        self.as_(user or self.proc_doctor).post(f"/api/patient-routes/{route.pk}/accept/")
        return route

    def test_the_record_is_written_with_its_sections_and_materials(self):
        route = self.claimed()
        response = self.record(route, self.proc_doctor, self.REPORT)
        self.assertEqual(response.status_code, 200, response.data)
        route.refresh_from_db()
        self.assertEqual(route.result_data["procedure_performed"],
                         "Incision and drainage, left forearm abscess")
        self.assertEqual(route.result_data["materials"][0], {
            "name": "Gauze swab", "category": "Consumable", "unit": "piece",
            "quantity_received": "10.00", "quantity_used": "6.00",
            "quantity_remaining": "3.00", "wastage": "1.00"})
        self.assertEqual(route.result_data["materials"][1]["quantity_used"], "4.50")
        self.assertEqual((route.result_by, route.assigned_to), (self.proc_doctor, self.proc_doctor))
        for text in ("Procedure performed:", "Follow-up:", "Materials used:",
                     "- Gauze swab (Consumable): used 6 piece, received 10 piece"):
            self.assertIn(text, route.result)

    def test_it_joins_the_patients_record_and_the_referrer_is_told(self):
        route = self.claimed()
        self.record(route, self.proc_doctor, self.REPORT)
        filed = MedicalTest.objects.get(source_route=route)
        self.assertEqual((filed.patient, filed.test_type), (self.patient, "other"))
        self.assertIn("Materials used:", filed.impressions)
        told = Notification.objects.get(recipient=self.referrer, category="clinical")
        self.assertEqual(told.action_url, f"/patients/{self.patient.uuid}/procedure")
        # The referring doctor reads it on the chart.
        overview = self.as_(self.referrer).get(f"/api/patients/{self.patient.uuid}/overview/")
        self.assertEqual(overview.status_code, 200)
        self.assertIn("Incision and drainage", json.dumps(overview.data, default=str))

    def test_the_quantity_rule_is_the_servers(self):
        route = self.claimed()
        broken = {"procedure_performed": "Dressing", "materials": [
            {"name": "Gauze", "quantity_received": "10", "quantity_used": "6",
             "quantity_remaining": "3"},                                # 9 ≠ 10
        ]}
        response = self.record(route, self.proc_doctor, broken)
        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity_received", response.data["report"]["materials"]["0"])
        route.refresh_from_db()
        self.assertEqual((route.result, route.result_data), ("", None))   # nothing corrected

    def test_bad_quantities_and_rows_are_refused_by_row(self):
        route = self.claimed()
        cases = {
            "negative": {"name": "Gauze", "quantity_used": "-1"},
            "not a number": {"name": "Gauze", "quantity_used": "two"},
            "three decimals": {"name": "Gauze", "quantity_used": "1.005"},
            "no name": {"quantity_used": "2"},
            "no quantity": {"name": "Gauze"},
            "unknown field": {"name": "Gauze", "quantity_used": "1", "price": "100"},
        }
        for label, row in cases.items():
            with self.subTest(label):
                response = self.record(route, self.proc_doctor,
                                       {"procedure_performed": "x", "materials": [row]})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("0", response.data["report"]["materials"])
        self.assertFalse(MedicalTest.objects.filter(source_route=route).exists())

    def test_rows_without_a_received_figure_are_simply_recorded(self):
        route = self.claimed()
        response = self.record(route, self.proc_doctor, {
            "procedure_performed": "Dressing",
            "materials": [{"name": "Crepe bandage", "quantity_used": "1", "unit": "roll"}]})
        self.assertEqual(response.status_code, 200, response.data)

    def test_editing_the_rows_replaces_them(self):
        route = self.claimed()
        self.record(route, self.proc_doctor, self.REPORT)
        self.record(route, self.proc_doctor, {**self.REPORT, "materials": self.REPORT["materials"][:1]})
        route.refresh_from_db()
        self.assertEqual([row["name"] for row in route.result_data["materials"]], ["Gauze swab"])
        self.assertEqual(MedicalTest.objects.filter(source_route=route).count(), 1)

    def test_a_multipart_save_sends_the_rows_as_json(self):
        route = self.claimed()
        response = self.as_(self.proc_doctor).post(
            f"/api/patient-routes/{route.pk}/record-result/",
            {"report.procedure_performed": "Dressing",
             "report.materials": json.dumps([{"name": "Gauze", "quantity_used": "2"}])},
            format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        route.refresh_from_db()
        self.assertEqual(route.result_data["materials"][0]["quantity_used"], "2.00")

    def test_other_units_cannot_record_materials(self):
        scan = self.as_(self.referrer).post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "ultrasound", "notes": "Abdomen"}, format="json")
        radiographer = User.objects.create_user(username="rad", password="t", role="radiology")
        response = self.as_(radiographer).post(
            f"/api/patient-routes/{scan.data['id']}/record-result/",
            {"report": {"findings": "Normal", "materials": [{"name": "Gel", "quantity_used": "1"}]}},
            format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("materials", response.data["report"])

    def test_completing_needs_a_record(self):
        route = self.claimed()
        response = self.as_(self.proc_doctor).post(f"/api/patient-routes/{route.pk}/complete/")
        self.assertEqual((response.status_code, response.data["code"]), (400, "record_required"))
        done = self.record(route, self.proc_doctor, self.REPORT, complete=True)
        self.assertEqual(done.status_code, 200, done.data)
        route.refresh_from_db()
        self.assertEqual(route.status, "completed")

    def test_a_completed_record_is_locked_and_an_admin_corrects_it(self):
        route = self.claimed(self.proc_nurse)
        self.record(route, self.proc_nurse, self.REPORT, complete=True)
        locked = self.record(route, self.proc_nurse, {**self.REPORT, "outcome": "Changed"})
        self.assertEqual((locked.status_code, locked.data["code"]), (409, "record_locked"))
        route.refresh_from_db()
        self.assertEqual(route.result_data["outcome"], "Tolerated well.")

        fixed = self.record(route, self.admin, {**self.REPORT, "outcome": "Tolerated well; mild pain."})
        self.assertEqual(fixed.status_code, 200, fixed.data)
        row = AuditLog.objects.filter(action="patient.route_result_recorded",
                                      object_id=route.pk).first()
        self.assertEqual((row.actor, row.details["correction"]), (self.admin, True))

    def test_the_work_is_audited(self):
        route = self.claimed()
        self.record(route, self.proc_doctor, self.REPORT, complete=True)
        actions = set(AuditLog.objects.filter(object_id=route.pk)
                      .values_list("action", flat=True))
        self.assertTrue({"patient.referred", "patient.route_accepted",
                         "patient.route_completed"} <= actions)
        done = AuditLog.objects.get(action="patient.route_completed", object_id=route.pk)
        self.assertEqual(done.details["materials"], 2)


class BillingTests(ProcedureCase):
    def ordered(self, *items):
        route = self.referral()
        response = self.as_(self.referrer).post(
            f"/api/patient-routes/{route.pk}/request-services/",
            {"services": [item.pk for item in items]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return route

    def test_ordering_a_priced_procedure_raises_its_charge_at_the_catalogue_price(self):
        route = self.ordered(self.drainage, self.dressing)
        charges = Charge.objects.filter(patient=self.patient).order_by("amount")
        self.assertEqual([(c.amount, c.source_type, c.department.code) for c in charges],
                         [(D("2500.00"), "procedure", "procedure"), (D("5000.00"), "procedure", "procedure")])
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("7500.00"))
        self.assertEqual(route.services.count(), 2)
        # The cash desk sees them on the ordinary bill.
        listed = rows(self.as_(self.cashier).get("/api/charges/", {"patient": self.patient.pk}))
        self.assertEqual(len(listed), 2)

    def test_a_reprice_does_not_rewrite_the_bill(self):
        route = self.ordered(self.drainage)
        BillingItem.objects.filter(pk=self.drainage.pk).update(price=D("9000"), name="Renamed")
        service = RouteService.objects.get(route=route)
        self.assertEqual((service.unit_price, service.charge.amount), (D("5000.00"), D("5000.00")))

    def test_part_payment_discount_refund_and_cancellation_are_the_ordinary_ones(self):
        route = self.ordered(self.drainage, self.dressing)
        drainage = RouteService.objects.get(route=route, item=self.drainage).charge
        dressing = RouteService.objects.get(route=route, item=self.dressing).charge
        payment = record_payment(patient=self.patient, amount=D("2000"), received_by=self.cashier,
                                 charge=drainage)
        drainage.refresh_from_db()
        self.assertEqual((drainage.settlement_status, drainage.outstanding), ("partial", D("3000.00")))
        apply_percentage_discount(charge=drainage, percent=D("10"), reason="Staff",
                                  approved_by=self.cashier)
        refund_payment(payment=payment, amount=D("500"), reason="Over-collected",
                       processed_by=self.cashier)
        cancel_charge(charge=dressing, cancelled_by=self.cashier, reason="Not needed")
        drainage.refresh_from_db()
        dressing.refresh_from_db()
        self.assertEqual(dressing.status, "cancelled")
        self.assertEqual(drainage.amount_paid, D("1500.00"))
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance,
                         D("3000.00"))   # 5000 − 500 discount − 1500 paid

    def test_an_unpaid_procedure_is_never_blocked(self):
        route = self.ordered(self.drainage)
        self.as_(self.proc_doctor).post(f"/api/patient-routes/{route.pk}/accept/")
        done = self.record(route, self.proc_doctor, self.REPORT, complete=True)
        self.assertEqual(done.status_code, 200, done.data)

    def test_the_team_is_told_when_the_bill_is_settled(self):
        route = self.ordered(self.dressing)
        charge = RouteService.objects.get(route=route).charge
        record_payment(patient=self.patient, amount=D("2500"), received_by=self.cashier, charge=charge)
        cleared = set(Notification.objects.filter(category="billing",
                                                  title__startswith="Payment cleared")
                      .values_list("recipient__username", flat=True))
        self.assertEqual(cleared, {"pdoc", "pnur"})

    def test_only_a_clinician_orders_and_only_from_the_procedure_list(self):
        route = self.referral()
        scan = BillingItem.objects.create(category="ultrasound", price=D("8000"), name="Scan (test)")
        self.assertEqual(self.as_(self.proc_nurse).post(
            f"/api/patient-routes/{route.pk}/request-services/",
            {"services": [self.dressing.pk]}, format="json").status_code, 403)
        wrong = self.as_(self.referrer).post(
            f"/api/patient-routes/{route.pk}/request-services/",
            {"services": [scan.pk]}, format="json")
        self.assertEqual(wrong.status_code, 400)
        self.assertFalse(Charge.objects.exists())


class PrintingTests(ProcedureCase):
    def test_the_printed_record_carries_the_materials_as_rows(self):
        route = self.referral()
        self.as_(self.proc_doctor).post(f"/api/patient-routes/{route.pk}/accept/")
        self.record(route, self.proc_doctor, self.REPORT, complete=True)
        doc = self.as_(self.proc_nurse).get(f"/api/patient-routes/{route.pk}/document/")
        self.assertEqual(doc.status_code, 200, doc.data)
        result = doc.data["result"]
        self.assertEqual([row["name"] for row in result["materials"]], ["Gauze swab", "Lignocaine 2%"])
        self.assertIn("Procedure performed:", result["findings_text"])
        self.assertNotIn("Materials used:", result["findings_text"])
        self.assertEqual(result["recorded_by"], "Ifeoma Eze")
        self.assertEqual(doc.data["route"]["routed_by"], "Chidi Nwosu")
        # The front desk prints the request form, never the clinical record.
        desk = self.as_(self.reception).get(f"/api/patient-routes/{route.pk}/document/")
        self.assertIn(desk.status_code, (200, 404))
        if desk.status_code == 200:
            self.assertIsNone(desk.data["result"])

    def test_the_form_reads_its_fields_from_the_server(self):
        schema = self.as_(self.proc_nurse).get("/api/patient-routes/report-fields/",
                                              {"purpose": "procedure"}).data["schema"]
        self.assertEqual([s["key"] for s in schema["sections"]],
                         ["procedure_performed", "clinical_notes", "findings", "outcome", "follow_up"])
        self.assertEqual([q["key"] for q in schema["materials"]["quantities"]],
                         ["quantity_received", "quantity_used", "quantity_remaining", "wastage"])


class ProcedureIsItsOwnDepartmentTests(ProcedureCase):
    """
    Procedure ≠ Theatre. Two rows, two staff lists, and membership of one says
    nothing about the other — the existing `Department.staff` many-to-many,
    which already lets a member of staff belong to several departments.
    """

    def test_two_departments_two_names(self):
        self.assertEqual((self.procedure.name, self.theatre.name), ("Procedure", "Theatre"))
        self.assertNotEqual(self.procedure.pk, self.theatre.pk)
        self.assertFalse(Department.objects.filter(name__icontains="Theatre /").exists())

    def test_many_doctors_and_nurses_can_be_procedure_staff_at_once(self):
        from apps.workflow import procedures

        extra = [User.objects.create_user(username=f"p{i}", password="t", role=role)
                 for i, role in enumerate(["doctor", "nurse", "nurse"])]
        self.procedure.staff.add(*extra)
        team = set(procedures.team().values_list("username", flat=True))
        self.assertEqual(team, {"pdoc", "pnur", "p0", "p1", "p2"})   # not the posted cashier
        route = self.referral()
        for user in [self.proc_doctor, self.proc_nurse, *extra]:
            with self.subTest(user=user.username):
                self.assertIn(route.pk, self.queue_ids(user))

    def test_theatre_membership_grants_no_procedure_work_and_the_reverse(self):
        from apps.accounts.departments import works_in
        from apps.workflow import procedures

        self.assertFalse(procedures.in_team(self.theatre_doctor))
        self.assertFalse(procedures.in_team(self.theatre_nurse))
        self.assertFalse(works_in(self.proc_nurse, "theatre"))
        self.assertTrue(works_in(self.theatre_nurse, "theatre"))

    def test_staff_in_both_work_procedures_and_keep_theatre(self):
        from apps.accounts.departments import works_in

        both = User.objects.create_user(username="both", password="t", role="nurse")
        self.procedure.staff.add(both)
        self.theatre.staff.add(both)
        route = self.referral()
        self.assertIn(route.pk, self.queue_ids(both))
        self.assertEqual(self.as_(both).post(f"/api/patient-routes/{route.pk}/accept/").status_code, 200)
        self.assertTrue(works_in(both, "theatre"))

    def test_nobody_on_theatre_is_told_about_a_procedure(self):
        self.refer()
        told = set(Notification.objects.filter(category="routing")
                   .values_list("recipient__username", flat=True))
        self.assertFalse(told & {"tdoc", "tnur"})

    def test_django_admin_assigns_procedure_staff_and_it_takes_effect(self):
        root = User.objects.create_superuser(username="root", password="t", email="r@x.test",
                                             role="admin")
        self.client.force_login(root)
        page = self.client.get(f"/admin/departments/department/{self.procedure.pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="staff"')
        newcomer = User.objects.create_user(username="newdoc", password="t", role="doctor")
        route = self.referral()
        self.assertNotIn(route.pk, self.queue_ids(newcomer))

        response = self.client.post(f"/admin/departments/department/{self.procedure.pk}/change/", {
            "code": "procedure", "name": "Procedure", "is_active": "on",
            "staff": [self.proc_doctor.pk, self.proc_nurse.pk, newcomer.pk],
            "visible_domains": "[]",
            "services-TOTAL_FORMS": "0", "services-INITIAL_FORMS": "0",
            "services-MIN_NUM_FORMS": "0", "services-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context.get("errors"))
        self.assertEqual(set(self.procedure.staff.values_list("username", flat=True)),
                         {"pdoc", "pnur", "newdoc"})
        self.assertIn(route.pk, self.queue_ids(newcomer))          # immediately
        self.assertNotIn(route.pk, self.queue_ids(self.theatre_doctor))


class TheSplitMigrationTests(TestCase):
    """`departments/0007` on the data an install already had."""

    def setUp(self):
        import importlib
        from django.apps import apps as django_apps

        self.split = importlib.import_module(
            "apps.departments.migrations.0007_separate_procedure_from_theatre").separate
        self.apps = django_apps
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        self.theatre = Department.objects.get(code="theatre")
        self.procedure = Department.objects.get(code="procedure")

    def charge(self, source_type, department):
        return Charge.objects.create(patient=self.patient, description=source_type, amount=D("100"),
                                     created_by=self.reception, source_type=source_type,
                                     department=department)

    def test_only_procedure_work_moves_and_theatre_keeps_its_own(self):
        self.theatre.name = "Theatre / Procedures"
        self.theatre.save(update_fields=["name"])
        route = PatientRoute.objects.create(visit=self.visit, department=self.theatre,
                                            purpose="procedure", routed_by=self.reception)
        other = PatientRoute.objects.create(visit=self.visit, department=self.theatre,
                                            purpose="consultation", routed_by=self.reception)
        procedure_charge = self.charge("procedure", self.theatre)
        surgery_charge = self.charge("surgery", self.theatre)
        staffer = User.objects.create_user(username="t1", password="t", role="nurse")
        self.theatre.staff.add(staffer)

        self.split(self.apps, None)

        route.refresh_from_db(); other.refresh_from_db()
        procedure_charge.refresh_from_db(); surgery_charge.refresh_from_db()
        self.theatre.refresh_from_db()
        self.assertEqual((route.department, other.department), (self.procedure, self.theatre))
        self.assertEqual((procedure_charge.department, surgery_charge.department),
                         (self.procedure, self.theatre))
        self.assertEqual((procedure_charge.amount, procedure_charge.amount_paid), (D("100.00"), D("0.00")))
        self.assertEqual(self.theatre.name, "Theatre")
        self.assertEqual(list(self.theatre.staff.all()), [staffer])         # staff stay
        self.assertFalse(self.procedure.staff.filter(pk=staffer.pk).exists())  # not copied

    def test_an_edited_theatre_name_is_left_alone_and_it_is_safe_to_rerun(self):
        self.theatre.name = "Main Operating Theatre"
        self.theatre.save(update_fields=["name"])
        self.split(self.apps, None)
        self.split(self.apps, None)
        self.theatre.refresh_from_db()
        self.assertEqual(self.theatre.name, "Main Operating Theatre")
        self.assertEqual(Department.objects.filter(code="procedure").count(), 1)



class TheSplitIsReversibleTests(TheSplitMigrationTests):
    def test_reversing_puts_procedure_work_back_and_restores_the_label(self):
        import importlib

        module = importlib.import_module(
            "apps.departments.migrations.0007_separate_procedure_from_theatre")
        self.theatre.name = "Theatre / Procedures"
        self.theatre.save(update_fields=["name"])
        route = PatientRoute.objects.create(visit=self.visit, department=self.theatre,
                                            purpose="procedure", routed_by=self.reception)
        module.separate(self.apps, None)
        route.refresh_from_db()
        self.assertEqual(route.department.code, "procedure")

        module.rejoin(self.apps, None)
        route.refresh_from_db()
        self.theatre.refresh_from_db()
        self.assertEqual(route.department, self.theatre)
        self.assertEqual(self.theatre.name, "Theatre / Procedures")
        # Nothing points at Procedure any more, so the reverse removes it.
        self.assertFalse(Department.objects.filter(code="procedure").exists())


class EachDepartmentGrantsOnlyItself(ProcedureCase):
    """
    Final verification of the split, every way in: role + Procedure membership
    is the whole of procedure access, and Theatre — by relation *or* by the
    legacy `user.department` text, old combined label included — is no part
    of it.
    """

    def test_each_combination_of_role_and_membership(self):
        from apps.workflow import procedures

        cases = [
            (self.proc_doctor, True), (self.proc_nurse, True),
            (self.other_doctor, False), (self.other_nurse, False),
            (self.theatre_doctor, False), (self.theatre_nurse, False),
            (self.posted_cashier, False),
        ]
        for user, expected in cases:
            with self.subTest(user=user.username):
                self.assertEqual(procedures.in_team(user), expected)
                self.assertEqual(procedures.may_be_named(user), expected)

    def test_legacy_theatre_text_grants_no_procedure_work(self):
        from apps.workflow import procedures

        route = self.referral()
        for text in ("Theatre", "theatre", "Theatre / Procedures"):
            legacy = User.objects.create_user(username=f"legacy-{text}", password="t",
                                              role="doctor", department=text)
            with self.subTest(text=text):
                self.assertFalse(procedures.in_team(legacy))
                self.assertNotIn(route.pk, self.queue_ids(legacy))
                self.assertEqual(self.as_(legacy).post(
                    f"/api/patient-routes/{route.pk}/accept/").status_code, 403)

    def test_procedure_membership_is_not_theatre_membership(self):
        from apps.accounts.departments import staff_of, works_in

        theatre_staff = set(staff_of(self.theatre).values_list("username", flat=True))
        self.assertEqual(theatre_staff, {"tdoc", "tnur"})
        for user in (self.proc_doctor, self.proc_nurse):
            with self.subTest(user=user.username):
                self.assertFalse(works_in(user, self.theatre))
                self.assertFalse(works_in(user, "Theatre"))

    def test_theatre_only_staff_cannot_start_complete_or_print_it(self):
        route = self.referral()
        for user in (self.theatre_doctor, self.theatre_nurse):
            with self.subTest(user=user.username):
                api = self.as_(user)
                self.assertIn(api.post(f"/api/patient-routes/{route.pk}/start/").status_code,
                              (403, 404))
                self.assertIn(self.record(route, user, self.REPORT, complete=True).status_code,
                              (403, 404))
                self.assertIn(api.get(f"/api/patient-routes/{route.pk}/document/").status_code,
                              (403, 404))
        route.refresh_from_db()
        self.assertEqual((route.status, route.result, route.assigned_to), ("queued", "", None))

    def test_two_procedure_staff_work_two_procedures_at_once(self):
        first = self.referral()
        # One open referral per patient per unit (`already_referred`), so the
        # second procedure is somebody else's.
        other = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")
        Visit.objects.create(patient=other, opened_by=self.reception, attending_doctor=self.referrer)
        response = self.refer(patient=other.pk, notes="Second dressing")
        self.assertEqual(response.status_code, 201, response.data)
        second = PatientRoute.objects.get(pk=response.data["id"])
        for user, route in ((self.proc_doctor, first), (self.proc_nurse, second)):
            with self.subTest(user=user.username):
                api = self.as_(user)
                # Accepting claims *and* starts it (queued → in progress).
                self.assertEqual(api.post(f"/api/patient-routes/{route.pk}/accept/").status_code, 200)
                route.refresh_from_db()
                self.assertEqual((route.status, route.assigned_to), ("in_progress", user))
        for user, route in ((self.proc_doctor, first), (self.proc_nurse, second)):
            with self.subTest(user=user.username):
                done = self.record(route, user, self.REPORT, complete=True)
                self.assertEqual(done.status_code, 200, done.data)
                self.assertEqual(self.as_(user).get(
                    f"/api/patient-routes/{route.pk}/document/").status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, first.result_by), ("completed", self.proc_doctor))
        self.assertEqual((second.status, second.result_by), ("completed", self.proc_nurse))
        # Neither could have written the other's.
        self.assertEqual(self.record(first, self.proc_nurse, self.REPORT).status_code, 403)
