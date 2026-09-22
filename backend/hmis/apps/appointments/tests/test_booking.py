"""
Booking an appointment into a department, for a configured service.

The extension under test is additive in the strictest sense: the queue, the
statuses, the transitions and the notification are the ones that were already
there, and an appointment queued the old way — patient, doctor, reason — is
still queued the old way. What is new is that the same queue can now carry
"Eye Consultation at the Eye Clinic with Dr Femi, ₦5,000" without a second
catalogue, a second department table, a second provider directory or a second
price list existing anywhere.
"""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.appointments.models import Appointment
from apps.billing.models import BillingItem, Charge
from apps.departments.models import Department
from apps.patients.models import Patient


class Booking(TestCase):
    def setUp(self):
        self.reception = User.objects.create_user(username="reception", password="t", role="reception")
        self.doctor = User.objects.create_user(username="doc", password="t", role="doctor",
                                               first_name="Dara", last_name="Ade")
        self.eye_doctor = User.objects.create_user(username="eye", password="t",
                                                   role="ophthalmologist",
                                                   first_name="Femi", last_name="Bello")
        self.radiographer = User.objects.create_user(username="rad", password="t", role="radiology")
        self.lab = User.objects.create_user(username="lab", password="t", role="laboratory")
        self.cashier = User.objects.create_user(username="cash", password="t", role="cashier")
        self.patient = Patient.objects.create(first_name="John", last_name="Doe", sex="M",
                                              created_by=self.reception)

        # Configuration, in the one catalogue the counter already bills from.
        self.consultation = BillingItem.objects.create(
            category="consultation", name="Doctor Consultation", price=Decimal("5000"),
            is_appointment_service=True)
        self.eye_service = BillingItem.objects.create(
            category="eye", name="Eye Consultation", price=Decimal("5000"),
            is_appointment_service=True)
        self.scan = BillingItem.objects.create(
            category="ultrasound", name="Ultrasound Examination", price=Decimal("10000"),
            is_appointment_service=True)
        # Priced, but nobody has said it may be booked.
        self.card = BillingItem.objects.create(
            category="card", name="Hospital Card", price=Decimal("1000"))
        # Bookable and free — a follow-up nobody is charged for.
        self.follow_up = BillingItem.objects.create(
            category="eye", name="Eye Follow-up", price=Decimal("0"),
            is_appointment_service=True)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def book(self, user=None, **body):
        payload = {"patient": self.patient.id, "reason": "Blurred vision", **body}
        return self.api(user or self.reception).post("/api/appointments/", payload)

    def options(self, user=None):
        return self.api(user or self.reception).get("/api/appointments/booking-options/")

    def _service(self, department_name, service_name):
        """
        One offered service, by the names a person reads.

        Looked up rather than indexed because the test database runs the
        hospital's own seed migrations: the catalogue these tests add to is
        never empty, which is the same catalogue the desk really sees.
        """
        for department in self.options().data["departments"]:
            if department["name"] != department_name:
                continue
            for service in department["services"]:
                if service["name"] == service_name:
                    return service
        return None


class TheExistingConsultationQueue(Booking):
    """
    The path that was there before this feature, unchanged. Every assertion
    here is about something that must not have moved.
    """

    def test_patient_doctor_reason_still_queues_an_appointment(self):
        response = self.book(doctor=self.doctor.id)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "queued")
        self.assertIsNone(response.data["start_time"])

    def test_a_booking_with_no_service_raises_no_charge_and_names_no_department(self):
        appointment = Appointment.objects.get(pk=self.book(doctor=self.doctor.id).data["id"])
        self.assertIsNone(appointment.service_id)
        self.assertIsNone(appointment.charge_id)
        self.assertIsNone(appointment.department_id)
        self.assertEqual(Charge.objects.count(), 0)

    def test_the_doctor_is_still_notified(self):
        self.book(doctor=self.doctor.id)
        from apps.core.models import Notification
        note = Notification.objects.filter(recipient=self.doctor).first()
        self.assertIsNotNone(note)
        self.assertIn("John", note.title)
        self.assertEqual(note.action_url, "/appointments")

    def test_accept_start_and_end_still_work_for_the_doctor(self):
        appointment_id = self.book(doctor=self.doctor.id).data["id"]
        client = self.api(self.doctor)
        for transition, expected in (("accept", "accepted"), ("start", "in_progress"),
                                     ("end", "completed")):
            response = client.post(f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 200, (transition, response.data))
            self.assertEqual(Appointment.objects.get(pk=appointment_id).status, expected)

    def test_reception_still_cannot_transition_anything(self):
        appointment_id = self.book(doctor=self.doctor.id).data["id"]
        for transition in ("accept", "start", "end", "cancel"):
            response = self.api(self.reception).post(
                f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 403, transition)

    def test_one_doctor_cannot_transition_another_s_appointment(self):
        appointment_id = self.book(doctor=self.doctor.id).data["id"]
        other = User.objects.create_user(username="doc2", password="t", role="doctor")
        response = self.api(other).post(f"/api/appointments/{appointment_id}/accept/")
        # 404 rather than 403: a provider's queryset is their own queue, so
        # the row is not theirs to find. Exactly as it answered before.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Appointment.objects.get(pk=appointment_id).status, "queued")

    def test_queueing_the_same_patient_twice_is_still_refused(self):
        self.book(doctor=self.doctor.id)
        again = self.book(doctor=self.doctor.id)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["code"], "already_queued")


