"""Pull new Telegram messages and store them as raw per-day JSONL files.

Telegram keeps undelivered updates for about 24 hours only, so this runs on a
short schedule and banks every message immediately. Turning the raw notes into
a tidy daily report is report.py's job.
"""

import os
import sys

import requests

from common import (
    bdt_date_of,
    bdt_time_of,
    load_state,
    raw_file_for,
    read_jsonl,
    require_env,
    save_state,
    write_jsonl,
)

API_BASE = "https://api.telegram.org/bot{token}/{method}"
MESSAGE_KEYS = ("message", "edited_message", "channel_post", "edited_channel_post")


def call(token: str, method: str, **params) -> dict:
    response = requests.get(API_BASE.format(token=token, method=method), params=params, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise SystemExit(f"Telegram API error on {method}: {payload}")
    return payload


def allowed_chat_ids() -> set[str]:
    raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def sender_name(message: dict) -> str:
    user = message.get("from") or {}
    name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    return name or user.get("username") or (message.get("chat") or {}).get("title") or "unknown"


def fetch_updates(token: str, offset: int) -> tuple[list[dict], int]:
    """Drain every pending update, following the offset until Telegram is empty."""
    updates: list[dict] = []
    while True:
        payload = call(
            token,
            "getUpdates",
            offset=offset,
            limit=100,
            timeout=0,
            allowed_updates='["message","edited_message","channel_post","edited_channel_post"]',
        )
        batch = payload.get("result", [])
        if not batch:
            return updates, offset
        updates.extend(batch)
        offset = max(update["update_id"] for update in batch) + 1


def to_entry(message: dict, edited: bool) -> dict | None:
    text = message.get("text") or message.get("caption")
    if not text or not text.strip():
        return None
    timestamp = message.get("edit_date") or message.get("date")
    return {
        "chat_id": str((message.get("chat") or {}).get("id")),
        "message_id": message.get("message_id"),
        "ts": timestamp,
        "time": bdt_time_of(timestamp),
        "from": sender_name(message),
        "text": text.strip(),
        "edited": edited,
    }


def main() -> int:
    token = require_env("TELEGRAM_BOT_TOKEN")
    allowed = allowed_chat_ids()
    state = load_state()

    updates, new_offset = fetch_updates(token, state.get("offset", 0))
    if not updates:
        print("No new updates.")
        return 0

    # Group by the Bangladesh day each message belongs to, since a single run
    # can span midnight.
    by_day: dict[str, list[dict]] = {}
    seen_chats: set[str] = set()
    skipped = 0

    for update in updates:
        for key in MESSAGE_KEYS:
            message = update.get(key)
            if not message:
                continue
            entry = to_entry(message, edited=key.startswith("edited"))
            if entry is None:
                continue
            seen_chats.add(entry["chat_id"])
            if allowed and entry["chat_id"] not in allowed:
                skipped += 1
                continue
            by_day.setdefault(bdt_date_of(entry["ts"]), []).append(entry)

    stored = 0
    for date_str, entries in by_day.items():
        path = raw_file_for(date_str)
        # Key on (chat, message) so an edited message replaces the original
        # instead of being stored twice.
        merged = {(e["chat_id"], e["message_id"]): e for e in read_jsonl(path)}
        before = len(merged)
        for entry in entries:
            merged[(entry["chat_id"], entry["message_id"])] = entry
        ordered = sorted(merged.values(), key=lambda e: (e["ts"], e["message_id"]))
        write_jsonl(path, ordered)
        stored += len(merged) - before
        print(f"{path.relative_to(path.parent.parent.parent)}: {len(ordered)} message(s)")

    save_state({**state, "offset": new_offset})
    print(f"Processed {len(updates)} update(s); {stored} new message(s); offset -> {new_offset}")
    if skipped:
        print(f"Skipped {skipped} message(s) from chats outside TELEGRAM_ALLOWED_CHAT_IDS.")
    print(f"Chat IDs seen this run: {', '.join(sorted(seen_chats)) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
