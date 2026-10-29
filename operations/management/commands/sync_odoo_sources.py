"""One shared sync command, optionally repeated for the local laboratory."""
import time
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from operations.source_sync import synchronize_sources


class Command(BaseCommand):
    help = "Sincroniza inventario y avance Odoo; --loop repite cada cinco minutos."

    def add_arguments(self, parser):
        parser.add_argument("--admin", required=True)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--inventory-only", action="store_true")

    def handle(self, *args, **options):
        while True:
            user = get_user_model().objects.filter(username=options["admin"], is_active=True, is_staff=True).first()
            if user is None:
                raise CommandError("Indique un administrador activo de esta instancia.")
            try:
                run = synchronize_sources(user, refresh_orders=not options["inventory_only"])
                self.stdout.write(f"Lectura {run.pk}: {run.status}; filas={run.rows}; rechazadas={run.rejected}")
                if run.status != "success" and not options["loop"]:
                    raise CommandError(run.error)
            except ValueError as exc:
                if not options["loop"]:
                    raise CommandError(str(exc)) from exc
                self.stderr.write(str(exc))
            finally:
                close_old_connections()
            if not options["loop"]:
                break
            # ponytail: one process per installation, no queue; scheduler can invoke single runs.
            time.sleep(300)