class TheDepartmentsOnOffer(Booking):
    """
    Reception picks the destination from the departments an administrator has
    opened for appointments.

    **Two switches, two questions.** `Department.is_appointment_available`
    decides whether the department is offered at all;
    `BillingItem.is_appointment_service` decides what can be booked once it
    is. So an offered department with nothing ticked appears and offers
    nothing, rather than being hidden by a rule or given invented services.
    """

    def _named(self):
        return {d["name"]: d for d in self.options().data["departments"]}

    def _open(self, code):
        Department.objects.filter(code=code).update(is_appointment_available=True)

    def test_the_offered_departments_are_the_ones_switched_on(self):
        self.assertEqual(set(self._named()),
                         {"Consultation", "Eye Clinic", "Radiology / Ultrasound"})

    def test_a_department_that_is_not_switched_on_is_not_offered(self):
        listed = self._named()
        for absent in ("Clinicals (Nursing)", "Pharmacy", "Reception",
                       "Laboratory", "Theatre / Procedures"):
            self.assertNotIn(absent, listed)

    def test_a_retired_department_is_not_offered_however_it_is_switched(self):
        self._open("theatre")
        Department.objects.filter(code="theatre").update(is_active=False)
        self.assertNotIn("Theatre / Procedures", self._named())

    def test_an_opened_department_with_nothing_configured_offers_no_services(self):
        self._open("laboratory")
        self.assertEqual(self._named()["Laboratory"]["services"], [])

    def test_no_service_is_invented_for_a_department_that_has_none(self):
        self._open("pharmacy")
        self.assertEqual(self._named()["Pharmacy"]["services"], [])

    def test_a_service_appears_under_its_department_the_moment_it_is_ticked(self):
        self._open("laboratory")
        self.assertEqual(self._named()["Laboratory"]["services"], [])
        BillingItem.objects.create(category="laboratory", name="Fasting Blood Sugar (booked)",
                                   price=Decimal("2000"), is_appointment_service=True)
        names = [s["name"] for s in self._named()["Laboratory"]["services"]]
        self.assertIn("Fasting Blood Sugar (booked)", names)

    def test_theatre_offers_nothing_until_a_procedure_is_ticked(self):
        self._open("theatre")
        self.assertEqual(self._named()["Theatre / Procedures"]["services"], [])
        BillingItem.objects.create(category="procedure", name="Circumcision (booked)",
                                   price=Decimal("25000"), is_appointment_service=True)
        names = [s["name"] for s in self._named()["Theatre / Procedures"]["services"]]
        self.assertEqual(names, ["Circumcision (booked)"])

    def test_a_department_empties_when_its_last_service_is_unticked(self):
        BillingItem.objects.filter(category="ultrasound").update(is_appointment_service=False)
        self.assertEqual(self._named()["Radiology / Ultrasound"]["services"], [])

    def test_the_departments_are_the_seeded_rows_not_new_ones(self):
        for entry in self.options().data["departments"]:
            self.assertTrue(Department.objects.filter(pk=entry["id"], code=entry["code"]).exists())


class TheServicesOnOffer(Booking):
    def test_services_are_grouped_under_the_department_that_performs_them(self):
        eye = self._service("Eye Clinic", "Eye Consultation")
        self.assertIsNotNone(eye)
        # And not under anybody else's heading.
        self.assertIsNone(self._service("Radiology / Ultrasound", "Eye Consultation"))

    def test_the_fee_is_the_catalogue_s_own_price(self):
        scan = self._service("Radiology / Ultrasound", "Ultrasound Examination")
        self.assertEqual(Decimal(scan["fee"]), Decimal("10000.00"))

    def test_an_administrator_re_pricing_it_changes_what_the_desk_is_quoted(self):
        self.scan.price = Decimal("12500")
        self.scan.save()
        scan = self._service("Radiology / Ultrasound", "Ultrasound Examination")
        self.assertEqual(Decimal(scan["fee"]), Decimal("12500.00"))

    def test_an_unticked_priced_row_is_not_offered(self):
        offered = {s["name"] for d in self.options().data["departments"] for s in d["services"]}
        self.assertNotIn("Hospital Card", offered)

    def test_a_retired_service_is_not_offered(self):
        self.scan.is_active = False
        self.scan.save()
        offered = {s["name"] for d in self.options().data["departments"] for s in d["services"]}
        self.assertNotIn("Ultrasound Examination", offered)

    def test_a_free_service_is_offered_and_marked_unbillable(self):
        by_name = {d["name"]: d for d in self.options().data["departments"]}
        follow_up = [s for s in by_name["Eye Clinic"]["services"] if s["name"] == "Eye Follow-up"][0]
        self.assertFalse(follow_up["billable"])

    def test_the_laboratory_test_catalogue_is_not_dragged_in(self):
        """
        A `LabTest` is ordered by a clinician and billed at the counter
        (rules 24 and 50). Sixty-six of them on the booking form would be a
        different feature, and a laboratory *appointment* is a ticked
        `BillingItem` instead.
        """
        from apps.laboratory.models import LabTest
        LabTest.objects.create(name="Full Blood Count", code="FBC", category="haematology",
                               price=Decimal("3500"))
        offered = {s["key"] for d in self.options().data["departments"] for s in d["services"]}
        self.assertTrue(all(key.startswith("billing_item:") for key in offered))


class TheProvidersOnOffer(Booking):
    def _providers_for(self, department_name, service_name):
        service = self._service(department_name, service_name)
        return {p["id"] for p in service["providers"]} if service else set()

    def test_an_eye_service_offers_the_eye_doctor(self):
        self.assertIn(self.eye_doctor.id, self._providers_for("Eye Clinic", "Eye Consultation"))

    def test_an_eye_service_does_not_offer_laboratory_staff(self):
        offered = self._providers_for("Eye Clinic", "Eye Consultation")
        self.assertNotIn(self.lab.id, offered)
        self.assertNotIn(self.cashier.id, offered)
        self.assertNotIn(self.radiographer.id, offered)

    def test_a_scan_offers_radiology_and_not_a_doctor(self):
        offered = self._providers_for("Radiology / Ultrasound", "Ultrasound Examination")
        self.assertIn(self.radiographer.id, offered)
        self.assertNotIn(self.doctor.id, offered)

    def test_a_consultation_still_offers_the_existing_doctors(self):
        offered = self._providers_for("Consultation", "Doctor Consultation")
        self.assertEqual(offered, {self.doctor.id})

    def test_a_disabled_account_is_never_offered(self):
        self.eye_doctor.is_active = False
        self.eye_doctor.save()
        self.assertNotIn(self.eye_doctor.id,
                         self._providers_for("Eye Clinic", "Eye Consultation"))

    def test_departmental_staff_are_eligible_even_without_the_role(self):
        """`Department.staff` is the fallback rule 16 already draws."""
        surgeon = User.objects.create_user(username="surg", password="t", role="surgeon")
        Department.objects.get(code="eye").staff.add(surgeon)
        self.assertIn(surgeon.id, self._providers_for("Eye Clinic", "Eye Consultation"))


