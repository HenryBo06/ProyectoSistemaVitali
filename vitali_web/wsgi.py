"""WSGI entry point for SmartOrder AI."""

import os

from django.core.wsgi import get_wsgi_application


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vitali_web.settings")
application = get_wsgi_application()
