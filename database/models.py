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
    car_color: Optional[str] = Field(default=None, max_length=100)
    car_type: Optional[str] = Field(default=None, max_length=100)
    city: str = Field(max_length=100)
    area: str = Field(max_length=100)
    street: str = Field(max_length=255)

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    timestamp: datetime
    image_filename: Optional[str] = Field(default=None, max_length=255)

    # Who uploaded the image (NULL for records created before accounts existed)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", nullable=True)

    fine: Optional[Fine] = Relationship(back_populates="violations")


# Table 3: Accounts
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(max_length=50, unique=True, index=True)
    full_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=255)
    password_hash: str = Field(max_length=255)
    role: str = Field(default="user", max_length=10)  # "admin" | "user"
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)
    last_login: Optional[datetime] = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# Table 4: Every analysed upload (with or without a violation)
class Upload(SQLModel, table=True):
    __tablename__ = "uploads"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="users.id", index=True)
    image_filename: str = Field(max_length=255)
    original_name: Optional[str] = Field(default=None, max_length=255)
    image_hash: Optional[str] = Field(default=None, max_length=64, index=True)

    plate_number: Optional[str] = Field(default=None, max_length=20)
    car_color: Optional[str] = Field(default=None, max_length=100)
    car_type: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=100)
    area: Optional[str] = Field(default=None, max_length=100)
    street: Optional[str] = Field(default=None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    violations: str = Field(default="", max_length=255)  # comma-separated names
    total_fine: int = Field(default=0)
    uploaded_at: datetime = Field(default_factory=datetime.now)

    @property
    def violation_list(self) -> list:
        return [v for v in (self.violations or "").split(",") if v]
