from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .attachments import (
    attachment_path,
    download_attachment,
    download_attachment_with_discord_refresh,
    is_ocr_supported_file,
    is_probably_downloadable,
)
from .db import init_db
from .matching import pair_text_for_attachment, parse_meme_text
from .ocr import run_ocr, similarity
from .tags import add_entry_tag, load_rules, match_tags


def ingest_fixed_export(
    conn: sqlite3.Connection,
    fixed_path: str | Path,
    *,
    channel_id: str = "",
    raw_path: str | None = None,
    attachment_dir: str | Path = "data/attachments",
    tag_rules_path: str | Path = "config/tags.rules.json",
    download_attachments: bool = False,
    run_ocr: bool = False,
    ocr_engine: str = "paddle",
    ocr_device: str = "auto",
    ocr_scope: str = "all",
    ocr_confidence_threshold: float = 0.7,
    discord_bot_token: str | None = None,
) -> dict[str, int]:
    init_db(conn)
    path = Path(fixed_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    date_range = data.get("dateRange") or {}
    messages = [message for message in data.get("messages", []) if isinstance(message, dict)]
    ordered_messages = sorted(messages, key=lambda item: str(item.get("timestamp") or ""))
    first_message = ordered_messages[0] if ordered_messages else None
    last_message = ordered_messages[-1] if ordered_messages else None
    export_run_id = create_export_run(
        conn,
        channel_id=channel_id,
        after_value=date_range.get("after"),
        before_value=date_range.get("before"),
        raw_path=raw_path,
        fixed_path=str(path),
        message_count=len(messages),
        exported_at=data.get("exportedAt"),
        first_message=first_message,
        last_message=last_message,
    )

    existing_message_signatures = {
        str(row["id"]): tuple(row[column] for column in ("timestamp", "timestamp_edited", "content", "is_pinned"))
        for row in conn.execute(
            "SELECT id, timestamp, timestamp_edited, content, is_pinned FROM discord_messages"
        )
    }
    changed_message_ids: set[str] = set()
    new_message_ids: set[str] = set()
    for message in messages:
        message_id = str(message.get("id"))
        signature = (
            message.get("timestamp"),
            message.get("timestampEdited"),
            message.get("content") or "",
            1 if message.get("isPinned") else 0,
        )
        previous = existing_message_signatures.get(message_id)
        if previous is None:
            new_message_ids.add(message_id)
        elif previous != signature:
            changed_message_ids.add(message_id)
        upsert_message(conn, message, export_run_id)
        for attachment in message.get("attachments") or []:
            if isinstance(attachment, dict):
                upsert_attachment(conn, message, attachment, attachment_dir)

    used_text_ids: set[str] = set()
    created_entries = 0
    inserted_entries = 0
    review_count = 0
    tag_rules = load_rules(tag_rules_path)

    for index, message in enumerate(messages):
        attachments = [item for item in message.get("attachments") or [] if isinstance(item, dict)]
        if not attachments:
            continue
        pairing = pair_text_for_attachment(messages, index, used_text_ids)
        parsed = parse_meme_text(pairing.text_message.get("content") if pairing.text_message else None)
        if pairing.text_message is not None and pairing.method != "same_message":
            used_text_ids.add(pairing.text_message["id"])

        for attachment in attachments:
            key = f"meme_{attachment.get('id')}"
            confidence = clamp(pairing.confidence + parsed.confidence_delta)
            status = "ready" if confidence >= 0.7 and parsed.quote_text else "needs_review"
            if len(attachments) > 1:
                status = "needs_review"
            entry = {
                "key": key,
                "attachment_id": attachment.get("id"),
                "source_message_id": message.get("id"),
                "text_message_id": (pairing.text_message or {}).get("id"),
                "anime_title": parsed.anime_title,
                "speaker": parsed.speaker,
                "quote_text": parsed.quote_text or "",
                "pairing_method": pairing.method,
                "confidence": confidence,
                "status": status,
                "ocr_text": None,
                "ocr_score": None,
                "ocr_status": "skipped",
            }

            if download_attachments:
                maybe_download(
                    conn,
                    attachment,
                    attachment_dir,
                    channel_id=channel_id,
                    message_id=message.get("id"),
                    bot_token=discord_bot_token,
                )
            should_ocr = run_ocr and should_run_ocr_for_entry(
                entry, ocr_scope, ocr_confidence_threshold
            )
            if should_ocr:
                apply_ocr(conn, entry, attachment, attachment_dir, ocr_engine, ocr_device)
                if entry["ocr_score"] is not None:
                    if entry["ocr_score"] >= 0.45:
                        entry["confidence"] = clamp(float(entry["confidence"]) + 0.08)
                    else:
                        entry["status"] = "needs_review"

            inserted = upsert_entry(conn, entry)
            created_entries += 1
            if inserted:
                inserted_entries += 1
                for tag, reason in match_tags(entry, tag_rules):
                    add_entry_tag(conn, key, tag, "auto", 1.0, reason)
                if entry["status"] == "needs_review":
                    review_count += 1
                    add_review_item(conn, entry, pairing.reason or "low confidence or incomplete data")

    conn.execute(
        """
        UPDATE export_runs
        SET status = 'success', new_message_count = ?, changed_message_count = ?, entries_inserted = ?
        WHERE id = ?
        """,
        (len(new_message_ids), len(changed_message_ids), inserted_entries, export_run_id),
    )
    conn.commit()
    return {
        "messages": len(messages),
        "attachments": count_rows(conn, "attachments"),
        "entries_processed": created_entries,
        "entries_inserted": inserted_entries,
        "export_run_id": export_run_id,
        "new_messages": len(new_message_ids),
        "changed_messages": len(changed_message_ids),
        "review_items_added": review_count,
    }


def create_export_run(
    conn: sqlite3.Connection,
    *,
    channel_id: str,
    after_value: str | None,
    before_value: str | None,
    raw_path: str | None,
    fixed_path: str,
    message_count: int,
    exported_at: str | None,
    first_message: dict[str, Any] | None,
    last_message: dict[str, Any] | None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO export_runs(
            channel_id, after_value, before_value, raw_path, fixed_path,
            message_count, status, exported_at,
            first_message_timestamp, first_message_id, last_message_timestamp, last_message_id
        )
        VALUES(?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            after_value,
            before_value,
            raw_path,
            fixed_path,
            message_count,
            exported_at,
            (first_message or {}).get("timestamp"),
            (first_message or {}).get("id"),
            (last_message or {}).get("timestamp"),
            (last_message or {}).get("id"),
        ),
    )
    return int(cursor.lastrowid)


def upsert_message(conn: sqlite3.Connection, message: dict[str, Any], export_run_id: int) -> None:
    author = message.get("author") or {}
    conn.execute(
        """
        INSERT INTO discord_messages(
            id, type, timestamp, timestamp_edited, call_ended_timestamp, is_pinned,
            content, author_id, author_name, author_discriminator, author_is_bot, export_run_id
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type=excluded.type,
            timestamp=excluded.timestamp,
            timestamp_edited=excluded.timestamp_edited,
            call_ended_timestamp=excluded.call_ended_timestamp,
            is_pinned=excluded.is_pinned,
            content=excluded.content,
            author_id=excluded.author_id,
            author_name=excluded.author_name,
            author_discriminator=excluded.author_discriminator,
            author_is_bot=excluded.author_is_bot,
            export_run_id=excluded.export_run_id,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            message.get("id"),
            message.get("type"),
            message.get("timestamp"),
            message.get("timestampEdited"),
            message.get("callEndedTimestamp"),
            1 if message.get("isPinned") else 0,
            message.get("content") or "",
            author.get("id"),
            author.get("name"),
            author.get("discriminator"),
            1 if author.get("isBot") else 0,
            export_run_id,
        ),
    )


def upsert_attachment(
    conn: sqlite3.Connection,
    message: dict[str, Any],
    attachment: dict[str, Any],
    attachment_dir: str | Path,
) -> None:
    local_path = attachment_path(str(attachment.get("id")), attachment.get("fileName"), attachment_dir)
    conn.execute(
        """
        INSERT INTO attachments(id, message_id, discord_url, file_name, file_size_bytes, local_path)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            message_id=excluded.message_id,
            discord_url=excluded.discord_url,
            file_name=excluded.file_name,
            file_size_bytes=excluded.file_size_bytes,
            local_path=excluded.local_path,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            attachment.get("id"),
            message.get("id"),
            attachment.get("url"),
            attachment.get("fileName"),
            attachment.get("fileSizeBytes"),
            str(local_path),
        ),
    )