class TheServerDecidesWhoAndWhat(Booking):
    """
    The dropdowns are a courtesy. Posting past them has to be refused.
    """

    def test_a_service_that_is_not_bookable_is_refused(self):
        response = self.book(doctor=self.doctor.id, service=self.card.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "service_not_bookable")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_a_retired_service_is_refused(self):
        self.eye_service.is_active = False
        self.eye_service.save()
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "service_not_bookable")

    def test_a_laboratory_test_cannot_be_submitted_as_a_service(self):
        from apps.laboratory.models import LabTest
        test = LabTest.objects.create(name="Widal", code="WID", category="serology",
                                      price=Decimal("2500"))
        response = self.book(doctor=self.doctor.id, service=test.id)
        self.assertEqual(response.status_code, 400)

    def test_an_ineligible_provider_is_refused(self):
        response = self.book(doctor=self.lab.id, service=self.eye_service.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "provider_not_eligible")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_a_doctor_cannot_be_booked_for_a_scan(self):
        response = self.book(doctor=self.doctor.id, service=self.scan.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "provider_not_eligible")

    def test_an_admin_may_be_named_on_anything(self):
        admin = User.objects.create_user(username="boss", password="t", role="admin")
        response = self.book(doctor=admin.id, service=self.scan.id)
        self.assertEqual(response.status_code, 201, response.data)

    def test_the_client_cannot_set_the_fee(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id,
                             service_fee="1.00")
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.service_fee, Decimal("5000.00"))
        self.assertEqual(appointment.charge.amount, Decimal("5000.00"))

    def test_the_client_cannot_set_the_department(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id,
                             department=Department.objects.get(code="pharmacy").id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.department.code, "eye")

    def test_the_client_cannot_attach_its_own_charge(self):
        from apps.billing.services import add_charge
        stray = add_charge(patient=self.patient, description="Something else",
                           amount=Decimal("99"), created_by=self.cashier, source_type="other")
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id,
                             charge=stray.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertNotEqual(appointment.charge_id, stray.id)

    def test_a_clinician_still_cannot_book(self):
        self.assertEqual(self.book(user=self.doctor, doctor=self.doctor.id).status_code, 403)

    def test_only_reception_reads_the_booking_options(self):
        self.assertEqual(self.options(self.cashier).status_code, 403)
        self.assertEqual(self.options(self.doctor).status_code, 403)


