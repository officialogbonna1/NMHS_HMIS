"""
The pharmacy POS, as services — the only code that opens or closes a register,
holds or completes a sale, or takes a return.

Each operation is one transaction. A completed sale leaves behind its stock
movements, its `Payment` (and, for a registered patient, the Pharmacy charge
that payment settled) and a completed receipt — or, if anything refuses, it
leaves behind nothing at all. There is no state with stock gone and no payment,
or a payment and no stock movement.

What this module deliberately does **not** own:

* the shelf — `inventory.services.consume_fefo` / `quarantine_return`, the same
  FEFO order dispensing uses;
* the money — `billing.services.add_charge`, `apply_amount_discount`,
  `record_pos_payment`, `refund_payment` and `credit_returned_goods`;
* who may do what — the role groups in `accounts/permissions.py`, checked here
  as well as in the views, so a service is never a way round the API.

Discounts are approvals (rule 13): only POS_DISCOUNT_ROLES give one, every one
needs a reason, and a product's price is never changed to express one. A line
may carry its own discount and the sale may carry one more; whatever the mix,
no line goes below zero and the sale never completes for nothing. How large one
may be, and who has to authorise an unusual one, is
`sales/discount_policy.py` — read from the hospital's settings, judged on the
**priced** amount, and enforced here rather than on the screen.
"""
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.accounts.permissions import (ADMIN_ROLES, POS_DISCOUNT_ROLES, POS_RETURN_ROLES,
                                       POS_ROLES, has_any_role)
from apps.billing.models import Payment, Refund
from apps.billing.services import (add_charge, apply_amount_discount, credit_returned_goods,
                                   record_pos_payment, refund_payment, refundable_balance)
from apps.core.services import audit_event
from apps.inventory.models import Item, dispensing_location
from apps.inventory.services import consume_fefo, quarantine_return

from .discount_policy import DiscountPolicy, DiscountRefused, approver_for
from .models import PosRegister, Sale, SaleItem, SaleLine, SaleReturn, SaleReturnLine

#: The `Charge.source_type` of a registered patient's POS purchase — Pharmacy
#: in `billing/departments.py`, and distinct from `prescription` so the
#: department report can tell dispensing from the counter.
POS_SOURCE = "pos_sale"
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
METHODS = dict(Payment.METHOD)


def _money(value, what="amount"):
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"Enter a valid {what}.")


def _whole(value, what):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{what} must be a whole number.")
    return number


def can_operate_till(user):
    return has_any_role(user, POS_ROLES)


def can_discount(user):
    return has_any_role(user, POS_DISCOUNT_ROLES)


def can_take_return(user):
    return has_any_role(user, POS_RETURN_ROLES)


def _require_till(user):
    if not can_operate_till(user):
        raise PermissionDenied("Only a pharmacist or a cashier operates the POS till.")


# --------------------------------------------------------------- registers


def current_register(operator):
    return (PosRegister.objects.select_related("location")
            .filter(opened_by=operator, status="open").first())


def open_register(*, operator, opening_float=0, request=None):
    _require_till(operator)
    location = dispensing_location()
    if location is None:
        raise ValidationError("No dispensing location is configured, so there is no shelf to sell from.")
    opening = _money(opening_float or 0, "opening float")
    if opening < 0:
        raise ValidationError("The opening float cannot be negative.")
    existing = current_register(operator)
    if existing is not None:
        raise ValidationError(f"You already have register {existing.reference} open — close it first.")
    try:
        with transaction.atomic():
            register = PosRegister.objects.create(location=location, opened_by=operator,
                                                  opening_float=opening)
    except IntegrityError:
        raise ValidationError("You already have a register open — close it first.")
    audit_event(actor=operator, action="pos.register_opened", instance=register,
                details={"reference": register.reference, "opening_float": str(opening),
                         "location": location.code}, request=request)
    return register


