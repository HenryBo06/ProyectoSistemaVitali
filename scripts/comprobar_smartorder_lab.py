"""Check five sessions and both shared interfaces in the isolated synthetic lab.

Use --odoo for a real JSON-2 sale and a separate 3 kg manufacturing authorization.
That option preserves its native documents as reviewable laboratory evidence.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import socket
from threading import Barrier
from time import perf_counter
from uuid import uuid4

from preparar_smartorder_lab import ACCOUNTS, CLIENTS, LAB_HOME, PORTFOLIOS, PRODUCT, ROOT, bootstrap


def check(*, with_odoo=False):
    bootstrap(odoo=with_odoo)
    from django.contrib.auth import get_user_model
    from django.core.exceptions import PermissionDenied
    from django.db import connections
    from django.test import Client, override_settings
    from django.urls import reverse
    from operations import odoo
    from operations.models import Assignment, OdooOrderLink, OrderLine, OrderRequest, SalesDataset
    from operations.streamlit_bridge import call_screen, request_for, screen_context, sign_in, sign_out

    marker = json.loads((LAB_HOME / "laboratorio.json").read_text(encoding="utf-8"))
    if marker.get("kind") != "SmartOrder synthetic laboratory":
        raise RuntimeError("Prepare the dedicated synthetic laboratory before checking it")
    credentials = json.loads((LAB_HOME / "credentials.json").read_text(encoding="utf-8"))
    accounts = {name: get_user_model().objects.get(username=name) for name in ACCOUNTS}
    assert len(accounts) == 5 and sum(account.is_staff for account in accounts.values()) == 2
    assert get_user_model().objects.count() == 5, "Only the five synthetic accounts belong in this laboratory"
    for source in marker["sources"]:
        from pathlib import Path
        path = Path(source["path"]).resolve()
        assert path.is_relative_to(LAB_HOME.resolve()), "Source files must remain inside the separate laboratory"
        assert sha256(path.read_bytes()).hexdigest() == source["sha256"], "Synthetic source changed"
    dataset = SalesDataset.objects.get(pk=marker["sales_dataset_id"])
    assert dataset.row_count == marker["row_count"] == dataset.lines.count()
    assert marker["calendar_months"] == 18 and dataset.last_date == date.fromisoformat(marker["last_date"])

    def login_all():
        web, bridge = {}, {}
        for name in ACCOUNTS:
            web[name] = Client()
            assert web[name].login(username=name, password=credentials[name]), name
            bridge[name] = sign_in(name, credentials[name])
            assert bridge[name], name
        return web, bridge

    def sale_data(client, note, quantity="8"):
        return {"client": client, "required_date": (date.today() + timedelta(days=3)).isoformat(),
                "product": [PRODUCT], "quantity": [quantity], "unit": ["kg"],
                "reason": ["SIMULADO: cliente confirmó la cantidad del recorrido de prueba"],
                "unit_price": ["4"], "discount_percent": ["5"], "payment_terms": "SIMULADO: crédito a 30 días",
                "note": note}

    web, bridge = login_all()
    baseline = {}
    # ponytail: reuse the local baseline sales; no duplicate fixture set or separate business rules.
    with override_settings(SMARTORDER_ODOO_CONFIG=None):
        for index, client in enumerate(CLIENTS, 1):
            note = f"SIMULADO | COMPROBADOR SMARTORDER | cliente {index:03d}"
            order = OrderRequest.objects.filter(note=note).first()
            if order is None:
                owner = Assignment.objects.get(client=client).user.username
                if index % 2:
                    response = web[owner].post(reverse("order_create"), sale_data(client, note))
                else:
                    response, _, _ = call_screen("order_create", bridge[owner], data=sale_data(client, note))
                assert response.status_code == 302, f"Sale creation failed for {client}"
                order = OrderRequest.objects.get(note=note)
            assert order.sale_confirmed and order.lines.get().status == OrderLine.Status.PENDING
            assert order.lines.get().approved_qty is None
            baseline[client] = order

    records = []
    gate = Barrier(5)

    def concurrent_session(name):
        try:
            gate.wait(timeout=20)
            started = perf_counter()
            response = web[name].get(reverse("dashboard"))
            assert response.status_code == 200
            native, shared = response.context_data, screen_context("dashboard", bridge[name])
            for field in ("role", "clients", "order_count", "kpis", "chart_monthly", "chart_channels", "chart_products"):
                assert native.get(field) == shared.get(field), f"Dashboard differs in {field}: {name}"
            assert native["role"] == ("admin" if accounts[name].is_staff else "vendedor")
            response = web[name].get(reverse("orders"))
            shared_orders = screen_context("orders", bridge[name])["orders"]
            native_orders = response.context_data["orders"]
            assert [(row.pk, row.client, row.status) for row in native_orders] == [
                (row.pk, row.client, row.status) for row in shared_orders]
            if not accounts[name].is_staff:
                expected = set(PORTFOLIOS[name])
                assert set(native["clients"]) == expected
                assert all(row.client in expected for row in shared_orders)
                assert "chart_monthly" not in shared and shared.get("dataset") is None
                assert all(not hasattr(row, "dataset") for row in shared_orders)
                assert b"ventas_SIMULADAS_18_meses.xlsx" not in response.content
            return {"account": name, "role": native["role"], "clients": list(native["clients"]),
                    "status": "passed", "seconds": round(perf_counter() - started, 3)}
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=5) as workers:
        records.extend(workers.map(concurrent_session, ACCOUNTS))
    print("PASS: five simultaneous sessions; Django and Streamlit shared contexts; isolated portfolios")

    seller, other = "lab_vendedor_01", "lab_vendedor_02"
    own = baseline[CLIENTS[0]]
    forbidden = baseline[CLIENTS[1]]
    for name in ("sales_upload", "inventory_upload", "users", "odoo_settings", "operational_reports"):
        assert web[seller].get(reverse(name)).status_code == 403, name
        try:
            screen_context(name, bridge[seller])
        except PermissionDenied:
            pass
        else:
            raise AssertionError(f"Streamlit shared route exposed administrative access: {name}")
    assert web[seller].get(reverse("order_detail", args=[forbidden.pk])).status_code == 403
    try:
        screen_context("order_detail", bridge[seller], order_id=forbidden.pk)
    except PermissionDenied:
        pass
    else:
        raise AssertionError("Streamlit exposed an order outside the seller portfolio")
    for route, values in (("order_review", {"line_id": own.lines.get().pk, "decision": "approve",
                             "approved_quantity": "3", "review_note": "SIMULADO: forbidden seller authorization"}),
                          ("order_odoo", {"action": "authorize", "confirm_action": "1"})):
        assert web[seller].post(reverse(route, args=[own.pk]), values).status_code == 403
        try:
            call_screen(route, bridge[seller], data=values, order_id=own.pk)
        except PermissionDenied:
            pass
        else:
            raise AssertionError(f"Seller was allowed to authorize production via Streamlit: {route}")
    detail = web[seller].get(reverse("order_detail", args=[own.pk])).context_data
    shared = screen_context("order_detail", bridge[seller], order_id=own.pk)
    for field in ("lines", "can_edit_sale", "can_cancel_sale", "odoo"):
        assert detail[field] == shared[field], f"Order differs in {field}"
    assert not hasattr(shared["order"], "dataset")
    assert all("reference_qty" not in line and "method" not in line for line in shared["lines"])
    assert "admin_finance" not in shared["odoo"] and "error" not in shared["odoo"]
    print("PASS: direct administrative routes and foreign orders denied; seller DTO excludes history and finance")

    admin = web["lab_admin_01"]
    try:
        reassignment = [CLIENTS[0], *PORTFOLIOS[other]]
        assert admin.post(reverse("users"), {"action": "assign", "user_id": accounts[other].pk,
                                               "clients": reassignment}).status_code == 302
        for name in (seller, other):
            assert not request_for(bridge[name]).user.is_authenticated, "Old Streamlit session remains active"
            assert web[name].get(reverse("dashboard")).status_code == 302, "Old Django session remains active"
        new_seller, new_other = Client(), Client()
        assert new_seller.login(username=seller, password=credentials[seller])
        assert new_other.login(username=other, password=credentials[other])
        assert new_seller.get(reverse("order_detail", args=[own.pk])).status_code == 403
        assert new_other.get(reverse("order_detail", args=[own.pk])).status_code == 200
        reassigned_key = sign_in(other, credentials[other])
        assert screen_context("order_detail", reassigned_key, order_id=own.pk)["order"].client == CLIENTS[0]
        sign_out(reassigned_key)
        assert OrderRequest.objects.get(pk=own.pk).seller_id == accounts[seller].pk, "Reassignment rewrote historical author"
        second_admin = accounts["lab_admin_02"]
        assert admin.post(reverse("users"), {"action": "role", "user_id": second_admin.pk,
                                               "role": "vendedor"}).status_code == 302
        assert not request_for(bridge["lab_admin_02"]).user.is_authenticated
        assert web["lab_admin_02"].get(reverse("dashboard")).status_code == 302
        admin.post(reverse("users"), {"action": "role", "user_id": accounts["lab_admin_01"].pk,
                                       "role": "vendedor"})
        accounts["lab_admin_01"].refresh_from_db()
        assert accounts["lab_admin_01"].is_staff, "Last administrator was removed"
    finally:
        admin.post(reverse("users"), {"action": "role", "user_id": accounts["lab_admin_02"].pk, "role": "admin"})
        for name, clients in PORTFOLIOS.items():
            assert admin.post(reverse("users"), {"action": "assign", "user_id": accounts[name].pk,
                                                    "clients": list(clients)}).status_code == 302
    assert Assignment.objects.count() == len(CLIENTS)
    print("PASS: reassignment closes both sessions and transfers access; role changes close sessions; last admin preserved")

    integration_evidence = None
    if with_odoo:
        config = odoo.configuration()
        if not config.get("enabled") or config.get("database") != "vitali_lab":
            raise RuntimeError("--odoo requires enabled credentials for the separate vitali_lab database")
        assert odoo.rpc("ping").get("database", "vitali_lab") == "vitali_lab"
        buyer = Client()
        assert buyer.login(username=seller, password=credentials[seller])
        note = "SIMULADO | INTEGRACIÓN REAL SMARTORDER | " + uuid4().hex
        response = buyer.post(reverse("order_create"), sale_data(CLIENTS[0], note))
        assert response.status_code == 302
        order = OrderRequest.objects.get(note=note)
        line = order.lines.get()
        link = OdooOrderLink.objects.get(order=order)
        assert link.status == "synced", link.last_error
        before = odoo.synchronize(order, "refresh")
        assert before["sale"]["state"] == "sale" and before["lines"][0]["ordered"] == 8
        assert not before["lines"][0].get("mo_ids") and not before["lines"][0].get("authorized")
        assert line.status == OrderLine.Status.PENDING and line.approved_qty is None
        payload = odoo.sale_payload(order, link)
        rpc_gate = Barrier(5)

        def repeated_send(_):
            rpc_gate.wait(timeout=20)
            return odoo.rpc("sync_sale", payload=payload)

        with ThreadPoolExecutor(max_workers=5) as workers:
            replies = list(workers.map(repeated_send, range(5)))
        assert {row["sale"]["id"] for row in replies} == {before["sale"]["id"]}, "Concurrent resend duplicated the sale"
        assert all(not row["lines"][0].get("mo_ids") for row in replies)
        with socket.socket() as unused_listener:
            unused_listener.bind(("127.0.0.1", 0))
            disconnected_port = unused_listener.getsockname()[1]
        offline_config = LAB_HOME / "configuracion_desconectada.json"
        offline_config.write_text(json.dumps({**config, "url": f"http://127.0.0.1:{disconnected_port}"}), encoding="utf-8")
        try:
            with override_settings(SMARTORDER_ODOO_CONFIG=str(offline_config)):
                try:
                    odoo.synchronize(order)
                except odoo.OdooError:
                    pass
                else:
                    raise AssertionError("An unreachable Odoo connection was presented as completed")
            link.refresh_from_db()
            assert link.status == "error" and link.last_error
        finally:
            offline_config.unlink(missing_ok=True)
        recovered = odoo.synchronize(order)
        link.refresh_from_db()
        assert link.status == "synced" and not link.last_error
        assert recovered["sale"]["id"] == before["sale"]["id"], "Reconnecting duplicated the native sale"
        response = admin.post(reverse("order_review", args=[order.pk]), {"line_id": line.pk,
            "decision": "approve", "approved_quantity": "3", "review_note": "SIMULADO: administrador autoriza fabricar 3 kg"})
        assert response.status_code == 302
        line.refresh_from_db()
        assert line.approved_qty == Decimal("3")
        after = odoo.synchronize(order, "refresh")
        native_line = after["lines"][0]
        assert native_line["authorized"] and native_line["authorized_quantity"] == 3
        assert len(native_line["mo_ids"]) == 1 and native_line["manufacturing_state"] == "confirmed"
        assert native_line["produced"] == 0, "Authorization was incorrectly presented as completed production"
        repeat = odoo.synchronize(order, "authorize")
        assert repeat["lines"][0]["mo_ids"] == native_line["mo_ids"]
        seller_key = sign_in(seller, credentials[seller])
        public = screen_context("order_detail", seller_key, order_id=order.pk)
        assert public["odoo"]["lines"][0]["approved"] == Decimal("3")
        assert "admin_finance" not in public["odoo"]
        assert buyer.get(reverse("order_detail", args=[order.pk])).context_data["odoo"] == public["odoo"]
        integration_evidence = {"order_id": order.pk, "line_id": line.pk, "reference": payload["reference"],
            "sale_id": after["sale"]["id"], "sale_name": after["sale"]["name"],
            "manufacturing_ids": native_line["mo_ids"], "ordered_kg": 8, "authorized_kg": 3,
            "reserved_kg": native_line["reserved"], "produced_kg": native_line["produced"],
            "concurrent_identical_transfers": 5, "real_unreachable_connection_and_recovery": "passed",
            "notice": "Native documents preserved; manufacturing is authorized but remains unfinished."}
        sign_out(seller_key)
        print("PASS: real JSON-2 sale; five concurrent retries keep one sale; separate 3 kg authorization keeps one native MO")

    for key in bridge.values():
        sign_out(key)
    connections.close_all()
    evidence = {"kind": "SmartOrder synthetic laboratory check", "status": "passed", "date": date.today().isoformat(),
        "database": str(LAB_HOME / "smartorder.sqlite3"), "source_rows": marker["row_count"],
        "sessions": records, "portfolio_isolation": "passed", "role_change": "passed", "reassignment": "passed",
        "django_streamlit_shared_contexts": "passed", "integration": integration_evidence,
        "notice": "Only isolated synthetic accounts and source rows were used. No business database or source was imported."}
    (LAB_HOME / "comprobacion.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Evidence: .local-web-lab/comprobacion.json")
    return evidence


def check_results(order_id):
    """Read the completed native scenario without creating or authorizing another sale."""
    bootstrap(odoo=True)
    from django.test import Client
    from django.urls import reverse
    from operations import odoo
    from operations.models import OdooOrderLink, OrderRequest
    from operations.streamlit_bridge import screen_context, sign_in, sign_out
    from streamlit.testing.v1 import AppTest

    if odoo.configuration().get("database") != "vitali_lab":
        raise RuntimeError("Results may only be refreshed from the separate Odoo laboratory")
    order = OrderRequest.objects.get(pk=order_id)
    if order.client not in CLIENTS or not order.note.startswith("SIMULADO | INTEGRACIÓN REAL SMARTORDER | "):
        raise RuntimeError("Only the preserved synthetic integrated scenario can be checked")
    credentials = json.loads((LAB_HOME / "credentials.json").read_text(encoding="utf-8"))
    snapshot = odoo.synchronize(order, "refresh")
    row = snapshot["lines"][0]
    for field, expected in (("ordered", 8), ("produced", 3), ("delivered", 8), ("returned", 1), ("net_delivered", 7), ("outstanding", 1)):
        assert Decimal(str(row[field])) == expected, f"Native result differs: {field}"
    assert row["manufacturing_state"] == "done"
    assert OdooOrderLink.objects.get(order=order).status == "synced"

    rendered_checks = []
    for name in (order.seller.username, "lab_admin_01"):
        client = Client()
        assert client.login(username=name, password=credentials[name])
        key = sign_in(name, credentials[name])
        assert key
        try:
            response = client.get(reverse("order_detail", args=[order.pk]))
            assert response.status_code == 200
            shared = screen_context("order_detail", key, order_id=order.pk)
            assert response.context_data["odoo"] == shared["odoo"]
            admin = name == "lab_admin_01"
            finance = shared["odoo"].get("admin_finance")
            if admin:
                assert finance["balance"] == 0
                assert {(invoice["type"], Decimal(str(invoice["total"]))) for invoice in finance["invoices"]} == {
                    ("out_invoice", Decimal("30.4")), ("out_refund", Decimal("3.8"))}
                assert all(invoice["state"] == "posted" and Decimal(str(invoice["residual"])) == 0
                           for invoice in finance["invoices"])
                assert b"Facturaci" in response.content
            else:
                assert finance is None and "invoices" not in shared["odoo"]
                assert all("unit_cost" not in line for line in shared["odoo"]["lines"])
                assert b"Saldo pendiente" not in response.content and b"Margen" not in response.content
                for invoice in snapshot["invoices"]:
                    assert invoice["name"].encode("utf-8") not in response.content

            app = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=30)
            app.session_state["session_key"] = key
            app.session_state["workspace"] = "Pedidos y producción"
            app.run()
            assert not app.exception, "Streamlit order rendering failed"
            selector = next(element for element in app.selectbox if element.label == "Pedido para consultar")
            selector.set_value(order.pk).run()
            assert not app.exception
            operation = next(frame.value for frame in app.dataframe if "Producido" in frame.value.columns and "Autorizado" in frame.value.columns)
            for label, expected in (("Vendido", 8), ("Autorizado", 3), ("Producido", 3), ("Entregado", 8), ("Devuelto", 1)):
                assert Decimal(str(operation.iloc[0][label])) == expected, f"Streamlit result differs: {label}"
            subheaders = [element.value for element in app.subheader]
            if admin:
                assert "Facturación y saldo" in subheaders
                financial = next(frame.value for frame in app.dataframe if "Documento" in frame.value.columns)
                assert len(financial) == 2 and all(Decimal(str(value)) == 0 for value in financial["Pendiente"])
                rendered_text = "\n".join(element.value for element in app.markdown)
                assert f"Saldo pendiente: {finance['balance']} {finance['currency']}" in rendered_text
            else:
                assert "Facturación y saldo" not in subheaders
                assert all("Documento" not in frame.value.columns for frame in app.dataframe)
                assert "Resultados operativos" not in app.radio[0].options
            rendered_checks.append({"role": "admin" if admin else "vendedor", "django": "passed", "streamlit": "passed"})
            if admin:
                native_report = client.get(reverse("operational_reports"))
                shared_report = screen_context("operational_reports", key)
                assert native_report.status_code == 200
                assert native_report.context_data["rows"] == shared_report["rows"]
                detail = next(item for item in shared_report["rows"] if item["order_id"] == order.pk)
                assert detail["delivered"] - detail["returned"] == 7 and detail["pending"] == 1
                assert detail["margin"] == Decimal("12.6") and detail["unit"] == "kg"
                assert len(shared_report["totals"]) == 1
                assert shared_report["totals"][0]["delivered"] == 7 and shared_report["totals"][0]["pending"] == 1
                app.radio[0].set_value("Resultados operativos").run()
                assert not app.exception
                report_frame = next(frame.value for frame in app.dataframe if "Margen simulado" in frame.value.columns)
                displayed = report_frame.loc[report_frame["Venta"] == order.pk].iloc[0]
                assert Decimal(str(displayed["Salida al cliente"])) - Decimal(str(displayed["Devuelto"])) == 7
                assert Decimal(str(displayed["Entrega neta"])) == 7
                assert Decimal(str(displayed["Pendiente"])) == 1 and Decimal(str(displayed["Margen simulado"])) == Decimal("12.6")
        finally:
            sign_out(key)
            client.logout()
    evidence = {"status": "passed", "order_id": order.pk, "sale_id": snapshot["sale"]["id"],
        "reference": snapshot["reference"], "native": {key: row[key] for key in
        ("ordered", "produced", "delivered", "returned", "net_delivered", "outstanding", "manufacturing_state")},
        "financial": {"invoice_usd": 30.4, "credit_usd": 3.8, "balance_usd": 0},
        "report": {"unit": "kg", "net_delivered": 7, "pending": 1, "standard_cost_margin_usd": 12.6},
        "rendered_interfaces": rendered_checks,
        "notice": "Native result refreshed; no sale or production authorization was created. Financial data stays administrative."}
    (LAB_HOME / "resultados-integrados.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: completed native results; Django and rendered Streamlit; seller privacy; admin balance 0; net 7 kg, pending 1 kg, simulated margin USD 12.60")
    print("Evidence: .local-web-lab/resultados-integrados.json; initial integration evidence preserved")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--odoo", action="store_true", help="Create and preserve an actual synthetic sale and manufacturing authorization")
    mode.add_argument("--refresh-order", type=int, help="Read and check the preserved completed native scenario; create no additional sale")
    arguments = parser.parse_args()
    check_results(arguments.refresh_order) if arguments.refresh_order is not None else check(with_odoo=arguments.odoo)
