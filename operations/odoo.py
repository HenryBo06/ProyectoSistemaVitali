"""Local Odoo adapter shared by both interfaces; credentials never enter their contexts."""
from datetime import timezone as utc_timezone
from decimal import Decimal
from http.client import HTTPException
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import (InventoryAdjustment, InventoryItem, OdooCustomerMapping, OdooOrderLink,
                     OdooProductMapping, OrderLine, OrderRequest)


class OdooError(ValueError):
    pass


def configuration():
    path = settings.SMARTORDER_ODOO_CONFIG
    if not path or not Path(path).is_file():
        return {'enabled': False}
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict) or type(config.get('enabled', False)) is not bool:
            raise ValueError
    except (ValueError, OSError) as exc:
        raise OdooError('La configuración privada de Odoo no es válida.') from exc
    if config.get('enabled') and any(not isinstance(config.get(name), str) or not config[name].strip() for name in ('url', 'database', 'key')):
        raise OdooError('Complete la dirección, base y credencial en la configuración privada.')
    if config.get('enabled') and any(any(ord(char) < 32 for char in config[name]) for name in ('url', 'database', 'key')):
        raise OdooError('La configuración privada contiene caracteres no permitidos.')
    return config


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OdooError('Odoo redirigió la conexión; revise su dirección local.')


def rpc(method, **kwargs):
    config = configuration()
    if not config.get('enabled') or not config.get('key'):
        raise OdooError('La conexión con Odoo no está habilitada.')
    url = config.get('url', '').rstrip('/')
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'} or parsed.port == 0:
            raise ValueError
    except ValueError as exc:
        raise OdooError('Este laboratorio admite únicamente la dirección local de Odoo.') from exc
    if method not in {'ping', 'sync_sale', 'authorize_production', 'snapshot', 'cancel_sale'}:
        raise OdooError('Operación de integración inválida.')
    # ponytail: localhost only, no proxies or redirects; external hosting needs its own reviewed connection.
    try:
        request = Request(url + '/json/2/vitali.integration/' + method,
            data=json.dumps(kwargs, allow_nan=False).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + config['key'], 'X-Odoo-Database': config['database'],
                     'Content-Type': 'application/json'}, method='POST')
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise OdooError('La respuesta de Odoo supera el tamaño permitido.')
        result = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(result, dict):
            raise ValueError
        json.dumps(result, allow_nan=False)  # also rejects overflow such as valid JSON number 1e999
        return result
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise OdooError('Odoo rechazó la credencial o los permisos de integración.') from exc
        raise OdooError('Odoo rechazó la operación. Revise catálogo, unidades y ejecución; no se da por completada.') from exc
    except (URLError, TimeoutError, ConnectionError, HTTPException) as exc:
        raise OdooError('Odoo no respondió. La operación queda pendiente para reintentar sin duplicados.') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise OdooError('Odoo devolvió una respuesta inválida.') from exc


def sale_payload(order, link):
    if not order.sale_confirmed:
        raise OdooError('Este pedido anterior necesita una venta confirmada; no se enviará automáticamente.')
    customer = OdooCustomerMapping.objects.filter(client=order.client).first()
    if customer is None:
        raise OdooError('Administración debe asociar este cliente con su código Odoo.')
    lines = []
    for line in order.lines.order_by('pk'):
        mapping = OdooProductMapping.objects.filter(product=line.product).first()
        if mapping is None or mapping.source_unit != line.unit:
            raise OdooError('Administración debe verificar los códigos y unidades de todos los productos de la venta.')
        lines.append({'key': str(line.pk), 'sku': mapping.sku, 'quantity': float(line.requested_qty),
                      'unit_price': float(line.unit_price), 'discount': float(line.discount_percent), 'unit': mapping.unit})
    return {'reference': 'smartorder:' + str(link.reference), 'revision': link.revision,
            'customer': {'code': customer.code, 'name': order.client},
            'delivery_date': order.required_date.isoformat(), 'terms': order.payment_terms,
            'lines': lines}


