"""集中專案根目錄、預設路徑與環境變數；只用標準庫，公開映像也會載入。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DB = "data/anime_memes.sqlite3"
DEFAULT_ATTACHMENT_DIR = "data/attachments"
DEFAULT_THUMBNAIL_DIR = "data/thumbnails"
DEFAULT_WEB_DIR = "data/web"
DEFAULT_RELEASE_ROOT = "data/releases"
DEFAULT_TAG_RULES = "config/tags.taxonomy.json"
DEFAULT_RAW_EXPORTS = "data/raw_exports"
DEFAULT_FIXED_EXPORTS = "data/fixed_exports"
DEFAULT_BACKUPS = "data/backups"

TAXONOMY_PATH = ROOT / "config" / "tags.taxonomy.json"


# 環境變數一律在呼叫時讀取，方便測試以 patch.dict 覆寫。
def db_path() -> str:
    return os.environ.get("MEMES_DB", DEFAULT_DB)


def attachment_dir() -> str:
    return os.environ.get("MEMES_ATTACHMENT_DIR", DEFAULT_ATTACHMENT_DIR)


def thumbnail_dir() -> str:
    return os.environ.get("MEMES_THUMBNAIL_DIR", DEFAULT_THUMBNAIL_DIR)


def web_dir() -> str:
    # 空字串也退回預設
    return os.environ.get("MEMES_WEB_DIR") or DEFAULT_WEB_DIR


def release_root() -> str:
    return os.environ.get("MEMES_RELEASE_ROOT", DEFAULT_RELEASE_ROOT)


def tag_rules() -> str:
    return os.environ.get("MEMES_TAG_RULES", DEFAULT_TAG_RULES)
