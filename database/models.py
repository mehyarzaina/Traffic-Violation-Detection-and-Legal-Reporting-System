from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


# Table 1: Violation Types and Fines
class Fine(SQLModel, table=True):
    __tablename__ = "fines"

    violation_id: Optional[int] = Field(default=None, primary_key=True)
    violation_name: str = Field(max_length=255)
    fine_amount: int

    violations: list["ViolationRecord"] = Relationship(back_populates="fine")


# Table 2: Records of Violations
class ViolationRecord(SQLModel, table=True):
    __tablename__ = "violation_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    violation_id: Optional[int] = Field(default=None, foreign_key="fines.violation_id", nullable=True)
    plate_number: str = Field(max_length=20)
    car_color: Optional[str] = Field(default=None, max_length=100)  # ← ADD
    car_type: Optional[str] = Field(default=None, max_length=100)   # ← ADD
    city: str = Field(max_length=100)
    area: str = Field(max_length=100)
    street: str = Field(max_length=255)

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    timestamp: datetime
    image_filename: Optional[str] = Field(default=None, max_length=255)

    fine: Optional[Fine] = Relationship(back_populates="violations")