def commercial_matches(order, link, result):
    """Compare the complete native sale before adopting a newer remote revision."""
    actual = result.get('commercial')
    if not isinstance(actual, dict):
        return False
    expected = sale_payload(order, link)
    try:
        actual = {key: value for key, value in actual.items() if key != 'revision'}
        expected.pop('revision')
        actual['lines'] = sorted(actual['lines'], key=lambda line: line['key'])
        expected['lines'] = sorted(expected['lines'], key=lambda line: line['key'])
        if any(isinstance(line.get(name), bool) for line in actual['lines'] for name in ('quantity', 'unit_price', 'discount')):
            return False
        return actual == expected
    except (KeyError, TypeError):
        return False


def accept_snapshot(link, result):
    if not isinstance(result, dict) or not isinstance(result.get('sale'), dict):
        raise OdooError('El seguimiento de Odoo no es válido.')
    sale = result['sale']
    if type(sale.get('id')) is not int or sale['id'] <= 0 or not isinstance(sale.get('name'), str) or not sale['name'].strip() or sale.get('state') not in {'draft', 'sent', 'sale', 'cancel'}:
        raise OdooError('Odoo no identificó una venta válida en el seguimiento.')
    lines = result.get('lines')
    if not isinstance(lines, list) or not 1 <= len(lines) <= 250:
        raise OdooError('El seguimiento de Odoo no es válido.')
    valid = {str(pk) for pk in link.order.lines.values_list('pk', flat=True)}
    keys = [str(line.get('key')) for line in lines if isinstance(line, dict)]
    if len(keys) != len(lines) or len(set(keys)) != len(keys) or set(keys) != valid:
        raise OdooError('El seguimiento no corresponde a las líneas de esta venta.')
    for row in lines:
        for name in ('ordered', 'reserved', 'produced', 'delivered', 'returned', 'scrapped',
                     *(name for name in ('net_delivered', 'outstanding', 'unit_cost', 'authorized_quantity') if name in row and (name != 'unit_cost' or row[name] is not None))):
            try:
                quantity = Decimal(str(row.get(name)))
                if not quantity.is_finite() or quantity < 0:
                    raise ValueError
            except (ValueError, ArithmeticError) as exc:
                raise OdooError('Odoo devolvió cantidades de seguimiento inválidas.') from exc
        if 'authorized' in row and type(row['authorized']) is not bool:
            raise OdooError('Odoo devolvió una autorización inválida.')
        if 'net_delivered' in row and abs(Decimal(str(row['net_delivered'])) -
                max(Decimal('0'), Decimal(str(row['delivered'])) - Decimal(str(row['returned'])))) > Decimal('0.000001'):
            raise OdooError('La entrega neta no concuerda con las devoluciones de Odoo.')
        try:
            if row.get('last_delivery_at') is not None and (not isinstance(row['last_delivery_at'], str) or not parse_datetime(row['last_delivery_at'])):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise OdooError('Odoo devolvió una fecha de entrega inválida.') from exc
    invoices = result.get('invoices', [])
    if not isinstance(invoices, list) or len(invoices) > 250:
        raise OdooError('Odoo devolvió facturas inválidas.')
    for invoice in invoices:
        if not isinstance(invoice, dict):
            raise OdooError('Odoo devolvió facturas inválidas.')
        for name in ('total', 'residual'):
            if name in invoice:
                try:
                    amount = Decimal(str(invoice[name]))
                    if not amount.is_finite() or amount < 0:
                        raise ValueError
                except (ValueError, ArithmeticError) as exc:
                    raise OdooError('Odoo devolvió importes de factura inválidos.') from exc
    reference = result.get('reference')
    if reference != 'smartorder:' + str(link.reference):
        raise OdooError('La respuesta no corresponde a esta venta.')
    if type(result.get('revision')) is not int or not 1 <= result['revision'] <= 2_147_483_647 or result['revision'] < link.revision:
        raise OdooError('La versión comercial en Odoo no coincide; administración debe conciliar la venta.')
    if not commercial_matches(link.order, link, result):
        raise OdooError('Los datos comerciales de Odoo difieren de la venta local. Si se perdió la respuesta de una edición, reenvíe esa misma edición; no se ha dado por actualizada ni se autorizará producción.')
    # ponytail: exact content plus CAS recovers an acknowledged revision; divergent edits require explicit retry.
    with transaction.atomic():
        updated = OdooOrderLink.objects.filter(pk=link.pk, revision=link.revision).update(
            revision=result['revision'], snapshot=result, status='synced', last_error='',
            last_synced=timezone.now(), updated_at=timezone.now())
        if not updated:
            raise OdooError('La venta cambió durante la conexión; actualice su revisión antes de continuar.')
        if result['sale'].get('state') == 'cancel':
            OrderRequest.objects.filter(pk=link.order_id).update(status=OrderRequest.Status.CANCELLED, updated_at=timezone.now())
            OrderLine.objects.filter(order_id=link.order_id).update(status=OrderLine.Status.REJECTED,
                review_note='Cancelación conciliada desde Odoo')
    link.revision = result['revision']
    return result


