"""
SmartPath Jordan — Traffic Violation Detection System
Run: python app.py

Setup:
  1. Fill in .env (DB_*, GEMINI_API_KEY, GROQ_API_KEY, FLASK_SECRET_KEY,
     ADMIN_USERNAME, ADMIN_PASSWORD)
  2. pip install -r requirements.txt
  3. python app.py   → http://localhost:5000
"""
import os
import io
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
from PIL import Image
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
)
if not os.getenv("FLASK_SECRET_KEY"):
    print("[Auth] FLASK_SECRET_KEY not set — sessions will reset on every restart.")

UPLOAD_FOLDER = "images"
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.before_request(auth.load_current_user)
app.before_request(auth.check_csrf)


@app.context_processor
def inject_globals():
    return {"current_user": g.get("user"), "csrf_token": auth.csrf_token}


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
    fines = _fines_map()
    violations = [{"name": v, "fine": fines.get(v, 0)} for v in upload.violation_list]
    return {
        "upload_id":  upload.id,
        "violations": violations,
        "total_fine": upload.total_fine,
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
        "image_url":  url_for("serve_image", filename=upload.image_filename),
        "original_name": upload.original_name,
    }


def _can_view_upload(upload) -> bool:
    return upload is not None and (g.user.is_admin or upload.user_id == g.user.id)


def _dashboard_error(msg: str):
    return render_template("dashboard.html", active="dashboard", result=None, error=msg), 400


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
        else:
            try:
                # Self sign-up always creates a normal user account.
                user = crud.create_user(
                    form.get("username", ""), form.get("password", ""),
                    full_name=form.get("full_name", ""), email=form.get("email", ""),
                    role="user",
                )
                login_user(user)
                flash(f"Welcome, {user.full_name or user.username}! Your account is ready.", "success")
                return redirect(url_for("index"))
            except UserError as e:
                error = str(e)
    return render_template("register.html", error=error, form=form, auth_page=True)


@app.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/api/health")
def health():
    try:
        stats = crud.get_stats()
        return jsonify({"status": "ok", "total": stats.total_violations})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/")
def index():
    if not g.user:
        return redirect(url_for("login"))
    return redirect(url_for("home" if g.user.is_admin else "dashboard"))


@app.route("/home")
@admin_required
def home():
    return render_template("home.html", active="home")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", active="dashboard", result=None, error=None)


@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    # 1. Validate upload
    file = request.files.get("image")
    if not file or not file.filename:
        return _dashboard_error("No image uploaded.")
    original = secure_filename(file.filename) or "image.jpg"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXT:
        return _dashboard_error("Please upload a JPG, PNG or WEBP image.")

    raw = file.read()
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return _dashboard_error("Could not read the uploaded image.")

    # 2. GPS coordinates (fallback: Amman centre)
    try:
        latitude  = float(request.form.get("latitude", ""))
        longitude = float(request.form.get("longitude", ""))
    except (TypeError, ValueError):
        latitude, longitude = 31.9516, 35.9342

    # 3. Same image analysed before? Reuse that result instead of calling the models again.
    image_hash = hashlib.sha256(raw).hexdigest()
    cached = crud.get_upload_by_hash(image_hash)
    if cached and os.path.exists(os.path.join(UPLOAD_FOLDER, cached.image_filename)):
        if cached.user_id == g.user.id:
            flash("You already analysed this image — showing the saved result.", "info")
            return redirect(url_for("view_upload", upload_id=cached.id))
        copy = crud.create_upload(
            user_id=g.user.id, image_filename=cached.image_filename, original_name=original,
            image_hash=image_hash, plate_number=cached.plate_number, car_color=cached.car_color,
            car_type=cached.car_type, city=cached.city, area=cached.area, street=cached.street,
            latitude=cached.latitude, longitude=cached.longitude,
            violations=cached.violations, total_fine=cached.total_fine,
        )
        flash("This image was analysed before — showing the saved result.", "info")
        return redirect(url_for("view_upload", upload_id=copy.id))

    # 4. Save with a unique name so different users' files never overwrite each other
    filename  = f"{uuid.uuid4().hex[:12]}_{original}"
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    image.save(file_path)
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

    if annotated:
        annotated.save(file_path)

    plate    = (vehicle.get("license_plate") or "Unreadable")[:20]
    color    = vehicle.get("car_color") or "Unknown"
    car_type = vehicle.get("car_type") or "Unknown"
    fines    = _fines_map()
    total    = sum(fines.get(v, 0) for v in violation_names)

    for v_name in violation_names:
        crud.save_violation(
            violation_name=v_name, plate_number=plate, car_color=color, car_type=car_type,
            city=geo["city"], area=geo["area"], street=geo["street"],
            latitude=latitude, longitude=longitude, timestamp=timestamp,
            image_filename=filename, user_id=g.user.id,
        )

    upload = crud.create_upload(
        user_id=g.user.id, image_filename=filename, original_name=original, image_hash=image_hash,
        plate_number=plate, car_color=color, car_type=car_type,
        city=geo["city"], area=geo["area"], street=geo["street"],
        latitude=latitude, longitude=longitude,
        violations=",".join(violation_names), total_fine=total, uploaded_at=timestamp,
    )
    # Redirect (POST → GET) so refreshing the page doesn't re-upload the image
    return redirect(url_for("view_upload", upload_id=upload.id))


