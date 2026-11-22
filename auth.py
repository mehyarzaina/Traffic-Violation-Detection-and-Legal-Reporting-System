"""
Session-based login, role checks and CSRF protection.
"""
import secrets
from functools import wraps

from flask import session, g, redirect, url_for, request, abort, jsonify, flash

from crud import get_user


def login_user(user):
    session.clear()
    session["user_id"] = user.id
    session.permanent = True


def logout_user():
    session.clear()


def load_current_user():
    """before_request hook: puts the signed-in user (or None) on g.user."""
    g.user = None
    uid = session.get("user_id")
    if uid:
        user = get_user(uid)
        if user and user.is_active:
            g.user = user
        else:
            session.clear()


def _wants_json() -> bool:
    return request.is_json or request.path.startswith("/api/") or request.path == "/chatbot"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if _wants_json():
                return jsonify({"error": "Please sign in."}), 401
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user.is_admin:
            if _wants_json():
                return jsonify({"error": "Admins only."}), 403
            flash("That page is for admins only.", "error")
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


# ── CSRF ─────────────────────────────────────────────────────────────────────

def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    """before_request hook: every POST must carry the session's CSRF token."""
    if request.method != "POST":
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
        if _wants_json():
            return jsonify({"error": "Your session expired. Reload the page and try again."}), 400
        abort(400, "Your session expired. Reload the page and try again.")