def register_summary(register):
    """
    What a register took, read off the payments and refunds already recorded
    against it — the reconciliation, never a second copy of the money.

    Cash expected in the drawer is the opening float, plus cash payments for
    sales completed on this register, less cash refunds paid out of it.
    """
    payments = (Payment.objects.filter(pos_sale__register=register)
                .values("method").annotate(amount=Sum("amount"), count=Count("id")))
    refunds = (Refund.objects.filter(pos_return__register=register)
               .values("method").annotate(amount=Sum("amount"), count=Count("id")))
    taken = {row["method"]: row for row in payments}
    given = {row["method"]: row for row in refunds}
    sales = Sale.objects.filter(register=register, status="completed").aggregate(
        count=Count("id"), gross=Sum("subtotal"), discounts=Sum("discount_amount"))

    methods = []
    for key, label in Payment.METHOD:
        if key not in taken and key not in given:
            continue
        received = _money(taken.get(key, {}).get("amount") or 0)
        refunded = _money(given.get(key, {}).get("amount") or 0)
        methods.append({"key": key, "label": label, "received": str(received),
                        "refunded": str(refunded), "net": str(received - refunded),
                        "count": taken.get(key, {}).get("count", 0)})

    takings = sum((_money(row["amount"]) for row in taken.values()), ZERO)
    refunded_total = sum((_money(row["amount"]) for row in given.values()), ZERO)
    cash_in = _money(taken.get("cash", {}).get("amount") or 0)
    cash_out = _money(given.get("cash", {}).get("amount") or 0)
    expected = _money(register.opening_float) + cash_in - cash_out
    return {
        "register": register.reference,
        "opening_float": str(_money(register.opening_float)),
        "sales_count": sales["count"],
        "gross": str(_money(sales["gross"] or 0)),
        "discounts": str(_money(sales["discounts"] or 0)),
        "takings": str(takings),
        "refunds": str(refunded_total),
        "net_takings": str(takings - refunded_total),
        "methods": methods,
        "cash_received": str(cash_in),
        "cash_refunded": str(cash_out),
        "expected_cash": str(expected),
        "held_sales": Sale.objects.filter(register=register, status="held").count(),
    }


def close_register(*, register, actor, counted_cash, note="", request=None):
    if counted_cash in (None, ""):
        raise ValidationError("Count the cash in the drawer and enter it.")
    counted = _money(counted_cash, "counted cash")
    if counted < 0:
        raise ValidationError("Counted cash cannot be negative.")
    with transaction.atomic():
        locked = PosRegister.objects.select_for_update().get(pk=register.pk)
        if locked.status != "open":
            raise ValidationError(f"{locked.reference} is already closed.")
        is_admin = getattr(actor, "role", None) in ADMIN_ROLES
        if locked.opened_by_id != actor.pk and not is_admin:
            raise PermissionDenied("Only the operator who opened a register, or an administrator, closes it.")
        summary = register_summary(locked)
        expected = Decimal(summary["expected_cash"])
        variance = counted - expected
        now = timezone.now()
        closed = PosRegister.objects.filter(pk=locked.pk, status="open").update(
            status="closed", closed_by=actor, closed_at=now, expected_cash=expected,
            counted_cash=counted, variance=variance, closing_note=str(note or "").strip(),
            closing_summary=summary, updated_at=now)
        if closed != 1:
            raise ValidationError(f"{locked.reference} is already closed.")
    locked.refresh_from_db()
    audit_event(actor=actor, action="pos.register_closed", instance=locked,
                details={"reference": locked.reference, "expected_cash": str(expected),
                         "counted_cash": str(counted), "variance": str(variance),
                         "takings": summary["takings"], "refunds": summary["refunds"]},
                request=request)
    if variance != 0:
        audit_event(actor=actor, action="pos.register_variance", instance=locked,
                    details={"reference": locked.reference, "variance": str(variance),
                             "note": locked.closing_note}, request=request)
    return locked


# ------------------------------------------------------------------- carts


def _requested(discount, policy=None):
    """
    A discount request as `(kind, value)`, or None when nothing is asked for.
    Validates the shape only; what it comes to depends on the batch prices, so
    the amount — and the policy's ceilings, which are judged on the amount —
    are worked out when the sale completes.

    `policy` refuses a *kind* the hospital does not offer, which is the one
    part of a policy that can be answered without a price.
    """
    if not isinstance(discount, dict):
        return None
    kind = str(discount.get("type") or "").strip()
    raw = discount.get("value")
    if not kind and raw in (None, "", 0, "0"):
        return None
    if kind not in dict(Sale.DISCOUNT):
        raise ValidationError("A discount is a percentage or a fixed amount.")
    value = _money(raw, "discount")
    if value <= 0:
        raise ValidationError("A discount must be greater than zero.")
    if kind == "percent" and value > 100:
        raise ValidationError("A percentage discount cannot be more than 100%.")
    if policy is not None:
        policy.check_request(kind)
    return kind, value


