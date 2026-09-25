"""
**The four readings of "where does this person work" agree.**

This file exists because they did not. Before `accounts/departments.py` there
were four hand-rolled spellings of the same question, and each carried a
different set of clauses:

    site                                 relation  text-by-name  text-by-code
    work_routes_for  (what you see)         yes        yes           yes
    may_work         (what you may do)      yes        yes           no
    nurse_patient_q  (your patient list)    yes        yes           no
    route_targets    (what you are told)    yes        no            no

Two real faults followed. An account authorised by the *code* in its text
could see a route in its queue and was refused when it tried to work it. And
an account authorised by its text at all could see a route in its queue and
was **never notified** about it — which is rule 16's warning read from the
other side:

    "Change `work_routes_for` and `route_targets` together — they must agree,
     or somebody is notified about work they cannot see."

So the change to `route_targets` is not an accidental broadening of the bell.
It is the correction that makes the bell agree with the queue, and this file is
what stops the four drifting apart again: each test asks one question through
**two** of the four readings and requires the same answer.
"""
from django.test import TestCase

from apps.accounts.departments import authorized_departments, staff_of, works_in
from apps.accounts.models import User
from apps.departments.models import Department
from apps.patients.models import Patient
from apps.workflow import access as workflow_access
from apps.workflow.models import PatientRoute, Visit
from apps.workflow.views import (PURPOSE_ROLE, ROLE_PURPOSES, route_targets,
                                 work_routes_for)


class OneDepartment(TestCase):
    """
    A route with a purpose **no role owns** (`other`), which is the only case
    where department membership decides anything — rule 16: the purpose is the
    reliable signal, and the department is the fallback beneath it.
    """

    def setUp(self):
        self.reception = User.objects.create_user(username="rec", password="t",
                                                  role="reception")
        self.theatre = Department.objects.get(code="theatre")
        self.patient = Patient.objects.create(first_name="Musa", last_name="Bello",
                                              sex="M", created_by=self.reception)
        visit = Visit.objects.create(patient=self.patient, opened_by=self.reception)
        self.route = PatientRoute.objects.create(
            visit=visit, department=self.theatre, purpose="other",
            routed_by=self.reception)

        # Three ways of being the theatre's, and one way of not being.
        self.by_relation = User.objects.create_user(username="rel", password="t",
                                                    role="nurse")
        self.theatre.staff.add(self.by_relation)
        self.by_name = User.objects.create_user(username="name", password="t",
                                                role="nurse",
                                                department="Theatre / Procedures")
        self.by_code = User.objects.create_user(username="code", password="t",
                                                role="nurse", department="theatre")
        self.outsider = User.objects.create_user(username="out", password="t",
                                                 role="nurse", department="Front Desk")

    def sees(self, user):
        """Reading 1 — `work_routes_for`, the queue."""
        return work_routes_for(user).filter(pk=self.route.pk).exists()

    def may_work(self, user):
        """Reading 2 — `may_work`, whether the buttons are live."""
        return workflow_access.may_work(user, self.route,
                                        role_purposes=ROLE_PURPOSES,
                                        purpose_role=PURPOSE_ROLE)

    def told(self, user):
        """Reading 3 — `route_targets`, the bell."""
        return user in route_targets(self.route)

    def authorised(self, user):
        """Reading 4 — the helper the other three now read."""
        return works_in(user, self.theatre)


class TheFourReadingsAgree(OneDepartment):

    def test_for_somebody_in_the_relation(self):
        for reading in (self.sees, self.may_work, self.told, self.authorised):
            with self.subTest(reading=reading.__name__):
                self.assertTrue(reading(self.by_relation))

    def test_for_somebody_whose_legacy_text_names_the_department(self):
        """`route_targets` used to say no here while the queue said yes."""
        for reading in (self.sees, self.may_work, self.told, self.authorised):
            with self.subTest(reading=reading.__name__):
                self.assertTrue(reading(self.by_name))

    def test_for_somebody_whose_legacy_text_holds_the_code(self):
        """`may_work` used to say no here while the queue said yes."""
        for reading in (self.sees, self.may_work, self.told, self.authorised):
            with self.subTest(reading=reading.__name__):
                self.assertTrue(reading(self.by_code))

    def test_and_for_somebody_who_is_not_the_departments_at_all(self):
        for reading in (self.sees, self.may_work, self.told, self.authorised):
            with self.subTest(reading=reading.__name__):
                self.assertFalse(reading(self.outsider))

    def test_seeing_work_and_being_told_about_it_are_the_same_set(self):
        """
        Rule 16, stated directly. Anybody the bell reaches can open the row,
        and anybody who can open the row is reached by the bell.
        """
        everyone = [self.by_relation, self.by_name, self.by_code, self.outsider]
        self.assertEqual({u.username for u in everyone if self.sees(u)},
                         {u.username for u in everyone if self.told(u)})


