"""
Who sees a maternity patient, who is responsible for her, and where the front
desk stops.

Three rules, and every test here is one of them:

1. **The department confers visibility; the nurse is only responsibility.**
   Every authorised midwife sees every Maternity patient — assigned, assigned
   to somebody else, or assigned to nobody — because a ward cannot wait for a
   name to be typed before the rest of it can work.
2. **Reception is not a clinical reader.** It finds a mother, registers her,
   puts her in Maternity's care and reads whether she is pregnant. It does not
   read a labour, a partogram, a delivery, a baby or a postpartum check, and
   the API is what refuses — not a hidden button.
3. **A midwife reaches Maternity's patients and no others**, however she asks.
"""
import datetime

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.inpatient.models import Admission, Bed, Ward
from apps.maternity import access, services
from apps.maternity.models import MaternityOption, MaternityVisitType, Pregnancy
from apps.patients.access import patient_queryset_for
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class Ward_(TestCase):
    """A labour ward with two midwives, a mother in it and a mother who is not."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception", first_name="Ngozi",
                                                  last_name="Desk")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.grace = User.objects.create_user(username="grace", password="t",
                                              role="maternity_nurse", first_name="Grace",
                                              last_name="Nwosu")
        self.ada = User.objects.create_user(username="ada", password="t",
                                            role="maternity_nurse", first_name="Ada",
                                            last_name="Okafor")
        self.doctor = User.objects.create_user(username="femi", password="t", role="doctor")
        self.cashier = User.objects.create_user(username="till", password="t", role="cashier")

        self.mother = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                             phone_number="08030000001",
                                             created_by=self.reception)
        # Somebody else's patient entirely: a surgical admission with no
        # maternity anything. She is what "and no others" is measured against.
        self.surgical = Patient.objects.create(first_name="Musa", last_name="Bello", sex="M",
                                               created_by=self.reception)
        theatre, _ = Department.objects.get_or_create(code="theatre",
                                                      defaults={"name": "Theatre"})
        visit = Visit.objects.create(patient=self.surgical, opened_by=self.reception)
        PatientRoute.objects.create(visit=visit, department=theatre, purpose="procedure",
                                    routed_by=self.reception)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def put_in_maternity(self, patient, nurse=None):
        return services.assign_to_maternity(patient=patient, actor=self.reception,
                                            nurse=nurse)


class TheDepartmentIsWhatConfersVisibility(Ward_):
    """Section B: department assignment, not individual assignment."""

    def test_a_midwife_sees_a_maternity_patient_with_no_nurse_assigned(self):
        self.put_in_maternity(self.mother)
        self.assertIsNone(access.assigned_nurse_for(self.mother))
        self.assertIn(self.mother, patient_queryset_for(self.grace))
        self.assertIn(self.mother, patient_queryset_for(self.ada))

    def test_every_midwife_sees_her_not_only_the_one_who_was_named(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        self.assertIn(self.mother, patient_queryset_for(self.grace))
        self.assertIn(self.mother, patient_queryset_for(self.ada),
                      "A ward cannot depend on who happened to be named.")

    def test_a_pregnancy_alone_makes_her_the_wards(self):
        """She was never routed — the clinical record says it instead."""
        services.start_pregnancy(patient=self.mother, actor=self.grace)
        self.assertIn(self.mother, patient_queryset_for(self.ada))

    def test_the_picker_shows_her_on_open_with_no_search_at_all(self):
        self.put_in_maternity(self.mother)
        answer = self.api(self.grace).get("/api/maternity/patients/")
        self.assertEqual(answer.status_code, 200)
        numbers = [row["patient_number"] for row in answer.data["results"]]
        self.assertIn(self.mother.patient_number, numbers)

    def test_the_picker_still_searches(self):
        self.put_in_maternity(self.mother)
        for term in ("Eze", self.mother.patient_number, "08030000001"):
            with self.subTest(term=term):
                found = self.api(self.grace).get("/api/maternity/patients/",
                                                 {"search": term})
                self.assertEqual([row["id"] for row in found.data["results"]],
                                 [self.mother.pk])

    def test_the_row_carries_what_the_desk_needs_to_tell_two_mothers_apart(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        services.start_pregnancy(patient=self.mother, actor=self.grace,
                                 lmp=datetime.date.today() - datetime.timedelta(weeks=30))
        row = self.api(self.grace).get("/api/maternity/patients/").data["results"][0]
        self.assertEqual(row["name"], "Eze, Ngozi")
        self.assertEqual(row["pregnancy_number"], 1)
        self.assertEqual(row["pregnancy_status"], "active")
        self.assertTrue(row["gestation"].startswith("30w"))
        self.assertEqual(row["assigned_nurse"], "Grace Nwosu")


class TheMidwifeReachesMaternityAndNothingElse(Ward_):
    """Section H and N: the scope is the server's, in every direction."""

    def test_a_patient_of_another_department_is_not_on_her_list(self):
        self.put_in_maternity(self.mother)
        self.assertNotIn(self.surgical, patient_queryset_for(self.grace))

    def test_the_picker_does_not_return_the_whole_hospital(self):
        self.put_in_maternity(self.mother)
        rows = self.api(self.grace).get("/api/maternity/patients/").data["results"]
        self.assertNotIn(self.surgical.patient_number,
                         [row["patient_number"] for row in rows])

    def test_searching_by_name_cannot_reach_past_the_scope(self):
        found = self.api(self.grace).get("/api/maternity/patients/", {"search": "Bello"})
        self.assertEqual(found.data["results"], [])

    def test_asking_for_every_patient_answers_with_her_own_scope(self):
        """
        `scope=all` is the desk's way to find a woman not in Maternity yet. For
        a midwife it is the same set, because her authorised set already *is*
        Maternity's — which is the point of scoping on the server.
        """
        self.put_in_maternity(self.mother)
        rows = self.api(self.grace).get("/api/maternity/patients/",
                                        {"scope": "all"}).data["results"]
        numbers = [row["patient_number"] for row in rows]
        self.assertIn(self.mother.patient_number, numbers)
        self.assertNotIn(self.surgical.patient_number, numbers)

    def test_the_generic_patient_list_is_scoped_the_same_way(self):
        """One rule, not one rule per endpoint."""
        self.put_in_maternity(self.mother)
        rows = self.api(self.grace).get("/api/patients/").data
        rows = rows["results"] if isinstance(rows, dict) else rows
        numbers = [row.get("patient_number") or row.get("file_number") for row in rows]
        self.assertIn(self.mother.patient_number, numbers)
        self.assertNotIn(self.surgical.patient_number, numbers)

    def test_she_cannot_reach_an_unrelated_clinical_department(self):
        for path in ("/api/lab-orders/", "/api/finance/report/", "/api/stock-records/",
                     "/api/charges/"):
            with self.subTest(path=path):
                self.assertEqual(self.api(self.grace).get(path).status_code, 403)


