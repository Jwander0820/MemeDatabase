from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .tags import add_entry_tag, split_tags


H = {
    "work_title": "\u4f5c\u54c1\u540d\u7a31",
    "speaker": "\u8aaa\u8a71\u8005",
    "quote": "\u5716\u7247\u53f0\u8a5e",
    "confidence": "\u4fe1\u5fc3\u5206\u6578",
    "ocr_score": "OCR\u76f8\u4f3c\u5ea6",
    "ocr_status": "OCR\u72c0\u614b",
    "ocr_text": "OCR\u6587\u5b57",
    "file_name": "\u6a94\u540d",
    "local_path": "\u672c\u5730\u8def\u5f91",
    "discord_url": "Discord\u5716\u7247\u9023\u7d50",
    "source_message_id": "\u4f86\u6e90\u8a0a\u606fID",
    "text_message_id": "\u6587\u5b57\u8a0a\u606fID",
    "pairing_method": "\u914d\u5c0d\u65b9\u5f0f",
    "tags": "\u6a19\u7c64",
    "status": "\u72c0\u614b",
    "notes": "\u5099\u8a3b",
    "fixed_work_title": "\u4fee\u6b63\u4f5c\u54c1\u540d\u7a31",
    "fixed_speaker": "\u4fee\u6b63\u8aaa\u8a71\u8005",
    "fixed_quote": "\u4fee\u6b63\u5716\u7247\u53f0\u8a5e",
    "fixed_tags": "\u4fee\u6b63\u6a19\u7c64",
    "fixed_status": "\u4fee\u6b63\u72c0\u614b",
    "fixed_notes": "\u4fee\u6b63\u5099\u8a3b",
}

REVIEW_COLUMNS = [
    ("key", "Key"),
    ("anime_title", H["work_title"]),
    ("speaker", H["speaker"]),
    ("quote_text", H["quote"]),
    ("confidence", H["confidence"]),
    ("ocr_score", H["ocr_score"]),
    ("ocr_status", H["ocr_status"]),
    ("ocr_text", H["ocr_text"]),
    ("file_name", H["file_name"]),
    ("local_path", H["local_path"]),
    ("discord_url", H["discord_url"]),
    ("source_message_id", H["source_message_id"]),
    ("text_message_id", H["text_message_id"]),
    ("pairing_method", H["pairing_method"]),
    ("tags", H["tags"]),
    ("status", H["status"]),
    ("notes", H["notes"]),
    ("fixed_anime_title", H["fixed_work_title"]),
    ("fixed_speaker", H["fixed_speaker"]),
    ("fixed_quote_text", H["fixed_quote"]),
    ("fixed_tags", H["fixed_tags"]),
    ("fixed_status", H["fixed_status"]),
    ("fixed_notes", H["fixed_notes"]),
]


