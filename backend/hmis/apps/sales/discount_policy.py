"""
How much the pharmacy till may take off, and who has to say so.

The POS has taken discounts since it was built (rule 39): a line's own, a
sale-wide one on top, `POS_DISCOUNT_ROLES` only, a mandatory reason, and never
a change to the product's price. What it had no way to express was *how much* —
the only ceilings were 100% and "something has to be paid", both written into
`services.py`. This module is those ceilings moved to where an administrator
sets them, plus the one thing that was missing entirely: an approval.

    limit  — what a cashier may give on their own
    max    — what nobody passes, approved or not

Between the two sits somebody in `POS_DISCOUNT_APPROVAL_ROLES`.

Three rules this file exists to keep:

* **The amount is judged, not the request.** A 10% limit that only ever looked
  at `type="percent"` would be bypassed by a fixed sum: ₦500 off a ₦1,000 line
  is 50%, whatever it is called. So every check runs on the **computed amount
  against the line it comes off**, after FEFO has priced it — which is the
  only moment either number is known.
* **An approver is authenticated here, never asserted by the client.** The till
  sends a supervisor's own credentials; `approver_for` checks the password, the
  account being active, and the role. A user id in a request body would be a
  discount anybody could self-approve with a browser console.
* **The defaults change nothing.** 100%, no fixed ceiling, both types, enabled
  — exactly the behaviour that was there before, so an untouched hospital sees
  the POS it had.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import authenticate

from apps.accounts.permissions import POS_DISCOUNT_APPROVAL_ROLES, has_any_role
from apps.core.models import HospitalSettings

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


class DiscountRefused(Exception):
    """
    A discount the policy will not allow.

    `code` says which of the three it is, so the till can tell "ask a
    supervisor" apart from "this is not allowed at all":

    * `discounts_disabled` / `discount_type_not_allowed` — turned off.
    * `discount_over_maximum` — above the ceiling; nobody can approve it.
    * `discount_needs_approval` — above the cashier's limit; an authorised
      person can.
    """

    def __init__(self, message, *, code, detail=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or {}


@dataclass(frozen=True)
class DiscountPolicy:
    """What this hospital allows, read off `HospitalSettings`."""
    enabled: bool = True
    types: str = "both"
    limit_percent: Decimal = HUNDRED
    max_percent: Decimal = HUNDRED
    limit_amount: Decimal = ZERO      # 0 = no ceiling of its own
    max_amount: Decimal = ZERO        # 0 = no ceiling of its own
    presets: tuple = ()
    reasons: tuple = ()

    @classmethod
    def load(cls, settings_row=None):
        row = settings_row or HospitalSettings.load()
        return cls(
            enabled=row.pos_discounts_enabled,
            types=row.pos_discount_types,
            limit_percent=Decimal(row.pos_discount_limit_percent),
            max_percent=Decimal(row.pos_max_discount_percent),
            limit_amount=Decimal(row.pos_discount_limit_amount),
            max_amount=Decimal(row.pos_max_discount_amount),
            presets=tuple(row.pos_discount_presets_list),
            reasons=tuple(row.pos_discount_reasons_list),
        )

    # -- what the till shows ------------------------------------------------

    def allows(self, kind):
        return self.enabled and (self.types == "both" or self.types == kind)

    def as_dict(self):
        """The shape the POS reads, so the dialog offers only what is allowed."""
        return {
            "enabled": self.enabled,
            "types": self.types,
            "limit_percent": str(self.limit_percent),
            "max_percent": str(self.max_percent),
            "limit_amount": str(self.limit_amount),
            "max_amount": str(self.max_amount),
            "presets": [str(value) for value in self.presets],
            "reasons": list(self.reasons),
            "approval_roles": list(POS_DISCOUNT_APPROVAL_ROLES),
        }

    # -- what the till may do ----------------------------------------------

    def check_request(self, kind):
        """The shape check, before anything is priced: is this kind allowed at all?"""
        if not self.enabled:
            raise DiscountRefused(
                "Discounts are switched off at the pharmacy till.",
                code="discounts_disabled")
        if not self.allows(kind):
            offered = "percentages" if self.types == "percent" else "fixed amounts"
            raise DiscountRefused(
                f"This hospital's till takes {offered} only.",
                code="discount_type_not_allowed", detail={"types": self.types})

    def check_amount(self, *, amount, base, label, approver=None):
        """
        Judge a **priced** discount: `amount` off `base`, on `label`.

        Returns True when an approval was needed and given, False when it was
        within the cashier's own limit. Raises `DiscountRefused` otherwise, and
        raises *before* anything is written — no stock has moved and no money
        has been taken when this runs.
        """
        if amount <= 0:
            return False
        percent = (amount / base * HUNDRED) if base > 0 else HUNDRED

        # The absolute ceilings. Nobody passes these, so an approver is not
        # even asked about them — being told "get a supervisor" for something
        # no supervisor can do is worse than being told no.
        if self.max_percent < HUNDRED and percent > self.max_percent:
            raise DiscountRefused(
                f"{_pc(percent)}% off {label} is more than the {_pc(self.max_percent)}% "
                f"this hospital allows on any discount.",
                code="discount_over_maximum",
                detail={"limit": "percent", "maximum": str(self.max_percent),
                        "requested_percent": _pc(percent), "amount": str(amount)})
        if self.max_amount > 0 and amount > self.max_amount:
            raise DiscountRefused(
                f"{amount:,.2f} off {label} is more than the {self.max_amount:,.2f} "
                f"this hospital allows on any discount.",
                code="discount_over_maximum",
                detail={"limit": "amount", "maximum": str(self.max_amount),
                        "amount": str(amount)})

        over_percent = self.limit_percent < HUNDRED and percent > self.limit_percent
        over_amount = self.limit_amount > 0 and amount > self.limit_amount
        if not (over_percent or over_amount):
            return False
        if approver is None:
            raise DiscountRefused(
                _needs(percent, amount, label, over_percent, self.limit_percent,
                       self.limit_amount),
                code="discount_needs_approval",
                detail={"limit": "percent" if over_percent else "amount",
                        "limit_percent": str(self.limit_percent),
                        "limit_amount": str(self.limit_amount),
                        "requested_percent": _pc(percent), "amount": str(amount),
                        "line": label})
        return True


def _pc(value):
    """A percentage as somebody would say it: 25, or 12.5 — never 25.00."""
    return f"{value.quantize(Decimal('0.1')).normalize():f}"


def _needs(percent, amount, label, over_percent, limit_percent, limit_amount):
    if over_percent:
        return (f"{_pc(percent)}% off {label} is above your {_pc(limit_percent)}% limit. "
                f"An accountant or an administrator has to authorise it.")
    return (f"{amount:,.2f} off {label} is above your {limit_amount:,.2f} limit. "
            f"An accountant or an administrator has to authorise it.")


def approver_for(authorization, *, operator):
    """
    Who is authorising an over-limit discount, or None.

    Two ways one is found, and both end in a real account with a real role:

    * the operator is already authorised — an accountant or an administrator
      working the till approves by being who they are;
    * a supervisor puts their own username and password into the till, which
      is authenticated **here**. Nothing about the approver is taken from the
      request except the credentials themselves, so a cashier cannot name an
      accountant and have it believed.

    A wrong password, an inactive account or an unauthorised role is refused
    with `discount_approval_failed` — never silently treated as "no approver",
    which would answer "ask a supervisor" to somebody who just did.
    """
    if has_any_role(operator, POS_DISCOUNT_APPROVAL_ROLES):
        return operator
    if not isinstance(authorization, dict):
        return None
    username = str(authorization.get("username") or "").strip()
    password = str(authorization.get("password") or "")
    if not username and not password:
        return None
    if not (username and password):
        raise DiscountRefused("Enter the authorising person's username and password.",
                              code="discount_approval_failed")
    user = authenticate(username=username, password=password)
    if user is None or not user.is_active:
        raise DiscountRefused("That username and password were not accepted.",
                              code="discount_approval_failed")
    if not has_any_role(user, POS_DISCOUNT_APPROVAL_ROLES):
        raise DiscountRefused(
            f"{user.get_full_name() or user.username} is not authorised to approve a discount "
            f"above the limit. An accountant or an administrator has to.",
            code="discount_approval_failed")
    return user
