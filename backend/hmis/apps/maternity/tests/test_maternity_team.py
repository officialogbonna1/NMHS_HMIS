"""
**The department is the team; the assignments are responsibility.**

Maternity is a workspace a team shares, not a list of patients each staff
member privately owns. So the rule these tests hold is:

    Department = Maternity     → who may see and work her
    Assigned nurse  (optional) → who is answerable, nursing
    Assigned doctor (optional) → who is answerable, medically

and the two assignments are *labels on a shared record*, never the gate. A
mother must not vanish from the ward because a colleague's name is on her —
which is precisely what `doctor_patient_q` alone would have done, and did.

Nothing here invented a mechanism. Team membership is the Maternity
`Department`'s own staff list, the clause `nurse_patient_q` and
`work_routes_for` have always used; the responsible doctor is
`Visit.attending_doctor`, the column that has always meant that; the
responsible midwife is `PatientRoute.assigned_to`. No `MaternityDoctor`, no
second RBAC, no second patient record.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.departments.models import Department
from apps.maternity import access, services
from apps.maternity.models import (Delivery, LabourEpisode, MaternityOption,
                                   MaternityVisitType, Newborn, Pregnancy)
from apps.patients.access import patient_queryset_for
from apps.patients.models import Patient
from apps.workflow.models import PatientRoute, Visit


class MaternityTeam(TestCase):
    """Grace and Ada on the nursing rota, Dr Dara and Dr Paul on the medical."""

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception")
        self.admin = User.objects.create_user(username="boss", password="t", role="admin")
        self.maternity = Department.objects.get(code="maternity")

        self.grace = User.objects.create_user(username="grace", password="t",
                                              role="maternity_nurse", first_name="Grace",
                                              last_name="Nwosu")
        self.ada = User.objects.create_user(username="ada", password="t",
                                            role="maternity_nurse", first_name="Ada",
                                            last_name="Okafor")
        # Doctors become maternity doctors the existing way: the department's
        # own staff list. No role was added for it.
        self.dara = User.objects.create_user(username="dara", password="t", role="doctor",
                                             first_name="Dara", last_name="Ibe")
        self.paul = User.objects.create_user(username="paul", password="t", role="doctor",
                                             first_name="Paul", last_name="Eze")
        self.maternity.staff.add(self.dara, self.paul)
        # And a doctor who has nothing to do with Maternity.
        self.stranger = User.objects.create_user(username="kemi", password="t",
                                                 role="doctor", first_name="Kemi",
                                                 last_name="Alao")

        self.mother = Patient.objects.create(first_name="Ngozi", last_name="Eze", sex="F",
                                             created_by=self.reception)
        self.route = services.assign_to_maternity(patient=self.mother,
                                                  actor=self.reception)
        self.pregnancy = services.start_pregnancy(patient=self.mother, actor=self.grace)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def sees(self, user):
        return patient_queryset_for(user).filter(pk=self.mother.pk).exists()

    def deliver(self, babies=2):
        """A delivery with `babies` newborns, so a multiple birth is on file."""
        labour = services.open_labour(pregnancy=self.pregnancy, actor=self.grace)
        services.record_observation(labour=labour, actor=self.grace,
                                    cervical_dilation_cm=6)
        return services.record_delivery(
            labour=labour, actor=self.grace,
            delivery_type=MaternityOption.objects.get(kind="delivery_type", code="svd"),
            newborns=[{"sex": "F", "birth_weight_grams": 2400 + index}
                      for index in range(babies)])


class APatientNeedsNeitherAssignment(MaternityTeam):
    """Cases 1 and 2: both fields are optional, and the ward works either way."""

    def test_she_can_be_in_maternity_with_no_nurse(self):
        self.assertTrue(access.in_maternity(self.mother))
        self.assertIsNone(access.assigned_nurse_for(self.mother))
        self.assertTrue(self.sees(self.grace))

    def test_she_can_be_in_maternity_with_no_doctor(self):
        self.assertIsNone(access.assigned_doctor_for(self.mother))
        self.assertTrue(self.sees(self.dara))

    def test_the_screen_is_told_both_are_unassigned_rather_than_left_blank(self):
        state = self.api(self.reception).get("/api/maternity/assignment/",
                                             {"patient": self.mother.pk}).data
        self.assertTrue(state["in_maternity"])
        self.assertEqual(state["department"], "Maternity")
        self.assertIsNone(state["assigned_nurse"])
        self.assertIsNone(state["assigned_doctor"])


class TheTeamSeesHerWhoeverIsNamed(MaternityTeam):
    """Cases 3, 4, 9 and 10 — the heart of it."""

    def setUp(self):
        super().setUp()
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.grace)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)

    def test_all_four_see_her(self):
        for who in (self.grace, self.ada, self.dara, self.paul):
            with self.subTest(who=who.username):
                self.assertTrue(self.sees(who))

    def test_the_other_nurse_sees_a_patient_assigned_to_grace(self):
        self.assertEqual(access.assigned_nurse_for(self.mother), self.grace)
        self.assertTrue(self.sees(self.ada))

    def test_the_other_doctor_sees_a_patient_assigned_to_dara(self):
        self.assertEqual(access.assigned_doctor_for(self.mother), self.dara)
        self.assertTrue(self.sees(self.paul))

    def test_the_unassigned_doctor_reads_the_clinical_records(self):
        """
        Case 9. Dr Paul is not the assigned doctor and reads the pregnancy, the
        antenatal course, the labour, the partogram, the delivery and the
        babies — because he is on the team, not because he was named.
        """
        delivery = self.deliver(babies=2)
        labour = delivery.labour
        services.open_encounter(pregnancy=self.pregnancy, actor=self.grace,
                                visit_type=MaternityVisitType.objects.filter(
                                    is_active=True).first())
        self.assertTrue(self.sees(self.paul), "she is on his list before he opens her")
        client = self.api(self.paul)
        for path in ("/api/pregnancies/", "/api/maternity-encounters/",
                     "/api/labour-episodes/", "/api/deliveries/", "/api/newborns/",
                     f"/api/labour-episodes/{labour.pk}/",
                     f"/api/pregnancies/{self.pregnancy.pk}/timeline/"):
            with self.subTest(path=path):
                answer = client.get(path)
                self.assertEqual(answer.status_code, 200)
                rows = answer.data
                if isinstance(rows, dict) and "results" in rows:
                    self.assertTrue(rows["results"], f"{path} came back empty")

    def test_the_unassigned_doctor_reads_the_partogram_itself(self):
        delivery = self.deliver()
        detail = self.api(self.paul).get(f"/api/labour-episodes/{delivery.labour.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["observations"])

    def test_the_unassigned_nurse_keeps_her_existing_access(self):
        """
        Case 10 — Ada records vitals and opens an encounter for Grace's
        patient. She is not the named midwife, and the ward is what gives her
        both.
        """
        from django.utils import timezone

        vitals = self.api(self.ada).post("/api/vitals/", {
            "patient": self.mother.pk, "visit_time": timezone.now().isoformat(),
            "bp_systolic": 118, "bp_diastolic": 76})
        self.assertEqual(vitals.status_code, 201, vitals.data)
        opened = self.api(self.ada).post("/api/maternity-encounters/", {
            "pregnancy": self.pregnancy.pk,
            "visit_type": MaternityVisitType.objects.filter(is_active=True).first().pk})
        self.assertEqual(opened.status_code, 201)

    def test_every_baby_of_a_multiple_birth_is_visible_to_the_team(self):
        """Case 13."""
        delivery = self.deliver(babies=3)
        for who in (self.paul, self.ada):
            with self.subTest(who=who.username):
                babies = self.api(who).get("/api/newborns/", {"delivery": delivery.pk})
                rows = babies.data.get("results", babies.data)
                self.assertEqual(len(rows), 3)
                self.assertEqual(sorted(row["birth_order"] for row in rows), [1, 2, 3])

    def test_the_picker_shows_her_to_the_whole_team_with_both_names_on_the_row(self):
        for who in (self.grace, self.ada, self.dara, self.paul):
            with self.subTest(who=who.username):
                rows = self.api(who).get("/api/maternity/patients/").data["results"]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["assigned_nurse"], "Grace Nwosu")
                self.assertEqual(rows[0]["assigned_doctor"], "Dara Ibe")


class AssignmentIsNotTheGate(MaternityTeam):
    """The negative of the same rule: clearing a name takes her from nobody."""

    def test_clearing_the_doctor_leaves_the_team_exactly_as_it_was(self):
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        services.assign_doctor(patient=self.mother, actor=self.reception, doctor=None)
        self.assertIsNone(access.assigned_doctor_for(self.mother))
        for who in (self.dara, self.paul, self.grace, self.ada):
            self.assertTrue(self.sees(who))

    def test_a_doctor_not_on_the_team_and_not_assigned_gains_no_patient(self):
        """
        Case 12. She is not his patient and not on his list — the boundary this
        work draws, and the one that decides what a screen shows him.

        **What it deliberately does not claim**: that he cannot reach the
        maternity *endpoints*. `MATERNITY_ROLES` holds every doctor and every
        nurse, which rule 56 records as a deliberate breadth — "a hospital this
        size covers maternity with whoever is on" — and narrowing it is a
        decision of its own, not a side effect of adding an assigned doctor.
        `TheEndpointsKeepTheBreadthRule56Chose` below states it out loud so
        nobody reads this test as more than it is.
        """
        self.assertFalse(self.sees(self.stranger))
        rows = self.api(self.stranger).get("/api/maternity/patients/").data["results"]
        self.assertEqual(rows, [])

    def test_naming_the_stranger_is_what_gives_him_the_patient(self):
        """
        And it is the *existing* rule that does it — `Visit.attending_doctor`
        read by `doctor_patient_q` — not a special case for assignment.
        """
        self.maternity.staff.add(self.stranger)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.stranger)
        self.maternity.staff.remove(self.stranger)
        self.assertFalse(access.in_maternity_team(self.stranger))
        self.assertTrue(self.sees(self.stranger), "the named doctor keeps his patient")

    def test_an_unrelated_role_gains_nothing(self):
        """Case 12, the rest of the hospital."""
        for role in ("pharmacist", "laboratory", "cashier", "accountant"):
            who = User.objects.create_user(username=f"x_{role}", password="t", role=role)
            with self.subTest(role=role):
                self.assertEqual(
                    self.api(who).get("/api/labour-episodes/").status_code, 403)
                self.assertEqual(
                    self.api(who).get("/api/maternity/patients/").status_code, 403)


class TheEndpointsKeepTheBreadthRule56Chose(MaternityTeam):
    """
    The one thing this work did **not** change, written down so it is a
    decision rather than an oversight.

    `MATERNITY_ROLES` is doctor + nurse + maternity_nurse, and the maternity
    viewsets filter their rows by nothing — so a doctor with no connection to
    Maternity can still *list* the ward's records. Rule 56 chose that: "every
    nurse and doctor may work maternity — deliberate, and the same breadth
    every other unit already has."

    Row-level scoping was written and **reverted**: it is a restriction rather
    than the additive change this was asked for, it would have rewritten 54
    existing Phase-2 tests, and tightening a documented breadth is a call for
    the hospital to make. If it is ever made, this class is what fails first
    and says where to look.
    """

    def test_an_unrelated_doctor_still_reaches_the_endpoints(self):
        self.deliver()
        for path in ("/api/labour-episodes/", "/api/deliveries/", "/api/newborns/"):
            with self.subTest(path=path):
                self.assertEqual(self.api(self.stranger).get(path).status_code, 200)

    def test_which_is_why_the_patient_list_is_where_the_team_boundary_is(self):
        self.assertFalse(self.sees(self.stranger))
        self.assertTrue(self.sees(self.paul))


class TheThreeDecisionsAreSeparate(MaternityTeam):
    """Cases 5, 6, 7 and 8."""

    def setUp(self):
        super().setUp()
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.grace)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)

    def test_changing_the_doctor_leaves_the_nurse_alone(self):
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)
        self.assertEqual(access.assigned_doctor_for(self.mother), self.paul)
        self.assertEqual(access.assigned_nurse_for(self.mother), self.grace)

    def test_changing_the_nurse_leaves_the_doctor_alone(self):
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.ada)
        self.assertEqual(access.assigned_nurse_for(self.mother), self.ada)
        self.assertEqual(access.assigned_doctor_for(self.mother), self.dara)

    def test_neither_moves_the_department(self):
        before = access.maternity_route_for(self.mother).department_id
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.ada)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)
        route = access.maternity_route_for(self.mother)
        self.assertEqual(route.department_id, before)
        self.assertEqual(route.department.code, "maternity")
        self.assertTrue(access.in_maternity(self.mother))

    def test_neither_creates_a_record_of_any_kind(self):
        """
        Case 8. Changing who is responsible is a column moving, and nothing
        else: no patient, no pregnancy, no labour, no delivery, no baby, and
        no second route to the department.
        """
        delivery = self.deliver(babies=2)
        before = {model.__name__: model.objects.count() for model in
                  (Patient, Pregnancy, LabourEpisode, Delivery, Newborn, Visit,
                   PatientRoute)}
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=self.ada)
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)
        services.assign_nurse(patient=self.mother, actor=self.reception, nurse=None)
        after = {model.__name__: model.objects.count() for model in
                 (Patient, Pregnancy, LabourEpisode, Delivery, Newborn, Visit,
                  PatientRoute)}
        self.assertEqual(before, after)
        self.assertEqual(delivery.newborns.count(), 2)

    def test_the_api_refuses_to_change_both_at_once(self):
        answer = self.api(self.reception).patch("/api/maternity/assignment/", {
            "patient": self.mother.pk, "nurse": self.ada.pk, "doctor": self.paul.pk})
        self.assertEqual(answer.status_code, 400)
        self.assertEqual(answer.data["code"], "one_assignment_at_a_time")
        # And nothing moved.
        self.assertEqual(access.assigned_nurse_for(self.mother), self.grace)
        self.assertEqual(access.assigned_doctor_for(self.mother), self.dara)

    def test_each_change_is_audited_with_both_ends_of_it(self):
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.paul)
        row = AuditLog.objects.filter(action="maternity.doctor_assigned").latest("id")
        self.assertEqual(row.details["previous_doctor"], "dara")
        self.assertEqual(row.details["new_doctor"], "paul")
        self.assertEqual(row.details["patient"], self.mother.patient_number)


class NamingSomebodyWhoCannotDoTheWork(MaternityTeam):
    """One column each, meaning one thing each."""

    def test_a_midwife_cannot_be_filed_as_the_responsible_doctor(self):
        with self.assertRaises(services.MaternityError) as refused:
            services.assign_doctor(patient=self.mother, actor=self.reception,
                                   doctor=self.grace)
        self.assertEqual(refused.exception.code, "not_maternity_staff")

    def test_a_doctor_cannot_be_filed_as_the_responsible_midwife(self):
        with self.assertRaises(services.MaternityError) as refused:
            services.assign_nurse(patient=self.mother, actor=self.reception,
                                  nurse=self.dara)
        self.assertEqual(refused.exception.code, "not_maternity_staff")

    def test_a_cashier_is_refused_either_way(self):
        till = User.objects.create_user(username="till", password="t", role="cashier")
        for as_doctor in (True, False):
            change = services.assign_doctor if as_doctor else services.assign_nurse
            field = "doctor" if as_doctor else "nurse"
            with self.subTest(as_doctor=as_doctor):
                with self.assertRaises(services.MaternityError):
                    change(patient=self.mother, actor=self.reception, **{field: till})


class TheSelectors(MaternityTeam):
    """Each column is filled from its own roster."""

    def test_the_nurse_selector_offers_midwives(self):
        rows = self.api(self.reception).get("/api/maternity/staff/", {"for": "nurse"}).data
        self.assertEqual({row["name"] for row in rows}, {"Grace Nwosu", "Ada Okafor"})

    def test_the_doctor_selector_offers_the_departments_doctors(self):
        rows = self.api(self.reception).get("/api/maternity/staff/",
                                            {"for": "doctor"}).data
        self.assertEqual({row["name"] for row in rows}, {"Dara Ibe", "Paul Eze"})
        self.assertNotIn("Kemi Alao", {row["name"] for row in rows})

    def test_with_nobody_on_the_staff_list_every_doctor_is_offered(self):
        """
        Rule 16's warning: a ward that cannot name a doctor at all is stranded.
        The fallback widens *who may be named*, never who sees the ward.
        """
        self.maternity.staff.clear()
        rows = self.api(self.reception).get("/api/maternity/staff/",
                                            {"for": "doctor"}).data
        self.assertIn("Kemi Alao", {row["name"] for row in rows})
        self.assertFalse(access.in_maternity_team(self.stranger),
                         "being offered is not being on the team")

    def test_the_whole_roster_is_the_two_put_together(self):
        rows = self.api(self.reception).get("/api/maternity/staff/").data
        self.assertEqual({row["name"] for row in rows},
                         {"Grace Nwosu", "Ada Okafor", "Dara Ibe", "Paul Eze"})


class ReceptionGainsNothingFromEitherAssignment(MaternityTeam):
    """Case 11, restated against the new fields."""

    def test_it_may_name_both_and_read_neither_record(self):
        named = self.api(self.reception).patch("/api/maternity/assignment/", {
            "patient": self.mother.pk, "doctor": self.dara.pk})
        self.assertEqual(named.status_code, 200)
        self.assertEqual(named.data["assigned_doctor"]["name"], "Dara Ibe")
        for path in ("/api/labour-episodes/", "/api/deliveries/", "/api/newborns/",
                     "/api/maternity-encounters/"):
            with self.subTest(path=path):
                self.assertEqual(self.api(self.reception).get(path).status_code, 403)

    def test_a_maternity_nurse_still_may_not_assign(self):
        answer = self.api(self.grace).patch("/api/maternity/assignment/", {
            "patient": self.mother.pk, "doctor": self.dara.pk})
        self.assertEqual(answer.status_code, 403)


class NothingElseWasBuilt(MaternityTeam):
    """Case 15's companion: the mechanisms are the ones already here."""

    def test_the_responsible_doctor_is_the_visits_attending_doctor(self):
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.dara)
        self.assertEqual(self.route.visit.__class__, Visit)
        self.route.visit.refresh_from_db()
        self.assertEqual(self.route.visit.attending_doctor, self.dara)

    def test_no_maternity_doctor_or_nurse_model_exists(self):
        from django.apps import apps as django_apps

        names = {model.__name__ for model in
                 django_apps.get_app_config("maternity").get_models()}
        for invented in ("MaternityDoctor", "MaternityNurse", "MaternityTeam",
                         "MaternityAssignment"):
            self.assertNotIn(invented, names)

    def test_team_membership_is_the_departments_own_staff_list(self):
        self.assertTrue(access.in_maternity_team(self.dara))
        self.maternity.staff.remove(self.dara)
        self.assertFalse(access.in_maternity_team(self.dara))

    def test_the_department_text_field_works_too_where_that_is_how_it_is_set(self):
        """The `user.department` fallback `nurse_patient_q` has always read."""
        self.maternity.staff.clear()
        self.stranger.department = "Maternity"
        self.stranger.save(update_fields=["department"])
        self.assertTrue(access.in_maternity_team(self.stranger))
        self.assertTrue(self.sees(self.stranger))


