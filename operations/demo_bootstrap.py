"""Opt-in, SQLite-only test accounts for an ephemeral Streamlit demo.

Credentials are supplied privately by Streamlit Secrets, never by source code.
This intentionally does not seed production databases or modify existing users.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction


def provision_demo_accounts(*, enabled=False, admin_username="", admin_password="",
                            seller_username="", seller_password=""):
    """Create one staff admin and one seller only in an empty demo SQLite database."""
    if enabled is not True or settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        return False

    User = get_user_model()
    if User.objects.exists():
        return False

    values = (admin_username, admin_password, seller_username, seller_password)
    if any(not isinstance(value, str) or not value for value in values):
        raise ImproperlyConfigured(
            "La demo requiere los cuatro secretos de cuentas (usuarios y contraseñas)."
        )
    if (admin_username != admin_username.strip()
            or seller_username != seller_username.strip()
            or admin_username == seller_username):
        raise ImproperlyConfigured("Los usuarios de prueba deben ser distintos y válidos.")
    if admin_password == seller_password:
        raise ImproperlyConfigured("Las cuentas de prueba requieren contraseñas distintas.")

    try:
        validate_password(admin_password, user=User(username=admin_username))
        validate_password(seller_password, user=User(username=seller_username))
    except ValidationError as exc:
        raise ImproperlyConfigured(
            "Las contraseñas de prueba no cumplen la política de seguridad."
        ) from None

    # Never overwrite or reset an account. Streamlit invokes this once after
    # migrations, and a new ephemeral SQLite database can be initialized again.
    with transaction.atomic():
        if User.objects.exists():
            return False
        User.objects.create_user(
            username=admin_username, password=admin_password,
            is_staff=True, is_superuser=False,
        )
        User.objects.create_user(
            username=seller_username, password=seller_password,
            is_staff=False, is_superuser=False,
        )
    return True
