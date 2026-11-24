from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .attachments import (
    OCR_UNSUPPORTED_SUFFIXES,
    attachment_path,
    download_attachment,
    download_attachment_with_discord_refresh,
    is_probably_downloadable,
)
from .db import init_db


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def measure_image(path: str | Path) -> tuple[int | None, int | None]:
    try:
        with Image.open(path) as image:
            return image.size
    except (FileNotFoundError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        return None, None


def reject_invalid_image(conn: sqlite3.Connection, attachment_id: str, path: Path, width: int | None) -> bool:
    if width is not None or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}:
        return False
    conn.execute(
        """UPDATE attachments SET download_status = 'invalid_image',
           download_error = 'Image rejected by Pillow', local_path = ?,
           image_width = NULL, image_height = NULL, thumbnail_path = NULL WHERE id = ?""",
        (str(path), attachment_id),
    )
    return True


def download_all_attachments(
    conn: sqlite3.Connection,
    *,
    attachment_dir: str | Path = "data/attachments",
    channel_id: str | None = None,
    discord_bot_token: str | None = None,
    force: bool = False,
    retry_failed: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    init_db(conn)
    sql = """
        SELECT a.id, a.message_id, a.discord_url, a.file_name, a.download_status
        FROM attachments a JOIN discord_messages m ON m.id=a.message_id
        WHERE COALESCE(m.type, '') != 'Manual'
    """
    params: list[Any] = []
    if not retry_failed:
        sql += " AND a.download_status != 'failed'"
    sql += " ORDER BY a.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    summary = {
        "candidates": len(rows),
        "downloaded": 0,
        "cached": 0,
        "failed": 0,
        "skipped": 0,
    }
    for row in rows:
        if row["download_status"] == "invalid_image" and not force:
            summary["skipped"] += 1
            continue
        attachment_id = str(row["id"])
        path = attachment_path(attachment_id, row["file_name"], attachment_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 0 and not force:
            width, height = measure_image(path)
            if reject_invalid_image(conn, attachment_id, path, width):
                summary["failed"] += 1
                continue
            update_attachment_download(
                conn,
                attachment_id,
                status="cached",
                error=None,
                local_path=path,
                discord_url=row["discord_url"],
                image_width=width,
                image_height=height,
                downloaded=True,
            )
            summary["cached"] += 1
            continue

        if not is_probably_downloadable(row["discord_url"]):
            update_attachment_download(
                conn,
                attachment_id,
                status="failed",
                error="missing or invalid url",
                local_path=path,
                discord_url=row["discord_url"],
                image_width=None,
                image_height=None,
                downloaded=False,
            )
            summary["failed"] += 1
            continue

        if channel_id and discord_bot_token:
            status, error, refreshed_url = download_attachment_with_discord_refresh(
                url=row["discord_url"],
                output_path=path,
                channel_id=channel_id,
                message_id=row["message_id"],
                attachment_id=attachment_id,
                bot_token=discord_bot_token,
                force=force,
            )
        else:
            status, error = download_attachment(row["discord_url"], path, force=force)
            refreshed_url = row["discord_url"]

        if status in {"downloaded", "cached"}:
            width, height = measure_image(path)
            if reject_invalid_image(conn, attachment_id, path, width):
                summary["failed"] += 1
                continue
            summary[status] += 1
            update_attachment_download(
                conn,
                attachment_id,
                status=status,
                error=None,
                local_path=path,
                discord_url=refreshed_url,
                image_width=width,
                image_height=height,
                downloaded=True,
            )
        else:
            summary["failed"] += 1
            update_attachment_download(
                conn,
                attachment_id,
                status="failed",
                error=error,
                local_path=path,
                discord_url=refreshed_url,
                image_width=None,
                image_height=None,
                downloaded=False,
            )
        conn.commit()
    conn.commit()
    return summary


def update_attachment_download(
    conn: sqlite3.Connection,
    attachment_id: str,
    *,
    status: str,
    error: str | None,
    local_path: str | Path,
    discord_url: str | None,
    image_width: int | None,
    image_height: int | None,
    downloaded: bool,
) -> None:
    conn.execute(
        """
        UPDATE attachments
        SET download_status = ?,
            download_error = ?,
            local_path = ?,
            discord_url = ?,
            image_width = COALESCE(?, image_width),
            image_height = COALESCE(?, image_height),
            downloaded_at = CASE WHEN ? THEN COALESCE(downloaded_at, ?) ELSE downloaded_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            status,
            error,
            str(local_path),
            discord_url,
            image_width,
            image_height,
            1 if downloaded else 0,
            utc_now(),
            attachment_id,
        ),
    )


def build_thumbnails(
    conn: sqlite3.Connection,
    *,
    thumbnail_dir: str | Path = "data/thumbnails",
    size: int = 420,
    missing: bool = True,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    init_db(conn)
    rows = conn.execute(
        """
        SELECT id, file_name, local_path, thumbnail_path, download_status
        FROM attachments
        ORDER BY id
        """
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    output_dir = Path(thumbnail_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"candidates": len(rows), "created": 0, "cached": 0, "missing_source": 0, "skipped": 0, "failed": 0}
    for row in rows:
        if row["download_status"] == "invalid_image" and not force:
            summary["skipped"] += 1
            continue
        suffix = Path(row["file_name"] or "").suffix.lower()
        if suffix in OCR_UNSUPPORTED_SUFFIXES:
            summary["skipped"] += 1
            continue
        source = Path(row["local_path"] or "")
        if not source.exists() or source.stat().st_size <= 0:
            summary["missing_source"] += 1
            continue
        output = output_dir / f"{row['id']}.webp"
        if output.exists() and output.stat().st_size > 0 and missing and not force:
            conn.execute(
                "UPDATE attachments SET thumbnail_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (str(output), row["id"]),
            )
            summary["cached"] += 1
            continue
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                image.save(output, "WEBP", quality=78, method=6)
                width, height = image.size
            conn.execute(
                """
                UPDATE attachments
                SET thumbnail_path = ?,
                    image_width = COALESCE(image_width, ?),
                    image_height = COALESCE(image_height, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(output), width, height, row["id"]),
            )
            summary["created"] += 1
        except Image.DecompressionBombError:
            summary["failed"] += 1
            conn.execute(
                "UPDATE attachments SET download_status = 'invalid_image', download_error = 'Image rejected by Pillow', thumbnail_path = NULL WHERE id = ?",
                (row["id"],),
            )
        except (OSError, UnidentifiedImageError):
            summary["failed"] += 1
        conn.commit()
    conn.commit()
    return summary
