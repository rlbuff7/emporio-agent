"""Filesystem locations shared by the ETL, the tools and the tests."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "emporio.db"
POLICIES_PATH = DATA_DIR / "policies.md"
HANDOFFS_PATH = DATA_DIR / "handoffs.jsonl"