def _cart(lines, policy=None):
    """
    `[{"item": id, "quantity": n, "discount": {type, value}?}, …]` →
    `[(Item, n, request-or-None), …]`, one line per product.

    The same product twice is merged into one line — unless either copy carries
    a discount, because which quantity the discount was meant for is a guess.
    """
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValidationError("The cart is empty.")
    merged, asked = {}, {}
    for entry in lines:
        entry = entry if isinstance(entry, dict) else {}
        item_id = _whole(entry.get("item"), "Each cart line's product")
        quantity = _whole(entry.get("quantity"), "Quantity")
        if quantity < 1:
            raise ValidationError("Quantities must be at least 1.")
        request = _requested(entry.get("discount"), policy)
        if item_id in merged:
            if request is not None or asked[item_id] is not None:
                raise ValidationError("A discounted product is on the cart twice — put the whole "
                                      "quantity on one line.")
            merged[item_id] += quantity
        else:
            merged[item_id], asked[item_id] = quantity, request
    items = Item.objects.in_bulk(list(merged))
    if len(items) != len(merged):
        raise ValidationError("One of these products is no longer in the catalogue.")
    retired = [items[pk].name for pk in merged if not items[pk].is_active]
    if retired:
        raise ValidationError(f"{', '.join(retired)} is no longer sold.")
    return [(items[pk], quantity, asked[pk]) for pk, quantity in merged.items()]


def _customer(customer_type, patient, customer_name, customer_phone):
    if customer_type not in dict(Sale.CUSTOMER):
        raise ValidationError("Choose a walk-in customer or a registered patient.")
    if customer_type == "patient":
        if patient is None:
            raise ValidationError("Pick the registered patient this sale is for.")
        return patient, "", ""
    # A walk-in stays a walk-in: no patient row is looked up or created.
    return None, str(customer_name or "").strip()[:120], str(customer_phone or "").strip()[:40]


def hold_sale(*, operator, lines, customer_type="walk_in", patient=None, customer_name="",
              customer_phone="", sale=None, request=None):
    """
    Park a cart. Lines only: no batch is chosen, no stock moves, no charge or
    payment exists — a held sale is invisible to inventory and to the money.
    A discount is not held either: it is approved when the sale completes.
    """
    _require_till(operator)
    cart = _cart(lines)
    patient, name, phone = _customer(customer_type, patient, customer_name, customer_phone)
    with transaction.atomic():
        register = current_register(operator)
        if register is None:
            raise ValidationError("Open a register before holding a sale.")
        if sale is None:
            sale = Sale.objects.create(status="held", register=register, sold_by=operator,
                                       customer_type=customer_type, patient=patient,
                                       customer_name=name, customer_phone=phone)
        else:
            sale = Sale.objects.select_for_update().get(pk=sale.pk)
            if sale.status != "held":
                raise ValidationError(f"{sale.reference} is {sale.get_status_display().lower()}, not held.")
            sale.register, sale.sold_by = register, operator
            sale.customer_type, sale.patient = customer_type, patient
            sale.customer_name, sale.customer_phone = name, phone
            sale.save()
            sale.lines.all().delete()
        for item, quantity, _ in cart:
            SaleLine.objects.create(sale=sale, item=item, quantity=quantity)
    audit_event(actor=operator, action="pos.sale_held", instance=sale,
                details={"reference": sale.reference, "lines": len(cart),
                         "customer_type": customer_type}, request=request)
    return sale


def discard_held_sale(*, sale, actor, request=None):
    _require_till(actor)
    now = timezone.now()
    discarded = Sale.objects.filter(pk=sale.pk, status="held").update(
        status="cancelled", cancelled_at=now, cancelled_by=actor, updated_at=now)
    if discarded != 1:
        raise ValidationError("Only a held sale can be discarded; a completed sale is returned, never deleted.")
    sale.refresh_from_db()
    audit_event(actor=actor, action="pos.sale_discarded", instance=sale,
                details={"reference": sale.reference}, request=request)
    return sale


# -------------------------------------------------------------- completion


