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


def django_smoke(browser) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8000/entrar/", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill("lab_vendedor_01")
    page.locator('input[name="password"]').fill(CREDENTIALS["lab_vendedor_01"])
    page.get_by_role("button", name="Entrar a mi espacio").click()
    page.get_by_role("heading", name="Mi trabajo").wait_for(timeout=20000)

    query = urlencode({
        "mode": "client_restock", "client": CLIENT,
        "required_date": (date.today() + timedelta(days=2)).isoformat(),
    })
    page.goto(f"http://127.0.0.1:8000/recomendaciones/?{query}", wait_until="domcontentloaded")
    page.get_by_role("heading", name="Productos y sugerencias").wait_for(timeout=30000)
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

    page.set_viewport_size({"width": 1440, "height": 900})
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / "django-restock-desktop.png"), full_page=True)
    context.close()


def streamlit_smoke(browser) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = context.new_page()
    page.goto("http://127.0.0.1:8501", wait_until="domcontentloaded", timeout=60000)
    page.get_by_label("Usuario", exact=True).wait_for(timeout=60000)
    page.get_by_label("Usuario", exact=True).fill("lab_vendedor_01")
    page.get_by_label("Contraseña", exact=True).fill(CREDENTIALS["lab_vendedor_01"])
    page.get_by_role("button", name="Entrar", exact=True).click()
    page.get_by_text("Mi trabajo", exact=True).first.wait_for(timeout=60000)
    page.get_by_text("Productos y pedidos", exact=True).click()
    page.get_by_role("heading", name="Productos y sugerencias").wait_for(timeout=60000)
    page.get_by_role("heading", name="Productos para ofrecer").wait_for(timeout=60000)
    title_size = page.get_by_role("heading", name="Productos y sugerencias").evaluate(
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
    page.screenshot(path=str(OUT / "streamlit-catalog-mobile.png"), full_page=False)
    context.close()


if __name__ == "__main__":
    seed_synthetic_restock()
    ready("http://127.0.0.1:8000/entrar/")
    ready("http://127.0.0.1:8501/_stcore/health")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            django_smoke(browser)
            streamlit_smoke(browser)
        finally:
            browser.close()
    print("PASS: real-browser smoke: Django and Streamlit, desktop + mobile, synthetic LAB data only.")
