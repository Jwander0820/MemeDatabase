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

from PIL import Image, UnidentifiedImageError

from .db import connect
from .public_data import SCHEMA, SCHEMA_VERSION, open_readonly
from .releases import digest, validate, version_name
from .search import NORMALIZATION_VERSION
from .paths import resolve_local
from .r2 import R2Config, R2Error, store_from_env
from .renditions import UNSUPPORTED_SUFFIXES, ensure_web_rendition, render_webp, sha256_bytes
from .urls import original_url

ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORE = object()  # 代表「由環境變數建立 R2 儲存」


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
    row = conn.execute("""SELECT e.publish_selected,e.public_id,a.public_original_url,a.r2_url
        FROM meme_entries e JOIN attachments a ON a.id=e.attachment_id WHERE e.key=?""", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return {"publish_selected": bool(row[0]), "public_id": row[1], "public_original_url": row[2], "r2_url": row[3]}


@contextmanager
def snapshot(db_path):
    with tempfile.TemporaryDirectory(prefix="anime-publish-") as tmp:
        path = Path(tmp) / "source.sqlite3"
        with closing(open_readonly(Path(db_path))) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
        with closing(open_readonly(path)) as conn:
            yield conn


def selected_rows(conn):
    return conn.execute("""SELECT e.*,a.thumbnail_path,a.public_original_url,
        a.local_path,a.file_name,a.r2_url FROM meme_entries e
        JOIN attachments a ON a.id=e.attachment_id WHERE e.publish_selected=1 ORDER BY e.key""").fetchall()


def thumbnail_path(row):
    path = resolve_local(row["thumbnail_path"], "THUMBNAIL")
    if path is None:
        raise ValueError("縮圖路徑無效")
    return path


def local_original(row):
    """回傳可用的本地原圖路徑；不存在、路徑無效或為 GIF／影片時回傳 None。"""
    for value in (row["file_name"], row["local_path"]):
        if Path(value or "").suffix.lower() in UNSUPPORTED_SUFFIXES:
            return None
    path = resolve_local(row["local_path"], "ATTACHMENT")
    if path is None or not Path(path).is_file():
        return None
    return Path(path)


def _decodable(path):
    try:
        with Image.open(path) as image:
            image.load()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def _thumbnail_source(row):
    """公開縮圖來源：優先本地原圖，其次既有縮圖；皆不可用時回傳 None。"""
    original = local_original(row)
    if original is not None and _decodable(original):
        return original
    if row["thumbnail_path"]:
        try:
            path = thumbnail_path(row)
        except ValueError:
            return None
        if _decodable(path):
            return path
    return None


def check(conn, *, r2_enabled=False):
    report = {"valid": True, "count": 0, "items": [], "errors": [],
              "warnings": ["請人工確認圖片內容及公開文字；自動檢查不保證內容合規。"]}
    if r2_enabled:
        report["warnings"].append("匯出時會為尚無外部原圖的項目產生 720P 大圖，並將缺少的檔案上傳至 R2。")
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
            original_url(row["public_original_url"] or row["r2_url"])
        except ValueError as exc:
            errors.append(str(exc))
        original = local_original(row)
        if not row["public_original_url"] and not row["r2_url"]:
            if not r2_enabled or original is None:
                warnings.append("僅發布縮圖，無外部原圖")
        if _thumbnail_source(row) is None:
            errors.append("縮圖不存在或無法解碼")
        report["items"].append({"key": row["key"], "errors": errors, "warnings": warnings})
        report["valid"] = report["valid"] and not errors
        report["count"] += 1
    if not report["count"]:
        report["warnings"].append("發布集合為空，啟用後公開站將沒有圖片。")
    return report


def check_database(db_path):
    try:
        r2_enabled = R2Config.from_env() is not None
        config_error = None
    except ValueError as exc:
        r2_enabled, config_error = False, str(exc)
    with snapshot(db_path) as conn:
        report = check(conn, r2_enabled=r2_enabled)
    if config_error:
        report["errors"].append(config_error)
        report["valid"] = False
    return report


def prepare_public_media(db_path, store, *, web_dir=None):
    """為發布選取且無手動原圖網址的項目產生 720P 並上傳 R2；可重複執行。"""
    web_dir = web_dir or os.environ.get("MEMES_WEB_DIR") or "data/web"
    summary = {"uploaded": 0, "reused": 0, "skipped": 0, "errors": [], "warnings": []}
    with closing(connect(db_path)) as conn:
        rows = conn.execute("""SELECT e.key,a.id,a.r2_key,a.r2_url FROM meme_entries e
            JOIN attachments a ON a.id=e.attachment_id
            WHERE e.publish_selected=1 AND COALESCE(a.public_original_url,'')='' ORDER BY e.key""").fetchall()
        for row in rows:
            key = row["key"]
            try:
                path, sha = ensure_web_rendition(conn, row["id"], web_dir=web_dir)
                object_key = store.key_for(sha)
                if row["r2_key"] == object_key and row["r2_url"]:
                    conn.commit()
                    summary["reused"] += 1
                    continue
                data = Path(path).read_bytes()
                store.upload_if_missing(object_key, data)
                conn.execute("UPDATE attachments SET r2_key=?, r2_url=? WHERE id=?",
                             (object_key, store.public_url(object_key), row["id"]))
                conn.commit()
                summary["uploaded"] += 1
            except (ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                conn.rollback()
                summary["skipped"] += 1
                summary["warnings"].append({"key": key, "warning": str(exc) or "無法產生 720P 大圖"})
            except (R2Error, OSError) as exc:
                conn.rollback()
                summary["errors"].append({"key": key, "error": str(exc) or type(exc).__name__})
    return summary


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


def export_release(db_path, root, *, store=_DEFAULT_STORE):
    root = Path(root).resolve()
    if store is _DEFAULT_STORE:
        store = store_from_env()  # R2 設定不完整時拋出 ValueError，與其他輸入錯誤一致
    with export_lock(root):
        media_summary = None
        if store is not None:
            # 先離線檢查；未通過就不產圖、不上傳，避免尚未確認可公開的圖片先出現在公開 bucket。
            with snapshot(db_path) as source:
                precheck = check(source, r2_enabled=True)
            if precheck["valid"]:
                media_summary = prepare_public_media(db_path, store)
        with snapshot(db_path) as source:
            return _export_snapshot(source, root, store is not None, media_summary)


def _export_snapshot(source, root, r2_enabled, media_summary):
    report = check(source, r2_enabled=r2_enabled)
    upload_failed = bool(media_summary and media_summary["errors"])
    if upload_failed:
        by_key = {item["key"]: item for item in report["items"]}
        for failure in media_summary["errors"]:
            message = "R2 上傳失敗：" + failure["error"]
            if failure["key"] in by_key:
                by_key[failure["key"]]["errors"].append(message)
            else:
                report["errors"].append(failure["key"] + "：" + message)
        report["valid"] = False
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
                # render_webp 以全新像素圖輸出，不攜帶來源 EXIF／ICC／文字 metadata。
                data, _, _ = render_webp(_thumbnail_source(row), "thumb")
                output.write_bytes(data)
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
                                "/media/" + filename, row["public_original_url"] or row["r2_url"], row["updated_at"]))
                target.executemany("INSERT INTO entry_tags VALUES(?,?)", [(row["public_id"], tag) for tag in tags])
            target.commit()
        manifest = {"schema_version": SCHEMA_VERSION, "normalization_version": NORMALIZATION_VERSION,
                    "version": version, "count": report["count"], "database_sha256": digest(staging / "public.sqlite3"), "media": media_hashes}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(staging, final)
        validate(root, version)
        return {"exported": True, "version": version, "report": report, "media": media_summary}
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if final.exists():
            shutil.rmtree(final)
        raise
