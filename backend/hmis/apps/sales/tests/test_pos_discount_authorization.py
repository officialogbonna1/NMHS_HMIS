"""
A POS discount given by somebody an administrator authorised by name.

`User.pos_discount_authorized` is a second way onto the discount the till
already had (rule 39/42), beside POS_DISCOUNT_ROLES, which is unchanged. What
this file holds is the line it must not cross:

    may give a discount        ≠   may approve one above the limit
    (roles, or the flag)           (POS_DISCOUNT_APPROVAL_ROLES only)

so a pharmacist an administrator has trusted with discounts still cannot clear
their own over-limit one — and everything the discount already did to the money,
the shelf and the audit trail is exactly what it was.
"""
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.billing.models import Adjustment, Charge, PatientLedger, Payment, PaymentAllocation
from apps.core.models import AuditLog, HospitalSettings
from apps.inventory.models import StockMovement
from apps.patients.models import Patient
from apps.sales import services
from apps.sales.discount_policy import DiscountPolicy, DiscountRefused, approver_for
from apps.sales.models import Sale
from apps.sales.tests.test_pos_discounts import ZERO, PosDiscountTestCase


class AuthorisedPharmacistTestCase(PosDiscountTestCase):
    """The base fixtures, plus a pharmacist with a till of their own."""

    def setUp(self):
        super().setUp()
        self.pharmacist.first_name, self.pharmacist.last_name = "Mary", "Okafor"
        self.pharmacist.set_password("mary-pass")
        self.pharmacist.save()
        services.open_register(operator=self.pharmacist, opening_float="0")

    def authorise(self, user, value=True):
        """What ticking (or unticking) the box in Django admin does: a save."""
        user = User.objects.get(pk=user.pk)
        user.pos_discount_authorized = value
        user.save()
        return User.objects.get(pk=user.pk)

    def discounted(self, operator, percent="10", **kwargs):
        return self.sell([self.line(self.para, 4, {"type": "percent", "value": percent})],
                         operator=operator, discount_reason="Loyal customer", **kwargs)

    def token_client(self, user):
        """Real token auth, so the user is re-read from the database per request."""
        client = APIClient()
        token, _ = Token.objects.get_or_create(user=user)
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        return client

    def complete_body(self, percent="10", **extra):
        return {"lines": [{"item": self.para.pk, "quantity": 4,
                           "discount": {"type": "percent", "value": percent}}],
                "customer_type": "walk_in", "customer_name": "Musa",
                "discount_reason": "Loyal customer", "payment_method": "cash", **extra}

    def assert_nothing_written(self):
        self.assertEqual(self.held(self.para_batch), 100)
        self.assertFalse(Sale.objects.filter(status="completed").exists())
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(StockMovement.objects.filter(reason="sale").exists())


# ------------------------------------------------------------ who may give one