class ReceptionFindsHerAndReadsNoChart(Ward_):
    """Section A: the front desk's existing workflow, and its ceiling."""

    def setUp(self):
        super().setUp()
        self.put_in_maternity(self.mother, nurse=self.grace)
        self.pregnancy = services.start_pregnancy(patient=self.mother, actor=self.grace)
        booking = MaternityVisitType.objects.filter(is_active=True).first()
        self.encounter = services.open_encounter(pregnancy=self.pregnancy,
                                                 visit_type=booking, actor=self.grace,
                                                 summary="Fundal height 30cm.")
        self.labour = services.open_labour(pregnancy=self.pregnancy, actor=self.grace)
        services.record_observation(labour=self.labour, actor=self.grace,
                                    cervical_dilation_cm=4)
        self.delivery = services.record_delivery(
            labour=self.labour, actor=self.grace,
            delivery_type=MaternityOption.objects.get(kind="delivery_type", code="svd"),
            newborns=[{"sex": "F", "birth_weight_grams": 3100}])

    def test_it_still_registers_a_patient(self):
        answer = self.api(self.reception).post("/api/patients/", {
            "first_name": "Bisi", "last_name": "Ade", "sex": "F"})
        self.assertEqual(answer.status_code, 201)

    def test_it_still_finds_her(self):
        found = self.api(self.reception).get("/api/patients/",
                                             {"search": self.mother.patient_number})
        rows = found.data["results"] if isinstance(found.data, dict) else found.data
        self.assertEqual(len(rows), 1)

    def test_it_still_reads_the_lookup_that_answers_continue_or_start(self):
        answer = self.api(self.reception).get("/api/maternity/lookup/",
                                              {"patient": self.mother.patient_number})
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.data["can_continue"])
        self.assertFalse(answer.data["can_start"])

    def test_the_lookup_carries_no_clinical_commentary(self):
        answer = self.api(self.reception).get("/api/maternity/lookup/",
                                              {"patient": self.mother.patient_number})
        episode = answer.data["active_pregnancy"]
        for field in ("notes", "gravida", "para"):
            self.assertNotIn(field, episode)

    def test_a_midwife_reading_the_same_lookup_does_get_them(self):
        answer = self.api(self.grace).get("/api/maternity/lookup/",
                                          {"patient": self.mother.patient_number})
        self.assertIn("notes", answer.data["active_pregnancy"])

    def test_it_cannot_read_the_clinical_records(self):
        """
        Every one of these answered 200 to reception before: the labour, the
        partogram, the birth and the babies were all on the front-desk screen.
        """
        forbidden = {
            "the attendance and its clinical summary": "/api/maternity-encounters/",
            "the labour": "/api/labour-episodes/",
            "the delivery": "/api/deliveries/",
            "the birth register": "/api/newborns/",
        }
        for what, path in forbidden.items():
            with self.subTest(what=what):
                self.assertEqual(self.api(self.reception).get(path).status_code, 403)

    def test_it_cannot_read_one_record_by_id_either(self):
        """A list filter is not the control; the endpoint is."""
        for path in (f"/api/labour-episodes/{self.labour.pk}/",
                     f"/api/deliveries/{self.delivery.pk}/",
                     f"/api/maternity-encounters/{self.encounter.pk}/",
                     f"/api/pregnancies/{self.pregnancy.pk}/timeline/"):
            with self.subTest(path=path):
                self.assertEqual(self.api(self.reception).get(path).status_code, 403)

    def test_it_cannot_read_the_partogram(self):
        detail = self.api(self.reception).get(f"/api/labour-episodes/{self.labour.pk}/")
        self.assertEqual(detail.status_code, 403)

    def test_it_cannot_write_any_of_it(self):
        writes = [
            ("/api/maternity-encounters/", {"pregnancy": self.pregnancy.pk}),
            ("/api/labour-episodes/", {"pregnancy": self.pregnancy.pk}),
            (f"/api/labour-episodes/{self.labour.pk}/observations/",
             {"cervical_dilation_cm": 6}),
            (f"/api/deliveries/{self.delivery.pk}/postpartum/", {}),
            (f"/api/deliveries/{self.delivery.pk}/newborns/", {"sex": "M"}),
        ]
        for path, body in writes:
            with self.subTest(path=path):
                self.assertEqual(self.api(self.reception).post(path, body).status_code, 403)

    def test_it_cannot_record_vitals(self):
        """Triage is the nurse's, in maternity as everywhere else."""
        answer = self.api(self.reception).post("/api/vitals/", {
            "patient": self.mother.pk, "bp_systolic": 120, "bp_diastolic": 80})
        self.assertEqual(answer.status_code, 403)

    def test_it_cannot_start_a_pregnancy(self):
        other = Patient.objects.create(first_name="Sade", last_name="Bala", sex="F",
                                       created_by=self.reception)
        answer = self.api(self.reception).post("/api/pregnancies/", {"patient": other.pk})
        self.assertEqual(answer.status_code, 403)

    def test_the_midwife_reads_all_of_it(self):
        for path in ("/api/maternity-encounters/", "/api/labour-episodes/",
                     "/api/deliveries/", "/api/newborns/",
                     f"/api/labour-episodes/{self.labour.pk}/"):
            with self.subTest(path=path):
                self.assertEqual(self.api(self.grace).get(path).status_code, 200)


