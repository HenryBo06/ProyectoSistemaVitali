"""Preserve the original GTQ quote and its manual FX reference; USD stays canonical."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0017_etlloadrun")]

    operations = [
        migrations.AddField(
            model_name="pricetariff", name="pricing_currency",
            field=models.CharField(max_length=3, default="USD", choices=[
                ("USD", "Dólar estadounidense"), ("GTQ", "Quetzal guatemalteco")]),
        ),
        migrations.AddField(
            model_name="pricetariff", name="quoted_price",
            field=models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricetariff", name="fx_rate",
            field=models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="pricetariff", name="fx_date",
            field=models.DateField(null=True, blank=True),
        ),
    ]
