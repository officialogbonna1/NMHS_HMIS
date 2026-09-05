"""
Arranging stock in a test.

Stock is product + batch + **location**, and only what is standing in the
Pharmacy can be dispensed — so `Batch.objects.create(...)` on its own puts
nothing anywhere, and a test that expects to fill a prescription from it
fails in a way that looks like a permissions bug.

These two helpers are the short way to say where the stock is. Use
`stock_the_pharmacy()` when the test is about dispensing, `stock_the_store()`
when it is about receiving or transferring.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import (
    MAIN_STORE, PHARMACY, Batch, Item, ItemCategory, StockLocation, UnitOfMeasure,
)
from .services import receive_stock


def unit(name="unit", abbreviation=""):
    """A unit of measure row — these are configuration, not a string."""
    return UnitOfMeasure.objects.get_or_create(
        name=name, defaults={"abbreviation": abbreviation})[0]


def category(name):
    return ItemCategory.objects.get_or_create(name=name)[0]


def product(name, *, unit_name="unit", category_name="", **kwargs):
    """
    A catalogue product with its category and unit resolved to rows.

    `Item.category` and `Item.unit` are relations now, so a test that passes
    strings gets a `ValueError` from the ORM. This is the short way to say
    what a product is.
    """
    return Item.objects.create(
        name=name,
        unit=unit(unit_name) if unit_name else None,
        category=category(category_name) if category_name else None,
        **kwargs,
    )


def _place(*, item, quantity, actor, code, batch_no="B1", cost_price="10",
           sale_price="20", expiry_days=180):
    batch = Batch.objects.create(
        item=item, batch_no=batch_no,
        cost_price=Decimal(cost_price), sale_price=Decimal(sale_price),
        expiry_date=timezone.localdate() + timedelta(days=expiry_days),
    )
    receive_stock(batch=batch, quantity=quantity, actor=actor,
                  location=StockLocation.objects.get(code=code))
    return batch


def stock_the_pharmacy(*, item, quantity, actor, **kwargs):
    """A lot standing on the dispensing shelf — ready to be given to a patient."""
    return _place(item=item, quantity=quantity, actor=actor, code=PHARMACY, **kwargs)


def stock_the_store(*, item, quantity, actor, **kwargs):
    """A lot in the Main Store — received, but not dispensable until transferred."""
    return _place(item=item, quantity=quantity, actor=actor, code=MAIN_STORE, **kwargs)
