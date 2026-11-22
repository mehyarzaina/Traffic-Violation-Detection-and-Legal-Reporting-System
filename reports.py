"""
PDF reports (ReportLab) and reverse geocoding helpers.
"""
import os
import io
from datetime import datetime

import requests as http_req
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage,
)

# ---------------------------------------------------------------------------
# PDF palette
# ---------------------------------------------------------------------------
PDF_DARK    = colors.HexColor("#0f0f14")
PDF_RED     = colors.HexColor("#ef4444")
PDF_AMBER   = colors.HexColor("#eab308")
PDF_BLUE    = colors.HexColor("#3b82f6")
PDF_MUTED   = colors.HexColor("#888888")
PDF_BORDER  = colors.HexColor("#2a2a38")
PDF_LIGHT   = colors.HexColor("#f4f4f8")


def reverse_geocode(latitude: float, longitude: float) -> dict:
    """GPS → city / area / street via OpenStreetMap Nominatim."""
    try:
        resp = http_req.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": latitude, "lon": longitude, "format": "json"},
            headers={"User-Agent": "SmartPathJordan/1.0"},
            timeout=5,
        )
        resp.raise_for_status()
        addr = resp.json().get("address", {})
        city   = addr.get("city") or addr.get("town") or addr.get("village") or "Unknown"
        area   = addr.get("suburb") or addr.get("neighbourhood") or addr.get("county") or "Unknown"
        street = addr.get("road") or "Unknown"
        return {"city": city, "area": area, "street": street}
    except Exception as exc:
        print(f"[Geocode] Reverse geocode failed: {exc}")
        return {"city": "Unknown", "area": "Unknown", "street": "Unknown"}


