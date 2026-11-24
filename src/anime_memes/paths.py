"""本機／容器共用媒體路徑解析，不改寫原 DB。"""
from pathlib import Path, PureWindowsPath
import os

ROOT = Path(__file__).resolve().parents[2]


def resolve_local(value, kind=None):
    if not value:
        return None
    path = Path(value)
    native = path if path.is_absolute() else ROOT / path
    # 明確掛載根目錄時，只映射已知媒體分類的 basename。
    # 不回退到任意系統位置，亦拒絕 .. 穿越。
    parts = PureWindowsPath(value).parts
    if ".." in parts:
        return None
    if kind is None:
        if "thumbnails" in parts:
            kind = "THUMBNAIL"
        elif "attachments" in parts:
            kind = "ATTACHMENT"
    configured = os.environ.get(f"ANIME_MEMES_{kind}_DIR") if kind else None
    if configured:
        return Path(configured) / PureWindowsPath(value).name
    return native