class DoctorPlusMaternityThroughTheGeneralMechanism(MaternityTeam):
    """
    §23's scenario, end to end: a doctor whose primary department is General
    Medicine is posted to Maternity by an administrator and becomes a **doctor
    in Maternity** — no maternity-specific authorisation anywhere.

    The posting is `Department.staff`, the hospital's own relation, read
    through `accounts.departments.works_in`. Maternity contributes the role
    half and nothing else.
    """

    def setUp(self):
        super().setUp()
        from apps.departments.models import Department

        self.medicine, _ = Department.objects.get_or_create(
            code="general-medicine", defaults={"name": "General Medicine"})
        # Doctor A: General Medicine only, to begin with.
        self.doctor_a = User.objects.create_user(username="dr_a", password="t",
                                                 role="doctor", first_name="Ada",
                                                 last_name="Mensah",
                                                 department="General Medicine")

    def test_before_the_posting_maternity_is_closed_to_her(self):
        self.assertFalse(access.in_maternity_team(self.doctor_a))
        self.assertFalse(self.sees(self.doctor_a))
        rows = self.api(self.doctor_a).get("/api/maternity/patients/").data["results"]
        self.assertEqual(rows, [])

    def test_the_administrator_posts_her_and_she_is_a_doctor_in_maternity(self):
        self.maternity.staff.add(self.doctor_a)
        self.assertTrue(access.in_maternity_team(self.doctor_a))
        self.assertTrue(self.sees(self.doctor_a))
        rows = self.api(self.doctor_a).get("/api/maternity/patients/").data["results"]
        self.assertEqual([row["patient_number"] for row in rows],
                         [self.mother.patient_number])

    def test_and_reaches_the_records_a_doctor_may_reach(self):
        self.maternity.staff.add(self.doctor_a)
        delivery = self.deliver(babies=2)
        client = self.api(self.doctor_a)
        for path in ("/api/pregnancies/", "/api/labour-episodes/", "/api/deliveries/",
                     "/api/newborns/", f"/api/pregnancies/{self.pregnancy.pk}/timeline/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)
        babies = client.get("/api/newborns/", {"delivery": delivery.pk})
        self.assertEqual(len(babies.data.get("results", babies.data)), 2)

    def test_without_being_assigned_to_a_single_patient(self):
        """§13 — department authorisation is not patient assignment."""
        self.maternity.staff.add(self.doctor_a)
        self.assertNotEqual(access.assigned_doctor_for(self.mother), self.doctor_a)
        self.assertTrue(self.sees(self.doctor_a))

    def test_her_general_medicine_work_is_untouched(self):
        self.maternity.staff.add(self.doctor_a)
        from apps.accounts.departments import works_in

        self.assertTrue(works_in(self.doctor_a, "general-medicine"))
        self.assertTrue(works_in(self.doctor_a, "maternity"))

    def test_the_posting_grants_no_other_department_and_no_other_role(self):
        """§17 — role AND department, never department alone."""
        self.maternity.staff.add(self.doctor_a)
        from apps.accounts.departments import works_in

        for elsewhere in ("pharmacy", "laboratory", "radiology"):
            with self.subTest(department=elsewhere):
                self.assertFalse(works_in(self.doctor_a, elsewhere))
        client = self.api(self.doctor_a)
        # Desks her *role* does not reach. `/api/lab-orders/` is deliberately
        # not on this list: a doctor orders laboratory tests, so reading them
        # is a doctor capability and has nothing to do with where she is
        # posted — which is the rule stated from the other side.
        for path in ("/api/finance/report/", "/api/stock-records/", "/api/sales/",
                     "/api/adjustments/"):
            with self.subTest(path=path):
                self.assertIn(client.get(path).status_code, (403, 404),
                              f"{path} opened to a doctor posted to Maternity")
        self.assertEqual(client.get("/api/lab-orders/").status_code, 200,
                         "a doctor orders laboratory tests whichever ward she is on")

    def test_she_does_not_become_a_midwife(self):
        """A doctor posted to Maternity is a doctor there, not a nurse."""
        self.maternity.staff.add(self.doctor_a)
        from django.utils import timezone

        # Recording vitals is `NURSING_ROLES`, which a doctor is not on.
        answer = self.api(self.doctor_a).post("/api/vitals/", {
            "patient": self.mother.pk, "visit_time": timezone.now().isoformat(),
            "bp_systolic": 120, "bp_diastolic": 80})
        self.assertEqual(answer.status_code, 403)

    def test_and_is_offered_as_a_maternity_doctor_only_once_posted(self):
        """§14 — the selector offers exactly who the server will accept."""
        before = {u.pk for u in access.maternity_doctors()}
        self.maternity.staff.add(self.doctor_a)
        after = {u.pk for u in access.maternity_doctors()}
        self.assertNotIn(self.doctor_a.pk, before)
        self.assertIn(self.doctor_a.pk, after)
        # And she can then actually be made responsible.
        services.assign_doctor(patient=self.mother, actor=self.reception,
                               doctor=self.doctor_a)
        self.assertEqual(access.assigned_doctor_for(self.mother), self.doctor_a)

    def test_removing_the_posting_closes_maternity_and_leaves_medicine(self):
        """§23's second half — independently controllable."""
        from apps.accounts.departments import works_in

        self.maternity.staff.add(self.doctor_a)
        self.assertTrue(self.sees(self.doctor_a))
        self.maternity.staff.remove(self.doctor_a)
        self.assertFalse(access.in_maternity_team(self.doctor_a))
        self.assertFalse(self.sees(self.doctor_a))
        self.assertTrue(works_in(self.doctor_a, "general-medicine"))
        self.assertEqual(
            self.api(self.doctor_a).get("/api/maternity/patients/").data["results"], [])

    def test_the_posting_moved_no_patient_data_at_all(self):
        """§25 — authorising a doctor transfers nothing."""
        before = {
            "pregnancy": self.pregnancy.pk,
            "nurse": access.assigned_nurse_for(self.mother),
            "doctor": access.assigned_doctor_for(self.mother),
            "department": access.maternity_route_for(self.mother).department_id,
            "patients": Patient.objects.count(),
        }
        self.maternity.staff.add(self.doctor_a)
        after = {
            "pregnancy": self.mother.pregnancies.get().pk,
            "nurse": access.assigned_nurse_for(self.mother),
            "doctor": access.assigned_doctor_for(self.mother),
            "department": access.maternity_route_for(self.mother).department_id,
            "patients": Patient.objects.count(),
        }
        self.assertEqual(before, after)