def build_violation_pdf(data: dict) -> bytes:
    """
    Build a formatted A4 PDF violation report and return its bytes.

    `data` keys:
      plate, car_color, car_type, city, area, street,
      timestamp, time_label, latitude, longitude,
      total_fine, violations (list of {name, fine}),
      image_path (optional local path)
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )

    styles = getSampleStyleSheet()
    base   = styles["Normal"]

    def P(text, **kw):
        style = ParagraphStyle("dyn", parent=base, **kw)
        return Paragraph(text, style)

    story = []

    # ── Header ─────────────────────────────────────────────────────────────
    story.append(P(
        "SmartPath Jordan — Violation Report (academic prototype)",
        fontSize=16, fontName="Helvetica-Bold",
        textColor=colors.white,
        backColor=PDF_DARK,
        borderPadding=(10, 14, 10, 14),
        leading=22,
    ))
    story.append(Spacer(1, 6*mm))
    story.append(P(
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
        f"Plate: <b>{data.get('plate','—')}</b>  |  "
        f"Timestamp: {data.get('timestamp','—')}",
        fontSize=8, textColor=PDF_MUTED,
    ))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=PDF_RED))
    story.append(Spacer(1, 6*mm))

    # ── Vehicle & Location table ────────────────────────────────────────────
    story.append(P("Vehicle & Location", fontSize=11, fontName="Helvetica-Bold"))
    story.append(Spacer(1, 3*mm))

    vl_data = [
        ["License Plate", data.get("plate","—"),
         "Car Color",     data.get("car_color","—")],
        ["Car Type",      data.get("car_type","—"),
         "City",          data.get("city","—")],
        ["Area",          data.get("area","—"),
         "Street",        data.get("street","—")],
        ["Latitude",      str(data.get("latitude","—")),
         "Longitude",     str(data.get("longitude","—"))],
        ["Timestamp",     data.get("timestamp","—"),
         "Period",        data.get("time_label","—")],
    ]

    vl_table = Table(vl_data, colWidths=[35*mm, 55*mm, 35*mm, 55*mm])
    vl_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,-1), PDF_LIGHT),
        ("BACKGROUND", (2,0), (2,-1), PDF_LIGHT),
        ("FONTNAME",   (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",   (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",   (0,0), (-1,-1), 8),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.white, colors.HexColor("#f9f9fb")]),
        ("GRID",       (0,0), (-1,-1), 0.5, PDF_BORDER),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
    ]))
    story.append(vl_table)
    story.append(Spacer(1, 6*mm))

    # ── Captured image ──────────────────────────────────────────────────────
    image_path = data.get("image_path")
    if image_path and os.path.exists(image_path):
        try:
            img = RLImage(image_path, width=80*mm, height=54*mm, kind="bound")
            story.append(P("Captured Frame", fontSize=11, fontName="Helvetica-Bold"))
            story.append(Spacer(1, 2*mm))
            story.append(img)
            story.append(Spacer(1, 5*mm))
        except Exception as exc:
            print(f"[PDF] Could not embed image: {exc}")

    # ── Violations & fines ─────────────────────────────────────────────────
    violations = data.get("violations", [])
    if violations:
        story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_BORDER))
        story.append(Spacer(1, 4*mm))
        story.append(P("Violations & Fines", fontSize=11, fontName="Helvetica-Bold"))
        story.append(Spacer(1, 3*mm))

        fine_rows = [["Violation Type", "Reference fine (JD)"]]
        for v in violations:
            fine_rows.append([v.get("name","—"), str(v.get("fine","—")) + " JD"])
        fine_rows.append(["TOTAL", str(data.get("total_fine","—")) + " JD"])

        ft = Table(fine_rows, colWidths=[130*mm, 40*mm])
        ft.setStyle(TableStyle([
            ("BACKGROUND",   (0,0),  (-1,0),  PDF_DARK),
            ("TEXTCOLOR",    (0,0),  (-1,0),  colors.white),
            ("FONTNAME",     (0,0),  (-1,0),  "Helvetica-Bold"),
            ("BACKGROUND",   (0,-1), (-1,-1), PDF_RED),
            ("TEXTCOLOR",    (0,-1), (-1,-1), colors.white),
            ("FONTNAME",     (0,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",     (0,0),  (-1,-1), 9),
            ("GRID",         (0,0),  (-1,-1), 0.5, PDF_BORDER),
            ("ALIGN",        (1,0),  (1,-1),  "CENTER"),
            ("VALIGN",       (0,0),  (-1,-1), "MIDDLE"),
            ("TOPPADDING",   (0,0),  (-1,-1), 5),
            ("BOTTOMPADDING",(0,0),  (-1,-1), 5),
            ("LEFTPADDING",  (0,0),  (-1,-1), 8),
            ("ROWBACKGROUNDS",(0,1), (-1,-2), [colors.white, colors.HexColor("#fff8f8")]),
        ]))
        story.append(ft)
        story.append(Spacer(1, 6*mm))

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(P(
        "Generated automatically by SmartPath Jordan, a university capstone prototype. "
        "Not an official document; not issued by or affiliated with any authority. "
        "Fines are reference values only. Contains personal data — handle confidentially.",
        fontSize=7, textColor=PDF_MUTED, alignment=1,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def build_table_pdf(records) -> bytes:
    """Build a summary table PDF of all violation records."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=14*mm, bottomMargin=14*mm,
    )
    styles = getSampleStyleSheet()
    base = styles["Normal"]

    def P(text, **kw):
        style = ParagraphStyle("dyn", parent=base, **kw)
        return Paragraph(text, style)

    story = []

    story.append(P(
        "SmartPath Jordan — Violation History Report",
        fontSize=14, fontName="Helvetica-Bold",
        textColor=colors.white, backColor=PDF_DARK,
        borderPadding=(8,12,8,12), leading=20,
    ))
    story.append(Spacer(1, 3*mm))
    story.append(P(
        f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
        f"Total Records: {len(records)}",
        fontSize=8, textColor=PDF_MUTED,
    ))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=1, color=PDF_RED))
    story.append(Spacer(1, 4*mm))

    # Table header
    header = ["#", "Plate", "Timestamp", "Violation", "Ref. fine (JD)", "City", "Area"]
    rows   = [header]
    for rec, fine in records:
        rows.append([
            str(rec.id),
            rec.plate_number or "—",
            rec.timestamp.strftime("%Y-%m-%d %H:%M") if rec.timestamp else "—",
            fine.violation_name if fine else "—",
            str(fine.fine_amount) + " JD" if fine else "—",
            rec.city or "—",
            rec.area or "—",
        ])

    col_widths = [10*mm, 28*mm, 32*mm, 50*mm, 22*mm, 28*mm, 26*mm]
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0),  (-1,0),  PDF_DARK),
        ("TEXTCOLOR",    (0,0),  (-1,0),  colors.white),
        ("FONTNAME",     (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0,0),  (-1,-1), 7.5),
        ("GRID",         (0,0),  (-1,-1), 0.4, PDF_BORDER),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#f4f4f8")]),
        ("VALIGN",       (0,0),  (-1,-1), "MIDDLE"),
        ("TOPPADDING",   (0,0),  (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),  (-1,-1), 3),
        ("LEFTPADDING",  (0,0),  (-1,-1), 5),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(P(
        "SmartPath Jordan · university capstone prototype · not an official document · contains personal data",
        fontSize=7, textColor=PDF_MUTED, alignment=1,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()

