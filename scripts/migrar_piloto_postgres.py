"""Copy the synthetic lab into independent PostgreSQL, then verify a pg_dump restore.

Does not switch or edit either existing SmartOrder installation or vitali_lab.
Run once with .venv-web Python after starting the private PostgreSQL cluster.
"""
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import quote

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / ".local-web-pilot"
SOURCE = ROOT / ".local-web-lab"
PG = Path(r"C:\Program Files\PostgreSQL\18\bin")
DATABASE = "smartorder_pilot"
ROLE = "smartorder_pilot"
RESTORE = "smartorder_pilot_restore"


def command(environment, *args):
    result = subprocess.run([sys.executable, str(ROOT / "manage.py"), *args], cwd=ROOT,
                            env=environment, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("Falló " + args[0] + ": " + result.stderr[-1800:])


def state(environment, target):
    command(environment, "dumpdata", "auth", "contenttypes", "sessions", "operations", "--all", "--output", str(target))
    rows = json.loads(target.read_text(encoding="utf-8"))
    # Content types / permissions are re-created by migrate using natural keys.
    significant = [row for row in rows if row["model"].startswith("operations.") or row["model"] in {"auth.user", "sessions.session"}]
    return sha256(json.dumps(significant, sort_keys=True, ensure_ascii=True).encode()).hexdigest(), len(significant)


def main():
    config_path = HOME / "postgres.json"
    resume = "--resume" in sys.argv
    if config_path.exists() and not resume:
        raise RuntimeError("Ya existe el piloto. No se sobrescribirá; consulte su evidencia o cree otro destino explícito.")
    private = json.loads((ROOT / ".local-odoo/credentials.json").read_text(encoding="utf-8"))
    connection = psycopg.connect(host="127.0.0.1", port=55432, dbname="postgres", user="postgres", password=private["postgres"], autocommit=True)
    password = secrets.token_urlsafe(36)
    if resume:
        from urllib.parse import urlsplit, unquote
        existing = json.loads(config_path.read_text(encoding="utf-8"))
        parsed = urlsplit(existing["url"])
        assert existing["database"] == DATABASE and parsed.username == ROLE and parsed.hostname == "127.0.0.1"
        password = unquote(parsed.password)
    with connection.cursor() as cursor:
        if not resume and cursor.execute("SELECT 1 FROM pg_database WHERE datname IN (%s,%s)", (DATABASE, RESTORE)).fetchone():
            raise RuntimeError("El destino ya existe. No se borrarán bases.")
        if not resume and cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,)).fetchone():
            raise RuntimeError("El rol ya existe. No se modificarán sus credenciales.")
        if not resume:
            cursor.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(ROLE), sql.Literal(password)))
            for database in (DATABASE, RESTORE):
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(ROLE)))
                cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
    HOME.mkdir(exist_ok=True)
    (HOME / "evidence").mkdir(exist_ok=True)
    # Keep even a partial migration configuration recoverable; never overwrite a destination.
    if not resume:
        config_path.write_text(json.dumps({"url": f"postgresql://{ROLE}:{quote(password, safe='')}@127.0.0.1:55432/{DATABASE}", "database": DATABASE}), encoding="utf-8")
        shutil.copytree(SOURCE / "uploads", HOME / "uploads")
        for name in ("secret.key", "credentials.json"):
            if (SOURCE / name).exists():
                shutil.copy2(SOURCE / name, HOME / name)
        with sqlite3.connect((SOURCE / "smartorder.sqlite3").as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(HOME / "source-copy.sqlite3") as target:
            source.backup(target)
    environment = {**os.environ, "SMARTORDER_WEB_HOME": str(HOME), "SMARTORDER_DB": str(HOME / "source-copy.sqlite3"), "SMARTORDER_DATABASE_URL": "", "SMARTORDER_ODOO_CONFIG": "", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    command(environment, "migrate", "--noinput")
    fixture = HOME / "source.json"
    command(environment, "dumpdata", "--all", "--natural-foreign", "--exclude", "contenttypes", "--exclude", "auth.permission", "--output", str(fixture))
    before = state(environment, HOME / "evidence/source-state.json")
    environment["SMARTORDER_DATABASE_URL"] = json.loads(config_path.read_text())["url"]
    command(environment, "migrate", "--noinput")
    with psycopg.connect(environment["SMARTORDER_DATABASE_URL"]) as target:
        assert target.execute("SELECT COUNT(*) FROM auth_user").fetchone()[0] == 0, "Destino no vacío: no se sobrescribirá"
    command(environment, "loaddata", str(fixture))
    after = state(environment, HOME / "evidence/postgres-state.json")
    assert before == after, "La copia migrada no concilia con SQLite"
    pg_env = {**os.environ, "PGPASSWORD": password}
    dump = HOME / "backup.dump"
    subprocess.run([str(PG / "pg_dump.exe"), "-h", "127.0.0.1", "-p", "55432", "-U", ROLE, "-d", DATABASE, "-Fc", "-f", str(dump)], env=pg_env, check=True, capture_output=True)
    subprocess.run([str(PG / "pg_restore.exe"), "-h", "127.0.0.1", "-p", "55432", "-U", ROLE, "-d", RESTORE, "--no-owner", "--exit-on-error", str(dump)], env=pg_env, check=True, capture_output=True)
    environment["SMARTORDER_DATABASE_URL"] = environment["SMARTORDER_DATABASE_URL"].rsplit("/", 1)[0] + "/" + RESTORE
    restored = state(environment, HOME / "evidence/restored-state.json")
    assert after == restored, "La restauración no concilia"
    originals = {str(p.relative_to(SOURCE / "uploads")): sha256(p.read_bytes()).hexdigest() for p in (SOURCE / "uploads").rglob("*") if p.is_file()}
    assert all(sha256((HOME / "uploads" / name).read_bytes()).hexdigest() == digest for name, digest in originals.items())
    evidence = {"status": "passed", "created_at": datetime.now().isoformat(), "source": str(SOURCE), "database": DATABASE,
                "restored_database": RESTORE, "records": before[1], "state_digest": before[0], "files": len(originals), "originals_preserved": True}
    (HOME / "evidence/migration.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    connection.close()
    print(json.dumps(evidence, ensure_ascii=True))


if __name__ == "__main__":
    main()
