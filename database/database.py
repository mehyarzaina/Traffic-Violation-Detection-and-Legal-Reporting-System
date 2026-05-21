from sqlmodel import SQLModel, Session, create_engine, select
import os
from dotenv import load_dotenv

load_dotenv()

DB_USER     = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_HOST     = os.getenv('DB_HOST')
DB_PORT     = os.getenv('DB_PORT')
DB_NAME     = os.getenv('DB_NAME')

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
engine = create_engine(DATABASE_URL, echo=False)


def create_db():
    """Create all tables."""
    SQLModel.metadata.create_all(engine)


def seed_fines():
    """Insert default violation fines if the table is empty."""
    # Import here to avoid circular imports at module level
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


def init_db():
    """Full initialization: create tables + seed fines."""
    create_db()
    seed_fines()