class WhoMayDiscountTests(AuthorisedPharmacistTestCase):
    def test_the_flag_is_off_for_every_existing_and_new_account(self):
        for role in Role.values:
            user = User.objects.create_user(username=f"new-{role}", password="t", role=role)
            self.assertFalse(user.pos_discount_authorized, role)
        self.assertFalse(User.objects.filter(pos_discount_authorized=True).exists())

    def test_an_unauthorised_pharmacist_cannot_discount(self):
        self.assertFalse(services.can_discount(self.pharmacist))
        with self.assertRaises(PermissionDenied):
            self.discounted(self.pharmacist)
        self.assert_nothing_written()

    def test_an_authorised_pharmacist_can_discount(self):
        mary = self.authorise(self.pharmacist)
        self.assertTrue(services.can_discount(mary))
        sale = self.discounted(mary)
        self.assertEqual((sale.subtotal, sale.discount_amount, sale.total_amount),
                         (D("4000.00"), D("400.00"), D("3600.00")))
        self.assertEqual(sale.discount_by, mary)
        self.assertEqual(sale.sold_by, mary)
        # Within the limit, so nobody was asked to approve it.
        self.assertIsNone(sale.discount_approved_by)

    def test_a_sale_wide_discount_is_open_to_them_too(self):
        mary = self.authorise(self.pharmacist)
        sale = self.sell([self.line(self.para, 4)], operator=mary,
                         discount={"type": "amount", "value": "200", "reason": "Staff discount"})
        self.assertEqual(sale.discount_amount, D("200.00"))

    def test_the_cashier_is_unchanged(self):
        self.assertFalse(self.cashier.pos_discount_authorized)
        self.assertTrue(services.can_discount(self.cashier))
        sale = self.discounted(self.cashier)
        self.assertEqual((sale.discount_by, sale.total_amount), (self.cashier, D("3600.00")))

    def test_the_accountant_is_unchanged(self):
        self.assertFalse(self.accountant.pos_discount_authorized)
        self.assertTrue(services.can_discount(self.accountant))
        self.assertEqual(approver_for(None, operator=self.accountant), self.accountant)

    def test_an_administrator_needs_no_flag(self):
        for role in ("admin", "hospital_admin"):
            admin = User.objects.create_user(username=role, password="t", role=role)
            self.assertFalse(admin.pos_discount_authorized)
            self.assertTrue(services.can_discount(admin), role)
        boss = User.objects.get(username="admin")
        services.open_register(operator=boss, opening_float="0")
        self.assertEqual(self.discounted(boss).discount_by, boss)

    def test_the_flag_does_not_open_the_till_to_a_role_that_cannot_work_one(self):
        # It is a discount permission, not a POS one: a doctor holding it still
        # has no till to discount at.
        doctor = self.authorise(self.doctor)
        with self.assertRaises(PermissionDenied):
            services.open_register(operator=doctor, opening_float="0")
        response = self.token_client(doctor).post("/api/sales/complete/", self.complete_body(),
                                                  format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assert_nothing_written()

    def test_revoking_it_refuses_the_very_next_request(self):
        client = self.token_client(self.pharmacist)
        self.authorise(self.pharmacist)
        first = client.post("/api/sales/complete/", self.complete_body(), format="json")
        self.assertEqual(first.status_code, 201, first.data)

        self.authorise(self.pharmacist, value=False)
        second = client.post("/api/sales/complete/", self.complete_body(), format="json")
        self.assertEqual(second.status_code, 403, second.data)
        # The first sale stands; the second moved nothing.
        self.assertEqual(Sale.objects.filter(status="completed").count(), 1)
        self.assertEqual(self.held(self.para_batch), 96)

        # And a sale with no discount in it still goes through — the till is
        # theirs, only the discount was taken away.
        plain = client.post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 1}], "customer_type": "walk_in",
            "customer_name": "Musa", "payment_method": "cash"}, format="json")
        self.assertEqual(plain.status_code, 201, plain.data)

    def test_a_direct_api_request_from_an_unauthorised_pharmacist_is_refused(self):
        client = self.token_client(self.pharmacist)
        line = client.post("/api/sales/complete/", self.complete_body(), format="json")
        self.assertEqual(line.status_code, 403, line.data)
        sale_wide = client.post("/api/sales/complete/", {
            "lines": [{"item": self.para.pk, "quantity": 4}], "customer_type": "walk_in",
            "customer_name": "Musa", "payment_method": "cash",
            "discount": {"type": "percent", "value": "5", "reason": "Asked nicely"}},
            format="json")
        self.assertEqual(sale_wide.status_code, 403, sale_wide.data)
        self.assert_nothing_written()

    def test_me_reports_the_flag_and_cannot_set_it(self):
        client = self.token_client(self.pharmacist)
        self.assertIs(client.get("/api/auth/me/").data["pos_discount_authorized"], False)
        self.authorise(self.pharmacist)
        self.assertIs(client.get("/api/auth/me/").data["pos_discount_authorized"], True)
        self.authorise(self.pharmacist, value=False)
        # /me is read-only; there is no way to grant it to yourself.
        attempt = client.patch("/api/auth/me/", {"pos_discount_authorized": True}, format="json")
        self.assertEqual(attempt.status_code, 405)
        self.assertFalse(User.objects.get(pk=self.pharmacist.pk).pos_discount_authorized)


# ------------------------------------------------ giving is not approving