class TheBellIsNotBroadened(OneDepartment):
    """
    §2 — what the change to `route_targets` must **not** have done.

    It reaches the department's authorised staff and nobody else. A text field
    that merely mentions a department, an account that is disabled, and the
    person who raised the route are all still out.
    """

    def test_text_that_names_no_department_reaches_nobody(self):
        vague = User.objects.create_user(username="vague", password="t", role="nurse",
                                         department="Theatre annexe, second floor")
        self.assertEqual(list(authorized_departments(vague)), [])
        self.assertFalse(self.told(vague))

    def test_a_disabled_account_is_never_told(self):
        self.by_relation.is_active = False
        self.by_relation.save(update_fields=["is_active"])
        self.assertFalse(self.told(self.by_relation))

    def test_the_person_who_raised_it_is_never_told(self):
        """Rule 14 — a bell full of your own actions stops being read."""
        self.theatre.staff.add(self.reception)
        self.assertTrue(self.authorised(self.reception))
        self.assertFalse(self.told(self.reception))

    def test_a_purpose_a_role_owns_does_not_fall_back_to_the_department(self):
        """
        The department is the fallback *beneath* the purpose, not beside it.
        A laboratory route reaches the bench, never everyone posted to the
        department it was filed against.
        """
        laboratory = Department.objects.get(code="laboratory")
        laboratory.staff.add(self.by_relation)          # a nurse, posted there
        visit = Visit.objects.filter(patient=self.patient).first()
        lab_route = PatientRoute.objects.create(
            visit=visit, department=laboratory, purpose="laboratory",
            routed_by=self.reception)
        self.assertNotIn(self.by_relation, route_targets(lab_route))

    def test_it_reaches_exactly_the_authorised_staff(self):
        """`staff_of` is the department's authorised staff, and says so."""
        self.assertEqual(
            {u.username for u in staff_of(self.theatre)},
            {u.username for u in (self.by_relation, self.by_name, self.by_code)})
        self.assertEqual({u.username for u in route_targets(self.route)},
                         {u.username for u in staff_of(self.theatre)})


class DepartmentAuthorizationOnlyNarrows(OneDepartment):
    """
    §3 — the invariant. A posting says *where*; it never says *what*.
    """

    def test_a_posting_adds_no_capability(self):
        """
        A nurse posted to Theatre is a nurse in Theatre. The endpoints her role
        does not reach are exactly the ones she did not reach before.
        """
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.by_relation)
        before = {path: client.get(path).status_code for path in (
            "/api/finance/report/", "/api/stock-records/", "/api/sales/",
            "/api/adjustments/", "/api/users/")}
        for department in Department.objects.all():
            department.staff.add(self.by_relation)
        after = {path: client.get(path).status_code for path in before}
        self.assertEqual(before, after,
                         "being posted everywhere changed what a role may do")

    def test_a_posting_without_the_role_reaches_nothing(self):
        """
        Department alone is not authorisation — **role AND department**, and
        this is where each half is enforced.

        `may_work` answers only the second half: "is this row waiting on this
        person, among the people who work here". For a purpose no role owns it
        falls back to department membership *without* a role check, which is
        rule 16's documented design — the department is the only signal there —
        and is exactly as it was before the multi-department work.

        The **role** half is the permission class on the endpoint. So a cashier
        posted to Theatre satisfies `may_work` and still cannot reach the queue
        at all, which is the boundary that actually decides.
        """
        from rest_framework.test import APIClient

        cashier = User.objects.create_user(username="till", password="t", role="cashier")
        self.theatre.staff.add(cashier)
        self.assertTrue(works_in(cashier, self.theatre))
        # The department half says yes — unchanged rule-16 fallback.
        self.assertTrue(self.may_work(cashier))
        # The role half says no, and it is the one on the wire.
        client = APIClient()
        client.force_authenticate(cashier)
        self.assertEqual(client.get("/api/patient-routes/").status_code, 403)
        for action in ("start", "complete", "accept"):
            with self.subTest(action=action):
                self.assertEqual(
                    client.post(f"/api/patient-routes/{self.route.pk}/{action}/").status_code,
                    403)

    def test_a_working_role_with_no_posting_is_refused_too(self):
        """The other half: the role, and nowhere to use it."""
        self.assertTrue(self.outsider.role == "nurse")
        self.assertFalse(works_in(self.outsider, self.theatre))
        self.assertFalse(self.may_work(self.outsider))

    def test_removing_a_posting_removes_only_that_department(self):
        laboratory = Department.objects.get(code="laboratory")
        laboratory.staff.add(self.by_relation)
        self.theatre.staff.remove(self.by_relation)
        self.assertFalse(works_in(self.by_relation, self.theatre))
        self.assertTrue(works_in(self.by_relation, laboratory))

    def test_the_legacy_text_cannot_be_set_by_the_person_it_describes(self):
        """
        The fallback is real authorisation, so the column behind it must not be
        self-service. `/auth/me/` does not take `department` back either.
        """
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.outsider)
        answer = client.patch(f"/api/users/{self.outsider.pk}/",
                              {"department": "Theatre / Procedures"}, format="json")
        self.assertEqual(answer.status_code, 403)
        self.outsider.refresh_from_db()
        self.assertEqual(self.outsider.department, "Front Desk")
        self.assertFalse(works_in(self.outsider, self.theatre))
