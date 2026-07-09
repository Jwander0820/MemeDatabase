from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path


HEADERS = [
    "Key",
    "\u4f5c\u54c1\u540d\u7a31",
    "\u8aaa\u8a71\u8005",
    "\u5716\u7247\u53f0\u8a5e",
    "\u5716\u7247\u9023\u7d50",
    "Discord \u5716\u7247\u9023\u7d50",
    "\u6a19\u7c64",
    "\u72c0\u614b",
    "\u4fe1\u5fc3\u5206\u6578",
    "\u4f86\u6e90\u8a0a\u606f\u6642\u9593",
    "\u4f86\u6e90\u8a0a\u606fID",
]


def export_notion_csv(conn: sqlite3.Connection, output_path: str | Path | None = None) -> Path:
    if output_path is None:
        output_path = Path("exports/notion") / f"anime_memes_{datetime.now().strftime('%Y%m%d')}.csv"
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT
            e.key,
            e.anime_title,
            e.speaker,
            e.quote_text,
            COALESCE(a.r2_url, a.local_path, a.discord_url) AS image_url,
            a.discord_url,
            e.status,
            e.confidence,
            m.timestamp,
            e.source_message_id,
            GROUP_CONCAT(t.tag_name, ',') AS tags
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        JOIN discord_messages m ON m.id = e.source_message_id
        LEFT JOIN entry_tags t ON t.entry_key = e.key
        GROUP BY e.key
        ORDER BY m.timestamp, e.key
        """
    ).fetchall()
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for row in rows:
            writer.writerow(
                [
                    row["key"],
                    row["anime_title"] or "",
                    row["speaker"] or "",
                    row["quote_text"] or "",
                    row["image_url"] or "",
                    row["discord_url"] or "",
                    row["tags"] or "",
                    row["status"],
                    row["confidence"],
                    row["timestamp"],
                    row["source_message_id"],
                ]
            )
    return output
