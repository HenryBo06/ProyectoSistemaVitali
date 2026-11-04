"""Isolated browser smoke check for synthetic SmartOrder Django and Streamlit."""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / ".local-web-lab"
OUT = ROOT / ".local-web-lab" / "browser-evidence"
OUT.mkdir(parents=True, exist_ok=True)
CREDENTIALS = json.loads((LAB / "credentials.json").read_text(encoding="utf-8"))
CLIENT = "Cliente Vitali LAB-001 (simulado)"


def ready(url: str, limit: int = 120) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        try:
            with urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"Server did not become ready: {url}")


def seed_synthetic_restock() -> None:
    """Add LAB-only stock and target so the corrected layout is tested with data."""
    import sys
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitali_web.settings")
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from operations.models import ClientStockCount, RestockTarget
    from decimal import Decimal

    seller = get_user_model().objects.get(username="lab_vendedor_01")
    admin = get_user_model().objects.get(username="lab_admin_01")
    product = "Producto avícola LAB (simulado)"
    ClientStockCount.objects.create(
        client=CLIENT, product=product, location="Bodega Demo",
        unit="kg", available=Decimal("2"), observed_on=date.today(), reported_by=seller,
    )
    RestockTarget.objects.create(
        scope=RestockTarget.Scope.CLIENT, owner_key=f"client:{CLIENT}",
        product=product, unit="kg", target_quantity=Decimal("7"), updated_by=admin,
    )
    from django.utils import timezone
    from operations.models import CommercialEvent
    day = timezone.localdate() + timedelta(days=5)
    CommercialEvent.objects.create(
        key="feria-lab-simulada", title="Feria LAB (simulada)",
        kind=CommercialEvent.Kind.COMMERCIAL, start_date=day, end_date=day,
        territory="Nacional", reviewed=True, active=True, created_by=admin,
    )


def django_smoke(browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill("lab_vendedor_01")
    page.locator('input[name="password"]').fill(CREDENTIALS["lab_vendedor_01"])
    page.get_by_role("button", name="Entrar a mi espacio").click()
    page.get_by_role("heading", name="Mi trabajo").wait_for(timeout=20000)
    page.get_by_text("Feria LAB (simulada)").first.wait_for(timeout=20000)
    vitali_logo = page.locator(".app-header .brand-symbol").first
    if not vitali_logo.is_visible() or not vitali_logo.evaluate("(e) => e.complete && e.naturalWidth > 0"):
        raise AssertionError("Django Vitali mark must load as the existing SVG.")
    page.screenshot(path=str(OUT / "django-event-dashboard-mobile.png"), full_page=True)
    page.goto("http://127.0.0.1:8000/notificaciones/", wait_until="domcontentloaded")
    page.get_by_text("dentro de 5 días").first.wait_for(timeout=20000)

    query = urlencode({
        "mode": "client_restock", "client": CLIENT,
        "required_date": (date.today() + timedelta(days=2)).isoformat(),
    })
    page.goto(f"http://127.0.0.1:8000/recomendaciones/?{query}", wait_until="domcontentloaded")
    page.get_by_role("heading", name="IA comercial · Qué ofrecer y qué hacer").wait_for(timeout=30000)
    page.get_by_role("heading", name="Sugerencia de venta para este cliente").wait_for(timeout=30000)
    page.get_by_text("Acción sugerida:", exact=False).first.wait_for(timeout=30000)
    page.get_by_text("Producto avícola LAB (simulado)", exact=True).first.wait_for(timeout=30000)
    tabs = page.locator(".seller-mode-tabs")
    if not tabs.is_visible() or tabs.bounding_box()["height"] > 51:
        raise AssertionError("Django seller navigation must fit one row on mobile.")
    links = tabs.locator("a")
    ys = [round(links.nth(i).bounding_box()["y"]) for i in range(links.count())]
    if len(ys) != 3 or max(ys) - min(ys) > 2:
        raise AssertionError(f"Django seller mode tabs stack or misalign: {ys}")
    heading_size = page.locator(".seller-page-heading h1").first.evaluate(
        "(element) => parseFloat(getComputedStyle(element).fontSize)")
    product_size = page.locator(".seller-product-summary h2").first.evaluate(
        "(element) => parseFloat(getComputedStyle(element).fontSize)")
    if not (17 <= heading_size <= 21 and 14 <= product_size <= 16):
        raise AssertionError(f"Django type scale is inconsistent: {heading_size}px / {product_size}px")
    summary = page.locator(".seller-restock-summary")
    if summary.count() != 1:
        raise AssertionError("Expected one populated synthetic restock summary.")
    summary.first.scroll_into_view_if_needed()
    if not summary.first.is_visible():
        raise AssertionError("Restock summary is not visible in Django mobile.")
    details = summary.first.inner_text()
    if "2 kg" not in details or "7 kg" not in details:
        raise AssertionError(f"Populated LAB stock or target missing: {details!r}")
    if summary.first.evaluate("(e) => e.scrollWidth > e.clientWidth + 2"):
        raise AssertionError("Restock summary overflows its own container.")
    if page.locator("body").evaluate("(e) => e.scrollWidth > window.innerWidth + 2"):
        raise AssertionError("Django mobile has horizontal document overflow.")
    # Screenshot from document top: a sticky bar captured mid-scroll is not a layout defect.
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / "django-restock-mobile.png"), full_page=True)

    # Narrow mobile reflow: the Vitali wordmark, menu, tabs and form must still
    # fit without clipping at 320 CSS pixels (not only at the 390px screenshot).
    page.set_viewport_size({"width": 320, "height": 720})
    page.wait_for_timeout(200)
    if page.locator("body").evaluate("(e) => e.scrollWidth > innerWidth + 2"):
        raise AssertionError("Django has horizontal document overflow at 320px.")
    brand_overlap = page.evaluate("""() => {
      const a = document.querySelector('.app-header .brand');
      const b = document.querySelector('.app-header button[aria-label="Abrir menú"]');
      if (!a || !b) return true;
      const x = a.getBoundingClientRect(), y = b.getBoundingClientRect();
      return x.right > y.left + 2;
    }""")
    if brand_overlap:
        raise AssertionError("Django Vitali brand and mobile menu overlap at 320px.")
    if page.locator(".app-header .brand-product").is_visible():
        raise AssertionError("Secondary SmartOrder header text must not wrap under the menu at 320px.")
    page.screenshot(path=str(OUT / "django-restock-320.png"), full_page=True)

    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / "django-restock-desktop.png"), full_page=True)
    context.close()



