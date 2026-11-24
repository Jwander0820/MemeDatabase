"""本機發布選擇與一致快照匯出；圖片處理僅在本模組執行。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from PIL import Image

from .public_data import SCHEMA, SCHEMA_VERSION, open_readonly
from .releases import digest, validate, version_name
from .search import NORMALIZATION_VERSION
from .paths import resolve_local
from .urls import original_url

ROOT = Path(__file__).resolve().parents[2]


def update_selection(conn, key, data):
    if not isinstance(data, dict) or set(data) - {"publish_selected", "public_original_url"} or not data:
        raise ValueError("僅接受 publish_selected 與 public_original_url")
    if "publish_selected" in data and not isinstance(data["publish_selected"], bool):
        raise ValueError("publish_selected 必須為布林值")
    url = original_url(data["public_original_url"]) if "public_original_url" in data else None
    row = conn.execute("SELECT attachment_id, public_id FROM meme_entries WHERE key=?", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    with conn:
        if "publish_selected" in data:
            conn.execute("UPDATE meme_entries SET publish_selected=?,public_id=COALESCE(public_id,?) WHERE key=?",
                         (int(data["publish_selected"]), uuid.uuid4().hex, key))
        if "public_original_url" in data:
            conn.execute("UPDATE attachments SET public_original_url=? WHERE id=?", (url, row[0]))
    return selection(conn, key)


def selection(conn, key):
    row = conn.execute("""SELECT e.publish_selected,e.public_id,a.public_original_url
        FROM meme_entries e JOIN attachments a ON a.id=e.attachment_id WHERE e.key=?""", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return {"publish_selected": bool(row[0]), "public_id": row[1], "public_original_url": row[2]}


@contextmanager
def snapshot(db_path):
    with tempfile.TemporaryDirectory(prefix="anime-publish-") as tmp:
        path = Path(tmp) / "source.sqlite3"
        with closing(open_readonly(Path(db_path))) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
        with closing(open_readonly(path)) as conn:
            yield conn


def selected_rows(conn):
    return conn.execute("""SELECT e.*,a.thumbnail_path,a.public_original_url FROM meme_entries e
        JOIN attachments a ON a.id=e.attachment_id WHERE e.publish_selected=1 ORDER BY e.key""").fetchall()


def thumbnail_path(row):
    path = resolve_local(row["thumbnail_path"], "THUMBNAIL")
    if path is None:
        raise ValueError("縮圖路徑無效")
    return path


def check(conn):
    report = {"valid": True, "count": 0, "items": [], "warnings": ["請人工確認圖片內容及公開文字；自動檢查不保證內容合規。"]}
    for row in selected_rows(conn):
        errors, warnings = [], []
        if row["status"] != "ready":
            errors.append("尚非 ready")
        if not row["public_id"]:
            errors.append("缺少公開 ID，請重新設定公開選擇")
        visible = [row[name] or "" for name in ("display_title", "speaker", "anime_title", "quote_text", "ocr_text")]
        visible.extend(r[0] for r in conn.execute("SELECT tag_name FROM entry_tags WHERE entry_key=?", (row["key"],)))
        visible.extend(r[0] for r in conn.execute("SELECT alias_text FROM entry_search_aliases WHERE entry_key=?", (row["key"],)))
        if any(re.search(r"(?i)(?:file://|[a-z]:[\\/]|(?:^|\s)/(?:Users|home|data|mnt|app)/)", value) for value in visible):
            errors.append("公開搜尋文字疑似包含本地路徑，請人工檢查並修正")
        try:
            original_url(row["public_original_url"])
        except ValueError as exc:
            errors.append(str(exc))
        if not row["public_original_url"]:
            warnings.append("僅發布縮圖，無外部原圖")
        try:
            if not row["thumbnail_path"]:
                raise ValueError("缺少縮圖")
            with Image.open(thumbnail_path(row)) as image:
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError):
            errors.append("縮圖不存在或無法解碼")
        report["items"].append({"key": row["key"], "errors": errors, "warnings": warnings})
        report["valid"] = report["valid"] and not errors
        report["count"] += 1
    if not report["count"]:
        report["warnings"].append("發布集合為空，啟用後公開站將沒有圖片。")
    return report


def check_database(db_path):
    with snapshot(db_path) as conn:
        return check(conn)


@contextmanager
def export_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".export.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("已有匯出執行中；若前次異常退出，確認無匯出程序後才移除 .export.lock") from exc
    try:
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def export_release(db_path, root):
    root = Path(root).resolve()
    with export_lock(root), snapshot(db_path) as source:
        report = check(source)
        version = version_name(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12])
        reports = root / "reports"
        reports.mkdir(exist_ok=True)
        (reports / (version + ".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not report["valid"]:
            return {"exported": False, "report": report, "report_id": version}
        versions, media = root / "versions", root / "media"
        versions.mkdir(exist_ok=True)
        media.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
        final = versions / version
        media_hashes = {}
        try:
            with closing(sqlite3.connect(staging / "public.sqlite3")) as target:
                target.executescript(SCHEMA)
                target.execute("INSERT INTO metadata VALUES(?,?,?)", (SCHEMA_VERSION, NORMALIZATION_VERSION, version))
                for row in selected_rows(source):
                    output = staging / "image.webp"
                    with Image.open(thumbnail_path(row)) as image:
                        image = image.convert("RGB")
                        image.thumbnail((420, 420), Image.Resampling.LANCZOS)
                        # 新建像素圖片，避免攜帶來源 EXIF／ICC／文字 metadata。
                        clean = Image.new("RGB", image.size)
                        clean.paste(image)
                        clean.save(output, "WEBP", quality=78, method=6)
                    hash_value = digest(output)
                    filename = hash_value + ".webp"
                    destination = media / filename
                    if destination.exists():
                        if digest(destination) != hash_value:
                            raise ValueError("既有媒體內容與雜湊不一致")
                        output.unlink()
                    else:
                        os.replace(output, destination)
                    media_hashes[filename] = hash_value
                    tags = sorted({r[0] for r in source.execute("SELECT tag_name FROM entry_tags WHERE entry_key=?", (row["key"],))})
                    aliases = [r[0] for r in source.execute("SELECT alias_text FROM entry_search_aliases WHERE entry_key=? ORDER BY alias_normalized", (row["key"],))]
                    target.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (row["public_id"], row["display_title"], row["speaker"], row["anime_title"], row["quote_text"],
                                    row["ocr_text"], json.dumps(tags, ensure_ascii=False), json.dumps(aliases, ensure_ascii=False),
                                    "/media/" + filename, row["public_original_url"], row["updated_at"]))
                    target.executemany("INSERT INTO entry_tags VALUES(?,?)", [(row["public_id"], tag) for tag in tags])
                target.commit()
            manifest = {"schema_version": SCHEMA_VERSION, "normalization_version": NORMALIZATION_VERSION,
                        "version": version, "count": report["count"], "database_sha256": digest(staging / "public.sqlite3"), "media": media_hashes}
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(staging, final)
            validate(root, version)
            return {"exported": True, "version": version, "report": report}
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            if final.exists():
                shutil.rmtree(final)
            raise