class ApprovalStaysSeparateTests(AuthorisedPharmacistTestCase):
    def setUp(self):
        super().setUp()
        self.policy(pos_discount_limit_percent=D("10"))
        self.mary = self.authorise(self.pharmacist)

    def test_an_authorised_pharmacist_is_not_an_approver(self):
        self.assertIsNone(approver_for(None, operator=self.mary))

    def test_over_the_limit_they_are_asked_for_an_approval(self):
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(self.mary, percent="25")
        self.assertEqual(caught.exception.code, "discount_needs_approval")
        self.assert_nothing_written()

    def test_they_cannot_approve_their_own_over_limit_discount(self):
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(self.mary, percent="25",
                            authorization={"username": "ph", "password": "mary-pass"})
        self.assertEqual(caught.exception.code, "discount_approval_failed")
        self.assertIn("not authorised to approve", caught.exception.message)
        self.assert_nothing_written()

    def test_another_authorised_person_cannot_approve_for_them(self):
        colleague = User.objects.create_user(username="ph2", password="ph2-pass",
                                             role="pharmacist")
        self.authorise(colleague)
        cashier = self.authorise(self.cashier)
        for username, password in (("ph2", "ph2-pass"), ("cash", "till-pass")):
            with self.assertRaises(DiscountRefused) as caught:
                self.discounted(self.mary, percent="25",
                                authorization={"username": username, "password": password})
            self.assertEqual(caught.exception.code, "discount_approval_failed", username)
        self.assertIsNone(approver_for(None, operator=cashier))
        self.assert_nothing_written()

    def test_an_accountant_approves_it_exactly_as_for_a_cashier(self):
        sale = self.discounted(self.mary, percent="25",
                               authorization={"username": "acc", "password": "super-secret"})
        self.assertEqual((sale.discount_amount, sale.total_amount),
                         (D("1000.00"), D("3000.00")))
        self.assertEqual(sale.discount_by, self.mary)
        self.assertEqual(sale.discount_approved_by, self.accountant)

    def test_the_api_answers_them_with_the_same_code_the_till_acts_on(self):
        response = self.token_client(self.mary).post(
            "/api/sales/complete/", self.complete_body(percent="25"), format="json")
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(response.data["code"], "discount_needs_approval")
        self.assertEqual(response.data["limit_percent"], "10.00")

        own = self.token_client(self.mary).post(
            "/api/sales/complete/",
            self.complete_body(percent="25", authorization={"username": "ph",
                                                            "password": "mary-pass"}),
            format="json")
        self.assertEqual(own.status_code, 403, own.data)
        self.assertEqual(own.data["code"], "discount_approval_failed")
        self.assert_nothing_written()


# ------------------------------------------------------ the limits still apply


class LimitsStillApplyTests(AuthorisedPharmacistTestCase):
    def setUp(self):
        super().setUp()
        self.mary = self.authorise(self.pharmacist)

    def test_the_maximum_holds_even_with_an_approval(self):
        self.policy(pos_discount_limit_percent=D("10"), pos_max_discount_percent=D("20"))
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(self.mary, percent="25",
                            authorization={"username": "acc", "password": "super-secret"})
        self.assertEqual(caught.exception.code, "discount_over_maximum")
        self.assert_nothing_written()

    def test_a_fixed_sum_limit_applies(self):
        self.policy(pos_discount_limit_amount=D("300"))
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "500"})],
                      operator=self.mary, discount_reason="Damaged packaging")
        self.assertEqual(caught.exception.code, "discount_needs_approval")

    def test_a_fixed_sum_cannot_walk_past_the_percentage_limit(self):
        self.policy(pos_discount_limit_percent=D("10"))
        # ₦500 off ₦4,000 is 12.5%.
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "500"})],
                      operator=self.mary, discount_reason="Damaged packaging")
        self.assertEqual(caught.exception.code, "discount_needs_approval")

    def test_switched_off_is_off_for_them_too(self):
        self.policy(pos_discounts_enabled=False)
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(self.mary)
        self.assertEqual(caught.exception.code, "discounts_disabled")

    def test_a_type_the_hospital_does_not_offer_is_refused(self):
        self.policy(pos_discount_types="percent")
        with self.assertRaises(DiscountRefused) as caught:
            self.sell([self.line(self.para, 4, {"type": "amount", "value": "100"})],
                      operator=self.mary, discount_reason="Damaged packaging")
        self.assertEqual(caught.exception.code, "discount_type_not_allowed")


# ------------------------------------------ money, shelf and audit unchanged


