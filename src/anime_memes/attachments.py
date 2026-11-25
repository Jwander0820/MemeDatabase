from __future__ import annotations

import json
import http.client
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
OCR_UNSUPPORTED_SUFFIXES = {".gif", ".mp4", ".mov", ".webm", ".m4v"}


def attachment_path(attachment_id: str, file_name: str | None, directory: str | Path) -> Path:
    suffix = Path(file_name or "").suffix.lower() or ".bin"
    return Path(directory) / f"{attachment_id}{suffix}"


def is_ocr_supported_file(file_name: str | None) -> bool:
    suffix = Path(file_name or "").suffix.lower()
    if suffix in OCR_UNSUPPORTED_SUFFIXES:
        return False
    return suffix in IMAGE_SUFFIXES or suffix == ""


def download_attachment(url: str, output_path: str | Path, timeout: int = 30, *, force: bool = False) -> tuple[str, str | None]:
    path = Path(output_path)
    if path.exists() and path.stat().st_size > 0 and not force:
        return "cached", None
    path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = None
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".part", delete=False) as handle:
                partial_path = Path(handle.name)
                shutil.copyfileobj(response, handle)
            length = response.headers.get("Content-Length")
            if length is not None and partial_path.stat().st_size != int(length):
                raise OSError("incomplete attachment download")
            if partial_path.stat().st_size == 0:
                raise OSError("empty attachment download")
            os.replace(partial_path, path)
        return "downloaded", None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
        return "failed", str(exc)
    finally:
        if partial_path is not None:
            partial_path.unlink(missing_ok=True)


def download_attachment_with_discord_refresh(
    *,
    url: str | None,
    output_path: str | Path,
    channel_id: str | None,
    message_id: str | None,
    attachment_id: str,
    bot_token: str | None,
    timeout: int = 30,
    force: bool = False,
) -> tuple[str, str | None, str | None]:
    if url and is_probably_downloadable(url):
        status, error = download_attachment(url, output_path, timeout=timeout, force=force)
        if status != "failed" or "HTTP Error 403" not in (error or ""):
            return status, error, url

    if not channel_id or not message_id or not bot_token:
        return "failed", "missing Discord API refresh credentials", url

    refreshed_url = refresh_attachment_url(
        channel_id=channel_id,
        message_id=message_id,
        attachment_id=attachment_id,
        bot_token=bot_token,
        timeout=timeout,
    )
    if not refreshed_url:
        return "failed", "Discord API did not return matching attachment URL", url
    status, error = download_attachment(refreshed_url, output_path, timeout=timeout, force=force)
    return status, error, refreshed_url


def refresh_attachment_url(
    *,
    channel_id: str,
    message_id: str,
    attachment_id: str,
    bot_token: str,
    timeout: int = 30,
) -> str | None:
    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
        headers={
            "Authorization": f"Bot {bot_token}",
            "User-Agent": "MemeDatabase/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    for attachment in payload.get("attachments", []):
        if str(attachment.get("id")) == str(attachment_id):
            return attachment.get("url")
    return None


def is_probably_downloadable(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def update_local_paths(conn: sqlite3.Connection, attachment_dir: str | Path) -> None:
    rows = conn.execute("SELECT id, file_name FROM attachments").fetchall()
    for row in rows:
        path = attachment_path(row["id"], row["file_name"], attachment_dir)
        conn.execute("UPDATE attachments SET local_path = ? WHERE id = ?", (str(path), row["id"]))
    conn.commit()
