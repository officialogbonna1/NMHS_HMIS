from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

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


class NotificationQuerySet(models.QuerySet):
    def active(self):
        """The inbox: everything not archived. What the bell and the dashboard count."""
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)

    def archive(self):
        """Move these out of the inbox. Already-archived rows keep their first time."""
        now = timezone.now()
        return self.active().update(archived_at=now, updated_at=now)

    def restore(self):
        """Back into the inbox."""
        return self.archived().update(archived_at=None, updated_at=timezone.now())

    def for_user(self, user):
        """
        One person's own notifications: addressed to them, **and raised for the
        role they hold now**.

        A notification is written for somebody doing a job — the cash desk's
        refund notice, a nurse's vitals request. When an account's role
        changes, what it was told in the old role is no longer its business:
        an ophthalmologist must not keep reading the billing notices they got
        as an administrator. The rows are kept (never deleted), an admin's
        whole-system view still lists them, and changing the role back brings
        them back. Every personal read — the list, the bell, mark-all-read,
        archiving, the dashboard — goes through here, so they cannot disagree.
        """
        return self.filter(recipient=user, raised_for_role=getattr(user, "role", "") or "")


class Notification(TimeStampedModel):
    """
    Something the system told one member of staff.

    Never deleted — not by the recipient, not from Django admin. Archiving is
    how a notification leaves an inbox: `archived_at` is stamped, the row stays
    and can be restored. What somebody was told, and when, is part of how a
    patient's care was handed about.
    """
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=180)
    message = models.TextField(blank=True)
    category = models.CharField(max_length=50, default="general")
    is_read = models.BooleanField(default=False)
    action_url = models.CharField(max_length=255, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    # The role the recipient held when this was raised — stamped once, in
    # `save()`, and never changed. `for_user` reads it.
    raised_for_role = models.CharField(
        max_length=20, blank=True, default="",
        help_text="The recipient's role when this was raised; their feed shows it only while they hold that role.")

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"]),
                   models.Index(fields=["recipient", "archived_at"])]

    @property
    def is_archived(self):
        return self.archived_at is not None

    def save(self, *args, **kwargs):
        if self._state.adding and not self.raised_for_role and self.recipient_id:
            self.raised_for_role = getattr(self.recipient, "role", "") or ""
        super().save(*args, **kwargs)


def _decimals(text):
    """"5, 10, bad, 20" → [5, 10, 20]. A typo in a preset list is dropped, never
    raised: these are buttons, and a bad one must not stop the till opening."""
    values = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            number = Decimal(part)
        except (ArithmeticError, ValueError):
            continue
        if 0 < number <= 100 and number not in values:
            values.append(number)
    return values


class HospitalSettings(TimeStampedModel):
    """
    The hospital's own details and the handful of numbers the application
    actually treats as tunable — one row, edited by an admin from the HMIS or
    from Django admin.

    Everything here **does something**. A setting that changes no behaviour is
    worse than no setting: somebody edits it, nothing happens, and they stop
    trusting the screen. So the fields are exactly the values that were
    hard-coded in the code until now — the hospital's name and address (which
    lived in a JavaScript constant, so a rename meant a deployment), the
    three thresholds the dashboards alert on, and the pharmacy till's discount
    ceilings, which were the constants `100` and "something has to be paid"
    inside `sales/services.py`.

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

    # --- what the pharmacy till may discount, and who may approve more -----
    #
    # The POS has always taken discounts (rule 39): a line's own, a sale-wide
    # one, POS_DISCOUNT_ROLES only, a mandatory reason, never a change to the
    # product's price. What it had no way to say was *how much* — the only
    # ceilings were 100% and "something has to be paid", written into the
    # service. These are those ceilings, moved to where an administrator can
    # set them, and they are read by `sales/discount_policy.py` on every sale.
    #
    # **Limit is what a cashier may give alone; max is what nobody passes.**
    # Between the two sits an approval by somebody in
    # POS_DISCOUNT_APPROVAL_ROLES. The defaults are deliberately the behaviour
    # that was there before this existed — 100% and no fixed ceiling — so a
    # hospital that never opens this screen sees no change at all.
    pos_discounts_enabled = models.BooleanField(
        default=True,
        help_text="Whether the pharmacy till may give discounts at all. "
                  "Off refuses every one, whatever the role.")
    pos_discount_types = models.CharField(
        max_length=10, default="both",
        choices=[("both", "Percentage and fixed amount"), ("percent", "Percentage only"),
                 ("amount", "Fixed amount only")],
        help_text="Which kinds of discount the till offers.")
    pos_discount_limit_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100.00"),
        help_text="The largest percentage a cashier may give on their own. "
                  "Above this needs an authorised approval. 100 means no approval is ever needed.")
    pos_max_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100.00"),
        help_text="The largest percentage anybody may give, approval or not.")
    pos_discount_limit_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="The largest sum a cashier may take off on their own. "
                  "0 means no ceiling of its own — the percentage limit still applies.")
    pos_max_discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"),
        help_text="The largest sum anybody may take off, approval or not. 0 means no ceiling.")
    pos_discount_presets = models.CharField(
        max_length=120, default="5,10,15,20,25", blank=True,
        help_text="The percentage buttons the discount dialog offers, comma separated. "
                  "The cashier can always type another value, within the limits above.")
    pos_discount_reasons = models.CharField(
        max_length=400, blank=True,
        default="Staff discount,Loyal customer,Hospital concession,"
                "Management approval,Promotional discount",
        help_text="The reasons the discount dialog offers, comma separated. "
                  "A reason is always required; a cashier may write their own.")

    class Meta:
        verbose_name = "hospital settings"
        verbose_name_plural = "hospital settings"

    def __str__(self):
        return self.full_name or self.name

    @property
    def pos_discount_presets_list(self):
        """The percentage buttons, as numbers. Unreadable entries are dropped."""
        return _decimals(self.pos_discount_presets)

    @property
    def pos_discount_reasons_list(self):
        return [part.strip() for part in (self.pos_discount_reasons or "").split(",")
                if part.strip()]

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
