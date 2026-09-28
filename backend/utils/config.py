import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Explicit path: bare load_dotenv() searches from CWD and silently finds nothing.
load_dotenv(PROJECT_ROOT / ".env")


REPO_URL = "https://github.com/sumit-0804/omniquery"


def demo_mode() -> bool:
    """The hosted, read-only demo. Read on each call so tests can switch it."""
    return os.environ.get("OMNIQUERY_DEMO") == "1"


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def db_config() -> dict:
    return {
        "host": require("DATABASE_HOST"),
        "port": int(require("DATABASE_PORT")),
        "user": require("DATABASE_USER"),
        "password": require("DATABASE_PASSWORD"),
        "dbname": require("DATABASE_NAME"),
    }
