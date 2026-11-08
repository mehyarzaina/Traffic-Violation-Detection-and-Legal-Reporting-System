"""
CRUD helpers — all database reads and writes go through here.
"""

from datetime import datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass

from sqlmodel import Session, select, func, or_
from werkzeug.security import generate_password_hash, check_password_hash

from database.database import engine
from database.models import Fine, ViolationRecord, User, Upload


# ── Fines ─────────────────────────────────────────────────────────────────────

def get_all_fines() -> List[Fine]:
    with Session(engine) as session:
        return session.exec(select(Fine)).all()


def get_fine_by_name(violation_name: str) -> Optional[Fine]:
    with Session(engine) as session:
        return session.exec(
            select(Fine).where(Fine.violation_name == violation_name)
        ).first()


# ── Violation Records ─────────────────────────────────────────────────────────

def save_violation(
    violation_name: str,
    plate_number: str,
    car_color: Optional[str],
    car_type: Optional[str],
    city: str,
    area: str,
    street: str,
    latitude: float,
    longitude: float,
    timestamp: datetime,
    image_filename: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[ViolationRecord]:
    fine = get_fine_by_name(violation_name)
    if not fine:
        return None

    record = ViolationRecord(
        violation_id=fine.violation_id,
        plate_number=(plate_number or "Unreadable")[:20],
        car_color=car_color,
        car_type=car_type,
        city=city,
        area=area,
        street=street,
        latitude=latitude,
        longitude=longitude,
        timestamp=timestamp,
        image_filename=image_filename,
        user_id=user_id,
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def get_all_violations() -> List[Tuple[ViolationRecord, Optional[Fine]]]:
    """Return all (ViolationRecord, Fine|None) pairs, newest first (one query)."""
    with Session(engine) as session:
        rows = session.exec(
            select(ViolationRecord, Fine)
            .join(Fine, ViolationRecord.violation_id == Fine.violation_id, isouter=True)
            .order_by(ViolationRecord.timestamp.desc())
        ).all()
        return [(rec, fine) for rec, fine in rows]


def get_violations_only() -> List[Tuple[ViolationRecord, Fine]]:
    """Return only records that have an actual violation (fine is not None)."""
    with Session(engine) as session:
        rows = session.exec(
            select(ViolationRecord, Fine)
            .join(Fine, ViolationRecord.violation_id == Fine.violation_id)
            .order_by(ViolationRecord.timestamp.desc())
        ).all()
        return [(rec, fine) for rec, fine in rows]


def find_violation(plate: str, ts: str) -> Optional[Tuple[ViolationRecord, Optional[Fine]]]:
    """Find a record by plate + 'YYYY-mm-dd HH:MM:SS' timestamp string."""
    for rec, fine in get_all_violations():
        if rec.plate_number == plate and rec.timestamp.strftime("%Y-%m-%d %H:%M:%S") == ts:
            return rec, fine
    return None


@dataclass
class Stats:
    total_violations: int
    total_fines_jd: int
    breakdown: dict


def get_stats() -> Stats:
    records = get_violations_only()
    breakdown = {}
    for _, fine in records:
        breakdown[fine.violation_name] = breakdown.get(fine.violation_name, 0) + 1
    return Stats(
        total_violations=len(records),
        total_fines_jd=sum(fine.fine_amount for _, fine in records),
        breakdown=breakdown,
    )


# ── Uploads ───────────────────────────────────────────────────────────────────

def create_upload(**fields) -> Upload:
    upload = Upload(**fields)
    with Session(engine) as session:
        session.add(upload)
        session.commit()
        session.refresh(upload)
        return upload


def get_upload_by_hash(image_hash: str) -> Optional[Upload]:
    """Most recent analysis of an identical image (used as a result cache)."""
    with Session(engine) as session:
        return session.exec(
            select(Upload).where(Upload.image_hash == image_hash)
            .order_by(Upload.uploaded_at.desc())
        ).first()


def get_upload(upload_id: int) -> Optional[Upload]:
    with Session(engine) as session:
        return session.get(Upload, upload_id)


def get_uploads_for_user(user_id: int) -> List[Upload]:
    with Session(engine) as session:
        return session.exec(
            select(Upload).where(Upload.user_id == user_id)
            .order_by(Upload.uploaded_at.desc())
        ).all()


def user_owns_image(user_id: int, filename: str) -> bool:
    with Session(engine) as session:
        return session.exec(
            select(Upload.id).where(Upload.user_id == user_id, Upload.image_filename == filename)
        ).first() is not None


def delete_upload(upload_id: int, user_id: int) -> bool:
    """Remove an upload from a user's history (the violation record stays)."""
    with Session(engine) as session:
        upload = session.get(Upload, upload_id)
        if not upload or upload.user_id != user_id:
            return False
        session.delete(upload)
        session.commit()
        return True


# ── Users ─────────────────────────────────────────────────────────────────────

class UserError(ValueError):
    """A user-facing validation error."""


def _clean_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not (3 <= len(username) <= 50) or not all(c.isalnum() or c in "._-" for c in username):
        raise UserError("Username must be 3–50 characters: letters, numbers, dot, dash or underscore.")
    return username


def _check_password(password: str):
    if len(password or "") < 6:
        raise UserError("Password must be at least 6 characters.")


def get_user(user_id: int) -> Optional[User]:
    with Session(engine) as session:
        return session.get(User, user_id)


def get_user_by_username(username: str) -> Optional[User]:
    with Session(engine) as session:
        return session.exec(
            select(User).where(User.username == (username or "").strip().lower())
        ).first()


def authenticate(username: str, password: str) -> Optional[User]:
    user = get_user_by_username(username)
    if not user or not user.is_active or not check_password_hash(user.password_hash, password or ""):
        return None
    with Session(engine) as session:
        db_user = session.get(User, user.id)
        db_user.last_login = datetime.now()
        session.add(db_user)
        session.commit()
        session.refresh(db_user)
        return db_user


def create_user(username: str, password: str, full_name: str = "", email: str = "",
                role: str = "user", is_active: bool = True) -> User:
    username = _clean_username(username)
    _check_password(password)
    if role not in ("admin", "user"):
        raise UserError("Invalid role.")
    if get_user_by_username(username):
        raise UserError("That username is already taken.")
    user = User(
        username=username,
        full_name=(full_name or "").strip() or None,
        email=(email or "").strip() or None,
        password_hash=generate_password_hash(password),
        role=role,
        is_active=is_active,
    )
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _admin_count(session, exclude_id: Optional[int] = None) -> int:
    q = select(func.count()).select_from(User).where(User.role == "admin", User.is_active == True)  # noqa: E712
    if exclude_id is not None:
        q = q.where(User.id != exclude_id)
    return session.exec(q).one()


def update_user(user_id: int, acting_user_id: int, *, full_name=None, email=None,
                role=None, is_active=None, password=None) -> User:
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            raise UserError("User not found.")
        demoting = (role is not None and role != "admin") or (is_active is False)
        if user.role == "admin" and demoting and _admin_count(session, exclude_id=user.id) == 0:
            raise UserError("You can't remove the last active admin.")
        if user.id == acting_user_id and demoting:
            raise UserError("You can't demote or deactivate your own account.")
        if full_name is not None:
            user.full_name = full_name.strip() or None
        if email is not None:
            user.email = email.strip() or None
        if role is not None:
            if role not in ("admin", "user"):
                raise UserError("Invalid role.")
            user.role = role
        if is_active is not None:
            user.is_active = is_active
        if password:
            _check_password(password)
            user.password_hash = generate_password_hash(password)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def delete_user(user_id: int, acting_user_id: int):
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            raise UserError("User not found.")
        if user.id == acting_user_id:
            raise UserError("You can't delete your own account.")
        if user.role == "admin" and _admin_count(session, exclude_id=user.id) == 0:
            raise UserError("You can't delete the last active admin.")
        # Keep the evidence: detach the user's uploads/violations instead of deleting them.
        for up in session.exec(select(Upload).where(Upload.user_id == user.id)).all():
            up.user_id = None
            session.add(up)
        for rec in session.exec(select(ViolationRecord).where(ViolationRecord.user_id == user.id)).all():
            rec.user_id = None
            session.add(rec)
        session.delete(user)
        session.commit()


def list_users(search: str = "") -> List[Tuple[User, int]]:
    """Users with their upload counts, newest first."""
    with Session(engine) as session:
        q = (
            select(User, func.count(Upload.id))
            .join(Upload, Upload.user_id == User.id, isouter=True)
            .group_by(User.id)
            .order_by(User.created_at.desc())
        )
        if search:
            like = f"%{search.strip().lower()}%"
            q = q.where(or_(func.lower(User.username).like(like),
                            func.lower(func.coalesce(User.full_name, "")).like(like),
                            func.lower(func.coalesce(User.email, "")).like(like)))
        return [(u, n) for u, n in session.exec(q).all()]


def user_counts() -> dict:
    with Session(engine) as session:
        total  = session.exec(select(func.count()).select_from(User)).one()
        admins = session.exec(select(func.count()).select_from(User).where(User.role == "admin")).one()
        active = session.exec(select(func.count()).select_from(User).where(User.is_active == True)).one()  # noqa: E712
        return {"total": total, "admins": admins, "users": total - admins, "active": active}
