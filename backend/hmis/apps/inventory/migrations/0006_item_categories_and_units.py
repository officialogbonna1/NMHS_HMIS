"""
Product categories and units of measure become configuration rows.

They were free text on the product, typed again for every item — which is
how one hospital ends up with "Analgesic", "analgesics" and "Pain relief" as
three different groups, none of which can be renamed, retired or reported
on. They are now `ItemCategory` and `UnitOfMeasure` rows that both the HMIS
administration screens and Django admin edit.

Written as one migration with the backfill in the middle, because the three
steps only make sense together: rename the old text columns out of the way,
add the relations, build a row for every distinct value that was in use,
point the products at them, and drop the text.

Nothing is lost and nothing is guessed: every distinct string that existed
becomes a row of the same name, so a product that said "tablet" still says
"tablet" afterwards.
"""
from django.db import migrations, models
import django.db.models.deletion


def build_rows_from_text(apps, schema_editor):
    Item = apps.get_model("inventory", "Item")
    ItemCategory = apps.get_model("inventory", "ItemCategory")
    UnitOfMeasure = apps.get_model("inventory", "UnitOfMeasure")

    categories = {}
    for name in (Item.objects.exclude(legacy_category="")
                 .values_list("legacy_category", flat=True).distinct()):
        clean = (name or "").strip()
        if not clean:
            continue
        categories[clean] = ItemCategory.objects.get_or_create(name=clean)[0]

    units = {}
    for name in Item.objects.values_list("legacy_unit", flat=True).distinct():
        clean = (name or "").strip()
        if not clean:
            continue
        # The old default was the word "unit"; it stays a real row so that
        # products carrying it keep printing what they always printed.
        units[clean] = UnitOfMeasure.objects.get_or_create(name=clean)[0]

    for item in Item.objects.all():
        category = categories.get((item.legacy_category or "").strip())
        unit = units.get((item.legacy_unit or "").strip())
        Item.objects.filter(pk=item.pk).update(category=category, unit=unit)


def put_the_text_back(apps, schema_editor):
    """Reverse: copy the names back into the text columns before they return."""
    Item = apps.get_model("inventory", "Item")
    for item in Item.objects.select_related("category", "unit"):
        Item.objects.filter(pk=item.pk).update(
            legacy_category=item.category.name if item.category_id else "",
            legacy_unit=item.unit.name if item.unit_id else "unit",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0005_drop_batch_quantity"),
    ]

    operations = [
        migrations.CreateModel(
            name="ItemCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("display_order", models.PositiveIntegerField(default=100)),
            ],
            options={"ordering": ["display_order", "name"],
                     "verbose_name_plural": "item categories"},
        ),
        migrations.CreateModel(
            name="UnitOfMeasure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=60, unique=True)),
                ("abbreviation", models.CharField(blank=True, max_length=16)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("display_order", models.PositiveIntegerField(default=100)),
            ],
            options={"ordering": ["display_order", "name"],
                     "verbose_name": "unit of measure",
                     "verbose_name_plural": "units of measure"},
        ),
        migrations.RenameField(model_name="item", old_name="category",
                               new_name="legacy_category"),
        migrations.RenameField(model_name="item", old_name="unit", new_name="legacy_unit"),
        migrations.AddField(
            model_name="item",
            name="category",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name="items", to="inventory.itemcategory"),
        ),
        migrations.AddField(
            model_name="item",
            name="unit",
            field=models.ForeignKey(blank=True, null=True,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name="items", to="inventory.unitofmeasure"),
        ),
        migrations.AddField(
            model_name="item",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text="An inactive product stays on every record that references it, "
                          "but cannot be received, transferred or newly prescribed."),
        ),
        migrations.RunPython(build_rows_from_text, put_the_text_back),
        migrations.RemoveField(model_name="item", name="legacy_category"),
        migrations.RemoveField(model_name="item", name="legacy_unit"),
    ]