class AccountingUnchangedTests(AuthorisedPharmacistTestCase):
    def setUp(self):
        super().setUp()
        self.mary = self.authorise(self.pharmacist)

    def test_a_walk_in_is_not_turned_into_a_patient(self):
        before = Patient.objects.count()
        sale = self.discounted(self.mary, percent="5")
        self.assertEqual(Patient.objects.count(), before)
        self.assertIsNone(sale.charge)
        self.assertIsNone(sale.payment.patient)
        self.assertFalse(PatientLedger.objects.exists())
        self.assertEqual((sale.total_amount, sale.payment.amount), (D("3800.00"), D("3800.00")))

    def test_a_registered_patient_gets_the_charge_the_adjustment_and_the_payment(self):
        sale = self.sell([self.line(self.para, 4, {"type": "percent", "value": "20"})],
                         operator=self.mary, customer_type="patient", patient=self.patient,
                         discount_reason="Hospital concession")
        charge = Charge.objects.get(pk=sale.charge_id)
        self.assertEqual((charge.amount, charge.amount_discounted, charge.amount_paid),
                         (D("4000.00"), D("800.00"), D("3200.00")))
        self.assertEqual(charge.status, "paid")
        self.assertEqual((charge.source_type, charge.department.code), ("pos_sale", "pharmacy"))
        self.assertTrue(Adjustment.objects.filter(charge=charge, kind="discount",
                                                  amount=D("800.00")).exists())
        self.assertEqual(PaymentAllocation.objects.get(payment=sale.payment).charge, charge)
        self.assertEqual(PatientLedger.objects.get(patient=self.patient).outstanding_balance, ZERO)

    def test_the_shelf_loses_the_quantity_sold_and_nothing_else(self):
        self.discounted(self.mary, percent="10")
        self.assertEqual(self.held(self.para_batch), 96)
        movement = StockMovement.objects.get(reason="sale")
        self.assertEqual(movement.batch, self.para_batch)

    def test_the_audit_row_names_the_pharmacist_and_the_approver(self):
        self.policy(pos_discount_limit_percent=D("10"))
        sale = self.discounted(self.mary, percent="25",
                               authorization={"username": "acc", "password": "super-secret"})
        entry = AuditLog.objects.get(action="pos.discount_applied")
        self.assertEqual(entry.actor, self.mary)
        self.assertEqual(entry.details["reference"], sale.reference)
        self.assertEqual((entry.details["applied_by"], entry.details["approved_by"]),
                         ("Mary Okafor", "Ngozi Books"))


# --------------------------------------------------------------- Django admin


