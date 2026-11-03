# Registry for external source database connections; contains no passwords.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0014_salesoperationalreconciliation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="ExternalDatabaseConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("alias", models.SlugField(max_length=48, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("engine", models.CharField(default="postgresql", max_length=20)),
                ("purpose", models.CharField(choices=[("sales", "Histórico de ventas"),
                    ("reporting", "Consulta y reportes"), ("reference", "Catálogos de referencia")],
                    default="sales", max_length=20)),
                ("host", models.CharField(max_length=253)),
                ("port", models.PositiveIntegerField(default=5432)),
                ("database", models.CharField(max_length=128)),
                ("username", models.CharField(max_length=128)),
                ("password_env", models.CharField(max_length=100)),
                ("sslmode", models.CharField(default="require", max_length=20)),
                ("enabled", models.BooleanField(default=False)),
                ("last_test_ok", models.BooleanField(blank=True, default=None, null=True)),
                ("last_tested_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                    related_name="external_database_connections", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name", "alias"], "constraints": [
                models.CheckConstraint(condition=models.Q(("port__gte", 1), ("port__lte", 65535)),
                                       name="external_db_valid_port"),
            ]},
        ),
    ]
