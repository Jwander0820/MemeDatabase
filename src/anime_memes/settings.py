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


def configured_media_dir(kind: str) -> str | None:
    """kind 為 ATTACHMENT／THUMBNAIL／WEB；有設定 MEMES_{kind}_DIR 才回傳，否則 None（不套預設）。"""
    return os.environ.get(f"MEMES_{kind}_DIR") or None


# Discord 匯出相關（CLI 使用）
def discord_bot_token() -> str | None:
    return os.environ.get("DISCORD_BOT_TOKEN")


def discord_channel_id() -> str | None:
    return os.environ.get("DISCORD_CHANNEL_ID")


def discord_chat_exporter_exe() -> str | None:
    return os.environ.get("DISCORD_CHAT_EXPORTER_EXE")


def web_host() -> str:
    return os.environ.get("MEMES_WEB_HOST", "127.0.0.1")


def web_port() -> int:
    return int(os.environ.get("MEMES_WEB_PORT", "3638"))


# 私人 MCP
def mcp_token() -> str:
    return os.environ.get("MEMES_MCP_TOKEN", "")


def private_url() -> str:
    return os.environ.get("MEMES_PRIVATE_URL", "http://127.0.0.1:3636")


def mcp_hosts() -> list[str]:
    return os.environ.get("MEMES_MCP_HOSTS", "127.0.0.1:8766,localhost:8766").split(",")


def mcp_origins() -> list[str]:
    return os.environ.get("MEMES_MCP_ORIGINS", "http://127.0.0.1:8766,http://localhost:8766").split(",")


def mcp_bind() -> str:
    return os.environ.get("MEMES_MCP_BIND", "127.0.0.1")


# 私人管理端只接受這些 Host（擋 DNS rebinding：惡意網域解析到 127.0.0.1 時 Host 會是該網域）。
DEFAULT_ADMIN_HOSTS = ("localhost", "127.0.0.1", "::1")


def admin_hosts() -> set[str]:
    """預設只有本機名稱；MEMES_ADMIN_HOSTS 可用逗號追加（例如區網內固定主機名稱）。"""
    extra = os.environ.get("MEMES_ADMIN_HOSTS", "")
    return {host.strip().lower() for host in (*DEFAULT_ADMIN_HOSTS, *extra.split(",")) if host.strip()}