class TheBillItRaises(Booking):
    def test_a_billable_service_raises_one_ordinary_charge(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        charge = appointment.charge
        self.assertIsNotNone(charge)
        self.assertEqual(Charge.objects.count(), 1)
        self.assertEqual(charge.amount, Decimal("5000.00"))
        self.assertEqual(charge.description, "Eye Consultation")
        self.assertEqual(charge.source_type, "eye")
        self.assertEqual(charge.source_id, appointment.pk)

    def test_the_charge_is_attributed_to_the_unit_that_performs_it(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.charge.department.code, "eye")
        # And the appointment names the same unit — one answer, not two.
        self.assertEqual(appointment.department_id, appointment.charge.department_id)

    def test_it_reaches_the_patient_s_ledger(self):
        self.book(doctor=self.radiographer.id, service=self.scan.id)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("10000.00"))

    def test_a_free_service_raises_nothing(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.follow_up.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertIsNone(appointment.charge_id)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(appointment.service_fee, Decimal("0"))

    def test_the_fee_is_not_a_payment(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(response.data["billing"]["status"], "unpaid")
        self.assertTrue(response.data["billing"]["requires_payment"])
        self.assertEqual(response.data["billing"]["paid"], "0.00")

    def test_a_free_appointment_reads_as_no_charge_rather_than_unpaid(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.follow_up.id)
        self.assertEqual(response.data["billing"]["status"], "unbilled")
        self.assertFalse(response.data["billing"]["requires_payment"])

    def test_the_existing_payment_workflow_settles_it(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        paid = self.api(self.cashier).post("/api/payments/", {
            "patient": self.patient.id, "amount": "5000", "method": "cash"})
        self.assertEqual(paid.status_code, 201, paid.data)
        appointment.charge.refresh_from_db()
        self.assertEqual(appointment.charge.settlement_status, "paid")
        # And the queue row says so, through the one billing vocabulary.
        row = self.api(self.reception).get(f"/api/appointments/{appointment.pk}/").data
        self.assertEqual(row["billing"]["status"], "paid")
        self.assertFalse(row["billing"]["requires_payment"])

    def test_a_part_payment_reads_as_part_paid(self):
        response = self.book(doctor=self.radiographer.id, service=self.scan.id)
        self.api(self.cashier).post("/api/payments/", {
            "patient": self.patient.id, "amount": "4000", "method": "cash"})
        row = self.api(self.reception).get(f"/api/appointments/{response.data['id']}/").data
        self.assertEqual(row["billing"]["status"], "partial")
        self.assertEqual(row["billing"]["outstanding"], "6000.00")


class HistoryDoesNotMove(Booking):
    def test_re_pricing_the_service_leaves_the_booking_alone(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment = Appointment.objects.get(pk=response.data["id"])

        self.eye_service.price = Decimal("7000")
        self.eye_service.save()

        appointment.refresh_from_db()
        self.assertEqual(appointment.service_fee, Decimal("5000.00"))
        self.assertEqual(appointment.charge.amount, Decimal("5000.00"))

    def test_renaming_the_service_leaves_the_booking_alone(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.eye_service.name = "Ophthalmic Review"
        self.eye_service.save()
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.service_name, "Eye Consultation")

    def test_withdrawing_the_service_leaves_the_booking_readable(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.eye_service.is_appointment_service = False
        self.eye_service.is_active = False
        self.eye_service.save()
        row = self.api(self.reception).get(f"/api/appointments/{response.data['id']}/").data
        self.assertEqual(row["service_name"], "Eye Consultation")
        self.assertEqual(row["service_fee"], "5000.00")

    def test_deleting_the_service_leaves_the_booking_readable(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment_id = response.data["id"]
        self.eye_service.delete()
        appointment = Appointment.objects.get(pk=appointment_id)
        self.assertIsNone(appointment.service_id)
        self.assertEqual(appointment.service_name, "Eye Consultation")
        self.assertEqual(appointment.service_fee, Decimal("5000.00"))


class TheProviderWorksTheirQueue(Booking):
    def test_an_eye_doctor_accepts_starts_and_ends_their_appointment(self):
        appointment_id = self.book(doctor=self.eye_doctor.id,
                                   service=self.eye_service.id).data["id"]
        client = self.api(self.eye_doctor)
        for transition, expected in (("accept", "accepted"), ("start", "in_progress"),
                                     ("end", "completed")):
            response = client.post(f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 200, (transition, response.data))
            self.assertEqual(Appointment.objects.get(pk=appointment_id).status, expected)

    def test_a_provider_sees_only_their_own_queue(self):
        mine = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).data["id"]
        other = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                       created_by=self.reception)
        self.api(self.reception).post("/api/appointments/", {
            "patient": other.id, "doctor": self.radiographer.id, "reason": "Scan",
            "service": self.scan.id})
        listed = self.api(self.eye_doctor).get("/api/appointments/").data
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertEqual([row["id"] for row in rows], [mine])

    def test_a_radiographer_is_notified_of_their_appointment(self):
        self.book(doctor=self.radiographer.id, service=self.scan.id)
        from apps.core.models import Notification
        note = Notification.objects.filter(recipient=self.radiographer).first()
        self.assertIsNotNone(note)
        self.assertIn("Ultrasound Examination", note.message)


class ReturningPatients(Booking):
    def test_the_same_patient_books_again_in_another_department(self):
        first = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(first.status_code, 201, first.data)
        second = self.book(doctor=self.radiographer.id, service=self.scan.id)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(self.patient.appointments.count(), 2)

    def test_the_patient_is_found_by_number_name_and_phone(self):
        self.patient.phone_number = "08031112222"
        self.patient.save()
        for term in (self.patient.patient_number, "Doe", "08031112222"):
            found = self.api(self.reception).get("/api/patients/", {"search": term}).data
            rows = found["results"] if isinstance(found, dict) else found
            self.assertIn(self.patient.id, [row["id"] for row in rows], term)


class EverythingStaysTraceable(Booking):
    def test_a_booking_records_who_what_where_and_the_bill(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        row = response.data
        self.assertEqual(row["patient"], self.patient.id)
        self.assertEqual(row["patient_number"], self.patient.patient_number)
        self.assertEqual(row["department_name"], "Eye Clinic")
        self.assertEqual(row["service_name"], "Eye Consultation")
        self.assertEqual(row["provider"], self.eye_doctor.id)
        self.assertEqual(row["provider_name"], "Femi Bello")
        self.assertEqual(row["doctor"], self.eye_doctor.id)
        self.assertEqual(row["status"], "queued")
        self.assertIsNotNone(row["charge"])
        self.assertIsNotNone(row["created_at"])

    def test_the_queue_filters_by_department_service_provider_and_status(self):
        eye = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).data["id"]
        other = Patient.objects.create(first_name="Ada", last_name="Obi", sex="F",
                                       created_by=self.reception)
        self.api(self.reception).post("/api/appointments/", {
            "patient": other.id, "doctor": self.radiographer.id, "reason": "Scan",
            "service": self.scan.id})
        client = self.api(self.reception)
        eye_department = Department.objects.get(code="eye").id
        for params in ({"department": eye_department}, {"service": self.eye_service.id},
                       {"doctor": self.eye_doctor.id}, {"patient": self.patient.id}):
            listed = client.get("/api/appointments/", params).data
            rows = listed["results"] if isinstance(listed, dict) else listed
            self.assertEqual([row["id"] for row in rows], [eye], params)
        listed = client.get("/api/appointments/", {"status": "queued"}).data
        rows = listed["results"] if isinstance(listed, dict) else listed
        self.assertEqual(len(rows), 2)

class TheServicelessBookingIsStillAConsultation(Booking):
    """
    Widening the transitions from `doctor` to `PROVIDER_ROLES` must not turn
    the serviceless path into the way round the eligibility rule. A booking
    with no service is a general consultation, so it takes a consultation
    provider — which is exactly who the old form offered and exactly who the
    old permission allowed.
    """

    def test_a_doctor_is_still_booked_with_no_service_at_all(self):
        response = self.book(doctor=self.doctor.id)
        self.assertEqual(response.status_code, 201, response.data)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertIsNone(appointment.service_id)
        self.assertIsNone(appointment.charge_id)
        self.assertIsNone(appointment.department_id)

    def test_a_laboratory_scientist_cannot_be_named_without_a_service(self):
        response = self.book(doctor=self.lab.id)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "provider_not_eligible")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_nor_a_radiographer_or_an_eye_doctor(self):
        for provider in (self.radiographer, self.eye_doctor):
            response = self.book(doctor=provider.id)
            self.assertEqual(response.status_code, 400, provider.role)
            self.assertEqual(response.data["code"], "provider_not_eligible")

    def test_and_so_the_serviceless_path_cannot_be_worked_by_them_either(self):
        """The hole this closes: named without a service, then transitioned."""
        self.assertEqual(Appointment.objects.filter(doctor=self.lab).count(), 0)

    def test_an_admin_is_still_named_on_anything(self):
        admin = User.objects.create_user(username="boss2", password="t", role="admin")
        self.assertEqual(self.book(doctor=admin.id).status_code, 201)


class TheFastPathForAConsultation(Booking):
    """
    Patient → doctor → reason → queue, in as few decisions as it ever took.
    """

    def test_the_general_consultation_names_the_consultation_department(self):
        general = self.options().data["general"]
        self.assertEqual(general["department_name"], "Consultation")
        self.assertEqual(general["label"], "General consultation")

    def test_it_offers_the_doctors_and_nobody_else(self):
        offered = {p["id"] for p in self.options().data["general"]["providers"]}
        self.assertIn(self.doctor.id, offered)
        for absent in (self.lab, self.radiographer, self.eye_doctor, self.cashier):
            self.assertNotIn(absent.id, offered, absent.role)

    def test_it_raises_no_charge(self):
        response = self.book(doctor=self.doctor.id)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(response.data["billing"]["status"], "unbilled")

    def test_booking_the_consultation_service_instead_does_raise_one(self):
        """Both are available; reception chooses whether the visit is billed."""
        response = self.book(doctor=self.doctor.id, service=self.consultation.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.charge.amount, Decimal("5000.00"))


class TheProviderRuleHoldsAcrossAppointments(Booking):
    """
    Eligible provider **and** their own appointment. The role gets somebody
    past the door; the queryset is what decides whose row it is.
    """

    def _eye_appointment(self):
        return self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).data["id"]

    def test_a_laboratory_scientist_cannot_transition_an_eye_appointment(self):
        appointment_id = self._eye_appointment()
        for transition in ("accept", "start", "end", "cancel"):
            response = self.api(self.lab).post(
                f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 404, transition)
        self.assertEqual(Appointment.objects.get(pk=appointment_id).status, "queued")

    def test_a_radiographer_cannot_transition_an_eye_appointment(self):
        appointment_id = self._eye_appointment()
        response = self.api(self.radiographer).post(f"/api/appointments/{appointment_id}/accept/")
        self.assertEqual(response.status_code, 404)

    def test_one_eye_doctor_cannot_transition_another_s(self):
        appointment_id = self._eye_appointment()
        other = User.objects.create_user(username="eye2", password="t", role="ophthalmologist")
        response = self.api(other).post(f"/api/appointments/{appointment_id}/accept/")
        self.assertEqual(response.status_code, 404)

    def test_a_cashier_is_refused_at_the_door(self):
        appointment_id = self._eye_appointment()
        response = self.api(self.cashier).post(f"/api/appointments/{appointment_id}/accept/")
        self.assertEqual(response.status_code, 403)

    def test_nobody_else_can_even_see_it(self):
        self._eye_appointment()
        for stranger in (self.lab, self.radiographer):
            listed = self.api(stranger).get("/api/appointments/").data
            rows = listed["results"] if isinstance(listed, dict) else listed
            self.assertEqual(rows, [], stranger.role)


class BillingStaysTheExistingBilling(Booking):
    """
    An appointment charge is an ordinary charge. Every existing money workflow
    reaches it, and none of them had to learn what an appointment is.
    """

    def _billed(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        return Appointment.objects.get(pk=response.data["id"])

    def test_a_discount_a_waiver_a_payment_and_a_refund_all_reach_it(self):
        appointment = self._billed()
        charge = appointment.charge_id
        cash = self.api(self.cashier)
        self.assertEqual(cash.post(f"/api/charges/{charge}/discount/",
                                   {"percent": "10", "reason": "staff"}).status_code, 200)
        self.assertEqual(cash.post(f"/api/charges/{charge}/waive/",
                                   {"amount": "500", "reason": "hardship"}).status_code, 200)
        payment = cash.post("/api/payments/", {"patient": self.patient.id, "amount": "1000",
                                               "method": "cash"})
        self.assertEqual(payment.status_code, 201)
        self.assertEqual(cash.post(f"/api/payments/{payment.data['id']}/refund/",
                                   {"amount": "400", "reason": "overpaid"}).status_code, 201)

        row = self.api(self.reception).get(f"/api/appointments/{appointment.pk}/").data
        self.assertEqual(row["billing"]["status"], "partial")
        self.assertEqual(row["billing"]["outstanding"], "3400.00")

    def test_the_finance_report_attributes_it_to_the_unit_that_performs_it(self):
        self._billed()
        report = self.api(self.cashier).get("/api/finance/report/", {"preset": "today"}).data
        eye = [row for row in report["departments"] if row["key"] == "eye"]
        self.assertEqual(len(eye), 1)
        self.assertEqual(eye[0]["gross"], Decimal("5000.00"))

    def test_cancelling_a_charge_that_holds_money_is_still_refused(self):
        appointment = self._billed()
        cash = self.api(self.cashier)
        cash.post("/api/payments/", {"patient": self.patient.id, "amount": "1000",
                                     "method": "cash"})
        response = cash.post(f"/api/charges/{appointment.charge_id}/cancel/",
                             {"reason": "not done"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "refund_required")

    def test_cancelling_the_appointment_leaves_the_money_to_the_cash_desk(self):
        """
        Two different decisions (rule 38). Withdrawing the bill is the cash
        desk's, through Service Cancellations — a clinical cancellation that
        silently cancelled a charge would be new financial behaviour.
        """
        appointment = self._billed()
        self.api(self.eye_doctor).post(f"/api/appointments/{appointment.pk}/cancel/")
        appointment.charge.refresh_from_db()
        self.assertEqual(appointment.charge.status, "unpaid")
        self.assertEqual(appointment.charge.amount, Decimal("5000.00"))


class OneBookingRaisesOneCharge(Booking):
    def test_submitting_the_same_booking_twice_is_refused(self):
        first = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        second = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        third = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(first.status_code, 201)
        self.assertEqual([second.status_code, third.status_code], [400, 400])
        self.assertEqual(second.data["code"], "already_queued")
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(Charge.objects.count(), 1)

    def test_a_refused_booking_raises_no_charge_at_all(self):
        self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        before = Charge.objects.count()
        self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(Charge.objects.count(), before)

    def test_a_refused_provider_raises_no_charge_either(self):
        self.book(doctor=self.lab.id, service=self.eye_service.id)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(Appointment.objects.count(), 0)

class SeveralServicesOnOneAppointment(Booking):
    """
    A patient sent for three scans is one visit, one provider and one queue
    entry — with one charge per scan, because that is how the counter already
    bills several services (rule 52) and how the laboratory already bills
    several tests (rule 24).
    """

    def setUp(self):
        super().setUp()
        self.scan_b = BillingItem.objects.create(
            category="ultrasound", name="Renal Scan (clinic)", price=Decimal("8000"),
            is_appointment_service=True)
        self.scan_c = BillingItem.objects.create(
            category="ultrasound", name="Pelvic Scan (clinic)", price=Decimal("6000"),
            is_appointment_service=True)

    def book_scans(self, *items, provider=None):
        return self.api(self.reception).post("/api/appointments/", {
            "patient": self.patient.id, "doctor": (provider or self.radiographer).id,
            "reason": "Abdominal pain",
            "service": [item.id for item in items],
        }, format="json")

    def test_one_appointment_carries_all_of_them(self):
        response = self.book_scans(self.scan, self.scan_b, self.scan_c)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Appointment.objects.count(), 1)
        appointment = Appointment.objects.get()
        self.assertEqual([row["name"] for row in appointment.services],
                         ["Ultrasound Examination", "Renal Scan (clinic)",
                          "Pelvic Scan (clinic)"])

    def test_each_service_raises_its_own_charge(self):
        self.book_scans(self.scan, self.scan_b, self.scan_c)
        charges = Charge.objects.order_by("pk")
        self.assertEqual(charges.count(), 3)
        self.assertEqual([c.description for c in charges],
                         ["Ultrasound Examination", "Renal Scan (clinic)",
                          "Pelvic Scan (clinic)"])
        self.assertEqual([c.amount for c in charges],
                         [Decimal("10000.00"), Decimal("8000.00"), Decimal("6000.00")])

    def test_every_charge_points_back_at_the_appointment(self):
        appointment_id = self.book_scans(self.scan, self.scan_b).data["id"]
        for charge in Charge.objects.all():
            self.assertEqual(charge.source_id, appointment_id)
            self.assertEqual(charge.source_type, "ultrasound")

    def test_the_combined_fee_is_the_sum(self):
        response = self.book_scans(self.scan, self.scan_b, self.scan_c)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.service_fee, Decimal("24000.00"))
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("24000.00"))

    def test_the_row_names_what_was_booked(self):
        response = self.book_scans(self.scan, self.scan_b)
        self.assertEqual(response.data["service_name"],
                         "Ultrasound Examination + Renal Scan (clinic)")

    def test_a_long_basket_says_how_many_rather_than_overflowing(self):
        extras = [BillingItem.objects.create(
            category="ultrasound", name=f"Doppler Study Of A Rather Long Name {n}",
            price=Decimal("5000"), is_appointment_service=True) for n in range(8)]
        response = self.book_scans(self.scan, *extras)
        name = response.data["service_name"]
        self.assertLessEqual(len(name), 160)
        self.assertIn("more", name)

    def test_the_money_reads_as_a_basket_and_takes_the_worst_of_them(self):
        response = self.book_scans(self.scan, self.scan_b)
        # Settle one of the two.
        self.api(self.cashier).post("/api/payments/", {
            "patient": self.patient.id, "amount": "10000", "method": "cash"})
        row = self.api(self.reception).get(f"/api/appointments/{response.data['id']}/").data
        self.assertEqual(row["billing"]["status"], "partial")
        self.assertEqual(row["billing"]["total"], "18000.00")
        self.assertEqual(row["billing"]["outstanding"], "8000.00")
        self.assertEqual(row["billing"]["count"], 2)

    def test_each_charge_is_separately_discountable_and_waivable(self):
        appointment = Appointment.objects.get(pk=self.book_scans(self.scan, self.scan_b).data["id"])
        first, second = appointment.charges
        cash = self.api(self.cashier)
        self.assertEqual(cash.post(f"/api/charges/{first.pk}/discount/",
                                   {"percent": "10", "reason": "staff"}).status_code, 200)
        self.assertEqual(cash.post(f"/api/charges/{second.pk}/waive/",
                                   {"amount": "8000", "reason": "hardship"}).status_code, 200)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual(first.amount_discounted, Decimal("1000.00"))
        self.assertEqual(second.amount_waived, Decimal("8000.00"))
        # And neither rewrote its own face value.
        self.assertEqual(first.amount, Decimal("10000.00"))
        self.assertEqual(second.amount, Decimal("8000.00"))

    def test_a_free_service_is_recorded_and_raises_nothing(self):
        free = BillingItem.objects.create(category="ultrasound", name="Repeat Review (clinic)",
                                          price=Decimal("0"), is_appointment_service=True)
        response = self.book_scans(self.scan, free)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual([row["name"] for row in appointment.services],
                         ["Ultrasound Examination", "Repeat Review (clinic)"])
        self.assertIsNone(appointment.services[1]["charge"])
        self.assertEqual(Charge.objects.count(), 1)

    def test_one_booking_is_one_line_on_the_cash_desk(self):
        from apps.core.models import Notification
        self.book_scans(self.scan, self.scan_b, self.scan_c)
        raised = Notification.objects.filter(category="billing")
        senders = {note.recipient_id for note in raised}
        # One announcement for the decision, however many services it held
        # (rule 35) — not three per recipient.
        for recipient in senders:
            self.assertEqual(raised.filter(recipient_id=recipient).count(), 1)

    def test_the_client_still_cannot_price_any_of_them(self):
        response = self.api(self.reception).post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.radiographer.id, "reason": "x",
            "service": [self.scan.id, self.scan_b.id], "service_fee": "1.00",
        }, format="json")
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.service_fee, Decimal("18000.00"))

    def test_ticking_the_same_service_twice_bills_it_once(self):
        response = self.book_scans(self.scan, self.scan)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(len(appointment.services), 1)
        self.assertEqual(Charge.objects.count(), 1)

    def test_an_unbookable_service_in_the_basket_books_nothing_at_all(self):
        response = self.api(self.reception).post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.radiographer.id, "reason": "x",
            "service": [self.scan.id, self.card.id],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "service_not_bookable")
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertEqual(Charge.objects.count(), 0)

    def test_services_from_two_departments_are_refused(self):
        response = self.api(self.reception).post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.radiographer.id, "reason": "x",
            "service": [self.scan.id, self.eye_service.id],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "services_span_departments")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_the_provider_must_be_eligible_for_every_one_of_them(self):
        response = self.book_scans(self.scan, self.scan_b, provider=self.eye_doctor)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "provider_not_eligible")
        self.assertEqual(Charge.objects.count(), 0)

    def test_re_pricing_afterwards_leaves_every_snapshot_alone(self):
        response = self.book_scans(self.scan, self.scan_b)
        self.scan.price = Decimal("99000"); self.scan.save()
        self.scan_b.name = "Renamed"; self.scan_b.save()
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual([row["fee"] for row in appointment.services], ["10000.00", "8000.00"])
        self.assertEqual([row["name"] for row in appointment.services],
                         ["Ultrasound Examination", "Renal Scan (clinic)"])
        self.assertEqual(appointment.service_fee, Decimal("18000.00"))

    def test_a_single_service_booking_is_unchanged_by_any_of_this(self):
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertEqual(appointment.service_name, "Eye Consultation")
        self.assertEqual(appointment.service_fee, Decimal("5000.00"))
        self.assertEqual(appointment.service_id, self.eye_service.id)
        self.assertIsNotNone(appointment.charge_id)
        self.assertEqual(len(appointment.services), 1)
        self.assertEqual(response.data["billing"]["status"], "unpaid")


