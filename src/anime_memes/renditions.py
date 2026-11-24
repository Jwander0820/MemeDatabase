"""統一的圖片衍生版本（480P 縮圖 / 720P 網頁版，皆為 WebP）。"""
from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .attachments import OCR_UNSUPPORTED_SUFFIXES
from .paths import resolve_local

UNSUPPORTED_SUFFIXES = OCR_UNSUPPORTED_SUFFIXES


@dataclass(frozen=True)
class Preset:
    long_edge: int
    short_edge: int
    quality: int


PRESETS: dict[str, Preset] = {
    "thumb": Preset(854, 480, 78),
    "web": Preset(1280, 720, 82),
}


def fit_size(width: int, height: int, preset: Preset) -> tuple[int, int]:
    """不分方向、不放大：長邊與短邊各自受限，取較小縮放比。"""
    long_side, short_side = max(width, height), min(width, height)
    if long_side <= 0 or short_side <= 0:
        return max(1, width), max(1, height)
    scale = min(1.0, preset.long_edge / long_side, preset.short_edge / short_side)
    if scale >= 1.0:
        return width, height
    # 以進位避免 16:9 來源落在 853x480 這類差 1px 的結果；上限由 preset 夾住
    def scaled(value: int, limit: int) -> int:
        return max(1, min(limit, math.ceil(value * scale - 1e-9)))

    if width >= height:
        return scaled(width, preset.long_edge), scaled(height, preset.short_edge)
    return scaled(width, preset.short_edge), scaled(height, preset.long_edge)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


def render_webp(source: str | Path, preset_name: str) -> tuple[bytes, int, int]:
    """讀取來源圖並輸出去除中繼資料的 WebP；回傳 (bytes, width, height)。"""
    preset = PRESETS[preset_name]
    with Image.open(source) as opened:
        opened.seek(0)  # 只取第一幀
        image = ImageOps.exif_transpose(opened)
        image.load()
        mode = "RGBA" if _has_alpha(image) else "RGB"
        image = image.convert(mode)
        target = fit_size(*image.size, preset)
        if target != image.size:
            image = image.resize(target, Image.Resampling.LANCZOS)
        # 貼到全新影像，確保 EXIF / ICC / 文字等中繼資料不會殘留
        clean = Image.new(mode, image.size)
        clean.paste(image)
    buffer = BytesIO()
    clean.save(buffer, "WEBP", quality=preset.quality, method=6)
    return buffer.getvalue(), clean.width, clean.height


def write_atomic(path: str | Path, data: bytes) -> None:
    """先寫入同目錄暫存檔，再以 os.replace 取代目標。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_path(row: sqlite3.Row) -> Path | None:
    resolved = resolve_local(row["local_path"], "ATTACHMENT")
    return Path(resolved) if resolved else None


def _is_unsupported(row: sqlite3.Row) -> bool:
    for value in (row["file_name"], row["local_path"]):
        if Path(value or "").suffix.lower() in UNSUPPORTED_SUFFIXES:
            return True
    return False


def ensure_web_rendition(
    conn: sqlite3.Connection,
    attachment_id: str,
    *,
    web_dir: str | Path = "data/web",
    force: bool = False,
) -> tuple[Path, str]:
    """確保附件有 720P 網頁版；不 commit，由呼叫端負責。"""
    row = conn.execute(
        "SELECT id, file_name, local_path, web_path, web_sha256 FROM attachments WHERE id = ?",
        (attachment_id,),
    ).fetchone()
    if row is None:
        raise ValueError("找不到附件")
    if _is_unsupported(row):
        raise ValueError("不支援的媒體格式")
    source = _source_path(row)
    if source is None or not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("缺少本地原圖")
    if not force and row["web_sha256"] and row["web_path"]:
        existing = resolve_local(row["web_path"], "WEB")
        if existing and Path(existing).is_file():
            return Path(existing), row["web_sha256"]
    data, _, _ = render_webp(source, "web")
    output = Path(web_dir) / f"{attachment_id}.webp"
    write_atomic(output, data)
    digest = sha256_bytes(data)
    conn.execute(
        "UPDATE attachments SET web_path = ?, web_sha256 = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (str(output), digest, attachment_id),
    )
    return output, digest


def build_web_renditions(
    conn: sqlite3.Connection,
    *,
    web_dir: str | Path = "data/web",
    selected_only: bool = True,
    missing: bool = True,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, int]:
    """批次產生 720P 網頁版；結束時 commit。"""
    sql = "SELECT a.id, a.file_name, a.local_path, a.web_path, a.web_sha256 FROM attachments a WHERE a.local_path IS NOT NULL AND a.local_path != ''"
    if selected_only:
        sql += " AND a.id IN (SELECT attachment_id FROM meme_entries WHERE publish_selected = 1)"
    sql += " ORDER BY a.id"
    rows = conn.execute(sql).fetchall()
    if limit is not None:
        rows = rows[:limit]
    summary = {"candidates": len(rows), "created": 0, "cached": 0, "missing_source": 0, "skipped": 0, "failed": 0}
    for row in rows:
        if _is_unsupported(row):
            summary["skipped"] += 1
            continue
        source = _source_path(row)
        if source is None or not source.is_file() or source.stat().st_size <= 0:
            summary["missing_source"] += 1
            continue
        cached = False
        if not force and row["web_sha256"] and row["web_path"]:
            existing = resolve_local(row["web_path"], "WEB")
            cached = bool(existing and Path(existing).is_file())
        if cached and missing:
            summary["cached"] += 1
            continue
        try:
            ensure_web_rendition(conn, row["id"], web_dir=web_dir, force=force or not missing)
            summary["created"] += 1
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError):
            summary["failed"] += 1
    conn.commit()
    return summary
