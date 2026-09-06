"""
The staff half of the hospital's register: `NMHS-S000001`.

Issued to accounts that belong to somebody the hospital employs, and to no
others — a login with no role reaches nothing in the application and is not on
the payroll list.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User


class StaffNumberTests(TestCase):
    def test_a_new_staff_account_is_numbered_on_creation(self):
        nurse = User.objects.create_user(username="nurse", password="t", role="nurse")
        self.assertRegex(nurse.staff_number, r"^NMHS-S\d{6}$")

    def test_numbers_are_unique_across_accounts(self):
        numbers = {
            User.objects.create_user(username=f"staff{n}", password="t", role="nurse").staff_number
            for n in range(3)
        }
        self.assertEqual(len(numbers), 3)

    def test_an_account_with_no_role_is_not_issued_a_staff_number(self):
        # Not every row in this table is a member of staff.
        outsider = User.objects.create_user(username="service-account", password="t")
        self.assertIsNone(outsider.staff_number)

    def test_a_superuser_is_staff_even_before_a_role_is_chosen(self):
        root = User.objects.create_superuser(username="root", password="t", email="")
        self.assertIsNotNone(root.staff_number)

    def test_giving_an_account_a_role_later_numbers_it_then(self):
        person = User.objects.create_user(username="latecomer", password="t")
        self.assertIsNone(person.staff_number)
        person.role = "reception"
        person.save()
        person.refresh_from_db()
        self.assertRegex(person.staff_number, r"^NMHS-S\d{6}$")

    def test_the_number_does_not_move_when_the_account_changes(self):
        doctor = User.objects.create_user(username="doc", password="t", role="doctor")
        issued = doctor.staff_number
        doctor.role = "surgeon"
        doctor.last_name = "Adeyemi"
        doctor.save()
        doctor.set_password("something-else")
        doctor.save(update_fields=["password"])
        doctor.refresh_from_db()
        self.assertEqual(doctor.staff_number, issued)

    def test_the_number_does_not_depend_on_the_password_reset_path(self):
        admin = User.objects.create_user(username="boss", password="t", role="hospital_admin")
        target = User.objects.create_user(username="pharm", password="t", role="pharmacist")
        issued = target.staff_number
        client = APIClient(); client.force_authenticate(admin)
        response = client.post(f"/api/users/{target.pk}/set_password/", {"password": "new-one"})
        self.assertEqual(response.status_code, 204)
        target.refresh_from_db()
        self.assertEqual(target.staff_number, issued)


class StaffNumberApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin", password="t", role="hospital_admin")
        self.nurse = User.objects.create_user(username="florence", password="t", role="nurse",
                                              first_name="Florence", last_name="Nightingale")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def _rows(self, **params):
        response = self.client.get("/api/users/", params)
        self.assertEqual(response.status_code, 200)
        return response.data.get("results", response.data)

    def test_an_admin_reads_the_staff_number_on_the_account(self):
        response = self.client.get(f"/api/users/{self.nurse.pk}/")
        self.assertEqual(response.data["staff_number"], self.nurse.staff_number)

    def test_an_account_created_through_the_api_is_numbered(self):
        response = self.client.post("/api/users/", {
            "username": "newstaff", "role": "laboratory", "password": "a-password",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertRegex(response.data["staff_number"], r"^NMHS-S\d{6}$")

    def test_the_number_is_not_accepted_from_the_form(self):
        response = self.client.post("/api/users/", {
            "username": "cheeky", "role": "nurse", "password": "a-password",
            "staff_number": "NMHS-S999999",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data["staff_number"], "NMHS-S999999")

    def test_the_number_cannot_be_edited_by_an_admin_either(self):
        issued = self.nurse.staff_number
        response = self.client.patch(f"/api/users/{self.nurse.pk}/",
                                     {"staff_number": "NMHS-S999999"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.nurse.refresh_from_db()
        self.assertEqual(self.nurse.staff_number, issued)

    def test_staff_are_found_by_number_name_username_and_email(self):
        self.nurse.email = "flo@nmhs.test"
        self.nurse.save(update_fields=["email"])
        for term in (self.nurse.staff_number, "Nightingale", "florence", "flo@nmhs.test"):
            with self.subTest(term=term):
                found = [row["staff_number"] for row in self._rows(search=term)]
                self.assertIn(self.nurse.staff_number, found)

    def test_filtering_by_role_still_works(self):
        rows = self._rows(role="nurse")
        self.assertEqual([row["staff_number"] for row in rows], [self.nurse.staff_number])

    def test_the_directory_a_non_admin_reads_carries_the_number_but_not_the_email(self):
        client = APIClient(); client.force_authenticate(self.nurse)
        response = client.get("/api/users/")
        self.assertEqual(response.status_code, 200)
        row = (response.data.get("results", response.data))[0]
        self.assertIn("staff_number", row)
        self.assertNotIn("email", row)
        self.assertNotIn("department", row)
