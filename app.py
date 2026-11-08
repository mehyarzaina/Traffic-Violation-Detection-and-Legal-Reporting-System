"""
SmartPath Jordan — Traffic Violation Reporting (academic prototype)
Run: python app.py

Roles
  * user  (reporter) — uploads photos of possible violations; only ever sees
                       "report received" and the list of photos they sent.
  * admin (officer)  — sees AI results and vehicle details, reviews reports
                       (confirm / not a violation), history, analytics, users.

Setup:
  1. Fill in .env (DB_*, GEMINI_API_KEY, GROQ_API_KEY, FLASK_SECRET_KEY,
     ADMIN_USERNAME, ADMIN_PASSWORD, optional CONTACT_EMAIL)
  2. pip install -r requirements.txt
  3. python app.py   → http://localhost:5000
"""
import os
import io
import json
import time
import uuid
import hashlib
import secrets
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for,
    send_from_directory, send_file, Response, g, flash, abort,
)
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

from database.database import init_db
from gemini.gemini_function import extract_vehicle_info
from detector import detect_violations
from times import get_time_label
from reports import reverse_geocode, build_violation_pdf, build_table_pdf
import auth
from auth import login_required, admin_required, login_user, logout_user
import crud
from crud import UserError
import chatbot
import site_info
import icons

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,           # 16 MB uploads
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    TEMPLATES_AUTO_RELOAD=True,                     # template edits show without a restart
)
if not os.getenv("FLASK_SECRET_KEY"):
    print("[Auth] FLASK_SECRET_KEY not set — sessions will reset on every restart.")

UPLOAD_FOLDER = "images"
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
MAX_IMAGE_SIDE = 1920            # uploads are downscaled to this for storage
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Pages a signed-in user can open before accepting the current Terms
TERMS_EXEMPT = {"accept_terms", "logout", "privacy", "terms", "cookies", "static", "health"}

app.jinja_env.globals.update(icon=icons.icon, logo_mark=icons.logo_mark)

app.before_request(auth.load_current_user)
app.before_request(auth.check_csrf)


POLICIES_DATE = datetime.fromisoformat(site_info.SITE["policies_updated"])


def _needs_terms(user) -> bool:
    """No acceptance yet, or accepted before the current policies took effect."""
    return user.accepted_terms_at is None or user.accepted_terms_at < POLICIES_DATE


@app.before_request
def require_terms():
    if g.user and _needs_terms(g.user) and request.endpoint not in TERMS_EXEMPT:
        if request.method == "GET":
            return redirect(url_for("accept_terms", next=request.full_path.rstrip("?")))
        return jsonify({"error": "Please accept the Terms first."}), 403


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    # Other sites get only our origin (never the page path). OpenStreetMap's tile
    # servers require a Referer, so "same-origin" (no Referer) gets map tiles blocked.
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(self), geolocation=(self), microphone=()")
    return resp