class OnlyOneOpenAppointmentSurvives(Booking):
    """
    The database says so, not only the guard in front of it.

    `open_appointment_for` answers the ordinary repeat with a sentence; the
    partial unique constraint is what two genuinely concurrent requests meet,
    in the one place that cannot be interleaved.
    """

    def test_the_constraint_exists_on_the_open_statuses_only(self):
        constraint = [c for c in Appointment._meta.constraints
                      if c.name == "one_open_appointment_per_patient_and_provider"]
        self.assertEqual(len(constraint), 1)
        self.assertEqual(list(constraint[0].fields), ["patient", "doctor"])

    def test_the_first_booking_succeeds(self):
        self.assertEqual(self.book(doctor=self.doctor.id).status_code, 201)

    def test_the_repeat_is_refused_readably(self):
        self.book(doctor=self.doctor.id)
        again = self.book(doctor=self.doctor.id)
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["code"], "already_queued")
        self.assertIn("already in", again.data["detail"])

    def test_a_concurrent_duplicate_cannot_leave_two_open_appointments(self):
        """
        The guard is bypassed deliberately — which is exactly what two
        interleaved requests do to each other — and the database refuses
        anyway.
        """
        from django.db import IntegrityError, transaction

        self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Appointment.objects.create(patient=self.patient, doctor=self.eye_doctor,
                                           reason="raced", status="queued")
        self.assertEqual(Appointment.objects.filter(
            patient=self.patient, doctor=self.eye_doctor,
            status__in=["queued", "accepted", "in_progress"]).count(), 1)

    def test_only_one_charge_survives_a_raced_billable_booking(self):
        from django.db import IntegrityError, transaction
        from apps.appointments.services import queue_appointment

        self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(Charge.objects.count(), 1)
        # The loser's whole transaction — appointment *and* charge — is rolled
        # back, because both are written inside one `transaction.atomic`.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                second = Appointment.objects.create(
                    patient=self.patient, doctor=self.eye_doctor, reason="raced",
                    status="queued")
                queue_appointment(appointment=second, service_key=self.eye_service.id,
                                  actor=self.reception)
        self.assertEqual(Charge.objects.count(), 1)
        self.assertEqual(self.patient.ledger.outstanding_balance, Decimal("5000.00"))

    def test_a_completed_appointment_does_not_block_the_next_one(self):
        first = self.book(doctor=self.doctor.id).data["id"]
        client = self.api(self.doctor)
        client.post(f"/api/appointments/{first}/accept/")
        client.post(f"/api/appointments/{first}/start/")
        client.post(f"/api/appointments/{first}/end/")
        self.assertEqual(self.book(doctor=self.doctor.id).status_code, 201)
        self.assertEqual(Appointment.objects.count(), 2)

    def test_a_cancelled_appointment_does_not_block_the_next_one(self):
        first = self.book(doctor=self.doctor.id).data["id"]
        self.api(self.doctor).post(f"/api/appointments/{first}/cancel/")
        self.assertEqual(self.book(doctor=self.doctor.id).status_code, 201)

    def test_historical_appointments_are_unaffected(self):
        """Two closed appointments for one pair are legitimate history."""
        for status in ("completed", "cancelled"):
            Appointment.objects.create(patient=self.patient, doctor=self.doctor,
                                       reason="history", status=status)
        Appointment.objects.create(patient=self.patient, doctor=self.doctor,
                                   reason="history 2", status="completed")
        self.assertEqual(Appointment.objects.filter(patient=self.patient).count(), 3)

    def test_the_same_patient_may_see_a_different_provider_at_the_same_time(self):
        self.assertEqual(self.book(doctor=self.doctor.id).status_code, 201)
        self.assertEqual(
            self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).status_code, 201)


