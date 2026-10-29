from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
import pandas as pd

from .commercial import salvadoran_event_candidates
from .models import (
    Assignment, ClientStockCount, ClientStockMovement, CommercialEvent, DatedFlow,
    LocationIncoming, LocationStockSnapshot, OdooCustomerMapping, OdooOrderLink, OrderLine,
    OrderRequest, PriceTariff, RestockTarget, SalesDataset, SalesLine, UserNotification,
)
from .services import (
    _line_duplicate_candidates, _operational_sales, import_sales,
    operational_overlap_pending, operational_overlap_rows,
    recommendation_cards, review_operational_sale, sale_forecast_rows,
    sales_data_with_confirmed_orders, vitali_restock_plan,
)
from .streamlit_bridge import screen_context, sign_in
from .tests import LocalCase, sales_book
from smartorder.data import SalesData


def sales_book_with_units(first: date, days: int) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Ventas"
    sheet.append(("Fecha", "Cliente", "Producto", "Cantidad", "Precio", "Monto", "Unidad"))
    for offset in range(days):
        day = first + timedelta(days=offset)
        sheet.append((day, "Cliente A", "Alitas", 1, 12.5, 12.5, "kg"))
        sheet.append((day, "Cliente B", "Alitas", 2, 7.25, 14.5, "lb"))
    output = BytesIO()
    book.save(output)
    return output.getvalue()


