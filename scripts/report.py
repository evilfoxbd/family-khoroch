"""Turn one day of raw Telegram notes into a tidy Markdown report + CSV ledger.

Gemini does the messy part: reading free-form Bangla/English notes like
"চাল ৫ কেজি ৩৫০ টাকা" or "ডিম ১ ডজন কিনতে হবে" and splitting them into items,
quantities, amounts and categories.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

from common import (
    LEDGER_FILE,
    default_report_date,
    raw_file_for,
    read_jsonl,
    report_file_for,
)

MODEL = "gemini-3.8-flash"

CATEGORIES = [
    "চাল ও ডাল",
    "সবজি",
    "মাছ ও মাংস",
    "দুধ ও ডিম",
    "ফল",
    "মসলা",
    "তেল ও চিনি",
    "মুদি ও অন্যান্য",
    "ঘরোয়া ও পরিষ্কার",
    "যাতায়াত",
    "অন্যান্য",
]

STATUSES = ["কেনা হয়েছে", "কিনতে হবে"]

SYSTEM = """তুমি একটি বাংলাদেশি পরিবারের দৈনিক বাজার-খরচের হিসাবরক্ষক।

সারাদিন ধরে টেলিগ্রামে এলোমেলোভাবে লেখা নোট পাবে — বাংলা, ইংরেজি বা বাংলিশ মেশানো।
প্রতিটি নোট থেকে আলাদা আলাদা জিনিস বের করে গুছিয়ে দেবে।

নিয়ম:
- একটি মেসেজে একাধিক জিনিস থাকলে প্রতিটিকে আলাদা আইটেম করবে।
- amount সবসময় বাংলাদেশি টাকায়, শুধু সংখ্যা। দাম লেখা না থাকলে 0 দেবে।
- বাংলা সংখ্যা (৫, ১২০) ইংরেজি সংখ্যায় রূপান্তর করবে।
- quantity-তে পরিমাণ ও একক লিখবে (যেমন "৫ কেজি", "১ ডজন")। উল্লেখ না থাকলে খালি রাখবে।
- যেটা এখনো কেনা হয়নি, শুধু কিনতে হবে বলে লেখা — status হবে "কিনতে হবে"।
- নোটে যা লেখা নেই তা অনুমান করবে না; দাম বা পরিমাণ বানিয়ে লিখবে না।
- যে মেসেজ বাজারের সাথে সম্পর্কিত নয় বা পড়ে বোঝা যায়নি, সেটি হুবহু unclear তালিকায় রাখবে।
- summary হবে এক-দুই বাক্যে দিনের খরচের সারমর্ম, বাংলায়।"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "quantity": {"type": "string"},
                    "amount": {"type": "number"},
                    "status": {"type": "string", "enum": STATUSES},
                    "note": {"type": "string"},
                },
                "required": ["name", "category", "quantity", "amount", "status", "note"],
                "additionalProperties": False,
            },
        },
        "unclear": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["items", "unclear", "summary"],
    "additionalProperties": False,
}


def organize(entries: list[dict], date_str: str) -> dict:
    """Ask Gemini to structure the day's notes. Returns the parsed JSON object."""
    from google import genai
    from google.genai import errors

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    notes = "\n".join(f"[{e['time']}] {e['from']}: {e['text']}" for e in entries)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=f"তারিখ: {date_str}\n\nআজকের নোটগুলো:\n{notes}",
            config={
                "system_instruction": SYSTEM,
                "response_mime_type": "application/json",
                "response_json_schema": SCHEMA,
            },
        )
    except errors.APIError as e:
        raise SystemExit(f"Gemini API error ({e.code}): {e.message}")

    if response.prompt_feedback and response.prompt_feedback.block_reason:
        raise SystemExit(f"Gemini declined to process the notes: {response.prompt_feedback.block_reason}")

    candidate = response.candidates[0]
    if candidate.finish_reason != "STOP":
        raise SystemExit(f"Gemini did not finish normally: {candidate.finish_reason}")

    return json.loads(response.text)