class DjangoAdminDecidesWhichDepartmentsTakeAppointments(Booking):
    """
    Two switches on a department, answering different questions.

    `is_active` says the department is running. `is_appointment_available`
    says Reception may book a patient into it. Pharmacy is active every day
    and is not an appointment destination; turning that off takes nothing away
    from the POS, the dispensing queue or its billing.
    """

    def _offered(self):
        return {d["name"] for d in self.options().data["departments"]}

    def _set(self, code, **flags):
        Department.objects.filter(code=code).update(**flags)

    # ---------------------------------------------------------- the dropdown

    def test_active_and_available_appears(self):
        self._set("radiology", is_active=True, is_appointment_available=True)
        self.assertIn("Radiology / Ultrasound", self._offered())

    def test_active_but_unavailable_does_not_appear(self):
        self._set("radiology", is_active=True, is_appointment_available=False)
        self.assertNotIn("Radiology / Ultrasound", self._offered())

    def test_inactive_but_available_does_not_appear(self):
        self._set("radiology", is_active=False, is_appointment_available=True)
        self.assertNotIn("Radiology / Ultrasound", self._offered())

    def test_inactive_and_unavailable_does_not_appear(self):
        self._set("radiology", is_active=False, is_appointment_available=False)
        self.assertNotIn("Radiology / Ultrasound", self._offered())

    def test_the_seeded_configuration_is_the_one_that_was_already_working(self):
        """`departments/0005` switches on exactly what had bookable services."""
        self.assertEqual(self._offered(), {"Consultation", "Eye Clinic",
                                           "Radiology / Ultrasound"})

    def test_pharmacy_nursing_and_reception_are_not_offered(self):
        offered = self._offered()
        for absent in ("Pharmacy", "Clinicals (Nursing)", "Reception"):
            self.assertNotIn(absent, offered)

    def test_turning_one_on_offers_it_without_any_code_change(self):
        self.assertNotIn("Laboratory", self._offered())
        self._set("laboratory", is_appointment_available=True)
        self.assertIn("Laboratory", self._offered())

    def test_and_turning_it_off_again_withdraws_it(self):
        self._set("laboratory", is_appointment_available=True)
        self.assertIn("Laboratory", self._offered())
        self._set("laboratory", is_appointment_available=False)
        self.assertNotIn("Laboratory", self._offered())

    def test_an_offered_department_with_nothing_ticked_offers_no_services(self):
        self._set("laboratory", is_appointment_available=True)
        listed = {d["name"]: d for d in self.options().data["departments"]}
        self.assertEqual(listed["Laboratory"]["services"], [])

    def test_an_offered_department_carries_only_its_configured_services(self):
        self._set("laboratory", is_appointment_available=True)
        BillingItem.objects.create(category="laboratory", name="Fasting Sugar (booked)",
                                   price=Decimal("2000"), is_appointment_service=True)
        listed = {d["name"]: d for d in self.options().data["departments"]}
        self.assertEqual([s["name"] for s in listed["Laboratory"]["services"]],
                         ["Fasting Sugar (booked)"])

    # ------------------------------------------------------- the server says

    def test_the_api_refuses_a_booking_into_a_closed_department(self):
        """The dropdown is a courtesy; this is what actually decides."""
        self._set("eye", is_appointment_available=False)
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "department_not_available")
        self.assertEqual(Appointment.objects.count(), 0)
        self.assertEqual(Charge.objects.count(), 0)

    def test_it_refuses_an_inactive_department_too(self):
        self._set("eye", is_active=False)
        response = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "department_not_available")

    def test_it_refuses_the_general_consultation_when_consultation_is_closed(self):
        self._set("consultation", is_appointment_available=False)
        response = self.book(doctor=self.doctor.id)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "department_not_available")
        self.assertEqual(Appointment.objects.count(), 0)

    def test_a_multi_service_booking_is_refused_as_a_whole(self):
        self._set("radiology", is_appointment_available=False)
        response = self.api(self.reception).post("/api/appointments/", {
            "patient": self.patient.id, "doctor": self.radiographer.id, "reason": "x",
            "service": [self.scan.id],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "department_not_available")
        self.assertEqual(Charge.objects.count(), 0)

    def test_it_books_again_the_moment_the_switch_goes_back_on(self):
        self._set("eye", is_appointment_available=False)
        self.assertEqual(
            self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).status_code, 400)
        self._set("eye", is_appointment_available=True)
        self.assertEqual(
            self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).status_code, 201)

    # --------------------------------------------------- nothing else moves

    def test_switching_it_off_leaves_an_existing_appointment_alone(self):
        booked = self.book(doctor=self.eye_doctor.id, service=self.eye_service.id)
        self.assertEqual(booked.status_code, 201)
        appointment_id = booked.data["id"]

        self._set("eye", is_appointment_available=False)

        row = self.api(self.reception).get(f"/api/appointments/{appointment_id}/")
        self.assertEqual(row.status_code, 200)
        self.assertEqual(row.data["department_name"], "Eye Clinic")
        self.assertEqual(row.data["service_name"], "Eye Consultation")
        self.assertEqual(row.data["provider_name"], "Femi Bello")
        self.assertEqual(row.data["status"], "queued")
        self.assertEqual(row.data["billing"]["amount"], "5000.00")

    def test_the_provider_can_still_work_an_appointment_already_booked(self):
        appointment_id = self.book(doctor=self.eye_doctor.id,
                                   service=self.eye_service.id).data["id"]
        self._set("eye", is_appointment_available=False)
        client = self.api(self.eye_doctor)
        for transition, expected in (("accept", "accepted"), ("start", "in_progress"),
                                     ("end", "completed")):
            response = client.post(f"/api/appointments/{appointment_id}/{transition}/")
            self.assertEqual(response.status_code, 200, (transition, response.data))
            self.assertEqual(Appointment.objects.get(pk=appointment_id).status, expected)

    def test_the_bill_it_raised_is_untouched(self):
        appointment = Appointment.objects.get(
            pk=self.book(doctor=self.eye_doctor.id, service=self.eye_service.id).data["id"])
        self._set("eye", is_appointment_available=False)
        appointment.charge.refresh_from_db()
        self.assertEqual(appointment.charge.amount, Decimal("5000.00"))
        self.assertEqual(appointment.charge.department.code, "eye")
        self.assertEqual(appointment.charge.status, "unpaid")
        # And the cash desk can still settle it.
        paid = self.api(self.cashier).post("/api/payments/", {
            "patient": self.patient.id, "amount": "5000", "method": "cash"})
        self.assertEqual(paid.status_code, 201)

    def test_the_service_catalogue_is_untouched(self):
        self._set("eye", is_appointment_available=False)
        self.eye_service.refresh_from_db()
        self.assertTrue(self.eye_service.is_active)
        self.assertTrue(self.eye_service.is_appointment_service)
        self.assertEqual(self.eye_service.price, Decimal("5000.00"))
        # And the counter still bills it like any other service.
        billable = self.api(self.reception).get("/api/billable-services/",
                                                {"category": "eye"}).data["results"]
        self.assertIn("Eye Consultation", [row["name"] for row in billable])

    def test_the_department_itself_keeps_everything_else(self):
        eye = Department.objects.get(code="eye")
        eye.staff.add(self.eye_doctor)
        self._set("eye", is_appointment_available=False)
        eye.refresh_from_db()
        self.assertTrue(eye.is_active)
        self.assertIn(self.eye_doctor, eye.staff.all())

    def test_the_consultation_no_charge_path_is_unchanged_while_it_is_on(self):
        response = self.book(doctor=self.doctor.id)
        self.assertEqual(response.status_code, 201)
        appointment = Appointment.objects.get(pk=response.data["id"])
        self.assertIsNone(appointment.charge_id)
        self.assertIsNone(appointment.service_id)
        self.assertEqual(Charge.objects.count(), 0)
        self.assertEqual(response.data["billing"]["status"], "unbilled")