class AssigningTheResponsibleMidwife(Ward_):
    """Sections C, D, E, L and M, in the order the ward works."""

    def test_the_whole_sequence(self):
        # 1–2. Registered, put in Maternity, nobody named.
        self.put_in_maternity(self.mother)
        self.assertTrue(access.in_maternity(self.mother))
        self.assertIsNone(access.assigned_nurse_for(self.mother))

        # 3–4. Both midwives see her.
        self.assertIn(self.mother, patient_queryset_for(self.grace))
        self.assertIn(self.mother, patient_queryset_for(self.ada))

        # 5–6. Named to Grace; Ada still sees her.
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.grace)
        self.assertEqual(access.assigned_nurse_for(self.mother), self.grace)
        self.assertIn(self.mother, patient_queryset_for(self.ada))

        # 7–8. Handed to Ada; the department does not move.
        before = access.maternity_route_for(self.mother).department_id
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.ada)
        route = access.maternity_route_for(self.mother)
        self.assertEqual(route.assigned_to, self.ada)
        self.assertEqual(route.department_id, before)
        self.assertEqual(route.department.code, "maternity")
        self.assertIn(self.mother, patient_queryset_for(self.grace))

    def test_clearing_the_nurse_leaves_her_exactly_as_visible(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=None)
        self.assertIsNone(access.assigned_nurse_for(self.mother))
        self.assertIn(self.mother, patient_queryset_for(self.ada))

    def test_assigning_twice_does_not_file_her_twice(self):
        self.put_in_maternity(self.mother)
        self.put_in_maternity(self.mother, nurse=self.grace)
        self.assertEqual(
            PatientRoute.objects.filter(visit__patient=self.mother,
                                        purpose="maternity").count(), 1)

    def test_only_a_maternity_nurse_can_be_made_responsible(self):
        self.put_in_maternity(self.mother)
        for who in (self.cashier, self.doctor):
            with self.subTest(role=who.role):
                with self.assertRaises(services.MaternityError) as refused:
                    services.assign_nurse(patient=self.mother, actor=self.reception,
                                          nurse=who)
                self.assertEqual(refused.exception.code, "not_maternity_staff")

    def test_naming_a_nurse_for_a_woman_who_is_not_in_maternity_is_refused(self):
        with self.assertRaises(services.MaternityError) as refused:
            services.assign_nurse(patient=self.surgical, actor=self.reception,
                                  nurse=self.grace)
        self.assertEqual(refused.exception.code, "not_in_maternity")

    def test_the_change_is_audited_with_both_ends_of_it(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.ada)
        row = AuditLog.objects.filter(action="maternity.nurse_assigned").latest("id")
        self.assertEqual(row.actor, self.reception)
        self.assertEqual(row.details["patient"], self.mother.patient_number)
        self.assertEqual(row.details["previous_nurse"], "grace")
        self.assertEqual(row.details["new_nurse"], "ada")
        self.assertIsNotNone(row.created_at)

    def test_putting_her_in_the_department_is_audited_too(self):
        self.put_in_maternity(self.mother)
        row = AuditLog.objects.filter(action="maternity.department_assigned").latest("id")
        self.assertEqual(row.details["department"], "Maternity")

    def test_the_midwife_is_told_she_is_responsible(self):
        from apps.core.models import Notification

        self.put_in_maternity(self.mother, nurse=self.grace)
        self.assertTrue(Notification.objects.filter(recipient=self.grace,
                                                    action_url="/maternity").exists())