class DjangoAdminTests(AuthorisedPharmacistTestCase):
    """The two boxes an administrator actually uses, through the real forms."""

    def setUp(self):
        super().setUp()
        self.superuser = User.objects.create_superuser(username="root", password="root-pass",
                                                       email="root@example.com", role="admin")
        self.client.force_login(self.superuser)

    def user_url(self, user):
        return f"/admin/accounts/user/{user.pk}/change/"

    def user_form(self, user, authorised):
        data = {
            "username": user.username, "first_name": user.first_name,
            "last_name": user.last_name, "email": user.email, "is_active": "on",
            "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
            "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
            "last_login_0": "", "last_login_1": "",
            "role": user.role, "department": user.department,
        }
        if authorised:
            data["pos_discount_authorized"] = "on"
        return data

    def test_the_user_page_explains_the_box(self):
        page = self.client.get(self.user_url(self.pharmacist))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="pos_discount_authorized"')
        self.assertContains(page, "POS discount authorized")
        self.assertContains(page, "This does not grant discount approval authority.")

    def test_ticking_then_unticking_the_box_grants_and_revokes_the_discount(self):
        response = self.client.post(self.user_url(self.pharmacist),
                                    self.user_form(self.pharmacist, authorised=True))
        self.assertEqual(response.status_code, 302, getattr(response, "context", None)
                         and response.context["adminform"].form.errors)
        mary = User.objects.get(pk=self.pharmacist.pk)
        self.assertTrue(mary.pos_discount_authorized)
        # Nothing else on the account moved.
        self.assertEqual((mary.role, mary.staff_number, mary.check_password("mary-pass")),
                         ("pharmacist", self.pharmacist.staff_number, True))
        self.assertEqual(self.discounted(mary).discount_by, mary)

        response = self.client.post(self.user_url(mary), self.user_form(mary, authorised=False))
        self.assertEqual(response.status_code, 302)
        mary = User.objects.get(pk=mary.pk)
        self.assertFalse(mary.pos_discount_authorized)
        with self.assertRaises(PermissionDenied):
            self.discounted(mary)

    def test_the_user_list_filters_on_the_flag(self):
        self.authorise(self.pharmacist)
        page = self.client.get("/admin/accounts/user/?pos_discount_authorized__exact=1")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(list(page.context["cl"].queryset), [self.pharmacist])

    # -- hospital settings -------------------------------------------------

    def settings_url(self):
        return f"/admin/core/hospitalsettings/{HospitalSettings.load().pk}/change/"

    def settings_form(self, **overrides):
        row = HospitalSettings.load()
        data = {
            "name": row.name, "full_name": row.full_name, "address": row.address,
            "phone": row.phone, "email": row.email,
            "expiry_warning_days": row.expiry_warning_days,
            "vitals_wait_alert_minutes": row.vitals_wait_alert_minutes,
            "unpaid_charge_alert_hours": row.unpaid_charge_alert_hours,
            "pos_discounts_enabled": "on", "pos_discount_types": row.pos_discount_types,
            "pos_discount_limit_percent": row.pos_discount_limit_percent,
            "pos_discount_limit_amount": row.pos_discount_limit_amount,
            "pos_max_discount_percent": row.pos_max_discount_percent,
            "pos_max_discount_amount": row.pos_max_discount_amount,
            "pos_discount_presets": row.pos_discount_presets,
            "pos_discount_reasons": row.pos_discount_reasons,
        }
        data.update(overrides)
        return {key: value for key, value in data.items() if value is not None}

    def test_the_settings_page_shows_the_discount_configuration(self):
        page = self.client.get(self.settings_url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "POS discount configuration")
        for field in ("pos_discounts_enabled", "pos_discount_types",
                      "pos_discount_limit_percent", "pos_discount_limit_amount",
                      "pos_max_discount_percent", "pos_max_discount_amount",
                      "pos_discount_presets", "pos_discount_reasons"):
            self.assertContains(page, f'name="{field}"')

    def test_what_is_saved_there_is_what_the_till_enforces(self):
        response = self.client.post(self.settings_url(), self.settings_form(
            pos_discount_types="percent", pos_discount_limit_percent="10",
            pos_discount_limit_amount="0", pos_max_discount_percent="30",
            pos_max_discount_amount="0", pos_discount_presets="5,10",
            pos_discount_reasons="Staff discount,Loyal customer"))
        self.assertEqual(response.status_code, 302)
        policy = DiscountPolicy.load()
        self.assertEqual((policy.types, policy.limit_percent, policy.max_percent),
                         ("percent", D("10.00"), D("30.00")))
        self.assertEqual((policy.presets, policy.reasons),
                         ((D("5"), D("10")), ("Staff discount", "Loyal customer")))
        # The same row the HMIS screen and the till read.
        served = self.api(self.pharmacist).get("/api/hospital-settings/current/").data
        self.assertEqual(served["pos_discount_limit_percent"], "10.00")

        mary = self.authorise(self.pharmacist)
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(mary, percent="25")
        self.assertEqual(caught.exception.code, "discount_needs_approval")

    def test_switching_discounts_off_there_stops_them(self):
        response = self.client.post(self.settings_url(),
                                    self.settings_form(pos_discounts_enabled=None))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(HospitalSettings.load().pos_discounts_enabled)
        with self.assertRaises(DiscountRefused) as caught:
            self.discounted(self.cashier)
        self.assertEqual(caught.exception.code, "discounts_disabled")

    def test_a_limit_above_its_maximum_is_refused_as_on_the_hmis_screen(self):
        response = self.client.post(self.settings_url(), self.settings_form(
            pos_discount_limit_percent="50", pos_max_discount_percent="20"))
        self.assertEqual(response.status_code, 200)
        errors = response.context["adminform"].form.errors
        self.assertIn("pos_discount_limit_percent", errors)
        self.assertEqual(HospitalSettings.load().pos_discount_limit_percent, D("100.00"))
