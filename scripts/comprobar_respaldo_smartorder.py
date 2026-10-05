"""Back up and restore only the isolated synthetic SmartOrder database and files.

No argument creates a fresh backup. An existing backup under .local-web-lab/backups
can be supplied to verify it again. Restored copies are preserved for inspection.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
LAB_HOME = ROOT / ".local-web-lab"
BACKUPS = LAB_HOME / "backups"


def digest(path):
    with path.open("rb") as source:
        return sha256(source.read()).hexdigest()


def database_state(path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
        assert source.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        users = source.execute("SELECT username, is_staff, is_active FROM auth_user ORDER BY username").fetchall()
        assert len(users) == 5 and all(row[0].startswith("lab_") for row in users), "Only synthetic laboratory accounts are allowed"
        assert sum(row[1] for row in users) == 2, "Expected the two laboratory administrators"
        counts = {table: source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                  ("operations_salesdataset", "operations_salesline", "operations_inventorybatch",
                   "operations_inventoryitem", "operations_orderrequest", "operations_orderline",
                   "operations_assignment", "operations_odooorderlink", "operations_accessevent")}
        files = source.execute("SELECT file, digest FROM operations_salesdataset UNION ALL SELECT file, digest FROM operations_inventorybatch").fetchall()
        links = source.execute("SELECT order_id, reference, revision, status FROM operations_odooorderlink ORDER BY order_id").fetchall()
        portfolios = source.execute("SELECT u.username, a.client FROM operations_assignment a JOIN auth_user u ON u.id=a.user_id ORDER BY u.username,a.client").fetchall()
    return {"users": users, "counts": counts, "uploaded_sources": files, "odoo_links": links, "portfolios": portfolios}


RESTORED_CHECK = r'''
import json, os
from pathlib import Path
import django
django.setup()
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from operations.models import Assignment, SalesDataset, InventoryBatch, OdooOrderLink
from operations.streamlit_bridge import screen_context, sign_in, sign_out
home = Path(os.environ["SMARTORDER_WEB_HOME"]).resolve()
assert Path(settings.DATABASES["default"]["NAME"]).resolve() == home / "smartorder.sqlite3"
assert Path(settings.MEDIA_ROOT).resolve() == home / "uploads"
assert not settings.SMARTORDER_ODOO_CONFIG
credentials = json.loads((home / "credentials.json").read_text(encoding="utf-8"))
assert get_user_model().objects.count() == 5
assert get_user_model().objects.filter(is_staff=True,is_active=True).count() == 2
marker = json.loads((home / "laboratorio.json").read_text(encoding="utf-8"))
assert Assignment.objects.count() == sum(len(clients) for clients in marker["portfolios"].values())
for name, password in credentials.items():
    client = Client()
    assert client.login(username=name,password=password), name
    session = sign_in(name,password)
    assert session
    native = client.get(reverse("dashboard"))
    shared = screen_context("dashboard",session)
    assert native.status_code == 200 and native.context_data["role"] == shared["role"]
    assert native.context_data["clients"] == shared["clients"]
    if name.startswith("lab_vendedor_"):
        assert "chart_monthly" not in shared and shared.get("dataset") is None
        assert client.get(reverse("operational_reports")).status_code == 403
    sign_out(session)
    client.logout()
for record in list(SalesDataset.objects.all()) + list(InventoryBatch.objects.all()):
    from hashlib import sha256
    with record.file.open("rb") as uploaded:
        assert sha256(uploaded.read()).hexdigest() == record.digest
admin = Client()
assert admin.login(username="lab_admin_01",password=credentials["lab_admin_01"])
admin_key = sign_in("lab_admin_01",credentials["lab_admin_01"])
for link in OdooOrderLink.objects.select_related("order"):
    native = admin.get(reverse("order_detail",args=[link.order_id]))
    shared = screen_context("order_detail",admin_key,order_id=link.order_id)
    assert native.status_code == 200 and native.context_data["odoo"] == shared["odoo"]
    from decimal import Decimal
    expected_balance = sum(Decimal(str(invoice.get("residual",0))) *
        (-1 if invoice.get("type") == "out_refund" else 1) for invoice in link.snapshot.get("invoices",[]) if invoice.get("state") == "posted")
    assert shared["odoo"]["admin_finance"]["balance"] == expected_balance
    for public, snapshot in zip(shared["odoo"]["lines"],link.snapshot.get("lines",[])):
        for field in ("ordered","produced","delivered","returned"):
            assert public[field] == snapshot[field]
sign_out(admin_key)
admin.logout()
print("PASS: restored Django and Streamlit sessions, two roles, portfolios, cached results/finance and all uploaded source bytes")
'''


def check(backup_argument=None):
    original_marker = LAB_HOME / "laboratorio.json"
    if not original_marker.is_file() or json.loads(original_marker.read_text(encoding="utf-8")).get("kind") != "SmartOrder synthetic laboratory":
        raise RuntimeError("Prepare the separate synthetic SmartOrder laboratory before backing it up")
    if backup_argument:
        backup = Path(backup_argument).resolve()
        if not backup.is_relative_to(BACKUPS.resolve()) or not (backup / "respaldo.json").is_file():
            raise RuntimeError("Only a completed backup inside .local-web-lab/backups is allowed")
    else:
        BACKUPS.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup.mkdir(exist_ok=False)
        # ponytail: SQLite online backup plus immutable uploaded sources; no archive dependency.
        # Files referenced by the snapshot must keep their recorded digest throughout copying.
        with sqlite3.connect((LAB_HOME / "smartorder.sqlite3").as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup / "smartorder.sqlite3") as destination:
                source.backup(destination)
        state = database_state((backup / "smartorder.sqlite3").resolve())
        files = []
        selected = [LAB_HOME / name for name in ("credentials.json", "secret.key", "laboratorio.json")]
        for folder in ("uploads", "fuentes_simuladas"):
            selected.extend(path for path in (LAB_HOME / folder).rglob("*") if path.is_file())
        for path in selected:
            if not path.resolve().is_relative_to(LAB_HOME.resolve()) or path.is_symlink():
                raise RuntimeError("A laboratory file points outside its dedicated directory")
            relative = path.relative_to(LAB_HOME)
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            value = digest(target)
            assert value == digest(path), f"File changed while backing up: {relative}"
            files.append({"path": relative.as_posix(), "sha256": value})
        for filename, expected in state["uploaded_sources"]:
            path = (backup / "uploads" / filename).resolve()
            assert path.is_relative_to((backup / "uploads").resolve())
            assert path.is_file() and digest(path) == expected, "Snapshot upload differs from its recorded source digest"
        manifest = {"kind": "SmartOrder synthetic laboratory backup", "source": str(LAB_HOME),
            "database_sha256": digest(backup / "smartorder.sqlite3"), "files": files, "state": state,
            "notice": "Contains only laboratory data; credentials are private. Uploaded sources are immutable snapshots."}
        (backup / "respaldo.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = json.loads((backup / "respaldo.json").read_text(encoding="utf-8"))
    if manifest.get("kind") != "SmartOrder synthetic laboratory backup" or manifest.get("source") != str(LAB_HOME):
        raise RuntimeError("Unrecognized laboratory backup")
    assert digest(backup / "smartorder.sqlite3") == manifest["database_sha256"], "Backup database bytes changed"
    for entry in manifest["files"]:
        path = (backup / entry["path"]).resolve()
        assert path.is_relative_to(backup), "Backup manifest contains an external file path"
        assert digest(path) == entry["sha256"], f"Backup file bytes changed: {entry['path']}"

    restore = LAB_HOME / "restauraciones" / (backup.name + "-" + uuid4().hex[:8])
    restore.mkdir(parents=True, exist_ok=False)
    shutil.copy2(backup / "smartorder.sqlite3", restore / "smartorder.sqlite3")
    for entry in manifest["files"]:
        target = (restore / entry["path"]).resolve()
        assert target.is_relative_to(restore.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / entry["path"], target)
    assert digest(restore / "smartorder.sqlite3") == manifest["database_sha256"]
    restored_state = database_state((restore / "smartorder.sqlite3").resolve())
    assert json.loads(json.dumps(restored_state)) == manifest["state"], "Restored data differs from backup snapshot"
    for entry in manifest["files"]:
        assert digest(restore / entry["path"]) == entry["sha256"]
    child_env = os.environ.copy()
    child_env.update(SMARTORDER_WEB_HOME=str(restore), SMARTORDER_DB=str(restore / "smartorder.sqlite3"),
        SMARTORDER_ODOO_CONFIG="", SMARTORDER_DEBUG="0", DJANGO_SETTINGS_MODULE="vitali_web.settings",
        PYTHONIOENCODING="utf-8")
    child_env.pop("SMARTORDER_TELEGRAM_BOT_TOKEN", None)
    child_env.pop("SMARTORDER_TELEGRAM_CHAT_ID", None)
    result = subprocess.run([sys.executable, "-c", RESTORED_CHECK], cwd=ROOT, env=child_env,
                            capture_output=True, encoding="utf-8", timeout=60)
    (restore / "comprobacion.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Restored application verification failed; inspect {restore / 'comprobacion.log'}")
    print(result.stdout.strip())
    assert database_state((restore / "smartorder.sqlite3").resolve()) == restored_state
    evidence = {"kind": "SmartOrder synthetic restore verification", "status": "passed", "backup": str(backup),
        "restored_home": str(restore), "source_home": str(LAB_HOME), "database_sha256_before_session_checks": manifest["database_sha256"],
        "file_count": len(manifest["files"]), "counts": restored_state["counts"],
        "checks": ["SQLite integrity", "Database snapshot and rows", "Uploads and configuration SHA-256",
                   "Five credentials", "Two roles and isolated portfolios", "Django and Streamlit restored sessions",
                   "Cached operational quantities and administrative financial balance"],
        "notice": "Active laboratory and business database were not replaced. Restored application checks had Odoo disabled."}
    (restore / "comprobacion.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    (LAB_HOME / "comprobacion-respaldo.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: SmartOrder backup and restored application; {len(manifest['files'])} files verified")
    print(f"Backup: {backup}")
    print(f"Restored copy preserved: {restore}")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", nargs="?", help="Optional existing backup under .local-web-lab/backups")
    check(parser.parse_args().backup)
