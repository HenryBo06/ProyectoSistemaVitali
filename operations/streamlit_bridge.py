"""Call the existing role-checked workflow from Streamlit without an HTTP server."""

from django.contrib.auth import authenticate, get_user, login, logout
from django.contrib.messages import get_messages
from django.contrib.messages.storage.session import SessionStorage
from django.contrib.sessions.backends.db import SessionStore
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, QueryDict
from django.urls import resolve, reverse
from django.utils.datastructures import MultiValueDict


def request_for(session_key=None):
    request = HttpRequest()
    request.META.update(HTTP_HOST="localhost", SERVER_NAME="localhost", SERVER_PORT="8501")
    request.session = SessionStore(session_key=session_key)
    request.user = get_user(request)
    request._messages = SessionStorage(request)
    return request


def sign_in(username, password, session_key=None):
    request = request_for(session_key)
    user = authenticate(request, username=username, password=password)
    if user is None:
        return None
    login(request, user)
    request.session.save()
    return request.session.session_key


def sign_out(session_key):
    logout(request_for(session_key))


def call_screen(name, session_key=None, *, data=None, files=None, local_setup=False,
                home_url=None, **route):
    request = request_for(session_key)
    if name == "setup" and not (local_setup or len(settings.SMARTORDER_SETUP_CODE) >= 32):
        raise PermissionDenied("La cuenta inicial requiere un código privado de al menos 32 caracteres.")
    request.META["REMOTE_ADDR"] = "127.0.0.1" if local_setup else ""
    request.smartorder_home_url = home_url
    request.path = request.path_info = reverse(name, kwargs=route)
    request.resolver_match = resolve(request.path)
    request.method = "POST" if data is not None else "GET"
    values = QueryDict(mutable=True)
    for key, value in (data or {}).items():
        if value is not None and value is not False:
            values.setlist(key, [str(item) for item in value] if isinstance(value, list)
                           else [str(value)])
    request.POST = values if data is not None else QueryDict()
    request.GET = QueryDict()
    request.FILES = MultiValueDict({key: [value] for key, value in (files or {}).items()})
    response = request.resolver_match.func(request, **route)
    notices = [(message.level_tag, str(message)) for message in get_messages(request)]
    if request.session.modified:
        request.session.save()
    return response, notices, request.session.session_key


def screen_context(name, session_key, params=None, **route):
    request = request_for(session_key)
    request.path = request.path_info = reverse(name, kwargs=route)
    request.resolver_match = resolve(request.path)
    request.method = "GET"
    values = QueryDict(mutable=True)
    for key, value in (params or {}).items():
        values[key] = str(value)
    request.GET = values
    response = request.resolver_match.func(request, **route)
    return getattr(response, "context_data", {})