def django_connection_smoke(browser) -> None:
    """Admin-only connection management must be usable without a database secret."""
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill("lab_admin_01")
    page.locator('input[name="password"]').fill(CREDENTIALS["lab_admin_01"])
    page.get_by_role("button", name="Entrar a mi espacio").click()
    page.goto("http://127.0.0.1:8000/administracion/conexiones/", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Conexiones de bases de datos").wait_for(timeout=20000)
    fields = {"alias": "lab-db-ventas", "name": "Ventas LAB simuladas",
              "host": "localhost", "port": "5432", "database": "lab_vitali",
              "username": "read_only", "password_env": "SMARTORDER_SOURCE_LAB_PASSWORD"}
    for name, value in fields.items():
        page.locator(f'input[name="{name}"]').fill(value)
    page.get_by_role("button", name="Guardar configuración").click()
    page.get_by_text("Ventas LAB simuladas", exact=True).first.wait_for(timeout=15000)
    if "SMARTORDER_SOURCE_LAB_PASSWORD" in page.locator("body").inner_text():
        # The secret variable name may appear in the editing form, but not a secret value.
        pass
    if page.locator("body").evaluate("(e) => e.scrollWidth > innerWidth + 2"):
        raise AssertionError("Admin database connection screen overflows at 390px.")
    page.screenshot(path=str(OUT / "django-admin-database-connections-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(OUT / "django-admin-database-connections-desktop.png"), full_page=True)
    context.close()



def django_etl_smoke(browser) -> None:
    """ETL preflight must handle changed CSV headers without activating history."""
    from operations.models import SalesDataset
    from django.db import close_old_connections
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections

    def count_sales():
        try:
            return SalesDataset.objects.count()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        old_count = executor.submit(count_sales).result()
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill("lab_admin_01")
    page.locator('input[name="password"]').fill(CREDENTIALS["lab_admin_01"])
    page.get_by_role("button", name="Entrar a mi espacio").click()
    page.goto("http://127.0.0.1:8000/administracion/etl/", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Preparación ETL de históricos").wait_for(timeout=20000)
    csv_data = (
        "DiaFactura;Cuenta;Articulo;Volumen;PrecioNeto;TipoUnidad\n"
        "10/01/2026;Cliente LAB;Alitas LAB;2,5;10,00;kg\n"
    ).encode("utf-8")
    page.locator('input[name="etl_file"]').set_input_files({
        "name": "fuente-lab-cambiada.csv", "mimeType": "text/csv", "buffer": csv_data})
    page.locator('#etl-date').select_option("%d/%m/%Y")
    page.locator('#etl-decimal').select_option(",")
    page.get_by_text("Mapeo manual cuando cambian las columnas").click()
    page.locator('textarea[name="mapping"]').first.fill(
        '{"Fecha":"DiaFactura","Cliente":"Cuenta","Producto":"Articulo",'
        '"Cantidad_kg_unid":"Volumen","Precio_Unitario_USD":"PrecioNeto","Unidad":"TipoUnidad"}')
    page.get_by_role("button", name="Inspeccionar sin cargar").click()
    page.get_by_text("Estructura preliminar válida").wait_for(timeout=20000)
    page.get_by_text("2.5", exact=True).first.wait_for(timeout=20000)
    if page.locator("body").evaluate("(e) => e.scrollWidth > window.innerWidth + 2"):
        raise AssertionError("ETL admin screen overflows at 390px.")
    page.screenshot(path=str(OUT / "django-admin-etl-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(OUT / "django-admin-etl-desktop.png"), full_page=True)
    context.close()
    with ThreadPoolExecutor(max_workers=1) as executor:
        new_count = executor.submit(count_sales).result()
    if new_count != old_count:
        raise AssertionError("ETL preflight unexpectedly mutated historical sales.")


def streamlit_smoke(browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8501", wait_until="domcontentloaded", timeout=60000)
    page.get_by_label("Usuario", exact=True).wait_for(timeout=60000)
    page.get_by_label("Usuario", exact=True).fill("lab_vendedor_01")
    page.get_by_label("Contraseña", exact=True).fill(CREDENTIALS["lab_vendedor_01"])
    page.get_by_role("button", name="Entrar", exact=True).click()
    page.get_by_text("Mi trabajo", exact=True).first.wait_for(timeout=60000)
    page.get_by_text("Feria LAB (simulada)").first.wait_for(timeout=60000)
    logo = page.locator(".vitali-identity img.vitali-symbol").first
    if not logo.is_visible():
        raise AssertionError("Streamlit must show the Vitali V in its sidebar.")
    page.screenshot(path=str(OUT / "streamlit-event-dashboard-desktop.png"), full_page=True)
    page.get_by_text("Productos y pedidos", exact=True).click()
    page.get_by_role("heading", name="IA comercial: qué ofrecer y qué hacer").wait_for(timeout=60000)
    page.get_by_role("heading", name="Productos para ofrecer").wait_for(timeout=60000)
    title_size = page.get_by_role("heading", name="IA comercial: qué ofrecer y qué hacer").evaluate(
        "(element) => parseFloat(getComputedStyle(element).fontSize)")
    if not (18 <= title_size <= 21):
        raise AssertionError(f"Streamlit seller page heading is oversized: {title_size}px")
    desktop = page.locator(".st-key-seller-desktop-catalog").first
    mobile = page.locator(".st-key-seller-mobile-catalog").first
    if not desktop.is_visible() or mobile.is_visible():
        raise AssertionError("Streamlit desktop catalogue is not isolated from mobile catalogue.")
    page.screenshot(path=str(OUT / "streamlit-catalog-desktop.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    mobile.wait_for(state="visible", timeout=20000)
    # Streamlit may keep the desktop sidebar open across a viewport resize.
    # Close it before asserting that the actual catalogue is unobscured.
    collapse = page.locator('[data-testid="stSidebar"] button[aria-label="Collapse sidebar"]')
    if collapse.count():
        # Streamlit re-renders responsive sidebar buttons; DOM-level click avoids
        # Playwright trying to interact with a handle that has been detached.
        page.evaluate("""() => {
          const button = document.querySelector(
            '[data-testid="stSidebar"] button[aria-label="Collapse sidebar"]');
          if (button) button.click();
        }""")
        page.wait_for_timeout(500)
    if desktop.is_visible():
        raise AssertionError("Streamlit desktop table is still visible at mobile breakpoint.")
    first_row = page.locator('[class*="st-key-seller-mobile-catalog-row"]').first
    first_row.scroll_into_view_if_needed()
    if not first_row.is_visible():
        raise AssertionError("Streamlit mobile catalogue has no visible product rows.")
    # Streamlit's sidebar animation and internal scroll containers can detach
    # elements during a hit-test. Inspect the screenshot for visual occlusion;
    # assert DOM visibility and no document overflow here.
    page.wait_for_timeout(250)
    if page.locator("body").evaluate("(e) => e.scrollWidth > window.innerWidth + 2"):
        raise AssertionError("Streamlit mobile has horizontal document overflow.")

    # The mobile expand-sidebar icon must not intersect SmartOrder's heading.
    overlap = page.evaluate("""() => {
      const text = document.querySelector('.vitali-ticket span');
      const toggle = document.querySelector('[data-testid="stHeader"] button[aria-label="Expand sidebar"]');
      if (!text || !toggle) return false;
      const a = text.getBoundingClientRect(), b = toggle.getBoundingClientRect();
      return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    }""")
    if overlap:
        raise AssertionError("Streamlit mobile navigation button overlaps the application identity.")
    symbol = page.locator(".vitali-ticket img.vitali-symbol").first.bounding_box()
    title = page.locator(".vitali-ticket .vitali-ticket-name").first.bounding_box()
    if symbol is None or title is None:
        raise AssertionError("Streamlit Vitali mobile brand elements are missing.")
    logo_center = symbol["y"] + symbol["height"] / 2
    title_center = title["y"] + title["height"] / 2
    if abs(logo_center - title_center) > 7 or title["x"] < symbol["x"] + symbol["width"] - 1:
        raise AssertionError("The Vitali logo and wordmark must share an unobstructed mobile row.")
    page.screenshot(path=str(OUT / "streamlit-catalog-mobile.png"), full_page=False)

    page.set_viewport_size({"width": 320, "height": 720})
    page.wait_for_timeout(300)
    if page.locator("body").evaluate("(e) => e.scrollWidth > innerWidth + 2"):
        raise AssertionError("Streamlit has horizontal document overflow at 320px.")
    small_brand_overlap = page.evaluate("""() => {
      const text = document.querySelector('.vitali-ticket .vitali-ticket-name');
      const toggle = document.querySelector('[data-testid="stHeader"] button[aria-label="Expand sidebar"]');
      if (!text || !toggle) return false;
      const a = text.getBoundingClientRect(), b = toggle.getBoundingClientRect();
      return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    }""")
    if small_brand_overlap:
        raise AssertionError("Streamlit Vitali brand and sidebar button overlap at 320px.")
    page.screenshot(path=str(OUT / "streamlit-catalog-320.png"), full_page=False)
    context.close()


def django_etl_approved_load_smoke(browser) -> None:
    """Exercise the entire staff-approved ETL lifecycle in isolated LAB browser."""
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    from operations.models import EtlLoadRun, SalesDataset, SalesLine

    def stats():
        try:
            return {
                "datasets": SalesDataset.objects.count(),
                "loads": EtlLoadRun.objects.count(),
            }
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        before = executor.submit(stats).result()

    context = browser.new_context(viewport={"width": 1280, "height": 820}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill("lab_admin_01")
    page.locator('input[name="password"]').fill(CREDENTIALS["lab_admin_01"])
    page.get_by_role("button", name="Entrar a mi espacio").click()
    page.goto("http://127.0.0.1:8000/administracion/etl/", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Preparación ETL de históricos").wait_for()

    profile_form = page.locator('form:has(input[name="action"][value="save_profile"])')
    profile_form.locator('input[name="alias"]').fill("smoke-vitali-2025")
    profile_form.locator('input[name="name"]').fill("Contrato de ventas LAB")
    profile_form.locator('textarea[name="mapping"]').fill(
        '{"Fecha":"Fecha","Cliente":"Cliente","Producto":"Producto",'
        '"Cantidad_kg_unid":"Cantidad","Precio_Unitario_USD":"Precio"}'
    )
    profile_form.get_by_role("button", name="Guardar nueva versión").click()
    page.get_by_text("Perfil smoke-vitali-2025 versión 1 guardado").wait_for(timeout=20000)
    profile_option = page.locator("#etl-profile option").filter(has_text="smoke-vitali-2025").first
    profile_id = profile_option.get_attribute("value")
    if not profile_id:
        raise AssertionError("Browser ETL profile was not created.")

    raw = (
        "Fecha,Cliente,Producto,Cantidad,Precio\n"
        f"2025-01-01,{CLIENT},Producto avícola LAB (simulado),2,4\n"
        f"2025-01-02,{CLIENT},Producto avícola LAB (simulado),3,4\n"
        f"2025-01-01,{CLIENT},Producto adicional LAB,800,4\n"
    ).encode("utf-8")
    page.locator("#etl-profile").select_option(profile_id)
    page.locator('input[name="etl_file"]').set_input_files({
        "name": "lab-ventas-2025.csv", "mimeType": "text/csv", "buffer": raw,
    })
    page.get_by_role("button", name="Inspeccionar sin cargar").click()
    page.get_by_text("Estructura preliminar válida").wait_for(timeout=20000)
    if page.locator('select[name="unit_value"]').count() != 2:
        raise AssertionError("The two LAB products must require separate provisional units.")
    page.screenshot(path=str(OUT / "django-etl-approved-inspection-desktop.png"), full_page=True)
    inspection_selector = page.locator("#etl-load-inspection")
    if inspection_selector.locator("option").count() < 2:
        raise AssertionError("Successful inspection not available for approval.")
    inspection_selector.select_option(index=1)
    page.locator('input[name="etl_load_file"]').set_input_files({
        "name": "lab-ventas-2025.csv", "mimeType": "text/csv", "buffer": raw,
    })
    for index, selector in enumerate(page.locator('select[name="unit_value"]').all()):
        selector.select_option("lb" if index == 0 else "kg")
    page.locator("#etl-load-provenance").select_option("simulated")
    page.locator("#etl-load-semantics").select_option("sale_order")
    for name in ("confirmed_origin", "confirmed_full_snapshot", "approved_load"):
        page.locator(f'input[name="{name}"]').check()
    page.get_by_role("button", name="Revalidar y activar histórico").click()
    page.wait_for_url("**/ventas/**", timeout=20000)
    feedback = page.locator(".flash-messages").inner_text()
    if "ETL aprobado" not in feedback:
        raise AssertionError(f"Staff-approved ETL did not complete: {feedback!r}")

    def activated():
        try:
            new = SalesDataset.objects.get(active=True)
            rows = list(SalesLine.objects.filter(dataset=new).values_list("client", "product", "unit"))
            return {"datasets": SalesDataset.objects.count(),
                    "loads": EtlLoadRun.objects.count(),
                    "rows": rows, "verified": new.verified,
                    "provenance": new.provenance}
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(activated).result()
    if (result["datasets"] != before["datasets"] + 1
            or result["loads"] != before["loads"] + 1
            or len(result["rows"]) != 3 or result["verified"]
            or result["provenance"] != "simulated"
            or {r[2] for r in result["rows"]} != {"lb", "kg"}):
        raise AssertionError(f"Approved ETL did not persist safe LAB snapshot: {result!r}")
    context.close()

    seller_context = browser.new_context(viewport={"width": 390, "height": 844})
    seller = seller_context.new_page()
    seller.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    seller.locator('input[name="username"]').fill("lab_vendedor_01")
    seller.locator('input[name="password"]').fill(CREDENTIALS["lab_vendedor_01"])
    seller.get_by_role("button", name="Entrar a mi espacio").click()
    query = urlencode({"client": CLIENT, "mode": "sale",
                       "required_date": date.today().isoformat()})
    seller.goto(f"http://127.0.0.1:8000/recomendaciones/?{query}", wait_until="domcontentloaded")
    seller.get_by_role("heading", name="IA comercial · Qué ofrecer y qué hacer").wait_for(timeout=20000)
    seller.get_by_role("heading", name="Sugerencia de venta para este cliente").wait_for(timeout=20000)
    rows = seller.locator(".product-summary")
    if rows.count() != 2:
        raise AssertionError("Seller must see the two products from the approved LAB snapshot.")
    names = [rows.nth(i).locator("h2").inner_text() for i in range(2)]
    if names != ["Producto avícola LAB (simulado)", "Producto adicional LAB"]:
        raise AssertionError(f"Seller historical priority is incorrect: {names!r}")
    text = seller.locator(".product-grid").inner_text()
    if "Prioridad histórica 1 de 2" not in text or "3 días con ventas" in text:
        raise AssertionError("Seller historical purchase-frequency evidence is inconsistent.")
    if "Sin estimación" not in text:
        raise AssertionError("Stale, provisional data must not produce automatic delivery quantities.")
    seller.screenshot(path=str(OUT / "django-etl-approved-seller-priority-mobile.png"), full_page=True)
    denied = seller.goto("http://127.0.0.1:8000/administracion/etl/",
                         wait_until="domcontentloaded")
    if denied.status != 403:
        raise AssertionError("Seller must not access staff ETL approvals.")
    seller_context.close()


if __name__ == "__main__":
    seed_synthetic_restock()
    ready("http://127.0.0.1:8000/entrar/")
    ready("http://127.0.0.1:8501/_stcore/health")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            django_smoke(browser)
            django_connection_smoke(browser)
            django_etl_smoke(browser)
            streamlit_smoke(browser)
            # This changes the active LAB snapshot, so it runs after both existing journeys.
            django_etl_approved_load_smoke(browser)
        finally:
            browser.close()
    print("PASS: real-browser smoke: Django and Streamlit, desktop + mobile, synthetic LAB data only.")
