"""Shared MongoDB-backed fixtures for the attendance API tests."""
import os
import uuid
from pathlib import Path

import pytest
from bson import json_util
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

TEST_DB_SUFFIX = os.environ.get("MONGO_TEST_DB_SUFFIX", str(uuid.uuid4())[:8])
MONGO_DB_NAME = f"test_hrones_{TEST_DB_SUFFIX}"

# Keep tests on their isolated database, regardless of MONGO_DB in .env.
os.environ["MONGO_URI"] = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
os.environ["MONGO_DB"] = MONGO_DB_NAME

from app.main import app  # noqa: E402

mongo_client = MongoClient(
    os.environ["MONGO_URI"],
    tz_aware=True,
    serverSelectionTimeoutMS=3000,
)


@pytest.fixture(scope="session")
def mongo_db():
    """Use a uniquely named real MongoDB database and remove it after the suite."""
    try:
        mongo_client.admin.command("ping")
    except PyMongoError as exc:
        raise RuntimeError(
            f"Cannot reach MongoDB for isolated test database {MONGO_DB_NAME!r}. "
            "Check MONGO_URI in the process environment or project .env and "
            "confirm that the MongoDB server is reachable. Tests do not use "
            "the production attendance_db."
        ) from exc
    database = mongo_client[MONGO_DB_NAME]
    yield database
    mongo_client.drop_database(MONGO_DB_NAME)
    mongo_client.close()


@pytest.fixture
def seed_sample_data(mongo_db):
    """Reload the repository's sample documents without dropping their indexes."""
    sample_dir = Path(__file__).resolve().parent.parent / "sample_data"
    for name in ("employees", "attendance_logs"):
        collection = mongo_db[name]
        collection.delete_many({})
        documents = json_util.loads(
            (sample_dir / f"{name}.json").read_text(encoding="utf-8"),
            json_options=json_util.JSONOptions(tz_aware=True),
        )
        if documents:
            collection.insert_many(documents)


@pytest.fixture
def test_client(mongo_db, seed_sample_data):
    """Run the application lifespan against freshly reset, isolated test data."""
    with TestClient(app) as api_client:
        yield api_client