@app.route("/uploads/<int:upload_id>")
@login_required
def view_upload(upload_id):
    upload = crud.get_upload(upload_id)
    if not _can_view_upload(upload):
        abort(404)
    return render_template("dashboard.html", active="dashboard",
                           result=_result_from_upload(upload), error=None)


@app.route("/my-uploads")
@login_required
def my_uploads():
    uploads = crud.get_uploads_for_user(g.user.id)
    n_violations = sum(1 for u in uploads if u.violation_list)
    return render_template(
        "my_uploads.html", active="my_uploads", uploads=uploads,
        n_violations=n_violations, total_fines=sum(u.total_fine for u in uploads),
    )


@app.route("/my-uploads/<int:upload_id>/delete", methods=["POST"])
@login_required
def delete_my_upload(upload_id):
    if crud.delete_upload(upload_id, g.user.id):
        flash("Upload removed from your history.", "success")
    else:
        flash("Upload not found.", "error")
    return redirect(url_for("my_uploads"))


# ── Images ──────────────────────────────────────────────────────────────────

@app.route("/images/<filename>")
@login_required
def serve_image(filename):
    # Admins see every image; users only the images they uploaded.
    if not g.user.is_admin and not crud.user_owns_image(g.user.id, filename):
        abort(404)
    return send_from_directory(UPLOAD_FOLDER, filename)


# ── PDF reports ─────────────────────────────────────────────────────────────

def _pdf_response(data: dict, plate: str):
    pdf = build_violation_pdf(data)
    safe_plate = (plate or "unknown").replace(" ", "_").replace("/", "-")
    return send_file(
        io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
        download_name=f"violation_{safe_plate}_{datetime.now():%Y%m%d%H%M%S}.pdf",
    )


@app.route("/uploads/<int:upload_id>/pdf")
@login_required
def upload_pdf(upload_id):
    upload = crud.get_upload(upload_id)
    if not _can_view_upload(upload) or not upload.violation_list:
        abort(404)
    r = _result_from_upload(upload)
    img = os.path.join(UPLOAD_FOLDER, upload.image_filename)
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


# ── History & analytics (admin) ─────────────────────────────────────────────

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


# ── User management (admin) ─────────────────────────────────────────────────

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
        flash(f"User '{user.username}' created.", "success")
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
        crud.delete_user(user_id, g.user.id)
        flash("User deleted. Their uploads and violation records were kept.", "success")
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
        return _dashboard_error("That image is larger than 16 MB.")
    return Response("File too large", status=413)


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="That page doesn't exist."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           message=getattr(e, "description", "Bad request.")), 400


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
