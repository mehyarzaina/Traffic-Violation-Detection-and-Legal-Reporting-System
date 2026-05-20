"""
CRUD helpers — all database reads and writes go through here.
"""

from datetime import datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass

from sqlmodel import Session, select

from database.database import engine
from database.models import Fine, ViolationRecord


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
) -> Optional[ViolationRecord]:
    fine = get_fine_by_name(violation_name)
    if not fine:
        return None

    record = ViolationRecord(
        violation_id=fine.violation_id,
        plate_number=plate_number or "Unreadable",
        car_color=car_color,
        car_type=car_type,
        city=city,
        area=area,
        street=street,
        latitude=latitude,
        longitude=longitude,
        timestamp=timestamp,
        image_filename=image_filename,
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


# def save_no_violation(
#     plate_number: str,
#     car_color: Optional[str],
#     car_type: Optional[str],
#     city: str,
#     area: str,
#     street: str,
#     latitude: float,
#     longitude: float,
#     timestamp: datetime,
#     image_filename: Optional[str] = None,
# ) -> ViolationRecord:
#     """Save a record with no violation — violation_id is NULL."""
#     record = ViolationRecord(
#         violation_id=None,
#         plate_number=plate_number or "Unreadable",
#         car_color=car_color,
#         car_type=car_type,
#         city=city,
#         area=area,
#         street=street,
#         latitude=latitude,
#         longitude=longitude,
#         timestamp=timestamp,
#         image_filename=image_filename,
#     )
#     with Session(engine) as session:
#         session.add(record)
#         session.commit()
#         session.refresh(record)
#         return record


def get_all_violations() -> List[Tuple[ViolationRecord, Optional[Fine]]]:
    """Return all (ViolationRecord, Fine|None) pairs, newest first."""
    with Session(engine) as session:
        records = session.exec(
            select(ViolationRecord).order_by(ViolationRecord.timestamp.desc())
        ).all()
        result = []
        for rec in records:
            fine = session.get(Fine, rec.violation_id) if rec.violation_id else None
            result.append((rec, fine))
        return result


def get_violation_by_filename(filename: str) -> Optional[Tuple[ViolationRecord, Optional[Fine]]]:
    """Return the first (ViolationRecord, Fine|None) matching this filename, or None."""
    with Session(engine) as session:
        rec = session.exec(
            select(ViolationRecord).where(ViolationRecord.image_filename == filename)
        ).first()
        if not rec:
            return None
        fine = session.get(Fine, rec.violation_id) if rec.violation_id else None
        return (rec, fine)

def get_violations_only() -> List[Tuple[ViolationRecord, Fine]]:
    """Return only records that have an actual violation (fine is not None)."""
    with Session(engine) as session:
        records = session.exec(
            select(ViolationRecord).order_by(ViolationRecord.timestamp.desc())
        ).all()
        result = []
        for rec in records:
            if rec.violation_id is not None:
                fine = session.get(Fine, rec.violation_id)
                if fine:
                    result.append((rec, fine))
        return result

@dataclass
class Stats:
    total_violations: int
    total_fines_jd: int
    breakdown: dict


def get_stats() -> Stats:
    records = get_all_violations()
    # Only count records that actually have a violation
    violation_records = [(rec, fine) for rec, fine in records if fine]
    total_violations = len(violation_records)
    total_fines_jd = sum(fine.fine_amount for _, fine in violation_records)
    breakdown = {}
    for _, fine in violation_records:
        breakdown[fine.violation_name] = breakdown.get(fine.violation_name, 0) + 1
    return Stats(
        total_violations=total_violations,
        total_fines_jd=total_fines_jd,
        breakdown=breakdown,
    )