def export_review_csv(conn: sqlite3.Connection, output_path: str | Path | None = None) -> Path:
    if output_path is None:
        output_path = Path("exports/review") / f"ocr_needs_review_{datetime.now().strftime('%Y%m%d')}.csv"
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT
            e.key,
            COALESCE(e.anime_title, '') AS anime_title,
            COALESCE(e.speaker, '') AS speaker,
            e.quote_text,
            e.confidence,
            COALESCE(e.ocr_score, '') AS ocr_score,
            e.ocr_status,
            COALESCE(e.ocr_text, '') AS ocr_text,
            a.file_name,
            a.local_path,
            a.discord_url,
            e.source_message_id,
            COALESCE(e.text_message_id, '') AS text_message_id,
            e.pairing_method,
            COALESCE(GROUP_CONCAT(DISTINCT t.tag_name), '') AS tags,
            e.status,
            COALESCE(e.notes, '') AS notes
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        LEFT JOIN entry_tags t ON t.entry_key = e.key
        WHERE e.status = 'needs_review'
        GROUP BY e.key
        ORDER BY e.confidence ASC, e.source_message_id DESC
        """
    ).fetchall()
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([label for _, label in REVIEW_COLUMNS])
        for row in rows:
            values = dict(row)
            values.update(
                {
                    "fixed_anime_title": "",
                    "fixed_speaker": "",
                    "fixed_quote_text": "",
                    "fixed_tags": "",
                    "fixed_status": "",
                    "fixed_notes": "",
                }
            )
            writer.writerow([values.get(key, "") for key, _ in REVIEW_COLUMNS])
    return output


def import_review_corrections(
    conn: sqlite3.Connection,
    csv_path: str | Path,
    *,
    mark_ready: bool = False,
) -> dict[str, int]:
    updated = 0
    missing = 0
    tags_added = 0
    resolved = 0
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration:
            return {"updated": 0, "missing": 0, "tags_added": 0, "resolved": 0}
        for row in reader:
            values = row_values(headers, row)
            key = pick(values, "Key", "key").strip()
            if not key:
                continue
            exists = conn.execute("SELECT 1 FROM meme_entries WHERE key = ?", (key,)).fetchone()
            if not exists:
                missing += 1
                continue

            anime_title = prefer(values, H["fixed_work_title"], "fixed_anime_title", H["work_title"], "anime_title")
            speaker = prefer(values, H["fixed_speaker"], "fixed_speaker", H["speaker"], "speaker")
            quote_text = prefer(values, H["fixed_quote"], "fixed_quote_text", H["quote"], "quote_text")
            notes = prefer(values, H["fixed_notes"], "fixed_notes", H["notes"], "notes")
            fixed_status = prefer(values, H["fixed_status"], "fixed_status").strip()
            base_status = prefer(values, H["status"], "status").strip()
            status = fixed_status
            if mark_ready and not status and quote_text.strip():
                status = "ready"
            if not status:
                status = base_status
            if status not in {"", "ready", "needs_review", "ignored"}:
                status = ""

            updates: list[str] = []
            params: list[object] = []
            for column, value, nullable in (
                ("anime_title", anime_title, True),
                ("speaker", speaker, True),
                ("quote_text", quote_text, False),
                ("notes", notes, True),
            ):
                if value != "":
                    updates.append(f"{column} = ?")
                    params.append(value.strip() or None if nullable else value)
            if status:
                updates.append("status = ?")
                params.append(status)
                if status == "ready":
                    updates.append("confidence = MAX(confidence, ?)")
                    params.append(0.95)
            if updates:
                updates.append("updated_at = CURRENT_TIMESTAMP")
                params.append(key)
                conn.execute(f"UPDATE meme_entries SET {', '.join(updates)} WHERE key = ?", params)
                updated += 1

            tags_value = prefer(values, H["fixed_tags"], "fixed_tags", H["tags"], "tags")
            for tag in split_tags(tags_value):
                add_entry_tag(conn, key, tag, "manual", None, "review correction import")
                tags_added += 1
            if status == "ready":
                conn.execute(
                    "UPDATE review_items SET status = 'resolved' WHERE entry_key = ? AND status = 'open'",
                    (key,),
                )
                resolved += 1
    conn.commit()
    return {"updated": updated, "missing": missing, "tags_added": tags_added, "resolved": resolved}


def row_values(headers: list[str], row: list[str]) -> dict[str, str]:
    if is_broken_header(headers):
        return row_values_by_position(row)
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}


def is_broken_header(headers: Iterable[str]) -> bool:
    header_set = set(headers)
    return "Key" in header_set and H["work_title"] not in header_set and any("?" in item for item in header_set)


def row_values_by_position(row: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, (key, label) in enumerate(REVIEW_COLUMNS):
        value = row[index] if index < len(row) else ""
        values[key] = value
        values[label] = value
    return values


def prefer(values: dict[str, str], *names: str) -> str:
    for name in names:
        value = values.get(name)
        if value is not None and value.strip():
            return value
    return ""


def pick(values: dict[str, str], *names: str) -> str:
    for name in names:
        value = values.get(name)
        if value is not None:
            return value
    return ""