def upsert_entry(conn: sqlite3.Connection, entry: dict[str, Any]) -> bool:
    cursor = conn.execute(
        """
        INSERT INTO meme_entries(
            key, attachment_id, source_message_id, text_message_id, anime_title, speaker,
            quote_text, pairing_method, confidence, status, ocr_text, ocr_score, ocr_status
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO NOTHING
        """,
        (
            entry["key"],
            entry["attachment_id"],
            entry["source_message_id"],
            entry["text_message_id"],
            entry["anime_title"],
            entry["speaker"],
            entry["quote_text"],
            entry["pairing_method"],
            entry["confidence"],
            entry["status"],
            entry["ocr_text"],
            entry["ocr_score"],
            entry["ocr_status"],
        ),
    )
    return cursor.rowcount > 0


def maybe_download(
    conn: sqlite3.Connection,
    attachment: dict[str, Any],
    attachment_dir: str | Path,
    *,
    channel_id: str | None = None,
    message_id: str | None = None,
    bot_token: str | None = None,
) -> None:
    attachment_id = str(attachment.get("id"))
    url = attachment.get("url")
    path = attachment_path(attachment_id, attachment.get("fileName"), attachment_dir)
    if not is_probably_downloadable(url):
        status, error = "failed", "missing or invalid url"
        refreshed_url = url
    elif channel_id and message_id and bot_token:
        status, error, refreshed_url = download_attachment_with_discord_refresh(
            url=str(url),
            output_path=path,
            channel_id=channel_id,
            message_id=message_id,
            attachment_id=attachment_id,
            bot_token=bot_token,
        )
    else:
        status, error = download_attachment(str(url), path)
        refreshed_url = url
    conn.execute(
        """
        UPDATE attachments
        SET download_status = ?, download_error = ?, local_path = ?, discord_url = ?
        WHERE id = ?
        """,
        (status, error, str(path), refreshed_url, attachment_id),
    )