class WhoMayAssign(Ward_):
    """Section E: assignment is restricted, and a midwife is not an assigner."""

    def body(self, nurse=None):
        return {"patient": self.mother.patient_number,
                **({"nurse": nurse.pk} if nurse else {})}

    def test_reception_may(self):
        answer = self.api(self.reception).post("/api/maternity/assignment/",
                                               self.body(self.grace))
        self.assertEqual(answer.status_code, 201)
        self.assertEqual(answer.data["assigned_nurse"]["name"], "Grace Nwosu")
        self.assertEqual(answer.data["department"], "Maternity")

    def test_an_administrator_may(self):
        self.assertEqual(
            self.api(self.admin).post("/api/maternity/assignment/", self.body()).status_code,
            201)

    def test_a_midwife_may_not_reassign_every_patient_in_the_hospital(self):
        self.put_in_maternity(self.mother)
        answer = self.api(self.grace).patch("/api/maternity/assignment/",
                                            self.body(self.ada))
        self.assertEqual(answer.status_code, 403)

    def test_a_midwife_may_not_put_a_patient_into_a_department(self):
        answer = self.api(self.grace).post("/api/maternity/assignment/", self.body())
        self.assertEqual(answer.status_code, 403)

    def test_a_cashier_reaches_none_of_it(self):
        self.assertEqual(
            self.api(self.cashier).get("/api/maternity/assignment/",
                                       {"patient": self.mother.pk}).status_code, 403)

    def test_a_midwife_still_reads_who_is_responsible(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        answer = self.api(self.grace).get("/api/maternity/assignment/",
                                          {"patient": self.mother.pk})
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.data["in_maternity"])

    def test_changing_the_nurse_over_the_api_never_moves_the_department(self):
        self.api(self.reception).post("/api/maternity/assignment/", self.body(self.grace))
        answer = self.api(self.reception).patch("/api/maternity/assignment/",
                                                self.body(self.ada))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.data["department_code"], "maternity")
        self.assertEqual(answer.data["assigned_nurse"]["name"], "Ada Okafor")


