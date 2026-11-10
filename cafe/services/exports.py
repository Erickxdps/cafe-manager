"""Exportación de informes a Excel (openpyxl) y PDF (reportlab), 100 % local.

Ambos formatos se generan a partir del MISMO diccionario que muestra la pantalla
(services.reports.report), por eso las cifras coinciden. El dinero se calcula en centavos
enteros; solo al escribir la celda se convierte a Decimal para mostrar bolivianos.
"""
import io
from datetime import datetime
from decimal import Decimal

from ..money import format_bs

# openpyxl y reportlab se importan dentro de to_excel/to_pdf: tardan segundos y solo hacen falta al descargar.

DISCLAIMER = ("Registro personal y orientativo; no sustituye el sistema oficial de ventas, caja ni inventario "
              "del establecimiento.")
MONEY_FMT = '"Bs" #,##0.00'
ESPRESSO, CREMA, LINEA = "241B16", "F4EFE7", "DDD3C3"


def _bs(cents):
    return None if cents is None else Decimal(int(cents)) / 100


def _fecha(d):
    return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else f"{d[8:10]}/{d[5:7]}/{d[0:4]}"


def period_text(data):
    start, end = data["start"], data["end"]
    return _fecha(start) if start == end else f"{_fecha(start)} al {_fecha(end)}"


def filename(data, ext):
    start, end = data["start"].isoformat(), data["end"].isoformat()
    return f"informe_{start}.{ext}" if start == end else f"informe_{start}_a_{end}.{ext}"


# ---------- secciones comunes (columnas, filas y tipo de cada columna) ----------
def sections(data):
    """Lista de (título, columnas, filas, tipos). tipos: 't' texto, 'n' número, 'm' dinero (centavos)."""
    t = data["totals"]
    out = []
    out.append(("Resumen", ["Concepto", "Valor"], [
        ["Jornadas", t["days"]], ["Pedidos pagados", t["paid"]], ["Pedidos cancelados", t["cancelled"]],
        ["Unidades vendidas (líneas)", t["units"]],
        ["Ventas en efectivo", ("m", t["cash"])], ["Ventas por QR", ("m", t["qr"])],
        ["Total de ventas registradas", ("m", t["sales"])], ["Descuentos aplicados", ("m", t["discounts"])],
        ["Gastos", ("m", t["expenses"])], ["Promedio de ventas por jornada", ("m", t["avg_sales"])],
        ["Pagos anulados (pedidos cancelados ya cobrados)", ("m", t["voided"])],
        ["Diferencias de caja registradas", ("m", t["difference"]) if t["difference"] is not None else "Sin conteos"],
    ], None))
    out.append(("Jornadas", ["Fecha", "Estado", "Pagados", "Cancel.", "Efectivo", "QR", "Ventas", "Gastos",
                             "Fondo", "Esperado", "Contado", "Diferencia"],
                [[_fecha(d["work_date"]), "Abierta" if d["status"] == "open" else "Cerrada", d["paid"], d["cancelled"],
                  d["cash"], d["qr"], d["sales"], d["expenses"], d["opening_fund_cents"], d["expected"],
                  d["counted_cash_cents"], d["difference"]] for d in data["days"]],
                "ttnnmmmmmmmm"))
    out.append(("Ventas por producto", ["Producto", "Unidades", "Importe", "%"],
                [[_combo_label(p), p["units"], p["amount"], p["share"]]
                 for p in data["by_product"]], "tnmn"))
    out.append(("Ventas por categoría", ["Categoría", "Unidades", "Importe", "%"],
                [[c["name"], c["units"], c["amount"], c["share"]] for c in data["by_category"]], "tnmn"))
    out.append(("Métodos de pago", ["Método", "Pedidos", "Importe"],
                [[m["label"], m["orders"], m["amount"]] for m in data["methods"]], "tnm"))
    out.append(("Gastos", ["Fecha", "Hora", "Concepto", "Observación", "Importe"],
                [[_fecha(e["work_date"]), e["created_at"][11:16], e["concept"], e["note"], e["amount_cents"]]
                 for e in data["expenses"]], "ttttm"))
    out.append(("Movimientos de inventario", ["Producto", "Inicial", "Entradas", "Vendidas", "Ajuste +", "Ajuste −"],
                [[i["name"], i["initial"], i["receipts"], i["sold"], i["adj_pos"], i["adj_neg"]] for i in data["inventory"]],
                "tnnnnn"))
    out.append((f"Existencias al {_fecha(data['end'])}", ["Producto", "Categoría", "Existencias", "Mínimo", "Estado"],
                [[s["name"], s["category"], s["stock"], s["min_stock"], {"agotado": "Agotado", "bajo": "Bajo", "ok": "OK"}[s["state"]]]
                 for s in data["stock"]], "ttnnt"))
    out.append(("Pedidos cancelados", ["Fecha", "Pedido", "Productos", "Total", "Pago anulado"],
                [[_fecha(c["work_date"]), f"#{c['id']}", c["resumen"] or "", c["total_cents"],
                  (("Efectivo" if c["voided_method"] == "cash" else "QR") + " " + format_bs(c["voided_amount"]))
                  if c["voided_method"] else "No se había cobrado"] for c in data["cancelled"]], "tttmt"))
    return out


def _combo_label(p):
    return p["name"] + (" (combo)" if p["is_combo"] and "combo" not in p["name"].lower() else "")