def synchronize(order, action='sync'):
    if action not in {'sync', 'refresh', 'cancel', 'authorize'}:
        raise OdooError('Operación de integración inválida.')
    link, _ = OdooOrderLink.objects.get_or_create(order=order)
    reference = 'smartorder:' + str(link.reference)
    try:
        if action == 'refresh':
            result = rpc('snapshot', reference=reference)
        elif action == 'cancel':
            result = rpc('cancel_sale', reference=reference, revision=link.revision)
            if not isinstance(result, dict) or not isinstance(result.get('sale'), dict) or result['sale'].get('state') != 'cancel':
                raise OdooError('Odoo no confirmó la cancelación; la venta local se conserva.')
        else:
            if order.status == OrderRequest.Status.CANCELLED:
                raise OdooError('Una venta cancelada no puede enviarse como venta activa.')
            result = rpc('sync_sale', payload=sale_payload(order, link))
            if action == 'authorize':
                accept_snapshot(link, result)
                if result['sale'].get('state') == 'cancel':
                    return result
                approved = [{'key': str(line.pk), 'quantity': float(line.approved_qty)}
                    for line in order.lines.filter(status__in=[OrderLine.Status.APPROVED, OrderLine.Status.EXPORTED])
                    if line.approved_qty is not None]
                if not approved:
                    raise OdooError('Primero autorice las cantidades de producción.')
                result = rpc('authorize_production', reference=reference, lines=approved, revision=link.revision)
        return accept_snapshot(link, result)
    except OdooError as exc:
        OdooOrderLink.objects.filter(pk=link.pk, revision=link.revision).update(
            status='error', last_error=str(exc)[:500], updated_at=timezone.now())
        raise


def try_synchronize(order, *, authorize=False):
    try:
        if configuration().get('enabled'):
            synchronize(order, 'authorize' if authorize else 'sync')
    except OdooError:
        # ponytail: persisted pending/error plus explicit retry, no worker dependency for this local lab.
        pass


def changed(order):
    OdooOrderLink.objects.filter(order=order).update(revision=F('revision') + 1, status='pending')


def stage(line):
    ordered = Decimal(str(line.get('ordered', 0)))
    net = max(Decimal('0'), Decimal(str(line.get('delivered', 0))) - Decimal(str(line.get('returned', 0))))
    if line.get('delivery_state') == 'cancel':
        return 'Cancelado'
    if net >= ordered and ordered > 0:
        return 'Entregado'
    if net > 0:
        return 'Entrega parcial'
    if Decimal(str(line.get('reserved', 0))) >= ordered and ordered > 0:
        return 'Para entregar'
    if line.get('manufacturing_state') in {'progress', 'to_close'}:
        return 'En producción'
    if line.get('manufacturing_state') == 'done':
        return 'Listo'
    if line.get('authorized') or line.get('manufacturing_state') in {'draft', 'confirmed'}:
        return 'Mandado a producir'
    return 'Pendiente de producción'


def delivery_moment(line):
    value = line.get('last_delivery_at')
    try:
        moment = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
    return timezone.make_aware(moment, utc_timezone.utc) if moment and timezone.is_naive(moment) else moment