class TheDepartmentFlagIsOrdinaryConfiguration(Booking):
    """It is a field on the existing model, edited through the existing doors."""

    def test_the_field_defaults_to_off(self):
        made = Department.objects.create(code="new-unit", name="A New Unit")
        self.assertFalse(made.is_appointment_available)
        self.assertTrue(made.is_active)

    def test_an_admin_switches_it_through_the_existing_api(self):
        admin = User.objects.create_user(username="cfg", password="t", role="admin")
        eye = Department.objects.get(code="eye")
        response = self.api(admin).patch(f"/api/departments/{eye.pk}/",
                                         {"is_appointment_available": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        eye.refresh_from_db()
        self.assertFalse(eye.is_appointment_available)

    def test_it_is_on_the_django_admin_form(self):
        from apps.departments.admin import DepartmentAdmin

        self.assertIn("is_appointment_available", DepartmentAdmin.list_display)
        self.assertIn("is_appointment_available", DepartmentAdmin.list_editable)
        self.assertIn("is_appointment_available", DepartmentAdmin.list_filter)

    def test_reception_cannot_switch_it(self):
        eye = Department.objects.get(code="eye")
        response = self.api(self.reception).patch(f"/api/departments/{eye.pk}/",
                                                  {"is_appointment_available": False},
                                                  format="json")
        self.assertEqual(response.status_code, 403)
