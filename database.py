# database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
from urllib.parse import quote_plus

# ✅ Load environment variables from .env
load_dotenv()

DB_USER = os.getenv("DB_USER")          # postgres
DB_PASSWORD = quote_plus(os.getenv("DB_PASSWORD"))  # Pass@123s → Pass%40123s
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "training_tracker")

# ✅ Construct SQLAlchemy DATABASE_URL dynamically
DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ✅ Create engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ✅ Base class for models
Base = declarative_base()

# ✅ Dependency function for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()  # ✅ Commit only if successful
    except:
        db.rollback()  # ❌ Rollback on error
        raise
    finally:
        db.close()