@app.context_processor
def inject_globals():
    user = g.get("user")
    pending = 0
    if user and user.is_admin:
        try:
            pending = crud.report_counts()["pending"]
        except Exception:
            pending = 0
    return {
        "current_user": user,
        "csrf_token": auth.csrf_token,
        "site": site_info.SITE,
        "current_year": datetime.now().year,
        "pending_reviews": pending,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_next(target: str) -> str:
    """Only allow redirects back to a path on this site."""
    if target and target.startswith("/") and not target.startswith("//") and not urlparse(target).netloc:
        return target
    return url_for("index")


def _fines_map() -> dict:
    return {f.violation_name: f.fine_amount for f in crud.get_all_fines()}


def _result_from_upload(upload) -> dict:
    """Full result for officers (never rendered for reporters)."""
    fines = _fines_map()
    shown = upload.confirmed_list if upload.review_status == "confirmed" else upload.violation_list
    violations = [{"name": v, "fine": fines.get(v, 0)} for v in shown]
    return {
        "upload_id":  upload.id,
        "violations": violations,
        "ai_violations": upload.violation_list,
        "total_fine": sum(v["fine"] for v in violations),
        "plate":      upload.plate_number or "Unreadable",
        "car_color":  upload.car_color or "Unknown",
        "car_type":   upload.car_type or "Unknown",
        "city":       upload.city or "Unknown",
        "area":       upload.area or "Unknown",
        "street":     upload.street or "Unknown",
        "latitude":   upload.latitude,
        "longitude":  upload.longitude,
        "timestamp":  upload.uploaded_at.strftime("%Y-%m-%d %H:%M:%S"),
        "time_label": get_time_label(upload.uploaded_at.hour),
        "image_url":  url_for("serve_image", filename=upload.officer_image),
        "review_status": upload.review_status,
        "review_note": upload.review_note,
    }


def _delete_files(names):
    for name in names or []:
        path = os.path.join(UPLOAD_FOLDER, os.path.basename(name))
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            print(f"[Files] Could not delete {name}: {exc}")


def _upload_error(msg: str):
    template = "dashboard.html" if g.user.is_admin else "report.html"
    return render_template(template, active="dashboard", result=None, error=msg), 400


def _officer_auto_review(upload):
    """An officer's own upload is decided immediately from the AI result."""
    if upload.violation_list:
        crud.review_report(upload.id, g.user.id, "confirm", upload.violation_list, "Uploaded by an officer")
    else:
        crud.review_report(upload.id, g.user.id, "reject", [], "Uploaded by an officer — no violation detected")


def _prepare_image(raw: bytes) -> Image.Image:
    """Open, apply phone rotation, drop metadata (EXIF/GPS) and downscale."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    return img


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        user = crud.authenticate(request.form.get("username", ""), request.form.get("password", ""))
        if user:
            login_user(user)
            return redirect(_safe_next(request.args.get("next", "")))
        error = "Wrong username or password, or the account is disabled."
    return render_template("login.html", error=error, auth_page=True)


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))
    error = None
    form = {}
    if request.method == "POST":
        form = request.form
        if form.get("password") != form.get("confirm"):
            error = "The two passwords don't match."
        elif form.get("agree") != "on":
            error = "Please confirm you are 18 or older and accept the Terms and Privacy Policy."
        else:
            try:
                # Self sign-up always creates a normal (reporter) account.
                user = crud.create_user(
                    form.get("username", ""), form.get("password", ""),
                    full_name=form.get("full_name", ""), email=form.get("email", ""),
                    role="user",
                )
                crud.accept_terms(user.id)
                login_user(user)
                flash(f"Welcome, {user.full_name or user.username}! Your account is ready.", "success")
                return redirect(url_for("index"))
            except UserError as e:
                error = str(e)
    return render_template("register.html", error=error, form=form, auth_page=True)


@app.route("/accept-terms", methods=["GET", "POST"])
@login_required
def accept_terms():
    if request.method == "POST":
        if request.form.get("agree") != "on":
            flash("You need to accept the Terms and Privacy Policy to continue.", "error")
        else:
            crud.accept_terms(g.user.id)
            return redirect(_safe_next(request.args.get("next", "")))
    return render_template("accept_terms.html", auth_page=True)


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    try:
        stats = crud.get_stats()
        return jsonify({"status": "ok", "total": stats.total_violations})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


def _legal(page: str):
    lang = "ar" if request.args.get("lang") == "ar" else "en"
    return render_template(f"legal/{page}.html", active=page, lang=lang, page=page)


@app.route("/privacy")
def privacy():
    return _legal("privacy")


@app.route("/terms")
def terms():
    return _legal("terms")


@app.route("/cookies")
def cookies():
    return _legal("cookies")


@app.route("/")
def index():
    """Public welcome page; signed-in people go straight to their workspace."""
    if not g.user:
        return render_template("welcome.html", active="welcome")
    return redirect(url_for("home" if g.user.is_admin else "dashboard"))


# ---------------------------------------------------------------------------
# Upload (reporters and officers)
# ---------------------------------------------------------------------------

@app.route("/home")
@admin_required
def home():
    return render_template("home.html", active="home")


@app.route("/dashboard")
@login_required
def dashboard():
    template = "dashboard.html" if g.user.is_admin else "report.html"
    return render_template(template, active="dashboard", result=None, error=None)


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    is_officer = g.user.is_admin

    # 1. Consent + validation
    if not is_officer and request.form.get("consent") != "on":
        return _upload_error("Please tick the box to confirm the photo rules before sending.")
    file = request.files.get("image")
    if not file or not file.filename:
        return _upload_error("No image uploaded.")
    original = secure_filename(file.filename) or "image.jpg"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXT:
        return _upload_error("Please upload a JPG, PNG or WEBP image.")

    raw = file.read()
    try:
        image = _prepare_image(raw)
    except Exception:
        return _upload_error("Could not read the uploaded image.")

    # 2. GPS coordinates (fallback: Amman centre)
    try:
        latitude  = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError
    except (TypeError, ValueError):
        latitude, longitude = 31.9516, 35.9342

    image_hash = hashlib.sha256(raw).hexdigest()

    # 3. Same image analysed before? Reuse that AI result instead of calling the models again.
    cached = crud.get_upload_by_hash(image_hash)
    if cached and os.path.exists(os.path.join(UPLOAD_FOLDER, cached.image_filename)):
        if cached.user_id == g.user.id:
            if is_officer:
                flash("You already analysed this image — showing the saved result.", "info")
                return redirect(url_for("view_upload", upload_id=cached.id))
            flash("You already sent this photo. Thank you — it is in your reports.", "info")
            return redirect(url_for("report_received", upload_id=cached.id))
        upload = crud.create_upload(
            user_id=g.user.id, image_filename=cached.image_filename,
            annotated_filename=cached.annotated_filename, image_hash=image_hash,
            plate_number=cached.plate_number, car_color=cached.car_color, car_type=cached.car_type,
            city=cached.city, area=cached.area, street=cached.street,
            latitude=cached.latitude, longitude=cached.longitude,
            violations=cached.violations, total_fine=cached.total_fine,
            review_status="pending",
        )
        if is_officer:
            _officer_auto_review(upload)
        return redirect(url_for("view_upload" if is_officer else "report_received", upload_id=upload.id))

    # 4. Save the (metadata-free, downscaled) photo under a random name
    stem = uuid.uuid4().hex
    filename = f"{stem}.jpg"
    image.save(os.path.join(UPLOAD_FOLDER, filename), "JPEG", quality=85, optimize=True)
    timestamp = datetime.now()

    # 5. Geocode + YOLO + Gemini in parallel
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_geo = ex.submit(reverse_geocode, latitude, longitude)
        f_det = ex.submit(detect_violations, image)
        f_veh = ex.submit(extract_vehicle_info, image)
        geo = f_geo.result()
        violation_names, annotated = f_det.result()
        vehicle = f_veh.result()
    print(f"[TIMER] Geocode + YOLO + Gemini (parallel): {time.time() - t0:.2f}s")

    annotated_name = None
    if annotated:
        annotated_name = f"{stem}_ai.jpg"
        annotated.save(os.path.join(UPLOAD_FOLDER, annotated_name), "JPEG", quality=85, optimize=True)

    fines = _fines_map()
    upload = crud.create_upload(
        user_id=g.user.id, image_filename=filename, annotated_filename=annotated_name,
        image_hash=image_hash,
        plate_number=(vehicle.get("license_plate") or "Unreadable")[:20],
        car_color=vehicle.get("car_color") or "Unknown",
        car_type=vehicle.get("car_type") or "Unknown",
        city=geo["city"], area=geo["area"], street=geo["street"],
        latitude=latitude, longitude=longitude,
        violations=",".join(violation_names),
        total_fine=sum(fines.get(v, 0) for v in violation_names),
        uploaded_at=timestamp, review_status="pending",
    )

    # Officers are authorised: their own uploads count straight away.
    if is_officer:
        _officer_auto_review(upload)
        return redirect(url_for("view_upload", upload_id=upload.id))

    # Redirect (POST → GET) so refreshing doesn't send the photo twice
    return redirect(url_for("report_received", upload_id=upload.id))


# ── Reporter pages ──────────────────────────────────────────────────────────

@app.route("/reports/<int:upload_id>/received")
@login_required
def report_received(upload_id):
    upload = crud.get_upload(upload_id)
    if not upload or upload.user_id != g.user.id:
        abort(404)
    return render_template("report_received.html", active="dashboard", upload=upload)


@app.route("/my-reports")
@login_required
def my_reports():
    if g.user.is_admin:
        return redirect(url_for("admin_reports"))
    return render_template("my_reports.html", active="my_reports",
                           uploads=crud.get_uploads_for_user(g.user.id))


@app.route("/my-uploads")
def my_uploads_legacy():
    return redirect(url_for("my_reports"))


@app.route("/my-reports/<int:upload_id>/delete", methods=["POST"])
@login_required
def delete_my_report(upload_id):
    files = crud.withdraw_report(upload_id, g.user.id)
    if files is None:
        flash("Report not found.", "error")
    else:
        _delete_files(files)
        flash("The report was removed from your list.", "success")
    return redirect(url_for("my_reports"))


# ── Account (all users) ─────────────────────────────────────────────────────

@app.route("/account")
@login_required
def account():
    return render_template("account.html", active="account",
                           n_reports=len(crud.get_uploads_for_user(g.user.id)))


@app.route("/account/password", methods=["POST"])
@login_required
def account_password():
    f = request.form
    try:
        if f.get("new_password") != f.get("confirm"):
            raise UserError("The two new passwords don't match.")
        crud.change_password(g.user.id, f.get("current_password", ""), f.get("new_password", ""))
        flash("Your password was changed.", "success")
    except UserError as e:
        flash(str(e), "error")
    return redirect(url_for("account"))


@app.route("/account/export")
@login_required
def account_export():
    data = json.dumps(crud.export_user_data(g.user.id), ensure_ascii=False, indent=2)
    return send_file(io.BytesIO(data.encode("utf-8")), mimetype="application/json",
                     as_attachment=True, download_name=f"smartpath_my_data_{datetime.now():%Y%m%d}.json")


@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    try:
        files = crud.delete_own_account(g.user.id, request.form.get("password", ""))
    except UserError as e:
        flash(str(e), "error")
        return redirect(url_for("account"))
    _delete_files(files)
    logout_user()
    flash("Your account was deleted.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Officer pages
# ---------------------------------------------------------------------------

@app.route("/uploads/<int:upload_id>")
@admin_required
def view_upload(upload_id):
    upload = crud.get_upload(upload_id)
    if not upload:
        abort(404)
    return render_template("dashboard.html", active="dashboard",
                           result=_result_from_upload(upload), error=None)


@app.route("/admin/reports")
@admin_required
def admin_reports():
    status = request.args.get("status", "pending")
    if status not in ("pending", "confirmed", "rejected", "all"):
        status = "pending"
    return render_template(
        "admin_reports.html", active="reports", status=status,
        reports=crud.get_reports(status), counts=crud.report_counts(),
        fines=crud.get_all_fines(),
    )


@app.route("/admin/reports/<int:upload_id>/review", methods=["POST"])
@admin_required
def admin_review_report(upload_id):
    decision = request.form.get("decision", "")
    try:
        crud.review_report(upload_id, g.user.id, decision,
                           request.form.getlist("violations"), request.form.get("note", ""))
        flash("Report confirmed as a violation." if decision == "confirm"
              else "Report marked as not a violation.", "success")
    except UserError as e:
        flash(str(e), "error")
    return redirect(url_for("admin_reports", status=request.args.get("status", "pending")))


# ── Images ──────────────────────────────────────────────────────────────────

@app.route("/images/<filename>")
@login_required
def serve_image(filename):
    # Officers see every image; reporters only the original photos they sent.
    if not g.user.is_admin and not crud.user_owns_image(g.user.id, filename):
        abort(404)
    resp = send_from_directory(UPLOAD_FOLDER, filename)
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ── PDF reports (officers) ──────────────────────────────────────────────────

def _pdf_response(data: dict, plate: str):
    pdf = build_violation_pdf(data)
    safe_plate = (plate or "unknown").replace(" ", "_").replace("/", "-")
    return send_file(
        io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
        download_name=f"violation_{safe_plate}_{datetime.now():%Y%m%d%H%M%S}.pdf",
    )


@app.route("/uploads/<int:upload_id>/pdf")
@admin_required
def upload_pdf(upload_id):
    upload = crud.get_upload(upload_id)
    if not upload:
        abort(404)
    r = _result_from_upload(upload)
    if not r["violations"]:
        abort(404)
    img = os.path.join(UPLOAD_FOLDER, upload.officer_image)
    r["image_path"] = img if os.path.exists(img) else None
    return _pdf_response(r, r["plate"])


@app.route("/report/pdf")
@admin_required
def report_pdf():
    """Single-record PDF from the History table (query params: plate, ts)."""
    plate = request.args.get("plate", "")
    ts    = request.args.get("ts", "")
    found = crud.find_violation(plate, ts)
    if not found:
        return Response("Record not found", status=404)
    rec, fine = found
    violations = [{"name": fine.violation_name, "fine": fine.fine_amount}] if fine else []
    img = os.path.join(UPLOAD_FOLDER, rec.image_filename) if rec.image_filename else None
    data = {
        "plate": rec.plate_number, "car_color": rec.car_color or "Unknown",
        "car_type": rec.car_type or "Unknown", "city": rec.city, "area": rec.area,
        "street": rec.street, "latitude": rec.latitude, "longitude": rec.longitude,
        "timestamp": ts, "time_label": get_time_label(rec.timestamp.hour),
        "total_fine": fine.fine_amount if fine else 0, "violations": violations,
        "image_path": img if img and os.path.exists(img) else None,
    }
    return _pdf_response(data, plate)


@app.route("/report/table/pdf")
@admin_required
def report_table_pdf():
    pdf = build_table_pdf(crud.get_all_violations())
    return send_file(
        io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
        download_name=f"smartpath_violations_{datetime.now():%Y%m%d%H%M%S}.pdf",
    )


# ── History & analytics ─────────────────────────────────────────────────────

@app.route("/history")
@admin_required
def history():
    return render_template("history.html", active="history",
                           records=crud.get_violations_only(), stats=crud.get_stats())


@app.route("/analytics")
@admin_required
def analytics():
    return render_template("analytics.html", active="analytics")


@app.route("/api/stats")
@admin_required
def api_stats():
    records = crud.get_violations_only()
    by_hour, by_city, by_type, timeline = {}, {}, {}, {}
    for rec, fine in records:
        by_hour[rec.timestamp.hour] = by_hour.get(rec.timestamp.hour, 0) + 1
        by_city[rec.city] = by_city.get(rec.city, 0) + 1
        by_type[fine.violation_name] = by_type.get(fine.violation_name, 0) + 1
        day = rec.timestamp.strftime("%Y-%m-%d")
        timeline[day] = timeline.get(day, 0) + 1
    return jsonify({
        "total_violations": len(records),
        "total_fines_jd":   sum(f.fine_amount for _, f in records),
        "pending_reviews":  crud.report_counts()["pending"],
        "by_hour":  [{"hour": k, "count": v} for k, v in sorted(by_hour.items())],
        "by_city":  [{"city": k, "count": v} for k, v in sorted(by_city.items(), key=lambda x: -x[1])],
        "by_type":  [{"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda x: -x[1])],
        "timeline": [{"date": k, "count": v} for k, v in sorted(timeline.items())],
    })


@app.route("/api/violations/map")
@admin_required
def api_violations_map():
    pins = []
    for rec, fine in crud.get_violations_only():
        if rec.latitude is None or rec.longitude is None:
            continue
        pins.append({
            "latitude": rec.latitude, "longitude": rec.longitude,
            "violation_name": fine.violation_name, "fine_amount": fine.fine_amount,
            "plate_number": rec.plate_number, "city": rec.city, "area": rec.area,
            "street": rec.street, "timestamp": rec.timestamp.strftime("%Y-%m-%d %H:%M"),
        })
    return jsonify(pins)


# ── User management ─────────────────────────────────────────────────────────

@app.route("/admin/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()
    return render_template("admin_users.html", active="users",
                           users=crud.list_users(q), counts=crud.user_counts(), q=q)


@app.route("/admin/users/create", methods=["POST"])
@admin_required
def admin_create_user():
    f = request.form
    try:
        user = crud.create_user(
            f.get("username", ""), f.get("password", ""),
            full_name=f.get("full_name", ""), email=f.get("email", ""),
            role=f.get("role", "user"), is_active=f.get("is_active") == "on",
        )
        flash(f"User '{user.username}' created. They will be asked to accept the Terms when they first sign in.", "success")
    except UserError as e:
        flash(str(e), "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
@admin_required
def admin_update_user(user_id):
    f = request.form
    try:
        user = crud.update_user(
            user_id, g.user.id,
            full_name=f.get("full_name", ""), email=f.get("email", ""),
            role=f.get("role"), is_active=f.get("is_active") == "on",
            password=f.get("password") or None,
        )
        flash(f"User '{user.username}' updated.", "success")
    except UserError as e:
        flash(str(e), "error")
    return redirect(url_for("admin_users", q=request.args.get("q", "")))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    try:
        _delete_files(crud.delete_user(user_id, g.user.id))
        flash("User deleted. Their unreviewed reports were deleted; reviewed reports were kept without their name.", "success")
    except UserError as e:
        flash(str(e), "error")
    return redirect(url_for("admin_users"))


# ── Chatbot ─────────────────────────────────────────────────────────────────

@app.route("/chatbot", methods=["POST"])
@login_required
def chatbot_route():
    data = request.get_json(silent=True) or {}
    question = str(data.get("question", "")).strip()
    if not question:
        return jsonify({"answer": "Please ask a question."})
    history = data.get("history") if isinstance(data.get("history"), list) else []
    try:
        answer = chatbot.ask(g.user, question, history)
    except Exception as exc:
        print(f"[Chatbot] {exc}")
        answer = "Sorry, the assistant ran into an error. Please try again."
    return jsonify({"answer": answer})


# ── Errors ──────────────────────────────────────────────────────────────────

@app.errorhandler(413)
def too_large(_):
    if g.get("user"):
        return _upload_error("That image is larger than 16 MB.")
    return Response("File too large", status=413)


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="That page doesn't exist."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           message=getattr(e, "description", "Bad request.")), 400


@app.errorhandler(500)
def server_error(_):
    return render_template("error.html", code=500,
                           message="Something went wrong on our side. Please try again."), 500


# ---------------------------------------------------------------------------
# Background warm-up: load the heavy models while the server is already up,
# so the first upload doesn't wait for them.
# ---------------------------------------------------------------------------

def _warm_up():
    t0 = time.time()
    try:
        from gemini.gemini_function import warm_up as warm_gemini
        from detector import warm_up as warm_detector
        warm_gemini()
        warm_detector()
        print(f"[Warm-up] Models ready in {time.time() - t0:.1f}s")
    except Exception as exc:
        print(f"[Warm-up] Failed (models will load on first use): {exc}")


if __name__ == "__main__":
    init_db()
    threading.Thread(target=_warm_up, daemon=True).start()
    app.run(debug=False, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", 5000)))