def order_context(order, *, admin=False):
    try:
        enabled = bool(configuration().get('enabled'))
    except OdooError:
        enabled = False
    link = OdooOrderLink.objects.filter(order=order).first()
    snapshot = link.snapshot if link else {}
    originals = {str(line.pk): line for line in order.lines.all()}
    pending_authorizations = {key for key, line in originals.items()
        if line.status in {OrderLine.Status.APPROVED, OrderLine.Status.EXPORTED} and line.approved_qty is not None}
    public_lines = []
    for row in snapshot.get('lines', []):
        original = originals.get(str(row.get('key')))
        if original is None:
            continue
        if row.get('authorized') is True and original.approved_qty is not None and row.get('authorized_quantity') == float(original.approved_qty):
            pending_authorizations.discard(str(row.get('key')))
        net = max(Decimal('0'), Decimal(str(row.get('delivered', 0))) - Decimal(str(row.get('returned', 0))))
        public_lines.append({**{name: row.get(name) for name in
            ('key', 'ordered', 'reserved', 'produced', 'delivered', 'returned', 'scrapped',
             'manufacturing_state', 'delivery_state')}, 'product': original.product, 'unit': original.unit,
            'approved': original.approved_qty, 'stage': stage(row), 'net_delivered': net,
            'outstanding': max(Decimal('0'), Decimal(str(row.get('ordered', 0))) - net),
            'last_delivery_at': delivery_moment(row)})
    synced = bool(snapshot.get('sale'))
    stale = synced and (link.status != 'synced' or snapshot.get('revision') != link.revision)
    result = {'enabled': enabled, 'synced': synced, 'stale': stale,
              'status': 'Actualizado' if link and link.status == 'synced' and not stale else 'Pendiente de actualizar',
              'last_synced': link.last_synced if link else None, 'sale_name': snapshot.get('sale', {}).get('name', ''),
              'lines': public_lines, 'can_sync': admin and enabled and order.sale_confirmed,
              'can_refresh': admin and enabled and synced,
              'can_authorize': admin and enabled and not stale and order.sale_confirmed and bool(pending_authorizations),
              'can_cancel': admin and enabled and synced and all(not row.get('authorized') for row in snapshot.get('lines', []))}
    if admin:
        result['error'] = link.last_error if link else ''
        invoices = [{name: invoice.get(name) for name in ('name', 'state', 'total', 'residual', 'currency', 'type', 'payment_state')}
                    for invoice in snapshot.get('invoices', [])]
        for invoice in invoices:
            invoice['state_label'] = {'draft': 'Borrador', 'posted': 'Contabilizada', 'cancel': 'Cancelada'}.get(invoice['state'], 'Sin confirmar')
            invoice['type_label'] = {'out_invoice': 'Factura', 'out_refund': 'Nota de crédito'}.get(invoice['type'], 'Documento')
            invoice['payment_state_label'] = {'not_paid': 'Pendiente de cobro', 'partial': 'Cobro parcial',
                'in_payment': 'Cobro en proceso', 'paid': 'Cobrada', 'reversed': 'Revertida', 'blocked': 'Bloqueada'}.get(invoice['payment_state'], 'Sin confirmar')
            if invoice['type'] == 'out_refund' and invoice['payment_state'] == 'paid':
                invoice['payment_state_label'] = 'Conciliada'
        result['admin_finance'] = {'invoices': invoices, 'balance': sum(Decimal(str(row.get('residual', 0))) * (-1 if row.get('type') == 'out_refund' else 1) for row in invoices if row.get('state') == 'posted'),
                                  'currency': snapshot.get('currency', 'USD')}
    return result


