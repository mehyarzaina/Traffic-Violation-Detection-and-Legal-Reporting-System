from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy import text
import os
from dotenv import load_dotenv

load_dotenv()

DB_USER     = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST     = os.getenv('DB_HOST')
DB_PORT     = os.getenv('DB_PORT')
DB_NAME     = os.getenv('DB_NAME')

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)


def create_db():
    """Create all tables."""
    import database.models  # noqa: F401  (registers the tables)
    SQLModel.metadata.create_all(engine)


def migrate_db():
    """
    create_all() never adds columns to tables that already exist, so new
    columns on old tables are added here (idempotent).
    """
    statements = [
        "ALTER TABLE violation_records ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE violation_records ADD COLUMN IF NOT EXISTS upload_id INTEGER REFERENCES uploads(id) ON DELETE SET NULL",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS accepted_terms_at TIMESTAMP",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS annotated_filename VARCHAR(255)",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS review_status VARCHAR(12) NOT NULL DEFAULT 'pending'",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS confirmed_violations VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
        "ALTER TABLE uploads ADD COLUMN IF NOT EXISTS review_note VARCHAR(500)",
        "CREATE INDEX IF NOT EXISTS ix_uploads_review_status ON uploads (review_status)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def seed_fines():
    """Insert default violation fines if the table is empty."""
    from database.models import Fine

    default_fines = [
        Fine(violation_name="Wrong Way Driving", fine_amount=250),
        Fine(violation_name="Wrong Parking",     fine_amount=30),
    ]
    with Session(engine) as session:
        existing = session.exec(select(Fine)).all()
        if not existing:
            for fine in default_fines:
                session.add(fine)
            session.commit()


def seed_admin():
    """
    Create the first admin account if there is no admin yet.
    Credentials come from ADMIN_USERNAME / ADMIN_PASSWORD in .env.
    """
    from database.models import User
    from werkzeug.security import generate_password_hash

    username = (os.getenv("ADMIN_USERNAME") or "").strip()
    password = os.getenv("ADMIN_PASSWORD") or ""

    with Session(engine) as session:
        if session.exec(select(User).where(User.role == "admin")).first():
            return
        if not username or not password:
            print("[Auth] No admin account exists. Set ADMIN_USERNAME and "
                  "ADMIN_PASSWORD in .env and restart to create one.")
            return
        session.add(User(
            username=username,
            full_name="Administrator",
            password_hash=generate_password_hash(password),
            role="admin",
        ))
        session.commit()
        print(f"[Auth] Created admin account '{username}'.")


def init_db():
    """Full initialization: create tables, migrate, seed fines + admin."""
    create_db()
    migrate_db()
    seed_fines()
    seed_admin()