def render(date_str: str, data: dict, entries: list[dict]) -> str:
    bought = [i for i in data["items"] if i["status"] == "কেনা হয়েছে"]
    todo = [i for i in data["items"] if i["status"] == "কিনতে হবে"]
    total = sum(i["amount"] for i in bought)

    lines = [f"# বাজার ও খরচ — {date_str}", ""]
    if data.get("summary"):
        lines += [data["summary"], ""]
    lines += [f"**মোট খরচ: {total:,.0f} টাকা**", ""]

    if bought:
        by_category: dict[str, list[dict]] = defaultdict(list)
        for item in bought:
            by_category[item["category"]].append(item)

        for category in CATEGORIES:
            items = by_category.get(category)
            if not items:
                continue
            subtotal = sum(i["amount"] for i in items)
            lines += [
                f"## {category} — {subtotal:,.0f} টাকা",
                "",
                "| জিনিস | পরিমাণ | টাকা | নোট |",
                "| --- | --- | ---: | --- |",
            ]
            for item in items:
                amount = f"{item['amount']:,.0f}" if item["amount"] else "—"
                lines.append(
                    f"| {item['name']} | {item['quantity'] or '—'} | {amount} | {item['note'] or ''} |"
                )
            lines.append("")

    if todo:
        lines += ["## কিনতে হবে", ""]
        for item in todo:
            quantity = f" — {item['quantity']}" if item["quantity"] else ""
            lines.append(f"- [ ] {item['name']}{quantity}")
        lines.append("")

    if data.get("unclear"):
        lines += ["## বোঝা যায়নি", ""]
        lines += [f"- {note}" for note in data["unclear"]]
        lines.append("")

    lines += ["<details>", "<summary>আসল মেসেজগুলো</summary>", ""]
    lines += [f"- `{e['time']}` **{e['from']}**: {e['text']}" for e in entries]
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def render_raw(date_str: str, entries: list[dict]) -> str:
    """Fallback report when no Gemini credentials are configured."""
    lines = [
        f"# বাজার ও খরচ — {date_str}",
        "",
        "> GEMINI_API_KEY সেট করা নেই, তাই মেসেজগুলো গোছানো যায়নি — নিচে হুবহু রাখা হলো।",
        "",
    ]
    lines += [f"- `{e['time']}` **{e['from']}**: {e['text']}" for e in entries]
    lines.append("")
    return "\n".join(lines)


def update_ledger(date_str: str, items: list[dict]) -> None:
    """Rewrite this date's rows so re-running a day stays idempotent."""
    header = ["date", "category", "item", "quantity", "amount", "status", "note"]
    rows = []
    if LEDGER_FILE.exists():
        with LEDGER_FILE.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [row for row in csv.reader(f)][1:]
    rows = [row for row in rows if row and row[0] != date_str]
    rows += [
        [
            date_str,
            item["category"],
            item["name"],
            item["quantity"],
            f"{item['amount']:.2f}",
            item["status"],
            item["note"],
        ]
        for item in items
    ]
    rows.sort(key=lambda row: (row[0], row[1], row[2]))

    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=default_report_date(), help="YYYY-MM-DD (default: the day that just ended)")
    args = parser.parse_args()
    date_str = args.date

    entries = read_jsonl(raw_file_for(date_str))
    if not entries:
        print(f"No messages recorded for {date_str}; nothing to report.")
        return 0

    has_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if has_key:
        data = organize(entries, date_str)
        markdown = render(date_str, data, entries)
        update_ledger(date_str, data["items"])
    else:
        print("GEMINI_API_KEY is not set - writing the raw notes without organizing them.")
        markdown = render_raw(date_str, entries)

    path = report_file_for(date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    print(f"Wrote {path.relative_to(path.parent.parent.parent)} from {len(entries)} message(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
