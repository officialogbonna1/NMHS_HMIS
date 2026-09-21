"""
Radiology / Ultrasound: the laboratory's pattern, at the size imaging needs.

Doctor refers → names the examinations from the configured catalogue → the
charges are raised there and then → the patient settles them at the existing
counter → the request is on radiology's worklist with what was asked for and
where the money stands → the scan is done and the report written through the
station that already existed.

Nothing here is a second system. The referral is `PatientRoute` (rule 16
routes it, rule 14 notifies it), the money is `billing.services.add_charge`
(rule 24's laboratory exception, applied to the second unit that orders a
priced service), the catalogue is the price list Reception bills from, and the
report is the station's existing `record-result`. What is new is one row per
examination (`workflow.RouteService`) joining the three together.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.billing.models import BillingItem, Charge
from apps.core.models import AuditLog, Notification
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, RouteService, Visit


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) and "results" in data else data


class Imaging(TestCase):
    """A doctor with a patient in front of them, and a radiographer on duty."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor",
                                               first_name="Chidi", last_name="Nwosu")
        self.radiographer = User.objects.create_user(username="rad", password="t", role="radiology",
                                                     first_name="Bisi", last_name="Ade")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.nurse = User.objects.create_user(username="nur", password="t", role="nurse")

        self.department = Department.objects.create(code="radiology-test", name="Radiology (test)")
        self.patient = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F")
        self.visit = Visit.objects.create(patient=self.patient, opened_by=self.reception,
                                          attending_doctor=self.doctor)

        self.abdominal = BillingItem.objects.create(category="ultrasound", price=Decimal("8000"),
                                                    name="Abdominal Ultrasound (test)")
        self.obstetric = BillingItem.objects.create(category="ultrasound", price=Decimal("15000"),
                                                    name="Obstetric Ultrasound (test)")
        self.unpriced = BillingItem.objects.create(category="ultrasound", price=Decimal("0"),
                                                   name="Repeat view (test)")

    def as_(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def refer(self, **extra):
        response = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "ultrasound",
            "priority": "routine", "notes": "RUQ pain, rule out gallstones",
            "department": self.department.pk, **extra,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return PatientRoute.objects.get(pk=response.data["id"])

    def request_services(self, route, items, user=None):
        return self.as_(user or self.doctor).post(
            f"/api/patient-routes/{route.pk}/request-services/",
            {"services": [item.pk for item in items]}, format="json")


class TheDoctorOrdersFromTheConfiguredCatalogue(Imaging):
    def test_radiology_is_a_referral_destination(self):
        route = self.refer()
        self.assertEqual(route.purpose, "ultrasound")
        self.assertEqual(route.status, "queued")

    def test_the_examinations_offered_are_the_configured_ones(self):
        """Not a hard-coded list: the doctor reads the same endpoint the desk
        bills from, filtered to this unit's category."""
        response = self.as_(self.doctor).get("/api/billable-services/",
                                             {"category": "ultrasound"})
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.data["results"]]
        self.assertIn(self.abdominal.name, names)
        self.assertIn(self.obstetric.name, names)

    def test_choosing_one_records_it_and_bills_it_at_the_configured_price(self):
        route = self.refer()
        response = self.request_services(route, [self.abdominal])
        self.assertEqual(response.status_code, 201, response.data)

        service = RouteService.objects.get(route=route)
        self.assertEqual(service.name, self.abdominal.name)
        self.assertEqual(service.unit_price, Decimal("8000"))

        charge = service.charge
        self.assertIsNotNone(charge)
        self.assertEqual(charge.amount, Decimal("8000"))
        self.assertEqual(charge.patient, self.patient)
        self.assertEqual(charge.source_type, "ultrasound")
        self.assertEqual(charge.created_by, self.doctor)
        # Attributed through the existing registry, not by a new rule.
        self.assertEqual(charge.department.code, "radiology")

    def test_re_pricing_the_catalogue_does_not_move_a_bill_already_raised(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        self.abdominal.price = Decimal("12000")
        self.abdominal.save(update_fields=["price"])

        service = RouteService.objects.get(route=route)
        self.assertEqual(service.unit_price, Decimal("8000"))
        self.assertEqual(service.charge.amount, Decimal("8000"))

    def test_several_examinations_are_one_line_on_the_cash_desk_s_bell(self):
        route = self.refer()
        self.request_services(route, [self.abdominal, self.obstetric])
        self.assertEqual(RouteService.objects.filter(route=route).count(), 2)
        self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 2)

        raised = Notification.objects.filter(recipient=self.cashier, category="billing")
        self.assertEqual(raised.count(), 1, [n.title for n in raised])
        self.assertIn("23,000", raised.first().message)

    def test_an_examination_with_no_price_is_recorded_and_not_billed(self):
        """A zero charge is noise on a bill; the request is still real."""
        route = self.refer()
        self.request_services(route, [self.unpriced])
        service = RouteService.objects.get(route=route)
        self.assertIsNone(service.charge)
        self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 0)

    def test_asking_twice_does_not_bill_twice(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        self.request_services(route, [self.abdominal])
        self.assertEqual(RouteService.objects.filter(route=route).count(), 1)
        self.assertEqual(Charge.objects.filter(patient=self.patient).count(), 1)

    def test_a_service_from_another_department_is_not_ordered_here(self):
        card = BillingItem.objects.create(category="card", name="Adult Card (test)",
                                          price=Decimal("2000"))
        route = self.refer()
        response = self.request_services(route, [card])
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data.get("code"), "no_services")
        self.assertFalse(RouteService.objects.exists())

    def test_a_referral_that_orders_nothing_from_the_price_list_is_refused(self):
        response = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "procedure",
            "department": self.department.pk,
        }, format="json")
        route = PatientRoute.objects.get(pk=response.data["id"])
        refused = self.request_services(route, [self.abdominal])
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.data.get("code"), "not_a_service_referral")

    def test_the_audit_row_says_what_was_ordered_and_what_it_cost(self):
        route = self.refer()
        self.request_services(route, [self.obstetric])
        entry = AuditLog.objects.filter(action="patient.services_requested").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor, self.doctor)
        self.assertEqual(entry.details["services"], [self.obstetric.name])
        self.assertEqual(entry.details["charged"], ["15000.00"])