class TheNurseSelector(Ward_):
    """Section J: midwives, not everyone with a nurse-shaped role."""

    def nurses(self):
        """The roster the *nurse* column is filled from — `?for=nurse`, since
        the endpoint now answers either column's (see `test_maternity_team`)."""
        return self.api(self.reception).get("/api/maternity/staff/", {"for": "nurse"}).data

    def test_it_offers_the_midwives(self):
        self.assertEqual({row["name"] for row in self.nurses()},
                         {"Grace Nwosu", "Ada Okafor"})

    def test_it_offers_no_doctor_cashier_or_general_nurse(self):
        general = User.objects.create_user(username="nia", password="t", role="nurse")
        ids = {row["id"] for row in self.nurses()}
        for who in (self.doctor, self.cashier, general):
            self.assertNotIn(who.pk, ids)

    def test_a_disabled_account_is_not_offered(self):
        self.ada.is_active = False
        self.ada.save(update_fields=["is_active"])
        self.assertNotIn(self.ada.pk, {row["id"] for row in self.nurses()})

    def test_department_staff_are_included_so_an_empty_role_cannot_strand_the_ward(self):
        """
        Rule 16's warning, applied here: the role is what normally answers, and
        the department's own staff list is additive rather than a filter. A
        general nurse on Maternity's staff is offered for the nurse column.
        """
        general = User.objects.create_user(username="nia", password="t", role="nurse",
                                           first_name="Nia", last_name="Bello")
        Department.objects.get(code="maternity").staff.add(general)
        self.assertIn(general.pk, {row["id"] for row in self.nurses()})


