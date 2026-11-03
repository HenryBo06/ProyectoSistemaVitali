# Immutable mapping profiles and metadata-only ETL inspection audit.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0015_externaldatabaseconnection"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="EtlMappingProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("alias", models.SlugField(max_length=48)),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=100)),
                ("mapping", models.JSONField(default=dict)),
                ("date_format", models.CharField(default="%Y-%m-%d", max_length=20)),
                ("decimal_separator", models.CharField(default=".", max_length=1)),
                ("sheet_name", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="etl_mapping_profiles", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["alias", "-version"],
                "constraints": [models.UniqueConstraint(fields=("alias", "version"),
                                                        name="etl_profile_version_once")]},
        ),
        migrations.CreateModel(
            name="EtlInspectionRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("filename", models.CharField(max_length=255)),
                ("source_sha256", models.CharField(max_length=64)),
                ("schema_sha256", models.CharField(max_length=64)),
                ("source_format", models.CharField(max_length=8)),
                ("row_count", models.PositiveIntegerField()),
                ("valid_rows", models.PositiveIntegerField()),
                ("invalid_rows", models.PositiveIntegerField()),
                ("issue_count", models.PositiveIntegerField()),
                ("schema_changed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="etl_inspections", to=settings.AUTH_USER_MODEL)),
                ("profile", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="inspections", to="operations.etlmappingprofile")),
            ],
            options={"ordering": ["-created_at"],
                "indexes": [models.Index(fields=["profile", "-created_at"], name="etl_profile_created_idx")]},
        ),
    ]