class TheRequestReachesRadiology(Imaging):
    def test_the_unit_is_notified_by_the_existing_routing(self):
        """Rule 14's pool: nobody had to be added to a department for this."""
        self.refer()
        note = Notification.objects.filter(recipient=self.radiographer).first()
        self.assertIsNotNone(note)
        self.assertIn("Ultrasound", note.title)
        self.assertIn(self.patient.display_name, note.title)

    def test_the_referring_doctor_is_not_told_about_their_own_referral(self):
        self.refer()
        self.assertFalse(Notification.objects.filter(recipient=self.doctor,
                                                     category="routing").exists())

    def test_the_worklist_carries_the_examination_the_doctor_asked_for(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])

        queue = rows(self.as_(self.radiographer).get("/api/patient-routes/"))
        entry = next(r for r in queue if r["id"] == route.pk)
        self.assertEqual(entry["patient_name"], self.patient.display_name)
        self.assertEqual(entry["purpose_label"], "Ultrasound / Imaging")
        self.assertEqual(entry["routed_by_name"], "Chidi Nwosu")
        self.assertTrue(entry["created_at"])
        self.assertEqual([s["name"] for s in entry["services"]], [self.abdominal.name])
        self.assertEqual(entry["services"][0]["billing"]["status"], "unpaid")
        self.assertEqual(entry["services"][0]["billing"]["amount"], "8000.00")
        # The clinical question travels with it.
        self.assertIn("gallstones", entry["notes"])

    def test_payment_shows_on_the_worklist_once_the_desk_has_taken_it(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        paid = self.as_(self.cashier).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "8000", "method": "cash",
        }, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)

        queue = rows(self.as_(self.radiographer).get("/api/patient-routes/"))
        entry = next(r for r in queue if r["id"] == route.pk)
        self.assertEqual(entry["services"][0]["billing"]["status"], "paid")

    def test_an_unpaid_examination_is_shown_and_never_blocks_the_work(self):
        """
        The laboratory's rule, deliberately: the unit reads the money and is
        never gated by it (rule 24). A patient already on the couch is scanned
        and the cash desk chases the balance — this asserts the state is
        *visible*, and that nothing in the workflow refuses on it.
        """
        route = self.refer()
        self.request_services(route, [self.abdominal])

        queue = rows(self.as_(self.radiographer).get("/api/patient-routes/"))
        entry = next(r for r in queue if r["id"] == route.pk)
        self.assertEqual(entry["services"][0]["billing"]["status"], "unpaid")

        # Accepting claims it and puts it in progress; nothing in either
        # transition consults the bill.
        accepted = self.as_(self.radiographer).post(f"/api/patient-routes/{route.pk}/accept/")
        self.assertEqual(accepted.status_code, 200, accepted.data)
        route.refresh_from_db()
        self.assertEqual(route.status, "in_progress")
        done = self.as_(self.radiographer).post(f"/api/patient-routes/{route.pk}/complete/",
                                                {"result": "Normal study."}, format="json")
        self.assertEqual(done.status_code, 200, done.data)

    def test_the_unit_performs_the_scan_and_files_the_report(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        self.as_(self.radiographer).post(f"/api/patient-routes/{route.pk}/accept/")
        recorded = self.as_(self.radiographer).post(
            f"/api/patient-routes/{route.pk}/record-result/",
            {"result": "Normal liver echotexture. No gallstones seen.",
             "title": "Abdominal ultrasound"}, format="json")
        self.assertEqual(recorded.status_code, 200, recorded.data)

        route.refresh_from_db()
        self.assertIn("No gallstones", route.result)
        self.assertEqual(route.result_by, self.radiographer)
        # Filed on the permanent record by the existing machinery.
        self.assertTrue(self.patient.tests.filter(test_type="ultrasound").exists())
        # And the doctor who asked is told.
        self.assertTrue(Notification.objects.filter(recipient=self.doctor,
                                                    title__icontains="result").exists())

    def test_the_printable_request_form_lists_what_was_ordered(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        response = self.as_(self.radiographer).get(f"/api/patient-routes/{route.pk}/document/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([s["name"] for s in response.data["route"]["services"]],
                         [self.abdominal.name])


class WhoMayDoWhat(Imaging):
    def test_the_radiographer_may_add_an_examination_they_decided_on(self):
        route = self.refer()
        self.as_(self.radiographer).post(f"/api/patient-routes/{route.pk}/accept/")
        response = self.request_services(route, [self.obstetric], user=self.radiographer)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(RouteService.objects.get(route=route).requested_by, self.radiographer)

    def test_a_nurse_may_not_order_an_examination(self):
        route = self.refer()
        response = self.request_services(route, [self.abdominal], user=self.nurse)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(RouteService.objects.exists())

    def test_the_cash_desk_may_not_order_one(self):
        route = self.refer()
        self.assertEqual(self.request_services(route, [self.abdominal], user=self.cashier).status_code,
                         403)

    def test_the_laboratory_does_not_see_the_radiology_queue(self):
        route = self.refer()
        queue = rows(self.as_(self.lab).get("/api/patient-routes/"))
        self.assertNotIn(route.pk, [r["id"] for r in queue])

    def test_a_doctor_with_no_claim_on_the_patient_cannot_order(self):
        """
        A referral they did not raise, for a patient they are not holding, is
        not on their list at all — so knowing its id gets them the same answer
        knowing nothing does (rule 32: an identifier is not an authorisation).
        `may_act_for` is the second layer behind it, for a caller who can see
        the route.
        """
        other_doctor = User.objects.create_user(username="doc2", password="t", role="doctor")
        route = self.refer()
        response = self.request_services(route, [self.abdominal], user=other_doctor)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(RouteService.objects.exists())
        self.assertFalse(Charge.objects.exists())


class TheDeskBillsTheSameService(Imaging):
    def test_reception_sees_every_examination_the_doctor_can_order(self):
        clinic = {row["key"] for row in
                  self.as_(self.doctor).get("/api/billable-services/",
                                            {"category": "ultrasound"}).data["results"]}
        desk = {row["key"] for row in
                self.as_(self.reception).get("/api/billable-services/",
                                             {"category": "ultrasound"}).data["results"]}
        self.assertEqual(clinic, desk)
        self.assertGreater(len(desk), 1)

    def test_the_patient_settles_it_through_the_existing_counter(self):
        route = self.refer()
        self.request_services(route, [self.abdominal])
        charge = RouteService.objects.get(route=route).charge

        payment = self.as_(self.cashier).post("/api/payments/", {
            "patient": self.patient.pk, "amount": "8000", "method": "cash",
        }, format="json")
        self.assertEqual(payment.status_code, 201, payment.data)

        charge.refresh_from_db()
        self.assertEqual(charge.amount_paid, Decimal("8000"))
        self.assertEqual(charge.settlement_status, "paid")


class TheReportHasTheShapeOfAScan(Imaging):
    """
    An imaging report is a technique, what was seen, the figures and a
    conclusion — not one box of prose, and not a laboratory parameter with a
    reference range either. `workflow/report_fields.py` is the one definition;
    the station renders it and the server validates against it.
    """

    def setUp(self):
        super().setUp()
        self.route = self.refer()
        self.request_services(self.route, [self.abdominal])
        self.as_(self.radiographer).post(f"/api/patient-routes/{self.route.pk}/accept/")

    def record(self, report, user=None):
        return self.as_(user or self.radiographer).post(
            f"/api/patient-routes/{self.route.pk}/record-result/", report, format="json")

    def test_the_station_is_told_which_sections_to_ask_for(self):
        response = self.as_(self.radiographer).get("/api/patient-routes/report-fields/",
                                                   {"purpose": "ultrasound"})
        self.assertEqual(response.status_code, 200)
        keys = [section["key"] for section in response.data["schema"]["sections"]]
        self.assertEqual(keys, ["technique", "findings", "measurements", "impression"])

    def test_a_unit_whose_report_is_prose_is_told_so(self):
        response = self.as_(self.radiographer).get("/api/patient-routes/report-fields/",
                                                   {"purpose": "eye"})
        self.assertIsNone(response.data["schema"])

    def test_the_sections_are_stored_and_rendered_for_every_existing_reader(self):
        response = self.record({"report": {
            "technique": "Transabdominal, full bladder",
            "findings": "Normal liver echotexture. No gallstones.",
            "measurements": "RK 10.2cm, LK 10.6cm",
            "impression": "Normal abdominal ultrasound.",
        }})
        self.assertEqual(response.status_code, 200, response.data)

        self.route.refresh_from_db()
        self.assertEqual(self.route.result_data["impression"], "Normal abdominal ultrasound.")
        # `result` stays the text the chart, the printed sheet and the
        # permanent record already read.
        self.assertIn("Technique:\nTransabdominal, full bladder", self.route.result)
        self.assertIn("Impression:\nNormal abdominal ultrasound.", self.route.result)
        filed = self.patient.tests.get(test_type="ultrasound")
        self.assertIn("No gallstones", filed.impressions)

    def test_an_empty_section_is_dropped_rather_than_printed_blank(self):
        self.record({"report": {"findings": "Bulky uterus.", "technique": "   ",
                                "measurements": "", "impression": "Fibroid uterus."}})
        self.route.refresh_from_db()
        self.assertEqual(set(self.route.result_data), {"findings", "impression"})
        self.assertNotIn("Technique", self.route.result)

    def test_a_report_of_one_section_is_a_report(self):
        self.assertEqual(self.record({"report": {"impression": "Normal study."}}).status_code, 200)
        self.route.refresh_from_db()
        self.assertEqual(self.route.result_data, {"impression": "Normal study."})

    def test_a_section_the_report_does_not_have_is_refused(self):
        response = self.record({"report": {"diagnosis": "Cholecystitis"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "invalid_report")
        self.route.refresh_from_db()
        self.assertEqual(self.route.result, "")

    def test_nothing_at_all_is_still_refused(self):
        response = self.record({"report": {"findings": "  "}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("result", response.data)

    def test_the_sections_arrive_the_same_way_from_a_multipart_post(self):
        """The form that also carries the scanned printout cannot nest JSON."""
        response = self.as_(self.radiographer).post(
            f"/api/patient-routes/{self.route.pk}/record-result/",
            {"report.findings": "Single live intrauterine fetus.",
             "report.impression": "28 weeks, cephalic."},
            format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.route.refresh_from_db()
        self.assertEqual(set(self.route.result_data), {"findings", "impression"})

    def test_closing_the_work_carries_the_report_too(self):
        done = self.as_(self.radiographer).post(
            f"/api/patient-routes/{self.route.pk}/complete/",
            {"report": {"findings": "Normal.", "impression": "No abnormality detected."}},
            format="json")
        self.assertEqual(done.status_code, 200, done.data)
        self.route.refresh_from_db()
        self.assertEqual(self.route.status, "completed")
        self.assertEqual(self.route.result_data["impression"], "No abnormality detected.")

    def test_the_doctor_who_asked_reads_it_on_the_chart(self):
        self.record({"report": {"findings": "Normal.", "impression": "Normal study."}})
        overview = self.as_(self.doctor).get(f"/api/patients/{self.patient.uuid}/overview/")
        self.assertEqual(overview.status_code, 200)
        routes = [r for visit in overview.data["visits"] for r in visit["routes"]]
        answered = next(r for r in routes if r["id"] == self.route.pk)
        self.assertIn("Normal study.", answered["result"])

    def test_the_report_cannot_be_typed_over_the_row_itself(self):
        """`result_data` is written by the actions that stamp who wrote it."""
        response = self.as_(self.radiographer).patch(
            f"/api/patient-routes/{self.route.pk}/",
            {"result_data": {"impression": "Typed straight in"}}, format="json")
        self.assertIn(response.status_code, (403, 405))
        self.route.refresh_from_db()
        self.assertIsNone(self.route.result_data)

    def test_another_unit_cannot_write_this_report(self):
        response = self.record({"report": {"impression": "Normal."}}, user=self.lab)
        self.assertIn(response.status_code, (403, 404))
        self.route.refresh_from_db()
        self.assertEqual(self.route.result, "")


class TheUnitSeesWhoHasWhat(Imaging):
    """
    Accepting marks the request as accepted; it does not make it vanish.

    A radiology room is shared — two sonographers, one machine, one list — so
    the board shows everything sent to the unit and says who has each one. The
    rest of the unit sees "With Bisi Ade" instead of an Accept button that the
    server would refuse, and a request nobody has taken still reads Unclaimed.

    Seeing is not doing: `workflow/access.py` is the one rule, and the row
    carries its answer so the screen cannot invent a different one.
    """

    def setUp(self):
        super().setUp()
        self.colleague = User.objects.create_user(username="rad2", password="t", role="radiology",
                                                  first_name="Tunde", last_name="Okafor")
        self.route = self.refer()
        self.request_services(self.route, [self.abdominal])

    def board(self, user):
        response = self.as_(user).get("/api/patient-routes/")
        self.assertEqual(response.status_code, 200)
        return {r["id"]: r for r in rows(response)}

    def test_before_anybody_accepts_it_is_unclaimed_for_the_whole_unit(self):
        for person in (self.radiographer, self.colleague):
            with self.subTest(person=person.username):
                row = self.board(person)[self.route.pk]
                self.assertIsNone(row["assigned_to"])
                self.assertTrue(row["can_accept"])
                self.assertTrue(row["can_work"])
                self.assertFalse(row["claimed_by_other"])

    def test_accepting_marks_it_accepted_for_everybody(self):
        self.as_(self.radiographer).post(f"/api/patient-routes/{self.route.pk}/accept/")

        mine = self.board(self.radiographer)[self.route.pk]
        self.assertEqual(mine["assigned_to"], self.radiographer.pk)
        self.assertEqual(mine["status"], "in_progress")
        self.assertTrue(mine["can_work"])
        self.assertFalse(mine["claimed_by_other"])

        # The colleague still sees the patient — and sees whose they are.
        theirs = self.board(self.colleague)[self.route.pk]
        self.assertEqual(theirs["assigned_to_name"], "Bisi Ade")
        self.assertTrue(theirs["claimed_by_other"])
        self.assertFalse(theirs["can_accept"])
        self.assertFalse(theirs["can_work"])

    def test_a_colleague_cannot_accept_start_or_report_on_it(self):
        self.as_(self.radiographer).post(f"/api/patient-routes/{self.route.pk}/accept/")
        api = self.as_(self.colleague)

        taken = api.post(f"/api/patient-routes/{self.route.pk}/accept/")
        self.assertEqual(taken.status_code, 409)
        self.assertIn("already accepted", taken.data["detail"])

        self.assertEqual(api.post(f"/api/patient-routes/{self.route.pk}/start/").status_code, 403)
        wrote = api.post(f"/api/patient-routes/{self.route.pk}/record-result/",
                         {"report": {"impression": "Not mine to write."}}, format="json")
        self.assertEqual(wrote.status_code, 403)

        self.route.refresh_from_db()
        self.assertEqual(self.route.assigned_to, self.radiographer)
        self.assertEqual(self.route.result, "")

    def test_a_radiologist_named_on_a_referral_sees_it_and_works_it(self):
        """Reception or the doctor can send the patient to one person by
        name; that person's board shows it as theirs from the start."""
        # A second patient: one open ultrasound referral per visit is an
        # existing rule (`already_referred`), and not the one under test here.
        other = Patient.objects.create(first_name="Bo", last_name="Eze", sex="M")
        Visit.objects.create(patient=other, opened_by=self.reception,
                             attending_doctor=self.doctor)
        named = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": other.pk, "purpose": "ultrasound", "department": self.department.pk,
            "assigned_to": self.colleague.pk, "notes": "Doppler please",
        }, format="json")
        self.assertEqual(named.status_code, 201, named.data)
        route_id = named.data["id"]

        row = self.board(self.colleague)[route_id]
        self.assertEqual(row["assigned_to"], self.colleague.pk)
        self.assertTrue(row["can_work"])
        self.assertFalse(row["claimed_by_other"])
        # And they can do the work without accepting anything first.
        api = self.as_(self.colleague)
        self.assertEqual(api.post(f"/api/patient-routes/{route_id}/start/").status_code, 200)
        wrote = api.post(f"/api/patient-routes/{route_id}/record-result/",
                         {"report": {"impression": "Normal Doppler."}}, format="json")
        self.assertEqual(wrote.status_code, 200, wrote.data)

        # The other sonographer sees it on the board as theirs, not as free.
        theirs = self.board(self.radiographer)[route_id]
        self.assertTrue(theirs["claimed_by_other"])
        self.assertFalse(theirs["can_accept"])

    def test_every_radiologist_sees_the_unit_s_work_whoever_raised_it(self):
        second = self.as_(self.doctor).post("/api/patient-routes/refer/", {
            "patient": self.patient.pk, "purpose": "laboratory", "department": self.department.pk,
        }, format="json")
        self.assertEqual(second.status_code, 201, second.data)

        board = self.board(self.colleague)
        self.assertIn(self.route.pk, board)
        # Still only this unit's work: the laboratory's referral is not theirs.
        self.assertNotIn(second.data["id"], board)

    def test_the_nurses_rule_is_untouched(self):
        """Vitals is one nurse to one patient: accepting still takes it off
        every other nurse's list (`test_nurse_handoff`)."""
        nurse = User.objects.create_user(username="n1", password="t", role="nurse")
        other_nurse = User.objects.create_user(username="n2", password="t", role="nurse")
        vitals = PatientRoute.objects.create(visit=self.visit, department=self.department,
                                             purpose="vitals", routed_by=self.reception)
        self.as_(nurse).post(f"/api/patient-routes/{vitals.pk}/accept/")

        seen = [r["id"] for r in rows(self.as_(other_nurse).get("/api/patient-routes/"))]
        self.assertNotIn(vitals.pk, seen)

    def test_the_dashboard_still_counts_only_my_own_work(self):
        """The board is the queue page's; "My queue" is mine."""
        self.as_(self.radiographer).post(f"/api/patient-routes/{self.route.pk}/accept/")
        cards = {c["key"]: c for c in self.as_(self.colleague).get("/api/dashboard/").data["cards"]
                 if "key" in c}
        self.assertEqual(cards["my_queue"]["value"], 0)
        self.assertEqual(cards["referrals_in_progress"]["value"], 0)
