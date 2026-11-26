"""不可變發布版本的驗證、啟用與清理；不依賴管理庫或 Pillow。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
import time
from contextlib import closing

from .public_data import open_readonly, validate_schema
from .public_data import SCHEMA_VERSION
from .search import NORMALIZATION_VERSION
from .urls import original_url

VERSION_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z")


class ReleaseMissing(ValueError):
    pass


def version_name(value: str) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ValueError("無效發布版本")
    return value


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def release_path(root: Path, version: str) -> Path:
    path = root / "versions" / version_name(version)
    if path.is_symlink() or not path.is_dir():
        raise ReleaseMissing("版本不存在或已過期，請重新搜尋")
    return path


def current_version(root: Path) -> str:
    try:
        return version_name(json.loads((root / "current.json").read_text(encoding="utf-8"))["version"])
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ReleaseMissing("尚無啟用版本") from exc


def validate(root: Path, version: str) -> dict:
    folder = release_path(root, version)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("version") != version or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("normalization_version") != NORMALIZATION_VERSION:
        raise ValueError("manifest 版本不符")
    if (folder / "public.sqlite3").is_symlink():
        raise ValueError("資料庫不可使用符號連結")
    if digest(folder / "public.sqlite3") != manifest["database_sha256"]:
        raise ValueError("資料庫雜湊不符")
    with closing(open_readonly(folder / "public.sqlite3")) as conn:
        validate_schema(conn, version)
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("資料庫完整性檢查失敗")
        if conn.execute("SELECT count(*) FROM entries").fetchone()[0] != manifest["count"]:
            raise ValueError("公開筆數不符")
        references = {r[0] for r in conn.execute("SELECT thumbnail_url FROM entries")}
        for row in conn.execute("SELECT * FROM entries"):
            if not re.fullmatch(r"[0-9a-f]{32}", row["id"]):
                raise ValueError("無效公開 ID")
            original_url(row["original_url"])
            for field in ("tags_json", "aliases_json"):
                values = json.loads(row[field])
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise ValueError("無效公開搜尋詞")
    media = manifest["media"]
    if references != {"/media/" + name for name in media}:
        raise ValueError("媒體清單不一致")
    for name, expected in media.items():
        if not re.fullmatch(r"[0-9a-f]{64}\.webp", name):
            raise ValueError("無效媒體名稱")
        target = root / "media" / name
        if target.is_symlink() or digest(target) != expected or expected != name[:-5]:
            raise ValueError("媒體雜湊不符")
    return manifest


def activate(root: Path, version: str) -> dict:
    manifest = validate(root, version)
    tmp = root / (".current-" + uuid.uuid4().hex)
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump({"version": version}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows 短暫開啟 current.json 讀取時可能禁止 replace；不影響舊版服務。
        for attempt in range(100):
            try:
                os.replace(tmp, root / "current.json")
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.01)
    finally:
        tmp.unlink(missing_ok=True)
    return manifest


def prune(root: Path, *, service_stopped: bool, keep_recent: int = 3) -> list[str]:
    if not service_stopped:
        raise ValueError("請先停止搜尋服務，並明確指定 --service-stopped")
    if not 0 <= keep_recent <= 20:
        raise ValueError("keep_recent 必須介於 0 到 20")
    active = current_version(root)
    versions = sorted([p for p in (root / "versions").iterdir() if p.is_dir() and not p.is_symlink()
                       and VERSION_RE.fullmatch(p.name)], key=lambda p: p.stat().st_mtime_ns, reverse=True)
    keep = {p.name for p in versions[:keep_recent]} | {active}
    # 所有保留版本先驗證；任何損壞都不執行清理。
    used = set()
    for version in keep:
        used.update(validate(root, version)["media"])
    removed = []
    for p in versions:
        if p.name not in keep:
            shutil.rmtree(p)
            removed.append(p.name)
    for p in (root / "media").glob("*.webp"):
        if not p.is_symlink() and p.name not in used:
            p.unlink()
    return removed