class DepartmentTransferIsItsOwnDecision(Ward_):
    """Section L: changing the nurse is not a transfer, and never becomes one."""

    def test_the_transfer_is_the_existing_routing_workflow(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        theatre = Department.objects.get(code="theatre")
        visit = Visit.objects.filter(patient=self.mother).latest("created_at")
        moved = self.api(self.reception).post("/api/patient-routes/", {
            "visit": visit.pk, "department": theatre.pk, "purpose": "procedure"})
        self.assertEqual(moved.status_code, 201)
        # Nothing in maternity did it, and nothing in maternity undid it.
        self.assertEqual(access.maternity_route_for(self.mother).department.code,
                         "maternity")

    def test_a_maternity_route_cannot_name_somebody_who_cannot_do_the_work(self):
        visit = Visit.objects.create(patient=self.mother, opened_by=self.reception)
        maternity = Department.objects.get(code="maternity")
        refused = self.api(self.reception).post("/api/patient-routes/", {
            "visit": visit.pk, "department": maternity.pk, "purpose": "maternity",
            "assigned_to": self.cashier.pk})
        self.assertEqual(refused.status_code, 400)


class TheWardSharesItsBoard(Ward_):
    """Section B, on the queue as well as on the patient list (rule 54)."""

    def test_a_route_a_colleague_has_claimed_is_still_on_the_board(self):
        from apps.workflow.views import work_routes_for

        self.put_in_maternity(self.mother, nurse=self.grace)
        board = work_routes_for(self.ada, include_unit=True)
        self.assertIn(access.maternity_route_for(self.mother), board)

    def test_but_it_is_still_graces_to_work(self):
        from apps.workflow import access as workflow_access
        from apps.workflow.views import PURPOSE_ROLE, ROLE_PURPOSES

        self.put_in_maternity(self.mother, nurse=self.grace)
        route = access.maternity_route_for(self.mother)
        self.assertTrue(workflow_access.may_work(self.grace, route,
                                                 role_purposes=ROLE_PURPOSES,
                                                 purpose_role=PURPOSE_ROLE))
        self.assertFalse(workflow_access.may_work(self.ada, route,
                                                  role_purposes=ROLE_PURPOSES,
                                                  purpose_role=PURPOSE_ROLE))


class TheDashboardKeepsBothNumbers(Ward_):
    """Section K: department patients and assigned patients are not one figure."""

    def cards(self, user):
        answer = self.api(user).get("/api/dashboard/")
        return {card["key"]: card["value"] for card in answer.data["cards"] if "key" in card}

    def test_the_department_count_does_not_need_an_assignment(self):
        self.put_in_maternity(self.mother)
        cards = self.cards(self.grace)
        self.assertEqual(cards["maternity_department_patients"], 1)
        self.assertEqual(cards["maternity_assigned_to_me"], 0)

    def test_assigning_moves_only_the_second_number(self):
        self.put_in_maternity(self.mother, nurse=self.grace)
        self.assertEqual(self.cards(self.grace)["maternity_assigned_to_me"], 1)
        self.assertEqual(self.cards(self.ada)["maternity_assigned_to_me"], 0)
        self.assertEqual(self.cards(self.ada)["maternity_department_patients"], 1)


class NothingHereBuiltASecondAnything(Ward_):
    """Section P, asserted rather than asserted about."""

    def test_the_patient_has_no_department_or_nurse_column(self):
        columns = {field.name for field in Patient._meta.get_fields()}
        for invented in ("department", "assigned_nurse", "maternity_nurse", "ward"):
            self.assertNotIn(invented, columns)

    def test_the_assignment_is_an_ordinary_patient_route(self):
        route = self.put_in_maternity(self.mother, nurse=self.grace)
        self.assertIsInstance(route, PatientRoute)
        self.assertEqual(route.purpose, "maternity")
        self.assertEqual(route.department.code, "maternity")
        self.assertEqual(route.assigned_to, self.grace)

    def test_maternity_still_holds_exactly_its_own_models(self):
        from django.apps import apps as django_apps

        names = {model.__name__ for model in
                 django_apps.get_app_config("maternity").get_models()}
        self.assertEqual(names, {
            "Pregnancy", "MaternityEncounter", "MaternityVisitType", "MaternityOption",
            "LabourEpisode", "LabourObservation", "Delivery", "Newborn",
            "PostpartumVisit",
            # The correction trail — one model over all of them.
            "MaternityAmendment"})


class ThePickerCostsTheSameWhateverTheWardHolds(Ward_):
    """
    A fifty-row picker must not be fifty round trips.

    It used to ask for the responsible midwife once per row. The figure below
    is a ceiling rather than an exact count — what matters is that it does not
    move with the number of mothers on the ward.
    """

    def ward_of(self, size):
        for index in range(size):
            mother = Patient.objects.create(first_name=f"Mother{index}", last_name="Ward",
                                            sex="F", created_by=self.reception)
            self.put_in_maternity(mother, nurse=self.grace if index % 2 else None)
            services.start_pregnancy(patient=mother, actor=self.grace)

    def test_four_mothers_and_twenty_cost_the_same(self):
        client = self.api(self.grace)
        self.ward_of(4)
        with self.assertNumQueries(5) as small:
            client.get("/api/maternity/patients/")
        self.ward_of(16)
        with self.assertNumQueries(len(small.captured_queries)):
            answer = client.get("/api/maternity/patients/")
        self.assertEqual(len(answer.data["results"]), 20)

    def test_the_responsible_midwife_is_still_right_on_every_row(self):
        self.ward_of(4)
        rows = self.api(self.grace).get("/api/maternity/patients/").data["results"]
        named = {row["name"]: row["assigned_nurse"] for row in rows}
        self.assertEqual(set(named.values()), {None, "Grace Nwosu"})