def _off(kind, value, base):
    """What a percentage or a fixed amount comes to against `base`."""
    if kind == "percent":
        return (base * value / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return value


def _spread_discount(lines, amount):
    """
    Share a sale-wide discount across the lines in proportion to what each still
    costs after its own discount. No share is ever more than its line has left,
    and rounding is settled a kobo at a time on lines with room, so the shares
    add up to the amount exactly and no line's net goes below zero.
    """
    bases = [line.gross - line.line_discount for line in lines]
    room = sum(bases, ZERO)
    shares = [ZERO] * len(lines)
    if amount > 0 and room > 0:
        shares = [min((amount * base / room).quantize(CENT, rounding=ROUND_HALF_UP), base)
                  for base in bases]
        gap = amount - sum(shares, ZERO)
        step = CENT if gap > 0 else -CENT
        widest = sorted(range(len(lines)), key=lambda i: bases[i] - shares[i], reverse=True)
        # The caller guarantees amount < room, so a line with room (or with a
        # share to give back) always exists; the bound is a backstop, not logic.
        for turn in range(len(lines) * 100 + 100):
            if gap == 0:
                break
            index = widest[turn % len(widest)]
            if (step > 0 and shares[index] < bases[index]) or (step < 0 and shares[index] > 0):
                shares[index] += step
                gap -= step
        if gap != 0:
            raise ValidationError("The discount could not be shared across the lines.")
    for line, share in zip(lines, shares):
        line.discount = line.line_discount + share
        line.net = line.gross - line.discount
        line.save(update_fields=["unit_price", "gross", "discount", "net", "line_discount_type",
                                 "line_discount_value", "line_discount", "updated_at"])


def _price_discounts(priced, subtotal, order, policy, approver):
    """
    Turn discount requests into amounts, now each line's gross is known, and
    refuse any that would leave nothing to pay. Returns
    `(line_discounts, sale_discount, approval_used)`.

    A line's own discount comes off that line first and can never be more than
    the line. A sale-wide percentage is then taken off what is left; a sale-wide
    fixed amount is that amount. Whatever the mix, the total must stay below the
    subtotal: the till never completes a sale for nothing.

    **This is where the policy is enforced**, because this is the first moment
    the two numbers it judges both exist: the amount, and the line it comes
    off. Checking the *request* instead would let a fixed sum walk past a
    percentage limit — ₦500 off a ₦1,000 line is 50% however it was typed — so
    every discount is measured against its own base here, priced, before a
    single row is saved. Each line is judged separately and the sale-wide one
    against what is left, so one over-limit line is refused by name rather than
    the whole cart being refused vaguely.
    """
    line_total = ZERO
    approval_used = False
    for line, request in priced:
        line.line_discount_type, line.line_discount_value, line.line_discount = "", ZERO, ZERO
        if request is None:
            continue
        kind, value = request
        amount = _off(kind, value, line.gross)
        if amount > line.gross:
            raise ValidationError(
                f"A discount of {amount:,.2f} on {line.item.name} is more than the line's "
                f"{line.gross:,.2f}.")
        approval_used |= policy.check_amount(amount=amount, base=line.gross,
                                             label=line.item.name, approver=approver)
        line.line_discount_type, line.line_discount_value, line.line_discount = kind, value, amount
        line_total += amount
    sale_amount = _off(*order, subtotal - line_total) if order else ZERO
    if sale_amount:
        approval_used |= policy.check_amount(amount=sale_amount, base=subtotal - line_total,
                                             label="this sale", approver=approver)
    total = line_total + sale_amount
    if total and total >= subtotal:
        raise ValidationError(
            f"Discounts of {total:,.2f} cannot cover the whole {subtotal:,.2f} sale — "
            f"something has to be paid.")
    _spread_discount([line for line, _ in priced], sale_amount)
    return line_total, sale_amount, approval_used


def _tender(method, amount_tendered, total):
    if method != "cash" or amount_tendered in (None, ""):
        return total, ZERO
    tendered = _money(amount_tendered, "amount received")
    if tendered < total:
        raise ValidationError(f"{tendered:,.2f} received is short of the {total:,.2f} due.")
    return tendered, tendered - total


def _token(value):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ValidationError("The till sent an invalid transaction token.")


def complete_sale(*, operator, lines, customer_type="walk_in", patient=None, customer_name="",
                  customer_phone="", discount=None, discount_reason="", authorization=None,
                  payment_method="cash",
                  amount_tendered=None, client_token=None, held_sale=None, request=None):
    """
    Sell: stock off the shelf FEFO, the payment taken, the receipt completed —
    together or not at all. Returns `(sale, created)`; `created` is False when
    the same till token had already completed this sale.

    `discount` is the sale-wide `{type, value, reason}`; a line's own discount
    rides on its cart line. One reason covers both (`discount.reason`, else
    `discount_reason`), and one `authorization` — a supervisor's own username
    and password — covers every discount on the receipt that needs one
    (`discount.authorization` is accepted too). How much may be given, and
    when an authorisation is needed at all, is `discount_policy.py`.
    """
    _require_till(operator)
    if payment_method not in METHODS:
        raise ValidationError("Choose how the customer paid.")
    token = _token(client_token)
    if token is not None:
        done = Sale.objects.filter(client_token=token, status="completed").first()
        if done is not None:
            return done, False
    try:
        with transaction.atomic():
            sale = _complete(operator=operator, lines=lines, customer_type=customer_type,
                             patient=patient, customer_name=customer_name,
                             customer_phone=customer_phone, discount=discount,
                             discount_reason=discount_reason, authorization=authorization,
                             payment_method=payment_method,
                             amount_tendered=amount_tendered, token=token, held_sale=held_sale)
    except IntegrityError:
        # Two submissions of one cart raced past the check above; the unique
        # token let exactly one of them commit, and this is the other.
        done = Sale.objects.filter(client_token=token, status="completed").first() if token else None
        if done is None:
            raise
        return done, False

    audit_event(actor=operator, action="pos.sale_completed", instance=sale,
                details={"reference": sale.reference, "total": str(sale.total_amount),
                         "method": sale.payment_method, "customer_type": sale.customer_type,
                         "payment": sale.payment_id, "register": sale.register.reference},
                request=request)
    if sale.discount_amount:
        audit_event(actor=operator, action="pos.discount_applied", instance=sale,
                    details=_discount_audit(sale), request=request)
    return sale, True


def _discount_audit(sale):
    """
    What a future administrator needs to answer "who discounted this, by how
    much, for whom, and why?" — per line, not just per sale.

    The sale's own total said what was given away but not what it was given
    away *on*: a receipt with one over-limit line and four ordinary ones read
    as a single number. Each line is recorded with the price it was sold at,
    the quantity, its gross, the discount as asked and as priced, and what was
    left — so the arithmetic can be rechecked from the audit row alone, years
    later, without the sale.
    """
    lines = list(sale.lines.select_related("item").all())
    return {
        "reference": sale.reference,
        "customer_type": sale.customer_type,
        "customer": sale.customer_label,
        "patient_number": sale.patient.patient_number if sale.patient_id else "",
        "subtotal": str(sale.subtotal),
        "amount": str(sale.discount_amount),
        "line_discounts": str(sum((line.line_discount for line in lines), ZERO)),
        # The sale-wide part: what `discount_type`/`discount_value` describe.
        "sale_discount_type": sale.discount_type,
        "sale_discount_value": str(sale.discount_value),
        "sale_discount": str(sale.discount_amount
                             - sum((line.line_discount for line in lines), ZERO)),
        "total": str(sale.total_amount),
        "reason": sale.discount_reason,
        "applied_by": _who(sale.discount_by),
        # NULL unless the policy required an authorisation — see `_complete`.
        "approved_by": _who(sale.discount_approved_by),
        "lines": [{
            "item": line.item_id, "product": line.item.name,
            "unit_price": str(line.unit_price or ZERO), "quantity": line.quantity,
            "gross": str(line.gross),
            "line_discount_type": line.line_discount_type,
            "line_discount_value": str(line.line_discount_value),
            "line_discount": str(line.line_discount),
            # `discount` is the line's own plus its share of any sale-wide one.
            "discount": str(line.discount), "net": str(line.net),
        } for line in lines if line.discount],
    }


def _who(user):
    if user is None:
        return ""
    return user.get_full_name() or user.username


def _complete(*, operator, lines, customer_type, patient, customer_name, customer_phone,
              discount, discount_reason, authorization, payment_method, amount_tendered,
              token, held_sale):
    register = (PosRegister.objects.select_for_update().select_related("location")
                .filter(opened_by=operator, status="open").first())
    if register is None:
        raise ValidationError("Open a register before completing a sale.")
    policy = DiscountPolicy.load()
    cart = _cart(lines, policy)
    patient, name, phone = _customer(customer_type, patient, customer_name, customer_phone)

    # Who may discount, and why, is settled before a single unit moves.
    order = _requested(discount, policy)
    sale_wide_reason = discount.get("reason") if isinstance(discount, dict) else ""
    reason = str(sale_wide_reason or "").strip() or str(discount_reason or "").strip()
    approver = None
    if order is not None or any(asked is not None for _, _, asked in cart):
        if not can_discount(operator):
            raise PermissionDenied(
                "Only a cashier, an accountant or an administrator can apply a POS discount.")
        if not reason:
            raise ValidationError("A discount needs a reason.")
        # Authenticated here, from credentials — never taken as a user id from
        # the request body, which would be a discount anybody could approve for
        # themselves. It is resolved before pricing so a wrong password fails
        # the sale rather than half of it.
        on_discount = discount.get("authorization") if isinstance(discount, dict) else None
        approver = approver_for(authorization or on_discount, operator=operator)
    now = timezone.now()

    if held_sale is not None:
        # Compare-and-set: of two tills completing the same held cart, only one
        # finds it still held.
        claimed = Sale.objects.filter(pk=held_sale.pk, status="held").update(
            status="completed", completed_at=now, client_token=token, updated_at=now)
        if claimed != 1:
            raise ValidationError("This sale has already been completed or discarded.")
        sale = Sale.objects.select_for_update().get(pk=held_sale.pk)
        sale.lines.all().delete()
    else:
        sale = Sale.objects.create(status="completed", completed_at=now, client_token=token,
                                   register=register, sold_by=operator)

    subtotal, priced = ZERO, []
    for item, quantity, asked in cart:
        pieces = consume_fefo(item=item, quantity=quantity, location=register.location,
                              actor=operator, reason="sale", reference=sale.reference)
        line = SaleLine.objects.create(sale=sale, item=item, quantity=quantity,
                                       unit_price=pieces[0]["batch"].sale_price)
        gross = ZERO
        for piece in pieces:
            batch = piece["batch"]
            SaleItem.objects.create(sale=sale, line=line, batch=batch,
                                    quantity=piece["quantity"], unit_price=batch.sale_price)
            gross += batch.sale_price * piece["quantity"]
        line.gross = gross.quantize(CENT)
        subtotal += line.gross
        priced.append((line, asked))
    if subtotal <= 0:
        raise ValidationError("These products have no selling price on their batches.")

    line_discounts, sale_discount, approval_used = _price_discounts(
        priced, subtotal, order, policy, approver)
    discount_amount = line_discounts + sale_discount
    total = subtotal - discount_amount
    tendered, change = _tender(payment_method, amount_tendered, total)

    charge = None
    if patient is not None:
        # The patient's statement shows the purchase as a Pharmacy charge, the
        # authorised discount on it and the payment that settled it.
        charge = add_charge(patient=patient, description=f"Pharmacy sale {sale.reference}",
                            amount=subtotal, created_by=operator, source_type=POS_SOURCE,
                            source_id=sale.pk, notify=False)
        if discount_amount:
            apply_amount_discount(charge=charge, amount=discount_amount,
                                  reason=f"POS {sale.reference}: {reason}",
                                  approved_by=operator, notify=False)
    payment = record_pos_payment(amount=total, method=payment_method, received_by=operator,
                                 reference=sale.reference, patient=patient, charge=charge)

    sale.register, sale.sold_by = register, operator
    sale.customer_type, sale.patient = customer_type, patient
    sale.customer_name, sale.customer_phone = name, phone
    sale.subtotal, sale.discount_amount, sale.total_amount = subtotal, discount_amount, total
    if discount_amount:
        # `discount_type` / `discount_value` describe the sale-wide discount
        # only; each line keeps its own. `discount_amount` is all of it.
        sale.discount_type, sale.discount_value = order if order else ("", ZERO)
        sale.discount_reason, sale.discount_by, sale.discount_at = reason, operator, now
        # Only when the policy actually required one. A discount inside the
        # cashier's own limit records no approver, even where an accountant
        # happened to ring it up, so the column reads as "this needed
        # authorising" rather than "an accountant was present".
        sale.discount_approved_by = approver if approval_used else None
    sale.payment_method, sale.amount_tendered, sale.change_due = payment_method, tendered, change
    sale.payment, sale.charge = payment, charge
    sale.status, sale.completed_at, sale.client_token = "completed", now, token
    sale.save()
    return sale


# ----------------------------------------------------------------- returns


def _piece_value(piece, quantity):
    """What `quantity` units of this batch piece actually cost the customer, net of discount."""
    line = piece.line
    gross = piece.unit_price * piece.quantity
    share = (line.discount * gross / line.gross) if line and line.gross else ZERO
    net = gross - share
    if quantity >= piece.returnable_quantity:
        # The last units take whatever is left, so rounding never strands a kobo.
        already = piece.return_lines.aggregate(v=Sum("amount"))["v"] or ZERO
        return (net - already).quantize(CENT, rounding=ROUND_HALF_UP)
    return (net * quantity / piece.quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def process_return(*, sale, operator, lines, reason, refund_method=None, request=None):
    """
    Take goods back against a completed sale.

    The sale and its payment are left exactly as they were. The medicine is
    received into the returns quarantine (never onto the shelf); the money goes
    back as a `Refund` against the sale's payment; and for a registered patient
    the Pharmacy charge records the goods returned, so their statement still
    balances.
    """
    if not can_take_return(operator):
        raise PermissionDenied("A POS return pays money out of a till, so it needs a cashier "
                               "or an administrator.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Give a reason for the return.")
    if refund_method not in (None, "") and refund_method not in METHODS:
        raise ValidationError("Choose how the money goes back.")
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValidationError("Choose what is being returned.")
    wanted = {}
    for entry in lines:
        entry = entry if isinstance(entry, dict) else {}
        piece_id = _whole(entry.get("sale_item"), "Each returned item")
        quantity = _whole(entry.get("quantity"), "Quantity")
        if quantity < 1:
            continue
        wanted[piece_id] = wanted.get(piece_id, 0) + quantity
    if not wanted:
        raise ValidationError("Choose what is being returned.")

    with transaction.atomic():
        register = PosRegister.objects.select_for_update().filter(
            opened_by=operator, status="open").first()
        if register is None:
            raise ValidationError("Open a register — the refund is paid out of it.")
        locked = (Sale.objects.select_for_update().select_related("payment", "charge")
                  .get(pk=sale.pk))
        if locked.status != "completed" or locked.payment_id is None:
            raise ValidationError("Only a completed, paid sale can be returned.")
        pieces = {piece.pk: piece for piece in SaleItem.objects.select_for_update()
                  .select_related("line", "batch__item").filter(sale=locked)}
        if any(pk not in pieces for pk in wanted):
            raise ValidationError("That item is not on this sale.")

        ret = SaleReturn.objects.create(sale=locked, register=register, reason=reason,
                                        refund_method=refund_method or locked.payment_method,
                                        processed_by=operator)
        total, created = ZERO, []
        for pk, quantity in wanted.items():
            piece = pieces[pk]
            returnable = piece.returnable_quantity
            if quantity > returnable:
                raise ValidationError(
                    f"Only {returnable} of {piece.batch.item.name} (batch {piece.batch.batch_no}) "
                    f"can still be returned.")
            amount = _piece_value(piece, quantity)
            created.append(SaleReturnLine.objects.create(sale_return=ret, sale_item=piece,
                                                         quantity=quantity, amount=amount))
            quarantine_return(batch=piece.batch, quantity=quantity, actor=operator,
                              reference=ret.reference)
            total += amount

        # Everything on the sale has now come back: give back exactly what is
        # left on the payment, so rounding can neither strand nor overpay a kobo.
        if all(piece.returnable_quantity == 0 for piece in pieces.values()):
            remaining = refundable_balance(locked.payment)
            delta = remaining - total
            if delta and created:
                last = created[-1]
                last.amount += delta
                last.save(update_fields=["amount", "updated_at"])
                total = remaining
        if total <= 0:
            raise ValidationError("Nothing of value is being returned.")

        refund = refund_payment(payment=locked.payment, amount=total,
                                reason=f"POS return {ret.reference}: {reason}",
                                processed_by=operator, method=ret.refund_method,
                                reference=ret.reference, charge=locked.charge, notify=False,
                                pos_return=True)
        if locked.charge_id:
            credit_returned_goods(charge=locked.charge, amount=total,
                                  reason=f"POS return {ret.reference}: {reason}",
                                  approved_by=operator)
        ret.amount, ret.refund = total, refund
        ret.save(update_fields=["amount", "refund", "updated_at"])

    audit_event(actor=operator, action="pos.sale_returned", instance=ret,
                details={"reference": ret.reference, "sale": locked.reference,
                         "amount": str(total), "method": ret.refund_method,
                         "lines": [{"batch": pieces[pk].batch.batch_no, "quantity": q}
                                   for pk, q in wanted.items()],
                         "reason": reason}, request=request)
    return ret
