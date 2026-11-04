# Immutable audit trail for approved ETL snapshot loads.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0016_etlmappingprofile_etlinspectionrun"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="EtlLoadRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                            serialize=False, verbose_name="ID")),
                ("source_sha256", models.CharField(max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dataset", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="etl_load", to="operations.salesdataset")),
                ("inspection", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="approved_load", to="operations.etlinspectionrun")),
                ("loaded_by", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="etl_loads", to=settings.AUTH_USER_MODEL)),
                ("profile", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="approved_loads", to="operations.etlmappingprofile")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
