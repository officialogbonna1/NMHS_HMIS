"""
Fill in the order-time snapshot for tests ordered before snapshots existed.

Their result form and report were reading the live catalogue, so an edit to a
reference range would have silently rewritten what an old report said. There
is no way to recover the values as they were on the day, so the best that can
be done is to freeze them as they are now — from here on they cannot drift.

Statuses move with the same migration: `in_progress` was a draft being typed
and `resulted` was a result submitted for verification.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    LabOrderTest = apps.get_model("laboratory", "LabOrderTest")
    LabParameter = apps.get_model("laboratory", "LabParameter")

    TYPE_OPTIONS = {
        "positive_negative": ["Positive", "Negative"],
        "reactive_nonreactive": ["Reactive", "Non-reactive"],
        "detected_notdetected": ["Detected", "Not detected"],
        "normal_abnormal": ["Normal", "Abnormal"],
    }

    for item in LabOrderTest.objects.select_related("test").all():
        if item.parameters_snapshot:
            continue
        test = item.test
        rows = LabParameter.objects.filter(test=test, is_active=True).order_by(
            "display_order", "id")
        item.test_name = test.name
        item.test_category = test.category
        item.specimen_type = test.specimen_type
        item.container = test.container
        item.unit_price = test.price
        item.parameters_snapshot = [
            {
                "id": p.id, "code": p.code, "name": p.name, "group": p.group,
                "result_type": p.result_type, "unit": p.unit,
                "reference_range": p.reference_range,
                "ref_low": str(p.ref_low) if p.ref_low is not None else None,
                "ref_high": str(p.ref_high) if p.ref_high is not None else None,
                "normal_value": p.normal_value,
                "options": TYPE_OPTIONS.get(p.result_type) or list(p.options or []),
                "display_order": p.display_order, "is_required": p.is_required,
            }
            for p in rows
        ]
        item.save()

    LabOrderTest.objects.filter(status="in_progress").update(status="draft")
    LabOrderTest.objects.filter(status="resulted").update(status="submitted")
    # Anything on an order that was already released is released.
    for item in LabOrderTest.objects.filter(status="submitted",
                                            order__verified_at__isnull=False).select_related("order"):
        item.status = "verified"
        item.verified_by_id = item.order.verified_by_id
        item.verified_at = item.order.verified_at
        item.save(update_fields=["status", "verified_by", "verified_at"])


def unbackfill(apps, schema_editor):
    LabOrderTest = apps.get_model("laboratory", "LabOrderTest")
    LabOrderTest.objects.filter(status="draft").update(status="in_progress")
    LabOrderTest.objects.filter(status__in=["submitted", "verified"]).update(status="resulted")


class Migration(migrations.Migration):
    dependencies = [
        ("laboratory", "0003_labordertest_charge_labordertest_container_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