# ---------- Excel ----------
def to_excel(data):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill("solid", fgColor=ESPRESSO)
    thin = Border(bottom=Side(style="thin", color=LINEA))
    for title, cols, rows, types in sections(data):
        ws = wb.create_sheet(_sheet_name(title))
        ws["A1"] = f"Café Personal · {title}"
        ws["A1"].font = Font(bold=True, size=13)
        ws["A2"] = f"Periodo: {period_text(data)} · generado {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        ws["A3"] = DISCLAIMER
        ws["A2"].font = ws["A3"].font = Font(italic=True, size=9, color="7A6A5D")
        ws.append([])
        ws.append(cols)
        head = ws.max_row
        for c in ws[head]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), header_fill
        for row in rows:
            values = []
            for i, v in enumerate(row):
                kind = types[i] if types else ("m" if isinstance(v, tuple) else "t")
                if isinstance(v, tuple):
                    v = v[1]
                values.append(_bs(v) if kind == "m" and isinstance(v, int) else v)
            ws.append(values)
            for i, c in enumerate(ws[ws.max_row]):
                kind = types[i] if types else ("m" if isinstance(row[i], tuple) else "t")
                if kind == "m":
                    c.number_format = MONEY_FMT
                c.border = thin
        if not rows:
            ws.append(["Sin registros en el periodo."])
        for i, col in enumerate(cols, 1):
            width = max([len(str(col))] + [len(str(r[i - 1][1] if isinstance(r[i - 1], tuple) else r[i - 1] or ""))
                                           for r in rows] + [8])
            ws.column_dimensions[get_column_letter(i)].width = min(width + 3, 60)
        ws.freeze_panes = ws.cell(row=head + 1, column=1)
        for c in ws[head]:
            c.alignment = Alignment(vertical="center")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_name(title):
    for ch in '[]:*?/\\':
        title = title.replace(ch, "-")
    return title[:31]


# ---------- PDF ----------
def _pdf_text(value):
    """Helvetica (incluida en reportlab, sin descargas) cubre español; lo que no esté en Latin-1 se sustituye."""
    text = str(value).replace("−", "-").replace("—", "-").replace("–", "-").replace("…", "...")
    return text.encode("latin-1", "replace").decode("latin-1")


def to_pdf(data):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    PAGE = landscape(A4)
    doc = SimpleDocTemplate(buf, pagesize=PAGE, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Informe {period_text(data)}", author="Café Personal")
    st = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=st["Title"], fontName="Helvetica-Bold", fontSize=17, alignment=0, spaceAfter=2,
                        textColor=colors.HexColor("#" + ESPRESSO))
    h2 = ParagraphStyle("h2", parent=st["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, spaceBefore=9, spaceAfter=4,
                        textColor=colors.HexColor("#2E4A3F"))
    small = ParagraphStyle("s", parent=st["Normal"], fontSize=8, textColor=colors.HexColor("#7A6A5D"))
    cell = ParagraphStyle("c", parent=st["Normal"], fontSize=8, leading=10)
    cell_r = ParagraphStyle("cr", parent=cell, alignment=TA_RIGHT)
    story = [Paragraph("Informe · Café Personal", h1),
             Paragraph(_pdf_text(f"Periodo: {period_text(data)} · generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}"), small),
             Paragraph(_pdf_text(DISCLAIMER), small), Spacer(1, 4 * mm)]
    width = PAGE[0] - 32 * mm
    for title, cols, rows, types in sections(data):
        block = [Paragraph(_pdf_text(title), h2)]
        if not rows:
            block.append(Paragraph("Sin registros en el periodo.", small))
            story.append(KeepTogether(block))
            continue
        table = [[Paragraph(f"<b>{_xml(_pdf_text(c))}</b>", cell_r if (types[i] in "nm" if types else i) else cell)
                  for i, c in enumerate(cols)]]
        for row in rows:
            out = []
            for i, v in enumerate(row):
                kind = types[i] if types else ("m" if isinstance(v, tuple) else ("n" if i else "t"))
                if isinstance(v, tuple):
                    v = v[1]
                if kind == "m":
                    text, style = ("" if v is None else format_bs(v)), cell_r
                elif kind == "n":
                    text, style = ("" if v is None else (str(v).replace(".", ",") if isinstance(v, float) else str(v))), cell_r
                else:
                    text, style = ("" if v is None else str(v)), (cell_r if types is None and i else cell)
                out.append(Paragraph(_xml(_pdf_text(text)), style))
            table.append(out)
        t = Table(table, colWidths=_widths(cols, types, width), repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + CREMA)),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#" + LINEA)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        block.append(t)
        # tablas cortas no se parten entre páginas; las largas sí (repiten el encabezado)
        if len(rows) < 25:
            story.append(KeepTogether(block))
        else:
            story.extend(block)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _widths(cols, types, width):
    if types is None:
        return [width * 0.45, width * 0.2]
    weights = [3.2 if (k == "t" and i == 0) else (1.6 if k == "t" else 1.0) for i, k in enumerate(types)]
    total = sum(weights)
    return [width * w / total for w in weights]


def _footer(canvas, doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm

    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#7A6A5D"))
    canvas.drawString(16 * mm, 9 * mm, _pdf_text("Café Personal · registro personal, no oficial"))
    canvas.drawRightString(landscape(A4)[0] - 16 * mm, 9 * mm, f"Página {doc.page}")
    canvas.restoreState()
