"""Admin-controlled connections to external PostgreSQL, never arbitrary SQL."""

from __future__ import annotations

import os
import re

import psycopg
from django.utils import timezone

from .models import ExternalDatabaseConnection


HOST_RE = re.compile(r"^[A-Za-z0-9.:-]+$")
SECRET_RE = re.compile(r"^SMARTORDER_SOURCE_[A-Z0-9_]+_PASSWORD$")


def validate_connection_fields(values):
    """Return sanitized metadata only; passwords are always secret references."""
    alias = values.get("alias", "").strip()
    name = values.get("name", "").strip()
    host = values.get("host", "").strip().lower()
    database = values.get("database", "").strip()
    username = values.get("username", "").strip()
    password_env = values.get("password_env", "").strip()
    purpose = values.get("purpose", "sales")
    sslmode = values.get("sslmode", "require")
    try:
        port = int(values.get("port", 5432))
    except (ValueError, TypeError) as exc:
        raise ValueError("El puerto debe ser un número válido.") from exc
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,47}", alias):
        raise ValueError("El alias necesita 3–48 caracteres en minúsculas, números o guiones.")
    if not name or len(name) > 100:
        raise ValueError("Indique un nombre de conexión de hasta 100 caracteres.")
    if not host or len(host) > 253 or not HOST_RE.fullmatch(host) or ".." in host:
        raise ValueError("Indique un servidor o dirección IP válida, sin protocolo ni ruta.")
    if not 1 <= port <= 65535:
        raise ValueError("El puerto está fuera del intervalo permitido.")
    if not database or len(database) > 128 or any(c.isspace() for c in database):
        raise ValueError("Indique el nombre de la base, sin espacios.")
    if not username or len(username) > 128 or any(c.isspace() for c in username):
        raise ValueError("Indique un usuario de solo lectura, sin espacios.")
    if not SECRET_RE.fullmatch(password_env) or len(password_env) > 100:
        raise ValueError("La contraseña debe referenciar SMARTORDER_SOURCE_<NOMBRE>_PASSWORD.")
    if purpose not in ExternalDatabaseConnection.Purpose.values:
        raise ValueError("Seleccione un propósito válido.")
    if sslmode not in {"prefer", "require", "verify-full"}:
        raise ValueError("Seleccione un modo TLS admitido.")
    if sslmode == "prefer" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Una base remota requiere TLS (require o verify-full).")
    return {"alias": alias, "name": name, "host": host, "port": port,
            "database": database, "username": username,
            "password_env": password_env, "purpose": purpose, "sslmode": sslmode,
            "engine": "postgresql"}


def test_postgres_readonly(item: ExternalDatabaseConnection) -> bool:
    """One bounded SELECT in a read-only transaction. Never expose a DSN/error."""
    allowed = {host.strip().lower() for host in os.environ.get(
        "SMARTORDER_SOURCE_DB_ALLOWED_HOSTS",
        "localhost,127.0.0.1,::1").split(",") if host.strip()}
    if item.host.lower() not in allowed:
        raise ValueError("Servidor no autorizado. Configure la lista privada de servidores permitidos.")
    password = os.environ.get(item.password_env)
    if not password:
        raise ValueError("Credencial privada no configurada en el servidor.")
    try:
        with psycopg.connect(
            host=item.host, port=item.port, dbname=item.database,
            user=item.username, password=password,
            sslmode=item.sslmode, connect_timeout=5, autocommit=True,
            options="-c default_transaction_read_only=on -c statement_timeout=3000",
        ) as conn:
            # Statement and server session are read only. This does NOT replace
            # a DB role limited to SELECT by the owning DBA.
            conn.execute("BEGIN READ ONLY")
            result = conn.execute("SELECT 1").fetchone()
            conn.execute("ROLLBACK")
        if result != (1,):
            raise ValueError("La respuesta de la base de datos no fue válida.")
        return True
    except (psycopg.Error, OSError, TimeoutError) as exc:
        raise ValueError("No se pudo comprobar la conexión. Revise red, TLS y permisos.") from None


def mark_connection_test(item: ExternalDatabaseConnection, success: bool):
    item.last_test_ok = success
    item.last_tested_at = timezone.now()
    item.save(update_fields=["last_test_ok", "last_tested_at", "updated_at"])
