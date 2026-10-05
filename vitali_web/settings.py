"""Settings for the local Django installation of SmartOrder AI."""

from pathlib import Path
import os
import secrets


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
MEDIA_ROOT = WEB_HOME / "uploads"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"

# ponytail: a configurable booking horizon, independent from the seven-day forecast.
SMARTORDER_DELIVERY_DAYS = int(os.environ.get("SMARTORDER_DELIVERY_DAYS", "365"))
SMARTORDER_SELLER_EDIT = os.environ.get("SMARTORDER_SELLER_EDIT", "1") == "1"
SMARTORDER_SELLER_CANCEL = os.environ.get("SMARTORDER_SELLER_CANCEL", "1") == "1"
SMARTORDER_ODOO_CONFIG = os.environ.get("SMARTORDER_ODOO_CONFIG", BASE_DIR / '.local-odoo' / 'smartorder.json')
