"""
SmartPath Jordan — Traffic Violation Detection System
Run: python app.py

Setup:
  1. Copy .env.example → .env and fill in values
  2. pip install flask pillow werkzeug sqlalchemy psycopg2-binary requests python-dotenv reportlab
  3. python app.py
"""

import os
import io
import base64
import requests as http_req
from datetime import datetime

from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, send_from_directory, send_file, Response,
)
from PIL import Image
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# ── ReportLab imports for PDF generation ──────────────────────────────────
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, Image as RLImage, KeepTogether,
)

load_dotenv()

from database.database import init_db
from gemini.gemini_function import extract_vehicle_info
from detector import detect_violations
from times import get_time_label
from crud import (
    get_all_fines,
    save_violation,
    get_all_violations,
    get_violations_only,
    get_stats,
    get_violation_by_filename,
)
from rag.rag_engine import run_rag_for_violations, chatbot as rag_chatbot

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

UPLOAD_FOLDER = "images"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
      rag_reports (list), image_path (optional local path)
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
        "🚔  SmartPath Jordan — Official Violation Report",
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

        fine_rows = [["Violation Type", "Fine (JD)"]]
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

    # ── RAG legal reports ──────────────────────────────────────────────────
    rag_reports = data.get("rag_reports", [])
    if rag_reports:
        story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_BORDER))
        story.append(Spacer(1, 4*mm))
        story.append(P("Legal References (Jordan Traffic Law)", fontSize=11, fontName="Helvetica-Bold"))

        for rr in rag_reports:
            story.append(Spacer(1, 4*mm))
            story.append(KeepTogether([
                P(f"<b>{rr.get('article_id','—')}</b> — {rr.get('title','—')}",
                  fontSize=9, textColor=PDF_BLUE),
                Spacer(1, 1*mm),
                P(f"Penalty: {rr.get('penalty','—')}", fontSize=8,
                  textColor=PDF_RED, fontName="Helvetica-Bold"),
                Spacer(1, 2*mm),
                P(rr.get("report",""), fontSize=8, leading=12, textColor=colors.HexColor("#333333")),
            ]))

    # ── Footer ─────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PDF_BORDER))
    story.append(Spacer(1, 3*mm))
    story.append(P(
        "This report was automatically generated by SmartPath Jordan · "
        "Jordan Traffic Authority · Confidential",
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
    header = ["#", "Plate", "Timestamp", "Violation", "Fine (JD)", "City", "Area"]
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
        "SmartPath Jordan · Jordan Traffic Authority · Confidential",
        fontSize=7, textColor=PDF_MUTED, alignment=1,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    try:
        stats = get_stats()
        return jsonify({"status": "ok", "total": stats.total_violations})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/")
def index():
    return redirect(url_for("home"))


# ── Home page ───────────────────────────────────────────────────────────────

@app.route("/home")
def home():
    return render_template("home.html", active="home")


# ── Dashboard / upload ──────────────────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html", active="dashboard", result=None, error=None)


@app.route("/analyze", methods=["POST"])
def analyze():
    import time
    from concurrent.futures import ThreadPoolExecutor

    # 1. Validate upload
    if "image" not in request.files or request.files["image"].filename == "":
        return render_template(
            "dashboard.html", active="dashboard", result=None,
            error="No image uploaded."
        )

    file      = request.files["image"]
    filename  = secure_filename(file.filename)
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)

    # 2. Open image
    try:
        image = Image.open(file_path).convert("RGB")
    except Exception:
        return render_template(
            "dashboard.html", active="dashboard", result=None,
            error="Could not read the uploaded image."
        )

    # 3. Parse GPS coordinates
    try:
        latitude  = float(request.form.get("latitude",  ""))
        longitude = float(request.form.get("longitude", ""))
    except (TypeError, ValueError):
        latitude, longitude = 31.9516, 35.9342
        print("[GPS] No coordinates received — using Amman centre as fallback.")

    # 4. Timestamp + time label
    timestamp  = datetime.now()
    time_label = get_time_label(timestamp.hour)

    # 5. Return cached result if already analysed
    existing = get_violation_by_filename(filename)
    if existing:
        rec, fine = existing
        violations = [{"name": fine.violation_name, "fine": fine.fine_amount}] if fine else []
        rag_reports = []
        if violations:
            try:
                rag_reports = run_rag_for_violations(
                    violation_names=[v["name"] for v in violations],
                    plate_number=rec.plate_number,
                    timestamp=rec.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    city=rec.city, area=rec.area, street=rec.street,
                )
            except Exception as exc:
                print(f"[RAG] Cached report generation failed: {exc}")

        result = {
            "violations":  violations,
            "total_fine":  fine.fine_amount if fine else 0,
            "plate":       rec.plate_number,
            "car_color":   rec.car_color or "Unknown",
            "car_type":    rec.car_type  or "Unknown",
            "city":        rec.city,
            "area":        rec.area,
            "street":      rec.street,
            "latitude":    rec.latitude,
            "longitude":   rec.longitude,
            "timestamp":   rec.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "time_label":  get_time_label(rec.timestamp.hour),
            "rag_reports": rag_reports,
            "image_url":   url_for("serve_image", filename=filename),
            "from_cache":  True,
        }
        return render_template("dashboard.html", active="dashboard", result=result, error=None)

    # ── PARALLEL: Geocode + YOLO + Gemini all at once ──────────────────────
    t_total = time.time()

    with ThreadPoolExecutor(max_workers=3) as executor:
        t0 = time.time()
        future_geo        = executor.submit(reverse_geocode, latitude, longitude)
        future_violations = executor.submit(detect_violations, image)
        future_vehicle    = executor.submit(extract_vehicle_info, image)

        geo                              = future_geo.result()
        violation_names, annotated_image = future_violations.result()
        vehicle                          = future_vehicle.result()

    print(f"[TIMER] Geocode + YOLO + Gemini (parallel): {time.time()-t0:.2f}s")

    city   = geo["city"]
    area   = geo["area"]
    street = geo["street"]

    # Save annotated image
    if annotated_image:
        annotated_image.save(file_path)

    plate    = vehicle.get("license_plate") or "Unreadable"
    color    = vehicle.get("car_color")     or "Unknown"
    car_type = vehicle.get("car_type")      or "Unknown"

    # Fine details
    fines_map  = {f.violation_name: f for f in get_all_fines()}
    violations = []
    total_fine = 0

    if violation_names:
        for v_name in violation_names:
            fine_obj = fines_map.get(v_name)
            fine_amt = fine_obj.fine_amount if fine_obj else 0
            violations.append({"name": v_name, "fine": fine_amt})
            total_fine += fine_amt
            save_violation(
                violation_name=v_name,
                plate_number=plate,
                car_color=color,
                car_type=car_type,
                city=city, area=area, street=street,
                latitude=latitude, longitude=longitude,
                timestamp=timestamp,
                image_filename=filename,
            )

    # RAG legal reports
    rag_reports = []
    if violation_names:
        try:
            t0 = time.time()
            rag_reports = run_rag_for_violations(
                violation_names=violation_names,
                plate_number=plate,
                timestamp=timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                city=city, area=area, street=street,
            )
            print(f"[TIMER] RAG: {time.time()-t0:.2f}s")
        except Exception as exc:
            print(f"[RAG] Report generation failed: {exc}")

    print(f"[TIMER] Total analyze: {time.time()-t_total:.2f}s")

    result = {
        "violations":  violations,
        "total_fine":  total_fine,
        "plate":       plate,
        "car_color":   color,
        "car_type":    car_type,
        "city":        city,
        "area":        area,
        "street":      street,
        "latitude":    latitude,
        "longitude":   longitude,
        "timestamp":   timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "time_label":  time_label,
        "rag_reports": rag_reports,
        "image_url":   url_for("serve_image", filename=filename),
        "from_cache":  False,
    }
    return render_template("dashboard.html", active="dashboard", result=result, error=None)


# ── Serve uploaded images ───────────────────────────────────────────────────

@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ── PDF report for a single violation (by plate + timestamp) ────────────────

@app.route("/report/pdf")
def report_pdf():
    """
    Generate and stream a violation report PDF.
    Query params: plate, ts (timestamp string)
    """
    plate = request.args.get("plate", "")
    ts    = request.args.get("ts", "")

    # Fetch matching record from DB
    records = get_all_violations()
    target_rec  = None
    target_fine = None

    for rec, fine in records:
        if rec.plate_number == plate and rec.timestamp.strftime("%Y-%m-%d %H:%M:%S") == ts:
            target_rec  = rec
            target_fine = fine
            break

    if not target_rec:
        return Response("Record not found", status=404)

    violations = []
    total_fine = 0
    if target_fine:
        violations = [{"name": target_fine.violation_name, "fine": target_fine.fine_amount}]
        total_fine = target_fine.fine_amount

    # Generate RAG for PDF
    rag_reports = []
    if violations:
        try:
            rag_reports = run_rag_for_violations(
                violation_names=[v["name"] for v in violations],
                plate_number=plate,
                timestamp=ts,
                city=target_rec.city,
                area=target_rec.area,
                street=target_rec.street,
            )
        except Exception as exc:
            print(f"[PDF/RAG] {exc}")

    image_path = None
    if target_rec.image_filename:
        candidate = os.path.join(UPLOAD_FOLDER, target_rec.image_filename)
        if os.path.exists(candidate):
            image_path = candidate

    data = {
        "plate":       target_rec.plate_number,
        "car_color":   target_rec.car_color or "Unknown",
        "car_type":    target_rec.car_type  or "Unknown",
        "city":        target_rec.city,
        "area":        target_rec.area,
        "street":      target_rec.street,
        "latitude":    target_rec.latitude,
        "longitude":   target_rec.longitude,
        "timestamp":   ts,
        "time_label":  get_time_label(target_rec.timestamp.hour),
        "total_fine":  total_fine,
        "violations":  violations,
        "rag_reports": rag_reports,
        "image_path":  image_path,
    }

    pdf_bytes = build_violation_pdf(data)
    safe_plate = plate.replace(" ", "_").replace("/", "-")
    filename   = f"violation_{safe_plate}_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ── PDF export of the full violation history table ──────────────────────────

@app.route("/report/table/pdf")
def report_table_pdf():
    """Export all violation records as a summary table PDF."""
    records   = get_all_violations()
    pdf_bytes = build_table_pdf(records)
    filename  = f"smartpath_violations_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


# ── History ─────────────────────────────────────────────────────────────────

@app.route("/history")
def history():
    records = get_violations_only()
    stats   = get_stats()
    return render_template(
        "history.html",
        active="history",
        records=records,
        stats=stats,
    )


# ── Analytics ───────────────────────────────────────────────────────────────

@app.route("/analytics")
def analytics():
    return render_template("analytics.html", active="analytics")


@app.route("/api/stats")
def api_stats():
    stats   = get_stats()
    records = get_violations_only()

    by_hour:    dict[int, int]  = {}
    by_city:    dict[str, int]  = {}
    by_type:    dict[str, int]  = {}
    timeline:   dict[str, int]  = {}

    for rec, fine in records:
        h = rec.timestamp.hour
        by_hour[h] = by_hour.get(h, 0) + 1

        by_city[rec.city] = by_city.get(rec.city, 0) + 1

        name = fine.violation_name if fine else "Unknown"
        by_type[name] = by_type.get(name, 0) + 1

        day = rec.timestamp.strftime("%Y-%m-%d")
        timeline[day] = timeline.get(day, 0) + 1

    return jsonify({
        "total_violations": stats.total_violations,
        "total_fines_jd":   stats.total_fines_jd,
        "by_hour":  [{"hour": k, "count": v} for k, v in sorted(by_hour.items())],
        "by_city":  [{"city": k,  "count": v} for k, v in sorted(by_city.items(),  key=lambda x: -x[1])],
        "by_type":  [{"type": k,  "count": v} for k, v in sorted(by_type.items(),  key=lambda x: -x[1])],
        "timeline": [{"date": k,  "count": v} for k, v in sorted(timeline.items())],
    })


@app.route("/api/violations/map")
def api_violations_map():
    records = get_violations_only()
    pins = []
    for rec, fine in records:
        if rec.latitude is None or rec.longitude is None:
            continue
        pins.append({
            "latitude":       rec.latitude,
            "longitude":      rec.longitude,
            "violation_name": fine.violation_name if fine else "Unknown",
            "fine_amount":    fine.fine_amount    if fine else None,
            "plate_number":   rec.plate_number,
            "city":           rec.city,
            "area":           rec.area,
            "street":         rec.street,
            "timestamp":      rec.timestamp.strftime("%Y-%m-%d %H:%M"),
        })
    return jsonify(pins)



# ── Chatbot ─────────────────────────────────────────────────────────────────

@app.route("/chatbot", methods=["POST"])
def chatbot_route():
    data     = request.get_json()
    question = (data or {}).get("question", "").strip()
    if not question:
        return jsonify({"answer": "Please ask a question."})
    try:
        answer = rag_chatbot(question)
    except Exception as exc:
        answer = f"Sorry, the assistant encountered an error: {exc}"
    return jsonify({"answer": answer})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=False, port=5000)