def apply_ocr(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    attachment: dict[str, Any],
    attachment_dir: str | Path,
    engine: str = "paddle",
    device: str = "auto",
) -> None:
    if not is_ocr_supported_file(attachment.get("fileName")):
        suffix = Path(attachment.get("fileName") or "").suffix.lower() or "unknown"
        entry["ocr_status"] = f"skipped: unsupported media type ({suffix})"
        return
    path = attachment_path(str(attachment.get("id")), attachment.get("fileName"), attachment_dir)
    if not path.exists():
        entry["ocr_status"] = "skipped: missing local image"
        return
    ocr_text, status = run_ocr(path, engine=engine, device=device)
    entry["ocr_text"] = ocr_text or None
    entry["ocr_status"] = status
    entry["ocr_score"] = similarity(entry["quote_text"], ocr_text) if ocr_text else None
    if not status.startswith("ok"):
        conn.execute(
            "INSERT INTO review_items(entry_key, attachment_id, reason, details) VALUES(?, ?, ?, ?)",
            (entry["key"], attachment.get("id"), "ocr_skipped", status),
        )


def should_run_ocr_for_entry(entry: dict[str, Any], scope: str, threshold: float) -> bool:
    if scope == "all":
        return True
    if scope == "review":
        return entry.get("status") == "needs_review" or float(entry.get("confidence") or 0.0) < threshold
    return False


def add_review_item(conn: sqlite3.Connection, entry: dict[str, Any], reason: str) -> None:
    conn.execute(
        "DELETE FROM review_items WHERE entry_key = ? AND reason = ? AND status = 'open'",
        (entry["key"], reason),
    )
    conn.execute(
        "INSERT INTO review_items(entry_key, attachment_id, reason, details) VALUES(?, ?, ?, ?)",
        (
            entry["key"],
            entry["attachment_id"],
            reason,
            json.dumps(
                {
                    "pairing_method": entry["pairing_method"],
                    "confidence": entry["confidence"],
                    "quote_text": entry["quote_text"],
                },
                ensure_ascii=False,
            ),
        ),
    )

