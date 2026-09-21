"""
Four tries, then five minutes off.

The rule is `accounts/lockout.py`'s and the enforcement is `LoginView`'s; these
tests hold both to it from outside, through the real endpoint, because that is
where it has to be true. A frontend countdown is feedback — every test here
drives the API directly, so nothing it proves depends on a browser.

Time is moved rather than waited for: the cooldown is stored as an absolute
moment, so `time.time` is the only clock to push. A test that actually slept
five minutes would be a test nobody runs.
"""
import time
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.models import AuditLog

LOGIN = "/api/auth/login/"

# The lockout counts in a cache. Tests get their own, in this process, so a
# developer with REDIS_URL set does not have their real cache counted into a
# test run — and so the suite needs no Redis to be up.
LOCAL_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "login-lockout-tests",
    }
}


@override_settings(CACHES=LOCAL_CACHE, LOGIN_MAX_FAILED_ATTEMPTS=5,
                   LOGIN_LOCKOUT_SECONDS=300)
class LockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.nurse = User.objects.create_user(username="ada", password="correct-horse",
                                              role="nurse", first_name="Ada", last_name="Okoro")

    def tearDown(self):
        cache.clear()

    # -- helpers ---------------------------------------------------------------

    def attempt(self, username="ada", password="wrong", **extra):
        return self.api.post(LOGIN, {"username": username, "password": password}, **extra)

    def fail_attempts(self, times, username="ada", **extra):
        return [self.attempt(username=username, **extra) for _ in range(times)]

    def travel(self, seconds):
        """Move the wall clock on. The lock is stored as an absolute moment,
        so this is all it takes — and the cache entry's own TTL is irrelevant
        to the check, which is why storing the moment was worth doing."""
        return mock.patch("apps.accounts.lockout.time.time",
                          return_value=time.time() + seconds)

    # -- the rule ---------------------------------------------------------------

    def test_four_failed_attempts_are_allowed(self):
        for number, response in enumerate(self.fail_attempts(4), start=1):
            with self.subTest(attempt=number):
                self.assertEqual(response.status_code, 400)
                # Every refusal carries a code now (`core/exceptions.py`); what
                # matters is that it is the ordinary one, not the lockout.
                self.assertNotEqual(response.data.get("code"), "login_locked")

    def test_a_correct_password_still_works_after_four_failures(self):
        self.fail_attempts(4)
        ok = self.attempt(password="correct-horse")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("token", ok.data)

    def test_the_fifth_failure_locks_for_exactly_five_minutes(self):
        self.fail_attempts(4)
        fifth = self.attempt()

        self.assertEqual(fifth.status_code, 429)
        self.assertEqual(fifth.data["code"], "login_locked")
        self.assertEqual(fifth.data["retry_after"], 300)
        self.assertEqual(fifth["Retry-After"], "300")
        self.assertIn("temporarily blocked for 5 minutes", fifth.data["detail"])
        self.assertIn("Too many failed login attempts", fifth.data["detail"])

    def test_attempts_during_the_cooldown_are_rejected(self):
        self.fail_attempts(5)
        for number in range(3):
            with self.subTest(attempt=number):
                again = self.attempt()
                self.assertEqual(again.status_code, 429)
                self.assertEqual(again.data["code"], "login_locked")

    def test_the_right_password_does_not_get_past_the_cooldown(self):
        """The lock is asked about before the credentials are read at all."""
        self.fail_attempts(5)
        response = self.attempt(password="correct-horse")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("token", response.data)

    def test_the_remaining_time_counts_down_while_the_lock_stands(self):
        self.fail_attempts(5)
        with self.travel(60):
            response = self.attempt()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["retry_after"], 240)
        with self.travel(272):
            self.assertEqual(self.attempt().data["retry_after"], 28)

    def test_login_works_again_once_the_five_minutes_have_passed(self):
        self.fail_attempts(5)
        with self.travel(301):
            response = self.attempt(password="correct-horse")
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)

    def test_a_wrong_password_after_the_cooldown_is_an_ordinary_refusal(self):
        """The lock lifts into four fresh attempts, not straight back into one."""
        self.fail_attempts(5)
        with self.travel(301):
            response = self.attempt()
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.attempt().status_code, 400)

    def test_a_successful_login_resets_the_counter(self):
        self.fail_attempts(4)
        self.assertEqual(self.attempt(password="correct-horse").status_code, 200)
        # Four more must be allowed — the earlier four were forgotten.
        for response in self.fail_attempts(4):
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.attempt().status_code, 429)

    def test_the_response_carries_what_the_login_page_needs(self):
        self.fail_attempts(4)
        body = self.attempt().data
        self.assertEqual(
            sorted(body), ["code", "detail", "locked_until", "retry_after"])
        self.assertIsInstance(body["retry_after"], int)
        # An absolute moment as well as a duration, so a page that was open
        # while the clock ran does not count down from a stale figure.
        self.assertRegex(body["locked_until"], r"^\d{4}-\d{2}-\d{2}T")

    # -- what it must not give away ----------------------------------------------

    def test_an_unknown_username_is_refused_in_exactly_the_same_words(self):
        real = self.attempt(username="ada")
        unknown = self.attempt(username="nobody-here")
        self.assertEqual(real.status_code, unknown.status_code)
        self.assertEqual(real.data, unknown.data)

    def test_an_unknown_username_locks_out_identically(self):
        """Counting only real usernames would make the 429 itself the tell."""
        for _ in range(4):
            self.assertEqual(self.attempt(username="nobody-here").status_code, 400)
        locked = self.attempt(username="nobody-here")
        self.assertEqual(locked.status_code, 429)

        self.assertEqual(self.fail_attempts(4)[-1].status_code, 400)
        real = self.attempt(username="ada")
        self.assertEqual(real.status_code, 429)
        self.assertEqual({k: v for k, v in real.data.items() if k != "locked_until"},
                         {k: v for k, v in locked.data.items() if k != "locked_until"})

    def test_the_audit_row_does_not_claim_the_account_is_real(self):
        self.fail_attempts(5, username="nobody-here")
        entry = AuditLog.objects.get(action="auth.login_locked")
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.details["username"], "nobody-here")

    # -- who it shuts out ---------------------------------------------------------

    def test_the_lockout_is_per_username_not_the_whole_client(self):
        """One person mistyping must not shut the desk beside them out."""
        self.fail_attempts(5, username="ada")
        other = User.objects.create_user(username="bala", password="also-correct", role="doctor")
        response = self.api.post(LOGIN, {"username": "bala", "password": "also-correct"})
        self.assertEqual(response.status_code, 200)

    def test_another_client_can_still_sign_in_to_a_locked_out_username(self):
        """So nobody can shut a named member of staff out of the hospital by
        guessing at their password from somewhere else."""
        self.fail_attempts(5, username="ada", REMOTE_ADDR="10.0.0.9")
        elsewhere = self.api.post(LOGIN, {"username": "ada", "password": "correct-horse"},
                                  REMOTE_ADDR="10.0.0.10")
        self.assertEqual(elsewhere.status_code, 200)

    def test_the_staff_account_itself_is_never_locked(self):
        self.fail_attempts(5)
        self.nurse.refresh_from_db()
        self.assertTrue(self.nurse.is_active)
        # And nothing was written on the account to represent the lockout.
        with self.travel(301):
            self.assertEqual(self.attempt(password="correct-horse").status_code, 200)

    # -- blast radius --------------------------------------------------------------

    def test_no_other_endpoint_is_rate_limited(self):
        """The limiter counts failed sign-ins and nothing else. An
        authenticated request never reaches it."""
        self.api.force_authenticate(self.nurse)
        for _ in range(12):
            response = self.api.get("/api/patients/")
            self.assertEqual(response.status_code, 200)

    def test_being_locked_out_does_not_touch_an_existing_session(self):
        signed_in = APIClient()
        token = self.attempt(password="correct-horse").data["token"]
        signed_in.credentials(HTTP_AUTHORIZATION=f"Token {token}")

        self.fail_attempts(5)
        self.assertEqual(self.attempt().status_code, 429)
        # The nurse already at a workstation carries on.
        self.assertEqual(signed_in.get("/api/auth/me/").status_code, 200)

    def test_logging_out_is_not_rate_limited(self):
        token = self.attempt(password="correct-horse").data["token"]
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.fail_attempts(5, username="someone-else")
        self.assertEqual(self.api.post("/api/auth/logout/").status_code, 204)


@override_settings(CACHES=LOCAL_CACHE, LOGIN_MAX_FAILED_ATTEMPTS=5,
                   LOGIN_LOCKOUT_SECONDS=300)
class WhenTheCacheIsGone(TestCase):
    """
    A cache that is down must not shut the hospital out of its own records.

    This is a deliberate trade: with no cache there is no counter, so the
    lockout stops applying rather than starts refusing everybody. The person a
    fail-closed limiter would lock out is the nurse holding the patient.
    """

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        User.objects.create_user(username="ada", password="correct-horse", role="nurse")

    def test_sign_in_still_works_when_every_cache_call_raises(self):
        with mock.patch("apps.accounts.lockout.cache") as broken:
            broken.get.side_effect = ConnectionError("redis is down")
            broken.incr.side_effect = ConnectionError("redis is down")
            broken.set.side_effect = ConnectionError("redis is down")
            broken.delete_many.side_effect = ConnectionError("redis is down")

            for _ in range(8):
                self.assertEqual(
                    self.api.post(LOGIN, {"username": "ada", "password": "wrong"}).status_code,
                    400)
            ok = self.api.post(LOGIN, {"username": "ada", "password": "correct-horse"})
        self.assertEqual(ok.status_code, 200)
