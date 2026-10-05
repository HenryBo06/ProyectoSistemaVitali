"""One-time read-only import of legacy local accounts and client ownership."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from operations.hashers import encode_legacy_hash
from operations.models import Assignment


class Command(BaseCommand):
    help = "Import legacy local users and assignments without changing the old database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path", type=Path, default=settings.BASE_DIR / ".local" / "smartorder.sqlite3",
            help="Old Streamlit SQLite file (read only).",
        )

    def handle(self, *args, **options):
        source = options["path"].expanduser().resolve()
        if not source.is_file():
            self.stdout.write("No hay cuentas locales anteriores que importar.")
            return
        User = get_user_model()
        if User.objects.exists():
            self.stdout.write("Ya hay cuentas en Django; no se importó otra vez.")
            return
        try:
            db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
            db.row_factory = sqlite3.Row
            with db:
                existing = {
                    row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                if not {"users", "assignments"}.issubset(existing):
                    raise CommandError("El archivo anterior no tiene las tablas de cuentas esperadas.")
                for table in ("datasets", "operational_inputs", "decisions", "reviews"):
                    if table in existing and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                        raise CommandError(
                            "El archivo anterior contiene ventas, inventario o pedidos. "
                            "Conserve su copia; esta importación automática solo admite cuentas y clientes."
                        )
                old_users = list(db.execute(
                    "SELECT id,username,salt,password_hash,role,active FROM users ORDER BY id"
                ))
                old_assignments = list(db.execute(
                    "SELECT user_id,client FROM assignments ORDER BY user_id,client"
                ))
        except sqlite3.DatabaseError as exc:
            raise CommandError("No se pudo leer la base local anterior.") from exc
        finally:
            if "db" in locals():
                db.close()
        if not old_users:
            self.stdout.write("La base anterior no tiene cuentas que importar.")
            return
        if len({row["client"] for row in old_assignments}) != len(old_assignments):
            raise CommandError("Un cliente tiene varios vendedores en la base anterior; resuelva esa asignación manualmente.")
        if any(row["role"] not in {"admin", "vendedor"} for row in old_users):
            raise CommandError("Hay un rol anterior que no se puede importar.")
        try:
            with transaction.atomic():
                mapping = {}
                for row in old_users:
                    user = User.objects.create(
                        username=row["username"],
                        password=encode_legacy_hash(row["salt"], row["password_hash"]),
                        is_staff=row["role"] == "admin",
                        is_active=bool(row["active"]),
                    )
                    mapping[row["id"]] = user
                Assignment.objects.bulk_create([
                    Assignment(user=mapping[row["user_id"]], client=row["client"])
                    for row in old_assignments
                ])
        except (IntegrityError, KeyError) as exc:
            raise CommandError("Las cuentas anteriores no se pudieron importar; la base nueva quedó sin cambios.") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Se importaron {len(old_users)} cuentas y {len(old_assignments)} clientes. "
            "Las contraseñas anteriores siguen funcionando."
        ))