def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def clamp(value: float) -> float:
    return max(0.0, min(1.0, round(value, 3)))


def ocr_review_entries(
    conn: sqlite3.Connection,
    *,
    attachment_dir: str | Path = "data/attachments",
    engine: str = "paddle",
    device: str = "auto",
    confidence_threshold: float = 0.7,
    limit: int | None = None,
    download_missing: bool = False,
    retry_failed_downloads: bool = False,
    order: str = "confidence",
    channel_id: str | None = None,
    discord_bot_token: str | None = None,
) -> dict[str, int]:
    init_db(conn)
    sql = """
        SELECT
            e.key, e.attachment_id, e.source_message_id, e.text_message_id,
            e.anime_title, e.speaker, e.quote_text, e.pairing_method,
            e.confidence, e.status, a.file_name, a.discord_url
        FROM meme_entries e
        JOIN attachments a ON a.id = e.attachment_id
        JOIN discord_messages m ON m.id = e.source_message_id
        WHERE (e.status = 'needs_review'
           OR e.confidence < ?)
    """
    if not retry_failed_downloads:
        sql += " AND a.download_status != 'failed'"
    if order == "newest":
        sql += " ORDER BY m.timestamp DESC, e.key"
    elif order == "oldest":
        sql += " ORDER BY m.timestamp ASC, e.key"
    else:
        sql += " ORDER BY e.confidence ASC, e.key"
    params: list[Any] = [confidence_threshold]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    processed = 0
    improved = 0
    failed = 0
    download_failed = 0
    skipped_unsupported = 0
    for row in rows:
        attachment = {
            "id": row["attachment_id"],
            "fileName": row["file_name"],
            "url": row["discord_url"],
        }
        if not is_ocr_supported_file(row["file_name"]):
            skipped_unsupported += 1
            entry = dict(row)
            entry["ocr_text"] = None
            entry["ocr_score"] = None
            suffix = Path(row["file_name"] or "").suffix.lower() or "unknown"
            entry["ocr_status"] = f"skipped: unsupported media type ({suffix})"
            upsert_entry(conn, entry)
            continue
        if download_missing:
            maybe_download(
                conn,
                attachment,
                attachment_dir,
                channel_id=channel_id,
                message_id=row["source_message_id"],
                bot_token=discord_bot_token,
            )
            refreshed = conn.execute(
                "SELECT download_status, download_error FROM attachments WHERE id = ?",
                (row["attachment_id"],),
            ).fetchone()
            if refreshed and refreshed["download_status"] == "failed":
                download_failed += 1
                add_download_failed_review_item(
                    conn,
                    row["key"],
                    row["attachment_id"],
                    refreshed["download_error"],
                )
                continue
        entry = dict(row)
        entry["ocr_text"] = None
        entry["ocr_score"] = None
        entry["ocr_status"] = "skipped"
        apply_ocr(conn, entry, attachment, attachment_dir, engine, device)
        if entry["ocr_status"].startswith("ok"):
            processed += 1
            if entry["ocr_score"] is not None and entry["ocr_score"] >= 0.45:
                entry["confidence"] = clamp(float(entry["confidence"]) + 0.08)
                if entry["confidence"] >= confidence_threshold and entry["quote_text"]:
                    entry["status"] = "ready"
                improved += 1
            else:
                entry["status"] = "needs_review"
        else:
            failed += 1
        upsert_entry(conn, entry)
        if entry["status"] == "needs_review":
            add_review_item(conn, entry, "ocr_review_unresolved")
    conn.commit()
    return {
        "candidates": len(rows),
        "processed": processed,
        "improved": improved,
        "failed": failed,
        "download_failed": download_failed,
        "skipped_unsupported": skipped_unsupported,
    }


def add_download_failed_review_item(
    conn: sqlite3.Connection,
    entry_key: str,
    attachment_id: str,
    error: str | None,
) -> None:
    conn.execute(
        "DELETE FROM review_items WHERE entry_key = ? AND reason = ? AND status = 'open'",
        (entry_key, "attachment_download_failed"),
    )
    conn.execute(
        "INSERT INTO review_items(entry_key, attachment_id, reason, details) VALUES(?, ?, ?, ?)",
        (entry_key, attachment_id, "attachment_download_failed", error),
    )
