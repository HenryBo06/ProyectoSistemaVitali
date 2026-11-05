"""Repeatable, non-destructive local seller portfolio seeder."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from operations.demo_catalog import seed_demo_seller_portfolio
from operations.services import active_sales


class Command(BaseCommand):
    help = (
        "Assign unowned clients from the known synthetic LAB XLSX to an existing "
        "seller without changing accounts, passwords or other seller portfolios."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="Existing active seller username.")
        parser.add_argument("--actor", required=True, help="Existing active admin username for audit.")
        parser.add_argument("--dry-run", action="store_true", help="Preview without writing any rows.")

    def handle(self, *args, **options):
        User = get_user_model()
        seller = User.objects.filter(username=options["username"]).first()
        actor = User.objects.filter(username=options["actor"]).first()
        try:
            result = seed_demo_seller_portfolio(
                active_sales(), seller, actor, dry_run=options["dry_run"]
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        prefix = "Previsualización" if options["dry_run"] else "Cartera LAB preparada"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}: {result['seller']} | "
            f"{result['new']} clientes {'disponibles' if options['dry_run'] else 'añadidos'}, "
            f"{result['already_owned']} conservados, "
            f"{result['owned_by_others']} de otros vendedores | "
            f"{result['source_clients']} clientes en histórico activo."
        ))