def reports(from_date=None, to_date=None):
    rows, totals = [], {}
    links = OdooOrderLink.objects.select_related('order', 'order__dataset', 'order__inventory_batch').prefetch_related('order__lines').filter(status='synced').exclude(order__status=OrderRequest.Status.CANCELLED)
    if from_date:
        links = links.filter(order__required_date__gte=from_date)
    if to_date:
        links = links.filter(order__required_date__lte=to_date)
    for link in links:
        originals = {str(line.pk): line for line in link.order.lines.all()}
        for row in link.snapshot.get('lines', []):
            original = originals.get(str(row.get('key')))
            if original is None:
                continue
            quantities = {name: Decimal(str(row.get(name, 0))) for name in
                          ('ordered', 'reserved', 'produced', 'delivered', 'returned', 'scrapped')}
            net = max(Decimal('0'), quantities['delivered'] - quantities['returned'])
            pending = max(Decimal('0'), quantities['ordered'] - net)
            delivered_at = delivery_moment(row)
            on_time = timezone.localtime(delivered_at).date() <= link.order.required_date if delivered_at and pending == 0 else None
            unit_cost = row.get('unit_cost')
            margin = net * (original.unit_price * (1 - original.discount_percent / 100) - Decimal(str(unit_cost))) if unit_cost is not None else None
            rows.append({**quantities, 'order_id': link.order_id, 'client': link.order.client, 'product': original.product,
                         'unit': original.unit, 'pending': pending, 'net_delivered': net, 'stage': stage(row),
                         'suggested': original.suggested_qty, 'approved': original.approved_qty,
                         'production_difference': quantities['produced'] - original.suggested_qty if original.suggested_qty is not None else None,
                         'method': original.method, 'created_at': link.order.created_at,
                         'source': link.order.dataset.filename, 'source_digest': link.order.dataset.digest,
                         'source_from': link.order.dataset.first_date, 'source_to': link.order.dataset.last_date,
                         'inventory_source': link.order.inventory_batch.filename if link.order.inventory_batch_id else '',
                         'required_date': link.order.required_date, 'last_synced': link.last_synced,
                         'on_time': on_time, 'margin': margin})
            total = totals.setdefault(original.unit, {'unit': original.unit, 'ordered': Decimal('0'),
                'delivered': Decimal('0'), 'pending': Decimal('0'), 'scrapped': Decimal('0')})
            for name, value in [('ordered', quantities['ordered']), ('delivered', net), ('pending', pending), ('scrapped', quantities['scrapped'])]:
                total[name] += value
    for total in totals.values():
        total['fulfillment_percent'] = 100 * total['delivered'] / total['ordered'] if total['ordered'] else None
    adjustments = list(InventoryAdjustment.objects.select_related('batch', 'correction', 'approved_by').order_by('created_at', 'pk'))
    prior = {(item.batch_id, item.client, item.product): (item.available, item.unit)
             for item in InventoryItem.objects.filter(batch_id__in={change.batch_id for change in adjustments})}
    corrections, correction_totals = [], {}
    # ponytail: one linear pass preserves predecessors outside the period; large audit volumes can use SQL predecessor lookup.
    for change in adjustments:
        key = (change.batch_id, change.client, change.product)
        previous, unit = prior.get(key, (None, change.correction.unit))
        prior[key] = (change.available, unit)
        if (from_date and change.observed_on < from_date) or (to_date and change.observed_on > to_date):
            continue
        difference = change.available - previous if previous is not None else None
        corrections.append({'id': change.pk, 'client': change.client, 'product': change.product, 'unit': unit,
            'previous': previous, 'observed': change.available, 'difference': difference,
            'observed_on': change.observed_on, 'approved_at': change.created_at,
            'approved_by': change.approved_by.username, 'reason': change.correction.reason,
            'source': change.batch.filename, 'source_digest': change.batch.digest})
        total = correction_totals.setdefault(unit, {'unit': unit, 'count': 0, 'difference': Decimal('0')})
        total['count'] += 1
        total['difference'] = total['difference'] + difference if total['difference'] is not None and difference is not None else None
    return {'rows': rows, 'totals': list(totals.values()), 'from_date': from_date, 'to_date': to_date,
            'corrections': corrections, 'correction_totals': list(correction_totals.values()),
            'notice': 'Resultados de Odoo según última actualización. Sugerido es reposición/fabricación; producido menos sugerido describe la ejecución, no error de pronóstico ni beneficio real. Las fuentes son las conservadas al crear la venta. El período filtra entregas comprometidas y fechas observadas de correcciones. Las diferencias comparan lecturas consecutivas del inventario importado; no son conteos físicos ni pérdidas Odoo. Margen simulado con costo estándar; no contabilidad fiscal validada.'}
