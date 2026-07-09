from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def load_rules(path: str | Path) -> list[dict[str, Any]]:
    rules_path = Path(path)
    if not rules_path.exists():
        return []
    data = json.loads(rules_path.read_text(encoding="utf-8"))
    rules = data.get("rules", [])
    return rules if isinstance(rules, list) else []


def match_tags(entry: dict[str, Any], rules: list[dict[str, Any]]) -> list[tuple[str, str]]:
    haystack = "\n".join(
        str(entry.get(field) or "")
        for field in ("anime_title", "speaker", "quote_text", "ocr_text")
    ).lower()
    matched: list[tuple[str, str]] = []
    for rule in rules:
        tag = str(rule.get("tag") or "").strip()
        if not tag:
            continue
        for keyword in rule.get("keywords", []):
            keyword_text = str(keyword).strip()
            if keyword_text and keyword_text.lower() in haystack:
                matched.append((tag, f"keyword:{keyword_text}"))
                break
    return matched


def add_entry_tag(
    conn: sqlite3.Connection,
    entry_key: str,
    tag_name: str,
    source: str,
    confidence: float | None = None,
    reason: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO tags(name, source) VALUES(?, ?)",
        (tag_name, source),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO entry_tags(entry_key, tag_name, source, confidence, reason)
        VALUES(?, ?, ?, ?, ?)
        """,
        (entry_key, tag_name, source, confidence, reason),
    )


def import_manual_tags(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    import csv

    count = 0
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            for tag in split_tags(row.get("tags") or ""):
                add_entry_tag(conn, key, tag, "manual", None, row.get("notes"))
                count += 1
    conn.commit()
    return count


def import_llm_suggestions(conn: sqlite3.Connection, jsonl_path: str | Path) -> tuple[int, int]:
    imported = 0
    errors = 0
    for line_number, line in enumerate(Path(jsonl_path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            key = str(item["key"]).strip()
            confidence = float(item.get("confidence", 0.0))
            reason = str(item.get("reason") or "")
            tags = item.get("suggested_tags")
            if not key or not isinstance(tags, list):
                raise ValueError("missing key or suggested_tags")
            for tag in tags:
                tag_name = str(tag).strip()
                if tag_name:
                    add_entry_tag(conn, key, tag_name, "local_llm", confidence, reason)
                    imported += 1
        except Exception as exc:
            errors += 1
            conn.execute(
                "INSERT INTO review_items(reason, details) VALUES(?, ?)",
                ("llm_import_error", f"line {line_number}: {exc}"),
            )
    conn.commit()
    return imported, errors


def split_tags(value: str) -> list[str]:
    normalized = value
    for separator in ("\uff0c", "\u3001", "\uff1b", ";", "\n"):
        normalized = normalized.replace(separator, ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]
