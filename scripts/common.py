"""Shared helpers: Bangladesh time handling and on-disk paths."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Bangladesh Standard Time is a fixed UTC+6 with no daylight saving,
# so a fixed offset is used instead of depending on a tz database.
BDT = timezone(timedelta(hours=6))

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
STATE_FILE = ROOT / "data" / "state.json"
REPORTS_DIR = ROOT / "reports"
LEDGER_FILE = REPORTS_DIR / "expenses.csv"


def now_bdt() -> datetime:
    return datetime.now(timezone.utc).astimezone(BDT)


def bdt_date_of(unix_ts: int) -> str:
    """Which Bangladesh calendar day a Telegram timestamp belongs to."""
    return datetime.fromtimestamp(unix_ts, timezone.utc).astimezone(BDT).strftime("%Y-%m-%d")


def bdt_time_of(unix_ts: int) -> str:
    return datetime.fromtimestamp(unix_ts, timezone.utc).astimezone(BDT).strftime("%H:%M")


def default_report_date() -> str:
    """The day a report run should cover.

    Rolling back one hour makes the 00:05 BDT cron land on the day that just
    ended, while a manual run during the day still targets the current day.
    """
    return (now_bdt() - timedelta(hours=1)).strftime("%Y-%m-%d")


def raw_file_for(date_str: str) -> Path:
    return RAW_DIR / f"{date_str}.jsonl"


def report_file_for(date_str: str) -> Path:
    return REPORTS_DIR / date_str[:7] / f"{date_str}.md"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"offset": 0}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value
