"""Settings for the local Django installation of SmartOrder AI."""

from pathlib import Path
import os
import secrets
from urllib.parse import urlsplit, unquote, parse_qs
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
WEB_HOME = Path(os.environ.get("SMARTORDER_WEB_HOME", BASE_DIR / ".local-web")).resolve()
WEB_HOME.mkdir(parents=True, exist_ok=True)


def _local_secret() -> str:
    override = os.environ.get("SMARTORDER_SECRET_KEY")
    if override:
        return override
    key_file = WEB_HOME / "secret.key"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        # ponytail: a single local process creates the secret once; external hosting
        # must set SMARTORDER_SECRET_KEY for shared workers and managed secrets.
        key = secrets.token_urlsafe(64)
        try:
            with key_file.open("x", encoding="utf-8") as output:
                output.write(key)
        except FileExistsError:
            return key_file.read_text(encoding="utf-8").strip()
        return key


SECRET_KEY = _local_secret()
DEBUG = os.environ.get("SMARTORDER_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "[::1]", "testserver"]
SMARTORDER_SETUP_CODE = os.environ.get("SMARTORDER_SETUP_CODE", "").strip()

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "operations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "vitali_web.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "vitali_web.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("SMARTORDER_DB", WEB_HOME / "smartorder.sqlite3")),
        "OPTIONS": {"timeout": 15},
    }
}
database_url = os.environ.get("SMARTORDER_DATABASE_URL", "")
if database_url:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ImproperlyConfigured("SMARTORDER_DATABASE_URL debe identificar una base PostgreSQL.")
    options = parse_qs(parsed.query, strict_parsing=True)
    if set(options) - {"sslmode", "channel_binding"}:
        raise ImproperlyConfigured("La URL admite únicamente sslmode y channel_binding.")
    if any(len(values) != 1 for values in options.values()):
        raise ImproperlyConfigured("Los parámetros de conexión no deben repetirse.")
    if "sslmode" in options and options["sslmode"][0] not in {
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    }:
        raise ImproperlyConfigured("Valor sslmode no válido.")
    if "channel_binding" in options and options["channel_binding"][0] not in {
        "disable", "prefer", "require"
    }:
        raise ImproperlyConfigured("Valor channel_binding no válido.")
    connection_options = {"connect_timeout": 10}
    connection_options.update({name: values[0] for name, values in options.items()})
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql", "NAME": unquote(parsed.path[1:]),
        "USER": unquote(parsed.username or ""), "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname, "PORT": parsed.port or 5432,
        "CONN_MAX_AGE": 0, "OPTIONS": connection_options,
    }

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "operations.hashers.LegacySmartOrderHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es"
TIME_ZONE = "America/El_Salvador"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("SMARTORDER_MEDIA_ROOT", WEB_HOME / "uploads")).resolve()
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"

# ponytail: a configurable booking horizon, independent from the seven-day forecast.
SMARTORDER_DELIVERY_DAYS = int(os.environ.get("SMARTORDER_DELIVERY_DAYS", "365"))
SMARTORDER_SELLER_EDIT = os.environ.get("SMARTORDER_SELLER_EDIT", "1") == "1"
SMARTORDER_SELLER_CANCEL = os.environ.get("SMARTORDER_SELLER_CANCEL", "1") == "1"
SMARTORDER_MAINTENANCE_UI = os.environ.get("SMARTORDER_MAINTENANCE_UI", "0") == "1"
SMARTORDER_ODOO_CONFIG = os.environ.get("SMARTORDER_ODOO_CONFIG", BASE_DIR / '.local-odoo' / 'smartorder.json')
