"""Informe estático offline de la auditoría ficticia, sin JS ni recursos externos."""
from __future__ import annotations

from html import escape


def _percent(value):
    if value is None:
        return "Sin datos"
    return f"{100 * value:.2f} %"


def _bar(value, accent="indigo"):
    if value is None:
        return '<span class="muted">Sin datos evaluables</span>'
    width = min(100, max(0, round(value * 100, 2)))
    return (
        f'<div class="meter" role="img" aria-label="{width:.2f} por ciento">'
        f'<span class="{accent}" style="width:{width:.2f}%"></span>'
        "</div>"
    )


def render_html(report: dict) -> str:
    """Solo agrega estadísticos; jamás muestra filas de venta ni credenciales."""
    q = report["calidad"]
    perf = report["desempeno_observado_segundos"]
    scores = report["priorizacion_retroactiva"]
    s7 = scores["7"]["total"]
    s30 = scores["30"]["total"]
    a, b = s7["hit1_adaptativo"], s7["hit1_historico"]
    lift = f"{(a - b) * 100:+.2f} pp" if a is not None and b is not None else "No estimable"
    saturation = (
        s30["hit1_adaptativo"] is not None and
        s30["hit1_historico"] is not None and
        s30["hit1_adaptativo"] >= .95 and s30["hit1_historico"] >= .95
    )
    pattern_rows = []
    for pattern, metric in scores["7"]["por_patron"].items():
        cases = metric["escenarios_evaluados"]
        pattern_rows.append(
            "<tr><th scope='row'>" + escape(pattern.replace("_", " ")) + "</th>"
            f"<td>{cases}</td>"
            f"<td>{_percent(metric['hit1_adaptativo'])}{_bar(metric['hit1_adaptativo'])}</td>"
            f"<td>{_percent(metric['hit1_historico'])}{_bar(metric['hit1_historico'], 'slate')}</td>"
            f"<td>{metric['cambios_a_metodo_auditado']}</td></tr>"
        )
    quantity_rows = []
    for unit, stats in report["cantidades_por_transaccion_y_unidad"].items():
        median_value = stats["mae_mediana_4"]
        last_value = stats["mae_ultima"]
        quantity_rows.append(
            f"<tr><th scope='row'>{escape(unit)}</th>"
            f"<td>{stats['transacciones_evaluadas']}</td>"
            f"<td>{median_value if median_value is not None else 'N/D'}</td>"
            f"<td>{last_value if last_value is not None else 'N/D'}</td></tr>"
        )
    anomalies = "".join(
        f"<li><span>{escape(name.replace('_', ' '))}</span><strong>{count}</strong></li>"
        for name, count in q["ocho_anomalias_aisladas_en_cuarentena"].items()
    )
    units = "".join(
        f"<span class='token'>{escape(unit)}: {count:,} filas ficticias</span>"
        for unit, count in q["conteo_por_unidad"].items()
    )
    warnings = "".join(f"<li>{escape(note)}</li>" for note in report["limitaciones"])
    sat = (
        "<p class='warning'>Alerta de saturación: ambos métodos alcanzan un acierto muy alto "
        "en 30 días. Este horizonte no distingue bien los métodos con estos datos inventados.</p>"
        if saturation else
        "<p class='muted'>La ventana de 30 días es un control secundario, no una garantía de demanda.</p>"
    )
    return """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>SmartOrder | Laboratorio sintético</title>
<style>
:root{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1b2540;background:#f5f7fb}
*{box-sizing:border-box}body{margin:0}main{max-width:1140px;margin:auto;padding:36px 22px 80px}
header{background:#162342;color:white;padding:40px 0}header section{max-width:1140px;margin:auto;padding:0 22px}
h1{font-size:clamp(1.6rem,4vw,2.5rem);margin:.45rem 0}h2{font-size:1.22rem}
p{line-height:1.55}header p{color:#dbe5fa}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:.8rem}
.label{display:block;color:#63708a;font-size:.85rem}.kpi{font-weight:760;font-size:clamp(1.65rem,4vw,2.1rem);margin:.5rem 0}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}
.card,.panel{background:white;border:1px solid #e0e6f0;border-radius:15px;padding:21px}
.panel{margin:16px 0}.muted{color:#657089}.warning{padding:14px;border:1px solid #ddbf80;border-radius:10px;background:#fff9e9;color:#6b4d13}
table{border-collapse:collapse;width:100%;min-width:640px;text-align:left;font-size:.9rem}
.table-scroll{overflow-x:auto}th,td{padding:12px 10px;border-bottom:1px solid #edf0f5;vertical-align:top}
thead{background:#f6f8fc;color:#394563}th{font-weight:600}
.meter{height:7px;background:#e9edf7;border-radius:9px;overflow:hidden;margin:8px 0 0}
.meter span{display:block;height:100%;border-radius:9px}.indigo{background:#396ee3}.slate{background:#8493a9}
.tokens{display:flex;flex-wrap:wrap;gap:8px}.token{padding:7px 11px;border:1px solid #d7e3f3;border-radius:25px;background:#f2f7ff;font-size:.83rem}
.anomalies{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,1fr);gap:9px}
.anomalies li{display:flex;justify-content:space-between;gap:10px;background:#f5f7fa;border-radius:9px;padding:10px;font-size:.86rem}
small{color:#8291af}code{overflow-wrap:anywhere}.foot{margin-top:28px;font-size:.85rem}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.anomalies{grid-template-columns:1fr}}
@media(max-width:440px){.cards{grid-template-columns:1fr}main{padding:22px 12px}}
@media print{header{background:white;color:#142244}header p{color:#374968}body{background:white}.panel,.card{break-inside:avoid}}
</style></head><body>
<header><section><div class="eyebrow">SmartOrder AI · Evidencia reproducible</div>
<h1>Laboratorio comercial sintético</h1>
<p>NO SON DATOS EMPRESARIALES. Todos los clientes, productos, precios y movimientos son ficticios.
Ningún resultado autoriza pedidos automáticos, entregas ni producción.</p>
</section></header><main>
<div class="cards">
<div class="card"><span class="label">Transacciones ficticias</span><div class="kpi">""" + f"{q['filas_validas']:,}" + """</div><span class="muted">Fuente generada localmente</span></div>
<div class="card"><span class="label">Clientes / productos</span><div class="kpi">""" + f"{q['clientes_distintos']} / {q['productos_distintos']}" + """</div><span class="muted">Catálogo totalmente inventado</span></div>
<div class="card"><span class="label">Hit@1 adaptativo a 7 días</span><div class="kpi">""" + _percent(a) + """</div><span class="muted">Condicional a compras observadas</span></div>
<div class="card"><span class="label">Diferencia frente a histórico</span><div class="kpi">""" + escape(lift) + """</div><span class="muted">Puntos porcentuales, no causalidad</span></div>
</div>
<section class="panel"><h2>Comparación temporal a 7 y 30 días</h2>
<p>El 65 % inicial del calendario precede a las evaluaciones. En cada corte se utilizan solo
registros anteriores; se excluyen horizontes sin compras observadas.</p>
<div class="table-scroll"><table>
<thead><tr><th scope="col">Ventana</th><th scope="col">Casos evaluados</th><th scope="col">Adaptativo</th><th scope="col">Histórico</th><th scope="col">Reciente</th><th scope="col">Azar esperado</th></tr></thead>
<tbody>""" + "".join(
    f"<tr><th>{days} días</th><td>{scores[days]['total']['escenarios_evaluados']}</td>"
    f"<td>{_percent(scores[days]['total']['hit1_adaptativo'])}</td>"
    f"<td>{_percent(scores[days]['total']['hit1_historico'])}</td>"
    f"<td>{_percent(scores[days]['total']['hit1_reciente'])}</td>"
    f"<td>{_percent(scores[days]['total']['aleatorio_esperado'])}</td></tr>"
    for days in ("7", "30")
) + """</tbody></table></div>""" + sat + """</section>
<section class="panel"><h2>Ocho escenarios · resultado a siete días</h2>
<div class="table-scroll"><table><thead><tr>
<th>Patrón sintético</th><th>Casos</th><th>Adaptativo</th><th>Histórico</th><th>Cambios auditados</th>
</tr></thead><tbody>""" + "".join(pattern_rows) + """</tbody></table></div></section>
<section class="panel"><h2>Cantidades por transacción (NO demanda diaria)</h2>
<p>Errores absolutos medios de mediana de cuatro transacciones y último valor.
No se agregan magnitudes físicamente incompatibles.</p>
<div class="tokens">""" + units + """</div>
<div class="table-scroll"><table><thead><tr><th>Presentación</th><th>Transacciones evaluadas</th><th>MAE mediana</th><th>MAE última compra</th></tr></thead>
<tbody>""" + "".join(quantity_rows) + """</tbody></table></div></section>
<section class="panel"><h2>Calidad y pruebas adversariales</h2>
<p>Ocho entradas malformadas fueron rechazadas en un lote separado. La ocupación de celdas
no demuestra cobertura empresarial ni permite imputar ceros a días sin registro.</p>
<ul class="anomalies">""" + anomalies + """</ul>
<p class="muted">Celdas cliente-producto-fecha observadas: """ + str(q["celdas_cliente_producto_fecha_con_venta"]) + """.
Ocupación observacional: """ + f"{q['ocupacion_registros_no_equivale_cobertura'] * 100:.2f} %" + """.</p></section>
<section class="panel"><h2>Reproducibilidad y límites</h2>
<p>Semilla: <strong>""" + str(report["parametros"]["semilla"]) + """</strong>.
Corte de fecha exclusivo: <strong>""" + escape(report["parametros"]["fecha_corte_exclusiva"]) + """</strong>.
Generación y análisis medidos: <strong>""" + str(perf["total"]) + """ segundos</strong>
en este entorno. La prueba de volumen solo puntúa los clientes declarados en su informe.</p>
<p>Huella SHA-256 del CSV limpio:</p><code>""" + escape(report["huella_csv_sha256"]) + """</code>
<ul>""" + warnings + """</ul></section>
<p class="foot muted">LABORATORIO 100 % SIMULADO · No representa desempeño comercial de Vitali ni valida unidades, cobertura o demanda reales.</p>
</main></body></html>"""
