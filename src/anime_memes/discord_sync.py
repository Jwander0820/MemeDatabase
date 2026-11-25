"""Discord 同步的業務邏輯：備份、增量匯出、匯入、特殊情況偵測與同步游標。

cli 只負責解析參數、呼叫這裡的函式並印出結果。
外部依賴（匯出器、附件下載、縮圖、資料庫初始化）可由呼叫端注入，方便測試打樁。
"""
from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from . import settings
from .db import backup_database, connect, init_db
from .exporter import export_and_convert, incremental_after, save_sync_state
from .images import build_thumbnails, download_all_attachments
from .ingest import ingest_fixed_export


def sync_discord(
    *,
    db_path,
    token: str,
    channel_id: str,
    exe: str,
    raw_dir,
    fixed_dir,
    backup_dir,
    after: str | None = None,
    before: str | None = None,
    skip_attachments: bool = False,
    skip_thumbnails: bool = False,
    attachment_dir=None,
    thumbnail_dir=None,
    export_fn: Callable[..., Any] = export_and_convert,
    download_fn: Callable[..., Any] = download_all_attachments,
    thumbnails_fn: Callable[..., Any] = build_thumbnails,
    init_fn: Callable[[Any], Any] = init_db,
) -> dict:
    """備份、增量匯出、匯入並回傳報告；報告的 state 為 "partial" 代表媒體處理未完成。"""
    attachment_dir = attachment_dir if attachment_dir is not None else settings.attachment_dir()
    thumbnail_dir = thumbnail_dir if thumbnail_dir is not None else settings.thumbnail_dir()

    backup_path = backup_database(db_path, backup_dir)
    with closing(connect(db_path)) as conn:
        init_fn(conn)
        before_count = int(conn.execute("SELECT COUNT(*) FROM discord_messages").fetchone()[0])
        after = after or incremental_after(conn, channel_id)

    raw_path, fixed_path = export_fn(
        exporter_exe=exe,
        token=token,
        channel_id=channel_id,
        raw_dir=raw_dir,
        fixed_dir=fixed_dir,
        after=after,
        before=before,
    )
    exported = json.loads(Path(fixed_path).read_text(encoding="utf-8"))
    messages = [item for item in exported.get("messages", []) if isinstance(item, dict)]
    exported_ids = {str(item.get("id")) for item in messages}

    with closing(connect(db_path)) as conn:
        prior = {
            str(row["id"]): (row["timestamp"], row["timestamp_edited"], row["content"], row["is_pinned"])
            for row in conn.execute("SELECT id, timestamp, timestamp_edited, content, is_pinned FROM discord_messages")
        }
        new_ids = [str(item.get("id")) for item in messages if str(item.get("id")) not in prior]
        changed_ids = []
        for item in messages:
            message_id = str(item.get("id"))
            signature = (item.get("timestamp"), item.get("timestampEdited"), item.get("content") or "", 1 if item.get("isPinned") else 0)
            if message_id in prior and prior[message_id] != signature:
                changed_ids.append(message_id)
        summary = ingest_fixed_export(
            conn,
            fixed_path,
            channel_id=channel_id,
            raw_path=str(raw_path),
            attachment_dir=attachment_dir,
            tag_rules_path=settings.tag_rules(),
            discord_bot_token=token,
        )
        conn.execute(
            "UPDATE export_runs SET status='partial', error=?, after_value=?, before_value=? WHERE id=?",
            ("Media processing has not completed", after, before, int(summary["export_run_id"])),
        )
        conn.commit()

        special = _special_cases(conn, messages, exported_ids, new_ids, changed_ids, after, before)

        attachment_summary = None
        thumbnail_summary = None
        if not skip_attachments:
            attachment_summary = download_fn(
                conn,
                attachment_dir=attachment_dir,
                channel_id=channel_id,
                discord_bot_token=token,
                retry_failed=True,
            )
        if not skip_thumbnails:
            thumbnail_summary = thumbnails_fn(
                conn,
                thumbnail_dir=thumbnail_dir,
                missing=True,
            )

        last_message_timestamp, last_message_id = _last_message(conn, messages)
        incomplete = bool(
            (attachment_summary or {}).get("failed", 0)
            or (thumbnail_summary or {}).get("failed", 0)
        )
        run_id = int(summary["export_run_id"])
        if incomplete:
            conn.execute(
                "UPDATE export_runs SET status='partial', error=?, after_value=?, before_value=? WHERE id=?",
                ("Media processing failed; retry sync to finish", after, before, run_id),
            )
            conn.commit()
        else:
            conn.execute("UPDATE export_runs SET status='success', error=NULL WHERE id=?", (run_id,))
            save_sync_state(
                conn,
                channel_id=channel_id,
                export_run_id=run_id,
                after_value=after,
                covered_through=before,
                last_message_timestamp=last_message_timestamp,
                last_message_id=last_message_id,
            )
        row = conn.execute("SELECT * FROM discord_sync_state WHERE channel_id = ?", (channel_id,)).fetchone()
        sync_state = dict(row) if row else None
        next_after = incremental_after(conn, channel_id)

    state = "partial" if incomplete else "initial_import" if before_count == 0 else "updated" if new_ids or changed_ids else "no_changes"
    return {
        "state": state,
        "range": {"after": after, "before": before},
        "backup": str(backup_path) if backup_path else None,
        "raw_path": str(raw_path),
        "fixed_path": str(fixed_path),
        "cursor": {
            "covered_through": sync_state["covered_through"],
            "last_message_timestamp": sync_state["last_message_timestamp"],
            "last_message_id": sync_state["last_message_id"],
            "next_after": next_after,
            "overlap_days": sync_state["overlap_days"],
            "last_success_at": sync_state["last_success_at"],
        } if sync_state else None,
        "new_message_ids": new_ids,
        "ingest": summary,
        "attachments": attachment_summary,
        "thumbnails": thumbnail_summary,
        "special_cases": special,
    }


