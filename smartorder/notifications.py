"""Avisos internos opcionales; se intentan únicamente al usar la aplicación."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def notify_internal(event: str, link: str) -> tuple[bool, str]:
    """Enviar resumen sin detalles comerciales si hay un bot configurado."""
    token = os.environ.get("SMARTORDER_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("SMARTORDER_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Telegram no configurado."
    if event not in {"Pedido pendiente de revisión", "Pedido revisado", "Corrección de inventario pendiente"}:
        return False, "Tipo de aviso inválido."
    text = f"SmartOrder AI: {event}. Consulte {link}"
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=4) as response:
            result = json.load(response)
        if result.get("ok") is True:
            return True, "Aviso enviado."
        return False, "Telegram rechazó el aviso."
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return False, "No se pudo enviar el aviso."
