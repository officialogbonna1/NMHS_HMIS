from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from .mixins import TimeStampedModel


class AuditLog(TimeStampedModel):
    """Append-only accountability record for clinical and financial actions."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=100)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"]), models.Index(fields=["action"])]


class Notification(TimeStampedModel):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    category = models.CharField(max_length=50, default="general")
    is_read = models.BooleanField(default=False)
    action_url = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]


class HospitalSettings(TimeStampedModel):
    """
    The hospital's own details and the handful of numbers the application
    actually treats as tunable — one row, edited by an admin from the HMIS or
    from Django admin.

    Everything here **does something**. A setting that changes no behaviour is
    worse than no setting: somebody edits it, nothing happens, and they stop
    trusting the screen. So the fields are exactly the values that were
    hard-coded in the code until now — the hospital's name and address (which
    lived in a JavaScript constant, so a rename meant a deployment), and the
    three thresholds the dashboards alert on.

    A singleton by construction: `load()` returns the one row, creating it
    with the defaults on first use, and the admin refuses a second.
    """
    name = models.CharField(max_length=120, default="NMHS")
    full_name = models.CharField(max_length=200,
                                 default="Ngozi Maternity and Hospital Services")
    address = models.CharField(max_length=255, default="Aba, Abia State")
    phone = models.CharField(max_length=60, blank=True)
    email = models.EmailField(blank=True)

    # --- what the dashboards and the nightly tasks watch -------------------
    expiry_warning_days = models.PositiveIntegerField(
        default=30,
        help_text="A batch this close to its expiry date is flagged on the stock screens.")
    vitals_wait_alert_minutes = models.PositiveIntegerField(
        default=30,
        help_text="How long a patient may wait for vitals before the nursing dashboard says so.")
    unpaid_charge_alert_hours = models.PositiveIntegerField(
        default=2,
        help_text="A charge raised this long ago and still unpaid is flagged to the cash desk.")

    class Meta:
        verbose_name = "hospital settings"
        verbose_name_plural = "hospital settings"

    def __str__(self):
        return self.full_name or self.name

    @classmethod
    def load(cls):
        """The one row. Created with the defaults the code used to hard-code."""
        settings_row = cls.objects.order_by("pk").first()
        if settings_row is None:
            settings_row = cls.objects.create()
        return settings_row

    def save(self, *args, **kwargs):
        # Belt and braces on the singleton: a second row would mean two
        # answers to "what is this hospital called", and the letterhead would
        # read whichever was found first. A second create edits the first row
        # instead — `force_insert` is dropped, because `objects.create()`
        # passes it and it would turn this into a duplicate-key error rather
        # than the update it is.
        existing = type(self).objects.order_by("pk").first()
        if self._state.adding and existing is not None:
            self.pk = existing.pk
            # `auto_now_add` only fills on an insert, so a redirected create
            # would write NULL over the real timestamp.
            self.created_at = existing.created_at
            self._state.adding = False
            kwargs.pop("force_insert", None)
        return super().save(*args, **kwargs)


class NotificationSetting(TimeStampedModel):
    """
    Whether a category of notification is sent at all.

    Wired into `core.services.notify()`, so switching one off actually stops
    those notifications rather than only claiming to. Categories are the ones
    the application raises: clinical, routing, pharmacy, billing, general.

    `clinical` is deliberately hard to turn off — see `notify()`. A result
    coming back to the doctor who ordered it is not a preference.
    """
    CATEGORIES = [
        ("clinical", "Clinical — results, vitals, chart activity"),
        ("routing", "Routing — referrals and hand-offs"),
        ("pharmacy", "Pharmacy — prescriptions and dispensing"),
        ("billing", "Billing — charges raised and money taken"),
        ("general", "General"),
    ]
    category = models.CharField(max_length=50, choices=CATEGORIES, unique=True)
    is_enabled = models.BooleanField(default=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["category"]

    def __str__(self):
        return f"{self.get_category_display()} — {'on' if self.is_enabled else 'off'}"