def _special_cases(conn, messages, exported_ids, new_ids, changed_ids, after, before) -> dict:
    """偵測編輯、疑似刪除、未配對與多附件的訊息。"""
    deleted_candidates = []
    if after and before:
        for row in conn.execute("SELECT id, timestamp FROM discord_messages WHERE COALESCE(type, '') != 'Manual'"):
            timestamp = str(row["timestamp"])
            if after <= timestamp[:10] < before and str(row["id"]) not in exported_ids:
                deleted_candidates.append(str(row["id"]))
    new_attachment_ids = [
        str(attachment.get("id"))
        for item in messages if str(item.get("id")) in new_ids
        for attachment in (item.get("attachments") or []) if isinstance(attachment, dict)
    ]
    return {
        "edited_message_ids": changed_ids,
        "possibly_deleted_message_ids": deleted_candidates,
        "unpaired_entry_keys": [
            str(row["key"]) for row in conn.execute(
                "SELECT key FROM meme_entries WHERE attachment_id IN ({}) AND pairing_method = 'unpaired'".format(
                    ",".join("?" for _ in new_attachment_ids)
                ),
                new_attachment_ids,
            )
        ] if new_attachment_ids else [],
        "multi_attachment_message_ids": [
            str(item.get("id")) for item in messages
            if str(item.get("id")) in new_ids and len(item.get("attachments") or []) > 1
        ],
    }


def _last_message(conn, messages) -> tuple[Any, str | None]:
    """取得本次匯出（或資料庫中）最後一則訊息的時間與 id。"""
    ordered = sorted(messages, key=lambda item: str(item.get("timestamp") or ""))
    if ordered:
        return ordered[-1].get("timestamp"), str(ordered[-1].get("id"))
    latest = conn.execute(
        "SELECT id, timestamp FROM discord_messages WHERE COALESCE(type, '') != 'Manual' ORDER BY timestamp DESC, id DESC LIMIT 1"
    ).fetchone()
    return (latest["timestamp"], str(latest["id"])) if latest else (None, None)


def sync_status(*, db_path, channel_id: str) -> dict:
    """回傳指定頻道的持久化增量游標狀態。"""
    with closing(connect(db_path)) as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT * FROM discord_sync_state WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return {
                "channel_id": channel_id,
                "initialized": False,
                "next_after": incremental_after(conn, channel_id),
            }
        state = dict(row)
        return {
            "channel_id": channel_id,
            "initialized": True,
            "covered_through": state["covered_through"],
            "last_message_timestamp": state["last_message_timestamp"],
            "last_message_id": state["last_message_id"],
            "next_after": incremental_after(conn, channel_id),
            "overlap_days": state["overlap_days"],
            "last_success_at": state["last_success_at"],
            "last_export_run_id": state["last_export_run_id"],
        }
