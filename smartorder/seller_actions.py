"""Orientación comercial explicable para vendedor, sin automatizar contacto ni ventas.

El ranking y el pronóstico se calculan en otros módulos. Este asesor transforma
la evidencia segura y autorizada que recibe el vendedor en una siguiente acción
con guardas explícitas. Nunca inventa una probabilidad, precio, unidad ni stock.
"""
from __future__ import annotations

from typing import Any, Mapping


def suggested_seller_action(card: Mapping[str, Any]) -> dict[str, str]:
    """Devuelve una instrucción orientativa para UNA ficha comercial segura.

    Entrada: la lista blanca de _seller_cards, NO los registros financieros o
    el historial bruto. Salida: solo texto para el vendedor, nunca una orden.
    """
    mode = card.get("suggestion_mode", "sale")
    unit = str(card.get("unit") or "").strip()
    tariff_unit = str(card.get("order_unit") or "").strip()
    product = str(card.get("product") or "producto").strip()

    if mode == "client_restock":
        if card.get("client_stock") is None:
            return {
                "action_code": "verify_client_stock",
                "action_title": "Solicitar un conteo de existencias al cliente",
                "action_reason": "Falta un conteo vigente y compatible para calcular la reposición.",
                "action_checks": "Registrar ubicación, fecha y unidad; no generar una reposición automática.",
            }
        if card.get("client_stock_target") is None:
            return {
                "action_code": "verify_restock_target",
                "action_title": "Pedir que se confirme el objetivo de reposición",
                "action_reason": "Hay existencias registradas, pero no un objetivo aprobado para compararlas.",
                "action_checks": "Solicitar el objetivo a administración antes de proponer cantidades.",
            }
        if card.get("suggested") is not None:
            return {
                "action_code": "confirm_restock",
                "action_title": "Conversar con el cliente sobre la reposición",
                "action_reason": "El sistema encontró una diferencia entre existencias y objetivo.",
                "action_checks": "Validar unidad, movimientos pendientes y aceptación del cliente antes de registrar una solicitud.",
            }
        return {
            "action_code": "review_restock",
            "action_title": "Revisar existencias antes de preparar una reposición",
            "action_reason": "Los datos actuales no sustentan una cantidad automática de reposición.",
            "action_checks": "Confirmar conteos y movimientos con el cliente y administración.",
        }

    if card.get("unit_mismatch"):
        unit_message = (
            f"El historial usa {unit or 'una presentación no identificada'} "
            f"y la tarifa usa {tariff_unit or 'otra presentación'}."
        )
        return {
            "action_code": "resolve_unit",
            "action_title": "Confirmar la presentación comercial antes de cotizar",
            "action_reason": unit_message + " No se convierten kg, lb y unidad automáticamente.",
            "action_checks": "Consultar la presentación válida con administración y el cliente. No comprometer cantidad ni precio.",
        }

    rank = card.get("priority_rank") or card.get("historical_rank")
    total = card.get("historical_total")
    audited = card.get("priority_method") not in (None, "historical")
    history = bool(card.get("historical_sale_days"))
    event = str(card.get("suggestion_event") or "").strip()

    if audited and rank:
        evidence = (
            f"El historial del cliente ubicó a {product} en la posición "
            f"{rank} de {total or 'los productos evaluados'}, usando "
            "una comparación cronológica que superó a la referencia histórica."
        )
    elif history and rank:
        evidence = (
            f"El cliente tiene compras registradas de {product}; su orden de "
            f"revisión es {rank} de {total or 'los productos registrados'}."
        )
    elif history:
        evidence = f"Existen compras registradas de {product} para este cliente."
    else:
        evidence = f"No hay evidencia histórica suficiente para priorizar {product}."
    if event:
        evidence += f" La fecha consultada coincide con el contexto comercial: {event}."
    if card.get("commercial_forecast") is not None:
        evidence += (
            " Hay una referencia de oportunidad comercial, distinta de la "
            "cantidad comprometida para entrega."
        )
    elif card.get("suggested") is not None:
        evidence += (
            " Hay una referencia de entrega sujeta a confirmación comercial; "
            "no equivale a una venta aceptada."
        )
    else:
        evidence += (
            " No existe una cantidad automática validada para esa entrega."
        )
    evidence += " No es una garantía de compra."

    if history or card.get("commercial_forecast") is not None:
        title = "Contactar al cliente y confirmar interés en este producto"
        code = "offer_product"
    else:
        title = "Consultar si el cliente necesita este producto"
        code = "explore_interest"

    checks: list[str] = []
    if card.get("delivery_outside_forecast"):
        checks.append("La fecha excede el horizonte evaluado: estimar cantidad manualmente")
    if not card.get("unit_confirmed"):
        checks.append("Confirmar la unidad de venta (kg, lb o unidad)")
    if card.get("unit_price") is None:
        checks.append("Confirmar tarifa autorizada con administración")
    if card.get("available") is None or card.get("inventory_warning"):
        checks.append("Validar disponibilidad antes de prometer entrega")
    elif card.get("available") <= 0:
        checks.append("No prometer existencias; consultar reposición o alternativa")
    if card.get("suggested") is None:
        checks.append("Preguntar al cliente qué cantidad requiere")
    checks.append("El vendedor decide y confirma; la IA no crea pedidos ni contacta automáticamente")

    return {
        "action_code": code,
        "action_title": title,
        "action_reason": evidence,
        "action_checks": ". ".join(checks) + ".",
    }