class PlanningInboxTests(LocalCase, TestCase):
    def setUp(self):
        super().setUp()
        start = timezone.localdate() - timedelta(days=40)
        self.dataset = import_sales(
            sales_book(start, 30), "ventas_contextuales.xlsx", self.admin, True, True,
        )
        Assignment.objects.create(user=self.seller, client="Cliente A")

    def _order(self, *, quantity="10", required_date=None, committed_date=None,
               status=OrderRequest.Status.PENDING, exception_reason=""):
        required_date = required_date or timezone.localdate() + timedelta(days=2)
        order = OrderRequest.objects.create(
            seller=self.seller, client="Cliente A", dataset=self.dataset,
            required_date=required_date, committed_date=committed_date,
            sale_confirmed=True, status=status, exception_reason=exception_reason,
            sale_confirmed_at=timezone.now(),
        )
        line = OrderLine.objects.create(
            order=order, product="Alitas", unit="kg", requested_qty=quantity,
            unit_price="12.5000",
        )
        return order, line

    def test_vitali_plan_ignores_series_first_seen_after_closed_cutoff(self):
        first = date(2024, 1, 1)
        today = first + timedelta(days=364)
        cutoff = today - timedelta(days=1)
        coverage = {("A", "X", "kg"): ((first, cutoff),)}
        records = [
            {"Fecha": pd.Timestamp(first + timedelta(days=offset)), "Cliente": "A",
             "Producto": "X", "Unidad": "kg", "Cantidad_kg_unid": float(1 + (offset // 7) % 4)}
            for offset in range(365)
        ]
        weekly = pd.DataFrame([{
            "Cliente": "A", "Producto": "X", "eligible_for_auto": True, "forecast": 7.246,
        }])
        stock = SimpleNamespace(
            location_id=1, product="X", unit="kg", observed_on=today,
            available=Decimal("0"), location_name="Bodega",
        )
        goal = SimpleNamespace(
            owner_key="location:1", product="X", unit="kg", target_quantity=Decimal("0"),
        )
        class EmptyFlows:
            def filter(self, *args, **kwargs):
                return self

            def __iter__(self):
                return iter(())

        empty_flows = EmptyFlows()

        def plan(extra):
            rows = pd.DataFrame(records + extra)
            source = SalesData(
                rows, "future-cutoff", (), 0, date_semantics="delivery", provenance="real",
                coverage=coverage,
            )

            class Lines:
                def values_list(self, *fields):
                    columns = {"client": "Cliente", "product": "Producto", "unit": "Unidad"}
                    values = rows[[columns[field] for field in fields]].drop_duplicates()
                    query = MagicMock()
                    query.distinct.return_value = list(values.itertuples(index=False, name=None))
                    return query

            dataset = SimpleNamespace(
                pk=1, digest="future-cutoff", file=SimpleNamespace(path="unused"),
                verified=True, full_snapshot_confirmed=True, date_semantics="delivery",
                lines=Lines(),
            )
            with patch("operations.services.OdooCustomerMapping.objects.exclude") as mapping, \
                    patch("operations.services.active_inventory", return_value=None), \
                    patch("operations.services.sales_data_for", return_value=source), \
                    patch("operations.services.operational_overlap_pending", return_value=False), \
                    patch("operations.services._confirmed_order_lines", return_value=[]), \
                    patch("operations.services._dated_flow_commitments", return_value=({}, {}, set())), \
                    patch("operations.services.DatedFlow.objects.filter", return_value=empty_flows), \
                    patch("operations.services.LocationStockSnapshot.objects.order_by", return_value=[stock]), \
                    patch("operations.services.RestockTarget.objects.filter", return_value=[goal]), \
                    patch("operations.services._dated_flow_incoming", return_value=({}, {}, set(), {})), \
                    patch("operations.services.LocationIncoming.objects.filter", return_value=[]), \
                    patch("smartorder.recommendations.RecommendationEngine.for_delivery_week",
                          return_value=weekly):
                mapping.return_value.values_list.return_value = [("A", 1), ("FUTURO", 1)]
                result = vitali_restock_plan(dataset, today)
            return next(row for row in result if row["location_id"] == 1 and row["product"] == "X")

        base = plan([])
        future_client = plan([{
            "Fecha": pd.Timestamp(today + timedelta(days=2)), "Cliente": "FUTURO",
            "Producto": "X", "Unidad": "kg", "Cantidad_kg_unid": 100.0,
        }])
        future_unit = plan([{
            "Fecha": pd.Timestamp(today + timedelta(days=2)), "Cliente": "A",
            "Producto": "X", "Unidad": "lb", "Cantidad_kg_unid": 100.0,
        }])

        for changed in (future_client, future_unit):
            self.assertEqual(
                {key: changed[key] for key in ("quantity", "state", "demand", "unit_confirmed")},
                {key: base[key] for key in ("quantity", "state", "demand", "unit_confirmed")},
            )
        self.assertEqual(base["quantity"], Decimal("7.246"))
        self.assertEqual(base["state"], "plan")

    def test_operational_confirmed_sales_refresh_cached_series_and_exclude_invalid_rows(self):
        today = timezone.localdate()
        delivery = today + timedelta(days=1)
        before = sale_forecast_rows(
            self.dataset.digest, self.dataset.file.path, delivery, True, today,
            dataset_id=self.dataset.pk, dataset_last_date=self.dataset.last_date,
        )
        self.assertEqual(before.iloc[0]["source_end"], self.dataset.last_date)

        realized = today - timedelta(days=3)
        historical_order, _ = self._order(quantity="9", required_date=realized)
        historical_order.sale_confirmed_at = timezone.make_aware(datetime.combine(realized, time(12, 0)))
        historical_order.save(update_fields=["sale_confirmed_at"])
        self._order(quantity="90", required_date=today - timedelta(days=2),
                    status=OrderRequest.Status.CANCELLED)
        self._order(quantity="80", required_date=today - timedelta(days=1),
                    exception_reason="pendiente de revisión")
        self._order(quantity="70", required_date=today - timedelta(days=2))
        mismatched = OrderLine.objects.filter(requested_qty=Decimal("70")).first()
        mismatched.unit = "lb"
        mismatched.save(update_fields=["unit"])

        after = sale_forecast_rows(
            self.dataset.digest, self.dataset.file.path, delivery, True, today,
            dataset_id=self.dataset.pk, dataset_last_date=self.dataset.last_date,
        )
        alitas = after[(after["Cliente"] == "Cliente A") & (after["Producto"] == "Alitas")
                       & (after["Unidad"].str.casefold() == "kg")].iloc[0]
        self.assertEqual(alitas["source_end"], realized)
        self.assertEqual(alitas["reference_date"], realized)
        self.assertEqual(alitas["reference_qty"], 9.0)

    def test_overlapping_operational_sales_require_line_reconciliation_and_are_counted_once(self):
        overlap_day = self.dataset.first_date + timedelta(days=1)
        candidate = SalesLine.objects.create(
            dataset=self.dataset, date=overlap_day, provenance=SalesLine.Provenance.REAL,
            unit="kg", client="Cliente A", product="Alitas", category="Pollo",
            quantity="9", unit_price="12.5000", amount="112.50",
        )
        duplicate_order, duplicate_line = self._order(quantity="9", required_date=overlap_day)
        duplicate_order.sale_confirmed_at = timezone.make_aware(
            datetime.combine(overlap_day, time(12, 0)),
        )
        duplicate_order.save(update_fields=["sale_confirmed_at"])

        self.assertTrue(operational_overlap_pending(self.dataset))
        self.assertEqual(len(operational_overlap_rows(self.dataset)), 1)
        pending = sales_data_with_confirmed_orders(
            self.dataset.digest, self.dataset.file.path, self.dataset.pk,
            self.dataset.last_date, overlap_day, "pending-overlap",
        )
        self.assertNotIn("Origen_operativo", pending.rows.columns)

        review_operational_sale(
            self.dataset, self.admin, order_line_id=duplicate_line.pk,
            decision="duplicate", imported_line_id=candidate.pk,
        )
        self.assertFalse(operational_overlap_pending(self.dataset))
        deduplicated = sales_data_with_confirmed_orders(
            self.dataset.digest, self.dataset.file.path, self.dataset.pk,
            self.dataset.last_date, overlap_day, "duplicate-overlap",
        )
        self.assertNotIn("Origen_operativo", deduplicated.rows.columns)

        independent_order, independent_line = self._order(quantity="3", required_date=overlap_day)
        independent_order.sale_confirmed_at = duplicate_order.sale_confirmed_at
        independent_order.save(update_fields=["sale_confirmed_at"])
        review_operational_sale(
            self.dataset, self.admin, order_line_id=independent_line.pk,
            decision="independent",
        )
        independent = sales_data_with_confirmed_orders(
            self.dataset.digest, self.dataset.file.path, self.dataset.pk,
            self.dataset.last_date, overlap_day, "independent-overlap",
        )
        operational = independent.rows[independent.rows["Origen_operativo"] == "operational"]
        self.assertEqual(len(operational), 1)
        self.assertEqual(operational.iloc[0]["Cantidad_kg_unid"], 3.0)
        self.assertEqual(
            len(sales_data_with_confirmed_orders(
                self.dataset.digest, self.dataset.file.path, self.dataset.pk,
                self.dataset.last_date, overlap_day, "independent-overlap",
            ).rows.query("Origen_operativo == 'operational'")),
            1,
        )

    def test_client_restock_uses_today_counts_confirmed_movements_and_committed_receipts(self):
        today = timezone.localdate()
        delivery = today + timedelta(days=1)
        for location, amount in (("Bodega A", "10"), ("Bodega B", "5")):
            ClientStockCount.objects.create(
                client="Cliente A", product="Alitas", location=location, unit="kg",
                available=amount, observed_on=today, reported_by=self.seller,
            )
        target = RestockTarget.objects.create(
            scope=RestockTarget.Scope.CLIENT, owner_key="client:Cliente A",
            product="Alitas", unit="kg", target_quantity="40", updated_by=self.admin,
        )
        ClientStockMovement.objects.create(
            client="Cliente A", product="Alitas", location="Bodega A",
            kind=ClientStockMovement.Kind.INCOMING, unit="kg", quantity="5",
            effective_date=delivery, confirmed=True,
            reconciliation=DatedFlow.Reconciliation.UNIQUE, reported_by=self.seller,
        )
        ClientStockMovement.objects.create(
            client="Cliente A", product="Alitas", location="Bodega B",
            kind=ClientStockMovement.Kind.OUTGOING, unit="kg", quantity="3",
            effective_date=delivery, confirmed=True,
            reconciliation=DatedFlow.Reconciliation.UNIQUE, reported_by=self.seller,
        )
        self._order(quantity="2", required_date=delivery, committed_date=delivery)
        DatedFlow.objects.create(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, client="Cliente A", product="Alitas",
            unit="kg", quantity="10", due_date=delivery, confirmed=True,
            date_semantics=DatedFlow.DateSemantics.COMMITTED,
            reconciliation=DatedFlow.Reconciliation.UNIQUE, created_by=self.admin,
        )

        card = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertEqual(card["client_stock"], Decimal("15.000"))
        self.assertEqual(card["client_stock_incoming"], Decimal("17.000"))
        self.assertEqual(card["client_stock_outgoing"], Decimal("3.000"))
        self.assertEqual(card["client_stock_projected"], Decimal("29.000"))
        self.assertEqual(card["suggested"], Decimal("11.000"))

        ClientStockMovement.objects.create(
            client="Cliente A", product="Alitas", location="Bodega A",
            kind=ClientStockMovement.Kind.OUTGOING, unit="lb", quantity="1",
            effective_date=delivery, confirmed=True, reported_by=self.seller,
        )
        manual = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertIsNone(manual["suggested"])
        self.assertEqual(manual["state"], "manual")

        ClientStockMovement.objects.filter(unit="lb").delete()
        ClientStockCount.objects.filter(client="Cliente A", product="Alitas").update(
            observed_on=today - timedelta(days=1)
        )
        stale = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertIsNone(stale["suggested"])
        self.assertEqual(target.product, "Alitas")

    def test_requested_sales_and_imports_are_not_client_receipts(self):
        today = timezone.localdate()
        delivery = today + timedelta(days=1)
        ClientStockCount.objects.create(
            client="Cliente A", product="Alitas", location="Bodega A", unit="kg",
            available="0", observed_on=today, reported_by=self.seller,
        )
        RestockTarget.objects.create(
            scope=RestockTarget.Scope.CLIENT, owner_key="client:Cliente A",
            product="Alitas", unit="kg", target_quantity="20", updated_by=self.admin,
        )
        self._order(quantity="10", required_date=delivery)
        DatedFlow.objects.create(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, client="Cliente A", product="Alitas",
            unit="kg", quantity="8", due_date=delivery, confirmed=True,
            date_semantics=DatedFlow.DateSemantics.REQUESTED,
            reconciliation=DatedFlow.Reconciliation.UNIQUE, created_by=self.admin,
        )

        card = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertEqual(card["client_stock_incoming"], Decimal("0.000"))
        self.assertEqual(card["suggested"], Decimal("20.000"))
        self.assertEqual(card["state"], "suggested")

    def test_resolved_duplicate_customer_movement_counts_the_order_once(self):
        today = timezone.localdate()
        delivery = today + timedelta(days=1)
        ClientStockCount.objects.create(
            client="Cliente A", product="Alitas", location="Bodega A", unit="kg",
            available="0", observed_on=today, reported_by=self.seller,
        )
        RestockTarget.objects.create(
            scope=RestockTarget.Scope.CLIENT, owner_key="client:Cliente A",
            product="Alitas", unit="kg", target_quantity="20", updated_by=self.admin,
        )
        order, line = self._order(
            quantity="10", required_date=delivery, committed_date=delivery,
        )
        movement = ClientStockMovement.objects.create(
            client="Cliente A", product="Alitas", location="Bodega A",
            kind=ClientStockMovement.Kind.INCOMING, unit="kg", quantity="10",
            effective_date=delivery, confirmed=True,
            reconciliation=DatedFlow.Reconciliation.DUPLICATE,
            related_order_line=line, reported_by=self.seller,
        )

        card = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertEqual(card["client_stock_incoming"], Decimal("10.000"))
        self.assertEqual(card["suggested"], Decimal("10.000"))
        self.assertEqual(card["state"], "suggested")

        order.committed_date = delivery + timedelta(days=1)
        order.save(update_fields=["committed_date", "updated_at"])
        movement.refresh_from_db()
        stale = next(row for row in recommendation_cards(
            self.dataset, delivery, "Cliente A", today=today, mode="client_restock",
        ) if row["product"] == "Alitas")
        self.assertIsNone(stale["suggested"])
        self.assertEqual(stale["state"], "manual")

    @override_settings(TIME_ZONE="America/El_Salvador", USE_TZ=True)
    def test_evening_confirmations_use_the_local_business_date(self):
        today = timezone.localdate()
        for index, local_time in enumerate((time(18, 0), time(19, 0), time(23, 59)), start=1):
            quantity = Decimal(index + 2)
            candidate = SalesLine.objects.create(
                dataset=self.dataset, date=today, provenance=SalesLine.Provenance.REAL,
                unit="kg", client="Cliente A", product="Alitas", category="Pollo",
                quantity=quantity, unit_price="12.5000", amount=quantity * Decimal("12.5"),
            )
            order, line = self._order(quantity=str(quantity), required_date=today)
            order.sale_confirmed_at = timezone.make_aware(datetime.combine(today, local_time))
            order.save(update_fields=["sale_confirmed_at"])
            self.assertEqual(
                _operational_sales(self.dataset.pk, today - timedelta(days=1), today)[0][3],
                today.isoformat(),
            )
            self.assertIn(candidate, _line_duplicate_candidates(self.dataset, order, line))

    def test_vitali_plan_deduplicates_commitments_and_uses_one_latest_physical_count(self):
        today = timezone.localdate()
        due = today + timedelta(days=2)
        OdooCustomerMapping.objects.create(client="Cliente A", code="CLI-A", location_id=41)
        RestockTarget.objects.create(
            scope=RestockTarget.Scope.LOCATION, owner_key="location:41",
            product="Alitas", unit="kg", target_quantity="5", updated_by=self.admin,
        )
        LocationStockSnapshot.objects.create(
            location_id=41, location_name="Central", product="Alitas", unit="kg",
            available="100", observed_on=today, reported_by=self.admin,
        )
        LocationStockSnapshot.objects.create(
            location_id=41, location_name="Central", product="Alitas", unit="kg",
            available="3", observed_on=today, reported_by=self.admin,
        )
        LocationIncoming.objects.create(
            location_id=41, location_name="Central", product="Alitas", unit="kg",
            quantity="1", due_date=due, confirmed=True, recorded_by=self.admin,
        )
        self._order(quantity="10", required_date=due)
        DatedFlow.objects.create(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, client="Cliente A", product="Alitas",
            unit="kg", quantity="7", due_date=due, confirmed=True,
            reconciliation=DatedFlow.Reconciliation.UNIQUE, created_by=self.admin,
        )

        row = next(item for item in vitali_restock_plan(self.dataset, today)
                   if item["location_id"] == 41 and item["product"] == "Alitas")
        self.assertEqual(row["demand"], Decimal("17.000"))
        self.assertEqual(row["current"], Decimal("3.000"))
        self.assertEqual(row["incoming"], Decimal("1.000"))
        self.assertEqual(row["need"], Decimal("18.000"))
        self.assertIsNone(row["suggested"])
        self.assertEqual(OrderLine.objects.get().status, OrderLine.Status.PENDING)

        LocationIncoming.objects.create(
            location_id=41, location_name="Central", product="Alitas", unit="lb",
            quantity="4", due_date=due, confirmed=True, recorded_by=self.admin,
        )
        incompatible = next(item for item in vitali_restock_plan(self.dataset, today)
                            if item["location_id"] == 41 and item["product"] == "Alitas")
        self.assertIsNone(incompatible["need"])
        self.assertIsNone(incompatible["suggested"])

    def test_vitali_shared_receipt_is_counted_once_by_physical_location(self):
        today = timezone.localdate()
        due = today + timedelta(days=2)
        OdooCustomerMapping.objects.create(client="Cliente A", code="CLI-A", location_id=41)
        OdooCustomerMapping.objects.create(client="Cliente B", code="CLI-B", location_id=41)
        RestockTarget.objects.create(
            scope=RestockTarget.Scope.LOCATION, owner_key="location:41",
            product="Alitas", unit="kg", target_quantity="30", updated_by=self.admin,
        )
        LocationStockSnapshot.objects.create(
            location_id=41, location_name="Central", product="Alitas", unit="kg",
            available="0", observed_on=today, reported_by=self.admin,
        )
        self._order(quantity="10", required_date=due, committed_date=due)
        for client, reconciliation in (
                ("Cliente A", DatedFlow.Reconciliation.UNIQUE),
                ("Cliente B", DatedFlow.Reconciliation.DUPLICATE)):
            DatedFlow.objects.create(
                kind=DatedFlow.Kind.INCOMING, client=client, product="Alitas",
                unit="kg", quantity="10", due_date=due, confirmed=True,
                source_reference="RECEIPT-1", reconciliation=reconciliation,
                created_by=self.admin,
            )

        row = next(item for item in vitali_restock_plan(self.dataset, today)
                   if item["location_id"] == 41 and item["product"] == "Alitas")
        self.assertEqual(row["demand"], Decimal("10.000"))
        self.assertEqual(row["incoming"], Decimal("10.000"))
        self.assertEqual(row["need"], Decimal("30.000"))

        DatedFlow.objects.create(
            kind=DatedFlow.Kind.INCOMING, client="Cliente B", product="Alitas",
            unit="kg", quantity="12", due_date=due, confirmed=True,
            source_reference="RECEIPT-1", reconciliation=DatedFlow.Reconciliation.UNIQUE,
            created_by=self.admin,
        )
        conflict = next(item for item in vitali_restock_plan(self.dataset, today)
                        if item["location_id"] == 41 and item["product"] == "Alitas")
        self.assertEqual(conflict["incoming"], Decimal("0"))
        self.assertEqual(conflict["need"], Decimal("40.000"))
        self.assertIsNone(conflict["suggested"])

    def test_event_calendar_seeds_reviewable_2026_dates_and_inbox_deduplicates(self):
        today = timezone.localdate()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("planning"), {"action": "seed_calendar", "year": 2026})
        self.assertEqual(response.status_code, 302)
        easter = CommercialEvent.objects.get(
            key="semana-santa", start_date=date(2026, 4, 2), territory="Nacional",
        )
        self.assertEqual(easter.end_date, date(2026, 4, 4))
        self.assertFalse(easter.reviewed)
        self.assertTrue(easter.source)
        candidate = next(row for row in salvadoran_event_candidates(2026) if row[0] == "semana-santa")
        self.assertEqual(candidate[3:5], (date(2026, 4, 2), date(2026, 4, 4)))
        mother = CommercialEvent.objects.get(key="dia-de-la-madre", start_date=date(2026, 5, 10))
        father = CommercialEvent.objects.get(key="dia-del-padre", start_date=date(2026, 6, 17))
        self.assertTrue(mother.source)
        self.assertTrue(father.source)
        self.assertEqual(mother.territory, "Nacional")
        self.assertEqual(father.territory, "Nacional")
        self.assertFalse(mother.reviewed or father.reviewed)
        mother.source = ""
        mother.save(update_fields=["source"])
        self.client.post(reverse("planning"), {"action": "seed_calendar", "year": 2026})
        mother.refresh_from_db()
        self.assertTrue(mother.source)
        self.assertFalse(mother.reviewed)

        start = today + timedelta(days=7)
        event = CommercialEvent.objects.create(
            key="local-test", title="Celebración local", kind=CommercialEvent.Kind.LOCAL,
            start_date=start, end_date=start, territory="Nacional", reviewed=True,
            active=True, created_by=self.admin,
        )
        self.client.force_login(self.seller)
        with patch("operations.views.timezone.localdate", return_value=today):
            self.client.get(reverse("notification_inbox"))
            self.client.get(reverse("notification_inbox"))
        notice = UserNotification.objects.get(recipient=self.seller, client="Cliente A")
        self.assertEqual(notice.kind, UserNotification.Kind.EVENT)
        self.assertIn(reverse("order_create"), notice.target_url)
        self.assertEqual(UserNotification.objects.filter(recipient=self.seller).count(), 1)
        with patch("operations.views.timezone.localdate", return_value=event.start_date):
            self.client.get(reverse("notification_inbox"))
            self.client.get(reverse("notification_inbox"))
        self.assertEqual(UserNotification.objects.filter(recipient=self.seller).count(), 2)

    def test_inbox_rechecks_current_assignment_and_planning_is_admin_only(self):
        notice = UserNotification.objects.create(
            event_key="owned-notice", recipient=self.seller, kind=UserNotification.Kind.EVENT,
            title="Oportunidad", body="Cliente A", client="Cliente A",
            target_url=f"{reverse('order_create')}?client=Cliente%20A",
        )
        self.client.force_login(self.seller)
        self.assertContains(self.client.get(reverse("notification_inbox")), "Oportunidad")
        self.client.post(reverse("notification_inbox"), {
            "notification_id": notice.pk, "action": "read",
        })
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)
        self.client.post(reverse("notification_inbox"), {
            "notification_id": notice.pk, "action": "unread",
        })
        notice.refresh_from_db()
        self.assertIsNone(notice.read_at)

        other = self.seller.__class__.objects.create_user("otra_vendedora", password="OtraClaveFirme2026")
        Assignment.objects.filter(client="Cliente A").update(user=other)
        self.assertNotContains(self.client.get(reverse("notification_inbox")), "Cliente A")
        response = self.client.post(reverse("notification_inbox"), {
            "notification_id": notice.pk, "action": "open",
        })
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse("planning")).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("client_stock")).status_code, 403)

    def test_seller_dashboard_shows_assigned_attention_deliveries_and_event_opportunities(self):
        today = timezone.localdate()
        attention, _ = self._order(required_date=today + timedelta(days=2))
        upcoming, _ = self._order(
            required_date=today + timedelta(days=3), committed_date=today + timedelta(days=3),
            status=OrderRequest.Status.REVIEWED,
        )
        event = CommercialEvent.objects.create(
            key="dashboard-event", title="Evento del tablero", kind=CommercialEvent.Kind.COMMERCIAL,
            start_date=today, end_date=today, territory="Nacional", reviewed=True,
            active=True, created_by=self.admin,
        )
        overlapping = CommercialEvent.objects.create(
            key="dashboard-event-overlap", title="Celebración coincidente",
            kind=CommercialEvent.Kind.LOCAL, start_date=today, end_date=today,
            territory="Nacional", reviewed=True, active=True, created_by=self.admin,
        )
        upcoming_event = CommercialEvent.objects.create(
            key="dashboard-event-soon", title="Evento en seis días",
            kind=CommercialEvent.Kind.COMMERCIAL, start_date=today + timedelta(days=6),
            end_date=today + timedelta(days=6), territory="Nacional", reviewed=True,
            active=True, created_by=self.admin,
        )
        self.client.force_login(self.seller)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, attention.client)
        self.assertContains(response, upcoming.client)
        self.assertContains(response, event.title)
        context = response.context[0]
        opportunities = context["opportunities"]
        self.assertEqual({row["event_id"] for row in opportunities},
                         {event.pk, overlapping.pk, upcoming_event.pk})
        opportunity = opportunities[0]
        self.assertEqual(opportunity["client"], "Cliente A")
        self.assertTrue(opportunity["product"])
        self.assertIn("client=Cliente+A", opportunity["url"])
        self.assertIn("required_date=" + today.isoformat(), opportunity["url"])
        self.assertIn("event_id=", opportunity["url"])
        for order in [*context["attention_orders"], *context["upcoming_orders"]]:
            for field in ("dataset", "inventory_batch", "lines"):
                self.assertFalse(hasattr(order, field))
        bridge = screen_context(
            "dashboard", sign_in(self.seller.username, "ClaveDelVendedor123"),
        )
        for order in [*bridge["attention_orders"], *bridge["upcoming_orders"]]:
            for field in ("dataset", "inventory_batch", "lines"):
                self.assertFalse(hasattr(order, field))

    def test_django_order_context_switch_clears_incompatible_line_and_refreshes_tariff(self):
        today = timezone.localdate()
        dataset = import_sales(
            sales_book_with_units(today - timedelta(days=35), 30), "unidades.xlsx",
            self.admin, False, True, provenance=SalesDataset.Provenance.REAL,
        )
        Assignment.objects.create(user=self.seller, client="Cliente B")
        PriceTariff.objects.create(
            client="Cliente B", product="Alitas", unit="lb", unit_price="7.25",
            discount_percent=5, updated_by=self.admin,
        )
        self.client.force_login(self.seller)
        previous_date = today + timedelta(days=1)
        response = self.client.post(reverse("order_create"), {
            "action": "refresh", "client": "Cliente B",
            "required_date": previous_date.isoformat(), "suggestion_mode": "sale",
            "previous_client": "Cliente A", "previous_required_date": previous_date.isoformat(),
            "previous_mode": "sale", "event_id": "", "submission_token": "",
            "product": ["Alitas"], "quantity": ["4"], "unit": ["kg"],
            "reason": ["Cliente confirmó cuatro kg"], "unit_price": ["12.5000"],
            "discount_percent": ["10.00"], "selected_product": ["Alitas"],
            "payment_terms": "30 días", "note": "Conservar esta nota",
            "exception_reason": "",
        })
        self.assertEqual(response.status_code, 200)
        card = next(item for item in response.context["products"] if item["product"] == "Alitas")
        self.assertEqual(card["entered_quantity"], "")
        self.assertEqual(card["entered_reason"], "")
        self.assertEqual(card["entered_unit"], "lb")
        self.assertEqual(card["unit_price"], Decimal("7.2500"))
        self.assertEqual(card["discount_percent"], Decimal("5.00"))
        self.assertNotIn("entered_unit_price", card)
        self.assertIn("Alitas", response.context["incompatible_lines"])
        self.assertEqual(response.context["form_data"]["note"], "Conservar esta nota")

    def test_commitment_change_a_to_b_to_a_creates_distinct_unread_notices(self):
        today = timezone.localdate()
        order, _ = self._order()
        self.client.force_login(self.admin)
        url = reverse("order_commitment", args=[order.pk])
        first_day, second_day = today + timedelta(days=1), today + timedelta(days=2)
        self.assertEqual(self.client.post(url, {"committed_date": first_day.isoformat()}).status_code, 302)
        first_notice = UserNotification.objects.get(recipient=self.seller, order=order)
        first_notice.read_at = timezone.now()
        first_notice.save(update_fields=["read_at"])
        self.assertEqual(self.client.post(url, {"committed_date": second_day.isoformat()}).status_code, 302)
        self.assertEqual(self.client.post(url, {"committed_date": first_day.isoformat()}).status_code, 302)
        self.assertEqual(self.client.post(url, {"committed_date": first_day.isoformat()}).status_code, 302)

        notices = list(UserNotification.objects.filter(
            recipient=self.seller, order=order,
        ).order_by("pk"))
        self.assertEqual(len(notices), 3)
        self.assertEqual(len({notice.event_key for notice in notices}), 3)
        self.assertIsNotNone(notices[0].read_at)
        self.assertIsNone(notices[-1].read_at)

    @override_settings(SMARTORDER_MAINTENANCE_UI=False)
    def test_admin_evaluation_metrics_render_outside_maintenance_with_fraction_as_percent(self):
        card = {
            "product": "Alitas", "category": "Congelados", "unit": "kg",
            "suggested": Decimal("1"), "available": Decimal("5"), "stock_date": None,
            "inventory_warning": "", "explanation": "Serie de prueba.",
            "sales_usd_display": "$100.00", "days_with_sale": 8,
            "reference_qty": None, "reference_period": "", "incoming": Decimal("0"),
            "commitments_before": Decimal("0"), "commitments": Decimal("0"),
            "sale_evaluation": {
                "windows": 8, "targets": 8, "coverage": 1.0, "automatic": True,
                "mae": Decimal("0.125"), "baseline_mae": Decimal("0.250"),
                "overforecast": Decimal("0.000"), "baseline_overforecast": Decimal("0.125"),
                "event": "",
            },
        }
        self.client.force_login(self.admin)
        with patch("operations.views.recommendation_cards", return_value=[card]):
            response = self.client.get(reverse("recommendations"), {"client": "Cliente A"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Evaluación diaria de venta para este cliente y producto")
        self.assertContains(response, "cobertura 100 %")
        self.assertNotContains(response, "cobertura 1 %")

    def test_operational_progress_notices_follow_real_snapshot_changes(self):
        order, _ = self._order()
        link = OdooOrderLink.objects.create(order=order, snapshot={"state": "A"})
        snapshots = iter(({"state": "B"}, {"state": "A"}, {"state": "A"}))
        base = timezone.now()
        calls = 0

        def refresh_snapshot(_order, action):
            nonlocal calls
            self.assertEqual(action, "refresh")
            snapshot = next(snapshots)
            OdooOrderLink.objects.filter(pk=link.pk).update(
                snapshot=snapshot, updated_at=base + timedelta(seconds=calls + 1),
            )
            calls += 1

        self.client.force_login(self.admin)
        with patch("operations.views.odoo_service.synchronize", side_effect=refresh_snapshot):
            for _ in range(3):
                response = self.client.post(reverse("order_odoo", args=[order.pk]), {"action": "refresh"})
                self.assertEqual(response.status_code, 302)
        notices = UserNotification.objects.filter(
            title="Avance de producción o entrega actualizado",
        )
        self.assertEqual(notices.count(), 2)
        self.assertEqual(notices.values("event_key").distinct().count(), 2)

    def test_admin_must_confirm_imported_commitment_before_it_enters_planning(self):
        flow = DatedFlow.objects.create(
            kind=DatedFlow.Kind.CUSTOMER_COMMITMENT, client="Cliente A", product="Alitas",
            unit="kg", quantity="7", due_date=timezone.localdate() + timedelta(days=1),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("planning"), {
            "action": "confirm_dated_flow", "flow_id": flow.pk, "confirmed": "1",
            "reconciliation": DatedFlow.Reconciliation.UNIQUE,
        })
        self.assertEqual(response.status_code, 302)
        flow.refresh_from_db()
        self.assertTrue(flow.confirmed)
        self.assertEqual(flow.confirmed_by, self.admin)
        self.assertIsNotNone(flow.confirmed_at)

    def test_admin_flow_quantity_uses_browser_safe_decimal_format(self):
        flow = DatedFlow.objects.create(
            kind=DatedFlow.Kind.INCOMING, client="Cliente A", product="Alitas",
            unit="kg", quantity=Decimal("1000.000"),
            due_date=timezone.localdate() + timedelta(days=1), created_by=self.admin,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("planning"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="flow-qty-{flow.pk}"')
        self.assertContains(response, 'value="1000.000"')
        self.assertNotContains(response, 'value="1,000')

    def test_reviewed_event_saves_source_and_seller_message_hides_audit_details(self):
        from .views import _seller_cards

        event = CommercialEvent.objects.create(
            key="navidad-local", title="Temporada navideña", kind=CommercialEvent.Kind.LOCAL,
            start_date=timezone.localdate(), end_date=timezone.localdate(),
            created_by=self.admin,
        )
        self.client.force_login(self.admin)
        response = self.client.post(reverse("planning"), {
            "action": "review_event", "event_id": event.pk, "reviewed": "1", "active": "1",
            "source": "https://example.com/calendario",
        })
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.source, "https://example.com/calendario")
        self.assertTrue(event.reviewed and event.active)

        seller_card = _seller_cards([{
            "product": "Alitas", "unit": "kg", "suggested": Decimal("18"),
            "suggestion_mode": "sale", "suggestion_event": "Semana Santa",
            "reference_date": date(2025, 4, 17), "reference_qty": 20,
            "explanation": "Mediana de 4 fechas; validada en 8 comparaciones cronológicas.",
        }])[0]
        self.assertIn("20 kg", seller_card["explanation"])
        self.assertIn("18 kg", seller_card["explanation"])
        self.assertNotIn("comparaciones cronológicas", seller_card["explanation"])

    def test_empty_seller_recommendation_explains_missing_client_series(self):
        Assignment.objects.create(user=self.seller, client="Cliente sin ventas")
        self.client.force_login(self.seller)
        response = self.client.get(reverse("recommendations"), {
            "client": "Cliente sin ventas", "mode": "sale",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este cliente no tiene ventas registradas en el catálogo activo")
        self.assertFalse(response.context[0]["client_in_catalog"])
        self.assertTrue(all("sale_evaluation" not in card
                            for card in response.context[0]["recommendations"]))

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("recommendations"), {"client": "Cliente A"})
        admin_card = next(card for card in admin_response.context[0]["recommendations"]
                          if card["product"] == "Alitas")
        self.assertIsInstance(admin_card["sale_evaluation"], dict)
        self.assertEqual(admin_card["sale_evaluation"]["targets"], 8)
