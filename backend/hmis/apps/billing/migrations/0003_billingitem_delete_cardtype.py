from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0002_cardtype'),
    ]

    operations = [
        migrations.RenameModel(old_name='CardType', new_name='BillingItem'),
        migrations.AddField(
            model_name='billingitem',
            name='category',
            field=models.CharField(
                choices=[('consultation', 'Consultation Fee'), ('card', 'Card')],
                default='card',
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name='billingitem',
            options={'ordering': ['category', 'name']},
        ),
    ]
