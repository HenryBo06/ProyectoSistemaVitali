"""Usuarios, fuente activa y decisiones en una base SQLite local."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from pathlib import Path
import os
import re
import sqlite3

from .orders import OperationalInput


ITERATIONS = 600_000
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'vendedor')),
                    active INTEGER NOT NULL DEFAULT 1,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    client TEXT NOT NULL,
                    PRIMARY KEY(user_id, client)
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    digest TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    first_date TEXT NOT NULL,
                    last_date TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    loaded_by INTEGER REFERENCES users(id),
                    loaded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operational_inputs (
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    client TEXT NOT NULL,
                    product TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    stock TEXT NOT NULL,
                    pending TEXT NOT NULL,
                    target_stock TEXT NOT NULL,
                    minimum TEXT NOT NULL,
                    pack_multiple TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, client, product)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    dataset_digest TEXT NOT NULL,
                    client TEXT NOT NULL,
                    product TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    forecast_date TEXT NOT NULL,
                    forecast REAL NOT NULL,
                    suggested REAL NOT NULL,
                    approved REAL NOT NULL,
                    stock REAL NOT NULL,
                    pending REAL NOT NULL,
                    target_stock REAL NOT NULL,
                    minimum REAL NOT NULL,
                    pack_multiple REAL NOT NULL,
                    stock_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _hash(password: str, salt: bytes) -> bytes:
        return pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)

    @staticmethod
    def _validate_credentials(username: str, password: str) -> None:
        if not USERNAME.fullmatch(username):
            raise ValueError("Usuario: 3–32 caracteres; letras, números, punto, guion o guion bajo.")
        if len(password) < 12:
            raise ValueError("La contraseña debe tener al menos 12 caracteres.")

    def has_users(self) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def bootstrap_admin(self, username: str, password: str) -> int:
        self._validate_credentials(username, password)
        salt = os.urandom(16)
        with self._db() as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise PermissionError("El administrador inicial ya existe.")
            result = db.execute(
                "INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,'admin')",
                (username, salt, self._hash(password, salt)),
            )
            return int(result.lastrowid)

    def authenticate(self, username: str, password: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if row is None or not row["active"]:
                return None
            now = datetime.now(timezone.utc)
            if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
                return None
            if not compare_digest(self._hash(password, row["salt"]), row["password_hash"]):
                attempts = row["failed_attempts"] + 1
                locked = (now + timedelta(minutes=5)).isoformat() if attempts >= 5 else None
                db.execute(
                    "UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?",
                    (0 if locked else attempts, locked, row["id"]),
                )
                return None
            db.execute(
                "UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?", (row["id"],)
            )
            return {"id": row["id"], "username": row["username"], "role": row["role"]}

    def user(self, user_id: int) -> dict | None:
        with self._db() as db:
            row = db.execute(
                "SELECT id,username,role,active FROM users WHERE id=?", (user_id,)
            ).fetchone()
            return dict(row) if row and row["active"] else None

    def _require_admin(self, db: sqlite3.Connection, actor_id: int) -> None:
        row = db.execute("SELECT role,active FROM users WHERE id=?", (actor_id,)).fetchone()
        if row is None or row["role"] != "admin" or not row["active"]:
            raise PermissionError("Esta acción requiere una cuenta administradora.")

    def create_user(self, actor_id: int, username: str, password: str, role: str) -> int:
        self._validate_credentials(username, password)
        if role not in ("admin", "vendedor"):
            raise ValueError("Rol inválido.")
        salt = os.urandom(16)
        with self._db() as db:
            self._require_admin(db, actor_id)
            result = db.execute(
                "INSERT INTO users(username,salt,password_hash,role) VALUES(?,?,?,?)",
                (username, salt, self._hash(password, salt), role),
            )
            return int(result.lastrowid)

    def list_users(self, actor_id: int) -> list[dict]:
        with self._db() as db:
            self._require_admin(db, actor_id)
            return [dict(row) for row in db.execute(
                "SELECT id,username,role,active FROM users ORDER BY username"
            )]

    def set_user_active(self, actor_id: int, target_id: int, active: bool) -> None:
        if actor_id == target_id and not active:
            raise ValueError("No puede desactivar su propia cuenta.")
        with self._db() as db:
            self._require_admin(db, actor_id)
            db.execute("UPDATE users SET active=? WHERE id=?", (int(active), target_id))

    def reset_password(self, actor_id: int, target_id: int, password: str) -> None:
        if len(password) < 12:
            raise ValueError("La contraseña debe tener al menos 12 caracteres.")
        salt = os.urandom(16)
        with self._db() as db:
            self._require_admin(db, actor_id)
            db.execute(
                "UPDATE users SET salt=?,password_hash=?,failed_attempts=0,locked_until=NULL WHERE id=?",
                (salt, self._hash(password, salt), target_id),
            )

    def set_assignments(self, actor_id: int, target_id: int, clients: list[str]) -> None:
        with self._db() as db:
            self._require_admin(db, actor_id)
            db.execute("DELETE FROM assignments WHERE user_id=?", (target_id,))
            db.executemany(
                "INSERT INTO assignments(user_id,client) VALUES(?,?)",
                [(target_id, client) for client in sorted(set(clients))],
            )

    def allowed_clients(self, user_id: int) -> list[str]:
        with self._db() as db:
            row = db.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None or not row["active"]:
                return []
            if row["role"] == "admin":
                return []
            return [record["client"] for record in db.execute(
                "SELECT client FROM assignments WHERE user_id=? ORDER BY client", (user_id,)
            )]

    def activate_dataset(self, actor_id: int, digest: str, path: str, filename: str,
                         first_date: str, last_date: str, row_count: int) -> None:
        with self._db() as db:
            self._require_admin(db, actor_id)
            db.execute(
                "INSERT OR IGNORE INTO datasets VALUES(?,?,?,?,?,?,?,?)",
                (digest, path, Path(filename).name, first_date, last_date, row_count,
                 actor_id, datetime.now(timezone.utc).isoformat()),
            )
            db.execute(
                "INSERT INTO settings(key,value) VALUES('active_dataset',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (digest,),
            )

    def active_dataset(self) -> dict | None:
        with self._db() as db:
            row = db.execute(
                "SELECT d.* FROM datasets d JOIN settings s ON s.value=d.digest "
                "WHERE s.key='active_dataset'"
            ).fetchone()
            return dict(row) if row else None

    def datasets(self, actor_id: int) -> list[dict]:
        with self._db() as db:
            self._require_admin(db, actor_id)
            return [dict(row) for row in db.execute(
                "SELECT digest,filename,first_date,last_date,row_count,loaded_at "
                "FROM datasets ORDER BY loaded_at DESC"
            )]

    def save_operational(self, user_id: int, value: OperationalInput) -> None:
        if value.client not in self.allowed_clients(user_id):
            raise PermissionError("Cliente no asignado al vendedor.")
        with self._db() as db:
            db.execute("""
                INSERT INTO operational_inputs VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,client,product) DO UPDATE SET
                    unit=excluded.unit,stock=excluded.stock,pending=excluded.pending,
                    target_stock=excluded.target_stock,minimum=excluded.minimum,
                    pack_multiple=excluded.pack_multiple,as_of=excluded.as_of,
                    updated_at=excluded.updated_at
            """, (user_id, value.client, value.product, value.unit, str(value.stock),
                  str(value.pending), str(value.target_stock), str(value.minimum),
                  str(value.pack_multiple), value.as_of.isoformat(),
                  datetime.now(timezone.utc).isoformat()))

    def operational(self, user_id: int, client: str, product: str) -> dict | None:
        if client not in self.allowed_clients(user_id):
            raise PermissionError("Cliente no asignado al vendedor.")
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM operational_inputs WHERE user_id=? AND client=? AND product=?",
                (user_id, client, product),
            ).fetchone()
            return dict(row) if row else None

    def save_decision(self, user_id: int, *, dataset_digest: str, client: str,
                      product: str, forecast_date: date, forecast: float,
                      suggested: float, approved: float, operational: OperationalInput,
                      reason: str) -> None:
        if client not in self.allowed_clients(user_id):
            raise PermissionError("Cliente no asignado al vendedor.")
        if not 0 <= approved < float("inf"):
            raise ValueError("La cantidad aprobada debe ser finita y no negativa.")
        if operational.as_of > forecast_date:
            raise ValueError("El inventario no puede observarse después del período pronosticado.")
        if approved != suggested and not reason.strip():
            raise ValueError("Explique por qué modificó la sugerencia.")
        with self._db() as db:
            db.execute("""
                INSERT INTO decisions(user_id,dataset_digest,client,product,unit,
                    forecast_date,forecast,suggested,approved,stock,pending,
                    target_stock,minimum,pack_multiple,stock_date,reason,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (user_id, dataset_digest, client, product, operational.unit,
                  forecast_date.isoformat(), forecast, suggested, approved,
                  float(operational.stock), float(operational.pending),
                  float(operational.target_stock), float(operational.minimum),
                  float(operational.pack_multiple), operational.as_of.isoformat(),
                  reason.strip() or "Aceptado sin cambios",
                  datetime.now(timezone.utc).isoformat()))

    def decisions(self, actor_id: int) -> list[dict]:
        with self._db() as db:
            actor = db.execute("SELECT role,active FROM users WHERE id=?", (actor_id,)).fetchone()
            if actor is None or not actor["active"]:
                raise PermissionError("Sesión inválida.")
            query = """SELECT d.created_at AS fecha_decision,u.username AS usuario,
                d.client AS cliente,d.product AS producto,d.unit AS unidad,
                d.forecast_date AS fecha_pronostico,d.forecast AS pronostico,
                d.suggested AS sugerido,d.approved AS aprobado,d.reason AS motivo,
                d.dataset_digest,d.stock,d.pending,d.target_stock,d.minimum,
                d.pack_multiple,d.stock_date
                FROM decisions d JOIN users u ON u.id=d.user_id"""
            params: tuple = ()
            if actor["role"] != "admin":
                query += " WHERE d.user_id=?"
                params = (actor_id,)
            query += " ORDER BY d.created_at DESC,d.id DESC"
            return [dict(row) for row in db.execute(query, params)]
